"""Edge Gateway 主入口：OpenAI/Anthropic 双协议 + Mock Provider

核心能力（规格 §8）：
- PromptPlan 注入：转发前插入 identity/continuity/system prompt
- Session ID 解析与 namespace 映射：多轮对话连续性保障
- 消息幂等/去重：基于 message_id / content hash
- Ledger provenance：记录每次转发的 timestamp, session, model, tokens, latency
"""

from __future__ import annotations

import httpx
import json
import logging
import time
import uuid
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import load_config
from .providers import create_provider, list_providers
from .session import resolve_session_namespace
from .prompt_plan import load_prompt_plan, PromptPlan
from .idempotency import check_idempotency
from .ledger import LedgerClient, hash_request, hash_response

# ─── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gateway.main")

# ─── 配置 ──────────────────────────────────────────────────────────────────────
config = load_config()
logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

# ─── PromptPlan ────────────────────────────────────────────────────────────────
_prompt_plan = load_prompt_plan()

# ─── Ledger 客户端 ─────────────────────────────────────────────────────────────
_ledger = LedgerClient(config.postgres_dsn)

# ─── Provider 实例（lazy init）──────────────────────────────────────────────────
_providers: dict[str, Any] = {}
_current_provider_name: str = config.default_provider

# ─── 每个 session 的消息计数器（用于 message_index）──────────────────────────────
_session_message_index: dict[str, int] = {}


def _get_provider(name: str | None = None):
    global _providers, _current_provider_name
    name = name or _current_provider_name
    if name not in _providers:
        _providers[name] = create_provider(name, config)
    return _providers[name]


def _require_admin_auth(request: Request) -> None:
    """检查管理接口的 Bearer token 鉴权。admin_token 为空时跳过（仅测试/开发）。"""
    if not config.admin_token:
        return
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    token = auth[7:].strip()
    if token != config.admin_token:
        logger.warning("admin auth failed: invalid token from %s", request.client)
        raise HTTPException(status_code=403, detail="Invalid admin token")


def _next_message_index(session_id: str) -> int:
    global _session_message_index
    idx = _session_message_index.get(session_id, 0)
    _session_message_index[session_id] = idx + 1
    return idx


# ─── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Edge Gateway", version="1.1.0")


@app.on_event("shutdown")
async def _shutdown() -> None:
    for p in _providers.values():
        if hasattr(p, "close"):
            await p.close()


# ─── 健康检查 ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    if not _ledger._available:
        raise HTTPException(status_code=503, detail="Ledger unavailable")
    return {
        "status": "ok",
        "service": "edge-gateway",
        "providers": list_providers(),
        "current_provider": _current_provider_name,
        "prompt_plan_enabled": _prompt_plan.enabled,
        "ledger_available": _ledger._available,
    }
@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "edge-gateway",
        "providers": list_providers(),
        "current_provider": _current_provider_name,
        "prompt_plan_enabled": _prompt_plan.enabled,
        "ledger_available": _ledger._available,
    }


@app.get("/v1/providers")
async def providers() -> dict:
    return {"providers": list_providers(), "current": _current_provider_name}


@app.post("/v1/switch-provider")
async def switch_provider(request: Request) -> dict:
    """
    热切换 provider（仅 mock/openai/anthropic）。
    需要 Bearer admin_token 鉴权（admin_token 为空时跳过，仅用于测试/开发）。
    """
    _require_admin_auth(request)
    global _current_provider_name
    body = await request.json()
    name = body.get("provider", "").lower()
    if name not in list_providers():
        raise HTTPException(status_code=400, detail=f"Unknown provider: {name}")
    _current_provider_name = name
    logger.info("provider switched to: %s", name)
    return {"provider": name, "status": "ok"}


# ─── 核心转发逻辑（含 PromptPlan / Session / 幂等 / Provenance）──────────────────

async def _handle_chat_request(
    request: Request,
    body: dict[str, Any],
    protocol: str,  # "openai" | "anthropic"
):
    """
    统一处理 chat 请求，注入 PromptPlan、session 解析、幂等检查、provenance 记录。
    """
    start_time = time.time()
    messages = body.get("messages", [])
    model = body.get("model", "")
    stream = body.get("stream", False)

    # ── 1. Session ID 提取与 namespace 映射 ─────────────────────────────────
    headers = dict(request.headers)
    session_id, namespace = resolve_session_namespace(body, headers)
    if session_id:
        _ledger.upsert_session(session_id, namespace or "default")
        logger.info("session resolved: id=%s namespace=%s", session_id, namespace)
    else:
        logger.debug("no session_id found in request")

    # ── 2. 消息幂等检查 ────────────────────────────────────────────────────
    if session_id and messages:
        last_msg = messages[-1]
        is_dup, msg_hash = check_idempotency(
            session_id=session_id,
            role=last_msg.get("role", "user"),
            content=last_msg.get("content", ""),
            message_id=body.get("message_id"),
            message_index=body.get("message_index"),
        )
        if is_dup:
            logger.info("duplicate request skipped: session=%s", session_id)
            raise HTTPException(status_code=409, detail="Duplicate request detected")

    # ── 3. PromptPlan 注入 ─────────────────────────────────────────────────
    if _prompt_plan.enabled and messages:
        original_system = [m for m in messages if m.get("role") == "system"]
        messages = _prompt_plan.assemble(messages)
        if messages != original_system + [m for m in messages if m.get("role") != "system"]:
            logger.debug("prompt plan injected: %d system messages", len([m for m in messages if m.get("role") == "system"]))

    # ── 4. 准备 provider ───────────────────────────────────────────────────
    provider_name = body.get("provider") or _current_provider_name
    provider = _get_provider(provider_name)

    logger.info("chat: protocol=%s provider=%s model=%s stream=%s session=%s", protocol, provider_name, model, stream, session_id)

    # ── 5. 请求 hash（用于 provenance）─────────────────────────────────────
    req_body_for_hash = {**body, "messages": messages}
    input_hash = hash_request(req_body_for_hash)
    event_id = str(uuid.uuid4())

    # ── 6. 调用 provider ───────────────────────────────────────────────────
    try:
        if protocol == "anthropic" or provider_name == "anthropic":
            # Anthropic 协议转译
            system_msg = ""
            anthropic_messages = []
            for m in messages:
                if m.get("role") == "system":
                    system_msg = m.get("content", "")
                else:
                    anthropic_messages.append(m)
            result = await provider.messages(
                anthropic_messages,
                model=model,
                stream=stream,
                system=system_msg,
                max_tokens=body.get("max_tokens"),
                temperature=body.get("temperature"),
            )
        else:
            result = await provider.chat_completions(
                messages,
                model=model,
                stream=stream,
                max_tokens=body.get("max_tokens"),
                temperature=body.get("temperature"),
                top_p=body.get("top_p"),
            )
    except httpx.HTTPStatusError as e:
        logger.error("upstream error: %s", e.response.text)
        _record_provenance(event_id, input_hash, None, provider_name, model, session_id, start_time, error=str(e))
        raise HTTPException(status_code=e.response.status_code, detail="Upstream LLM error")
    except Exception as e:
        logger.error("chat completion failed: %s", e, exc_info=True)
        _record_provenance(event_id, input_hash, None, provider_name, model, session_id, start_time, error=str(e))
        raise HTTPException(status_code=500, detail="Internal gateway error")

    # ── 7. 记录 Provenance ─────────────────────────────────────────────────
    latency_ms = int((time.time() - start_time) * 1000)
    result_hash = None
    token_usage = None

    if not stream:
        # 非流式：直接读取 usage
        if protocol == "anthropic" or provider_name == "anthropic":
            result_hash = hash_response(result)
            usage = result.get("usage", {})
            token_usage = {
                "prompt_tokens": usage.get("input_tokens", 0),
                "completion_tokens": usage.get("output_tokens", 0),
                "total_tokens": usage.get("input_tokens", 0) + usage.get("output_tokens", 0),
            }
        else:
            result_hash = hash_response(result)
            usage = result.get("usage", {})
            token_usage = {
                "prompt_tokens": usage.get("prompt_tokens", 0),
                "completion_tokens": usage.get("completion_tokens", 0),
                "total_tokens": usage.get("total_tokens", 0),
            }

        _record_provenance(event_id, input_hash, result_hash, provider_name, model, session_id, start_time, token_usage=token_usage)

        # 记录 conversation_messages
        if session_id:
            for m in messages:
                idx = _next_message_index(session_id)
                _ledger.record_message(
                    session_id=session_id,
                    message_index=idx,
                    role=m.get("role", "unknown"),
                    content_hash=hash_request({"content": m.get("content", "")}),
                )
    else:
        # 流式：延迟记录 provenance（在响应完成后异步记录较复杂，先记录基础信息）
        _record_provenance(event_id, input_hash, None, provider_name, model, session_id, start_time)

    # ── 8. 返回响应 ────────────────────────────────────────────────────────
    if stream:
        return StreamingResponse(result, media_type="text/event-stream")

    if protocol == "anthropic":
        # Anthropic 原生接口必须返回 Anthropic 协议结构
        return JSONResponse(result)

    # OpenAI 兼容接口：如底层调用 Anthropic provider，需转换为 OpenAI 格式
    if provider_name == "anthropic":
        return JSONResponse(_anthropic_to_openai(result, model))
    return JSONResponse(result)
    if stream:
        if protocol == "anthropic" or provider_name == "anthropic":
            return StreamingResponse(result, media_type="text/event-stream")
        return StreamingResponse(result, media_type="text/event-stream")

    if protocol == "anthropic" or provider_name == "anthropic":
        return JSONResponse(_anthropic_to_openai(result, model))
    return JSONResponse(result)


def _record_provenance(
    event_id: str,
    input_hash: str,
    result_hash: str | None,
    provider_name: str,
    model: str | None,
    session_id: str | None,
    start_time: float,
    token_usage: dict[str, int] | None = None,
    error: str | None = None,
):
    """辅助函数：记录 provenance（不阻塞响应）。"""
    latency_ms = int((time.time() - start_time) * 1000)
    meta = {"latency_ms": latency_ms}
    if error:
        meta["error"] = error
    _ledger.record_provenance(
        event_id=event_id,
        tool_name="chat_completions",
        caller_subject=session_id or "anonymous",
        input_hash=input_hash,
        target_kind="llm_provider",
        target_ref=provider_name,
        result_hash=result_hash,
        latency_ms=latency_ms,
        token_usage=token_usage,
        session_id=session_id,
        model=model,
        metadata=meta,
    )


# ─── OpenAI 兼容接口 ───────────────────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    body = await request.json()
    return await _handle_chat_request(request, body, protocol="openai")


# ─── Anthropic 原生接口 ────────────────────────────────────────────────────────
@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    system = body.get("system", "")
    # 将 Anthropic 的 system 字段转换为 OpenAI 风格的 system message
    if system:
        body["messages"] = [{"role": "system", "content": system}] + messages
    return await _handle_chat_request(request, body, protocol="anthropic")


# ─── 响应格式转换 ──────────────────────────────────────────────────────────────

def _anthropic_to_openai(anthropic_resp: dict, model: str) -> dict:
    """将 Anthropic messages 响应转换为 OpenAI chat.completion 格式。"""
    content = ""
    for block in anthropic_resp.get("content", []):
        if block.get("type") == "text":
            content += block.get("text", "")
    return {
        "id": anthropic_resp.get("id", ""),
        "object": "chat.completion",
        "created": anthropic_resp.get("usage", {}).get("created_at", 0),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": content},
            "finish_reason": anthropic_resp.get("stop_reason", "stop"),
        }],
        "usage": {
            "prompt_tokens": anthropic_resp.get("usage", {}).get("input_tokens", 0),
            "completion_tokens": anthropic_resp.get("usage", {}).get("output_tokens", 0),
            "total_tokens": (
                anthropic_resp.get("usage", {}).get("input_tokens", 0)
                + anthropic_resp.get("usage", {}).get("output_tokens", 0)
            ),
        },
    }


def _openai_to_anthropic(openai_resp: dict) -> dict:
    """将 OpenAI chat.completion 响应转换为 Anthropic messages 格式。"""
    choice = openai_resp.get("choices", [{}])[0]
    msg = choice.get("message", {})
    return {
        "id": openai_resp.get("id", ""),
        "type": "message",
        "role": "assistant",
        "model": openai_resp.get("model", ""),
        "content": [{"type": "text", "text": msg.get("content", "")}],
        "stop_reason": choice.get("finish_reason", "end_turn"),
        "usage": {
            "input_tokens": openai_resp.get("usage", {}).get("prompt_tokens", 0),
            "output_tokens": openai_resp.get("usage", {}).get("completion_tokens", 0),
        },
    }


# ─── 主入口 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=config.gateway_host,
        port=config.gateway_port,
        log_level=config.log_level.lower(),
    )
