"""番茄短故事工作台：分类、设置、章节骨架、单篇预览、发布导出。

对应番茄作家专区「短故事」发布页（2026.4 分类体系）的实际规则：
  · 发表超 **6000 字**即有机会签约；
  · **单篇完结**上传，正文用「第N章 标题」划分章节（页面左侧「暂未设置章节」提示）；
  · 作品分类**最多选 8 个，主分类必选**；
  · 要声明**是否使用 AI**、设置**试读比例**（影响解锁转化与收入）、准备封面。

这里不另造一套写作逻辑：短故事复用 flows 里的 fanqie_short（8 步流水线），
本章只负责"发布规格"这一层——分类、骨架、合并预览、导出。
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, HTTPException

from .. import store
from ..config import DATA_DIR
from ..flows import definitions
from ..short_categories import (
    BY_KEY,
    CATEGORIES,
    CHANNEL_TO_AUDIENCE,
    DEFAULT_TRIAL_RATIO,
    MAX_CATEGORIES,
    MIN_WORDS,
    category_names,
    main_channel,
)

router = APIRouter()

FLOW_ID = "fanqie_short"
# 五章骨架的默认小标题：来自 06 模块的「黄金五段」，用户可以改
DEFAULT_CHAPTER_TITLES = ["困境引爆", "压迫升级", "首次反击", "高潮决战", "结局收束"]
MAX_WORDS = 30000


# ---------------------------------------------------------------- 工具

def _book_or_404(bid: str) -> Dict[str, Any]:
    b = store.query_one("SELECT * FROM books WHERE id=?", [bid])
    if not b:
        raise HTTPException(404, "书不存在")
    return b


def _safe_name(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip()).strip(" ._")[:80] or "未命名"


def _meta(bid: str) -> Dict[str, Any]:
    row = store.query_one("SELECT * FROM short_meta WHERE book_id=?", [bid])
    if not row:
        return {"book_id": bid, "main_category": "", "sub_categories": [],
                "use_ai": 0, "trial_ratio": DEFAULT_TRIAL_RATIO, "cover_note": ""}
    try:
        subs = json.loads(row.get("sub_categories") or "[]")
    except Exception:  # noqa: BLE001
        subs = []
    return {
        "book_id": bid,
        "main_category": row.get("main_category") or "",
        "sub_categories": subs if isinstance(subs, list) else [],
        "use_ai": int(row.get("use_ai") or 0),
        "trial_ratio": int(row.get("trial_ratio") or DEFAULT_TRIAL_RATIO),
        "cover_note": row.get("cover_note") or "",
        "updated_at": row.get("updated_at") or "",
    }


def _save_meta(bid: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    row = {
        "book_id": bid,
        "main_category": meta.get("main_category", ""),
        "sub_categories": json.dumps(meta.get("sub_categories") or [], ensure_ascii=False),
        "use_ai": int(meta.get("use_ai") or 0),
        "trial_ratio": int(meta.get("trial_ratio") or DEFAULT_TRIAL_RATIO),
        "cover_note": meta.get("cover_note", ""),
        "updated_at": store.now(),
    }
    if store.query_one("SELECT book_id FROM short_meta WHERE book_id=?", [bid]):
        store.execute(
            "UPDATE short_meta SET main_category=?, sub_categories=?, use_ai=?, trial_ratio=?, cover_note=?, updated_at=? WHERE book_id=?",
            [row["main_category"], row["sub_categories"], row["use_ai"], row["trial_ratio"],
             row["cover_note"], row["updated_at"], bid],
        )
    else:
        store.insert("short_meta", row)
    return _meta(bid)


def _genre_of(main: str, subs: List[str]) -> str:
    names = category_names([main] + list(subs))
    channel = main_channel(main)
    return (channel + " · " + " / ".join(names)) if names else ""


def _clean_categories(main: str, subs: List[str]) -> List[str]:
    """保留主分类之外的作品标签：一级分类（BY_KEY）+ 二级标签（short_tags.SUBTAGS）。"""
    from ..short_tags import SUBTAGS as _SUBTAGS
    allowed = set(BY_KEY) | set(_SUBTAGS)
    out: List[str] = []
    for k in subs or []:
        k = (k or "").strip()
        if k in allowed and k != main and k not in out:
            out.append(k)
    if main in BY_KEY and len(out) + 1 > MAX_CATEGORIES:
        out = out[: MAX_CATEGORIES - 1]
    return out


def _chapters(bid: str) -> List[Dict[str, Any]]:
    return store.query(
        "SELECT id, no, title, brief, content, words, status FROM chapters WHERE book_id=? ORDER BY no",
        [bid],
    )


def _merged_text(chapters: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    for c in chapters:
        title = (c.get("title") or "").strip() or f"第{c['no']}章"
        head = f"第{c['no']}章 {title}" if not title.startswith("第") else title
        body = (c.get("content") or "").strip()
        parts.append(head + ("\n\n" + body if body else ""))
    return "\n\n".join(parts).strip()


def _trial_line(ratio: int, chapters: List[Dict[str, Any]], total: int) -> str:
    """按字数把试读比例换算到"卡在第几章"——番茄的试读线决定哪里开始要解锁。"""
    if total <= 0 or not chapters:
        return "正文还没写，暂时换算不出试读线"
    target = total * max(0, min(100, ratio)) / 100.0
    acc = 0
    for c in chapters:
        w = int(c.get("words") or 0)
        acc += w
        if acc >= target:
            return f"约卡在第 {c['no']} 章（累计 {acc} 字 / 全文 {total} 字）"
    return f"约卡在最后一章末尾（全文 {total} 字）"


# ---------------------------------------------------------------- 分类

@router.get("/api/short/categories")
def list_categories():
    return {
        "ok": True,
        "categories": CATEGORIES,
        "max_selectable": MAX_CATEGORIES,
        "min_words": MIN_WORDS,
        "max_words": MAX_WORDS,
        "default_trial_ratio": DEFAULT_TRIAL_RATIO,
    }


# ---------------------------------------------------------------- 书目

@router.get("/api/short/books")
def list_short_books():
    rows = store.query(
        "SELECT * FROM books WHERE flow_id=? ORDER BY updated_at DESC", [FLOW_ID]
    )
    out = []
    for b in rows:
        m = _meta(b["id"])
        chs = _chapters(b["id"])
        out.append({
            "id": b["id"],
            "name": b["name"],
            "genre": b.get("genre") or "",
            "brief": b.get("brief") or "",
            "audience": b.get("audience") or "none",
            "chapters": len(chs),
            "words": sum(int(c.get("words") or 0) for c in chs),
            "meta": m,
            "updated_at": b.get("updated_at") or "",
        })
    return {"ok": True, "books": out}


@router.post("/api/short/books")
def create_short_book(data: Dict[str, Any] = Body(...)):
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "请填短故事名称")
    main = (data.get("main_category") or "").strip()
    if main not in BY_KEY:
        raise HTTPException(400, "必须选择主分类")
    subs = _clean_categories(main, [str(x) for x in (data.get("sub_categories") or [])])
    use_ai = 1 if data.get("use_ai") else 0
    try:
        ratio = int(data.get("trial_ratio") or DEFAULT_TRIAL_RATIO)
    except (TypeError, ValueError):
        ratio = DEFAULT_TRIAL_RATIO
    ratio = max(0, min(100, ratio))
    brief = (data.get("brief") or "").strip()
    flow = definitions.get_flow(FLOW_ID) or definitions.get_flow("short_story")
    bid = store.new_id("book")
    store.insert("books", {
        "id": bid,
        "name": name,
        "genre": _genre_of(main, subs),
        "audience": CHANNEL_TO_AUDIENCE.get(main_channel(main), "none"),
        "brief": brief,
        "platform": "番茄短故事",
        "flow_id": FLOW_ID,
        "step_id": flow["steps"][0]["id"],
        "created_at": store.now(),
        "updated_at": store.now(),
    })
    meta = _save_meta(bid, {
        "main_category": main,
        "sub_categories": subs,
        "use_ai": use_ai,
        "trial_ratio": ratio,
        "cover_note": (data.get("cover_note") or "").strip(),
    })
    book = _book_or_404(bid)
    return {"ok": True, "book": book, "meta": meta}


@router.get("/api/short/books/{bid}")
def get_short_book(bid: str):
    b = _book_or_404(bid)
    m = _meta(bid)
    chs = _chapters(bid)
    return {
        "ok": True,
        "book": b,
        "meta": m,
        "categories": [BY_KEY[k] for k in ([m["main_category"]] + m["sub_categories"]) if k in BY_KEY],
        "chapters": chs,
        "total_words": sum(int(c.get("words") or 0) for c in chs),
    }


@router.put("/api/short/books/{bid}/meta")
def update_short_meta(bid: str, data: Dict[str, Any] = Body(...)):
    book = _book_or_404(bid)
    m = _meta(bid)
    if "main_category" in data:
        main = (data.get("main_category") or "").strip()
        if main not in BY_KEY:
            raise HTTPException(400, "主分类无效")
        m["main_category"] = main
    if "sub_categories" in data:
        m["sub_categories"] = _clean_categories(
            m["main_category"], [str(x) for x in (data.get("sub_categories") or [])]
        )
    else:
        m["sub_categories"] = _clean_categories(m["main_category"], m["sub_categories"])
    if "use_ai" in data:
        m["use_ai"] = 1 if data.get("use_ai") else 0
    if "trial_ratio" in data:
        try:
            m["trial_ratio"] = max(0, min(100, int(data.get("trial_ratio") or DEFAULT_TRIAL_RATIO)))
        except (TypeError, ValueError):
            pass
    if "cover_note" in data:
        m["cover_note"] = (data.get("cover_note") or "").strip()
    meta = _save_meta(bid, m)
    patch = {"genre": _genre_of(meta["main_category"], meta["sub_categories"]),
             "audience": CHANNEL_TO_AUDIENCE.get(main_channel(meta["main_category"]), book.get("audience") or "none"),
             "updated_at": store.now()}
    store.update("books", bid, patch)
    return {"ok": True, "meta": meta, "book": _book_or_404(bid)}


# ---------------------------------------------------------------- 章节骨架

def _titles_from_outline(bid: str) -> Dict[int, str]:
    """从大纲里解析「第N章 标题」，用于建骨架与纠正默认标题。"""
    outline = (store.query_one(
        "SELECT content FROM artifacts WHERE book_id=? AND key='outline'", [bid]) or {}).get("content") or ""
    out: Dict[int, str] = {}
    for m in re.finditer(r"第\s*(\d{1,2})\s*章[：:\s]+([^\n]{2,30})", outline or ""):
        no = int(m.group(1))
        t = m.group(2).strip()
        # 大纲里有时会重复写「第3章 第3章 首次反击」，先剥掉前缀
        t = re.sub(r"^第\s*\d{1,2}\s*章\s*[：:\s]*", "", t)
        t = re.split(r"[（(｜|，,。：:]", t)[0].strip()
        if 2 <= len(t) <= 18 and 1 <= no <= 10:
            out[no] = t
    return out


@router.post("/api/short/books/{bid}/skeleton")
def build_skeleton(bid: str, data: Dict[str, Any] = Body(default={})):
    """建 5 章骨架：标题优先取大纲里的「第N章 标题」，没有才用黄金五段默认名。"""
    _book_or_404(bid)
    parsed = _titles_from_outline(bid)
    titles = data.get("titles")
    if not isinstance(titles, list) or not titles:
        titles = [parsed.get(i) or DEFAULT_CHAPTER_TITLES[i - 1] for i in range(1, 6)]
    # 已建过、但标题还是「困境引爆」这类默认名的章：用大纲标题纠正（不动正文）
    for c in _chapters(bid):
        no = int(c["no"])
        if (c.get("title") or "") in DEFAULT_CHAPTER_TITLES and parsed.get(no):
            store.update_safe("chapters", c["id"], {"title": parsed[no], "updated_at": store.now()})
    existing = {int(c["no"]) for c in _chapters(bid)}
    created = []
    for i, t in enumerate(titles[:10], start=1):
        if i in existing:
            continue
        cid = store.new_id("ch")
        store.insert("chapters", {
            "id": cid, "book_id": bid, "no": i,
            "title": str(t).strip() or f"第{i}章",
            "brief": "", "content": "", "summary": "", "progress": "",
            "words": 0, "status": "draft",
            "created_at": store.now(), "updated_at": store.now(),
        })
        created.append(i)
    return {"ok": True, "created": created, "chapters": _chapters(bid)}


# ---------------------------------------------------------------- 预览 / 检查

@router.get("/api/short/books/{bid}/preview")
def preview_short_book(bid: str):
    b = _book_or_404(bid)
    m = _meta(bid)
    chs = _chapters(bid)
    total = sum(int(c.get("words") or 0) for c in chs)
    text = _merged_text(chs)
    no_title = [c["no"] for c in chs if not (c.get("title") or "").strip()]
    not_done = [c["no"] for c in chs if (c.get("status") or "draft") != "done"]
    main_name = (BY_KEY.get(m["main_category"]) or {}).get("name", "")
    channel = main_channel(m["main_category"]) if m["main_category"] else "—"
    checks = [
        {"key": "words", "label": f"正文 ≥ {MIN_WORDS} 字（签约机会线）",
         "ok": total >= MIN_WORDS, "detail": f"{total} 字 / 门槛 {MIN_WORDS}"},
        {"key": "words_max", "label": f"正文 < {MAX_WORDS} 字（番茄短故事上限）",
         "ok": total < MAX_WORDS,
         "detail": f"{total} 字 / 上限 {MAX_WORDS}" + ("" if total < MAX_WORDS else f"，超 {total - MAX_WORDS} 字，建议删减")},
        {"key": "chapters", "label": "章节骨架（建议 5 章，短故事按「章节标题」划分）",
         "ok": len(chs) >= 5, "detail": f"{len(chs)} 章"},
        {"key": "titles", "label": "每章都有标题", "ok": len(chs) > 0 and not no_title,
         "detail": "全部有标题" if not no_title else f"缺标题：第 {no_title} 章"},
        {"key": "done", "label": "全部章节已成稿", "ok": len(chs) > 0 and not not_done,
         "detail": "全部成稿" if not not_done else f"仍是草稿：第 {not_done} 章"},
        {"key": "category", "label": "已选作品分类（主分类必选，最多 8 个）",
         "ok": bool(m["main_category"]),
         "detail": (f"{main_name}（{channel}）｜次分类 {len(m['sub_categories'])} 个"
                    if m["main_category"] else "还没选主分类")},
    ]
    return {
        "ok": True,
        "book": {"id": b["id"], "name": b["name"], "brief": b.get("brief") or "", "genre": b.get("genre") or ""},
        "meta": m,
        "chapters": [{"no": c["no"], "title": c.get("title") or "", "words": c.get("words") or 0,
                      "status": c.get("status") or "draft"} for c in chs],
        "total_words": total,
        "text": text,
        "has_content": bool(text),
        "checks": checks,
        "min_words": MIN_WORDS,
    }


# ---------------------------------------------------------------- 导出

def _info_card(book: Dict[str, Any], meta: Dict[str, Any], chapters: List[Dict[str, Any]],
               total: int) -> str:
    main = (BY_KEY.get(meta["main_category"]) or {})
    subs = category_names(meta["sub_categories"])
    lines = [
        "番茄短故事 · 发布信息卡",
        "",
        f"短故事名称：{book['name']}",
        f"正文字数：{total} 字（签约机会线 {MIN_WORDS} 字）",
        f"章节数：{len(chapters)} 章",
        f"主分类：{main.get('name', '未选')}（{main.get('channel', '—')}）",
        "",
        "发布页要填的项：",
        "1. 短故事名称 → 粘贴上方名称",
        "2. 正文 → 粘贴「正文-粘贴用」文件（用「第N章 标题」行划分章节；也可用页面左下「智能分章」）",
        "3. 封面设置 → 上传封面（竖版约 3:4；官方要求：无版权争议、且不能出现人物）",
        "4. 作品分类 → 主分类必选，最多 8 个",
        "5. 发布协议 → 勾选「我已阅读短故事发布事项」",
    ]
    return "\n".join(lines)


def _write_docx(path: str, book: Dict[str, Any], chapters: List[Dict[str, Any]]) -> None:
    from docx import Document  # 延迟导入：没装 python-docx 时 txt 导出仍可用

    doc = Document()
    doc.add_heading(book["name"], level=0)
    for c in chapters:
        title = (c.get("title") or "").strip() or f"第{c['no']}章"
        doc.add_heading(f"第{c['no']}章 {title}", level=1)
        body = (c.get("content") or "").strip()
        for para in body.split("\n"):
            if para.strip():
                doc.add_paragraph(para)
    doc.save(path)


@router.post("/api/short/books/{bid}/export")
def export_short_book(bid: str, data: Dict[str, Any] = Body(default={})):
    book = _book_or_404(bid)
    meta = _meta(bid)
    chs = _chapters(bid)
    if not chs:
        raise HTTPException(400, "还没有章节，先点「建立 5 章骨架」")
    total = sum(int(c.get("words") or 0) for c in chs)
    folder = os.path.join(DATA_DIR, "exports", _safe_name(book["name"]))
    os.makedirs(folder, exist_ok=True)
    base = "番茄短故事-" + _safe_name(book["name"])
    written: List[str] = []

    text = _merged_text(chs)
    paste_path = os.path.join(folder, base + "-正文-粘贴用.txt")
    with open(paste_path, "w", encoding="utf-8") as f:
        f.write(text + "\n")
    written.append(paste_path)

    card_path = os.path.join(folder, base + "-发布信息卡.txt")
    with open(card_path, "w", encoding="utf-8") as f:
        f.write(_info_card(book, meta, chs, total) + "\n")
    written.append(card_path)

    fmt = (data.get("fmt") or "both").lower()
    if fmt in ("docx", "both"):
        try:
            docx_path = os.path.join(folder, base + ".docx")
            _write_docx(docx_path, book, chs)
            written.append(docx_path)
        except Exception as exc:  # noqa: BLE001
            written.append(f"（docx 导出失败：{exc}）")

    return {
        "ok": True,
        "dir": folder,
        "files": written,
        "count": len(chs),
        "words": total,
        "pass_line": total >= MIN_WORDS,
    }


# ---------------------------------------------------------------- 模型分工

# 短故事 8 步的推荐模型（按 model_name 匹配，不写死 id；平台上换模型也不用改代码）
# 省钱档：起草/大纲/核查走 DeepSeek V4.1 Flash 1017（¥1/¥4，与 deepseek-flash 同价，
#         实测更快、细节更密），改写走 GLM-5.3-FlashX（¥2/¥7），全文扫描走 Kimi K2.6
#         —— 一篇 1.2 万字约 ¥0.3–0.5
# 质量档：大纲与核查走 DeepSeek V4 Pro，起草/钩子走豆包 Seed 2.1 Pro（最贴番茄文风），
#         改写走 GLM-5.3，扫描/核验走 Kimi K3 —— 一篇约 ¥0.8–1.2
# 统一模型配置：不再分省钱档/质量档，但**按用途分工**（不是所有步骤同一个模型）。
# 依据 2026-10-19 实测：
#   · V4 Pro 比 1017 慢一倍且无质量优势 → 规划用 1017；
#   · 豆包 Seed 2.1 Pro 中文占比最高（84.5%）→ 短篇/短故事起草用它；
#   · GLM 改写 3 次跑偏 → 改写统一 1017；
#   · 摘要/伏笔是归纳任务 → 1017 足够。
UNIFIED_MODEL_PLAN = {
    "setup": "deepseek-v4.1-flash-1017",
    "outline": "deepseek-v4.1-flash-1017",
    "outline_check": "deepseek-v4.1-flash-1017",
    "draft": "seed-2.1-pro",              # 起草：短篇体量小，用质量最高的豆包
    "polish": "deepseek-v4.1-flash-1017",  # 改写：稳定优先
    "hook": "deepseek-v4.1-flash-1017",
    "scan": "deepseek-v4.1-flash-1017",
    "submit_check": "deepseek-v4.1-flash-1017",
    "summary": "deepseek-v4.1-flash-1017",
    "foreshadow": "deepseek-v4.1-flash-1017",
}

# 长篇/中篇：篇幅大，起草也用 1017（豆包按 400 章算会贵 6 倍）
LONG_MODEL_PLAN = {
    "topic": "deepseek-v4.1-flash-1017",
    "title_intro": "deepseek-v4.1-flash-1017",
    "characters": "deepseek-v4.1-flash-1017",
    "outline": "deepseek-v4.1-flash-1017",
    "golden3": "deepseek-v4.1-flash-1017",
    "chapter": "deepseek-v4.1-flash-1017",
    "deai": "deepseek-v4.1-flash-1017",
    "summary": "deepseek-v4.1-flash-1017",
    "foreshadow": "deepseek-v4.1-flash-1017",
    "finish_audit": "deepseek-v4.1-flash-1017",
    "presubmit": "deepseek-v4.1-flash-1017",
    "compliance": "deepseek-v4.1-flash-1017",
}


@router.post("/api/books/{bid}/apply-models")
def apply_models(bid: str, data: Dict[str, Any] = Body(default={})):
    """按流程套用统一配置（短故事/短篇用 UNIFIED，长篇/中篇用 LONG），不分档位。"""
    book = _book_or_404(bid)
    flow = (book.get("flow_id") or "").strip()
    plan = UNIFIED_MODEL_PLAN if flow in ("fanqie_short", "short_story") else LONG_MODEL_PLAN
    current = store.jloads(book.get("step_models"), {}) or {}
    bound: Dict[str, str] = {}
    missing: List[str] = []
    for step, model_name in plan.items():
        m = store.query_one("SELECT id, name FROM models WHERE model_name=? AND enabled=1", [model_name])
        if not m:
            missing.append(model_name)
            continue
        current[step] = m["id"]
        bound[step] = m["name"]
    store.update("books", bid, {"step_models": json.dumps(current, ensure_ascii=False), "updated_at": store.now()})
    return {"ok": True, "flow": flow, "label": "统一配置·" + ("短篇" if flow in ("fanqie_short", "short_story") else "长篇"),
            "bound": bound, "missing": missing}


@router.get("/api/short/model-plan")
def get_model_plan():
    """短故事统一模型配置（前端展示 + 一键绑定用）。"""
    items = []
    for step, mname in UNIFIED_MODEL_PLAN.items():
        m = store.query_one(
            "SELECT id, name, model_name, purpose FROM models WHERE model_name=? AND enabled=1",
            [mname],
        )
        items.append({
            "step": step,
            "model_name": mname,
            "model_id": m["id"] if m else "",
            "model_label": m["name"] if m else "（平台上没有这个模型）",
            "available": bool(m),
        })
    return {"ok": True, "plan": items, "label": "统一模型配置"}


@router.post("/api/short/books/{bid}/preset")
def bind_preset(bid: str, data: Dict[str, Any] = Body(default={})):
    """把短故事 8 步的推荐模型一键绑定到这本书（只改本书的步骤绑定，不动全局默认）。"""
    book = _book_or_404(bid)
    plan = dict(UNIFIED_MODEL_PLAN)
    current = store.jloads(book.get("step_models"), {}) or {}
    bound: Dict[str, str] = {}
    missing: List[str] = []
    # 「章节摘要 / 更新伏笔台账」是内部步骤，不在 8 步里，默认会退到全局默认模型
    # （平台没设默认时，那就是第一个启用的 API 模型——常常是最贵的那个）。
    # 这两步只需要归纳能力，统一绑到便宜快模型即可。
    plan = dict(plan)
    for extra in ("summary", "foreshadow"):
        plan.setdefault(extra, "deepseek-v4.1-flash-1017")
    for step, model_name in plan.items():
        m = store.query_one(
            "SELECT id, name FROM models WHERE model_name=? AND enabled=1", [model_name]
        )
        if not m:
            missing.append(model_name)
            continue
        current[step] = m["id"]
        bound[step] = m["name"]
    store.update("books", bid, {
        "step_models": json.dumps(current, ensure_ascii=False),
        "updated_at": store.now(),
    })
    return {"ok": True, "label": "统一模型配置", "bound": bound, "missing": missing}


@router.post("/api/short/books/{bid}/full-draft")
def start_full_draft(bid: str, data: Dict[str, Any] = Body(default={})):
    """一次成稿：一次调用写出五章全文，切分校验后落库。"""
    _book_or_404(bid)
    from ..flows import runner
    try:
        return runner.start_full_draft(bid, (data.get("model_id") or "").strip())
    except LookupError as exc:
        raise HTTPException(404, str(exc))


# ---------------------------------------------------------------- 封面方案

# ---------------------------------------------------------------- 书名查重

import json as _json
import re as _re
import urllib.parse as _urlparse


_SEARCH_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")


def _http_text(url: str, timeout: int = 15) -> str:
    import httpx
    try:
        with httpx.Client(timeout=timeout, trust_env=False, follow_redirects=True) as c:
            r = c.get(url, headers={"User-Agent": _SEARCH_UA, "Accept-Language": "zh-CN,zh;q=0.9"})
            return r.text if r.status_code == 200 else ""
    except Exception:  # noqa: BLE001
        return ""


def _titles_from(html: str, tag: str = "h3") -> List[str]:
    if not html:
        return []
    out: List[str] = []
    for m in _re.finditer(r"<" + tag + r"[^>]*>([\s\S]*?)</" + tag + r">", html):
        t = _re.sub(r"<[^>]+>", "", m.group(1))
        t = _re.sub(r"\s+", " ", t).strip()
        if not t or len(t) < 4 or "还搜" in t or "相关" in t:
            continue
        out.append(t)
        if len(out) >= 10:
            break
    return out


def _web_search_titles(query: str, timeout: int = 15) -> List[str]:
    """联网查标题：360 优先（反爬弱、结果相关），失败退搜狗，再退 Bing。"""
    q = _urlparse.quote(query)
    html = _http_text("https://www.so.com/s?q=" + q, timeout)
    titles = _titles_from(html, "h3")
    if titles:
        return titles
    html = _http_text("https://www.sogou.com/web?query=" + q, timeout)
    titles = _titles_from(html, "h3")
    if titles:
        return titles
    html = _http_text("https://cn.bing.com/search?q=" + q + "&setlang=zh-CN&ensearch=0", timeout)
    return _titles_from(html, "h2")


@router.post("/api/name/check")
def name_check(data: Dict[str, Any] = Body(...)):
    """书名查重：本地（平台内）+ 联网近似（搜狗搜「书名 番茄小说」）。

    ⚠️ 联网部分只能做近似判断：番茄没有公开搜索接口，搜索引擎收录也不全。
    最终是否重名，以番茄发布页的提示为准。
    """
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "请填书名")
    local = store.query("SELECT id, name, flow_id FROM books WHERE name=?", [name])
    titles = _web_search_titles(f'{name} 番茄小说')
    hits = [t for t in titles if name in t]
    return {
        "ok": True,
        "name": name,
        "local_duplicates": [{"id": b["id"], "name": b["name"], "flow": b["flow_id"]} for b in local],
        "web": {
            "checked": bool(titles),
            "hits": hits,
            "samples": titles[:5],
            "note": ("搜索引擎只能做近似判断，番茄站内是否重名以发布页提示为准。" if titles else "联网检索失败或没有结果，请以番茄发布页提示为准。"),
        },
    }


_NAME_SUGGEST_PROMPT = """你是番茄短故事的书名编辑。原书名「{name}」在番茄发布时提示「标题已存在」，请给出 12 个替代书名，并逐个打分（够用即可，不求多）。

分类：{genre}
一句话设定：{brief}

硬规则（违反直接淘汰）：
- 每个 ≤15 字（含标点），只用中文与中文标点；
- 不得出现英文单词、英文标点；
- 不得含政治/色情/暴力/违法敏感词。

打分维度（每项 0–20 分）：
- fit：贴合度（和设定、分类的匹配）
- conflict：冲突感（一眼能看到矛盾或悬念）
- diff：差异化（是否避开番茄烂大街套话，越不套路越高）
- memory：记忆点（好记、有画面）
- compliance：合规度（字数、标点、敏感词）

只输出 JSON 数组，不要解释、不要 markdown 围栏：
[{{"name":"...","fit":18,"conflict":16,"diff":15,"memory":17,"compliance":20,"reason":"一句话理由"}}]
"""


def _name_ok(n: str) -> bool:
    n = (n or "").strip()
    if not n or len(n) > 15:
        return False
    if _re.search(r"[A-Za-z]", n):
        return False
    if _re.search(r"[,.!?;:]", n):      # 英文标点（番茄会报错）
        return False
    return True


@router.post("/api/name/suggest")
def name_suggest(data: Dict[str, Any] = Body(...)):
    """重名时让模型生成候选并打分，只返回得分最高的那一个。"""
    name = (data.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "请填原书名")
    from ..flows import runner
    from ..llm import client as llm
    book = store.query_one("SELECT * FROM books WHERE id=?", [(data.get("book_id") or "").strip()])
    prompt = _NAME_SUGGEST_PROMPT.format(
        name=name,
        genre=(book or {}).get("genre") or data.get("genre") or "（未填）",
        brief=(book or {}).get("brief") or data.get("brief") or "（未填）",
    )
    model = runner.resolve_model_for_step(book or {}, "title_intro") if book else runner.pick_model("")
    if not model:
        raise HTTPException(400, "没有可用模型")
    try:
        out, _meta = llm.chat(model, prompt, "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"生成失败：{str(exc)[:200]}")

    raw = out.strip()
    m = _re.search(r"\[[\s\S]*\]", raw)
    items: List[Dict[str, Any]] = []
    if m:
        try:
            items = _json.loads(m.group(0))
        except Exception:  # noqa: BLE001
            items = []
    cands: List[Dict[str, Any]] = []
    for it in items if isinstance(items, list) else []:
        nm = str((it or {}).get("name") or "").strip()
        if not _name_ok(nm):
            continue
        score = 0
        for k in ("fit", "conflict", "diff", "memory", "compliance"):
            try:
                score += max(0, min(20, int((it or {}).get(k) or 0)))
            except (TypeError, ValueError):
                pass
        cands.append({
            "name": nm, "score": score,
            "fit": it.get("fit"), "conflict": it.get("conflict"), "diff": it.get("diff"),
            "memory": it.get("memory"), "compliance": it.get("compliance"),
            "reason": str(it.get("reason") or "").strip()[:80],
        })
    cands.sort(key=lambda x: (-x["score"], len(x["name"])))
    # 联网查重与打分「同步进行」：分数排好后，前 8 个候选并发去查（不再一个个排队等），
    # 查完再按分数顺序取第一个未命中的 —— 生成 20 个约 20 秒，查重只额外多 2–3 秒。
    best = None
    if cands:
        from concurrent.futures import ThreadPoolExecutor

        def _check(c):
            titles = _web_search_titles(f'{c["name"]} 番茄小说')
            hits = [t for t in titles if c["name"] in t]
            return titles, hits

        def _fill(c, titles, hits):
            c["web_checked"] = bool(titles)
            c["web_hits"] = hits[:3]
            if not titles:
                c["web_note"] = "联网检索失败，未能确认是否重名（以番茄发布页为准）"
            elif not hits:
                c["web_note"] = "联网未命中同名，可用"
            else:
                c["web_note"] = "联网疑似命中同名"

        # 先查分数最高的一个：未命中就立刻返回（大多数情况只发 1 次请求，最快）
        t0, h0 = _check(cands[0])
        _fill(cands[0], t0, h0)
        if not h0:
            best = cands[0]
        else:
            # 命中了才把后面 7 个并发查（限流风险低），再按分数取第一个未命中的
            rest = cands[1:8]
            with ThreadPoolExecutor(max_workers=3) as ex:
                results = list(ex.map(_check, rest))
            for c, (titles, hits) in zip(rest, results):
                _fill(c, titles, hits)
            for c in [cands[0]] + rest:
                if not c.get("web_hits"):
                    best = c
                    break
            if best is None:
                best = cands[0]
                best["web_note"] = "前 8 个候选在网上都出现了同名/近似标题，建议再生成一批"
    out_json = {"ok": True, "best": best, "checked": len(items) if isinstance(items, list) else 0,
                "valid": len(cands), "model": model.get("name")}
    if not best:
        out_json["debug"] = raw[:600]
    return out_json


_IDEA_PROMPT = """为番茄短故事构思一个开篇冲突点（一句话设定）。

分类：{cat}（{channel}）
分类释义：{desc}

要求：
1. 一句话，30–60 字，写清「谁、被谁、要什么、冲突在哪」；
2. **必须符合这个分类的频道与套路**：男频脑洞就写脑洞设定/金手指/规则反转，
   婚姻家庭才写婚恋婆媳；不要把任何分类都写成婆媳婚姻家庭；
3. 冲突要具体（钱、物、关系、规则），不要抽象；
4. 只输出这一句话，不要解释、不要引号。
"""


@router.post("/api/short/idea")
def short_idea(data: Dict[str, Any] = Body(default={})):
    """按主分类自动构思一句话设定（用户只选分类时用）。"""
    key = (data.get("main_category") or "").strip()
    cat = BY_KEY.get(key) or {}
    if not cat:
        raise HTTPException(400, "请先选主分类")
    from ..flows import runner
    from ..llm import client as llm
    model = runner.pick_model("")
    if not model:
        raise HTTPException(400, "没有可用模型")
    prompt = _IDEA_PROMPT.format(cat=cat.get("name") or key,
                                channel=cat.get("channel") or "",
                                desc=cat.get("desc") or "（按分类名理解其套路）")
    try:
        out, _meta = llm.chat(model, prompt, "")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"构思失败：{str(exc)[:200]}")
    brief = (out or "").strip().strip("“”")
    return {"ok": True, "brief": brief[:120], "category": cat.get("name")}


@router.get("/api/short/tags")
def short_tags():
    """番茄短故事的全部作品分类与标签（2026-10 从发布页抓取）。"""
    from ..short_tags import TAGS, PRIMARY, SUBTAGS, GROUPS
    return {"tags": TAGS, "primary": PRIMARY, "subtags": SUBTAGS,
            "groups": GROUPS, "max": 8}
