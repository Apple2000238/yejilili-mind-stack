"""Unit tests: adapter validation logic and helper functions.

运行方式:
    pytest tests/unit/test_adapter_validation.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# 将 services/nocturne-adapter 加入路径，以包形式导入
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "nocturne-adapter"))

import pytest

from src.mcp_bridge import (
    SOURCE_ALLOWLIST,
    _event_id,
    _extract_target_ref,
    _extract_text,
    _validate_breath,
    _validate_hold,
)
from src.config import Config


# ─── Fixtures ─────────────────────────────────────────────────────────────────

@pytest.fixture
def cfg() -> Config:
    return Config(
        adapter_port=8001,
        adapter_host="0.0.0.0",
        nocturne_url="http://nocturne:8000",
        mcp_adapter_token="test-token",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_db="test",
        postgres_user="test",
        postgres_password="test",
        log_level="INFO",
    )


class FakeCaller:
    def __init__(self, subject: str):
        self.subject = subject


# ─── _validate_breath ─────────────────────────────────────────────────────────

class TestValidateBreath:
    def test_defaults(self, cfg: Config):
        q, mr, mt = _validate_breath(None, None, None, cfg)
        assert q == ""
        assert mr == cfg.breath_default_max_results
        assert mt == cfg.breath_default_max_tokens

    def test_valid_inputs(self, cfg: Config):
        q, mr, mt = _validate_breath("memory", 10, 1500, cfg)
        assert q == "memory"
        assert mr == 10
        assert mt == 1500

    def test_max_results_too_high(self, cfg: Config):
        with pytest.raises(ValueError, match="max_results must be between 1 and"):
            _validate_breath("test", 99, 1000, cfg)

    def test_max_results_zero(self, cfg: Config):
        with pytest.raises(ValueError, match="max_results must be between 1 and"):
            _validate_breath("test", 0, 1000, cfg)

    def test_max_results_bool_rejected(self, cfg: Config):
        with pytest.raises(ValueError, match="max_results must be an integer"):
            _validate_breath("test", True, 1000, cfg)

    def test_max_tokens_too_low(self, cfg: Config):
        with pytest.raises(ValueError, match="max_tokens must be between 100 and"):
            _validate_breath("test", 5, 50, cfg)

    def test_max_tokens_too_high(self, cfg: Config):
        with pytest.raises(ValueError, match="max_tokens must be between 100 and"):
            _validate_breath("test", 5, 99999, cfg)

    def test_max_tokens_bool_rejected(self, cfg: Config):
        with pytest.raises(ValueError, match="max_tokens must be an integer"):
            _validate_breath("test", 5, True, cfg)

    def test_max_tokens_string_rejected(self, cfg: Config):
        with pytest.raises(ValueError, match="max_tokens must be an integer"):
            _validate_breath("test", 5, "1000", cfg)

    def test_query_whitespace_trimmed(self, cfg: Config):
        q, _, _ = _validate_breath("  memory  ", 5, 1000, cfg)
        assert q == "memory"

    def test_query_non_string_becomes_empty(self, cfg: Config):
        q, _, _ = _validate_breath(12345, 5, 1000, cfg)
        assert q == ""


# ─── _validate_hold ───────────────────────────────────────────────────────────

class TestValidateHold:
    def test_valid_minimal(self):
        c, t, i, a, s = _validate_hold("hello", None, None, None, None)
        assert c == "hello"
        assert t == ""
        assert i == 5  # default
        assert a is None
        assert s == ""

    def test_content_required(self):
        with pytest.raises(ValueError, match="content is required"):
            _validate_hold("", None, None, None, None)

    def test_content_none_rejected(self):
        with pytest.raises(ValueError, match="content is required"):
            _validate_hold(None, None, None, None, None)

    def test_content_whitespace_only_rejected(self):
        with pytest.raises(ValueError, match="content is required"):
            _validate_hold("   ", None, None, None, None)

    def test_importance_range_low(self):
        with pytest.raises(ValueError, match="importance must be between 1 and 10"):
            _validate_hold("hello", None, 0, None, None)

    def test_importance_range_high(self):
        with pytest.raises(ValueError, match="importance must be between 1 and 10"):
            _validate_hold("hello", None, 11, None, None)

    def test_importance_bool_rejected(self):
        with pytest.raises(ValueError, match="importance must be an integer"):
            _validate_hold("hello", None, True, None, None)

    def test_importance_float_rejected(self):
        with pytest.raises(ValueError, match="importance must be an integer"):
            _validate_hold("hello", None, 5.5, None, None)

    def test_auto_bool_true_accepted(self):
        _, _, _, a, _ = _validate_hold("hello", None, None, True, None)
        assert a is True

    def test_auto_bool_false_accepted(self):
        _, _, _, a, _ = _validate_hold("hello", None, None, False, None)
        assert a is False

    def test_auto_int_rejected(self):
        with pytest.raises(ValueError, match="auto must be a boolean"):
            _validate_hold("hello", None, None, 1, None)

    def test_auto_string_rejected(self):
        with pytest.raises(ValueError, match="auto must be a boolean"):
            _validate_hold("hello", None, None, "true", None)

    def test_source_allowlist_accepted(self):
        for src in SOURCE_ALLOWLIST:
            _, _, _, _, s = _validate_hold("hello", None, None, None, src)
            assert s == src

    def test_source_not_in_allowlist(self):
        with pytest.raises(ValueError, match="is not in allowlist"):
            _validate_hold("hello", None, None, None, "evil-source")

    def test_source_empty_string_accepted(self):
        _, _, _, _, s = _validate_hold("hello", None, None, None, "")
        assert s == ""

    def test_source_none_accepted(self):
        _, _, _, _, s = _validate_hold("hello", None, None, None, None)
        assert s == ""

    def test_source_special_chars_rejected(self):
        with pytest.raises(ValueError, match="alphanumeric with hyphens/underscores"):
            _validate_hold("hello", None, None, None, "evil<source>")

    def test_source_too_long(self):
        with pytest.raises(ValueError, match="≤64 characters"):
            _validate_hold("hello", None, None, None, "a" * 65)

    def test_tags_non_string_rejected(self):
        with pytest.raises(ValueError, match="tags must be a string"):
            _validate_hold("hello", 123, None, None, None)


# ─── _extract_target_ref ──────────────────────────────────────────────────────

class TestExtractTargetRef:
    def test_bucket_id_in_result(self):
        result = {"result": {"bucket_id": "abc123xyz"}}
        ref = _extract_target_ref(result, "content")
        assert ref == "abc123xyz"

    def test_memory_id_in_content_metadata(self):
        result = {
            "result": {
                "content": [
                    {"type": "text", "text": "ok", "metadata": {"memory_id": "mem-42"}}
                ]
            }
        }
        ref = _extract_target_ref(result, "content")
        assert ref == "mem-42"

    def test_id_field_at_top_level(self):
        result = {"id": "top-level-id"}
        ref = _extract_target_ref(result, "content")
        assert ref == "top-level-id"

    def test_fallback_to_content_hash(self):
        result = {"result": {"content": [{"type": "text", "text": "ok"}]}}
        ref = _extract_target_ref(result, "my-content")
        # 应该是内容 hash 的前 24 位
        import hashlib
        expected = hashlib.sha256("my-content".encode()).hexdigest()[:24]
        assert ref == expected

    def test_empty_result_fallback(self):
        ref = _extract_target_ref({}, "test-content")
        import hashlib
        expected = hashlib.sha256("test-content".encode()).hexdigest()[:24]
        assert ref == expected


# ─── _extract_text ────────────────────────────────────────────────────────────

class TestExtractText:
    def test_from_result_content_list(self):
        result = {"result": {"content": [{"type": "text", "text": "hello"}]}}
        assert _extract_text(result) == "hello"

    def test_from_top_level_content(self):
        result = {"content": [{"type": "text", "text": "world"}]}
        assert _extract_text(result) == "world"

    def test_multiple_text_blocks(self):
        result = {"result": {"content": [
            {"type": "text", "text": "a"},
            {"type": "text", "text": "b"},
        ]}}
        assert _extract_text(result) == "a\nb"

    def test_empty_result(self):
        assert _extract_text({}) == ""


# ─── _event_id ────────────────────────────────────────────────────────────────

class TestEventId:
    def test_deterministic(self):
        caller = FakeCaller("test-caller")
        inp = {"content": "hello"}
        e1 = _event_id(caller, "hold", inp)
        e2 = _event_id(caller, "hold", inp)
        assert e1 == e2
        assert len(e1) == 32

    def test_different_inputs_different_ids(self):
        caller = FakeCaller("test-caller")
        e1 = _event_id(caller, "hold", {"content": "a"})
        e2 = _event_id(caller, "hold", {"content": "b"})
        assert e1 != e2

    def test_different_callers_different_ids(self):
        c1 = FakeCaller("alice")
        c2 = FakeCaller("bob")
        inp = {"content": "hello"}
        e1 = _event_id(c1, "hold", inp)
        e2 = _event_id(c2, "hold", inp)
        assert e1 != e2
