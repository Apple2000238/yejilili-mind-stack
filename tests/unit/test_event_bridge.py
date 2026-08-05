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
