"""AR Sidecar Connector — AfterRain 网关与侧车之间的唯一接线边界

职责：
    - 接收 AfterRain 网关数据（只读代理，不修改 SQLite）
    - 转发到 continuity-guard 身份门（/identity/assemble）
    - 转发到 continuity-guard 事件桥接（/bridge/*）
    - 转发到 edge-gateway（/v1/chat/completions）
    - 所有写入路由必须鉴权
    - 使用隔离夹具和合成数据自测

安全约束：
    - 禁止连接生产 VPS
    - 禁止读取/修改生产 SQLite
    - 只使用合成数据自测
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("ar-sidecar-connector")

app = FastAPI(title="AR Sidecar Connector", version="1.0.0")

# ─── 配置 ────────────────────────────────────────────────────────────────────
IDENTITY_GATE_URL = os.environ.get("IDENTITY_GATE_URL", "http://continuity-guard:8003")
EDGE_GATEWAY_URL = os.environ.get("EDGE_GATEWAY_URL", "http://edge-gateway:8002")
BRIDGE_URL = os.environ.get("BRIDGE_URL", "http://continuity-guard:8003")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")
CONNECTOR_TOKEN = os.environ.get("CONNECTOR_TOKEN", "")

# ─── 鉴权 helpers ─────────────────────────────────────────────────────────────

def _check_auth(request: Request, expected_token: str) -> bool:
    """Bearer token 鉴权，使用 constant-time comparison。"""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return False
    token = auth[7:].strip()
    if not token or not expected_token:
        return False
    return hmac.compare_digest(token.encode(), expected_token.encode())


def _require_auth(request: Request) -> None:
    if not _check_auth(request, ADMIN_TOKEN) and not _check_auth(request, CONNECTOR_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    """健康检查：验证下游依赖可用性。"""
    components: dict[str, dict[str, Any]] = {}
    overall_ready = True

    # 1. Identity Gate
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{IDENTITY_GATE_URL}/identity/readiness")
            components["identity_gate"] = {"ready": r.status_code == 200, "detail": r.json()}
            if r.status_code != 200:
                overall_ready = False
    except Exception as e:
        components["identity_gate"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    # 2. Edge Gateway
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(f"{EDGE_GATEWAY_URL}/health")
            components["edge_gateway"] = {"ready": r.status_code == 200, "detail": r.json()}
            if r.status_code != 200:
                overall_ready = False
    except Exception as e:
        components["edge_gateway"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    status_code = 200 if overall_ready else 503
    return JSONResponse(
        content={
            "status": "ready" if overall_ready else "not_ready",
            "service": "ar-sidecar-connector",
            "components": components,
        },
        status_code=status_code,
    )


# ─── Identity Gate 转发 ──────────────────────────────────────────────────────

@app.post("/forward/identity/assemble")
async def forward_identity_assemble(request: Request) -> dict:
    """转发消息到 identity gate 进行五段 PromptPlan 装配。

    AfterRain 网关调用此接口，将 messages 列表传递给 continuity-guard 的
    /identity/assemble，获取装配后的五段 PromptPlan。
    """
    _require_auth(request)

    body = await request.json()
    messages = body.get("messages", [])

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Content-Type": "application/json"}
            if ADMIN_TOKEN:
                headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
            r = await client.post(
                f"{IDENTITY_GATE_URL}/identity/assemble",
                json=messages,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.error("identity gate error: %s", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail="Identity gate error")
    except Exception as e:
        logger.error("forward identity assemble failed: %s", e)
        raise HTTPException(status_code=502, detail="Identity gate unavailable")


# ─── Edge Gateway 转发 ───────────────────────────────────────────────────────

@app.post("/forward/edge/chat")
async def forward_edge_chat(request: Request) -> dict:
    """转发 chat 请求到 edge-gateway。

    AfterRain 网关调用此接口，将 OpenAI 兼容格式的 chat completion 请求
    转发到 edge-gateway，由 edge-gateway 注入 PromptPlan 后调用上游 LLM。
    """
    _require_auth(request)

    body = await request.json()

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            headers = {"Content-Type": "application/json"}
            if ADMIN_TOKEN:
                headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
            r = await client.post(
                f"{EDGE_GATEWAY_URL}/v1/chat/completions",
                json=body,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        logger.error("edge gateway error: %s", e.response.text)
        raise HTTPException(status_code=e.response.status_code, detail="Edge gateway error")
    except Exception as e:
        logger.error("forward edge chat failed: %s", e)
        raise HTTPException(status_code=502, detail="Edge gateway unavailable")


# ─── Event Bridge 转发 ───────────────────────────────────────────────────────

@app.post("/forward/bridge/nocturne-to-xinchao")
async def forward_bridge_n2x(request: Request) -> dict:
    """转发 Nocturne → 心潮 事件到 continuity-guard 事件桥接。"""
    _require_auth(request)

    body = await request.json()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Content-Type": "application/json"}
            if ADMIN_TOKEN:
                headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
            r = await client.post(
                f"{BRIDGE_URL}/bridge/nocturne-to-xinchao",
                json=body,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error("forward bridge n2x failed: %s", e)
        raise HTTPException(status_code=502, detail="Bridge unavailable")


@app.post("/forward/bridge/xinchao-to-nocturne")
async def forward_bridge_x2n(request: Request) -> dict:
    """转发 心潮 → Nocturne 事件到 continuity-guard 事件桥接。"""
    _require_auth(request)

    body = await request.json()

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            headers = {"Content-Type": "application/json"}
            if ADMIN_TOKEN:
                headers["Authorization"] = f"Bearer {ADMIN_TOKEN}"
            r = await client.post(
                f"{BRIDGE_URL}/bridge/xinchao-to-nocturne",
                json=body,
                headers=headers,
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error("forward bridge x2n failed: %s", e)
        raise HTTPException(status_code=502, detail="Bridge unavailable")


# ─── 隔离夹具端点（合成数据自测）─────────────────────────────────────────────

@app.post("/fixture/synthetic-data")
async def fixture_synthetic_data(request: Request) -> dict:
    """返回合成数据夹具，用于自测和集成测试。

    不连接任何真实数据库，只返回预定义的结构化合成数据。
    """
    _require_auth(request)

    return {
        "persona": [{"name": "梨梨", "role": "user", "content": "identity bedrock"}],
        "memory_layers": [{"layer_type": "core", "layer_key": "identity", "content": "bedrock", "protected": True}],
        "ar_buckets": [{"name": "core memory", "type": "memory", "anchor": True}],
        "message_archive": [{"role": "user", "content": "hello", "session_id": "test-session"}],
        "daily_summaries": [{"summary_text": "today was good"}],
        "promises": [{"promise_text": "be kind", "status": "active"}],
        "ar_dreams": [{"content": "a dream"}],
    }


# ─── 启动 ────────────────────────────────────────────────────────────────────

def main() -> None:
    import uvicorn
    port = int(os.environ.get("CONNECTOR_PORT", "8004"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")  # nosec: B104 - Docker container default bind


if __name__ == "__main__":
    main()
