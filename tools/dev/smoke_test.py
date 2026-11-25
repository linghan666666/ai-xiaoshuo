"""端到端冒烟测试：验证「skill 为核心」的全链路。

两种跑法：

  A) 进程内自测（推荐，不占端口、不用起后台进程）：
       python tools/dev/smoke_test.py --inproc

  B) 对着真实服务跑（前端在用的时候顺手验证）：
       python tools/dev/mock_llm.py                     # 假模型 :8899
       python -m uvicorn app.main:app --port 8788       # 平台
       python tools/dev/smoke_test.py

覆盖：知识库（模块/检索/指令块/清单/本机 skill 对比）→ 模型配置 → 建书与流程 →
步骤面板（手册原文指令 + 提示词组装）→ 生成并落库 → 黑名单预检 → 章节生成 →
一键写完本章（起草→去AI味→摘要→伏笔台账→扫描）→ 跨章摘要/伏笔注入 → 三条检查 →
全书批量扫描 → 步骤级默认模型 + 一键推荐配置 + 绑定总览 → 生成记录重试 → 导出 → 清理。
"""
import os
import sys
import threading
import time

import httpx

BASE = os.environ.get("PLATFORM_BASE", "http://127.0.0.1:8788")
MOCK = os.environ.get("MOCK_BASE", "http://127.0.0.1:8899/v1")
c = None          # 平台客户端（进程内为 TestClient）


def ok(m): print(f"  ✓ {m}")
def fail(m):
    print(f"  ✗ {m}")
    sys.exit(1)
def check(cond, m): ok(m) if cond else fail(m)


def wait_gen(gid, timeout=180):
    t0 = time.time()
    while time.time() - t0 < timeout:
        g = c.get(f"/api/gens/{gid}").json()["gen"]
        if g["status"] != "running":
            return g
        time.sleep(0.5)
    fail(f"生成 {gid} 超时")


def wait_task(tid, timeout=600):
    t0 = time.time()
    while time.time() - t0 < timeout:
        t = c.get(f"/api/tasks/{tid}").json()["task"]
        if t["status"] != "running":
            return t
        time.sleep(1)
    fail(f"任务 {tid} 超时")


def run(platform, mock_url: str) -> None:
    global c
    c = platform

    print("== 1. 健康检查 ==")
    h = c.get("/api/health").json()
    check(h.get("ok"), f"后端连通：{h['db']}")

    print("== 2. 知识库（skill 为核心）==")
    info = c.get("/api/kb/info").json()
    check(len(info["modules"]) == 14, f"内置 {len(info['modules'])} 个模块")
    check(info["total_chars"] > 300000, f"共 {info['total_chars']} 字 / {info['total_sections']} 节")
    check(len(info["routing"]) >= 10, "意图路由表可用（对应 SKILL.md）")
    check("local" in info, f"本机 skill 对比可用（{info['local'].get('note') or '未检测到本机目录'}）")
    doc = c.get("/api/kb/module", params={"name": "02d-去AI味专项指令.md"}).json()
    check(doc["chars"] > 5000 and len(doc["sections"]) >= 6,
          f"模块可读 + 分节索引：{doc['chars']} 字 / {len(doc['sections'])} 节")
    sec = c.get("/api/kb/section", params={"name": "02d-去AI味专项指令.md",
                                          "heading": doc["sections"][1]["heading"]}).json()
    check(sec["chars"] > 200, f"单节可读：{sec['heading']}（{sec['chars']} 字）")
    sr = c.post("/api/kb/search", json={"query": "违禁词"}).json()
    check(sr["hits"] > 0, f"关键词检索命中 {sr['hits']} 处")
    bl = c.post("/api/kb/blocks", json={"modules": ["02d", "05"],
                                        "keywords": ["万能通用核心去AI味", "去AI化实战"]}).json()
    check(bl["count"] >= 1, f"指令块抽取：{bl['count']} 块 / {bl['chars']} 字")
    check("指令" in bl["blocks"][0]["directive"][:60], "抽取结果能定位到「指令：」正文")
    black = c.get("/api/kb/blacklist").json()
    check(len(black["groups"]) >= 4, f"黑名单条款解析：{len(black['groups'])} 组")
    pre = c.get("/api/kb/presubmit-items").json()
    check(len(pre["items"]) >= 10, f"投稿前自查清单解析：{len(pre['items'])} 项")

    print("== 3. 模型配置（假模型）==")
    # 先清掉上次中途失败可能留下的测试书，避免同名残留干扰后续断言
    for b in c.get("/api/books").json()["books"]:
        if b["name"].startswith(("冒烟测试", "__探针", "__自检")):
            c.delete(f"/api/books/{b['id']}")
    # 记下当前默认模型，跑完要还回去（假模型会抢默认，别把用户的默认配置弄丢）
    _prev_default = next((m["id"] for m in c.get("/api/models").json()["models"] if m["is_default"]), "")
    for m in c.get("/api/models").json()["models"]:
        # 只清"指向本机假模型"的残留（绝不碰用户接的真实网关配置）
        if m["name"].startswith(("MOCK", "SLOW")) or \
           ("127.0.0.1" in (m.get("base_url") or "") and not m["name"].startswith("P-")):
            c.delete(f"/api/models/{m['id']}")
    mock = c.post("/api/models", json={
        "name": "MOCK-主力", "channel": "api", "base_url": mock_url, "model_name": "mock",
        "api_key": "", "is_default": 1, "enabled": 1, "stream": 0, "thinking_off": 1,
        "no_proxy": 1, "timeout": 60}).json()["model"]
    check(mock["id"], "已接入假模型并设为默认")
    t = c.post(f"/api/models/{mock['id']}/test", json={}).json()
    check(t.get("ok"), "连通性测试通过")

    print("== 4. 建书 + 流程定义 ==")
    flows = c.get("/api/flows").json()
    check(len(flows["flows"]) == 4, "四条开书流程（长篇 9 步 / 完本中篇 10 步 / 短篇 8 步 / 番茄短故事 8 步）")
    ids = [f["id"] for f in flows["flows"]]
    check(ids == ["long_novel", "mid_novel", "short_story", "fanqie_short"], f"开书流程顺序与 id：{ids}")
    mid = next((f for f in flows["flows"] if f["id"] == "mid_novel"), {})
    check(len(mid.get("steps") or []) == 10, "完本中篇 10 步（含完本三审预检）")
    check(any(s["id"] == "finish_audit" for s in mid.get("steps") or []), "完本中篇含 finish_audit 步骤")
    check(len(flows["tool_flows"][0]["steps"]) == 3, "卡文急救工具箱有 3 件工具")
    book = c.post("/api/books", json={
        "name": "冒烟测试书", "genre": "都市脑洞 / 规则怪谈", "audience": "male",
        "brief": "主角能看见规则下面的灰色注释。", "flow_id": "long_novel"}).json()["book"]
    flow = c.get(f"/api/books/{book['id']}/flow").json()
    check(len(flow["steps"]) == 9, f"长篇流程 {len(flow['steps'])} 步")

    print("== 5. 步骤面板：skill 原文指令 ==")
    panel = c.get(f"/api/books/{book['id']}/step/topic").json()
    check(len(panel["blocks"]) >= 1, f"「选题定方向」抽到 {len(panel['blocks'])} 块手册原文")
    check("番茄写作手册" in panel["prompt"] and "黑名单" in panel["prompt"], "提示词含手册原文指令")
    check(panel["prompt"].startswith("# 任务：1. 选题定方向"), "提示词按「任务→资料→原文指令→输出要求」组装")
    check("体裁与体量" in panel["prompt"] and "长篇" in panel["prompt"],
          "提示词含体裁与体量（长篇口径）——否则模型会按短剧逻辑出题")
    tool = c.get(f"/api/books/{book['id']}/step/diagnose", params={"flow": "stuck"}).json()
    check(tool["step"]["name"] == "卡文自检定位" and len(tool["blocks"]) >= 1,
          f"工具箱可借 stuck 流程的步骤（抽到 {len(tool['blocks'])} 块原文）")
    g3 = c.get(f"/api/books/{book['id']}/step/golden3").json()
    check(all(not b["module"].startswith("02c") for b in g3["blocks"])
          and all("女频" not in b["heading"] for b in g3["blocks"]),
          "受众过滤生效（模块级 + 节级）：男频书的黄金三章不吃女频指令（"
          + "、".join(f"{b['module'][:3]}:{b['heading'][:12]}" for b in g3["blocks"]) + "）")

    print("== 6. 生成第 1 步产出（后台任务 + 自动落库）==")
    r = c.post(f"/api/books/{book['id']}/generate", json={"step_id": "topic", "extra": "偏悬疑"}).json()
    g = wait_gen(r["gen_id"])
    check(g["status"] == "success", f"生成成功（{g['chars']} 字，模型 {g['model_name']}）")
    art = c.get(f"/api/books/{book['id']}/artifacts/topic").json()
    check(art["chars"] > 0, "产出已自动写入（选题结论）")

    print("== 7. 黑名单预检 ==")
    chk = c.post(f"/api/books/{book['id']}/check/blacklist").json()
    check("report" in chk and len(chk["groups"]) >= 4, f"预检完成（命中 {len(chk['hits'])} 个高危词）")

    print("== 8. 章节：新建 + 一键写完本章 ==")
    ch = c.post(f"/api/books/{book['id']}/chapters", json={"title": "第一单", "brief": "主角第一次看见注释"}).json()
    check(ch["created"], "章节已创建")
    task = c.post(f"/api/chapters/{ch['chapter']['id']}/finish").json()
    check(task.get("ok"), f"一键写完本章已启动（{len(task['steps'])} 步）")
    t = wait_task(task["task_id"])
    check(t["status"] == "success", f"任务链完成：{' → '.join(s['key'] + ':' + s['status'] for s in t['steps'])}")
    ch2 = c.get(f"/api/chapters/{ch['chapter']['id']}").json()["chapter"]
    check(ch2["words"] > 100, f"正文已落库：{ch2['words']} 字")
    check(ch2["status"] == "done", "状态已标记为成稿（走完去 AI 味）")
    check(len(ch2["summary"]) > 20, f"章节摘要已生成：{len(ch2['summary'])} 字")
    fh = c.get(f"/api/books/{book['id']}/foreshadows").json()
    check(len(fh["text"]) > 0, f"伏笔台账已更新：{len(fh['text'])} 字")

    print("== 9. 跨章连贯：前情摘要 + 伏笔台账注入起草 ==")
    ch_b = c.post(f"/api/books/{book['id']}/chapters", json={"no": 2, "title": "第二个名字"}).json()["chapter"]
    p2 = c.get(f"/api/books/{book['id']}/step/chapter", params={"chapter_id": ch_b["id"]}).json()
    check("前情摘要" in p2["prompt"], "起草提示词已注入「前情摘要」（读得到第 1 章摘要）")
    check("待回收伏笔账本" in p2["prompt"], "起草提示词已注入「待回收伏笔账本」")
    check("第1章《第一单》" in p2["prompt"], "摘要内容确实是上一章产出的（不是空壳）")
    r = c.post(f"/api/books/{book['id']}/generate",
               json={"step_id": "chapter", "chapter_id": ch_b["id"]}).json()
    g = wait_gen(r["gen_id"])
    check(g["status"] == "success", "第 2 章起草成功（带跨章上下文）")

    print("== 9b. 后台任务非阻塞 + 同章防抖（慢模型实测）==")
    slow = c.post("/api/models", json={
        "name": "MOCK-慢速", "channel": "api", "base_url": mock_url, "model_name": "sleep-1",
        "is_default": 0, "enabled": 1, "stream": 0, "thinking_off": 1, "no_proxy": 1,
        "timeout": 60}).json()["model"]
    c.put(f"/api/books/{book['id']}/step-models", json={"step_id": "chapter", "model_id": slow["id"]})
    t0 = time.time()
    rf = c.post(f"/api/chapters/{ch_b['id']}/finish")
    cost = time.time() - t0
    check(rf.status_code == 200 and cost < 1.5,
          f"「一键写完本章」提交耗时 {cost:.2f}s（<1.5s = 真后台；阻塞的话会 ≥ 链上调用数 ×1s）")
    # 链刚启动，此刻再提交必须被拒（原先这里是一条恒真断言，等于没测）
    r2 = c.post(f"/api/books/{book['id']}/generate",
                json={"step_id": "chapter", "chapter_id": ch_b["id"]}).json()
    check(r2.get("ok") is False and "正在生成" in (r2.get("error") or ""),
          f"同章重复提交被拒（防抖有效）：{r2.get('error')}")
    t = wait_task(rf.json()["task_id"], timeout=180)
    check(t["status"] == "success", "慢模型下任务链仍能跑完（后台执行不受影响）")
    c.put(f"/api/books/{book['id']}/step-models", json={"step_id": "chapter", "model_id": ""})
    c.delete(f"/api/models/{slow['id']}")

    print("== 9c. 改写保护：字数骤降不许覆盖正文 ==")
    shortm = c.post("/api/models", json={
        "name": "MOCK-短文", "channel": "api", "base_url": mock_url, "model_name": "short",
        "is_default": 0, "enabled": 1, "stream": 0, "thinking_off": 1, "no_proxy": 1,
        "timeout": 60}).json()["model"]
    c.put(f"/api/books/{book['id']}/step-models", json={"step_id": "deai", "model_id": shortm["id"]})
    words_before = c.get(f"/api/chapters/{ch_b['id']}").json()["chapter"]["words"]
    rg = c.post(f"/api/books/{book['id']}/generate",
                json={"step_id": "deai", "chapter_id": ch_b["id"]}).json()
    gd = wait_gen(rg["gen_id"])
    words_after = c.get(f"/api/chapters/{ch_b['id']}").json()["chapter"]["words"]
    check(gd["status"] == "success" and words_after == words_before,
          f"改写明显变短时未覆盖正文（{words_before} → {words_after} 字）")
    check("未覆盖" in (gd.get("error") or ""), "并在生成记录里留下原因说明")
    adopted = c.post(f"/api/gens/{rg['gen_id']}/adopt").json()
    check(adopted.get("ok"), f"可手动「采用这条结果」：{adopted.get('note')}")
    c.put(f"/api/books/{book['id']}/step-models", json={"step_id": "deai", "model_id": ""})
    c.delete(f"/api/models/{shortm['id']}")

    print("== 10. 生成记录：重试（流程步骤 + 内部步骤）==")
    gens = c.get(f"/api/books/{book['id']}/gens").json()["gens"]
    check(len(gens) >= 3, f"生成记录 {len(gens)} 条（可回看提示词与输出）")
    flow_gen = next(g for g in gens if g["step_id"] in ("chapter", "deai", "topic"))
    retry = c.post(f"/api/gens/{flow_gen['id']}/retry").json()
    check(retry.get("ok") and retry.get("gen_id"), f"流程步骤（{flow_gen['step_id']}）重试已提交")
    g = wait_gen(retry["gen_id"])
    check(g["status"] == "success", "重试完成")
    inner = next((g for g in gens if g["step_id"] in ("summary", "foreshadow")), None)
    if inner:
        r2 = c.post(f"/api/gens/{inner['id']}/retry").json()
        check(r2.get("ok"), f"内部步骤（{inner['step_id']}）也能重试并写回对应字段")
        wait_gen(r2["gen_id"])

    print("== 11. 三条检查 + 全书批量扫描 ==")
    v = c.post(f"/api/books/{book['id']}/check/violations", json={"chapter_id": ch["chapter"]["id"]}).json()
    check("report" in v, f"单章违禁词扫描（一级命中 {len(v['blocks'])}）")
    allv = c.post(f"/api/books/{book['id']}/check/violations-all").json()
    check(len(allv["chapters"]) == 2, f"全书批量扫描：{len(allv['chapters'])} 章汇总")
    p = c.post(f"/api/books/{book['id']}/check/presubmit", json={"ticks": pre["items"]}).json()
    check(p["ok"] and p["total"] >= 10, f"投稿前自查：{p['done']}/{p['total']} 项已确认")

    print("== 12. 步骤级默认模型 + 一键推荐配置 + 绑定总览 ==")
    pre_models = c.post("/api/models/presets", json={"base_url": mock_url, "api_key": "mock-key",
                                                     "tier": "value"}).json()
    roles = pre_models.get("roles") or {}
    done = len(pre_models.get("created") or []) + len(pre_models.get("skipped") or [])
    check(len(roles) == 4 and done == 4,
          f"一键创建「{pre_models['tier_label']}」配置：四个角色齐备"
          + f"（新建 {len(pre_models['created'])} 条，已存在跳过 {len(pre_models['skipped'])} 条）"
          + "｜" + "、".join(f"{x['role']}→{x['model_name']}" for x in pre_models["created"]))
    ap = c.post(f"/api/books/{book['id']}/step-models/apply-presets", json={"roles": roles}).json()
    check(ap["bound"].get("chapter") == roles["起草"], "「起草」角色已绑到章节步骤")
    bd = c.get("/api/models/bindings").json()["bindings"]
    check(any(v2 for v2 in bd.values()), "绑定总览有数据（能看出模型绑在哪些步骤）")
    panel = c.get(f"/api/books/{book['id']}/step/chapter", params={"chapter_id": ch_b["id"]}).json()
    check(panel["step_model_id"] == roles["起草"], "步骤面板返回绑定结果（前端可回显）")
    r = c.post(f"/api/books/{book['id']}/generate",
               json={"step_id": "chapter", "chapter_id": ch_b["id"]}).json()
    g = wait_gen(r["gen_id"])
    check("起草" in (g["model_name"] or ""), f"生成走的模型 = {g['model_name']}（步骤绑定生效）")
    for x in pre_models["created"]:
        c.delete(f"/api/models/{x['id']}")

    print("== 13. 导出成稿 ==")
    ex = c.post(f"/api/books/{book['id']}/export", json={"mode": "both", "fmt": "txt"}).json()
    check(ex["ok"] and ex["count"] == 2, f"导出 {ex['count']} 章 / {ex['words']} 字 → {ex['dir']}")
    head = open(ex["files"][-1], encoding="utf-8-sig").read().strip().splitlines()[0]
    check(head.startswith("第1章") or head.startswith("第2章"), f"导出文件首行是规范标题：{head}")
    outside = c.post(f"/api/books/{book['id']}/export",
                     json={"mode": "merged", "fmt": "txt", "dir": r"C:\Windows\Temp\aixs_probe"})
    check(outside.status_code == 400, f"越界导出目录被拒绝（HTTP {outside.status_code}）")

    print("== 14. 清理 ==")
    c.delete(f"/api/books/{book['id']}")
    c.delete(f"/api/models/{mock['id']}")
    if _prev_default and c.get("/api/models").json()["models"]:
        c.post(f"/api/models/{_prev_default}/default")      # 把默认模型还给用户原来的配置
    left = [b["name"] for b in c.get("/api/books").json()["books"]
            if b["id"] == book["id"] or b["name"].startswith("冒烟测试")]
    check(not left, f"测试数据已清理（剩余：{left or '无'}）")

    print("\n全部冒烟用例通过 ✅")


def run_inprocess() -> None:
    """进程内：平台用 TestClient（不占端口），假模型在本进程的线程里起一个 uvicorn。"""
    import uvicorn
    from fastapi.testclient import TestClient

    from app.main import app
    from tools.dev.mock_llm import app as mock_app

    cfg = uvicorn.Config(mock_app, host="127.0.0.1", port=8899, log_level="warning")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            if httpx.get("http://127.0.0.1:8899/v1/models", timeout=1).status_code == 200:
                break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)
    print("（进程内模式：假模型跑在本进程线程，平台用 TestClient，不占端口）\n")
    with TestClient(app) as client:
        run(client, "http://127.0.0.1:8899/v1")
    server.should_exit = True


if __name__ == "__main__":
    if "--inproc" in sys.argv:
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
        run_inprocess()
    else:
        with httpx.Client(base_url=BASE, timeout=120, trust_env=False) as client:
            run(client, MOCK)
