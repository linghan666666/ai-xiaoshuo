"""skill 知识库：载入 13 个模块 → 原文节（section）索引 + 检索 + 全文读取。

模块文件由 skill 整理而成，结构稳定：每篇原始资料是一个 `## 📄 原文件名` 节。
本层把它们解析成「模块 → 节」两级索引，供平台三处使用：
  ① 知识库页签（浏览/检索）；② 流程步骤的指令原文（blocks.py）；③ 检查清单（assets.py）。
解析结果按文件 mtime 缓存，改文件后自动重载。
"""
from __future__ import annotations

import os
import re
import threading
from typing import Any, Dict, List, Optional

from ..config import LOCAL_SKILL_DIR, MODULE_ROUTING, SKILL_DIR, SKILL_MD

_SEC_RE = re.compile(r"^## 📄 (.+?)\s*$", re.M)
_cache: Dict[str, Any] = {}
_lock = threading.Lock()

# 模块用途（取自 SKILL.md 路由表）
_PURPOSE: Dict[str, str] = {}
for _intent, _file, _tag in MODULE_ROUTING:
    _PURPOSE.setdefault(_file, _tag)


def module_file(name: str) -> Optional[str]:
    """把模块名（文件名或 02d 这类简称）规范化为存在的文件名。"""
    name = (name or "").strip()
    if not name:
        return None
    files = module_files()
    if name in files:
        return name
    for f in files:
        if f.startswith(name):
            return f
    return None


def module_files() -> List[str]:
    if not os.path.isdir(SKILL_DIR):
        return []
    return sorted(f for f in os.listdir(SKILL_DIR) if f.endswith(".md"))


def _read_file(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            return f.read()
    except Exception:  # noqa: BLE001
        return ""


def _sections_of(name: str) -> List[Dict[str, Any]]:
    """把模块切成节：[{i, heading, body, chars}]（首节是模块说明，heading='（模块说明）'）。"""
    path = os.path.join(SKILL_DIR, name)
    try:
        mtime = os.path.getmtime(path)
    except OSError:
        return []
    key = f"{path}:{mtime}"
    with _lock:
        if key in _cache:
            return _cache[key]
    text = _read_file(path)
    out: List[Dict[str, Any]] = []
    marks = list(_SEC_RE.finditer(text))
    if not marks:
        out.append({"i": 0, "heading": "（全文）", "body": text.strip(), "chars": len(text)})
    else:
        head = text[: marks[0].start()].strip()
        if head:
            out.append({"i": 0, "heading": "（模块说明）", "body": head, "chars": len(head)})
        for idx, m in enumerate(marks, 1):
            end = marks[idx].start() if idx < len(marks) else len(text)
            body = text[m.end():end].strip()
            out.append({"i": idx, "heading": m.group(1).strip(), "body": body, "chars": len(body)})
    with _lock:
        _cache[key] = out
    return out


def modules() -> List[Dict[str, Any]]:
    """模块清单（含用途、字数、节数）——平台"以 skill 为核心"的目录。"""
    out = []
    for f in module_files():
        secs = _sections_of(f)
        out.append({
            "file": f,
            "tag": f[:3].rstrip("-"),
            "name": f[:-3],
            "purpose": _PURPOSE.get(f, ""),
            "chars": sum(s["chars"] for s in secs),
            "sections": len(secs),
        })
    return out


def read(name: str) -> Optional[str]:
    f = module_file(name)
    if not f:
        return None
    return _read_file(os.path.join(SKILL_DIR, f))


def sections(name: str, min_chars: int = 0) -> List[Dict[str, Any]]:
    f = module_file(name)
    if not f:
        return []
    return [{"heading": s["heading"], "chars": s["chars"]}
            for s in _sections_of(f) if s["chars"] >= min_chars]


def sections_full(name: str) -> List[Dict[str, Any]]:
    """返回节的全部字段（含正文）——供 blocks.py 抽取指令用，避免跨模块调私有函数。"""
    f = module_file(name)
    return _sections_of(f) if f else []


def read_section(name: str, heading: str) -> Optional[str]:
    f = module_file(name)
    if not f:
        return None
    for s in _sections_of(f):
        if s["heading"] == heading:
            return s["body"]
    return None


def search(query: str, module: str = "", max_chars: int = 4000) -> Dict[str, Any]:
    """关键词检索（支持空格分隔的多个词，全部命中才算）。返回结构与前端约定一致。"""
    terms = [t for t in (query or "").split() if t]
    files = [module_file(module)] if module else module_files()
    files = [f for f in files if f]
    results: List[Dict[str, Any]] = []
    for f in files:
        text = _read_file(os.path.join(SKILL_DIR, f))
        lines = text.splitlines()
        for i, ln in enumerate(lines):
            if all(t in ln for t in terms):
                results.append({
                    "module": f, "line": i + 1, "match": ln.strip()[:160],
                    "context": "\n".join(lines[max(0, i - 2): i + 5]).strip(),
                })
                if len(results) >= 60:
                    break
        if len(results) >= 60:
            break
    limit = max(500, min(20000, int(max_chars or 4000)))
    out, used = [], 0
    for r in results:
        block = f"【{r['module']} · 第 {r['line']} 行】\n{r['context']}"
        if used + len(block) > limit:
            break
        out.append(block)
        used += len(block)
    return {"ok": True, "query": query, "hits": len(results), "shown": len(out),
            "chars": used, "report": "\n\n" + "-" * 30 + "\n\n".join(out) if out
            else "（没找到匹配段落，换个关键词试试）"}


def _dir_stats(d: str) -> Dict[str, Any]:
    if not os.path.isdir(d):
        return {"exists": False, "files": 0, "chars": 0}
    files = [f for f in os.listdir(d) if f.endswith(".md")]
    return {"exists": True, "files": len(files),
            "chars": sum(len(_read_file(os.path.join(d, f))) for f in files)}


def _local_compare() -> Dict[str, Any]:
    """对比「平台正在用的知识库」与「本机用户级 skill」，提示是否需要切换。

    ⚠️ 两边必须用同一口径（整文件字数）比较：modules() 里的字数是"节正文"之和
    （不含 `## 📄` 标题行），拿它跟整文件字数比会永远报"不一致"——那是假告警。
    """
    cur = _dir_stats(SKILL_DIR)
    local = _dir_stats(LOCAL_SKILL_DIR)
    same_path = os.path.abspath(LOCAL_SKILL_DIR) == os.path.abspath(SKILL_DIR)
    note = ""
    if not local["exists"]:
        note = "未检测到本机用户级 skill 目录（平台用内置副本即可，忽略本条）。"
    elif same_path:
        note = "平台当前直接使用本机 skill。"
    elif local["chars"] != cur["chars"] or local["files"] != cur["files"]:
        note = ("本机 skill 与内置副本不一致（本机 %d 文件 / %d 字；内置 %d 文件 / %d 字）。"
                "若本机那份更新，可在 .env 设 PLATFORM_SKILL_DIR 指向它。" %
                (local["files"], local["chars"], cur["files"], cur["chars"]))
    else:
        note = "内置副本与本机 skill 内容一致。"
    return {"dir": LOCAL_SKILL_DIR, **local, "same_as_current": same_path,
            "current": cur, "note": note}


def info() -> Dict[str, Any]:
    mods = modules()
    return {
        "ok": True,
        "dir": SKILL_DIR,
        "available": bool(mods),
        "skill_md": os.path.isfile(SKILL_MD),
        "modules": mods,
        "total_chars": sum(m["chars"] for m in mods),
        "total_sections": sum(m["sections"] for m in mods),
        "routing": [{"intent": i, "file": f, "tag": t} for i, f, t in MODULE_ROUTING],
        "local": _local_compare(),
    }


def clear_cache() -> int:
    """清空模块解析缓存（改文件后想立即重载时用；平时按 mtime 自动失效）。"""
    with _lock:
        n = len(_cache)
        _cache.clear()
    return n
