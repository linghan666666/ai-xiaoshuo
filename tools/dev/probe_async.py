"""后台任务"非阻塞"探针（P0 回归测试）。

背景：曾出现三个端点（/finish、/summary、/foreshadows/refresh）把后台任务
降级成"在请求线程里同步跑完"的问题——用户看到的是卡死的界面，逐步进度完全失效。

本脚本用「每次调用固定睡 N 秒」的假模型，测量各端点的**提交耗时**：
  · 真后台：提交应立刻返回（< 1.5 秒），任务在后台推进；
  · 阻塞退化：提交耗时会等于「链上模型调用次数 × N 秒」。

用法： python tools/dev/probe_async.py
"""
import os
import sys
import threading
import time

import httpx

SLEEP = 2  # 假模型每次调用睡多久


def main() -> None:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    import uvicorn
    from fastapi.testclient import TestClient

    from app.main import app
    from tools.dev.mock_llm import app as mock_app

    cfg = uvicorn.Config(mock_app, host="127.0.0.1", port=8898, log_level="warning")
    server = uvicorn.Server(cfg)
    threading.Thread(target=server.run, daemon=True).start()
    for _ in range(50):
        try:
            httpx.get("http://127.0.0.1:8898/v1/models", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.2)

    print(f"（假模型每次调用固定睡 {SLEEP} 秒，用来看端点是否真后台）\n")
    with TestClient(app) as c:
        # 记下用户当前的默认模型，跑完必须还回去（探针会把假模型设为默认，别把配置弄丢）
        prev_default = next((m["id"] for m in c.get("/api/models").json()["models"]
                             if m["is_default"]), "")
        for m in c.get("/api/models").json()["models"]:
            if m["name"].startswith("SLOW"):
                c.delete(f"/api/models/{m['id']}")
        c.post("/api/models", json={
            "name": f"SLOW-sleep-{SLEEP}", "channel": "api", "base_url": "http://127.0.0.1:8898/v1",
            "model_name": f"sleep-{SLEEP}", "is_default": 1, "enabled": 1,
            "stream": 0, "thinking_off": 1, "no_proxy": 1, "timeout": 120})
        book = c.post("/api/books", json={"name": "__探针书", "genre": "都市", "audience": "male",
                                          "brief": "探针", "flow_id": "long_novel"}).json()["book"]
        ch = c.post(f"/api/books/{book['id']}/chapters", json={"title": "探针章", "brief": "探针"}).json()["chapter"]

        rows = []
        t0 = time.time()
        r = c.post(f"/api/books/{book['id']}/generate", json={"step_id": "chapter", "chapter_id": ch["id"]})
        rows.append(("POST /generate（单步生成）", time.time() - t0, r.status_code == 200))
        time.sleep(0.3)   # 等这条跑完，避免后面的提交被并发锁挡住
        c.post(f"/api/gens/{r.json()['gen_id']}/retry") if False else None

        # 等第一次生成结束（SLEEP 秒）
        for _ in range(60):
            if c.get(f"/api/books/{book['id']}/gens").json()["gens"][0]["status"] != "running":
                break
            time.sleep(0.5)

        t0 = time.time()
        rs = c.post(f"/api/chapters/{ch['id']}/summary")
        rows.append(("POST /chapters/{id}/summary", time.time() - t0, rs.status_code == 200))

        t0 = time.time()
        rf = c.post(f"/api/books/{book['id']}/foreshadows/refresh", json={"chapter_id": ch["id"]})
        rows.append(("POST /foreshadows/refresh", time.time() - t0, rf.status_code == 200))

        # 等摘要/伏笔跑完（各 SLEEP 秒）
        time.sleep(SLEEP * 2 + 2)

        t0 = time.time()
        rq = c.post(f"/api/chapters/{ch['id']}/finish")
        rows.append(("POST /chapters/{id}/finish（链上 4 次调用）", time.time() - t0, rq.status_code == 200))

        print(f"{'端点':42s} {'提交耗时':>10s}   判定")
        bad = 0
        for name, cost, okk in rows:
            verdict = "✅ 真后台" if cost < 1.5 else f"❌ 阻塞（≈{round(cost / SLEEP)} 次调用被卡在请求里）"
            if cost >= 1.5:
                bad += 1
            print(f"{name:42s} {cost:9.2f}s   {verdict} {'（HTTP 非 200）' if not okk else ''}")

        # 清理
        for m in c.get("/api/models").json()["models"]:
            if m["name"].startswith("SLOW"):
                c.delete(f"/api/models/{m['id']}")
        c.delete(f"/api/books/{book['id']}")
        if prev_default and c.get("/api/models").json()["models"]:
            c.post(f"/api/models/{prev_default}/default")     # 还回默认模型
        server.should_exit = True

    print()
    if bad:
        sys.exit(f"[x] 有 {bad} 个端点是阻塞的 —— 后台任务没生效")
    print("✅ 全部端点都是真后台：提交立刻返回，任务在后台推进")


if __name__ == "__main__":
    main()
