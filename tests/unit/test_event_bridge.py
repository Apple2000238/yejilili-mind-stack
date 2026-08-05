"""Tests for event_bridge — 持久化状态机、目标 adapter、并发重试

覆盖场景：
- 持久化 inbox/outbox 状态机（P0-07）
- 目标 adapter 业务 receipt（P0-06）
- payload_hash 校验（P1-01）
- Drive → 心潮映射（P0-09）
- 回环抑制（P0-08）
"""

from __future__ import annotations

import pytest
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "continuity-guard" / "src"))


class TestDriveMapping:
    """P0-09: Drive → 心潮十二维版本化映射"""

    def test_curiosity_maps_correctly(self):
        from event_bridge import _map_drive
        result = _map_drive("curiosity", 5.0, False)
        assert result is not None
        assert result["dimension"] == "curiosity"
        assert result["delta"] > 0
        assert result["mapping_version"] == "1.0.0"

    def test_unknown_drive_returns_none(self):
        from event_bridge import _map_drive
        assert _map_drive("nonexistent_drive", 5.0, False) is None

    def test_satisfied_drive_has_zero_delta(self):
        from event_bridge import _map_drive
        result = _map_drive("possess", 8.0, True)
        assert result["delta"] == 0.0

    def test_delta_within_max_limit(self):
        from event_bridge import _map_drive
        result = _map_drive("curiosity", 100.0, False)
        assert result["delta"] <= 0.30


class TestNocturneToXinChaoTranslator:
    """Nocturne → 心潮转换器测试"""

    def test_translate_drive_event_with_mapping(self):
        from event_bridge import NocturneToXinChaoTranslator, create_envelope
        translator = NocturneToXinChaoTranslator()
        envelope = create_envelope(
            event_id="e1",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 5.0, "satisfied": False},
        )
        result = translator.translate_drive_event(envelope)
        assert result["driveDeltas"]
        assert result["driveDeltas"][0]["dimension"] == "curiosity"
        assert "thoughts" not in result

    def test_translate_memory_residue_thoughts_empty(self):
        from event_bridge import NocturneToXinChaoTranslator, create_envelope
        translator = NocturneToXinChaoTranslator()
        envelope = create_envelope(
            event_id="e2",
            origin="nocturne",
            event_type="memory_residue",
            payload={"summary": "test", "references": []},
        )
        result = translator.translate_memory_residue(envelope)
        assert result["thoughts"] == []


class TestXinChaoToNocturneTranslator:
    """心潮 → Nocturne 转换器测试"""

    def test_translate_dream_has_auto_and_source(self):
        from event_bridge import XinChaoToNocturneTranslator, create_envelope
        translator = XinChaoToNocturneTranslator()
        envelope = create_envelope(
            event_id="e3",
            origin="xinchao",
            event_type="dream",
            payload={"content": "dream content"},
        )
        result = translator.translate_dream(envelope)
        assert result["auto"] is True
        assert result["source"] == "xinchao-dream"
        assert result["event_id"] == "e3"

    def test_translate_state_change_is_log_only(self):
        from event_bridge import XinChaoToNocturneTranslator, create_envelope
        translator = XinChaoToNocturneTranslator()
        envelope = create_envelope(
            event_id="e4",
            origin="xinchao",
            event_type="state_change",
            payload={"mood": "happy"},
        )
        result = translator.translate_state_change(envelope)
        assert result["action"] == "log_only"


class TestEventEnvelope:
    """P1-01: payload_hash 校验"""

    def test_valid_hash_accepted(self):
        from event_bridge import EventEnvelope, _compute_payload_hash
        payload = {"key": "value"}
        envelope = EventEnvelope.from_dict({
            "event_id": "e1",
            "origin": "nocturne",
            "event_type": "test",
            "payload_hash": _compute_payload_hash(payload),
            "payload": payload,
        })
        assert envelope.event_id == "e1"

    def test_invalid_hash_rejected(self):
        from event_bridge import EventEnvelope
        with pytest.raises(ValueError, match="payload_hash mismatch"):
            EventEnvelope.from_dict({
                "event_id": "e1",
                "origin": "nocturne",
                "event_type": "test",
                "payload_hash": "invalid_hash",
                "payload": {"key": "value"},
            })

    def test_unknown_origin_rejected(self):
        from event_bridge import EventEnvelope, _compute_payload_hash
        payload = {"key": "value"}
        with pytest.raises(ValueError, match="allowlist"):
            EventEnvelope.from_dict({
                "event_id": "e1",
                "origin": "unknown",
                "event_type": "test",
                "payload_hash": _compute_payload_hash(payload),
                "payload": payload,
            })


class TestLoopSuppressor:
    """P0-08: 回环抑制"""

    def test_shallow_chain_allowed(self):
        from event_bridge import LoopSuppressor, create_envelope
        suppressor = LoopSuppressor("", max_depth=3)
        envelope = create_envelope(
            event_id="e1",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 1},
        )
        ok, reason = suppressor.check(envelope)
        assert ok is True

    def test_deep_chain_rejected(self):
        from event_bridge import LoopSuppressor, create_envelope
        suppressor = LoopSuppressor("", max_depth=3)
        envelope = create_envelope(
            event_id="e1",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 1},
            derived_from="a:1,b:2,c:3,d:4",
        )
        ok, reason = suppressor.check(envelope)
        assert ok is False
        assert "depth" in reason

    def test_back_loop_rejected(self):
        from event_bridge import LoopSuppressor, create_envelope
        suppressor = LoopSuppressor("", max_depth=3)
        envelope = create_envelope(
            event_id="e1",
            origin="xinchao",
            event_type="conversation_event",
            payload={"content": "hi"},
            derived_from="nocturne:e0",
        )
        ok, reason = suppressor.check(envelope)
        assert ok is False
        assert "back-loop" in reason


class TestPersistentEventStore:
    """P0-07: 持久化 inbox/outbox 状态机（真实数据库行为模拟）"""

    def _mock_store(self, existing_rows=None):
        """创建带 mock psycopg 的 PersistentEventStore。"""
        from unittest.mock import MagicMock, patch
        from event_bridge import PersistentEventStore, EventEnvelope, _compute_payload_hash

        store = PersistentEventStore("fake-dsn", claim_timeout_seconds=300)
        mock_pg = MagicMock()
        mock_cursor = MagicMock()
        mock_pg.execute = mock_cursor.execute
        mock_pg.commit = MagicMock()
        mock_pg.rollback = MagicMock()

        # 设置 fetchone / fetchall 返回值
        def _fetchone():
            return mock_cursor._fetchone_result
        def _fetchall():
            return mock_cursor._fetchall_result
        mock_cursor.fetchone = _fetchone
        mock_cursor.fetchall = _fetchall
        mock_pg.__enter__ = MagicMock(return_value=mock_pg)
        mock_pg.__exit__ = MagicMock(return_value=False)

        store._get_pg = lambda: mock_pg
        return store, mock_pg, mock_cursor

    def test_insert_new_event_returns_process(self):
        from event_bridge import PersistentEventStore, EventEnvelope, _compute_payload_hash
        store, mock_pg, cursor = self._mock_store()
        cursor.execute.side_effect = lambda *a, **k: None  # 首次插入无异常

        payload = {"drive_name": "curiosity", "intensity": 5}
        envelope = EventEnvelope(
            schema_version="1.0.0", event_id="e1", correlation_id="c1", causation_id="",
            origin="nocturne", event_type="drive_event_v2",
            occurred_at="2026-08-06T00:00:00+0800", received_at="2026-08-06T00:00:00+0800",
            namespace="", derived_from="",
            payload_hash=_compute_payload_hash(payload), payload=payload,
        )
        action, reason = store.insert_or_check(envelope)
        assert action == "process"
        assert reason == "new"

    def test_insert_duplicate_with_same_payload_returns_skip(self):
        from event_bridge import PersistentEventStore, EventEnvelope, _compute_payload_hash
        store, mock_pg, cursor = self._mock_store()
        payload = {"drive_name": "curiosity", "intensity": 5}
        phash = _compute_payload_hash(payload)

        # 第一次调用抛 UniqueViolation，第二次查询返回 existing
        call_count = [0]
        def _execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("unique violation")
            # 第二次查询
            cursor._fetchone_result = {"payload_hash": phash, "status": "completed", "receipt": "{}", "error": None}
        cursor.execute = _execute

        envelope = EventEnvelope(
            schema_version="1.0.0", event_id="e1", correlation_id="c1", causation_id="",
            origin="nocturne", event_type="drive_event_v2",
            occurred_at="2026-08-06T00:00:00+0800", received_at="2026-08-06T00:00:00+0800",
            namespace="", derived_from="",
            payload_hash=phash, payload=payload,
        )
        action, reason = store.insert_or_check(envelope)
        assert action == "skip"
        assert "already_completed" in reason

    def test_insert_duplicate_with_different_payload_returns_conflict(self):
        from event_bridge import PersistentEventStore, EventEnvelope, _compute_payload_hash
        store, mock_pg, cursor = self._mock_store()
        payload = {"drive_name": "curiosity", "intensity": 5}
        phash = _compute_payload_hash(payload)

        call_count = [0]
        def _execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("unique violation")
            cursor._fetchone_result = {"payload_hash": "different_hash", "status": "completed", "receipt": "{}", "error": None}
        cursor.execute = _execute

        envelope = EventEnvelope(
            schema_version="1.0.0", event_id="e1", correlation_id="c1", causation_id="",
            origin="nocturne", event_type="drive_event_v2",
            occurred_at="2026-08-06T00:00:00+0800", received_at="2026-08-06T00:00:00+0800",
            namespace="", derived_from="",
            payload_hash=phash, payload=payload,
        )
        action, reason = store.insert_or_check(envelope)
        assert action == "conflict"
        assert "mismatch" in reason

    def test_claim_pending_event(self):
        from event_bridge import PersistentEventStore
        store, mock_pg, cursor = self._mock_store()
        cursor._fetchone_result = {"event_id": "e1", "status": "claimed", "payload": "{}"}

        result = store.claim("e1", worker_id="w1")
        assert result is not None
        assert result["status"] == "claimed"

    def test_claim_claimed_timeout_event(self):
        from event_bridge import PersistentEventStore
        store, mock_pg, cursor = self._mock_store()
        # 第一次 pending 无结果，第二次 processing 无结果，第三次 claimed 超时成功
        call_count = [0]
        def _execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] <= 2:
                cursor._fetchone_result = None
            else:
                cursor._fetchone_result = {"event_id": "e1", "status": "claimed", "payload": "{}"}
        cursor.execute = _execute

        result = store.claim("e1", worker_id="w1")
        assert result is not None
        assert result["status"] == "claimed"

    def test_failed_event_reset_to_pending(self):
        from event_bridge import PersistentEventStore, EventEnvelope, _compute_payload_hash
        store, mock_pg, cursor = self._mock_store()
        payload = {"drive_name": "curiosity", "intensity": 5}
        phash = _compute_payload_hash(payload)

        call_count = [0]
        def _execute(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise Exception("unique violation")
            cursor._fetchone_result = {"payload_hash": phash, "status": "failed", "receipt": None, "error": "old error"}
        cursor.execute = _execute

        envelope = EventEnvelope(
            schema_version="1.0.0", event_id="e1", correlation_id="c1", causation_id="",
            origin="nocturne", event_type="drive_event_v2",
            occurred_at="2026-08-06T00:00:00+0800", received_at="2026-08-06T00:00:00+0800",
            namespace="", derived_from="",
            payload_hash=phash, payload=payload,
        )
        action, reason = store.insert_or_check(envelope)
        assert action == "process"
        assert "retry" in reason


class TestEventBridgeAdapterInjection:
    """P0-06: 目标 adapter 注入与 receipt 断言"""

    @pytest.mark.asyncio
    async def test_nocturne_event_with_mock_adapter(self):
        from event_bridge import EventBridge, PersistentEventStore, LoopSuppressor, create_envelope
        from unittest.mock import MagicMock, AsyncMock

        store = MagicMock(spec=PersistentEventStore)
        store.insert_or_check.return_value = ("process", "new")
        store.claim.return_value = {"event_id": "e1", "status": "claimed"}

        loop = MagicMock(spec=LoopSuppressor)
        loop.check.return_value = (True, "ok")

        async def mock_adapter(payload):
            return {"accepted": True, "dimension_delta": payload["driveDeltas"][0]["dimension"]}

        bridge = EventBridge(store, loop, xinchao_adapter=mock_adapter)
        envelope = create_envelope(
            event_id="e1", origin="nocturne", event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 5.0, "satisfied": False},
        )
        result = await bridge.process_nocturne_event(envelope)
        assert result["processed"] is True
        assert result["receipt"]["accepted"] is True
        store.mark_completed.assert_called_once()

    @pytest.mark.asyncio
    async def test_xinchao_event_with_mock_adapter(self):
        from event_bridge import EventBridge, PersistentEventStore, LoopSuppressor, create_envelope
        from unittest.mock import MagicMock

        store = MagicMock(spec=PersistentEventStore)
        store.insert_or_check.return_value = ("process", "new")
        store.claim.return_value = {"event_id": "e1", "status": "claimed"}

        loop = MagicMock(spec=LoopSuppressor)
        loop.check.return_value = (True, "ok")

        async def mock_adapter(payload):
            return {"hold_created": True, "ref": "hold-123"}

        bridge = EventBridge(store, loop, nocturne_adapter=mock_adapter)
        envelope = create_envelope(
            event_id="e1", origin="xinchao", event_type="dream",
            payload={"content": "a dream"},
        )
        result = await bridge.process_xinchao_event(envelope)
        assert result["processed"] is True
        assert result["receipt"]["hold_created"] is True

    @pytest.mark.asyncio
    async def test_adapter_failure_marks_failed(self):
        from event_bridge import EventBridge, PersistentEventStore, LoopSuppressor, create_envelope
        from unittest.mock import MagicMock

        store = MagicMock(spec=PersistentEventStore)
        store.insert_or_check.return_value = ("process", "new")
        store.claim.return_value = {"event_id": "e1", "status": "claimed"}

        loop = MagicMock(spec=LoopSuppressor)
        loop.check.return_value = (True, "ok")

        async def failing_adapter(payload):
            raise RuntimeError("target down")

        bridge = EventBridge(store, loop, xinchao_adapter=failing_adapter)
        envelope = create_envelope(
            event_id="e1", origin="nocturne", event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 5.0, "satisfied": False},
        )
        with pytest.raises(RuntimeError, match="target down"):
            await bridge.process_nocturne_event(envelope)
        store.mark_failed.assert_called_once()


class TestEventBridgeConcurrency:
    """并发重复提交测试"""

    @pytest.mark.asyncio
    async def test_duplicate_event_skip(self):
        from event_bridge import EventBridge, PersistentEventStore, LoopSuppressor, create_envelope
        from unittest.mock import MagicMock

        store = MagicMock(spec=PersistentEventStore)
        store.insert_or_check.return_value = ("skip", "already_completed")

        loop = MagicMock(spec=LoopSuppressor)
        bridge = EventBridge(store, loop)

        envelope = create_envelope(
            event_id="e1", origin="nocturne", event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 5.0, "satisfied": False},
        )
        result = await bridge.process_nocturne_event(envelope)
        assert result["processed"] is False
        assert "already_completed" in result["reason"]
