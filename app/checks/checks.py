"""规则检查：黑名单预检 / 违禁词扫描 / 投稿前自查。

三条检查的依据都来自 skill：
  · 黑名单与违禁词**条款**逐条取自 01 / 03 模块（原文展示，人工逐项确认）；
  · 关键词扫描用 vocab.json（派生物，见文件头说明）；
  · 投稿前自查清单直接解析 01 模块的 `□` 清单（11 项），并对其中可自动核对的几项给出证据。
设计取舍：一级命中 → 判不通过（会阻断"完成"）；二三级只提示 + 给替换建议；不阻断。
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List

from .. import store, textkit
from ..knowledge import assets

_HOOK = re.compile(r"[“\"「『]")
_PARA_SPLIT = re.compile(r"\n\s*\n|\n(?=[\u3000\s]*[^\s])")


def _book_text(book_id: str) -> str:
    """书级扫描文本 = 书名/题材/设定 + 各步产出。

    ⚠️ 排除"元报告型"产出（合规预检结论 / 投稿自查记录）：它们的工作就是**逐个列举**要规避的
    违禁词与违规情节，当成正文扫会把"声明规避"误报成"命中"（实测踩过）。
    """
    book = store.query_one("SELECT * FROM books WHERE id=?", [book_id]) or {}
    parts = [book.get("name") or "", book.get("genre") or "", book.get("brief") or ""]
    for a in store.query("SELECT key, content FROM artifacts WHERE book_id=?", [book_id]):
        if a["key"] in ("compliance", "presubmit"):
            continue
        parts.append(a.get("content") or "")
    return "\n".join(parts)


# 出现这些词说明命中处是"声明规避"的语境（如"不碰近代史""已规避乱伦"），不是真的在用
_NEGATION = ("不碰", "不涉", "未涉", "不含", "不做", "不得", "不用", "已避", "规避", "避免",
             "禁止", "严禁", "杜绝", "无任何", "无相关", "未加入", "均以", "统一替换", "替换为")


def _find(text: str, words: Iterable[str], width: int = 12, cap: int = 80) -> List[Dict[str, str]]:
    """找出命中词并带上上下文，标注是否属于"声明规避"型（假警报）。"""
    out: List[Dict[str, str]] = []
    for w in words:
        start = 0
        while len(out) < cap:
            i = text.find(w, start)
            if i < 0:
                break
            ctx = text[max(0, i - width): i + len(w) + width].replace("\n", " ")
            out.append({"word": w, "context": ctx,
                        "negated": "yes" if any(n in ctx for n in _NEGATION) else ""})
            start = i + len(w)
    return out


# ---------------------------------------------------------------- ① 黑名单预检

def blacklist_check(book_id: str) -> Dict[str, Any]:
    data = assets.blacklist()
    v = assets.vocab()
    text = _book_text(book_id)
    raw: List[Dict[str, str]] = []
    for cat, words in (v.get("level1") or {}).items():
        for h in _find(text, words):
            raw.append({**h, "category": cat})
    hits = [h for h in raw if not h["negated"]]          # 真命中才算不通过
    noted = [h for h in raw if h["negated"]]             # "声明规避"型：只展示，不算命中
    report = ["【题材黑名单预检】", f"依据：{data.get('source') or '知识库 03 模块'}",
              f"结论文本：{'✗ 命中高危词，必须处理后再开写' if hits else '✓ 未命中高危词'}"
              + (f"（另有 {len(noted)} 处属于「声明规避」语境，见下）" if noted else ""), ""]
    if hits:
        report.append("命中（务必人工确认上下文）：")
        report += [f"  ✖ [{h['category']}] {h['word']}　…{h['context']}…" for h in hits]
        report.append("")
    if noted:
        report.append("疑似误报（上下文是「不碰/已规避/替换为」这类表述，不算命中）：")
        report += [f"  · {h['word']}　…{h['context']}…" for h in noted[:8]]
        report.append("")
    report.append("以下条款来自手册，请逐条人工确认（本平台不替你判断）：")
    for g in data.get("groups") or []:
        report.append(f"  ■ {g['title']}")
        for sub in g.get("subs") or []:
            if sub.get("title"):
                report.append(f"    · {sub['title']}")
            for it in sub.get("items") or []:
                report.append(f"      - {it}")
    return {"ok": not hits, "kind": "blacklist", "hits": hits, "noted": noted,
            "groups": data.get("groups") or [], "source": data.get("source") or "",
            "report": "\n".join(report)}


# ---------------------------------------------------------------- ② 违禁词扫描

def _save_report(book_id: str, kind: str, ok: bool, report: str, chapter_id: str = "") -> None:
    store.insert("checks", {"id": store.new_id("chk"), "book_id": book_id,
                            "chapter_id": chapter_id, "kind": kind,
                            "ok": 1 if ok else 0, "report": report, "created_at": store.now()})


def violations_check(book_id: str, chapter_id: str = "") -> Dict[str, Any]:
    v = assets.vocab()
    if chapter_id:
        ch = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id])
        text = (ch or {}).get("content") or ""
        title = f"第 {(ch or {}).get('no')} 章"
    else:
        text, title = _book_text(book_id), "全书设定"
    if not text.strip():
        raise ValueError("没有可检查的正文（先写点内容或生成一章）")

    # 短故事放宽词：短故事里「报警 / 警察」是推进剧情的常见动作，按一级拦会大量误报。
    # 只对短故事流程放宽成「提示」，长篇/中篇仍按一级处理。
    book_row = store.query_one("SELECT flow_id FROM books WHERE id=?", [book_id]) or {}
    is_short = (book_row.get("flow_id") or "") in ("fanqie_short", "short_story")
    RELAX_SHORT = {"警察", "公安"}

    blocks, warns = [], []
    for cat, words in (v.get("level1") or {}).items():
        hit = [h for h in _find(text, words) if not h["negated"]]
        if is_short:
            relaxed = [h for h in hit if h["word"] in RELAX_SHORT]
            hit = [h for h in hit if h["word"] not in RELAX_SHORT]
            if relaxed:
                warns.append("[短故事放宽·提示] " + " / ".join(
                    f"{h['word']}（…{h['context'][:20]}…）" for h in relaxed[:4]))
        if hit:
            blocks.append(f"[一级·{cat}] " + " / ".join(f"{h['word']}（…{h['context'][:26]}…）"
                                                        for h in hit[:4]))
    for lvl, words in (("二级", v.get("level2") or []), ("三级", v.get("level3") or [])):
        hit = [h for h in _find(text, words) if not h["negated"]]
        if hit:
            warns.append(f"[{lvl}] " + " / ".join(f"{h['word']}×{len([x for x in hit if x['word'] == h['word']])}"
                                                  for h in hit[:8]))
    replace = [f"{w}×{text.count(w)} → 建议 {r}"
               for w, r in (v.get("replace") or {}).items() if text.count(w) >= 2]
    ai = {w: text.count(w) for w in (v.get("ai_words") or []) if w in text}

    n = textkit.count_words(text)
    paras = [p for p in _PARA_SPLIT.split(text) if p.strip()]
    sents = [len(re.findall(r"[。！？…]", p)) or 1 for p in paras] or [1]
    avg = sum(sents) / len(sents)
    dialogue = sum(len(m.group(0)) for m in re.finditer(r"[“\"]([^”\"]{1,300})[”\"]", text))
    ratio = dialogue / max(1, len(text)) * 100
    lines = [l for l in text.strip().splitlines() if l.strip()]
    tail = " ".join(lines[-3:])

    rep = [f"【违禁词与 AI 痕迹扫描 · {title}】",
           f"结论：{'❌ 存在一级违禁词，必须处理后才能发布' if blocks else '✅ 无一级违禁词'}",
           f"· 字数：{n}", f"· 段落：{len(paras)} 段，平均 {avg:.1f} 句/段",
           f"· 对白占比：{ratio:.1f}%",
           f"· AI 特征词命中 {len(ai)} 种" +
           ("，" + " / ".join(f"{w}×{c}" for w, c in ai.items()) if ai else "（干净 ✅）")]
    if blocks:
        rep += ["", "【一级违禁词（必须清除）】"] + [f"  ✖ {b}" for b in blocks]
    if warns:
        rep += ["", "【需要留意（不阻断）】"] + [f"  ⚠ {w}" for w in warns]
    if replace:
        rep += ["", "【替换建议】"] + [f"  · {s}" for s in replace]
    if not _HOOK.search(tail):
        rep += ["", "【章末钩子】结尾 3 行没有对话/悬念句，检查钩子是否够强"]
    out = {"ok": not blocks, "kind": "violations", "blocks": blocks, "warns": warns,
           "replace": replace, "ai_words": ai, "words": n,
           "report": "\n".join(rep)}
    _save_report(book_id, "violations", out["ok"], out["report"], chapter_id)
    return out


# ---------------------------------------------------------------- ③ 投稿前自查

def presubmit_check(book_id: str, ticks: Iterable[str] = ()) -> Dict[str, Any]:
    items = assets.presubmit_items()
    if not items:
        items = ["题材核对：避开平台黑名单题材", "字数核对：符合平台投稿字数要求",
                 "开篇核对：前 300 字出现冲突、具备钩子", "人设核对：逻辑通顺、无 OOC",
                 "参数核对：年龄/金钱/时间/身份前后统一", "逻辑 bug：无剧情漏洞与前后冲突",
                 "AI 痕迹：无模板化句式与空洞套话", "章节钩子：每章结尾有实体危机/悬念",
                 "格式排版：段落简短、长句已拆", "原创提醒：禁止直接复制范文投稿"]
    ticks = set(ticks or [])
    chapters = store.query("SELECT * FROM chapters WHERE book_id=? ORDER BY no", [book_id])
    v = assets.vocab()
    ai_hits = sum((ch.get("content") or "").count(w)
                  for ch in chapters for w in (v.get("ai_words") or []))
    words = [int(ch.get("words") or 0) for ch in chapters]
    first = next((ch for ch in chapters if ch.get("content")), None)

    auto: Dict[str, str] = {}
    if chapters:
        auto["字数核对"] = (f"共 {len(chapters)} 章，各章字数："
                            + "、".join(str(w) for w in words[:12])
                            + ("…" if len(words) > 12 else ""))
        auto["AI 痕迹"] = f"手册特征词在全书中出现 {ai_hits} 次" + ("（偏多，重点清）" if ai_hits > 10 else "（不多）")
        if first:
            head = (first.get("content") or "")[:300]
            auto["开篇核对"] = ("前 300 字里" + ("有" if _HOOK.search(head) else "没有") + "对话，"
                                + ("✓" if _HOOK.search(head) else "建议把冲突/对话提到最前面"))
        tails = []
        for ch in chapters:
            ls = [l for l in (ch.get("content") or "").strip().splitlines() if l.strip()]
            if ls and not _HOOK.search(" ".join(ls[-3:])):
                tails.append(str(ch.get("no")))
        auto["章节钩子"] = ("每章结尾都有对话/悬念句 ✓" if not tails
                            else f"第 {'、'.join(tails)} 章结尾像收尾，检查钩子")
    else:
        auto["字数核对"] = "还没有章节正文"

    rows = [{"text": t, "ticked": t in ticks,
             "auto": auto.get(t.split("：")[0].split(":", 1)[0], "")} for t in items]
    done = sum(1 for r in rows if r["ticked"])
    rep = ["【投稿前自查核对清单】", f"进度：{done}/{len(rows)} 项已确认",
           "（清单原文逐项解析自知识库 01 模块「投稿自查核对清单」）", ""]
    for r in rows:
        rep.append(f"  {'☑' if r['ticked'] else '□'} {r['text']}"
                   + (f"\n      ↳ {r['auto']}" if r["auto"] else ""))
    out = {"ok": done == len(rows), "kind": "presubmit", "items": rows,
           "done": done, "total": len(rows), "report": "\n".join(rep)}
    _save_report(book_id, "presubmit", out["ok"], out["report"])
    return out


def latest(book_id: str, kind: str) -> Dict[str, Any]:
    row = store.query_one(
        "SELECT * FROM checks WHERE book_id=? AND kind=? ORDER BY created_at DESC LIMIT 1",
        [book_id, kind])
    return row or {}
