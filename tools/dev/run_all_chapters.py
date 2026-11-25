"""一口气跑完整本：按逐章细纲建章 + 一键写完本章，全程双端监控。

设计要点
· **可断点续跑**：已有正文的章节一律跳过，重跑脚本不会重写任何已完成章节。
· **双端监控**：每章结束同时核对「任务链状态（后端）」与「章节状态/字数（前端读到的字段）」，
  不一致就记入日志并重试。
· **不手改正文**：本脚本只建章、触发流程、读结果；任何正文文本都不做修改。
· 失败自动重试（每章最多 3 次）；服务掉线时等待并重连。
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request

BASE = 'http://127.0.0.1:8788'
PID = 'book_06a6f811d4a4'
DB = 'data/app.db'
SYNOPSIS = 'data/exports/逐章细纲-写万生.json'
LOG = 'data/exports/_跑本日志.md'
BACKUP_DIR = 'data/exports/_旧版备份'
MIN_WORDS = 1900
MAX_WORDS = 4000          # 超过 4000 字基本可以断定是思考文本混进来了（正常章 1900–2800）
EN_RE = re.compile(r'[A-Za-z]{3,}')   # 中文正文里成串英文 = 模型自检/思考泄漏

# 余额不足的识别信号（网关返回 HTTP 402 + {"code":"INSUFFICIENT_BALANCE"}）
BALANCE_HINTS = ('INSUFFICIENT_BALANCE', '余额不足', '402')
STOP_MARK = 'data/exports/_停止原因.txt'
DRAFT_FAIL = {'consec': 0}          # 起草步骤连续失败计数（余额/网关异常的兜底信号）


class NoBalance(Exception):
    """密钥余额不足 —— 立即停止整本跑，等用户换密钥后再续跑。"""


def call(path, payload=None, method='GET', timeout=180):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={'Content-Type': 'application/json'}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        body = ''
        try:
            body = e.read().decode('utf-8', 'ignore')
        except Exception:  # noqa: BLE001
            pass
        if e.code == 402 or any(h in body for h in BALANCE_HINTS):
            raise NoBalance(body[:200] or f'HTTP {e.code}') from e
        raise


def check_balance(cid=''):
    """直连本地 DB 扫**最近 20 分钟内**的生成记录，找余额不足信号。

    ⚠️ 两个踩坑（都在 2026-10-15 犯过，务必别再犯）：
    1. 一开始扫的是 `/api/books/{pid}/gens` —— **那个接口不返回 `error` 字段**（只有 id/kind/status/preview…），
       余额关键词永远匹配不到 → 保护形同虚设，余额耗尽后白烧了 15 分钟重试。**余额判断必须读 DB。**
    2. 改成读 DB 后又加了 `LIMIT 40` 但不加时间过滤 —— 结果**扫到几小时前的旧 402 记录**，
       换完密钥仍误报"余额不足"直接停跑。**必须加时间窗，只看最近的失败。**
    """
    since = time.strftime('%Y-%m-%dT%H:%M:%S', time.localtime(time.time() - 1200))  # 近 20 分钟
    try:
        conn = sqlite3.connect(DB)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT id, kind, chapter_id, error FROM gens WHERE book_id=? AND started_at>=? '
            'ORDER BY rowid DESC LIMIT 60', [PID, since]).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001
        return
    for r in rows:
        if cid and r['chapter_id'] and r['chapter_id'] != cid:
            continue
        err = r['error'] or ''
        if any(h in err for h in BALANCE_HINTS):
            raise NoBalance(f"[{r['id']} / {r['kind']}] {err[:200]}")


def call(path, payload=None, method='GET', timeout=180):
    data = json.dumps(payload, ensure_ascii=False).encode('utf-8') if payload is not None else None
    req = urllib.request.Request(BASE + path, data=data,
                                 headers={'Content-Type': 'application/json'}, method=method)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read().decode('utf-8'))


def log(msg):
    line = f'[{time.strftime("%m-%d %H:%M:%S")}] {msg}'
    print(line, flush=True)
    with open(LOG, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def wait_service():
    while True:
        try:
            call('/api/health', timeout=10)
            return
        except Exception:  # noqa: BLE001
            log('  服务不可用，10 秒后重试…')
            time.sleep(10)


def chapters_by_no():
    d = call(f'/api/books/{PID}/flow', timeout=60)
    return {c['no']: c for c in d.get('chapters', [])}


def content_of(no):
    try:
        conn = sqlite3.connect(DB)
        r = conn.execute('SELECT content FROM chapters WHERE book_id=? AND no=?', [PID, no]).fetchone()
        conn.close()
        return (r[0] if r else '') or ''
    except Exception:  # noqa: BLE001
        return ''


def judge(no, cur):
    """判定一章是否合格 → (ok, 原因)。

    ⚠️ 2026-10-15 补的两条检查：只卡下界（字数偏短）**不够**——
    实测有 3 章被模型的**思考/自检文本混进正文**（中英混杂、章长 3.2k~5.3k 字），
    当时全被当成"正常完成"放过了（第 8 / 50 / 80 章）。所以必须同时卡上界与英文串。
    """
    if not cur:
        return False, '章节不存在'
    if cur.get('status') != 'done':
        return False, f'状态 {cur.get("status")}'
    w = cur.get('words') or 0
    if w < MIN_WORDS:
        return False, f'字数偏短（{w}）'
    n = len(EN_RE.findall(content_of(no)))
    if n >= 3:
        return False, f'疑似模型思考文本混入正文（英文词 {n} 个）'
    if w > MAX_WORDS:
        return False, f'字数严重超长（{w}）'
    return True, ''


def rerun_deai(cid, no):
    """只重跑「去 AI 味」这一步（流程层修正；不改动任何正文文本）。"""
    try:
        r = call(f'/api/books/{PID}/generate', {'step_id': 'deai', 'chapter_id': cid}, 'POST',
                 timeout=60)
    except Exception as e:  # noqa: BLE001
        log(f'  第{no}章 去AI味提交失败：{e}')
        return False
    gid = r.get('gen_id') or ''
    t0 = time.time()
    while gid and time.time() - t0 < 600:
        time.sleep(8)
        try:
            g = call(f'/api/gens/{gid}', timeout=60).get('gen') or {}
        except NoBalance:
            raise
        except Exception:  # noqa: BLE001
            continue
        if g.get('status') in ('success', 'failed', 'error'):
            break
    check_balance(cid)
    cur = chapters_by_no().get(no) or {}
    ok, why = judge(no, cur)
    log(f'  第{no}章 去AI味重跑后：{cur.get("words")}字/{cur.get("status")} → '
        f'{"OK" if ok else "仍未达标（" + why + "）"}')
    return ok


def run_one(item):
    no, title = item['no'], item['title']
    brief = item['brief']          # 篇幅要求已移进流程的「章节续写」步骤，只作用于起草，不给改写
    seen = chapters_by_no()
    cur = seen.get(no)
    if cur:
        ok, why = judge(no, cur)
        if ok:
            log(f'第{no}章 已完成（{cur["words"]} 字），跳过')
            return 'skip'
        log(f'第{no}章 判定不合格：{why}')
        # 情况一：正文够长但状态是 draft（去 AI 味被改写保护拦下）→ 只重跑那一步，成本最低
        if cur.get('status') != 'done' and (cur.get('words') or 0) >= MIN_WORDS:
            log(f'  → 先只重跑「去 AI 味」')
            if rerun_deai(cur['id'], no):
                return 'ok'
        # 情况二：字数偏短 / 超长 / 混入思考文本 → 整章重跑（先备份旧正文，绝不丢内容）

    if cur and (cur.get('words') or 0) > 0:
        try:
            old = call(f"/api/chapters/{cur['id']}", timeout=60).get('chapter') or {}
            os.makedirs(BACKUP_DIR, exist_ok=True)
            p = f'{BACKUP_DIR}/第{no}章-旧版.txt'
            with open(p, 'w', encoding='utf-8') as f:
                f.write(old.get('content') or '')
            log(f'  第{no}章原正文（{cur["words"]} 字）已备份到 {p}，准备整章重跑')
        except Exception as e:  # noqa: BLE001
            log(f'  第{no}章备份失败：{e}')

    r = call(f'/api/books/{PID}/chapters', {'no': no, 'title': title, 'brief': brief}, 'POST')
    cid = r['chapter']['id']

    tid, last, t0 = '', '', time.time()
    while time.time() - t0 < 1500:
        if not tid:                       # finish 只在提交成功后置 tid，避免重复起链
            try:
                fr = call(f'/api/chapters/{cid}/finish', {}, 'POST', timeout=60)
                tid = fr.get('task_id') or ''
                if not tid:
                    log(f'第{no}章 finish 未返回 task_id：{fr}')
                    return 'fail'
            except Exception as e:  # noqa: BLE001
                log(f'  第{no}章 finish 提交失败：{e}')
                time.sleep(10)
                continue
        time.sleep(8)
        try:
            t = call(f'/api/tasks/{tid}', timeout=60)
            t = t.get('task') or t
        except Exception:  # noqa: BLE001
            continue
        steps = t.get('steps') or []
        last = ' '.join(f"{s.get('name')}:{s.get('status')}" for s in steps) or str(t.get('status'))
        if t.get('status') in ('success', 'failed', 'error'):
            break

    check_balance(cid)               # 余额不足 → 直接抛 NoBalance 停下，不再重试
    if '起草正文:failed' in last:
        DRAFT_FAIL['consec'] += 1    # 起草失败是异常信号，累计到阈值就硬停（兜底）
    else:
        DRAFT_FAIL['consec'] = 0
    fe = chapters_by_no().get(no) or {}
    be_ok = 'failed' not in last
    jok, why = judge(no, fe)
    ok = be_ok and jok
    log(f'第{no}章《{title}》 结果={"OK" if ok else "异常"} | 后端: {last} | '
        f'前端: {fe.get("words")}字/{fe.get("status")}'
        + ('' if ok else f'  ← {why if not jok else "后端有 failed"}'))
    return 'ok' if ok else 'fail'


def stop_hard(reason, no, stats):
    """硬停止：余额不足 / 起草连续失败等，必须停下来等人工处理（不消耗重试）。"""
    log(f'⚠️⚠️ 已停止跑本（停在第 {no} 章）：{reason}')
    log(f'   处理完（如更换密钥）后重新运行本脚本即可继续，会从第 {no} 章接着跑。')
    with open(STOP_MARK, 'w', encoding='utf-8') as f:
        f.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} 停止于 第 {no} 章\n原因：{reason}\n')
    log(f'=== 中止：成功 {stats["ok"]} / 跳过 {stats["skip"]} / 失败 {stats["fail"]} ===')
    sys.exit(2)


def main():
    items = json.load(open(SYNOPSIS, encoding='utf-8'))
    items.sort(key=lambda x: x['no'])
    limit = int(os.environ.get('RUN_LIMIT') or 0)   # >0 时只新建/重跑 N 章（用于先试跑验收）
    log(f'=== 开跑：共 {len(items)} 章（第 {items[0]["no"]}–{items[-1]["no"]} 章）'
        f'{"｜本次限 " + str(limit) + " 章" if limit else ""} ===')
    stats = {'ok': 0, 'skip': 0, 'fail': 0}
    done_new = 0
    for it in items:
        if limit and done_new >= limit:
            log(f'--- 已达本次上限 {limit} 章，停止（剩余可下次续跑） ---')
            break
        wait_service()
        was_missing = not (chapters_by_no().get(it['no'], {}).get('words') or 0)
        try:
            check_balance()                      # 每章开工前先探一次余额（读 DB）
        except NoBalance as e:
            stop_hard(f'密钥余额不足：{e}', it['no'], stats)
        for attempt in range(1, 4):
            try:
                r = run_one(it)
                stats[r] += 1
                if r != 'fail':
                    if was_missing:
                        done_new += 1
                    break
                if DRAFT_FAIL['consec'] >= 2:    # 起草连续失败 ≥2 章 → 硬停（余额/网关异常的兜底）
                    stop_hard(f'起草步骤连续 {DRAFT_FAIL["consec"]} 次失败，'
                              f'极可能是余额不足或网关异常', it['no'], stats)
                log(f'  第{it["no"]}章 第 {attempt} 次失败，重试…')
                time.sleep(15)
            except NoBalance as e:
                stop_hard(f'密钥余额不足：{e}', it['no'], stats)
            except Exception as e:  # noqa: BLE001
                log(f'  第{it["no"]}章 第 {attempt} 次异常：{e}')
                time.sleep(15)
        else:
            stats['fail'] += 1
            log(f'  第{it["no"]}章 三次均失败，跳过（可稍后单独重跑）')
    log(f'=== 结束：成功 {stats["ok"]} / 跳过 {stats["skip"]} / 失败 {stats["fail"]} ===')


if __name__ == '__main__':
    sys.exit(main())
