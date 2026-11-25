"""书与流程路由：建书 / 流程状态 / 步骤面板 / 产出保存 / 生成（含网页回填）。"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from fastapi import APIRouter, Body, HTTPException

from .. import store
from ..flows import definitions, runner

router = APIRouter()

# 番茄官方硬规则：书名含标点最多 15 字，且只允许中文标点（英文标点会报错）
NAME_MAX = 15
_BAD_PUNCT = set("!?,[]:;.\"'(){}<>/\\|@#$%^&*~`-+=—")


def name_check(name: str) -> Dict[str, Any]:
    n = name or ""
    bad = sorted({c for c in n if c in _BAD_PUNCT})
    return {"chars": len(n), "ok": len(n) <= NAME_MAX and not bad,
            "over": max(0, len(n) - NAME_MAX), "bad_punct": bad}

# 「按角色分工」的角色 → 步骤映射（一键应用推荐配置时用；依据 skill 02a 的模型适配建议）
ROLE_STEPS: Dict[str, List[str]] = {
    "起草": ["chapter", "draft"],
    "改写": ["deai", "polish", "hook", "deai_fix", "ooc_fix"],
    "大纲": ["outline", "outline_check", "setup", "characters", "golden3"],
    "扫描": ["scan"],
}


def _book_or_404(pid: str) -> Dict[str, Any]:
    b = store.query_one("SELECT * FROM books WHERE id=?", [pid])
    if not b:
        raise HTTPException(404, "书不存在")
    return b


@router.get("/api/flows")
def list_flows():
    return {"ok": True, "flows": [definitions.FLOWS[f] for f in definitions.BOOK_FLOWS],
            "tool_flows": [definitions.FLOWS["stuck"]]}


@router.get("/api/books")
def list_books():
    books = store.query("SELECT * FROM books ORDER BY updated_at DESC, created_at DESC")
    for b in books:
        b["chapters"] = (store.query_one(
            "SELECT COUNT(*) c FROM chapters WHERE book_id=?", [b["id"]]) or {}).get("c") or 0
        b["artifacts"] = (store.query_one(
            "SELECT COUNT(*) c FROM artifacts WHERE book_id=? AND content!=''", [b["id"]]) or {}).get("c") or 0
        b["name_check"] = name_check(b["name"])       # 书名合规（≤15 字、中文标点）
    return {"ok": True, "books": books, "name_max": NAME_MAX}


@router.post("/api/books")
def create_book(data: dict = Body(...)):
    flow_id = (data.get("flow_id") or "long_novel")
    flow = definitions.get_flow(flow_id) or definitions.get_flow("long_novel")
    bid = store.new_id("book")
    store.insert("books", {
        "id": bid,
        "name": (data.get("name") or "未命名作品").strip(),
        "genre": (data.get("genre") or "").strip(),
        "audience": (data.get("audience") or "male"),
        "brief": (data.get("brief") or "").strip(),
        "platform": (data.get("platform") or "番茄小说").strip(),
        "flow_id": flow["id"],
        "step_id": flow["steps"][0]["id"],
        "created_at": store.now(), "updated_at": store.now(),
    })
    return {"ok": True, "book": store.query_one("SELECT * FROM books WHERE id=?", [bid])}


@router.put("/api/books/{pid}")
def update_book(pid: str, data: dict = Body(...)):
    _book_or_404(pid)
    patch = {k: v for k, v in data.items()
             if k in ("name", "genre", "audience", "brief", "platform", "flow_id", "step_id")}
    patch["updated_at"] = store.now()
    store.update("books", pid, patch)
    book = store.query_one("SELECT * FROM books WHERE id=?", [pid])
    return {"ok": True, "book": book, "name_check": name_check(book.get("name") or "")}


@router.delete("/api/books/{pid}")
def delete_book(pid: str):
    _book_or_404(pid)
    for t in ("artifacts", "chapters", "gens", "checks"):
        store.execute(f"DELETE FROM {t} WHERE book_id=?", [pid])
    store.execute("DELETE FROM books WHERE id=?", [pid])
    return {"ok": True}


@router.get("/api/books/{pid}/flow")
def book_flow(pid: str):
    """流程状态：每步「有没有产出 / 是否锁定 / 是不是当前步」。"""
    book = _book_or_404(pid)
    flow = definitions.get_flow(book["flow_id"]) or {}
    arts = {a["key"]: a for a in store.query("SELECT * FROM artifacts WHERE book_id=?", [pid])}
    chapters = store.query("SELECT * FROM chapters WHERE book_id=? ORDER BY no", [pid])
    steps: List[Dict[str, Any]] = []
    for s in flow.get("steps", []):
        a = arts.get(s["id"]) or {}
        content = (a.get("content") or "")
        steps.append({
            **s,
            "artifact_key": s["id"],
            "has_output": bool(content.strip()),
            "chars": len(content),
            "locked": int(a.get("locked") or 0),
            "is_current": book.get("step_id") == s["id"],
        })
    return {"ok": True, "book": book, "flow": {
        "id": flow.get("id"), "name": flow.get("name"), "intro": flow.get("intro"),
        "source": flow.get("source")},
        "steps": steps,
        "chapters": [{"id": c["id"], "no": c["no"], "title": c["title"],
                      "words": c["words"], "status": c["status"]} for c in chapters]}


@router.get("/api/books/{pid}/step/{step_id}")
def step_panel(pid: str, step_id: str, chapter_id: str = "", flow: str = ""):
    """步骤面板：skill 指令原文 + 提示词预览 + 已有产出 + 本步默认模型 + 最近检查结果。

    flow 参数用于「卡文急救工具箱」：临时用另一个流程（如 stuck）的步骤定义。
    """
    book = _book_or_404(pid)
    flow_id = (flow or book["flow_id"]).strip()
    step = definitions.get_step(flow_id, step_id)
    if not step:
        raise HTTPException(404, f"没有该步骤：{step_id}")
    chapter = store.query_one("SELECT * FROM chapters WHERE id=?", [chapter_id]) if chapter_id else None
    prompt, used = runner.build_prompt(book, step, chapter=chapter)
    art = store.query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?", [pid, step_id]) or {}
    checks = {}
    for kind in ("blacklist", "violations", "presubmit"):
        row = store.query_one("SELECT * FROM checks WHERE book_id=? AND kind=? "
                              "ORDER BY created_at DESC LIMIT 1", [pid, kind])
        if row:
            checks[kind] = row
    mods = runner.modules_for(step, book)
    return {
        "ok": True, "book": book, "step": step, "flow_id": flow_id,
        "modules": mods,
        "blocks": used,
        "prompt": prompt,
        "artifact": {"key": step_id, "content": art.get("content") or "",
                     "locked": int(art.get("locked") or 0),
                     "updated_at": art.get("updated_at") or ""},
        "chapter": chapter,
        "step_model_id": runner.step_model_id(book, step_id),
        "step_models": store.jloads(book.get("step_models"), {}) or {},
        "checks": checks,
    }


@router.get("/api/books/{pid}/foreshadows")
def get_foreshadows(pid: str):
    """待回收伏笔账本（每章由模型更新，也可人工整理），附条数与到期提醒。"""
    book = _book_or_404(pid)
    text = book.get("foreshadows") or ""
    items = [ln.strip() for ln in text.split("\n") if ln.strip().startswith("-")]
    oldest = None
    for it in items:
        m = __import__("re").search(r"第\s*(\d+)\s*章", it)
        if m:
            n = int(m.group(1))
            oldest = n if oldest is None else min(oldest, n)
    row = store.query_one("SELECT MAX(no) m FROM chapters WHERE book_id=?", [pid])
    cur = int((row or {}).get("m") or 0)
    warnings = []
    if items and oldest and cur and cur - oldest >= 20:
        warnings.append(f"最早伏笔埋在第 {oldest} 章，已过 {cur - oldest} 章仍未回收")
    if len(items) >= 10:
        warnings.append(f"待回收伏笔已有 {len(items)} 条，建议清理/回收")
    return {"ok": True, "text": text, "count": len(items), "oldest_chapter": oldest,
            "current_chapter": cur, "warnings": warnings}


@router.put("/api/books/{pid}/foreshadows")
def save_foreshadows(pid: str, data: dict = Body(...)):
    _book_or_404(pid)
    store.update("books", pid, {"foreshadows": data.get("text") or "", "updated_at": store.now()})
    return {"ok": True, "chars": len(data.get("text") or "")}


@router.post("/api/books/{pid}/foreshadows/refresh")
def refresh_foreshadows(pid: str, data: dict = Body(default={})):
    """用某一章更新伏笔账本（通常由「一键写完本章」自动调用，也可单独触发）。"""
    _book_or_404(pid)
    cid = (data.get("chapter_id") or "").strip()
    if not cid:
        raise HTTPException(400, "缺少 chapter_id")
    return runner.start_foreshadows(pid, cid)


@router.post("/api/gens/{gid}/retry")
async def retry_gen(gid: str):
    """按原提示词重试（提示词不重新组装，便于对比"是不是模型的锅"）。"""
    try:
        return runner.retry_generation(gid)
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.post("/api/gens/{gid}/adopt")
def adopt_gen(gid: str):
    """采用某条生成结果（改写被字数保护拦下时手动采用 / 回滚到上一版）。"""
    try:
        return runner.adopt_generation(gid)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:
        raise HTTPException(400, str(exc))


@router.put("/api/books/{pid}/step-models")
def set_step_model(pid: str, data: dict = Body(...)):
    """绑定「本步骤默认模型」：生成时优先用它（章节页的生成也走这个绑定）。"""
    _book_or_404(pid)
    step_id = (data.get("step_id") or "").strip()
    if not step_id:
        raise HTTPException(400, "缺少 step_id")
    mid = (data.get("model_id") or "").strip()
    if mid and not store.query_one("SELECT id FROM models WHERE id=?", [mid]):
        raise HTTPException(404, "模型不存在")
    m = runner.bind_step_model(pid, step_id, mid)
    return {"ok": True, "step_id": step_id, "model_id": mid, "step_models": m}


@router.post("/api/books/{pid}/step-models/apply-presets")
def apply_step_model_presets(pid: str, data: dict = Body(...)):
    """把「按角色分工」的推荐模型一键绑到本书对应步骤。

    body.roles 形如 {"起草": "mdl_a", "改写": "mdl_b", "大纲": "mdl_c", "扫描": "mdl_d"}
    （角色名与 /api/models/presets 的返回一致；没给的角色不绑。）
    """
    book = _book_or_404(pid)
    roles = {k: v for k, v in (data.get("roles") or {}).items() if v}
    if not roles:
        raise HTTPException(400, "roles 为空")
    flow = definitions.get_flow(book["flow_id"]) or {}
    have = {s["id"] for s in flow.get("steps", [])}
    binds: Dict[str, str] = {}
    for role, steps in ROLE_STEPS.items():
        mid = roles.get(role)
        if not mid:
            continue
        for sid in steps:
            if sid in have:
                binds[sid] = mid
    for sid, mid in binds.items():
        runner.bind_step_model(pid, sid, mid)
    return {"ok": True, "bound": binds,
            "step_models": store.jloads(_book_or_404(pid).get("step_models"), {}) or {}}


@router.get("/api/books/{pid}/artifacts/{key}")
def get_artifact(pid: str, key: str):
    """读取某一步的产出（章节级步骤的产出在 chapters 里，不在这里）。"""
    _book_or_404(pid)
    art = store.query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?", [pid, key]) or {}
    content = art.get("content") or ""
    return {"ok": True, "key": key, "content": content, "chars": len(content),
            "locked": int(art.get("locked") or 0), "updated_at": art.get("updated_at") or ""}


@router.put("/api/books/{pid}/artifacts/{key}")
def save_artifact(pid: str, key: str, data: dict = Body(...)):
    """保存某一步的产出（可同时锁定）。锁定后 AI 生成不会覆盖它。"""
    _book_or_404(pid)
    art = store.query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?", [pid, key])
    if "locked" in data:
        if art:
            store.update("artifacts", art["id"], {"locked": int(bool(data["locked"]))})
    if "content" in data:
        if art and int(art.get("locked") or 0) and not data.get("force"):
            raise HTTPException(400, "该产出已锁定，无法修改。请先解锁。")
        art = store.upsert_artifact(pid, key, data["content"] or "")
    store.update("books", pid, {"updated_at": store.now()})
    row = store.query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?", [pid, key]) or {}
    return {"ok": True, "key": key, "chars": len(row.get("content") or ""),
            "locked": int(row.get("locked") or 0)}


@router.post("/api/books/{pid}/generate")
async def generate(pid: str, data: dict = Body(default={})):
    """对某一步发起生成（后台任务；网页版通道返回待复制提示词）。

    必须是 async：生成任务用 asyncio.create_task 挂到当前事件循环上，
    同步端点跑在 threadpool 里，没有事件循环会直接 500。
    """
    _book_or_404(pid)
    step_id = (data.get("step_id") or "").strip()
    if not step_id:
        raise HTTPException(400, "缺少 step_id")
    try:
        return runner.start_generation(pid, step_id,
                                       chapter_id=(data.get("chapter_id") or "").strip(),
                                       extra=data.get("extra") or "",
                                       model_id=(data.get("model_id") or "").strip(),
                                       flow_id=(data.get("flow") or "").strip())
    except LookupError as exc:
        raise HTTPException(404, str(exc))


@router.get("/api/gens/{gid}")
def gen_detail(gid: str):
    g = runner.gen_view(gid)
    if not g:
        raise HTTPException(404, "生成记录不存在")
    return {"ok": True, "gen": g}


@router.get("/api/books/{pid}/tasks")
def book_tasks(pid: str, limit: int = 10):
    """列出这本书最近的后台任务链（含每步状态与结果）。

    给"前端点击 + 外部/后端同时监控"用：前端轮询走 /api/tasks/{tid}，
    但外部脚本无法知道 tid，有了这个列表就能对齐两边的状态。
    """
    rows = store.query("SELECT id, chapter_id, kind, status, steps, error, created_at, finished_at "
                       "FROM tasks WHERE book_id=? ORDER BY created_at DESC LIMIT ?", [pid, limit])
    for r in rows:
        try:
            r["steps"] = json.loads(r.get("steps") or "[]")
        except Exception:  # noqa: BLE001
            r["steps"] = []
    return {"ok": True, "tasks": rows}


@router.get("/api/books/{pid}/gens")
def book_gens(pid: str, limit: int = 20):
    rows = store.query("SELECT id, step_id, chapter_id, kind, model_name, status, chars, "
                       "started_at, finished_at, substr(output,1,120) AS preview "
                       "FROM gens WHERE book_id=? ORDER BY started_at DESC LIMIT ?", [pid, limit])
    return {"ok": True, "gens": rows}


@router.post("/api/gens/{gid}/submit")
def submit_web(gid: str, data: dict = Body(...)):
    """网页版人工通道：把网页 AI 的结果粘回来。"""
    text = (data.get("output") or "").strip()
    if not text:
        raise HTTPException(400, "内容为空")
    try:
        return runner.save_web_result(gid, text)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except RuntimeError as exc:            # 本章已有生成在跑 → 409，别让两边互相覆盖
        raise HTTPException(409, str(exc))


@router.get("/api/books/{bid}/cost")
def book_cost(bid: str):
    """这本书的 token 与费用汇总（按步骤 + 模型）。"""
    b = store.query_one("SELECT * FROM books WHERE id=?", [bid])
    if not b:
        raise HTTPException(404, "书不存在")
    rows = store.query(
        "SELECT step_id, model_name, COUNT(*) n, SUM(prompt_tokens) pt, "
        "SUM(completion_tokens) ct, SUM(cache_tokens) ca, SUM(cost) cost "
        "FROM gens WHERE book_id=? GROUP BY step_id, model_name ORDER BY cost DESC", [bid])
    total = sum(float(r["cost"] or 0) for r in rows)
    return {"ok": True, "total": round(total, 4), "book": b["name"],
            "rows": [{"step": r["step_id"], "model": r["model_name"], "calls": r["n"],
                      "prompt_tokens": r["pt"] or 0, "completion_tokens": r["ct"] or 0,
                      "cache_tokens": r["ca"] or 0,
                      "cost": round(float(r["cost"] or 0), 4)} for r in rows]}


@router.post("/api/books/{bid}/cast-check")
def cast_check(bid: str, data: dict = Body(default={})):
    """人设 / 金额一致性校验（只提示，不改正文）。不传 chapter_id 就校验全书。"""
    b = store.query_one("SELECT * FROM books WHERE id=?", [bid])
    if not b:
        raise HTTPException(404, "书不存在")
    cid = (data.get("chapter_id") or "").strip()
    if cid:
        ch = store.query_one("SELECT * FROM chapters WHERE id=?", [cid])
        text = (ch or {}).get("content") or ""
    else:
        text = "\n".join(c["content"] or "" for c in store.query(
            "SELECT content FROM chapters WHERE book_id=? ORDER BY no", [bid]))
    return runner.check_cast(b, text)


@router.post("/api/books/{bid}/auto-chapters")
def auto_chapters(bid: str, data: dict = Body(default={})):
    """一键续写 N 章（后端任务链，关掉页面也会继续跑完）。"""
    b = store.query_one("SELECT * FROM books WHERE id=?", [bid])
    if not b:
        raise HTTPException(404, "书不存在")
    return runner.start_auto_chapters(bid, int(data.get("count") or 3))


@router.get("/api/cost/overview")
def cost_overview():
    """全平台成本总览：按书聚合调用次数、token、费用。"""
    rows = store.query(
        "SELECT book_id, COUNT(*) calls, SUM(prompt_tokens) pt, SUM(completion_tokens) ct, "
        "SUM(cache_tokens) ca, SUM(cost) cost FROM gens GROUP BY book_id ORDER BY cost DESC")
    names = {b["id"]: b["name"] for b in store.query("SELECT id, name FROM books")}
    total = sum(float(r["cost"] or 0) for r in rows)
    return {"ok": True, "total": round(total, 4), "rows": [
        {"book_id": r["book_id"], "book": names.get(r["book_id"], r["book_id"]),
         "calls": r["calls"], "prompt_tokens": r["pt"] or 0,
         "completion_tokens": r["ct"] or 0, "cache_tokens": r["ca"] or 0,
         "cost": round(float(r["cost"] or 0), 4)} for r in rows]}


@router.post("/api/books/{bid}/drift-check")
def drift_check(bid: str):
    """大纲偏离检测：比对大纲与最近 10 章摘要。"""
    b = store.query_one("SELECT * FROM books WHERE id=?", [bid])
    if not b:
        raise HTTPException(404, "书不存在")
    return runner.drift_check(bid)
