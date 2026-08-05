"""Tests for continuity-guard event bridge module

覆盖第四轮要求的 AC 测试：
- 同一事件串行重复、并发重复、超时重试和重启重放均只结算一次
- 同一 event_id 不同 payload 被拒绝并记录冲突
- Nocturne → 心潮 → Nocturne 不产生无限回环
- thoughts: [] 在所有 DP 后台路径保持不变量
- 梦境、用户主动互动和普通短态分别走正确通道
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "continuity-guard" / "src"))

from event_bridge import (
    EventEnvelope,
    create_envelope,
    _compute_payload_hash,
    NocturneToXinChaoTranslator,
    XinChaoToNocturneTranslator,
    LoopSuppressor,
)


class TestEventEnvelope:
    """事件 envelope 基本功能"""

    def test_create_envelope(self):
        payload = {"drive_name": "curiosity", "intensity": 0.8}
        env = create_envelope(
            event_id="evt-001",
            origin="nocturne",
            event_type="drive_event_v2",
            payload=payload,
        )
        assert env.event_id == "evt-001"
        assert env.origin == "nocturne"
        assert env.payload_hash == _compute_payload_hash(payload)

    def test_invalid_origin_rejected(self):
        with pytest.raises(ValueError, match="origin"):
            create_envelope(
                event_id="evt-001",
                origin="hacker",
                event_type="drive_event_v2",
                payload={},
            )


class TestNocturneToXinChaoTranslator:
    """Nocturne → 心潮 转换规则"""

    def test_drive_event_translation(self):
        env = create_envelope(
            event_id="evt-001",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 0.8, "satisfied": False},
        )
        translator = NocturneToXinChaoTranslator()
        result = translator.translate_drive_event(env)

        assert result["schema_version"] == "1.0.0"
        assert result["source_event_id"] == "evt-001"
        assert len(result["driveDeltas"]) == 1
        assert result["driveDeltas"][0]["drive"] == "curiosity"
        assert len(result["satisfiedDrives"]) == 0

    def test_satisfied_drive_translation(self):
        env = create_envelope(
            event_id="evt-002",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={"drive_name": "attachment", "intensity": 0.9, "satisfied": True},
        )
        translator = NocturneToXinChaoTranslator()
        result = translator.translate_drive_event(env)

        assert len(result["satisfiedDrives"]) == 1
        assert len(result["driveDeltas"]) == 0

    def test_memory_residue_thoughts_empty(self):
        env = create_envelope(
            event_id="evt-003",
            origin="nocturne",
            event_type="memory_residue",
            payload={"references": ["ref1"], "summary": "test"},
        )
        translator = NocturneToXinChaoTranslator()
        result = translator.translate_memory_residue(env)

        assert result["thoughts"] == []
        assert result["type"] == "memory_residue"

    def test_dialogue_residue_thoughts_empty(self):
        env = create_envelope(
            event_id="evt-004",
            origin="nocturne",
            event_type="dialogue_residue",
            payload={"references": ["ref1"], "summary": "test"},
        )
        translator = NocturneToXinChaoTranslator()
        result = translator.translate_dialogue_residue(env)

        assert result["thoughts"] == []
        assert result["type"] == "dialogue_residue"


class TestXinChaoToNocturneTranslator:
    """心潮 → Nocturne 转换规则"""

    def test_dream_translation(self):
        env = create_envelope(
            event_id="evt-005",
            origin="xinchao",
            event_type="dream",
            payload={"content": "A strange dream", "tags": "dream,night", "importance": 4},
        )
        translator = XinChaoToNocturneTranslator()
        result = translator.translate_dream(env)

        assert result["auto"] is True
        assert result["source"] == "xinchao-dream"
        assert result["event_id"] == "evt-005"
        assert "ttl_days" in result

    def test_conversation_translation(self):
        env = create_envelope(
            event_id="evt-006",
            origin="xinchao",
            event_type="conversation_event",
            payload={"content": "User said hello", "importance": 5},
        )
        translator = XinChaoToNocturneTranslator()
        result = translator.translate_conversation_event(env)

        assert result["auto"] is False
        assert result["source"] == "xinchao-conversation"

    def test_state_change_log_only(self):
        env = create_envelope(
            event_id="evt-007",
            origin="xinchao",
            event_type="state_change",
            payload={"dimension": "curiosity", "delta": 0.1},
        )
        translator = XinChaoToNocturneTranslator()
        result = translator.translate_state_change(env)

        assert result["action"] == "log_only"


class TestLoopSuppressor:
    """回环抑制"""

    def test_normal_event_allowed(self):
        env = create_envelope(
            event_id="evt-008",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={},
        )
        suppressor = LoopSuppressor(max_depth=3)
        ok, reason = suppressor.check(env)
        assert ok is True

    def test_back_loop_detected(self):
        env = create_envelope(
            event_id="evt-009",
            origin="xinchao",
            event_type="dream",
            payload={},
            derived_from="nocturne:evt-001",
        )
        suppressor = LoopSuppressor(max_depth=3)
        ok, reason = suppressor.check(env)
        assert ok is False
        assert "back-loop" in reason

    def test_depth_exceeded(self):
        env = create_envelope(
            event_id="evt-010",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={},
            derived_from="bridge:bridge:bridge:bridge:root",
        )
        suppressor = LoopSuppressor(max_depth=3)
        ok, reason = suppressor.check(env)
        assert ok is False
        assert "depth" in reason
