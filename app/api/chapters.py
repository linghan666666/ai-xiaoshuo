"""章节路由：章节 CRUD + 导出成稿到本地。"""
from __future__ import annotations

import os
import re
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from .. import store, textkit
from ..config import DATA_DIR
from ..flows import runner

router = APIRouter()


def _book_or_404(pid: str) -> Dict[str, Any]:
    b = store.query_one("SELECT * FROM books WHERE id=?", [pid])
    if not b:
        raise HTTPException(404, "书不存在")
    return b


def _view(ch: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "id": ch["id"], "no": ch["no"], "title": ch.get("title") or "",
        "brief": ch.get("brief") or "", "status": ch.get("status") or "draft",
        "progress": ch.get("progress") or "", "summary": ch.get("summary") or "",
        "words": textkit.count_words(ch.get("content") or ""),
        "preview": (ch.get("content") or "")[:60],
        "content": ch.get("content") or "", "updated_at": ch.get("updated_at") or "",
    }


@router.get("/api/books/{pid}/chapters")
def list_chapters(pid: str):
    _book_or_404(pid)
    rows = store.query("SELECT * FROM chapters WHERE book_id=? ORDER BY no DESC", [pid])
    return {"ok": True, "chapters": [_view(c) for c in rows]}


@router.post("/api/books/{pid}/chapters")
def create_chapter(pid: str, data: dict = Body(default={})):
    _book_or_404(pid)
    if data.get("no"):
        no = int(data["no"])
    else:
        row = store.query_one("SELECT MAX(no) m FROM chapters WHERE book_id=?", [pid])
        no = int((row or {}).get("m") or 0) + 1
    exist = store.query_one("SELECT * FROM chapters WHERE book_id=? AND no=?", [pid, no])
    if exist:
        return {"ok": True, "created": False, "chapter": _view(exist),
                "message": f"第 {no} 章已存在"}
    cid = store.new_id("ch")
    store.insert("chapters", {"id": cid, "book_id": pid, "no": no,
                              "title": (data.get("title") or "").strip(),
                              "brief": (data.get("brief") or "").strip(),
                              "content": "", "words": 0, "status": "draft",
                              "created_at": store.now(), "updated_at": store.now()})
    ch = store.query_one("SELECT * FROM chapters WHERE id=?", [cid])
    return {"ok": True, "created": True, "chapter": _view(ch)}


@router.get("/api/chapters/{cid}")
def get_chapter(cid: str):
    ch = store.query_one("SELECT * FROM chapters WHERE id=?", [cid])
    if not ch:
        raise HTTPException(404, "章节不存在")
    return {"ok": True, "chapter": _view(ch)}


@router.put("/api/chapters/{cid}")
def save_chapter(cid: str, data: dict = Body(...)):
    ch = store.query_one("SELECT * FROM chapters WHERE id=?", [cid])
    if not ch:
        raise HTTPException(404, "章节不存在")
    patch: Dict[str, Any] = {"updated_at": store.now()}
    if "title" in data:
        patch["title"] = (data["title"] or "").strip()
    if "brief" in data:
        patch["brief"] = (data["brief"] or "").strip()
    if "status" in data:
        patch["status"] = data["status"] or "draft"
    if "content" in data:
        body = textkit.strip_fences(data["content"] or "")
        patch["content"] = body
        patch["words"] = textkit.count_words(body)
    store.update("chapters", cid, patch)
    return {"ok": True, "chapter": _view(store.query_one("SELECT * FROM chapters WHERE id=?", [cid]))}


@router.delete("/api/chapters/{cid}")
def delete_chapter(cid: str):
    if not store.query_one("SELECT id FROM chapters WHERE id=?", [cid]):
        raise HTTPException(404, "章节不存在")
    store.execute("DELETE FROM chapters WHERE id=?", [cid])
    return {"ok": True}


@router.post("/api/chapters/{cid}/finish")
def finish_chapter(cid: str):
    """一键写完本章：起草 → 去 AI 味 → 章节摘要 → 更新伏笔台账 → 违禁词扫描。

    后台链式执行，返回 task_id，前端轮询 /api/tasks/{id} 看进度。
    """
    ch = store.query_one("SELECT * FROM chapters WHERE id=?", [cid])
    if not ch:
        raise HTTPException(404, "章节不存在")
    return runner.start_finish_chapter(ch["book_id"], cid)


@router.post("/api/chapters/{cid}/summary")
def make_summary(cid: str):
    """（重新）生成本章结构化摘要——跨章连贯靠它。"""
    ch = store.query_one("SELECT * FROM chapters WHERE id=?", [cid])
    if not ch:
        raise HTTPException(404, "章节不存在")
    return runner.start_summary(ch["book_id"], cid)


@router.get("/api/tasks/{tid}")
def get_task(tid: str):
    t = runner.task_view(tid)
    if not t:
        raise HTTPException(404, "任务不存在")
    return {"ok": True, "task": t}


# ---------------------------------------------------------------- 导出

def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", (name or "").strip()).strip(" ._")[:80] or "未命名"


def _safe_export_dir(raw: str) -> str:
    """导出目录白名单校验：允许 data/exports/ 与你自己的用户目录，挡住系统目录与相对跳转。

    导出到桌面/文档是正常需求，所以不做死白名单；但要拦住
    `..\\..\\Windows\\System32` 这类越界写入（本机任意进程都可能调到这个接口）。
    """
    target = os.path.abspath(os.path.expanduser(raw)) if raw else ""
    if not target:
        return ""
    home = os.path.abspath(os.path.expanduser("~"))
    allowed = (os.path.abspath(DATA_DIR), home)
    if any(target == a or target.startswith(a + os.sep) for a in allowed):
        return target
    raise HTTPException(400, f"出于安全考虑，导出目录只允许在「{os.path.abspath(DATA_DIR)}」"
                             f"或你的用户目录「{home}」之下。想导出到别处，请先在该范围内建目录。")


def _write_upload_docx(book: Dict[str, Any], chapters: List[Dict[str, Any]], target: str,
                       max_mb: float = 4.5) -> List[str]:
    """按番茄「章节批量上传」的官方要求导出 docx。

    官方规则（《番茄小说完本签约模式上线通知》2026-09-07，流程第 2 步）：
      · 入口：章节管理 → 草稿箱 → 上传章节
      · 单次**仅支持上传 1 个 docx**，文件大小**不超过 5MB**
      · 章节标题格式必须是「第x章 章节名称」（如「第1章 风起云涌」）
      · 手机端不支持批量上传，只能逐章

    所以这里产出一个（必要时多个）docx：每个章节以标题行开头，正文按原段落分段。
    超过 max_mb（默认 4.5MB，给 5MB 留余量）时按章边界切包。
    """
    from docx import Document            # 延迟导入：没装 python-docx 时不影响 txt/md 导出

    limit = int(max_mb * 1024 * 1024)
    base = _safe(book.get("name") or "book")

    def render(chs: List[Dict[str, Any]]):
        doc = Document()
        for ch in chs:
            # ★ 番茄上传解析规则（建书页「上传章节」弹窗原文）：
            #   ①仅支持单文件；②docx 且 ≤5MB；③**系统按 H1 标题（一级标题）拆分章节**；
            #   ④标题须为「第x章 章节名称」且序号用阿拉伯数字。
            #   → 所以标题**必须套用 Heading 1 样式**；用普通段落会拆不出章节（2026-10-15 踩到）。
            doc.add_heading(f"第{ch['no']}章 {ch.get('title') or ''}".strip(), level=1)
            for line in (ch.get("content") or "").strip().split("\n"):
                if line.strip():
                    doc.add_paragraph(line.strip())
        return doc

    # 先按「整本一个文件」试：15–30 万字中篇基本都不会超 5MB
    single = os.path.join(target, f"{base}.docx")
    render(chapters).save(single)
    if os.path.getsize(single) <= limit:
        return [single]

    # 超限：逐章测量体积，再按章边界贪心装包（不在磁盘上反复重存，避免 O(n²) 慢）
    sizes: List[int] = []
    for ch in chapters:
        probe = os.path.join(target, f".__probe_{ch['no']}.docx")
        render([ch]).save(probe)
        sizes.append(os.path.getsize(probe))
        try:
            os.remove(probe)
        except OSError:
            pass

    try:
        os.remove(single)
    except OSError:
        pass

    packs: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    cur_size = 0
    for ch, sz in zip(chapters, sizes):
        if cur and cur_size + sz > limit:
            packs.append(cur)
            cur, cur_size = [], 0
        cur.append(ch)
        cur_size += sz
    if cur:
        packs.append(cur)

    written: List[str] = []
    for i, chs in enumerate(packs, 1):
        path = os.path.join(target, f"{base}-第{i}部分.docx")
        render(chs).save(path)
        written.append(path)
    return written


@router.post("/api/books/{pid}/export")
def export_book(pid: str, data: dict = Body(default={})):
    """把成稿写成本地文件（txt/md 合并或分章；docx 按番茄「章节批量上传」格式）。"""
    book = _book_or_404(pid)
    chapters = [c for c in store.query("SELECT * FROM chapters WHERE book_id=? ORDER BY no", [pid])
                if (c.get("content") or "").strip()]
    if not chapters:
        raise HTTPException(400, "还没有可导出的正文")
    mode = (data.get("mode") or "separate").strip()
    fmt = (data.get("fmt") or "txt").lower()
    if fmt not in ("txt", "md", "docx"):
        fmt = "txt"
    with_title = bool(data.get("with_title", True))
    target = _safe_export_dir((data.get("dir") or "").strip()) or os.path.join(
        DATA_DIR, "exports", _safe(book.get("name") or "book"))
    try:
        os.makedirs(target, exist_ok=True)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"无法创建目录 {target}：{exc}")

    # docx：番茄「章节批量上传」专用格式（一个 docx、章节标题行「第x章 章节名称」、≤5MB）
    if fmt == "docx":
        try:
            written = _write_upload_docx(book, chapters, target)
        except ImportError:
            raise HTTPException(400, "缺少 python-docx，请先执行 pip install python-docx")
        return {"ok": True, "dir": target, "count": len(chapters),
                "words": sum(textkit.count_words(c.get("content") or "") for c in chapters),
                "files": written,
                "note": "番茄完本签约的「批量上传」每次只能传 1 个 docx；若被切成多份，逐个上传即可。"}

    def body_of(ch: Dict[str, Any]) -> str:
        t = ""
        if with_title:
            t = f"第{ch['no']}章《{ch.get('title') or ''}》\n\n" if ch.get("title") \
                else f"第{ch['no']}章\n\n"
        return t + (ch.get("content") or "").strip() + "\n"

    written: List[str] = []
    if mode in ("merged", "both"):
        parts = [f"《{book.get('name')}》  共 {len(chapters)} 章\n导出时间："
                 f"{store.now().replace('T', ' ')}"]
        parts += [body_of(c) for c in chapters]
        path = os.path.join(target, f"{_safe(book.get('name') or 'book')}.{fmt}")
        with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
            f.write(("\n" + "—" * 20 + "\n\n").join(parts))
        written.append(path)
    if mode in ("separate", "both"):
        for ch in chapters:
            name = f"第{ch['no']:03d}章 {_safe(ch.get('title') or '')}".strip()
            path = os.path.join(target, f"{name}.{fmt}")
            with open(path, "w", encoding="utf-8-sig", newline="\n") as f:
                f.write(body_of(ch))
            written.append(path)
    return {"ok": True, "dir": target, "count": len(chapters),
            "words": sum(textkit.count_words(c.get("content") or "") for c in chapters),
            "files": written}
