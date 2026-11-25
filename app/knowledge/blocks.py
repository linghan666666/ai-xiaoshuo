"""指令块抽取：把 skill 的**原文节**变成可直接执行的「指令」。

这是平台的核心机制 —— skill 的原始指令措辞是反复调过的，**原样使用**效果最好
（skill 自己也强调：要指令时给原文，不要重写压缩）。所以：

  流程步骤不自己编提示词，而是声明「去哪些模块、找哪些关键词」，
  本模块按关键词在指定模块里挑出最相关的**原文节**，
  再把节正文（或其中 "指令：/【指令X：" 之后的正文）交给模型 / 给用户复制。

抽取规则：
  1) 只在步骤声明的模块里找（模块名支持 `02b` 这类简称，由 library 规范化）；
  2) 打分 = 关键词在标题命中×3 + 正文命中次数 + 指令特征（"指令：""请""要求"等）；
     标题/正文同时命中的节优先 —— 因为 skill 的节标题就是原文件名，标题命中通常意味着
     "这一节就是讲这件事的"；
  3) 返回原文（未改写），附出处（模块 + 节标题 + 字数），并给出 `directive`
     字段：若正文里有 "指令：" 标记，则从该标记起截取，方便一键丢给模型。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ..config import BLOCK_MAX_CHARS, BLOCKS_PER_STEP
from . import library

_DIRECTIVE_MARKS = ("指令：", "指令:", "【指令", "指令如下", "提示词：", "请对我", "请你对")
_INSTR_MARKERS = ("必须", "禁止", "要求", "公式", "模板", "示例", "步骤", "写法", "技巧", "不要")


def _directive_of(body: str) -> str:
    """从节的正文里切出「可执行指令」部分（找不到标记就返回全文）。"""
    for mk in _DIRECTIVE_MARKS:
        i = body.find(mk)
        if 0 <= i <= max(600, len(body) // 3):
            return body[i:].strip()
    return body.strip()


def _score(sec: Dict[str, Any], keywords: List[str]) -> tuple:
    title = sec["heading"]
    body = sec["body"]
    kw_hit = 0
    title_hit = sum(1 for k in keywords if k in title)
    for k in keywords:
        kw_hit += min(body.count(k), 5)
    instr = sum(1 for m in _INSTR_MARKERS if m in body[:600])
    txt_like = 1 if re.search(r"\.(txt|docx?|pdf)", title) else 0
    # 标题命中权重提高：标签（追妻火葬场/真假千金）就是靠标题命中的，
    # 正文命中次数再多也不该盖过「这一节就是讲这个题材」。
    return (title_hit * 6 + kw_hit + instr, title_hit, txt_like)


def select_blocks(modules: List[str], keywords: List[str],
                  limit: Optional[int] = None, exclude: Optional[List[str]] = None,
                  avoid_terms: Optional[List[str]] = None) -> List[Dict[str, Any]]:
    """在指定模块里挑出与关键词最相关的原文节。返回按相关度排序的块列表。

    exclude：要排除的**模块**前缀（如男频书排除女频模块 02c）。
    avoid_terms：要排除的**节标题关键词**——受众错配的节常常混在通用模块里
    （实测：03 模块内部就有《女频逆袭打脸流·黄金三章开局模板》，男频书抽到它既占额度又带偏文风）。
    """
    limit = limit or BLOCKS_PER_STEP
    keywords = [k for k in (keywords or []) if k]
    bad = tuple(exclude or ())
    avoid = tuple(avoid_terms or ())
    cands: List[Dict[str, Any]] = []
    for name in modules:
        f = library.module_file(name)
        if not f:
            continue
        if bad and f.startswith(bad):
            continue
        for sec in library.sections_full(f):
            if avoid and any(t in sec["heading"] for t in avoid):
                continue
            if sec["heading"] == "（模块说明）" or sec["chars"] < 200:
                continue
            sc = _score(sec, keywords)
            if sc[0] <= 0:
                continue
            # 官方课堂（15）优先：赛道指令（02b/02c）节长、命中词多，会把 15 挤出额度，
            # 而 15 讲的正是「节奏/冲突/爽点/追妻链路」这类最容易翻车的具体改法。
            _boost = 8 if f.startswith("15-") else 0
            body = sec["body"]
            cands.append({
                "module": f, "heading": sec["heading"], "score": sc[0] + _boost,
                "chars": min(len(body), BLOCK_MAX_CHARS),
                "text": body[:BLOCK_MAX_CHARS],
                "directive": _directive_of(body)[:BLOCK_MAX_CHARS],
            })
    cands.sort(key=lambda c: (-c["score"], c["chars"]))
    return cands[:limit]


def find_blocks(modules: List[str], keywords: List[str], limit: Optional[int] = None) -> Dict[str, Any]:
    """给接口用：抽取结果 + 统计。"""
    blocks = select_blocks(modules, keywords, limit)
    return {
        "ok": True, "modules": modules, "keywords": keywords,
        "count": len(blocks), "chars": sum(b["chars"] for b in blocks),
        "blocks": blocks,
    }
