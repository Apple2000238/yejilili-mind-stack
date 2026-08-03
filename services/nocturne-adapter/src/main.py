"""FastAPI 主入口：MCP streamable HTTP 服务 + 健康检查。"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request
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


# ─── 健康检查 ──────────────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "service": "nocturne-adapter", "upstream_commit": config.upstream_commit}


# ─── MCP Streamable HTTP 入口 ──────────────────────────────────────────────────
@app.post("/mcp")
async def mcp_endpoint(request: Request) -> JSONResponse:
    """
    MCP streamable HTTP 入口。
    支持 initialize、tools/list、tools/call。
    """
    # 鉴权
    caller = require_auth(request, config.mcp_adapter_token)

    body = await request.json()
    method = body.get("method", "")
    params = body.get("params", {})
    req_id = body.get("id")

    logger.info("mcp call: method=%s caller=%s", method, caller.subject)

    # ── initialize ───────────────────────────────────────────────────────────
    if method == "initialize":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "serverInfo": {"name": "nocturne-adapter", "version": "1.0.0"},
            },
        })

    # ── notifications/initialized ────────────────────────────────────────────
    if method == "notifications/initialized":
        return JSONResponse({})

    # ── tools/list ───────────────────────────────────────────────────────────
    if method == "tools/list":
        return JSONResponse({
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "breath",
                        "description": "浮现未解决记忆或按关键词检索",
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "query": {"type": "string", "description": "搜索关键词（可选）"},
                                "max_results": {"type": "integer", "default": 12, "maximum": 20},
                                "max_tokens": {"type": "integer", "default": 2200, "maximum": 4000},
                            },
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
                                "importance": {"type": "integer", "default": 5},
                                "auto": {"type": "boolean", "description": "是否为自动写入"},
                                "source": {"type": "string", "description": "来源标识，如 xinchao-dream", "maxLength": 64},
                            },
                            "required": ["content"],
                        },
                    },
                ],
            },
        })

    # ── tools/call ───────────────────────────────────────────────────────────
    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        try:
            if tool_name == "breath":
                result = await bridge.handle_breath(
                    caller=caller,
                    query=args.get("query"),
                    max_results=args.get("max_results"),
                    max_tokens=args.get("max_tokens"),
                )
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": result["content"],
                        "metadata": result.get("metadata", {}),
                    },
                })

            elif tool_name == "hold":
                result = await bridge.handle_hold(
                    caller=caller,
                    content=args.get("content", ""),
                    tags=args.get("tags", ""),
                    importance=args.get("importance", 5),
                    auto=args.get("auto"),
                    source=args.get("source"),
                )
                return JSONResponse({
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "result": {
                        "content": result["content"],
                        "metadata": result.get("metadata", {}),
                    },
                })

            else:
                raise HTTPException(status_code=400, detail=f"Unknown tool: {tool_name}")

        except ValueError as e:
            logger.warning("validation error: %s", e)
            raise HTTPException(status_code=422, detail=str(e))
        except Exception as e:
            logger.error("tool call failed: %s", e, exc_info=True)
            raise HTTPException(status_code=500, detail="Internal adapter error")

    # ── 未知方法 ─────────────────────────────────────────────────────────────
    raise HTTPException(status_code=400, detail=f"Unknown method: {method}")


# ─── 主入口 ────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "src.main:app",
        host=config.adapter_host,
        port=config.adapter_port,
        log_level=config.log_level.lower(),
    )
