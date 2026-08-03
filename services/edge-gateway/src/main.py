"""Edge Gateway 主入口：OpenAI/Anthropic 双协议 + Mock Provider"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

from .config import load_config
from .providers import create_provider, list_providers

# ─── 日志 ──────────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("gateway.main")

# ─── 配置 ──────────────────────────────────────────────────────────────────────
config = load_config()
logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

# ─── Provider 实例（lazy init）──────────────────────────────────────────────────
_providers: dict[str, Any] = {}
_current_provider_name: str = config.default_provider


def _get_provider(name: str | None = None):
    global _providers, _current_provider_name
    name = name or _current_provider_name
    if name not in _providers:
        _providers[name] = create_provider(name, config)
    return _providers[name]


# ─── FastAPI ───────────────────────────────────────────────────────────────────
app = FastAPI(title="Edge Gateway", version="1.0.0")


@app.on_event("shutdown")
async def _shutdown() -> None:
    for p in _providers.values():
        if hasattr(p, "close"):
            await p.close()


# ─── 健康检查 ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "edge-gateway",
        "providers": list_providers(),
        "current_provider": _current_provider_name,
    }


@app.get("/v1/providers")
async def providers() -> dict:
    return {"providers": list_providers(), "current": _current_provider_name}


@app.post("/v1/switch-provider")
async def switch_provider(request: Request) -> dict:
    """
    热切换 provider（仅 mock/openai/anthropic）。
    生产环境应加鉴权。
    """
    global _current_provider_name
    body = await request.json()
    name = body.get("provider", "").lower()
    if name not in list_providers():
        raise HTTPException(status_code=400, detail=f"Unknown provider: {name}")
    _current_provider_name = name
    logger.info("provider switched to: %s", name)
    return {"provider": name, "status": "ok"}


# ─── OpenAI 兼容接口 ───────────────────────────────────────────────────────────
@app.post("/v1/chat/completions")
async def openai_chat_completions(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", "")
    stream = body.get("stream", False)

    provider_name = body.get("provider") or _current_provider_name
    provider = _get_provider(provider_name)

    logger.info("openai_chat: provider=%s model=%s stream=%s", provider_name, model, stream)

    try:
        if provider_name == "anthropic":
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
            if stream:
                return StreamingResponse(result, media_type="text/event-stream")
            # 将 Anthropic 响应包装为 OpenAI 格式
            return JSONResponse(_anthropic_to_openai(result, model))

        result = await provider.chat_completions(
            messages,
            model=model,
            stream=stream,
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
            top_p=body.get("top_p"),
        )
        if stream:
            return StreamingResponse(result, media_type="text/event-stream")
        return JSONResponse(result)
    except httpx.HTTPStatusError as e:
        logger.error("upstream error: %s", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail="Upstream LLM error")
    except Exception as e:
        logger.error("chat completion failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail="Internal gateway error")


# ─── Anthropic 原生接口 ────────────────────────────────────────────────────────
@app.post("/v1/messages")
async def anthropic_messages(request: Request):
    body = await request.json()
    messages = body.get("messages", [])
    model = body.get("model", "")
    stream = body.get("stream", False)
    system = body.get("system", "")

    provider_name = body.get("provider") or _current_provider_name
    if provider_name == "openai":
        # OpenAI → Anthropic 转译
        provider = _get_provider("openai")
        openai_messages = [{"role": "system", "content": system}] if system else []
        openai_messages.extend(messages)
        result = await provider.chat_completions(
            openai_messages,
            model=model,
            stream=stream,
            max_tokens=body.get("max_tokens"),
            temperature=body.get("temperature"),
        )
        if stream:
            return StreamingResponse(result, media_type="text/event-stream")
        return JSONResponse(_openai_to_anthropic(result))

    provider = _get_provider(provider_name)
    result = await provider.messages(
        messages,
        model=model,
        stream=stream,
        system=system,
        max_tokens=body.get("max_tokens"),
        temperature=body.get("temperature"),
    )
    if stream:
        return StreamingResponse(result, media_type="text/event-stream")
    return JSONResponse(result)


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
