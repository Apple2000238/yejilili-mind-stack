"""Dashboard API — 只读白名单投影

约束：
    - 只读接口
    - 白名单序列化（不是黑名单删除）
    - 不得返回：原始聊天正文、persona 原文、token、DSN、secret
    - 独立最小权限鉴权（使用 hmac.compare_digest）
    - 数据不可用时返回 degraded/down 和明确时间戳
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
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
    checked_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall": self.overall,
            "components": self.components,
            "ledger_connected": self.ledger_connected,
            "nocturne_connected": self.nocturne_connected,
            "xinchao_connected": self.xinchao_connected,
            "checked_at": self.checked_at,
        }


# ─── Dashboard 服务 ───────────────────────────────────────────────────────────

class DashboardService:
    """Dashboard 只读聚合服务 — 从真实数据库读取投影数据。"""

    def __init__(self, dsn: str, dashboard_token: str) -> None:
        self.dsn = dsn
        self.dashboard_token = dashboard_token

    def _get_pg(self):
        import psycopg
        return psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row)

    def authenticate(self, token: str) -> bool:
        """最小权限鉴权 — 使用 hmac.compare_digest 防止时序攻击（P1-04）。"""
        if not token or not self.dashboard_token:
            return False
        return hmac.compare_digest(token.encode(), self.dashboard_token.encode())

    def _now_iso(self) -> str:
        return time.strftime("%Y-%m-%dT%H:%M:%S%z")

    def get_breath_summary(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        """获取 Breath 结果摘要（从数据库读取，不含正文）。"""
        try:
            with self._get_pg() as pg:
                rows = pg.execute(
                    """
                    SELECT source_refs, summary, relevance_score
                    FROM breath_results
                    WHERE summary ILIKE %s
                    ORDER BY relevance_score DESC
                    LIMIT %s
                    """,
                    (f"%{query}%", limit),
                ).fetchall()
            if rows:
                return [
                    BreathResultDTO(
                        source_refs=r.get("source_refs", []),
                        summary=r.get("summary", ""),
                        relevance_score=r.get("relevance_score", 0.0),
                    ).to_dict()
                    for r in rows
                ]
        except Exception as e:
            logger.warning("breath_summary query failed: %s", e)

        # 数据不可用时返回 degraded 标记
        return [
            {
                "source_refs": [],
                "summary": "[degraded: no breath data available]",
                "relevance_score": 0.0,
                "status": "degraded",
                "checked_at": self._now_iso(),
            }
        ]

    def get_dimensions(self) -> list[dict[str, Any]]:
        """获取心潮十二维快照（从数据库读取）。"""
        try:
            with self._get_pg() as pg:
                rows = pg.execute(
                    """
                    SELECT dimension, value, delta_1h
                    FROM dimension_snapshots
                    WHERE checked_at > now() - interval '1 hour'
                    ORDER BY dimension
                    """
                ).fetchall()
            if rows:
                return [
                    DimensionSnapshotDTO(
                        dimension=r["dimension"],
                        value=r.get("value", 0.0),
                        delta_1h=r.get("delta_1h", 0.0),
                    ).to_dict()
                    for r in rows
                ]
        except Exception as e:
            logger.warning("dimensions query failed: %s", e)

        # 数据不可用时返回 degraded
        return [
            {
                "dimension": d,
                "value": 0.0,
                "delta_1h": 0.0,
                "status": "degraded",
                "checked_at": self._now_iso(),
            }
            for d in [
                "possess", "monitor", "crave", "share", "libido", "curiosity",
                "boredom", "social", "duty", "reflection", "grieve", "anger",
            ]
        ]

    def get_recent_thoughts_meta(self, limit: int = 10) -> list[dict[str, Any]]:
        """获取近期念头元数据（不含正文，从数据库读取）。"""
        try:
            with self._get_pg() as pg:
                rows = pg.execute(
                    """
                    SELECT thought_id, created_at, emotion_tag, intensity
                    FROM thought_meta
                    ORDER BY created_at DESC
                    LIMIT %s
                    """,
                    (limit,),
                ).fetchall()
            if rows:
                return [
                    ThoughtMetaDTO(
                        thought_id=r["thought_id"],
                        created_at=str(r["created_at"]),
                        emotion_tag=r.get("emotion_tag", ""),
                        intensity=r.get("intensity", 0.0),
                    ).to_dict()
                    for r in rows
                ]
        except Exception as e:
            logger.warning("thoughts query failed: %s", e)

        return [
            {
                "thought_id": "none",
                "created_at": self._now_iso(),
                "emotion_tag": "unknown",
                "intensity": 0.0,
                "status": "degraded",
            }
        ]

    def get_migration_status(self) -> list[dict[str, Any]]:
        """获取迁移状态（从数据库读取）。"""
        try:
            with self._get_pg() as pg:
                rows = pg.execute(
                    """
                    SELECT id, status, records_total, records_migrated, started_at, completed_at
                    FROM migration_runs
                    ORDER BY started_at DESC
                    LIMIT 10
                    """
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
        except Exception as e:
            logger.warning("migration_status query failed: %s", e)
            return [
                {
                    "run_id": "error",
                    "status": "degraded",
                    "detail": str(e),
                    "checked_at": self._now_iso(),
                }
            ]

    def get_bridge_health(self) -> list[dict[str, Any]]:
        """获取桥接健康状态（从数据库读取真实统计）。"""
        try:
            with self._get_pg() as pg:
                results = []
                # 最近 1 小时的统计 — 分别查询两条桥
                for bridge_name, origin_filter in [
                    ("nocturne-to-xinchao", "nocturne"),
                    ("xinchao-to-nocturne", "xinchao"),
                ]:
                    stats = pg.execute(
                        """
                        SELECT
                            COUNT(*) FILTER (WHERE status = 'completed') AS completed,
                            COUNT(*) FILTER (WHERE status = 'failed') AS failed,
                            COUNT(*) FILTER (WHERE status = 'failed' AND error LIKE '%loop%') AS loop_suppressed,
                            MAX(updated_at) AS last_event
                        FROM event_inbox
                        WHERE origin = %s
                          AND updated_at > now() - interval '1 hour'
                        """,
                        (origin_filter,),
                    ).fetchone()

                    completed = stats.get("completed", 0) or 0
                    failed = stats.get("failed", 0) or 0
                    loop = stats.get("loop_suppressed", 0) or 0
                    last_event = stats.get("last_event")

                    # 状态判定
                    if failed > completed * 0.5:
                        status = "down"
                    elif failed > 0:
                        status = "degraded"
                    else:
                        status = "healthy"

                    results.append(
                        BridgeHealthDTO(
                            bridge_name=bridge_name,
                            status=status,
                            events_processed_1h=completed,
                            events_failed_1h=failed,
                            loop_suppressed_1h=loop,
                            last_event_at=str(last_event) if last_event else None,
                        ).to_dict()
                    )
                return results
        except Exception as e:
            logger.warning("bridge_health query failed: %s", e)

        return [
            BridgeHealthDTO(
                bridge_name="nocturne-to-xinchao",
                status="down",
                events_processed_1h=0,
                events_failed_1h=0,
                loop_suppressed_1h=0,
                last_event_at=None,
            ).to_dict(),
            BridgeHealthDTO(
                bridge_name="xinchao-to-nocturne",
                status="down",
                events_processed_1h=0,
                events_failed_1h=0,
                loop_suppressed_1h=0,
                last_event_at=None,
            ).to_dict(),
        ]

    def get_system_health(self) -> dict[str, Any]:
        """获取系统整体健康（检查真实依赖）。"""
        components: dict[str, str] = {}
        ledger_ok = False
        nocturne_ok = False
        xinchao_ok = False
        checked_at = self._now_iso()

        # 检查 ledger
        try:
            with self._get_pg() as pg:
                pg.execute("SELECT 1")
            components["continuity-ledger"] = "healthy"
            ledger_ok = True
        except Exception as e:
            components["continuity-ledger"] = "down"
            logger.warning("ledger health check failed: %s", e)

        # 检查 Nocturne adapter（通过 health endpoint）
        try:
            import urllib.request
            urllib.request.urlopen("http://nocturne-adapter:8001/health", timeout=5)
            components["nocturne-adapter"] = "healthy"
            nocturne_ok = True
        except Exception:
            components["nocturne-adapter"] = "down"

        # 检查 Edge Gateway
        try:
            import urllib.request
            urllib.request.urlopen("http://edge-gateway:8002/health", timeout=5)
            components["edge-gateway"] = "healthy"
        except Exception:
            components["edge-gateway"] = "down"

        # 检查心潮
        try:
            import urllib.request
            urllib.request.urlopen("http://xinchao:3000/health", timeout=5)
            components["xinchao"] = "healthy"
            xinchao_ok = True
        except Exception:
            components["xinchao"] = "down"

        # Guard 自身
        components["continuity-guard"] = "healthy"

        # 整体状态
        if all(v == "healthy" for v in components.values()):
            overall = "healthy"
        elif any(v == "down" for v in components.values()):
            overall = "degraded"
        else:
            overall = "degraded"

        return SystemHealthDTO(
            overall=overall,
            components=components,
            ledger_connected=ledger_ok,
            nocturne_connected=nocturne_ok,
            xinchao_connected=xinchao_ok,
            checked_at=checked_at,
        ).to_dict()

    def get_continuity_manifest_summary(self, manifest_path: str) -> dict[str, Any]:
        """获取连续性清单摘要（从真实 manifest 文件读取）。"""
        try:
            import json
            p = __import__("pathlib").Path(manifest_path)
            if not p.exists():
                return {
                    "total_entries": 0,
                    "pinned_count": 0,
                    "protected_count": 0,
                    "active_count": 0,
                    "status": "not_found",
                    "checked_at": self._now_iso(),
                }

            raw = json.loads(p.read_text(encoding="utf-8"))
            entries = raw.get("entries", [])
            active = [e for e in entries if e.get("active", True)]
            pinned = [e for e in active if e.get("protection") == "pinned"]
            protected = [e for e in active if e.get("protection") == "protected"]

            return {
                "total_entries": len(entries),
                "pinned_count": len(pinned),
                "protected_count": len(protected),
                "active_count": len(active),
                "status": "ok",
                "checked_at": self._now_iso(),
            }
        except Exception as e:
            logger.warning("manifest_summary failed: %s", e)
            return {
                "total_entries": 0,
                "pinned_count": 0,
                "protected_count": 0,
                "active_count": 0,
                "status": "error",
                "detail": str(e),
                "checked_at": self._now_iso(),
            }
