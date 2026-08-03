"""Contract tests: MCP adapter endpoints using FastAPI TestClient.

这些测试在本地直接运行，无需真实 Nocturne 或 Postgres 服务。
使用 unittest.mock 在模块导入前 patch 外部依赖。

运行方式:
    pytest tests/contract/test_mcp_contract.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

# 将 services/nocturne-adapter 加入路径
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "nocturne-adapter"))

import pytest
from fastapi.testclient import TestClient

# 在导入 main 之前 patch 外部依赖
_MockNocturneClient = MagicMock()
_MockNocturneClient.return_value.initialize = AsyncMock()
_MockNocturneClient.return_value.close = AsyncMock()
_MockNocturneClient.return_value.trace = AsyncMock(return_value={
    "result": {"content": [{"type": "text", "text": "trace result"}]}
})
_MockNocturneClient.return_value.breath = AsyncMock(return_value={
    "result": {"content": [{"type": "text", "text": "breath result"}]}
})
_MockNocturneClient.return_value.hold = AsyncMock(return_value={
    "result": {"content": [{"type": "text", "text": "hold result", "metadata": {"bucket_id": "bucket-abc123"}}]}
})

_MockProvenanceStore = MagicMock()
_MockProvenanceStore.return_value.ping = MagicMock()
_MockProvenanceStore.return_value.record = MagicMock(return_value="prov-123")
_MockProvenanceStore.return_value.check_idempotency = MagicMock(return_value=None)


@pytest.fixture
def client():
    """
    提供配置好 Bearer token 的 TestClient。
    在 lifespan 中 patch 外部依赖，无需真实服务。
    """
    import os
    # 设置测试环境变量
    os.environ["MCP_ADAPTER_TOKEN"] = "test-token"
    os.environ["POSTGRES_PASSWORD"] = "test-password"
    os.environ["MCP_ADAPTER_TOKEN_FILE"] = ""
    os.environ["POSTGRES_PASSWORD_FILE"] = ""
    os.environ["POSTGRES_HOST"] = "localhost"

    # patch 外部依赖类，使 lifespan 中的初始化使用 mock
    with patch("src.main.NocturneClient", _MockNocturneClient), \
         patch("src.main.ProvenanceStore", _MockProvenanceStore):
        # 强制重新加载 main 模块以应用 patch
        if "src.main" in sys.modules:
            del sys.modules["src.main"]
        from src import main as adapter_main

        # 设置 config token（因为 config 在模块导入时已加载）
        adapter_main.config = type(adapter_main.config)(
            adapter_port=8001,
            adapter_host="0.0.0.0",
            nocturne_url="http://nocturne:8000",
            mcp_adapter_token="test-token",
            postgres_host="localhost",
            postgres_port=5432,
            postgres_db="test",
            postgres_user="test",
            postgres_password="test",
            log_level="INFO",
        )

        with TestClient(adapter_main.app) as tc:
            yield tc


# ─── Auth ─────────────────────────────────────────────────────────────────────

class TestAuth:
    def test_missing_auth(self, client: TestClient):
        r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
        assert r.status_code == 401

    def test_invalid_token(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert r.status_code == 403


# ─── Health ───────────────────────────────────────────────────────────────────

class TestHealth:
    def test_health_endpoint(self, client: TestClient):
        r = client.get("/health")
        assert r.status_code == 200
        body = r.json()
        assert body["status"] in ("ok", "degraded")
        assert body["service"] == "nocturne-adapter"
        assert "checks" in body


# ─── MCP Initialize ───────────────────────────────────────────────────────────

class TestMCPInitialize:
    def test_initialize(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["jsonrpc"] == "2.0"
        assert "result" in body
        assert body["result"]["protocolVersion"] == "2025-06-18"
        assert body["result"]["serverInfo"]["name"] == "nocturne-adapter"

    def test_notifications_initialized(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "method": "notifications/initialized"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        assert r.json() == {}


# ─── MCP Tools List ───────────────────────────────────────────────────────────

class TestMCPToolsList:
    def test_tools_list(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        tools = body["result"]["tools"]
        names = {t["name"] for t in tools}
        assert "breath" in names
        assert "hold" in names

    def test_breath_schema(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={"Authorization": "Bearer test-token"},
        )
        tools = r.json()["result"]["tools"]
        breath = next(t for t in tools if t["name"] == "breath")
        schema = breath["inputSchema"]
        assert schema["type"] == "object"
        assert "query" in schema["properties"]
        assert schema["additionalProperties"] is False

    def test_hold_schema(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            headers={"Authorization": "Bearer test-token"},
        )
        tools = r.json()["result"]["tools"]
        hold = next(t for t in tools if t["name"] == "hold")
        schema = hold["inputSchema"]
        assert schema["type"] == "object"
        assert "content" in schema["properties"]
        assert "auto" in schema["properties"]
        assert "source" in schema["properties"]
        assert schema["additionalProperties"] is False
        assert "content" in schema["required"]


# ─── Breath Tool Call ─────────────────────────────────────────────────────────

class TestBreathToolCall:
    def test_breath_empty_query(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "breath",
                    "arguments": {},
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        meta = body["result"]["metadata"]
        assert meta["route"] == "breath"
        assert meta["query_honored"] is False

    def test_breath_with_query(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "breath",
                    "arguments": {"query": "memory", "max_results": 5},
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        meta = body["result"]["metadata"]
        assert meta["route"] == "trace"
        assert meta["query_honored"] is True

    def test_breath_validation_error_on_oversized_max_results(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "breath",
                    "arguments": {"max_results": 99},
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32602
        assert "max_results" in body["error"]["message"]

    def test_breath_unknown_field_rejected(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {
                    "name": "breath",
                    "arguments": {"evil_field": "injection"},
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32602


# ─── Hold Tool Call ───────────────────────────────────────────────────────────

class TestHoldToolCall:
    def test_hold_basic(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hold",
                    "arguments": {"content": "test memory"},
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        meta = body["result"]["metadata"]
        assert "target_ref" in meta
        assert meta["tags"] is not None

    def test_hold_with_source(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hold",
                    "arguments": {
                        "content": "test",
                        "source": "xinchao-dream",
                        "auto": True,
                    },
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "result" in body
        meta = body["result"]["metadata"]
        assert meta.get("source") == "xinchao-dream"

    def test_hold_unknown_field_rejected(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hold",
                    "arguments": {
                        "content": "test",
                        "evil_field": "injection",
                    },
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32602

    def test_hold_content_required(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hold",
                    "arguments": {},
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32602
        assert "content" in body["error"]["message"].lower()

    def test_hold_source_not_in_allowlist(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hold",
                    "arguments": {
                        "content": "test",
                        "source": "evil-source",
                    },
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32602
        assert "allowlist" in body["error"]["message"]

    def test_hold_auto_must_be_boolean(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "hold",
                    "arguments": {
                        "content": "test",
                        "auto": 1,
                    },
                },
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32602
        assert "boolean" in body["error"]["message"].lower()


# ─── Unknown Method / Tool ────────────────────────────────────────────────────

class TestUnknownMethod:
    def test_unknown_method(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 5, "method": "unknown/method"},
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32601

    def test_unknown_tool(self, client: TestClient):
        r = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "nonexistent_tool", "arguments": {}},
            },
            headers={"Authorization": "Bearer test-token"},
        )
        assert r.status_code == 200
        body = r.json()
        assert "error" in body
        assert body["error"]["code"] == -32601
        assert "Unknown tool" in body["error"]["message"]
