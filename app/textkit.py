"""文本工具：正文字数与切片（全平台唯一口径）。

口径：正文 = 去掉章节标题行、去掉末尾【本章摘要】、去掉所有空白后的字符数。
"""
from __future__ import annotations

import re

_TITLE = re.compile(r"^\s*#{0,6}\s*\*{0,2}\s*第\s*\d+\s*章[^\n]*\n?")
_SUMMARY = re.compile(r"\n*【本章摘要】[\s\S]*$")
_WS = re.compile(r"\s+")


def strip_summary(text: str) -> str:
    return _SUMMARY.sub("", text or "")


def count_words(text: str) -> int:
    """正文字数：不计标题行 / 摘要 / 空白。"""
    return len(_WS.sub("", _TITLE.sub("", strip_summary(text or ""), count=1)).strip())


def tail(text: str, n: int = 600) -> str:
    """取结尾节选（用于"承接上一章"）。"""
    t = (text or "").strip()
    return t[-n:] if len(t) > n else t


def strip_fences(text: str) -> str:
    """去掉模型常见的 ``` 代码围栏。"""
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    return t.strip()


def clean_preamble(text: str) -> str:
    """去掉开头客套前言（仅当它很短且紧跟章节标题时）。"""
    t = (text or "").strip()
    m = re.search(r"第\s*\d+\s*章", t)
    if m and 0 < m.start() <= 200 and t[:m.start()].count("。") <= 2:
        return t[m.start():]
    return t


# ---------------------------------------------------------------- 正文形态体检

_EN_LINE = re.compile(r"[A-Za-z]")
_NUM_LINE = re.compile(r"^\d{1,4}[.、)]\s")
# 只认「行首 + 冒号」的剧本格式标记（如「镜头：」「场景：」「第3幕：」）。
# 早期版本全文匹配「镜头」，把直播/娱乐圈题材里「盯着镜头」这种正常叙述也判成了分镜，
# 整章被当成非正文拒收（实测《情书直播念给前男友听》5 章全部误杀）。
_SHOT = re.compile(r"^\s*(?:镜头|转场|分镜|画面|旁白|字幕|场景|景别|第[一二三四五六七八九十\d]+幕)\s*[：:]", re.M)
_SHOT_EN = re.compile(r"\b(CUT TO|FADE IN|FADE OUT|INT\.|EXT\.)\b")


def body_health(text: str) -> tuple[bool, str]:
    """正文形态体检：判断模型输出是否「像一篇中文小说正文」。

    为什么需要（2026-10-19 实测）：短故事第 1 章「去 AI 味」把英文分镜 / 编号提纲
    当正文吐了出来——7029 字里 35 行英文、23 行编号，中文字占比只有 53%。
    字数保护（骤降 65% / 暴涨 1.8 倍）拦不住这种情况（那次只涨到 1.71 倍）。

    这里只做「形态」判断，不改内容；命中异常时由调用方拒绝覆盖正文，
    原始输出仍留在生成记录里，由人来决定是否采用。

    返回 (是否像正文, 问题描述)。
    """
    lines = [l.strip() for l in (text or "").splitlines() if l.strip()]
    if not lines:
        return False, "输出为空"
    total_chars = sum(len(l) for l in lines)
    cn = len(re.findall(r"[\u4e00-\u9fa5]", text or ""))
    cn_ratio = cn / max(1, total_chars)
    en_lines = [l for l in lines if len(_EN_LINE.findall(l)) >= 8]
    num_lines = [l for l in lines if _NUM_LINE.match(l)]
    shot_hits = len(_SHOT.findall(text or "")) + len(_SHOT_EN.findall(text or ""))
    problems = []
    if cn_ratio < 0.55:
        problems.append(f"中文字占比只有 {cn_ratio:.0%}")
    if len(en_lines) / len(lines) > 0.08:
        problems.append(f"英文行 {len(en_lines)}/{len(lines)}")
    if len(num_lines) / len(lines) > 0.05:
        problems.append(f"编号列表行 {len(num_lines)}/{len(lines)}")
    if shot_hits >= 3:
        problems.append(f"分镜/剧本标记 {shot_hits} 处")
    return (not problems), "；".join(problems)
