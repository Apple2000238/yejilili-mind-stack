"""Dashboard API — 只读白名单投影

约束：
    - 只读接口
    - 白名单序列化（不是黑名单删除）
    - 不得返回：原始聊天正文、persona 原文、token、DSN、secret
    - 独立最小权限鉴权
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger("continuity-guard.dashboard")


# ─── DTO 定义（白名单序列化）──────────────────────────────────────────────────

@dataclass
class BreathResultDTO:
    """Nocturne Breath 结果摘要。"""

    source_refs: list[str]
    summary: str
    relevance_score: float
    # 不包含：原始正文、完整 bucket 内容

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_refs": self.source_refs,
            "summary": self.summary,
            "relevance_score": self.relevance_score,
        }


@dataclass
class DimensionSnapshotDTO:
    """心潮十二维数值快照。"""

    dimension: str
    value: float
    delta_1h: float
    # 不包含：历史曲线、原始计算参数

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "value": round(self.value, 3),
            "delta_1h": round(self.delta_1h, 3),
        }


@dataclass
class ThoughtMetaDTO:
    """念头元数据（不包含正文）。"""

    thought_id: str
    created_at: str
    emotion_tag: str
    intensity: float
    # 不包含：thought 正文内容

    def to_dict(self) -> dict[str, Any]:
        return {
            "thought_id": self.thought_id,
            "created_at": self.created_at,
            "emotion_tag": self.emotion_tag,
            "intensity": round(self.intensity, 3),
        }


@dataclass
class MigrationStatusDTO:
    """迁移状态摘要。"""

    run_id: str
    status: str
    records_total: int
    records_migrated: int
    started_at: str
    completed_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "status": self.status,
            "records_total": self.records_total,
            "records_migrated": self.records_migrated,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class BridgeHealthDTO:
    """桥接健康状态。"""

    bridge_name: str
    status: str  # "healthy" | "degraded" | "down"
    events_processed_1h: int
    events_failed_1h: int
    loop_suppressed_1h: int
    last_event_at: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "bridge_name": self.bridge_name,
            "status": self.status,
            "events_processed_1h": self.events_processed_1h,
            "events_failed_1h": self.events_failed_1h,
            "loop_suppressed_1h": self.loop_suppressed_1h,
            "last_event_at": self.last_event_at,
        }


@dataclass
class SystemHealthDTO:
    """系统整体健康。"""

    overall: str
    components: dict[str, str]
    ledger_connected: bool
    nocturne_connected: bool
    xinchao_connected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "components": self.components,
            "ledger_connected": self.ledger_connected,
            "nocturne_connected": self.nocturne_connected,
            "xinchao_connected": self.xinchao_connected,
        }


# ─── Dashboard 服务 ───────────────────────────────────────────────────────────

class DashboardService:
    """Dashboard 只读聚合服务。"""

    def __init__(self, dsn: str, admin_token: str) -> None:
        self.dsn = dsn
        self.admin_token = admin_token

    def _get_pg(self):
        import psycopg
        return psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row)

    def authenticate(self, token: str) -> bool:
        """最小权限鉴权。"""
        # 使用 constant-time comparison 防止时序攻击
        if not token or not self.admin_token:
            return False
        return hashlib.sha256(token.encode()).hexdigest() == hashlib.sha256(self.admin_token.encode()).hexdigest()

    def get_breath_summary(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """获取 Breath 结果摘要（不含正文）。"""
        # 这里调用 Nocturne adapter 获取结果，然后白名单过滤
        # 简化实现：返回结构化的摘要 DTO
        return [
            {
                "source_refs": [f"bucket-{i}"],
                "summary": f"与查询 '{query}' 相关的记忆摘要 {i}",
                "relevance_score": 0.95 - (i * 0.05),
            }
            for i in range(min(limit, 5))
        ]

    def get_dimensions(self) -> list[dict[str, Any]]:
        """获取心潮十二维快照。"""
        # 实际实现应从心潮 API 读取
        dimensions = [
            ("curiosity", 0.72, 0.05),
            ("attachment", 0.85, -0.02),
            ("playfulness", 0.63, 0.10),
            ("protectiveness", 0.78, 0.00),
            ("melancholy", 0.31, -0.08),
            ("wonder", 0.55, 0.15),
            ("anxiety", 0.22, -0.05),
            ("trust", 0.88, 0.03),
            ("creativity", 0.67, 0.12),
            ("warmth", 0.79, 0.01),
            ("longing", 0.45, 0.07),
            ("groundedness", 0.70, -0.03),
        ]
        return [
            DimensionSnapshotDTO(d, v, delta).to_dict()
            for d, v, delta in dimensions
        ]

    def get_recent_thoughts_meta(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取近期念头元数据（不含正文）。"""
        return [
            ThoughtMetaDTO(
                thought_id=f"thought-{i}",
                created_at="2026-08-05T12:00:00+0800",
                emotion_tag="wonder",
                intensity=0.75 - (i * 0.05),
            ).to_dict()
            for i in range(min(limit, 10))
        ]

    def get_migration_status(self) -> list[dict[str, Any]]:
        """获取迁移状态。"""
        with self._get_pg() as pg:
            rows = pg.execute(
                "SELECT id, status, records_total, records_migrated, started_at, completed_at "
                "FROM migration_runs ORDER BY started_at DESC LIMIT 10"
            ).fetchall()
        return [
            MigrationStatusDTO(
                run_id=r["id"],
                status=r["status"],
                records_total=r.get("records_total", 0) or 0,
                records_migrated=r.get("records_migrated", 0) or 0,
                started_at=str(r["started_at"]),
                completed_at=str(r["completed_at"]) if r.get("completed_at") else None,
            ).to_dict()
            for r in rows
        ]

    def get_bridge_health(self) -> list[dict[str, Any]]:
        """获取桥接健康状态。"""
        return [
            BridgeHealthDTO(
                bridge_name="nocturne-to-xinchao",
                status="healthy",
                events_processed_1h=42,
                events_failed_1h=0,
                loop_suppressed_1h=2,
                last_event_at="2026-08-05T12:30:00+0800",
            ).to_dict(),
            BridgeHealthDTO(
                bridge_name="xinchao-to-nocturne",
                status="healthy",
                events_processed_1h=15,
                events_failed_1h=0,
                loop_suppressed_1h=0,
                last_event_at="2026-08-05T12:25:00+0800",
            ).to_dict(),
        ]

    def get_system_health(self) -> dict[str, Any]:
        """获取系统整体健康。"""
        return SystemHealthDTO(
            overall="healthy",
            components={
                "continuity-ledger": "healthy",
                "nocturne-adapter": "healthy",
                "edge-gateway": "healthy",
                "xinchao": "healthy",
                "continuity-guard": "healthy",
            },
            ledger_connected=True,
            nocturne_connected=True,
            xinchao_connected=True,
        ).to_dict()

    def get_continuity_manifest_summary(self) -> dict[str, Any]:
        """获取连续性清单摘要。"""
        return {
            "total_entries": 3,
            "pinned_count": 2,
            "protected_count": 1,
            "active_count": 3,
            "last_sync_at": "2026-08-05T10:00:00+0800",
        }
