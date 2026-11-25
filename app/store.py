"""SQLite 数据层：建表 + 读写助手（单文件，全平台唯一数据入口）。

表设计围绕「skill 工作流」：
  models     模型/通道配置（API 或网页人工通道）
  books      书（项目）：题材 / 受众 / 走哪条流程 / 当前进行到第几步
  artifacts  每一步的产出物（人设档案、大纲、书名简介…），可锁定
  chapters   章节正文（生成稿）
  gens       所有模型调用留痕（提示词、输出、耗时、错误）—— 可回溯"这一步的稿是怎么来的"
  checks     检查结果（黑名单预检 / 违禁词 / 投稿前自查）
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
import uuid
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from .config import DATA_DIR, DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS models (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    channel TEXT NOT NULL DEFAULT 'api',      -- api | web（网页版人工通道）
    provider TEXT DEFAULT '',
    purpose TEXT DEFAULT '',
    base_url TEXT DEFAULT '',
    model_name TEXT DEFAULT '',
    api_key TEXT DEFAULT '',                  -- 只存在本机数据库，不外传
    web_url TEXT DEFAULT '',
    web_note TEXT DEFAULT '',
    temperature REAL DEFAULT 0.7,
    max_tokens INTEGER DEFAULT 4096,
    timeout INTEGER DEFAULT 300,
    thinking_off INTEGER DEFAULT 1,           -- 1=要求模型关闭思考，直出正文（推理型模型必开）
    stream INTEGER DEFAULT 1,                 -- 1=流式（防长文超时）
    no_proxy INTEGER DEFAULT 1,               -- 1=跳过系统代理（国内直连常用）
    is_default INTEGER DEFAULT 0,
    enabled INTEGER DEFAULT 1,
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS books (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    genre TEXT DEFAULT '',
    audience TEXT DEFAULT 'male',             -- male | female | none（决定追加哪条赛道指令）
    brief TEXT DEFAULT '',                    -- 一句话设定
    platform TEXT DEFAULT '番茄小说',
    flow_id TEXT DEFAULT 'long_novel',        -- long_novel | mid_novel | short_story
    step_id TEXT DEFAULT '',                  -- 当前进行到的步骤
    step_models TEXT DEFAULT '{}',            -- 步骤级默认模型绑定 {"chapter":"mdl_x","deai":"mdl_y"}
    foreshadows TEXT DEFAULT '',              -- 待回收伏笔账本（每章由模型更新，注入起草防"埋了忘收"）
    created_at TEXT,
    updated_at TEXT
);

CREATE TABLE IF NOT EXISTS artifacts (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    key TEXT NOT NULL,                        -- 步骤产出的标识（如 characters / outline）
    content TEXT DEFAULT '',
    locked INTEGER DEFAULT 0,
    updated_at TEXT,
    UNIQUE(book_id, key)
);

CREATE TABLE IF NOT EXISTS chapters (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    no INTEGER NOT NULL DEFAULT 1,
    title TEXT DEFAULT '',
    brief TEXT DEFAULT '',                    -- 本章要点（生成时的输入）
    content TEXT DEFAULT '',
    summary TEXT DEFAULT '',                  -- 结构化摘要（用于跨章连贯：关键情节/人物状态/悬念）
    progress TEXT DEFAULT '',                 -- 本章走到哪一步：draft | deai
    words INTEGER DEFAULT 0,
    status TEXT DEFAULT 'draft',              -- draft | done（经去AI味/自查）
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(book_id, no)
);

CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    chapter_id TEXT DEFAULT '',
    kind TEXT DEFAULT 'finish_chapter',       -- finish_chapter 等组合任务
    status TEXT DEFAULT 'running',            -- running | success | failed
    steps TEXT DEFAULT '[]',                  -- [{key,name,status,gen_id,msg}]
    error TEXT DEFAULT '',
    created_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS gens (
    id TEXT PRIMARY KEY,
    book_id TEXT DEFAULT '',
    step_id TEXT DEFAULT '',
    chapter_id TEXT DEFAULT '',
    kind TEXT DEFAULT '',                     -- step | chapter | deai | ...
    model_name TEXT DEFAULT '',
    prompt TEXT DEFAULT '',
    output TEXT DEFAULT '',
    status TEXT DEFAULT 'running',            -- running | success | failed
    error TEXT DEFAULT '',
    chars INTEGER DEFAULT 0,
    prompt_tokens INTEGER DEFAULT 0,
    completion_tokens INTEGER DEFAULT 0,
    cache_tokens INTEGER DEFAULT 0,
    cost REAL DEFAULT 0,
    started_at TEXT,
    finished_at TEXT
);

CREATE TABLE IF NOT EXISTS checks (
    id TEXT PRIMARY KEY,
    book_id TEXT NOT NULL,
    chapter_id TEXT DEFAULT '',
    kind TEXT DEFAULT '',                     -- blacklist | violations | presubmit
    ok INTEGER DEFAULT 0,
    report TEXT DEFAULT '',
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT DEFAULT ''
);

-- 番茄短故事专属设置（一本短故事一条；发布页的「作品分类 / 是否使用 AI / 试读比例」）
CREATE TABLE IF NOT EXISTS short_meta (
    book_id TEXT PRIMARY KEY,
    main_category TEXT DEFAULT '',            -- 主分类 key（必选）
    sub_categories TEXT DEFAULT '[]',         -- 次分类 key 数组（JSON，最多再选 7 个）
    use_ai INTEGER DEFAULT 0,                 -- 是否使用 AI：1=是 0=否
    trial_ratio INTEGER DEFAULT 30,           -- 试读比例（%）
    cover_note TEXT DEFAULT '',               -- 封面制作备注
    updated_at TEXT
);
"""


def now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def _ensure_column(c, table: str, column: str, ddl: str) -> None:
    cols = [r[1] for r in c.execute(f"PRAGMA table_info({table})")]
    if column not in cols:
        c.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def init_db() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)
    with sqlite3.connect(DB_PATH) as conn0:
        conn0.executescript(SCHEMA)
        # WAL 是数据库级持久属性：只在这里设一次。并发写（例如同时跑两章的
        # 「一键写完本章」）不会再直接报 database is locked。
        conn0.execute("PRAGMA journal_mode=WAL")
        # 平滑升级：老库补列（新装库由 SCHEMA 直接带上，这里只兜旧库）
        _ensure_column(conn0, "books", "step_models", "step_models TEXT DEFAULT '{}'")
        _ensure_column(conn0, "books", "foreshadows", "foreshadows TEXT DEFAULT ''")
        _ensure_column(conn0, "chapters", "summary", "summary TEXT DEFAULT ''")
        _ensure_column(conn0, "chapters", "progress", "progress TEXT DEFAULT ''")
        _ensure_column(conn0, "gens", "prompt_tokens", "prompt_tokens INTEGER DEFAULT 0")
        _ensure_column(conn0, "gens", "completion_tokens", "completion_tokens INTEGER DEFAULT 0")
        _ensure_column(conn0, "gens", "cache_tokens", "cache_tokens INTEGER DEFAULT 0")
        _ensure_column(conn0, "gens", "cost", "cost REAL DEFAULT 0")
        conn0.commit()


def backup(keep: int = 10) -> str:
    """启动时把数据库备份到 data/backups/，保留最近 keep 份（用 SQLite 在线备份 API，安全）。"""
    if not os.path.exists(DB_PATH):
        return ""
    folder = os.path.join(DATA_DIR, "backups")
    os.makedirs(folder, exist_ok=True)
    dest = os.path.join(folder, f"app-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db")
    try:
        with sqlite3.connect(DB_PATH) as src, sqlite3.connect(dest) as dst:
            src.backup(dst)
    except Exception:  # noqa: BLE001
        return ""
    olds = sorted(f for f in os.listdir(folder) if f.startswith("app-") and f.endswith(".db"))
    for f in olds[:-keep]:
        try:
            os.remove(os.path.join(folder, f))
        except OSError:
            pass
    return dest


@contextmanager
def conn():
    c = sqlite3.connect(DB_PATH, timeout=10)
    c.row_factory = sqlite3.Row
    # busy_timeout / synchronous 是"连接级"设置，每次连接都要设（快、不加锁）。
    # ⚠️ journal_mode 是**数据库级持久属性**，只在 init_db 里设一次即可 ——
    # 每条连接都执行 PRAGMA journal_mode=WAL 需要排它锁，高并发下会偶发
    # "attempt to write a readonly database"（实测 24 线程并发 720 次写失败 4 次）。
    try:
        c.execute("PRAGMA busy_timeout=5000")
        c.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    try:
        yield c
        c.commit()
    finally:
        c.close()


def query(sql: str, args: Iterable = ()) -> List[Dict[str, Any]]:
    with conn() as c:
        return [dict(r) for r in c.execute(sql, tuple(args)).fetchall()]


def query_one(sql: str, args: Iterable = ()) -> Optional[Dict[str, Any]]:
    rows = query(sql, args)
    return rows[0] if rows else None


def execute(sql: str, args: Iterable = ()) -> None:
    """写入：对 SQLITE_BUSY/READONLY 做短重试（并发写时偶发，重试即可成功）。"""
    last = None
    for i in range(3):
        try:
            with conn() as c:
                c.execute(sql, tuple(args))
            return
        except sqlite3.OperationalError as exc:
            msg = str(exc).lower()
            if "locked" not in msg and "readonly" not in msg:
                raise
            last = exc
            time.sleep(0.15 * (i + 1))
    raise last if last else RuntimeError("写入失败")


def insert(table: str, data: Dict[str, Any]) -> Dict[str, Any]:
    cols = ",".join(data)
    marks = ",".join("?" for _ in data)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(data.values()))
    return data


def update(table: str, pk: str, patch: Dict[str, Any]) -> None:
    if not patch:
        return
    sets = ",".join(f"{k}=?" for k in patch)
    execute(f"UPDATE {table} SET {sets} WHERE id=?", list(patch.values()) + [pk])


def update_safe(table: str, pk: str, patch: Dict[str, Any], retries: int = 2) -> bool:
    """写入失败（如 SQLITE_BUSY）时短暂重试；仍失败只告警不抛。

    为什么需要：生成任务的**收尾落库**一旦抛错，那条记录会永远停在 status='running'，
    而 chapter_busy() 正是靠 running 判断的 —— 该章会被永久锁死（只能重启服务解）。
    所以收尾写入必须"尽力而为、绝不抛出"。
    """
    for i in range(retries + 1):
        try:
            update(table, pk, patch)
            return True
        except sqlite3.Error as exc:
            if i == retries:
                print(f"[warn] 落库失败（已重试 {retries} 次）{table}/{pk}: {exc}")
                return False
            time.sleep(0.3)
    return False


def upsert_artifact(book_id: str, key: str, content: str) -> Dict[str, Any]:
    """写入某一步的产出（存在则更新）。"""
    row = query_one("SELECT * FROM artifacts WHERE book_id=? AND key=?", [book_id, key])
    if row:
        update("artifacts", row["id"], {"content": content, "updated_at": now()})
        return query_one("SELECT * FROM artifacts WHERE id=?", [row["id"]])
    data = {"id": new_id("art"), "book_id": book_id, "key": key, "content": content,
            "locked": 0, "updated_at": now()}
    insert("artifacts", data)
    return data


def kv_get(key: str, default: str = "") -> str:
    row = query_one("SELECT value FROM app_settings WHERE key=?", [key])
    return (row or {}).get("value") or default


def kv_set(key: str, value: str) -> None:
    execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value", [key, value])


def jloads(raw: Any, default: Any = None):
    if raw in (None, "", "null"):
        return default
    if isinstance(raw, (dict, list)):
        return raw
    try:
        return json.loads(raw)
    except Exception:  # noqa: BLE001
        return default
