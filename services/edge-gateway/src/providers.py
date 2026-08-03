"""LLM Provider 适配层：OpenAI / Anthropic / Mock"""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator

import httpx

from .config import Config

logger = logging.getLogger("gateway.providers")


# ─── Mock Provider（确定性输出，用于测试）──────────────────────────────

class MockProvider:
    """
    确定性 mock provider。
    不调用外部 API，返回结构化占位响应。
    """

    def __init__(self):
        self.call_count = 0

    async def chat_completions(self, messages: list[dict], model: str, **kwargs) -> dict:
        self.call_count += 1
        prompt = ""
        for m in messages:
            prompt += m.get("content", "")
        # 生成确定性 hash 作为响应内容
        import hashlib
        content_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        return {
            "id": f"mock-{self.call_count}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model or "mock-model",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": f"[mock] echo hash: {content_hash}",
                },
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": len(prompt), "completion_tokens": 10, "total_tokens": len(prompt) + 10},
        }

    async def chat_completions_stream(
        self, messages: list[dict], model: str, **kwargs
    ) -> AsyncIterator[str]:
        self.call_count += 1
        prompt = ""
        for m in messages:
            prompt += m.get("content", "")
        import hashlib
        content_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]
        content = f"[mock] echo hash: {content_hash}"

        yield f"data: {json.dumps({'id': f'mock-{self.call_count}', 'object': 'chat.completion.chunk', 'created': int(time.time()), 'model': model or 'mock-model', 'choices': [{'index': 0, 'delta': {'role': 'assistant'}, 'finish_reason': None}]})}\n\n"
        for word in content.split():
            yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {'content': word + ' '}, 'finish_reason': None}]})}\n\n"
        yield f"data: {json.dumps({'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]})}\n\n"
        yield "data: [DONE]\n\n"


# ─── OpenAI Provider ─────────────────────────────────────────────────

class OpenAIProvider:
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.openai_base_url,
            headers={"Authorization": f"Bearer {config.openai_api_key}"},
            timeout=60.0,
        )

    async def chat_completions(self, messages: list[dict], model: str, stream: bool = False, **kwargs) -> dict | AsyncIterator[str]:
        payload = {
            "model": model or self.config.openai_model,
            "messages": messages,
            "stream": stream,
        }
        for k in ["temperature", "max_tokens", "top_p"]:
            if k in kwargs:
                payload[k] = kwargs[k]

        if stream:
            return self._stream(payload)

        resp = await self.client.post("/chat/completions", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _stream(self, payload: dict) -> AsyncIterator[str]:
        async with self.client.stream("POST", "/chat/completions", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield line + "\n"

    async def close(self):
        await self.client.aclose()


# ─── Anthropic Provider ──────────────────────────────────────────────

class AnthropicProvider:
    def __init__(self, config: Config):
        self.config = config
        self.client = httpx.AsyncClient(
            base_url=config.anthropic_base_url,
            headers={
                "x-api-key": config.anthropic_api_key,
                "anthropic-version": "2023-06-01",
            },
            timeout=60.0,
        )

    async def messages(self, messages: list[dict], model: str, stream: bool = False, system: str = "", **kwargs) -> dict | AsyncIterator[str]:
        payload = {
            "model": model or self.config.anthropic_model,
            "messages": messages,
            "stream": stream,
            "max_tokens": kwargs.get("max_tokens", 4096),
        }
        if system:
            payload["system"] = system
        for k in ["temperature", "top_p"]:
            if k in kwargs:
                payload[k] = kwargs[k]

        if stream:
            return self._stream(payload)

        resp = await self.client.post("/v1/messages", json=payload)
        resp.raise_for_status()
        return resp.json()

    async def _stream(self, payload: dict) -> AsyncIterator[str]:
        async with self.client.stream("POST", "/v1/messages", json=payload) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if line:
                    yield line + "\n"

    async def close(self):
        await self.client.aclose()


# ─── Provider 工厂 ───────────────────────────────────────────────────

_PROVIDER_REGISTRY: dict[str, type] = {
    "mock": MockProvider,
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
}


def create_provider(name: str, config: Config):
    cls = _PROVIDER_REGISTRY.get(name)
    if not cls:
        raise ValueError(f"Unknown provider: {name}")
    return cls(config)


def list_providers() -> list[str]:
    return list(_PROVIDER_REGISTRY.keys())
