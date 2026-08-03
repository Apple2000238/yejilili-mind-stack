"""Contract tests: MCP adapter schema, routing, and idempotency."""

import pytest
import httpx

ADAPTER_URL = "http://localhost:8001"
MCP_TOKEN = "test-token"


@pytest.fixture
def client():
    return httpx.Client(base_url=ADAPTER_URL, headers={"Authorization": f"Bearer {MCP_TOKEN}"})


def test_mcp_initialize(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 200
    body = r.json()
    assert body["jsonrpc"] == "2.0"
    assert "result" in body


def test_mcp_tools_list(client):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert r.status_code == 200
    tools = r.json()["result"]["tools"]
    names = {t["name"] for t in tools}
    assert "breath" in names
    assert "hold" in names


def test_breath_validation_error_on_oversized_max_results(client):
    r = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 3,
        "method": "tools/call",
        "params": {
            "name": "breath",
            "arguments": {"max_results": 99}
        }
    })
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == -32602


def test_hold_unknown_field_rejected(client):
    r = client.post("/mcp", json={
        "jsonrpc": "2.0", "id": 4,
        "method": "tools/call",
        "params": {
            "name": "hold",
            "arguments": {
                "content": "test",
                "evil_field": "injection"
            }
        }
    })
    assert r.status_code == 200
    body = r.json()
    assert "error" in body
    assert body["error"]["code"] == -32602
