"""检查路由：黑名单预检 / 违禁词扫描 / 投稿前自查（依据全部来自 skill）。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from .. import store
from ..checks import checks

router = APIRouter()


@router.post("/api/books/{pid}/check/blacklist")
def check_blacklist(pid: str):
    if not store.query_one("SELECT id FROM books WHERE id=?", [pid]):
        raise HTTPException(404, "书不存在")
    return {"ok": True, **checks.blacklist_check(pid)}


@router.post("/api/books/{pid}/check/violations")
def check_violations(pid: str, data: dict = Body(default={})):
    if not store.query_one("SELECT id FROM books WHERE id=?", [pid]):
        raise HTTPException(404, "书不存在")
    try:
        return {"ok": True, **checks.violations_check(pid, (data.get("chapter_id") or "").strip())}
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@router.post("/api/books/{pid}/check/presubmit")
def check_presubmit(pid: str, data: dict = Body(default={})):
    if not store.query_one("SELECT id FROM books WHERE id=?", [pid]):
        raise HTTPException(404, "书不存在")
    return {"ok": True, **checks.presubmit_check(pid, data.get("ticks") or [])}


@router.post("/api/books/{pid}/check/violations-all")
def check_violations_all(pid: str):
    """全书批量扫描：逐章跑违禁词扫描并汇总（哪几章有硬伤，一眼看到）。"""
    if not store.query_one("SELECT id FROM books WHERE id=?", [pid]):
        raise HTTPException(404, "书不存在")
    rows = [c for c in store.query("SELECT * FROM chapters WHERE book_id=? ORDER BY no", [pid])
            if (c.get("content") or "").strip()]
    if not rows:
        raise HTTPException(400, "还没有章节正文可扫描")
    results, summary, total_ai = [], [], 0
    for ch in rows:
        r = checks.violations_check(pid, ch["id"])
        total_ai += len(r["ai_words"])
        results.append({"no": ch["no"], "title": ch.get("title") or "",
                        "ok": r["ok"], "blocks": len(r["blocks"]),
                        "warns": len(r["warns"]), "ai_words": len(r["ai_words"]),
                        "words": r["words"]})
        if r["blocks"]:
            summary.append(f"第{ch['no']}章：" + "；".join(r["blocks"]))
    rep = [f"【全书违禁词扫描 · 共 {len(rows)} 章】",
           f"结论：{'❌ 有 ' + str(len(summary)) + ' 章存在一级违禁词' if summary else '✅ 全书无一级违禁词'}",
           f"AI 特征词种类合计：{total_ai}（按章去重前的累计）", ""]
    for x in results:
        rep.append(f"  第{x['no']:>3}章 {'✖' if not x['ok'] else '✓'} {x['words']} 字"
                   f"｜一级 {x['blocks']}｜提示 {x['warns']}｜AI 词 {x['ai_words']}  {x['title']}")
    if summary:
        rep += ["", "【必须处理】"] + ["  " + s for s in summary]
    return {"ok": not summary, **{"chapters": results, "report": "\n".join(rep),
                                  "bad_chapters": [x["no"] for x in results if not x["ok"]]}}


@router.get("/api/books/{pid}/checks")
def list_checks(pid: str):
    out = {}
    for kind in ("blacklist", "violations", "presubmit"):
        out[kind] = checks.latest(pid, kind)
    return {"ok": True, "checks": out}
