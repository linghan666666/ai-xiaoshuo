"""知识库路由：浏览 / 检索 / 指令块抽取 / 清单资产。"""
from __future__ import annotations

from fastapi import APIRouter, Body, HTTPException

from ..knowledge import assets, blocks, library

router = APIRouter()


@router.get("/api/kb/info")
def kb_info():
    return library.info()


@router.get("/api/kb/module")
def kb_module(name: str):
    text = library.read(name)
    if text is None:
        raise HTTPException(404, f"模块不存在：{name}")
    return {"ok": True, "name": name, "text": text, "chars": len(text),
            "sections": library.sections(name)}


@router.get("/api/kb/section")
def kb_section(name: str, heading: str):
    text = library.read_section(name, heading)
    if text is None:
        raise HTTPException(404, "找不到该节")
    return {"ok": True, "name": name, "heading": heading, "text": text, "chars": len(text)}


@router.post("/api/kb/search")
def kb_search(data: dict = Body(default={})):
    q = (data.get("query") or "").strip()
    if not q:
        raise HTTPException(400, "请输入关键词")
    return library.search(q, module=(data.get("module") or "").strip(),
                          max_chars=int(data.get("max_chars") or 4000))


@router.post("/api/kb/blocks")
def kb_blocks_api(data: dict = Body(default={})):
    """按「模块 + 关键词」抽取 skill 原文指令块（流程步骤的指令就是这么来的）。"""
    mods = data.get("modules") or []
    kws = data.get("keywords") or []
    if not mods:
        raise HTTPException(400, "请给出 modules（如 [\"02d\",\"05\"]）")
    return blocks.find_blocks(mods, kws, limit=int(data.get("limit") or 0) or None)


@router.get("/api/kb/blacklist")
def kb_blacklist():
    return {"ok": True, **assets.blacklist()}


@router.get("/api/kb/violation-rules")
def kb_violation_rules():
    return {"ok": True, "words": assets.violation_rules(), "plots": assets.violation_plots()}


@router.get("/api/kb/presubmit-items")
def kb_presubmit_items():
    return {"ok": True, "items": assets.presubmit_items()}
