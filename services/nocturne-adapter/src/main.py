"""FastAPI 主入口：MCP streamable HTTP 服务 + 健康检查。

错误处理：
- ValueError（参数校验失败）→ JSON-RPC error -32602（Invalid params）
- 其他 Exception → JSON-RPC error -32603（Internal error）
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .auth import require_auth
from .config import load_config
from .mcp_bridge import MCPBridge
from .nocturne_client import NocturneClient
from .provenance import ProvenanceStore

# ─── 结构化日志 ────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("adapter.main")

# ─── 加载配置 ──────────────────────────────────────────────────────────────────
config = load_config()
logger.setLevel(getattr(logging, config.log_level.upper(), logging.INFO))

# ─── 初始化组件 ────────────────────────────────────────────────────────────────
nocturne_client = NocturneClient(config.nocturne_url)

DSN = f"postgresql://{config.postgres_user}:{config.postgres_password}@{config.postgres_host}:{config.postgres_port}/{config.postgres_db}"
provenance_store = ProvenanceStore(DSN)

bridge = MCPBridge(config, nocturne_client, provenance_store)

# ─── FastAPI 应用 ──────────────────────────────────────────────────────────────
app = FastAPI(title="Nocturne Adapter", version="1.0.0")


@app.on_event("shutdown")
async def _shutdown() -> None:
    await nocturne_client.close()


# ─── 健康检查（含上游和账本验证）────────────────────────────────────────────────
@app.get("/health")
async def health() -> JSONResponse:
    """
    健康检查：验证自身、上游 Nocturne 和 Postgres 账本。
    """
    checks: dict[str, dict] = {
        "adapter": {"status": "ok"},
    }
    status_code = 200

    # 检查上游 Nocturne
    try:
        await nocturne_client.initialize()
        checks["nocturne"] = {"status": "ok", "url": config.nocturne_url}
    except Exception as e:
        checks["nocturne"] = {"status": "error", "detail": str(e)[:200]}
        status_code = 503

    # 检查 Postgres
    try:
        provenance_store.ping()
        checks["continuity_ledger"] = {"status": "ok"}
    except Exception as e:
        checks["continuity_ledger"] = {"status": "error", "detail": str(e)[:200]}
        status_code = 503

    overall = "ok" if status_code == 200 else "degraded"
    return JSONResponse(
        {
            "status": overall,
            "service": "nocturne-adapter",
            "upstream_commit": config.upstream_commit,
            "checks": checks,
        },
        status_code=status_code,
    )


# ─── MCP Streamable HTTP 入口 ──────────────────────────────────────────────────
@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    MCP streamable HTTP 入口。
    支持 initialize、tools/list、tools/call。
    """
    caller = require_auth(request, config.mcp_adapter_token)

    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    logger.info("mcp call: method=%s caller=%s", method, caller.subject)

    # ── initialize ───────────────────────────────────────────────────────────
    if method == "initialize":
        return _success(req_id, {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "serverInfo": {"name": "nocturne-adapter", "version": "1.0.0"},
        })

    # ── notifications/initialized ────────────────────────────────────────────
    if method == "notifications/initialized":
        return JSONResponse({})

    # ── tools/list ───────────────────────────────────────────────────────────
    if method == "tools/list":
        return _success(req_id, {
            "tools": [
                {
                    "name": "breath",
                    "description": "浮现未解决记忆或按关键词检索",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "query": {"type": "string", "description": "搜索关键词（可选）"},
                            "max_results": {"type": "integer", "minimum": 1, "maximum": 20},
                            "max_tokens": {"type": "integer", "minimum": 100, "maximum": 4000},
                        },
                        "additionalProperties": False,
                    },
                },
                {
                    "name": "hold",
                    "description": "存储记忆/感受/写作/悬置/窗口",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "content": {"type": "string"},
                            "tags": {"type": "string", "default": ""},
                            "importance": {"type": "integer", "minimum": 1, "maximum": 10, "default": 5},
                            "auto": {"type": "boolean", "description": "是否为自动写入"},
                            "source": {
                                "type": "string",
                                "description": "来源标识",
                                "enum": [
                                    "xinchao-dream",
                                    "xinchao-handoff",
                                    "xinchao-thought",
                                    "xinchao-heartbeat",
                                    "edge-gateway",
                                    "migration-cli",
                                ],
                            },
                        },
                        "required": ["content"],
                        "additionalProperties": False,
                    },
                },
            ],
        })

    # ── tools/call ───────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        args: dict = params.get("arguments", {})

        # 未知字段拒绝：根据工具 schema 检查
        if tool_name == "breath":
            allowed = {"query", "max_results", "max_tokens"}
            unknown = set(args.keys()) - allowed
            if unknown:
                return _error(req_id, -32602, f"Unknown fields for breath: {', '.join(sorted(unknown))}")

            try:
                result = await bridge.handle_breath(
                    caller=caller,
                    query=args.get("query"),
                    max_results=args.get("max_results"),
                    max_tokens=args.get("max_tokens"),
                )
                return _success(req_id, {
                    "content": result["content"],
                    "metadata": result.get("metadata", {}),
                })
            except ValueError as e:
                logger.warning("validation error: %s", e)
                return _error(req_id, -32602, str(e))
            except Exception as e:
                logger.error("tool call failed: %s", e, exc_info=True)
                return _error(req_id, -32603, "Internal adapter error")

        elif tool_name == "hold":
            allowed = {"content", "tags", "importance", "auto", "source"}
            unknown = set(args.keys()) - allowed
            if unknown:
                return _error(req_id, -32602, f"Unknown fields for hold: {', '.join(sorted(unknown))}")

            try:
                result = await bridge.handle_hold(
                    caller=caller,
                    content=args.get("content"),
                    tags=args.get("tags"),
                    importance=args.get("importance"),
                    auto=args.get("auto"),
                    source=args.get("source"),
                )
                return _success(req_id, {
                    "content": result["content"],
                    "metadata": result.get("metadata", {}),
                })
            except ValueError as e:
                logger.warning("validation error: %s", e)
                return _error(req_id, -32602, str(e))
            except Exception as e:
                logger.error("tool call failed: %s", e, exc_info=True)
                return _error(req_id, -32603, "Internal adapter error")

        else:
            return _error(req_id, -32601, f"Unknown tool: {tool_name}")

    # ── 未知方法 ─────────────────────────────────────────────────────────────
    return _error(req_id, -32601, f"Unknown method: {method}")


# ─── JSON-RPC 辅助函数 ─────────────────────────────────────────────────────────

def _success(req_id: Any, result: dict) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "result": result,
    })


def _error(req_id: Any, code: int, message: str) -> JSONResponse:
    return JSONResponse({
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": code, "message": message},
    })


# ─── 主入口 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=config.adapter_host,
        port=config.adapter_port,
        log_level=config.log_level.lower(),
    )
