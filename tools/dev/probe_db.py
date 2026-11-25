"""SQLite 并发写入探针：确认 WAL + busy_timeout 生效（P1 回归测试）。

背景：未开 WAL 时，24 线程 × 30 次并发写会有约 4% 的写失败（database is locked），
而落库失败可能让生成记录永远停在 running → 该章被并发锁永久锁死。

用法： python tools/dev/probe_db.py
"""
import os
import sqlite3
import sys
import tempfile
import threading
import time

THREADS, ROUNDS = 24, 30


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    # 用临时库，绝不碰 data/app.db
    tmp = os.path.join(tempfile.gettempdir(), f"aixs_probe_{os.getpid()}.db")
    os.environ["PLATFORM_DB"] = tmp
    from app import store

    store.init_db()
    with store.conn() as c:
        mode = c.execute("PRAGMA journal_mode").fetchone()[0]
        busy = c.execute("PRAGMA busy_timeout").fetchone()[0]
    print(f"journal_mode = {mode}    busy_timeout = {busy}")

    errors, done = [], []

    def worker(tid: int) -> None:
        for r in range(ROUNDS):
            try:
                store.execute("INSERT INTO app_settings(key,value) VALUES(?,?) "
                              "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                              [f"probe-{tid}-{r}", str(r)])
                done.append(1)
            except sqlite3.Error as exc:
                errors.append(str(exc))

    t0 = time.time()
    ts = [threading.Thread(target=worker, args=(i,)) for i in range(THREADS)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    cost = time.time() - t0

    total = THREADS * ROUNDS
    print(f"{THREADS} 线程 × {ROUNDS} 次并发写：耗时 {cost:.2f}s，成功 {len(done)}/{total}，"
          f"失败 {len(errors)}")
    if errors:
        print("  失败样例：", errors[0])
    try:
        os.remove(tmp)
    except OSError:
        pass
    if errors:
        sys.exit(f"[x] 仍有 {len(errors)} 次写失败 —— 并发写入不可靠")
    print("✅ 并发写入 0 失败（WAL + busy_timeout 生效）")


if __name__ == "__main__":
    main()
