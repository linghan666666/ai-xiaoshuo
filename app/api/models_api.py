"""模型路由：模型/通道配置、连通性测试、推荐配置与绑定总览。"""
from __future__ import annotations

from typing import Any, Dict

import httpx
from fastapi import APIRouter, Body, HTTPException

from .. import store
from ..llm import client as llm

router = APIRouter()

_FIELDS = ("name", "channel", "provider", "purpose", "base_url", "model_name", "api_key",
           "web_url", "web_note", "temperature", "max_tokens", "timeout",
           "thinking_off", "stream", "no_proxy", "is_default", "enabled")


def _mask(row: dict) -> dict:
    """不回显密钥明文，只告诉前端"填了没有"。"""
    r = dict(row)
    r["has_key"] = bool((r.get("api_key") or "").strip())
    r["api_key"] = ""
    return r


@router.get("/api/models")
def list_models():
    rows = store.query("SELECT * FROM models ORDER BY is_default DESC, created_at")
    return {"ok": True, "models": [_mask(r) for r in rows]}


@router.post("/api/models")
def create_model(data: dict = Body(...)):
    row = {k: data.get(k) for k in _FIELDS if k in data}
    row.setdefault("name", "未命名模型")
    row.setdefault("channel", "api")
    row.update({"id": store.new_id("mdl"), "created_at": store.now()})
    if int(row.get("is_default") or 0):
        store.execute("UPDATE models SET is_default=0")
    store.insert("models", row)
    return {"ok": True, "model": _mask(store.query_one("SELECT * FROM models WHERE id=?", [row["id"]]))}


@router.put("/api/models/{mid}")
def update_model(mid: str, data: dict = Body(...)):
    if not store.query_one("SELECT id FROM models WHERE id=?", [mid]):
        raise HTTPException(404, "模型不存在")
    patch = {k: v for k, v in data.items() if k in _FIELDS}
    if patch.get("api_key") in ("", None) and "api_key" in data:
        patch.pop("api_key")          # 空值不改动已存的密钥
    if int(patch.get("is_default") or 0):
        store.execute("UPDATE models SET is_default=0")
    store.update("models", mid, patch)
    return {"ok": True, "model": _mask(store.query_one("SELECT * FROM models WHERE id=?", [mid]))}


@router.delete("/api/models/{mid}")
def delete_model(mid: str):
    store.execute("DELETE FROM models WHERE id=?", [mid])
    return {"ok": True}


@router.post("/api/models/{mid}/default")
def set_default(mid: str):
    if not store.query_one("SELECT id FROM models WHERE id=?", [mid]):
        raise HTTPException(404, "模型不存在")
    store.execute("UPDATE models SET is_default=0")
    store.update("models", mid, {"is_default": 1, "enabled": 1})
    return {"ok": True}


@router.post("/api/models/{mid}/test")
def test_model(mid: str, data: dict = Body(default={})):
    m = store.query_one("SELECT * FROM models WHERE id=?", [mid])
    if not m:
        raise HTTPException(404, "模型不存在")
    if m.get("channel") == "web":
        url = (m.get("web_url") or "").strip()
        if not url:
            raise HTTPException(400, "未填写网页版入口地址")
        try:
            with llm._client(30, bool(int(m.get("no_proxy") or 1))) as c:
                r = c.get(url, follow_redirects=True)
            return {"ok": True, "channel": "web", "status": r.status_code,
                    "message": f"入口可达（HTTP {r.status_code}）。注意：网页版是人工通道，"
                               f"平台不会替你点网页 —— 生成时会给你提示词，你去网页执行后把结果粘回来。"}
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(400, f"入口不可达：{str(exc)[:200]}")
    try:
        out, meta = llm.chat(m, data.get("prompt") or "请回复两个字：正常", "简短回答即可。")
        return {"ok": True, "channel": "api", "output": out[:300], "meta": meta}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"调用失败：{str(exc)[:300]}")


def _fetch_upstream(base_url: str, api_key: str) -> list:
    """拉取网关的可用模型 id 列表（OpenAI 兼容 /models）。"""
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise HTTPException(400, "请先填接口地址 base_url")
    url = base if base.endswith("/models") else base + "/models"
    headers = {}
    if (api_key or "").strip():
        headers["Authorization"] = f"Bearer {api_key.strip()}"
    try:
        with httpx.Client(timeout=40, trust_env=False) as c:
            r = c.get(url, headers=headers)
        if r.status_code >= 400:
            raise HTTPException(400, f"拉取失败 HTTP {r.status_code}：{r.text[:200]}")
        return sorted({i.get("id") for i in (r.json().get("data") or []) if i.get("id")})
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"拉取失败：{str(exc)[:200]}")


# 「按角色分工」的推荐配置（依据 skill 02a《AI 模型适配使用建议》）
# 两档：value=省钱档（日常日更）；quality=质量档（手册称豆包是番茄文风天花板，出稿更贴平台）
# match：候选 id 需全部包含其中任一组关键词（小写子串），命中即选用（id 一律取自网关真实列表）。
PRESET_TIERS: Dict[str, Dict[str, Any]] = {
    "value": {
        "label": "省钱档",
        "note": "一章约 ¥0.03–0.06；Draft 用 DeepSeek Flash，改写用 GLM-5.3-FlashX，"
                "策划（选题/大纲/人设/黄金三章）仍走 DeepSeek V4 Pro——只跑几次、总价极低但决定全书骨架",
        "items": [
            {"role": "起草", "match": [["deepseek", "flash"]],
             "name": "DeepSeek Flash（起草·省钱档）", "purpose": "章节正文起草（¥1/¥4，384K）",
             "temperature": 0.85, "max_tokens": 8192, "thinking_off": 1},
            {"role": "改写", "match": [["glm", "5.3", "flashx"], ["glm", "flashx"]],
             "name": "GLM-5.3-FlashX（改写·去AI味）", "purpose": "去 AI 味 / 润色（¥2/¥7，98 秒/章，实测一次落地）",
             "temperature": 0.65, "max_tokens": 16384, "thinking_off": 0},
            {"role": "大纲", "match": [["v4", "pro"], ["deepseek", "pro"]],
             "name": "DeepSeek V4 Pro（大纲·人设）", "purpose": "大纲 / 人设 / 长线规划（一次性，质量优先）",
             "temperature": 0.7, "max_tokens": 8192, "thinking_off": 1},
            {"role": "扫描", "match": [["kimi", "k2"], ["kimi"]],
             "name": "Kimi K2.6（全文扫描）", "purpose": "全文逻辑漏洞扫描 / 超长大纲（256K）",
             "temperature": 0.6, "max_tokens": 16384, "thinking_off": 1},
        ],
    },
    "quality": {
        "label": "质量档",
        "note": "一章约 ¥0.10–0.15；起草换豆包 Seed Pro（手册说番茄文风天花板、过审率高）",
        "items": [
            {"role": "起草", "match": [["seed", "pro"], ["doubao", "pro"]],
             "name": "豆包 Seed 2.1 Pro（起草·质量档）",
             "purpose": "章节正文起草（¥6/¥30，手册称番茄文风天花板、违规率低）",
             "temperature": 0.85, "max_tokens": 8192, "thinking_off": 1},
            {"role": "改写", "match": [["glm", "5.3", "flashx"], ["glm", "flashx"]],
             "name": "GLM-5.3-FlashX（改写·去AI味）",
             "purpose": "去 AI 味 / 润色（¥2/¥7，实测一次落地；glm-5.3-flash 会提前停笔，已弃用）",
             "temperature": 0.65, "max_tokens": 16384, "thinking_off": 0},
            {"role": "大纲", "match": [["v4", "pro"], ["deepseek", "pro"]],
             "name": "DeepSeek V4 Pro（大纲·人设）",
             "purpose": "大纲 / 人设 / 长线规划（¥4.5/¥13.5，384K，一次性做质量）",
             "temperature": 0.7, "max_tokens": 8192, "thinking_off": 1},
            {"role": "扫描", "match": [["kimi", "k2"], ["kimi"]],
             "name": "Kimi K2.6（全文扫描）",
             "purpose": "全文逻辑漏洞扫描 / 超长大纲（¥6.5/¥27，256K）",
             "temperature": 0.6, "max_tokens": 16384, "thinking_off": 1},
        ],
    },
}


@router.get("/api/models/bindings")
def model_bindings():
    """反向总览：每个模型绑在哪些书的哪些步骤上（配错模型时一眼能查出来）。"""
    models = {m["id"]: m["name"] for m in store.query("SELECT id, name FROM models")}
    out: Dict[str, Any] = {mid: [] for mid in models}
    for b in store.query("SELECT id, name, step_models FROM books"):
        binds = store.jloads(b.get("step_models"), {}) or {}
        for step_id, mid in binds.items():
            if mid in out:
                out[mid].append({"book": b["name"], "book_id": b["id"], "step": step_id})
    defaults = [m["id"] for m in store.query("SELECT id FROM models WHERE is_default=1")]
    return {"ok": True, "bindings": out, "defaults": defaults}


@router.post("/api/models/presets")
def create_presets(data: dict = Body(...)):
    """一键创建推荐模型配置：拉取网关模型列表 → 按角色关键词匹配 → 建好并设好参数。

    body.tier: value=省钱档（默认）| quality=质量档（起草换豆包 Seed Pro）。
    不猜模型名：id 一律来自网关真实返回的列表；已有同名配置会跳过。
    """
    tier = (data.get("tier") or "value").strip()
    pack = PRESET_TIERS.get(tier) or PRESET_TIERS["value"]
    base = (data.get("base_url") or "").strip()
    key = (data.get("api_key") or "").strip()
    ids = _fetch_upstream(base, key)
    existing = {m["name"] for m in store.query("SELECT name FROM models")}
    created, skipped, roles = [], [], {}
    for p in pack["items"]:
        hit = next((i for i in ids
                    if any(all(t in i.lower() for t in grp) for grp in p["match"])), "")
        if not hit:
            skipped.append({"role": p["role"], "reason": "网关里没匹配到可用模型"})
            continue
        if p["name"] in existing:
            row = store.query_one("SELECT id FROM models WHERE name=?", [p["name"]])
            roles[p["role"]] = row["id"]
            skipped.append({"role": p["role"], "reason": "同名配置已存在"})
            continue
        mid = store.new_id("mdl")
        store.insert("models", {
            "id": mid, "name": p["name"], "channel": "api", "purpose": p["purpose"],
            "base_url": base, "model_name": hit, "api_key": key,
            "temperature": p["temperature"], "max_tokens": p["max_tokens"],
            "timeout": 600, "thinking_off": p["thinking_off"], "stream": 1, "no_proxy": 1,
            "is_default": 0, "enabled": 1, "created_at": store.now(),
        })
        roles[p["role"]] = mid
        created.append({"role": p["role"], "name": p["name"], "model_name": hit, "id": mid})
    if created and not store.query_one("SELECT id FROM models WHERE is_default=1"):
        store.update("models", created[0]["id"], {"is_default": 1})
    return {"ok": True, "tier": tier, "tier_label": pack["label"], "tier_note": pack["note"],
            "created": created, "skipped": skipped, "roles": roles,
            "upstream_count": len(ids),
            "message": (f"已按「{pack['label']}」创建 {len(created)} 条配置"
                        + (f"，跳过 {len(skipped)} 条" if skipped else ""))}


@router.get("/api/models/preset-tiers")
def preset_tiers():
    """可选的档位（前端按钮用）。"""
    return {"ok": True, "tiers": [{"id": k, "label": v["label"], "note": v["note"],
                                   "roles": [i["role"] + "=" + i["name"] for i in v["items"]]}
                                  for k, v in PRESET_TIERS.items()]}


@router.post("/api/models/upstream")
def upstream(data: dict = Body(...)):
    """拉取网关可用模型列表（OpenAI 兼容 /models），方便一个 key 挂多个模型。"""
    ids = _fetch_upstream(data.get("base_url") or "", data.get("api_key") or "")
    return {"ok": True, "count": len(ids), "ids": ids}
