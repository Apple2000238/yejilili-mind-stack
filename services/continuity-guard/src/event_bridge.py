"""Bidirectional Event Bridge — Nocturne ↔ XinChao 双向事件桥

约束：
    - 所有跨系统事件使用版本化 envelope
    - Nocturne drive_event_v2 只能转换成 driveDeltas / satisfiedDrives / Weather
    - memory_residue / dialogue_residue 的 thoughts 必须始终为空数组
    - 用户原始对话触发 hold 后，可结算一次心潮 applyConversationEvent
    - 桥接器派生的 hold 不得反向触发同一心潮结算
    - 心潮短态不得直接写成长期记忆
    - 梦境写入 Nocturne 必须带 auto=True, source='xinchao-dream'
    - 同一 event_id 并发/重试/重启只能产生一次业务效果
    - payload 与同一 event_id 冲突时必须报警并拒绝
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("continuity-guard.event_bridge")

# ─── Schema 常量 ──────────────────────────────────────────────────────────────

EVENT_SCHEMA_VERSION = "1.0.0"

# 允许的事件类型
NOCTURNE_TO_XINCHAO_TYPES = {
    "drive_event_v2",
    "memory_residue",
    "dialogue_residue",
}

XINCHAO_TO_NOCTURNE_TYPES = {
    "dream",
    "conversation_event",
    "state_change",
    "heartbeat",
}

# 来源白名单
ORIGIN_ALLOWLIST = {
    "nocturne",
    "xinchao",
    "edge-gateway",
    "migration-cli",
    "continuity-guard",
}


# ─── 事件 Envelope ────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EventEnvelope:
    """版本化事件 envelope — 不可变值对象。"""

    schema_version: str
    event_id: str
    correlation_id: str
    causation_id: str
    origin: str
    event_type: str
    occurred_at: str
    received_at: str
    namespace: str
    derived_from: str
    payload_hash: str
    payload: dict[str, Any]

    def __post_init__(self) -> None:
        if self.origin not in ORIGIN_ALLOWLIST:
            raise ValueError(f"origin '{self.origin}' not in allowlist")
        if not self.event_id:
            raise ValueError("event_id is required")
        if not self.payload_hash:
            raise ValueError("payload_hash is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "correlation_id": self.correlation_id,
            "causation_id": self.causation_id,
            "origin": self.origin,
            "event_type": self.event_type,
            "occurred_at": self.occurred_at,
            "received_at": self.received_at,
            "namespace": self.namespace,
            "derived_from": self.derived_from,
            "payload_hash": self.payload_hash,
            "payload": self.payload,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "EventEnvelope":
        return cls(
            schema_version=d.get("schema_version", EVENT_SCHEMA_VERSION),
            event_id=d["event_id"],
            correlation_id=d.get("correlation_id", ""),
            causation_id=d.get("causation_id", ""),
            origin=d["origin"],
            event_type=d["event_type"],
            occurred_at=d.get("occurred_at", ""),
            received_at=d.get("received_at", ""),
            namespace=d.get("namespace", ""),
            derived_from=d.get("derived_from", ""),
            payload_hash=d["payload_hash"],
            payload=d.get("payload", {}),
        )


def _compute_payload_hash(payload: dict[str, Any]) -> str:
    """计算 payload 的确定性 hash。"""
    canonical = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def create_envelope(
    event_id: str,
    origin: str,
    event_type: str,
    payload: dict[str, Any],
    correlation_id: str = "",
    causation_id: str = "",
    namespace: str = "",
    derived_from: str = "",
) -> EventEnvelope:
    """创建标准化事件 envelope。"""
    now = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    return EventEnvelope(
        schema_version=EVENT_SCHEMA_VERSION,
        event_id=event_id,
        correlation_id=correlation_id or event_id,
        causation_id=causation_id,
        origin=origin,
        event_type=event_type,
        occurred_at=now,
        received_at=now,
        namespace=namespace,
        derived_from=derived_from,
        payload_hash=_compute_payload_hash(payload),
        payload=payload,
    )


# ─── 幂等存储 ─────────────────────────────────────────────────────────────────

class IdempotencyStore:
    """跨服务幂等记录存储。

    要求：
    - 持久化存储（PostgreSQL）
    - 唯一约束或等价原子机制
    - 同一 event_id 不同 payload 时报警并拒绝
    """

    def __init__(self, dsn: str) -> None:
        self.dsn = dsn

    def _get_pg(self):
        import psycopg
        return psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row)

    def check_and_record(
        self,
        event_id: str,
        payload: dict[str, Any],
        origin: str,
        event_type: str,
    ) -> tuple[bool, str]:
        """
        检查幂等性并记录。

        返回：(should_process, reason)
        - should_process=True: 首次看到此 event_id，继续处理
        - should_process=False: 已处理过或冲突，跳过
        """
        payload_hash = _compute_payload_hash(payload)

        with self._get_pg() as pg:
            # 尝试插入（唯一约束在 event_id）
            try:
                pg.execute(
                    """
                    INSERT INTO event_idempotency_log
                    (event_id, payload_hash, origin, event_type, first_seen_at, status)
                    VALUES (%s, %s, %s, %s, now(), %s)
                    """,
                    (event_id, payload_hash, origin, event_type, "recorded"),
                )
                pg.commit()
                logger.info("idempotency: new event recorded: %s", event_id)
                return True, "new"
            except psycopg.errors.UniqueViolation:
                pg.rollback()
                # 检查 payload hash 是否一致
                existing = pg.execute(
                    "SELECT payload_hash, status FROM event_idempotency_log WHERE event_id=%s",
                    (event_id,),
                ).fetchone()
                if existing:
                    if existing["payload_hash"] == payload_hash:
                        logger.info("idempotency: duplicate event skipped: %s", event_id)
                        return False, "duplicate"
                    else:
                        # 严重冲突：同一 event_id 不同 payload
                        logger.error(
                            "IDEMPOTENCY CONFLICT: event_id=%s existing_hash=%s new_hash=%s",
                            event_id, existing["payload_hash"], payload_hash,
                        )
                        # 记录冲突
                        pg.execute(
                            """
                            INSERT INTO event_idempotency_conflicts
                            (event_id, existing_payload_hash, new_payload_hash, detected_at)
                            VALUES (%s, %s, %s, now())
                            ON CONFLICT (event_id) DO NOTHING
                            """,
                            (event_id, existing["payload_hash"], payload_hash),
                        )
                        pg.commit()
                        return False, "conflict"
                return False, "unknown"

    def mark_completed(self, event_id: str) -> None:
        """标记事件为已完成。"""
        with self._get_pg() as pg:
            pg.execute(
                "UPDATE event_idempotency_log SET status=%s, completed_at=now() WHERE event_id=%s",
                ("completed", event_id),
            )
            pg.commit()

    def mark_failed(self, event_id: str, error: str) -> None:
        """标记事件为失败。"""
        with self._get_pg() as pg:
            pg.execute(
                "UPDATE event_idempotency_log SET status=%s, error=%s WHERE event_id=%s",
                ("failed", error[:500], event_id),
            )
            pg.commit()


# ─── Nocturne → XinChao 转换器 ────────────────────────────────────────────────

class NocturneToXinChaoTranslator:
    """将 Nocturne 事件转换为心潮可接受的格式。"""

    def translate_drive_event(self, envelope: EventEnvelope) -> dict[str, Any]:
        """Nocturne drive_event_v2 → 心潮 driveDeltas / satisfiedDrives / Weather。"""
        payload = envelope.payload

        # 提取 drive 信息
        drive_name = payload.get("drive_name", "")
        intensity = payload.get("intensity", 0)
        satisfied = payload.get("satisfied", False)

        # 转换为心潮格式
        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "source_event_id": envelope.event_id,
            "source_origin": envelope.origin,
            "driveDeltas": [],
            "satisfiedDrives": [],
            "weather": {},
        }

        if satisfied:
            result["satisfiedDrives"].append({
                "drive": drive_name,
                "at": envelope.occurred_at,
            })
        else:
            result["driveDeltas"].append({
                "drive": drive_name,
                "delta": intensity,
                "at": envelope.occurred_at,
            })

        # Weather 结构化投影
        result["weather"] = {
            "drive": drive_name,
            "intensity": intensity,
            "source": "nocturne_drive_event",
        }

        return result

    def translate_memory_residue(self, envelope: EventEnvelope) -> dict[str, Any]:
        """Nocturne memory_residue → 心潮，thoughts 必须为空数组。"""
        return {
            "schema_version": "1.0.0",
            "source_event_id": envelope.event_id,
            "type": "memory_residue",
            "thoughts": [],  # 强制为空
            "references": envelope.payload.get("references", []),
            "summary": envelope.payload.get("summary", ""),
        }

    def translate_dialogue_residue(self, envelope: EventEnvelope) -> dict[str, Any]:
        """Nocturne dialogue_residue → 心潮，thoughts 必须为空数组。"""
        return {
            "schema_version": "1.0.0",
            "source_event_id": envelope.event_id,
            "type": "dialogue_residue",
            "thoughts": [],  # 强制为空
            "references": envelope.payload.get("references", []),
            "summary": envelope.payload.get("summary", ""),
        }


# ─── XinChao → Nocturne 转换器 ────────────────────────────────────────────────

class XinChaoToNocturneTranslator:
    """将心潮事件转换为 Nocturne 可接受的格式。"""

    def translate_dream(self, envelope: EventEnvelope) -> dict[str, Any]:
        """心潮梦境 → Nocturne hold，带 auto=True, source='xinchao-dream'。"""
        payload = envelope.payload
        return {
            "content": payload.get("content", ""),
            "tags": payload.get("tags", "dream,xinchao"),
            "importance": payload.get("importance", 3),
            "auto": True,
            "source": "xinchao-dream",
            "event_id": envelope.event_id,
            "ttl_days": payload.get("ttl_days", 30),
        }

    def translate_conversation_event(self, envelope: EventEnvelope) -> dict[str, Any]:
        """心潮对话事件 → Nocturne hold（用户主动互动）。"""
        payload = envelope.payload
        return {
            "content": payload.get("content", ""),
            "tags": payload.get("tags", "conversation,user-initiated"),
            "importance": payload.get("importance", 5),
            "auto": False,
            "source": "xinchao-conversation",
            "event_id": envelope.event_id,
        }

    def translate_state_change(self, envelope: EventEnvelope) -> dict[str, Any]:
        """心潮状态变化 → Nocturne（普通短态，不直接写入长期记忆）。"""
        # 状态变化不直接写成长期记忆，只记录日志
        return {
            "action": "log_only",
            "event_id": envelope.event_id,
            "state_delta": envelope.payload,
        }


# ─── 回环抑制器 ───────────────────────────────────────────────────────────────

class LoopSuppressor:
    """防止 Nocturne → 心潮 → Nocturne 无限回环。"""

    def __init__(self, max_depth: int = 3) -> None:
        self.max_depth = max_depth

    def check(self, envelope: EventEnvelope) -> tuple[bool, str]:
        """检查是否会导致回环。

        规则：
        1. derived_from 链深度不超过 max_depth
        2. 桥接器派生的 hold 不得反向触发同一心潮结算
        3. 检查 causation_id 循环
        """
        # 简化实现：检查 derived_from 是否包含本系统标记
        if envelope.derived_from.startswith("bridge:"):
            depth = envelope.derived_from.count("bridge:")
            if depth >= self.max_depth:
                return False, f"loop depth exceeded: {depth}"

        # 检查是否是由桥接器派生的事件反向触发
        if envelope.origin == "xinchao" and envelope.event_type in XINCHAO_TO_NOCTURNE_TYPES:
            if envelope.derived_from.startswith("nocturne:"):
                return False, "back-loop detected: xinchao event derived from nocturne"

        return True, "ok"


# ─── 事件桥接器 ───────────────────────────────────────────────────────────────

class EventBridge:
    """双向事件桥接器。"""

    def __init__(
        self,
        idempotency_store: IdempotencyStore,
        loop_suppressor: LoopSuppressor,
    ) -> None:
        self.idempotency = idempotency_store
        self.loop = loop_suppressor
        self.n2x = NocturneToXinChaoTranslator()
        self.x2n = XinChaoToNocturneTranslator()

    async def process_nocturne_event(self, envelope: EventEnvelope) -> dict[str, Any] | None:
        """处理 Nocturne → 心潮 事件。"""
        # 1. 幂等检查
        should_process, reason = self.idempotency.check_and_record(
            envelope.event_id,
            envelope.payload,
            envelope.origin,
            envelope.event_type,
        )
        if not should_process:
            logger.info("nocturne event skipped: %s reason=%s", envelope.event_id, reason)
            return None

        # 2. 回环检查
        ok, loop_reason = self.loop.check(envelope)
        if not ok:
            logger.warning("nocturne event loop suppressed: %s %s", envelope.event_id, loop_reason)
            self.idempotency.mark_failed(envelope.event_id, f"loop_suppressed: {loop_reason}")
            return None

        try:
            # 3. 类型分发
            if envelope.event_type == "drive_event_v2":
                result = self.n2x.translate_drive_event(envelope)
            elif envelope.event_type == "memory_residue":
                result = self.n2x.translate_memory_residue(envelope)
            elif envelope.event_type == "dialogue_residue":
                result = self.n2x.translate_dialogue_residue(envelope)
            else:
                logger.warning("unknown nocturne event type: %s", envelope.event_type)
                self.idempotency.mark_failed(envelope.event_id, f"unknown_type: {envelope.event_type}")
                return None

            self.idempotency.mark_completed(envelope.event_id)
            return result

        except Exception as e:
            logger.error("nocturne event processing failed: %s", e, exc_info=True)
            self.idempotency.mark_failed(envelope.event_id, str(e)[:500])
            raise

    async def process_xinchao_event(self, envelope: EventEnvelope) -> dict[str, Any] | None:
        """处理 心潮 → Nocturne 事件。"""
        # 1. 幂等检查
        should_process, reason = self.idempotency.check_and_record(
            envelope.event_id,
            envelope.payload,
            envelope.origin,
            envelope.event_type,
        )
        if not should_process:
            logger.info("xinchao event skipped: %s reason=%s", envelope.event_id, reason)
            return None

        # 2. 回环检查
        ok, loop_reason = self.loop.check(envelope)
        if not ok:
            logger.warning("xinchao event loop suppressed: %s %s", envelope.event_id, loop_reason)
            self.idempotency.mark_failed(envelope.event_id, f"loop_suppressed: {loop_reason}")
            return None

        try:
            # 3. 类型分发
            if envelope.event_type == "dream":
                result = self.x2n.translate_dream(envelope)
            elif envelope.event_type == "conversation_event":
                result = self.x2n.translate_conversation_event(envelope)
            elif envelope.event_type == "state_change":
                result = self.x2n.translate_state_change(envelope)
            else:
                logger.warning("unknown xinchao event type: %s", envelope.event_type)
                self.idempotency.mark_failed(envelope.event_id, f"unknown_type: {envelope.event_type}")
                return None

            self.idempotency.mark_completed(envelope.event_id)
            return result

        except Exception as e:
            logger.error("xinchao event processing failed: %s", e, exc_info=True)
            self.idempotency.mark_failed(envelope.event_id, str(e)[:500])
            raise
