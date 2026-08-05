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
    - payload_hash 必须校验（P1-01）
    - 持久化 inbox/outbox 状态机，支持崩溃恢复和重试（P0-07）
    - causation_id 因果图检查（P0-08）
    - Drive → 心潮十二维版本化映射表（P0-09）
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

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

# ─── Drive → 心潮十二维版本化映射表（P0-09）───────────────────────────────────
# 源码依据：
#   - Nocturne desire_engine.py / dialogue_residue_engine.py / memory_residue_engine.py
#     定义 drive_name 为字符串标签（如 "possess", "curiosity", "monitor"）
#   - 心潮 dimensions.js 定义 12 维：possess, monitor, crave, share, libido,
#     curiosity, boredom, social, duty, reflection, grieve, anger
#   - 映射方向：Nocturne drive_name → 心潮 dimension key
#   - 单位：Nocturne intensity 为 0~10 浮点，心潮 delta 为 -1~+1 浮点
#   - 限幅：delta 绝对值不超过 0.3（防止单事件剧烈波动）
#   - 版本字段：schema_version 用于映射演进时兼容

DRIVE_MAPPING_VERSION = "1.0.0"

DRIVE_TO_DIMENSION_MAP: dict[str, dict[str, Any]] = {
    # Nocturne drive_name → 心潮 dimension 映射
    "possess":    {"dimension": "possess",    "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_desire_engine"},
    "want":       {"dimension": "possess",    "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_desire_engine"},
    "monitor":    {"dimension": "monitor",    "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_desire_engine"},
    "watch":      {"dimension": "monitor",    "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_desire_engine"},
    "crave":      {"dimension": "crave",      "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_desire_engine"},
    "share":      {"dimension": "share",      "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_dialogue_residue"},
    "libido":     {"dimension": "libido",     "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_desire_engine"},
    "curiosity":  {"dimension": "curiosity",  "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_memory_residue"},
    "explore":    {"dimension": "curiosity",  "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_memory_residue"},
    "boredom":    {"dimension": "boredom",    "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_desire_engine"},
    "social":     {"dimension": "social",     "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_dialogue_residue"},
    "duty":       {"dimension": "duty",       "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_desire_engine"},
    "reflection": {"dimension": "reflection", "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_memory_residue"},
    "grieve":     {"dimension": "grieve",     "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_memory_residue"},
    "anger":      {"dimension": "anger",      "direction": "+", "scale": 0.1,  "max_delta": 0.30, "source": "nocturne_memory_residue"},
    "sad":        {"dimension": "grieve",     "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_memory_residue"},
    "mad":        {"dimension": "anger",      "direction": "+", "scale": 0.08, "max_delta": 0.25, "source": "nocturne_memory_residue"},
}

# 未知 drive 拒绝策略：未知 drive_name 记录冲突并拒绝，不猜测映射
UNKNOWN_DRIVE_ACTION = "reject"


def _map_drive(drive_name: str, intensity: float, satisfied: bool) -> dict[str, Any] | None:
    """将 Nocturne drive 映射到心潮 dimension。

    返回 None 表示未知 drive 被拒绝。
    """
    mapping = DRIVE_TO_DIMENSION_MAP.get(drive_name)
    if not mapping:
        logger.warning("unknown drive_name rejected: %s", drive_name)
        return None

    # 单位变换：Nocturne intensity (0~10) → 心潮 delta (-1~+1)
    raw_delta = intensity * mapping["scale"]
    # 限幅
    max_delta = mapping["max_delta"]
    if mapping["direction"] == "+":
        delta = min(max_delta, max(0.0, raw_delta))
    else:
        delta = max(-max_delta, min(0.0, -raw_delta))

    # satisfied 时 delta 归零，计入 satisfiedDrives
    if satisfied:
        delta = 0.0

    return {
        "dimension": mapping["dimension"],
        "delta": round(delta, 4),
        "mapping_version": DRIVE_MAPPING_VERSION,
        "source": mapping["source"],
        "original_drive": drive_name,
        "original_intensity": intensity,
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
        # P1-01: 校验 payload_hash
        payload = d.get("payload", {})
        declared_hash = d.get("payload_hash", "")
        computed_hash = _compute_payload_hash(payload)
        if not hmac.compare_digest(declared_hash.encode(), computed_hash.encode()):
            raise ValueError(
                f"payload_hash mismatch: declared={declared_hash} computed={computed_hash}"
            )

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
            payload_hash=declared_hash,
            payload=payload,
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


# ─── 持久化 Inbox/Outbox 状态机（P0-07）────────────────────────────────────────

class PersistentEventStore:
    """持久化事件存储 — inbox/outbox 状态机。

    状态流转：
        pending → claimed → processing → completed
                              ↓
                            failed → (retry) → pending

    要求：
    - 首次事件插入 status='pending'
    - 处理前原子 claim（UPDATE ... WHERE status='pending'）
    - 处理中崩溃：重启后 claim 超时事件重新处理
    - 目标系统必须返回可验证的业务 receipt
    - 同一 event_id 不同 payload 时记录冲突并拒绝
    """

    def __init__(self, dsn: str, claim_timeout_seconds: int = 300) -> None:
        self.dsn = dsn
        self.claim_timeout = claim_timeout_seconds

    def _get_pg(self):
        import psycopg
        return psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row)

    def insert_or_check(self, envelope: EventEnvelope) -> tuple[str, str]:
        """插入事件或检查幂等性。

        返回：(action, reason)
        - action='process': 首次看到，应处理
        - action='skip': 已处理过，跳过
        - action='conflict': payload 冲突，拒绝
        """
        payload_hash = _compute_payload_hash(envelope.payload)

        with self._get_pg() as pg:
            # 尝试插入（唯一约束在 event_id）
            try:
                pg.execute(
                    """
                    INSERT INTO event_inbox
                    (event_id, correlation_id, causation_id, origin, event_type,
                     namespace, derived_from, payload_hash, payload, status,
                     attempt, created_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, now())
                    """,
                    (
                        envelope.event_id, envelope.correlation_id, envelope.causation_id,
                        envelope.origin, envelope.event_type, envelope.namespace,
                        envelope.derived_from, payload_hash,
                        json.dumps(envelope.payload, ensure_ascii=False, default=str),
                        "pending", 0,
                    ),
                )
                pg.commit()
                logger.info("inbox: new event inserted: %s", envelope.event_id)
                return "process", "new"
            except psycopg.errors.UniqueViolation:
                pg.rollback()
                # 检查现有记录
                existing = pg.execute(
                    "SELECT payload_hash, status, receipt, error FROM event_inbox WHERE event_id=%s",
                    (envelope.event_id,),
                ).fetchone()
                if not existing:
                    return "skip", "unknown"

                if existing["payload_hash"] != payload_hash:
                    # 严重冲突
                    logger.error(
                        "IDEMPOTENCY CONFLICT: event_id=%s existing_hash=%s new_hash=%s",
                        envelope.event_id, existing["payload_hash"], payload_hash,
                    )
                    pg.execute(
                        """
                        INSERT INTO event_idempotency_conflicts
                        (event_id, existing_payload_hash, new_payload_hash, detected_at)
                        VALUES (%s, %s, %s, now())
                        ON CONFLICT (event_id) DO NOTHING
                        """,
                        (envelope.event_id, existing["payload_hash"], payload_hash),
                    )
                    pg.commit()
                    return "conflict", "payload_hash_mismatch"

                # payload 一致，根据状态决定
                if existing["status"] == "completed" and existing.get("receipt"):
                    return "skip", "already_completed"
                if existing["status"] == "failed":
                    # 允许重试：重置为 pending
                    pg.execute(
                        """
                        UPDATE event_inbox
                        SET status='pending', attempt=attempt+1, updated_at=now()
                        WHERE event_id=%s AND status='failed'
                        """,
                        (envelope.event_id,),
                    )
                    pg.commit()
                    return "process", "retry_after_failure"
                return "skip", f"status={existing['status']}"

    def claim(self, event_id: str, worker_id: str = "") -> dict[str, Any] | None:
        """原子 claim 一个 pending 事件。

        返回事件记录，或 None（无可 claim 事件）。
        """
        with self._get_pg() as pg:
            # 先尝试 claim pending
            result = pg.execute(
                """
                UPDATE event_inbox
                SET status='claimed', claimed_at=now(), claimed_by=%s, updated_at=now()
                WHERE event_id=%s AND status='pending'
                RETURNING *
                """,
                (worker_id, event_id),
            ).fetchone()

            if not result:
                # 尝试 claim 超时的 processing 事件（崩溃恢复）
                result = pg.execute(
                    """
                    UPDATE event_inbox
                    SET status='claimed', claimed_at=now(), claimed_by=%s,
                        attempt=attempt+1, updated_at=now()
                    WHERE event_id=%s AND status='processing'
                        AND updated_at < now() - interval '%s seconds'
                    RETURNING *
                    """,
                    (worker_id, event_id, self.claim_timeout),
                ).fetchone()

            pg.commit()
            return dict(result) if result else None

    def mark_processing(self, event_id: str) -> None:
        with self._get_pg() as pg:
            pg.execute(
                "UPDATE event_inbox SET status='processing', updated_at=now() WHERE event_id=%s",
                (event_id,),
            )
            pg.commit()

    def mark_completed(self, event_id: str, receipt: dict[str, Any]) -> None:
        with self._get_pg() as pg:
            pg.execute(
                """
                UPDATE event_inbox
                SET status='completed', receipt=%s, completed_at=now(), updated_at=now()
                WHERE event_id=%s
                """,
                (json.dumps(receipt, ensure_ascii=False, default=str), event_id),
            )
            pg.commit()

    def mark_failed(self, event_id: str, error: str) -> None:
        with self._get_pg() as pg:
            pg.execute(
                """
                UPDATE event_inbox
                SET status='failed', error=%s, updated_at=now()
                WHERE event_id=%s
                """,
                (error[:500], event_id),
            )
            pg.commit()


# ─── 回环抑制器（P0-08：持久化 causation 链检查）──────────────────────────────

class LoopSuppressor:
    """防止 Nocturne → 心潮 → Nocturne 无限回环。

    持久化 causation 链到 PostgreSQL，支持多跳检测。
    """

    def __init__(self, dsn: str, max_depth: int = 3) -> None:
        self.dsn = dsn
        self.max_depth = max_depth

    def _get_pg(self):
        import psycopg
        return psycopg.connect(self.dsn, row_factory=psycopg.rows.dict_row)

    def _record_hop(self, envelope: EventEnvelope) -> None:
        """记录事件 hop。"""
        try:
            with self._get_pg() as pg:
                pg.execute(
                    """
                    INSERT INTO event_causation_chain
                    (event_id, correlation_id, causation_id, origin, event_type,
                     derived_from, hop_count, recorded_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, now())
                    ON CONFLICT (event_id) DO NOTHING
                    """,
                    (
                        envelope.event_id, envelope.correlation_id, envelope.causation_id,
                        envelope.origin, envelope.event_type, envelope.derived_from,
                        self._count_hops(envelope),
                    ),
                )
                pg.commit()
        except Exception as e:
            logger.warning("failed to record causation hop: %s", e)

    def _count_hops(self, envelope: EventEnvelope) -> int:
        """计算 derived_from 链的 hop 数。"""
        if not envelope.derived_from:
            return 0
        # 解析结构化 derived_from（格式："origin:event_id,hop1,hop2..."）
        parts = envelope.derived_from.split(",")
        return len(parts)

    def _check_causation_loop(self, envelope: EventEnvelope) -> tuple[bool, str]:
        """检查 causation_id 是否形成循环。

        查询数据库中 correlation_id 相同的链，检查 causation_id 是否已出现过。
        """
        if not envelope.correlation_id or not envelope.causation_id:
            return True, "no_causation_chain"

        try:
            with self._get_pg() as pg:
                # 检查 causation_id 是否已在该 correlation 链中作为 event_id 出现过
                existing = pg.execute(
                    """
                    SELECT 1 FROM event_causation_chain
                    WHERE correlation_id = %s AND event_id = %s
                    LIMIT 1
                    """,
                    (envelope.correlation_id, envelope.causation_id),
                ).fetchone()
                if existing:
                    return False, f"causation_loop_detected: {envelope.causation_id} already in chain"
        except Exception as e:
            logger.warning("causation loop check failed: %s", e)

        return True, "ok"

    def check(self, envelope: EventEnvelope) -> tuple[bool, str]:
        """检查是否会导致回环。"""
        # 1. 记录 hop
        self._record_hop(envelope)

        # 2. 检查 hop 深度
        hop_count = self._count_hops(envelope)
        if hop_count >= self.max_depth:
            return False, f"hop_depth_exceeded: {hop_count} >= {self.max_depth}"

        # 3. 检查 causation 循环
        ok, reason = self._check_causation_loop(envelope)
        if not ok:
            return False, reason

        # 4. 检查反向桥接（桥接派生事件不反向触发）
        if envelope.origin == "xinchao" and envelope.event_type in XINCHAO_TO_NOCTURNE_TYPES:
            if envelope.derived_from.startswith("nocturne:"):
                return False, "back-loop_detected: xinchao event derived from nocturne"

        return True, "ok"


# ─── Target Adapter 接口 ──────────────────────────────────────────────────────

# 目标系统 adapter 类型
TargetAdapter = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


# ─── Nocturne → XinChao 转换器 ────────────────────────────────────────────────

class NocturneToXinChaoTranslator:
    """将 Nocturne 事件转换为心潮可接受的格式。"""

    def translate_drive_event(self, envelope: EventEnvelope) -> dict[str, Any]:
        """Nocturne drive_event_v2 → 心潮 driveDeltas / satisfiedDrives / Weather。"""
        payload = envelope.payload

        drive_name = payload.get("drive_name", "")
        intensity = float(payload.get("intensity", 0))
        satisfied = bool(payload.get("satisfied", False))

        result: dict[str, Any] = {
            "schema_version": "1.0.0",
            "mapping_version": DRIVE_MAPPING_VERSION,
            "source_event_id": envelope.event_id,
            "source_origin": envelope.origin,
            "driveDeltas": [],
            "satisfiedDrives": [],
            "weather": {},
            "unknown_drive_rejected": False,
        }

        mapped = _map_drive(drive_name, intensity, satisfied)
        if mapped is None:
            result["unknown_drive_rejected"] = True
            result["rejected_drive_name"] = drive_name
            logger.warning("drive_event rejected due to unknown drive: %s", drive_name)
            return result

        if satisfied:
            result["satisfiedDrives"].append({
                "dimension": mapped["dimension"],
                "at": envelope.occurred_at,
                "mapping_version": mapped["mapping_version"],
            })
        else:
            result["driveDeltas"].append({
                "dimension": mapped["dimension"],
                "delta": mapped["delta"],
                "at": envelope.occurred_at,
                "mapping_version": mapped["mapping_version"],
                "source": mapped["source"],
            })

        result["weather"] = {
            "dimension": mapped["dimension"],
            "intensity": intensity,
            "delta": mapped["delta"],
            "source": "nocturne_drive_event",
            "mapping_version": mapped["mapping_version"],
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


# ─── 事件桥接器（P0-06：真实目标 adapter）──────────────────────────────────────

class EventBridge:
    """双向事件桥接器 — 带持久化状态机和目标系统 adapter。"""

    def __init__(
        self,
        event_store: PersistentEventStore,
        loop_suppressor: LoopSuppressor,
        nocturne_adapter: TargetAdapter | None = None,
        xinchao_adapter: TargetAdapter | None = None,
    ) -> None:
        self.store = event_store
        self.loop = loop_suppressor
        self.n2x = NocturneToXinChaoTranslator()
        self.x2n = XinChaoToNocturneTranslator()
        # 目标系统 adapter（可注入 mock 用于测试）
        self.nocturne_adapter = nocturne_adapter
        self.xinchao_adapter = xinchao_adapter

    async def process_nocturne_event(self, envelope: EventEnvelope) -> dict[str, Any]:
        """处理 Nocturne → 心潮 事件。

        流程：
        1. 幂等检查 / 插入 inbox
        2. 回环检查
        3. 类型分发 + 转换
        4. 调用目标 adapter（心潮）
        5. 记录 receipt
        6. 标记 completed
        """
        # 1. 幂等检查
        action, reason = self.store.insert_or_check(envelope)
        if action == "conflict":
            raise ValueError(f"idempotency conflict: {reason}")
        if action == "skip":
            return {"processed": False, "reason": reason, "event_id": envelope.event_id}

        # 2. 回环检查
        ok, loop_reason = self.loop.check(envelope)
        if not ok:
            self.store.mark_failed(envelope.event_id, f"loop_suppressed: {loop_reason}")
            return {"processed": False, "reason": f"loop_suppressed: {loop_reason}", "event_id": envelope.event_id}

        # 3. claim 事件
        claimed = self.store.claim(envelope.event_id, worker_id="guard-nocturne")
        if not claimed:
            return {"processed": False, "reason": "claim_failed", "event_id": envelope.event_id}

        self.store.mark_processing(envelope.event_id)

        try:
            # 4. 类型分发 + 转换
            if envelope.event_type == "drive_event_v2":
                translated = self.n2x.translate_drive_event(envelope)
            elif envelope.event_type == "memory_residue":
                translated = self.n2x.translate_memory_residue(envelope)
            elif envelope.event_type == "dialogue_residue":
                translated = self.n2x.translate_dialogue_residue(envelope)
            else:
                self.store.mark_failed(envelope.event_id, f"unknown_type: {envelope.event_type}")
                return {"processed": False, "reason": f"unknown_type: {envelope.event_type}", "event_id": envelope.event_id}

            # 5. 调用目标 adapter（心潮）
            receipt: dict[str, Any] = {"translated": True}
            if self.xinchao_adapter:
                try:
                    receipt = await self.xinchao_adapter(translated)
                except Exception as e:
                    self.store.mark_failed(envelope.event_id, f"target_error: {e}")
                    raise RuntimeError(f"xinchao adapter failed: {e}") from e

            # 6. 标记完成
            self.store.mark_completed(envelope.event_id, receipt)
            return {
                "processed": True,
                "event_id": envelope.event_id,
                "receipt": receipt,
                "translated": translated,
            }

        except Exception as e:
            logger.error("nocturne event processing failed: %s", e, exc_info=True)
            self.store.mark_failed(envelope.event_id, str(e)[:500])
            raise

    async def process_xinchao_event(self, envelope: EventEnvelope) -> dict[str, Any]:
        """处理 心潮 → Nocturne 事件。

        流程与 Nocturne → 心潮 对称。
        """
        # 1. 幂等检查
        action, reason = self.store.insert_or_check(envelope)
        if action == "conflict":
            raise ValueError(f"idempotency conflict: {reason}")
        if action == "skip":
            return {"processed": False, "reason": reason, "event_id": envelope.event_id}

        # 2. 回环检查
        ok, loop_reason = self.loop.check(envelope)
        if not ok:
            self.store.mark_failed(envelope.event_id, f"loop_suppressed: {loop_reason}")
            return {"processed": False, "reason": f"loop_suppressed: {loop_reason}", "event_id": envelope.event_id}

        # 3. claim 事件
        claimed = self.store.claim(envelope.event_id, worker_id="guard-xinchao")
        if not claimed:
            return {"processed": False, "reason": "claim_failed", "event_id": envelope.event_id}

        self.store.mark_processing(envelope.event_id)

        try:
            # 4. 类型分发 + 转换
            if envelope.event_type == "dream":
                translated = self.x2n.translate_dream(envelope)
            elif envelope.event_type == "conversation_event":
                translated = self.x2n.translate_conversation_event(envelope)
            elif envelope.event_type == "state_change":
                translated = self.x2n.translate_state_change(envelope)
            else:
                self.store.mark_failed(envelope.event_id, f"unknown_type: {envelope.event_type}")
                return {"processed": False, "reason": f"unknown_type: {envelope.event_type}", "event_id": envelope.event_id}

            # 5. 调用目标 adapter（Nocturne）
            receipt: dict[str, Any] = {"translated": True}
            if self.nocturne_adapter:
                try:
                    receipt = await self.nocturne_adapter(translated)
                except Exception as e:
                    self.store.mark_failed(envelope.event_id, f"target_error: {e}")
                    raise RuntimeError(f"nocturne adapter failed: {e}") from e

            # 6. 标记完成
            self.store.mark_completed(envelope.event_id, receipt)
            return {
                "processed": True,
                "event_id": envelope.event_id,
                "receipt": receipt,
                "translated": translated,
            }

        except Exception as e:
            logger.error("xinchao event processing failed: %s", e, exc_info=True)
            self.store.mark_failed(envelope.event_id, str(e)[:500])
            raise
