"""Unit tests: edge-gateway session module

运行方式:
    pytest tests/unit/test_edge_gateway_session.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "edge-gateway"))

import pytest

from src.session import (
    extract_session_id,
    get_or_create_namespace,
    resolve_session_namespace,
    _stable_hash,
)


class TestExtractSessionId:
    def test_from_body_session_id(self):
        body = {"session_id": "sess-123"}
        assert extract_session_id(body, {}) == "sess-123"

    def test_from_body_sessionId_camelcase(self):
        body = {"sessionId": "sess-456"}
        assert extract_session_id(body, {}) == "sess-456"

    def test_from_metadata(self):
        body = {"metadata": {"session_id": "sess-789"}}
        assert extract_session_id(body, {}) == "sess-789"

    def test_from_metadata_conversation_id(self):
        body = {"metadata": {"conversation_id": "conv-abc"}}
        assert extract_session_id(body, {}) == "conv-abc"

    def test_from_header_x_session_id(self):
        body = {}
        headers = {"x-session-id": "hdr-sess"}
        assert extract_session_id(body, headers) == "hdr-sess"

    def test_from_header_x_conversation_id(self):
        body = {}
        headers = {"x-conversation-id": "hdr-conv"}
        assert extract_session_id(body, headers) == "hdr-conv"

    def test_body_priority_over_header(self):
        body = {"session_id": "body-sess"}
        headers = {"x-session-id": "hdr-sess"}
        assert extract_session_id(body, headers) == "body-sess"

    def test_none_returned_when_missing(self):
        assert extract_session_id({}, {}) is None

    def test_empty_string_ignored(self):
        body = {"session_id": ""}
        assert extract_session_id(body, {}) is None


class TestGetOrCreateNamespace:
    def test_deterministic_namespace(self):
        ns1 = get_or_create_namespace("sess-1", "discord", "room-a")
        ns2 = get_or_create_namespace("sess-1", "discord", "room-a")
        assert ns1 == ns2
        assert ns1.startswith("discord/room-a/")

    def test_different_session_different_namespace(self):
        ns1 = get_or_create_namespace("sess-1", "discord", "room-a")
        ns2 = get_or_create_namespace("sess-2", "discord", "room-a")
        assert ns1 != ns2

    def test_cache_returns_same(self):
        # 清除缓存后测试
        from src import session
        session._namespace_cache.clear()
        ns1 = get_or_create_namespace("sess-cache", "web", "main")
        ns2 = get_or_create_namespace("sess-cache", "web", "main")
        assert ns1 is ns2  # 应该是同一个对象（来自缓存）


class TestResolveSessionNamespace:
    def test_full_resolution(self):
        body = {
            "session_id": "sess-1",
            "metadata": {"platform": "discord", "room": "private"},
        }
        headers = {}
        sid, ns = resolve_session_namespace(body, headers)
        assert sid == "sess-1"
        assert ns.startswith("discord/private/")

    def test_header_platform_override(self):
        from src import session
        session._namespace_cache.clear()
        body = {"session_id": "sess-hdr", "metadata": {"platform": "web"}}
        headers = {"x-platform": "mobile"}
        sid, ns = resolve_session_namespace(body, headers)
        # header 优先级应该更高
        assert ns.startswith("mobile/")

    def test_no_session_returns_none(self):
        body = {}
        headers = {}
        sid, ns = resolve_session_namespace(body, headers)
        assert sid is None
        assert ns is None

    def test_default_platform_unknown(self):
        body = {"session_id": "sess-x"}
        sid, ns = resolve_session_namespace(body, {})
        assert ns.startswith("unknown/")
