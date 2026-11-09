"""上游 provider 转发：把请求按模型名路由到外部的 OpenAI 兼容服务。

为什么需要它
------------
``config.json`` 里的 ``upstream_providers`` 段（Grok2API / MiniMax2API 等）
本来是留给「一个端口代理多家」的，但 Python 侧一直没有消费它 ——
``/v1/chat/completions`` 只会去 ChatGPT 本地账号池里找能支持该模型的账号，
于是用 ``model: grok-4.7`` 打 8000 会得到
「model 'grok-4.7' is not available to any active account」。

本模块补上这一层：模型名命中某个 provider 的 models 列表时，
请求原样转发到那个 provider 的 base_url，并换成 provider 自己的 api_key。

约定
----
* provider 形如 ``{"id","name","type","base_url","api_key","models","enabled"}``
* ``models`` 里可以写 ``*`` 表示通配全部模型
* 只支持 OpenAI 兼容的 ``/v1/chat/completions`` 与 Anthropic 兼容的 ``/v1/messages``
"""
from __future__ import annotations

import json
from typing import Any, Iterator

import httpx

from services.config import config

# 转发时剔掉的内部字段
_INTERNAL_FIELDS = {"base_url", "_account_email", "_conversation_id"}


def _providers() -> list[dict[str, Any]]:
    raw = config.data.get("upstream_providers")
    if not isinstance(raw, list):
        return []
    return [p for p in raw if isinstance(p, dict)]


def _enabled(provider: dict[str, Any]) -> bool:
    value = provider.get("enabled", True)
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "off")
    return bool(value)


def list_providers() -> list[dict[str, Any]]:
    """对外暴露的 provider 列表（不含密钥）。"""
    out = []
    for p in _providers():
        if not _enabled(p):
            continue
        out.append({
            "id": str(p.get("id") or ""),
            "name": str(p.get("name") or ""),
            "type": str(p.get("type") or "openai"),
            "base_url": str(p.get("base_url") or ""),
            "models": [str(m) for m in (p.get("models") or [])],
        })
    return out


def find_provider(model: str) -> dict[str, Any] | None:
    """按模型名找 provider。找不到返回 None（走原有 ChatGPT 账号池）。"""
    name = str(model or "").strip()
    if not name:
        return None
    for p in _providers():
        if not _enabled(p):
            continue
        models = [str(m).strip() for m in (p.get("models") or [])]
        if "*" in models or name in models:
            return p
    return None


def provider_models() -> list[dict[str, Any]]:
    """provider 侧的模型列表（用于 /v1/models 合并展示）。"""
    out: list[dict[str, Any]] = []
    for p in list_providers():
        for m in p["models"]:
            if m == "*":
                continue
            out.append({
                "id": m,
                "object": "model",
                "created": 0,
                "owned_by": str(p.get("type") or "openai"),
                "provider": p["name"],
            })
    return out


def _endpoint(provider: dict[str, Any], path: str) -> str:
    base = str(provider.get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError(f"上游 {provider.get('name')} 没有配 base_url")
    return base + path


def _headers(provider: dict[str, Any]) -> dict[str, str]:
    headers = {"Content-Type": "application/json"}
    key = str(provider.get("api_key") or "").strip()
    if key:
        headers["Authorization"] = "Bearer " + key
    extra = provider.get("headers")
    if isinstance(extra, dict):
        for k, v in extra.items():
            headers[str(k)] = str(v)
    return headers


def _timeout(provider: dict[str, Any]) -> float:
    try:
        return float(provider.get("timeout") or 300)
    except Exception:
        return 300.0


def _clean(payload: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in dict(payload or {}).items() if k not in _INTERNAL_FIELDS}


def chat_completion(provider: dict[str, Any], payload: dict[str, Any]) -> Any:
    """转发 /v1/chat/completions。

    返回 dict（非流式）或 chunk 生成器（流式，每个元素是一个已解析的 dict）。
    """
    url = _endpoint(provider, "/v1/chat/completions")
    body = _clean(payload)
    headers = _headers(provider)
    timeout = _timeout(provider)

    if body.get("stream"):
        def gen() -> Iterator[dict[str, Any]]:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        detail = resp.read().decode("utf-8", "replace")
                        raise RuntimeError(
                            f"上游 {provider.get('name')} 返回 {resp.status_code}: {detail[:300]}")
                    for line in resp.iter_lines():
                        if not line:
                            continue
                        if not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            # [DONE] 由外层 sse_json_stream 统一补
                            continue
                        try:
                            yield json.loads(chunk)
                        except Exception:
                            yield {"choices": [{"delta": {"content": chunk}}]}
        return gen()

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
    if resp.status_code >= 400:
        detail = resp.text
        raise RuntimeError(f"上游 {provider.get('name')} 返回 {resp.status_code}: {detail[:300]}")
    try:
        return resp.json()
    except Exception:
        return {"error": {"message": resp.text[:300], "type": "upstream_invalid_json"}}


def responses(provider: dict[str, Any], payload: dict[str, Any]) -> Any:
    """转发 OpenAI Responses 协议（/v1/responses）。"""
    url = _endpoint(provider, "/v1/responses")
    body = _clean(payload)
    headers = _headers(provider)
    timeout = _timeout(provider)
    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"上游 {provider.get('name')} 返回 {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except Exception:
        return {"error": {"message": resp.text[:300], "type": "upstream_invalid_json"}}


def messages(provider: dict[str, Any], payload: dict[str, Any]) -> Any:
    """转发 Anthropic 兼容的 /v1/messages。"""
    url = _endpoint(provider, "/v1/messages")
    body = _clean(payload)
    headers = _headers(provider)
    # Anthropic 协议用 x-api-key，但不少网关同时接受 Bearer
    key = str(provider.get("api_key") or "").strip()
    if key:
        headers.setdefault("x-api-key", key)
        headers.setdefault("anthropic-version", "2023-06-01")
    timeout = _timeout(provider)

    if body.get("stream"):
        def gen() -> Iterator[dict[str, Any]]:
            with httpx.Client(timeout=timeout) as client:
                with client.stream("POST", url, json=body, headers=headers) as resp:
                    if resp.status_code >= 400:
                        detail = resp.read().decode("utf-8", "replace")
                        raise RuntimeError(
                            f"上游 {provider.get('name')} 返回 {resp.status_code}: {detail[:300]}")
                    for line in resp.iter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        chunk = line[5:].strip()
                        if not chunk or chunk == "[DONE]":
                            continue
                        try:
                            yield json.loads(chunk)
                        except Exception:
                            yield {"type": "content_block_delta", "delta": {"text": chunk}}
        return gen()

    with httpx.Client(timeout=timeout) as client:
        resp = client.post(url, json=body, headers=headers)
    if resp.status_code >= 400:
        raise RuntimeError(f"上游 {provider.get('name')} 返回 {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except Exception:
        return {"error": {"message": resp.text[:300], "type": "upstream_invalid_json"}}
