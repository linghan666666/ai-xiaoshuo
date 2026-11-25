"""入口：以 ai-xiaoshuo-skill 为核心的番茄 AI 写作平台。

启动： python -m uvicorn app.main:app --host 127.0.0.1 --port 8788
分层：knowledge（skill 知识库·指令引擎）/ flows（流程=skill 工作流）/ llm（模型接入）
      checks（合规检查）/ api（HTTP）/ store（数据）
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from . import store
from .api import books, chapters, checks_api, kb, models_api, short_api
from .config import SKILL_DIR, STATIC_DIR
from .flows import runner

app = FastAPI(title="ai-xiaoshuo · 番茄 AI 写作平台", version="1.1.0")


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    # 启动时备份数据库（保留最近 10 份），并清理上一次进程留下的"正在生成"僵尸记录
    path = store.backup()
    if path:
        print(f"[startup] 数据库已备份：{path}")
    n = runner.clear_stale_running()
    if n:
        print(f"[startup] 已清理 {n} 条中断的生成/任务记录")


@app.post("/api/admin/clear-cache")
def clear_cache():
    """清理平台侧缓存：知识库模块解析缓存 + 生成中僵尸记录 + 生成记录瘦身。"""
    from .knowledge import library
    n = library.clear_cache()
    stale = runner.clear_stale_running()
    # 生成记录里存着每次的完整提示词与输出，清理时顺便瘦身：每本书只保留最近 300 条
    pruned = 0
    for b in store.query("SELECT id FROM books"):
        rows = store.query("SELECT id FROM gens WHERE book_id=? ORDER BY started_at DESC", [b["id"]])
        old = [r["id"] for r in rows[300:]]
        if old:
            store.execute("DELETE FROM gens WHERE id IN (%s)" % ",".join("?" * len(old)), old)
            pruned += len(old)
    return {"ok": True, "library_entries": n, "stale_gens": stale, "pruned_gens": pruned}


@app.get("/api/health")
def health():
    return {"ok": True, "db": store.DB_PATH, "skill_dir": SKILL_DIR}


for _r in (kb.router, models_api.router, books.router, chapters.router, checks_api.router,
           short_api.router):
    app.include_router(_r)

app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
