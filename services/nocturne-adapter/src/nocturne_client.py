"""Nocturne MCP 客户端：通过 streamable HTTP 与上游 Nocturne 通信。"""

from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger("adapter.nocturne_client")


class NocturneClient:
    """
    与上游 Nocturne Memory Core 的 MCP streamable HTTP 通信。
    维护 session id，处理 initialize 生命周期。
    """

    def __init__(self, base_url: str, timeout: float = 15.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session_id: str | None = None
        self._client = httpx.AsyncClient(timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def _post(self, payload: dict, expect_body: bool = True) -> dict | None:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id

        resp = await self._client.post(
            f"{self.base_url}/mcp",
            headers=headers,
            json=payload,
        )
        resp.raise_for_status()

        self.session_id = resp.headers.get("mcp-session-id") or self.session_id

        if not expect_body:
            return None

        text = resp.text
        # 处理 SSE data: 前缀
        for line in text.splitlines():
            if line.startswith("data:"):
                text = line[5:].strip()
                break
        return {} if not text else __import__("json").loads(text)

    async def initialize(self) -> None:
        """MCP initialize handshake。"""
        if self.session_id:
            return
        await self._post({
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "nocturne-adapter", "version": "1.0.0"},
            },
        })
        await self._post({
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
        }, expect_body=False)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict:
        """调用上游 MCP tool。"""
        await self.initialize()
        result = await self._post({
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        })
        # 处理 session 过期重试（400/404）
        if result and isinstance(result, dict) and "error" in result:
            err = result.get("error", {})
            if err.get("code") in (-32600, -32601, -32602):
                self.session_id = None
                await self.initialize()
                result = await self._post({
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                })
        return result or {}

    async def breath(self) -> dict:
        """调用上游零参 breath()。"""
        return await self.call_tool("breath", {})

    async def trace(self, query: str, limit: int = 12) -> dict:
        """调用上游 trace 搜索。"""
        return await self.call_tool("trace", {"query": query, "limit": limit})

    async def hold(
        self,
        content: str,
        kind: str = "memory",
        tags: str = "",
        importance: int = 5,
    ) -> dict:
        """调用上游 hold()。"""
        return await self.call_tool("hold", {
            "content": content,
            "kind": kind,
            "tags": tags,
            "importance": importance,
        })
