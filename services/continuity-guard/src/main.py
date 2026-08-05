"""Continuity Guard — 融合层服务入口

职责：
    - 连续性清单同步（manifest sync）
    - 身份门装配（identity gate assembly）
    - 双向事件桥接（event bridge）
    - Dashboard 只读 API

安全约束：
    - 所有写入路由必须鉴权
    - 使用统一 secret loader（_FILE 后缀读取文件）
    - 不在日志或响应中输出 secret
    - readiness 检查所有关键依赖
"""

from __future__ import annotations

import hashlib
import hmac
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
from .event_bridge import (
    EventBridge, EventEnvelope, create_envelope,
    PersistentEventStore, LoopSuppressor,
)
from .dashboard import DashboardService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("continuity-guard")

app = FastAPI(title="Continuity Guard", version="1.0.0")

# ─── 统一 Secret Loader ───────────────────────────────────────────────────────

def _load_secret_file(env_var: str, default_path: str = "") -> str:
    """从环境变量指定的文件路径读取 secret。

    支持两种格式：
    - ADMIN_TOKEN_FILE=/run/secrets/admin_token  → 读取文件内容
    - ADMIN_TOKEN=plaintext  → 直接使用（不推荐，仅用于开发）
    """
    # 优先读取 _FILE 后缀的环境变量
    file_env = os.environ.get(f"{env_var}_FILE", "")
    if file_env and Path(file_env).exists():
        content = Path(file_env).read_text().strip()
        if content:
            return content

    # 回退到普通环境变量（开发/测试场景）
    plain = os.environ.get(env_var, "")
    if plain:
        return plain

    # 最后回退到默认路径
    if default_path and Path(default_path).exists():
        content = Path(default_path).read_text().strip()
        if content:
            return content

    return ""


def _build_dsn(
    host: str, port: str, db: str, user: str, password: str,
) -> str:
    """构建 PostgreSQL DSN，不在日志中输出密码。"""
    return f"postgresql://{user}:{password}@{host}:{port}/{db}"


# ─── 配置 ────────────────────────────────────────────────────────────────────
BUCKETS_DIR = Path(os.environ.get("NOCTURNE_BUCKETS_DIR", "/data/buckets"))
AUDIT_DIR = Path(os.environ.get("GUARD_AUDIT_DIR", "/var/log/guard"))
MANIFEST_PATH = Path(os.environ.get("CONTINUITY_MANIFEST_PATH", "/config/continuity_manifest.json"))
IDENTITY_CONFIG_PATH = Path(os.environ.get("IDENTITY_GATE_CONFIG_PATH", "/config/identity_gate.json"))

POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "continuity-ledger")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "continuity_ledger")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "continuity")
POSTGRES_PASSWORD = _load_secret_file("POSTGRES_PASSWORD", "/run/secrets/postgres_password")
POSTGRES_DSN = _build_dsn(POSTGRES_HOST, POSTGRES_PORT, POSTGRES_DB, POSTGRES_USER, POSTGRES_PASSWORD)

ADMIN_TOKEN = _load_secret_file("ADMIN_TOKEN", "/run/secrets/admin_token")
# Dashboard 使用独立 scope 的 token（可与 admin token 相同或不同）
DASHBOARD_TOKEN = _load_secret_file("DASHBOARD_TOKEN", "/run/secrets/admin_token")

AUDIT_DIR.mkdir(parents=True, exist_ok=True)

# ─── 服务实例（懒加载）───────────────────────────────────────────────────────
_dashboard_service: DashboardService | None = None
_event_bridge: EventBridge | None = None


def _get_dashboard() -> DashboardService:
    global _dashboard_service
    if _dashboard_service is None:
        _dashboard_service = DashboardService(POSTGRES_DSN, DASHBOARD_TOKEN)
    return _dashboard_service


def _get_event_bridge() -> EventBridge:
    global _event_bridge
    if _event_bridge is None:
        store = PersistentEventStore(POSTGRES_DSN, claim_timeout_seconds=300)
        loop = LoopSuppressor(POSTGRES_DSN, max_depth=3)
        _event_bridge = EventBridge(
            store, loop,
            nocturne_adapter=_make_nocturne_adapter(),
            xinchao_adapter=_make_xinchao_adapter(),
        )
    return _event_bridge


def _make_nocturne_adapter():
    """创建 Nocturne 目标系统 async adapter。"""
    import httpx
    endpoint = os.environ.get("NOCTURNE_ADAPTER_ENDPOINT", "http://nocturne-adapter:8001")
    token = _load_secret_file("NOCTURNE_API_TOKEN", "")
    timeout = float(os.environ.get("ADAPTER_TIMEOUT_SECONDS", "10"))

    async def _adapter(payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = await client.post(
                f"{endpoint}/api/v1/hold",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            return resp.json()
    return _adapter


def _make_xinchao_adapter():
    """创建心潮目标系统 async adapter。"""
    import httpx
    endpoint = os.environ.get("XINCHAO_ADAPTER_ENDPOINT", "http://xinchao:3000")
    token = _load_secret_file("XINCHAO_API_TOKEN", "")
    timeout = float(os.environ.get("ADAPTER_TIMEOUT_SECONDS", "10"))

    async def _adapter(payload: dict) -> dict:
        async with httpx.AsyncClient(timeout=timeout) as client:
            headers = {"Content-Type": "application/json"}
            if token:
                headers["Authorization"] = f"Bearer {token}"
            # 根据 payload 类型分发到心潮不同端点
            if payload.get("type") in ("memory_residue", "dialogue_residue"):
                url = f"{endpoint}/api/continuity/ingest"
            elif "driveDeltas" in payload or "satisfiedDrives" in payload:
                url = f"{endpoint}/api/drive/apply"
            else:
                url = f"{endpoint}/api/event"
            resp = await client.post(url, json=payload, headers=headers)
            resp.raise_for_status()
            return resp.json()
    return _adapter


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


def _require_admin_auth(request: Request) -> None:
    if not _check_auth(request, ADMIN_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")


def _require_dashboard_auth(request: Request) -> None:
    if not _check_auth(request, DASHBOARD_TOKEN):
        raise HTTPException(status_code=401, detail="unauthorized")


# ─── Health / Readiness（P0-14）──────────────────────────────────────────────

@app.get("/health")
async def health() -> dict:
    """Readiness 检查：逐项验证所有关键依赖。"""
    components: dict[str, dict[str, Any]] = {}
    overall_ready = True

    # 1. 身份门配置
    try:
        loader = IdentityGateLoader(IDENTITY_CONFIG_PATH)
        ready, msg = loader.readiness()
        components["identity_gate"] = {"ready": ready, "detail": msg}
        if not ready:
            overall_ready = False
    except Exception as e:
        components["identity_gate"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    # 2. 清单文件
    try:
        manifest = ManifestLoader(MANIFEST_PATH)
        ready, msg = manifest.readiness()
        components["manifest"] = {"ready": ready, "detail": msg}
        if not ready:
            overall_ready = False
    except Exception as e:
        components["manifest"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    # 3. PostgreSQL 连接
    try:
        import psycopg
        with psycopg.connect(POSTGRES_DSN) as pg:
            pg.execute("SELECT 1")
        components["postgres"] = {"ready": True, "detail": "connected"}
    except Exception as e:
        components["postgres"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    # 4. 幂等表和 schema 检查
    try:
        import psycopg
        with psycopg.connect(POSTGRES_DSN) as pg:
            pg.execute("SELECT 1 FROM event_inbox LIMIT 0")
            pg.execute("SELECT 1 FROM event_causation_chain LIMIT 0")
        components["event_store_schema"] = {"ready": True, "detail": "tables_exist"}
    except Exception as e:
        components["event_store_schema"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    # 5. Nocturne bucket 目录可访问性
    try:
        if BUCKETS_DIR.exists():
            subdirs = [d for d in BUCKETS_DIR.iterdir() if d.is_dir()]
            components["nocturne_buckets"] = {
                "ready": True,
                "detail": f"base_dir_exists, subdirs={len(subdirs)}",
            }
        else:
            components["nocturne_buckets"] = {"ready": False, "detail": f"dir_not_found: {BUCKETS_DIR}"}
            overall_ready = False
    except Exception as e:
        components["nocturne_buckets"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    # 6. 审计目录可写
    try:
        test_file = AUDIT_DIR / ".write_test"
        test_file.write_text("test")
        test_file.unlink()
        components["audit_dir"] = {"ready": True, "detail": "writable"}
    except Exception as e:
        components["audit_dir"] = {"ready": False, "detail": str(e)}
        overall_ready = False

    status_code = 200 if overall_ready else 503
    return JSONResponse(
        content={
            "status": "ready" if overall_ready else "not_ready",
            "components": components,
        },
        status_code=status_code,
    )


@app.get("/live")
async def liveness() -> dict:
    """Liveness 检查：进程存活即可。"""
    return {"status": "alive"}


# ─── Manifest 同步（P0-10：写入路由鉴权）──────────────────────────────────────

@app.post("/sync/manifest")
async def sync_manifest(request: Request) -> dict:
    """执行连续性清单同步。"""
    _require_admin_auth(request)

    manifest = ManifestLoader(MANIFEST_PATH)
    # P0-03: 清单 readiness 检查
    ready, msg = manifest.readiness()
    if not ready:
        raise HTTPException(status_code=503, detail=f"manifest not ready: {msg}")

    syncer = ProtectionSynchronizer(BUCKETS_DIR, AUDIT_DIR, POSTGRES_DSN)
    records = syncer.sync_all(manifest)

    return {
        "synced": len(records),
        "successes": sum(1 for r in records if r.success),
        "failures": sum(1 for r in records if not r.success),
        "records": [r.to_dict() for r in records],
    }


# ─── Identity Gate 装配（P0-10：写入路由鉴权）─────────────────────────────────

@app.post("/identity/assemble")
async def assemble_identity(request: Request, messages: list[dict]) -> dict:
    """装配 PromptPlan 到消息列表。"""
    _require_admin_auth(request)

    loader = IdentityGateLoader(IDENTITY_CONFIG_PATH)
    config = loader.load()
    assembler = PromptPlanAssembler(config, AUDIT_DIR, POSTGRES_DSN)
    final_messages, record = assembler.assemble(messages)

    # P1-05: overflow 时返回明确错误
    if record.overflow:
        raise HTTPException(
            status_code=507,
            detail={
                "error": "token_budget_overflow",
                "total_tokens": record.total_tokens,
                "model_hard_limit": config.model_hard_limit,
                "assembly": record.to_dict(),
            },
        )

    return {
        "messages": final_messages,
        "assembly": record.to_dict(),
    }


@app.get("/identity/readiness")
async def identity_readiness() -> dict:
    """身份门 readiness 检查。"""
    loader = IdentityGateLoader(IDENTITY_CONFIG_PATH)
    ready, msg = loader.readiness()
    status_code = 200 if ready else 503
    return JSONResponse(
        content={"ready": ready, "detail": msg},
        status_code=status_code,
    )


# ─── 事件桥接（P0-10：写入路由鉴权）───────────────────────────────────────────

@app.post("/bridge/nocturne-to-xinchao")
async def bridge_n2x(request: Request, envelope: dict) -> dict:
    """接收 Nocturne 事件，转换后输出心潮格式。"""
    _require_admin_auth(request)

    try:
        ev = EventEnvelope.from_dict(envelope)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {e}")

    bridge = _get_event_bridge()
    result = await bridge.process_nocturne_event(ev)
    return result


@app.post("/bridge/xinchao-to-nocturne")
async def bridge_x2n(request: Request, envelope: dict) -> dict:
    """接收心潮事件，转换后输出 Nocturne 格式。"""
    _require_admin_auth(request)

    try:
        ev = EventEnvelope.from_dict(envelope)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"invalid envelope: {e}")

    bridge = _get_event_bridge()
    result = await bridge.process_xinchao_event(ev)
    return result


# ─── Dashboard API（只读，P0-10：独立鉴权）────────────────────────────────────

@app.get("/dashboard/breath")
async def dashboard_breath(request: Request, query: str = "") -> dict:
    _require_dashboard_auth(request)
    return {"results": _get_dashboard().get_breath_summary(query)}


@app.get("/dashboard/dimensions")
async def dashboard_dimensions(request: Request) -> dict:
    _require_dashboard_auth(request)
    return {"dimensions": _get_dashboard().get_dimensions()}


@app.get("/dashboard/thoughts")
async def dashboard_thoughts(request: Request, limit: int = 10) -> dict:
    _require_dashboard_auth(request)
    return {"thoughts": _get_dashboard().get_recent_thoughts_meta(limit)}


@app.get("/dashboard/migrations")
async def dashboard_migrations(request: Request) -> dict:
    _require_dashboard_auth(request)
    return {"migrations": _get_dashboard().get_migration_status()}


@app.get("/dashboard/bridge-health")
async def dashboard_bridge(request: Request) -> dict:
    _require_dashboard_auth(request)
    return {"bridges": _get_dashboard().get_bridge_health()}


@app.get("/dashboard/system-health")
async def dashboard_system(request: Request) -> dict:
    _require_dashboard_auth(request)
    return _get_dashboard().get_system_health()


@app.get("/dashboard/continuity-manifest")
async def dashboard_manifest(request: Request) -> dict:
    _require_dashboard_auth(request)
    return _get_dashboard().get_continuity_manifest_summary(MANIFEST_PATH)


# ─── 启动 ────────────────────────────────────────────────────────────────────

def main() -> None:
    port = int(os.environ.get("GUARD_PORT", "8003"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
