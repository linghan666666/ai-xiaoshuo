"""结构化资产：把 skill 里的**清单类内容**解析成平台可用的检查项。

skill 里有三类天然结构化的内容，平台直接把它们变成功能：
  ① 01 模块「投稿自查核对清单」→ `□` 开头的 11 项 → 平台的「投稿前自查」勾选清单；
  ② 01 模块「违禁词清单 / 违规情节自查表」+ 03 模块「绝对不能碰题材黑名单」
     → 分级/分类条目 → 平台的「黑名单预检」清单（原文逐条展示，人工逐项确认）；
  ③ 检测词表（vocab.json）：由上述清单整理出的**检测用关键词**。
     ⚠️ 原文里的词是打了码的（GJ / Z治 / ZZ），无法直接用于扫描，
     所以这一份是"派生物"：条目来源在文件头注明，原文（含变体与规避手法说明）仍以知识库为准。
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from . import library

_VOCAB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                           "checks", "vocab.json")
_BULLET = re.compile(r"^[□☐✓✔]\s*")
_TOP = re.compile(r"^[一二三四五六七八九十]+[、.]\s*(.+)$")
_NUM = re.compile(r"^(\d+)\s*([\.、]?)\s*(.+)$")
_SENT_PUNCT = re.compile(r"[。；，`]|、|/|\|")
_SUB_TAIL = re.compile(r"(类|红线|禁区|严禁|专项|区|级)$")


def _split_levels(body: str) -> List[Dict[str, Any]]:
    """把「一、大类 → 1.子类 → 编号条目」式内容解析成分组。

    两处经验判据（来自 01/03 模块的真实写法）：
      · 「一级违禁词（…）」「一、ZZ/GJ/历史红线」这类 → 顶层分组；
      · 「1.Z治敏感类」「2.涉黄涉未成年红线」「1.涉 ZZ 敏感（零容忍）」→ 短、无句读、
        带"类/红线/禁区"等尾词 → 子类；反之带顿号/逗号的长句 → 条目。
    """
    groups: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {"title": "总览", "subs": []}
    sub: Dict[str, Any] = {"title": "", "items": []}

    def ensure_sub():
        nonlocal sub
        if sub["items"] or sub["title"]:
            return
        sub = {"title": "", "items": []}
        cur["subs"].append(sub)

    for line in body.splitlines():
        s = line.strip()
        if not s or s.startswith(("#", ">", "|", "—", "-")):
            continue
        m_top = _TOP.match(s)
        if m_top and len(s) <= 40:
            cur = {"title": m_top.group(1).strip(), "subs": []}
            groups.append(cur)
            sub = {"title": "", "items": []}
            continue
        m_num = _NUM.match(s)
        if m_num:
            text = m_num.group(3).strip()
            is_label = (len(text) <= 24 and not _SENT_PUNCT.search(text)) or \
                       bool(_SUB_TAIL.search(text))
            if is_label:
                sub = {"title": text, "items": []}
                cur["subs"].append(sub)
            else:
                ensure_sub()
                sub["items"].append(text)
            continue
        if re.match(r"^[一二三四五六七八九十]+级", s) and len(s) <= 40:
            cur = {"title": s, "subs": []}
            groups.append(cur)
            sub = {"title": "", "items": []}
            continue
        if len(s) <= 60:                       # 其余短行（如"2026严打"提示）算条目
            ensure_sub()
            sub["items"].append(s)
    if not groups and cur["subs"]:
        groups.append(cur)
    return groups


def _section(module: str, keyword: str) -> Optional[Dict[str, str]]:
    """在模块里找节标题含 keyword 的那一节（找不到就找正文含它的）。"""
    for f in library.module_files():
        if not f.startswith(module):
            continue
        for s in library.sections(f):
            if keyword in s["heading"]:
                return {"module": f, "heading": s["heading"],
                        "body": library.read_section(f, s["heading"]) or ""}
        full = library.read(f) or ""
        if keyword in full:
            return {"module": f, "heading": "（全文）", "body": full}
    return None


def checklist_items(module: str, keyword: str) -> List[str]:
    """抽取 `□` 清单项（如 01 的「投稿自查核对清单」、06 的「终稿核验清单」）。"""
    sec = _section(module, keyword)
    if not sec:
        return []
    out = []
    for line in sec["body"].splitlines():
        s = line.strip()
        if _BULLET.match(s):
            item = _BULLET.sub("", s).strip()
            if item:
                out.append(item)
    return out


def grouped_items(module: str, keyword: str) -> Dict[str, Any]:
    """把「大类 → 子类 → 编号条目」式内容解析成分组清单。"""
    sec = _section(module, keyword)
    if not sec:
        return {"module": "", "heading": "", "groups": []}
    return {"module": sec["module"], "heading": sec["heading"],
            "groups": _split_levels(sec["body"])}


def blacklist() -> Dict[str, Any]:
    """题材黑名单（03 模块首节）：一级大红线，开书前逐条确认。"""
    out = grouped_items("03", "黑名单")
    out["source"] = "03-选题与开篇模板库.md · 2026绝对不能碰题材黑名单"
    return out


def violation_rules() -> Dict[str, Any]:
    """违禁词清单（01 模块）：分级红线，投稿前逐条确认。"""
    out = grouped_items("01", "违禁词清单")
    out["source"] = "01-平台规则与合规红线.md · 2026.4番茄小说最新违禁词清单"
    return out


def violation_plots() -> Dict[str, Any]:
    """违规情节自查表（01 模块）。"""
    out = grouped_items("01", "违规情节自查表")
    out["source"] = "01-平台规则与合规红线.md · 真正绝对不能碰的违规情节自查表"
    return out


def presubmit_items() -> List[str]:
    """投稿前自查核对清单（01 模块的 `□` 清单）。"""
    items = checklist_items("01", "投稿自查核对清单")
    if not items:
        items = checklist_items("01", "投稿自查")
    return items


def vocab() -> Dict[str, Any]:
    """检测词表（派生物，来源见文件内 _note）。"""
    try:
        with open(_VOCAB_PATH, encoding="utf-8") as f:
            return json.load(f)
    except Exception:  # noqa: BLE001
        return {"_note": "词表文件缺失", "level1": {}, "level2": [], "level3": [],
                "replace": {}, "ai_words": []}
