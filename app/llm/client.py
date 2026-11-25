"""模型接入：OpenAI 兼容 API（流式 + 关思考 + 正文抢救）与网页版人工通道。

只做一件事：把提示词发给模型，把**正文**拿回来。踩过的坑都在这几行里：
  · 推理型模型会把额度花在思考上 → 可显式关思考（thinking={"type":"disabled"}）；
  · 部分模型（GLM 系）长输出时正文会跑到 reasoning_content → 需要抢救；
  · 非流式下长输出会长时间无数据往返 → 撞满读超时，所以默认走流式（SSE）；
  · 本机有代理时国内直连会 502 → 支持"跳过系统代理"。
"""
from __future__ import annotations

import json
import os
import re
import time
from typing import Any, Dict, List, Tuple

import httpx

TRUST_ENV = os.environ.get("PLATFORM_TRUST_ENV", "1").lower() not in ("0", "false", "no")


class LLMError(Exception):
    pass


def _endpoint(base_url: str) -> str:
    url = (base_url or "").strip().rstrip("/")
    if not url:
        raise LLMError("未配置接口地址 base_url")
    return url if url.endswith("/chat/completions") else url + "/chat/completions"


def _client(timeout: int, no_proxy: bool) -> httpx.Client:
    return httpx.Client(timeout=timeout, trust_env=(not no_proxy) and TRUST_ENV)


def _friendly(status: int, body: str) -> str:
    if status in (502, 503, 504) or "10061" in body or "upstream connect" in body:
        return f"HTTP {status}：疑似网络/代理问题，请在模型设置里勾选「跳过系统代理」；{body[:200]}"
    if status == 401 or "invalid_api_key" in body:
        return f"HTTP 401：密钥无效或未生效；{body[:200]}"
    if status == 404:
        return f"HTTP 404：接口地址或模型名不对（{body[:200]}）"
    return f"HTTP {status}：{body[:300]}"


def _looks_body(t: str) -> bool:
    return len(t or "") >= 300 and (t.count("。") >= 12 or (t.count("“") + t.count("「")) >= 3)


def _salvage(reasoning: str) -> str:
    """从思考通道里抢救正文：找最后一个章节标题或"以下是正文"类标记，取其后内容。"""
    if not reasoning or len(reasoning) < 400:
        return ""
    heads = list(re.finditer(r"第\s*\d+\s*章\s*[《<][^》>]{1,40}[》>]", reasoning))
    if heads and len(reasoning) - heads[-1].start() >= 400:
        return reasoning[heads[-1].start():].strip()
    for kw in ("以下是正文", "正文如下", "以下是修改后的正文", "以下是润色后的", "以下是完整正文",
               "以下是排版", "成稿如下", "以下是最终"):
        i = reasoning.rfind(kw)
        if i >= 0 and len(reasoning) - i >= 400:
            return reasoning[i + len(kw):].strip()
    tail = reasoning[-int(len(reasoning) * 0.6):]
    if (tail.count("“") + tail.count("「")) >= 6 and tail.count("。") >= 20:
        return tail.strip()
    return ""


def pick_body(content: str, reasoning: str) -> str:
    """统一选出流程需要的正文（开/关思考都走这一套）。"""
    c, r = (content or "").strip(), (reasoning or "").strip()
    if _looks_body(c):
        sv = _salvage(r) if r else ""
        if sv and len(sv) > len(c) * 1.25:
            return sv
        return c
    if r:
        sv = _salvage(r)
        if sv:
            return sv
        if _looks_body(r):
            return r
    return c or r


def _stream(client: httpx.Client, url: str, payload: Dict[str, Any],
            headers: Dict[str, str], total_limit: int = 1800) -> Dict[str, Any]:
    body, reason = [], []
    usage: Dict[str, Any] = {}
    t0 = time.time()
    with client.stream("POST", url, json=payload, headers=headers) as resp:
        if resp.status_code >= 400:
            raise LLMError(_friendly(resp.status_code, resp.read().decode("utf-8", "ignore")))
        ctype = (resp.headers.get("content-type") or "").lower()
        if "event-stream" not in ctype:                      # 网关忽略了 stream=true
            raw = resp.read().decode("utf-8", "ignore")
            try:
                return json.loads(raw)
            except Exception:
                raise LLMError(f"返回非 JSON：{raw[:200]}")
        for line in resp.iter_lines():
            if time.time() - t0 > total_limit:
                raise LLMError(f"流式响应超过 {total_limit} 秒未结束，已中断（已收正文 {len(''.join(body))} 字）")
            if not line or not line.startswith("data:"):
                continue
            chunk = line[5:].strip()
            if not chunk or chunk == "[DONE]":
                break
            try:
                obj = json.loads(chunk)
            except Exception:
                continue
            if obj.get("usage"):
                usage = obj["usage"]
            ch = (obj.get("choices") or [{}])[0]
            delta = ch.get("delta") or {}
            if delta.get("content"):
                body.append(delta["content"])
            if delta.get("reasoning_content"):
                reason.append(delta["reasoning_content"])
    return {"choices": [{"message": {"content": "".join(body),
                                    "reasoning_content": "".join(reason)}}],
            "usage": usage}


def chat(model: Dict[str, Any], prompt: str, system: str = "") -> Tuple[str, Dict[str, Any]]:
    """调用模型，返回 (正文, 元信息)。同步函数 —— 调用方放到线程里执行。"""
    messages: List[Dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: Dict[str, Any] = {
        "model": model.get("model_name") or "",
        "messages": messages,
        "temperature": float(model.get("temperature") or 0.7),
        "max_tokens": int(model.get("max_tokens") or 4096),
        "stream": bool(int(model.get("stream") if model.get("stream") is not None else 1)),
    }
    if int(model.get("thinking_off") if model.get("thinking_off") is not None else 1):
        payload["thinking"] = {"type": "disabled"}
    if payload["stream"]:
        # 让网关在流结束时回一段 usage（OpenAI 兼容写法）；不支持的网关会忽略它
        payload["stream_options"] = {"include_usage": True}

    headers = {"Content-Type": "application/json"}
    key = (model.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = f"Bearer {key}"
    url = _endpoint(model.get("base_url", ""))
    timeout = int(model.get("timeout") or 300)
    no_proxy = bool(int(model.get("no_proxy") if model.get("no_proxy") is not None else 1))

    meta: Dict[str, Any] = {"model": model.get("name") or model.get("model_name")}
    try:
        with _client(timeout, no_proxy) as client:
            t0 = time.time()
            if payload["stream"]:
                data = _stream(client, url, payload, headers)
            else:
                resp = client.post(url, json=payload, headers=headers)
                if resp.status_code >= 400:
                    raise LLMError(_friendly(resp.status_code, resp.text))
                data = resp.json()
            meta["seconds"] = round(time.time() - t0, 1)
    except LLMError:
        raise
    except httpx.TimeoutException as exc:
        raise LLMError(f"接口超时（{timeout}s，{type(exc).__name__}）。长文建议开流式；"
                       f"推理型模型可关思考；也可调大超时时间。") from exc
    except httpx.HTTPError as exc:
        raise LLMError(f"网络异常（{type(exc).__name__}）：{exc or '无详情'}。"
                       f"检查接口地址与代理设置（国内直连通常要勾选「跳过系统代理」）。") from exc

    try:
        msg = data["choices"][0]["message"]
    except (KeyError, IndexError, TypeError) as exc:
        raise LLMError(f"响应结构异常：{str(data)[:200]}") from exc
    body = pick_body(msg.get("content") or "", msg.get("reasoning_content") or "")
    if not body.strip():
        raise LLMError("模型没有返回正文（可能思考未关闭或输出被截断）")
    meta["chars"] = len(body)
    u = (data.get("usage") or {}) if isinstance(data, dict) else {}
    details = u.get("prompt_tokens_details") or {}
    meta["usage"] = {
        "prompt_tokens": int(u.get("prompt_tokens") or 0),
        "completion_tokens": int(u.get("completion_tokens") or 0),
        "total_tokens": int(u.get("total_tokens") or 0),
        "cache_tokens": int(details.get("cached_tokens")
                            or u.get("cache_read_input_tokens") or 0),
    }
    return body.strip(), meta
