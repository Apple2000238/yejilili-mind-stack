"""Continuity Guard — 融合层服务入口

职责：
    - 连续性清单同步（manifest sync）
    - 身份门装配（identity gate assembly）
    - 双向事件桥接（event bridge）
    - Dashboard 只读 API
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException, Depends, Request
from fastapi.responses import JSONResponse
import uvicorn

from .manifest import ManifestLoader, ProtectionSynchronizer
from .identity_gate import IdentityGateLoader, PromptPlanAssembler
from .event_bridge import EventBridge, EventEnvelope, create_envelope, IdempotencyStore, LoopSuppressor
from .dashboard import DashboardService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("continuity-guard")

app = FastAPI(title="Continuity Guard", version="1.0.0")

# ─── 配置 ────────────────────────────────────────────────────────────────────
BUCKETS_DIR = Path(os.environ.get("NOCTURNE_BUCKETS_DIR", "/data/buckets"))
AUDIT_DIR = Path(os.environ.get("GUARD_AUDIT_DIR", "/var/log/guard"))
MANIFEST_PATH = Path(os.environ.get("CONTINUITY_MANIFEST_PATH", "/config/continuity_manifest.json"))
IDENTITY_CONFIG_PATH = Path(os.environ.get("IDENTITY_GATE_CONFIG_PATH", "/config/identity_gate.json"))
POSTGRES_DSN = os.environ.get("POSTGRES_DSN", "")
ADMIN_TOKEN = os.environ.get("ADMIN_TOKEN", "")

AUDIT_DIR.mkdir(parents=True, exist_ok=True)

# ─── 服务实例（懒加载）───────────────────────────────────────────────────────
_dashboard_service: DashboardService | None = None
_event_bridge: EventBridge | None = None


def _get_dashboard() -> DashboardService:
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService(POSTGRES_DSN, ADMIN_TOKEN)
    return _dashboard_service


def _get_event_bridge() -> EventBridge:
    global _event_bridge
    if _event_bridge is None:
        idem = IdempotencyStore(POSTGRES_DSN)
        loop = LoopSuppressor(max_depth=3)
        _event_bridge = EventBridge(idem, loop)
    return _event_bridge


# ─── Health ──────────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    loader = IdentityGateLoader(IDENTITY_CONFIG_PATH)
    ready, msg = loader.readiness()
    status_code = 200 if ready else 503
    return JSONResponse(
        content={"status": "ready" if ready else "not_ready", "detail": msg},
        status_code=status_code,
    )


# ─── Manifest 同步 ───────────────────────────────────────────────────────────

@app.post("/sync/manifest")
async def sync_manifest(request: Request) -> dict:
    """执行连续性清单同步。"""
    auth = request.headers.get("Authorization", "")
    if not _get_dashboard().authenticate(auth.replace("Bearer ", "")):
        raise HTTPException(status_code=401, detail="unauthorized")

    manifest = ManifestLoader(MANIFEST_PATH)
    syncer = ProtectionSynchronizer(BUCKETS_DIR, AUDIT_DIR)
    records = syncer.sync_all(manifest)

    return {
        "synced": len(records),
        "successes": sum(1 for r in records if r.success),
        "failures": sum(1 for r in records if not r.success),
        "records": [r.to_dict() for r in records],
    }


# ─── Identity Gate 装配 ──────────────────────────────────────────────────────

@app.post("/identity/assemble")
async def assemble_identity(messages: list[dict]) -> dict:
    """装配 PromptPlan 到消息列表。"""
    loader = IdentityGateLoader(IDENTITY_CONFIG_PATH)
    config = loader.load()
    assembler = PromptPlanAssembler(config, AUDIT_DIR)
    final_messages, record = assembler.assemble(messages)
    return {
        "messages": final_messages,
        "assembly": record.to_dict(),
    }


@app.get("/identity/readiness")
async def identity_readiness() -> dict:
    """身份门 readiness 检查。"""
    loader = IdentityGateLoader(IDENTITY_CONFIG_PATH)
    ready, msg = loader.readiness()
    return {"ready": ready, "detail": msg}


# ─── 事件桥接 ────────────────────────────────────────────────────────────────

@app.post("/bridge/nocturne-to-xinchao")
async def bridge_n2x(envelope: dict) -> dict:
    """接收 Nocturne 事件，转换后输出心潮格式。"""
    try:
        ev = EventEnvelope.from_dict(envelope)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {e}")

    bridge = _get_event_bridge()
    result = await bridge.process_nocturne_event(ev)
    return {"processed": result is not None, "result": result}


@app.post("/bridge/xinchao-to-nocturne")
async def bridge_x2n(envelope: dict) -> dict:
    """接收心潮事件，转换后输出 Nocturne 格式。"""
    try:
        ev = EventEnvelope.from_dict(envelope)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {e}")

    bridge = _get_event_bridge()
    result = await bridge.process_xinchao_event(ev)
    return {"processed": result is not None, "result": result}


# ─── Dashboard API（只读）────────────────────────────────────────────────────

async def _dashboard_auth(request: Request) -> bool:
    auth = request.headers.get("Authorization", "")
    return _get_dashboard().authenticate(auth.replace("Bearer ", ""))


@app.get("/dashboard/breath")
async def dashboard_breath(query: str = "", request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"results": _get_dashboard().get_breath_summary(query)}


@app.get("/dashboard/dimensions")
async def dashboard_dimensions(request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"dimensions": _get_dashboard().get_dimensions()}


@app.get("/dashboard/thoughts")
async def dashboard_thoughts(limit: int = 10, request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"thoughts": _get_dashboard().get_recent_thoughts_meta(limit)}


@app.get("/dashboard/migrations")
async def dashboard_migrations(request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"migrations": _get_dashboard().get_migration_status()}


@app.get("/dashboard/bridge-health")
async def dashboard_bridge(request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return {"bridges": _get_dashboard().get_bridge_health()}


@app.get("/dashboard/system-health")
async def dashboard_system(request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return _get_dashboard().get_system_health()


@app.get("/dashboard/continuity-manifest")
async def dashboard_manifest(request: Request = None) -> dict:
    if request and not await _dashboard_auth(request):
        raise HTTPException(status_code=401, detail="unauthorized")
    return _get_dashboard().get_continuity_manifest_summary()


# ─── 启动 ────────────────────────────────────────────────────────────────────

def main() -> None:
    port = int(os.environ.get("GUARD_PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
