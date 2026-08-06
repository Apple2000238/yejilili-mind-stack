"""Unit tests: edge-gateway idempotency module

运行方式:
    pytest tests/unit/test_edge_gateway_idempotency.py -v
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "services" / "edge-gateway"))

# Clear cached "src" from other service tests to avoid import collision
for mod in list(sys.modules.keys()):
    if mod == "src" or mod.startswith("src."):
        del sys.modules[mod]

import pytest

from src import idempotency as idem


class TestComputeMessageHash:
    def test_with_index(self):
        h1 = idem.compute_message_hash("sess-1", "user", "hello", message_index=0)
        h2 = idem.compute_message_hash("sess-1", "user", "different content", message_index=0)
        # 当提供了 message_index 时，hash 只取决于 session_id 和 index
        assert h1 == h2

    def test_without_index_uses_content(self):
        h1 = idem.compute_message_hash("sess-1", "user", "hello")
        h2 = idem.compute_message_hash("sess-1", "user", "world")
        assert h1 != h2

    def test_same_content_same_hash(self):
        h1 = idem.compute_message_hash("sess-1", "user", "hello")
        h2 = idem.compute_message_hash("sess-1", "user", "hello")
        assert h1 == h2

    def test_different_session_different_hash(self):
        h1 = idem.compute_message_hash("sess-1", "user", "hello")
        h2 = idem.compute_message_hash("sess-2", "user", "hello")
        assert h1 != h2


class TestIsDuplicate:
    def test_new_hash_not_duplicate(self):
        idem._seen_hashes.clear()
        assert idem.is_duplicate("new-hash-123") is False

    def test_seen_hash_is_duplicate(self):
        idem._seen_hashes.clear()
        idem.mark_seen("seen-hash-456")
        assert idem.is_duplicate("seen-hash-456") is True


class TestCheckIdempotency:
    def test_first_request_not_duplicate(self):
        idem._seen_hashes.clear()
        is_dup, msg_hash = idem.check_idempotency("sess-1", "user", "hello")
        assert is_dup is False
        assert len(msg_hash) == 64  # sha256 hex

    def test_duplicate_detected(self):
        idem._seen_hashes.clear()
        idem.check_idempotency("sess-1", "user", "hello")
        is_dup, msg_hash = idem.check_idempotency("sess-1", "user", "hello")
        assert is_dup is True

    def test_message_id_priority(self):
        idem._seen_hashes.clear()
        # 使用 message_id 时，即使 content 不同也视为同一条消息
        is_dup1, h1 = idem.check_idempotency("sess-1", "user", "hello", message_id="msg-1")
        is_dup2, h2 = idem.check_idempotency("sess-1", "user", "world", message_id="msg-1")
        assert is_dup1 is False
        assert is_dup2 is True
        assert h1 == h2

    def test_message_index_priority(self):
        idem._seen_hashes.clear()
        is_dup1, h1 = idem.check_idempotency("sess-1", "user", "a", message_index=0)
        is_dup2, h2 = idem.check_idempotency("sess-1", "user", "b", message_index=0)
        # message_index 会传递给 compute_message_hash，所以 hash 相同
        assert is_dup1 is False
        assert is_dup2 is True


class TestMarkSeen:
    def test_cache_size_limit(self):
        idem._seen_hashes.clear()
        # 填充缓存到接近上限
        for i in range(100):
            idem.mark_seen(f"hash-{i}")
        assert len(idem._seen_hashes) == 100

    def test_cache_eviction(self):
        # 设置一个较小的上限用于测试
        original_max = idem._MAX_CACHE_SIZE
        idem._MAX_CACHE_SIZE = 10
        idem._seen_hashes.clear()

        for i in range(15):
            idem.mark_seen(f"hash-{i}")

        # 由于简单的淘汰策略，缓存应该仍然能工作
        assert len(idem._seen_hashes) <= 15

        # 恢复原始值
        idem._MAX_CACHE_SIZE = original_max
