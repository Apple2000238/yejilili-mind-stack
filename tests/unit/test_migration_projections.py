"""R6-07: 六类非零投影验证测试

使用合成数据验证 identity/memory/message/summary/promise/affect
六类投影在导入后均有非零计数。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "migration-cli" / "src"))


class TestSixProjectionsNonZero:
    """六类投影非零计数验证"""

    def _make_mock_pg(self):
        """创建记录所有 execute 调用的 mock PostgreSQL 连接。"""
        pg = MagicMock()
        pg.execute = MagicMock()
        pg.commit = MagicMock()
        return pg

    def test_identity_projection_non_zero(self):
        """identity_projection: persona + memory_layers → 非零计数"""
        from main import _insert_identity_projection

        pg = self._make_mock_pg()
        records = [
            {"source_table": "persona", "source_pk": "1", "payload_json": {"name": "梨梨", "role": "user", "content": "test"}, "payload_hash": "h1"},
            {"source_table": "memory_layers", "source_pk": "2", "payload_json": {"layer_type": "core", "layer_key": "identity", "content": "bedrock"}, "payload_hash": "h2"},
        ]
        count = _insert_identity_projection(pg, "run-test", "v1", records)
        assert count > 0, "identity_projection must have non-zero count"
        assert pg.execute.call_count == 2

    def test_memory_projection_non_zero(self):
        """memory_projection: ar_buckets → 非零计数"""
        from main import _insert_memory_projection

        pg = self._make_mock_pg()
        records = [
            {"source_table": "ar_buckets", "source_pk": "1", "payload_json": {"name": "bucket1", "type": "memory", "anchor": True}, "payload_hash": "h1"},
        ]
        count = _insert_memory_projection(pg, "run-test", "v1", records)
        assert count > 0, "memory_projection must have non-zero count"
        assert pg.execute.call_count == 1

    def test_message_projection_non_zero(self):
        """message_projection: message_archive + message_buffer + chat_sessions → 非零计数"""
        from main import _insert_message_projection

        pg = self._make_mock_pg()
        records = [
            {"source_table": "message_archive", "source_pk": "1", "payload_json": {"role": "user", "content": "hello", "session_id": "s1"}, "payload_hash": "h1"},
            {"source_table": "message_buffer", "source_pk": "2", "payload_json": {"content": "buffered"}, "payload_hash": "h2"},
            {"source_table": "chat_sessions", "source_pk": "3", "payload_json": {"session_id": "s1", "room": "main"}, "payload_hash": "h3"},
        ]
        count = _insert_message_projection(pg, "run-test", "v1", records)
        assert count > 0, "message_projection must have non-zero count"
        assert pg.execute.call_count == 3

    def test_summary_projection_non_zero(self):
        """summary_projection: daily_summaries + weekly_summaries → 非零计数"""
        from main import _insert_summary_projection

        pg = self._make_mock_pg()
        records = [
            {"source_table": "daily_summaries", "source_pk": "1", "payload_json": {"summary_text": "today was good", "batch_id": "b1"}, "payload_hash": "h1"},
            {"source_table": "weekly_summaries", "source_pk": "2", "payload_json": {"summary_text": "week recap"}, "payload_hash": "h2"},
        ]
        count = _insert_summary_projection(pg, "run-test", "v1", records)
        assert count > 0, "summary_projection must have non-zero count"
        assert pg.execute.call_count == 2

    def test_promise_projection_non_zero(self):
        """promise_projection: promises → 非零计数"""
        from main import _insert_promise_projection

        pg = self._make_mock_pg()
        records = [
            {"source_table": "promises", "source_pk": "1", "payload_json": {"promise_text": "always be kind", "status": "active"}, "payload_hash": "h1"},
        ]
        count = _insert_promise_projection(pg, "run-test", "v1", records)
        assert count > 0, "promise_projection must have non-zero count"
        assert pg.execute.call_count == 1

    def test_affect_projection_non_zero(self):
        """affect_projection: ar_dreams + ar_whispers + diary + knots + ar_state + proactive_messages → 非零计数"""
        from main import _insert_affect_projection

        pg = self._make_mock_pg()
        records = [
            {"source_table": "ar_dreams", "source_pk": "1", "payload_json": {"content": "dream content"}, "payload_hash": "h1"},
            {"source_table": "ar_whispers", "source_pk": "2", "payload_json": {"content": "whisper"}, "payload_hash": "h2"},
            {"source_table": "diary", "source_pk": "3", "payload_json": {"content": "dear diary"}, "payload_hash": "h3"},
            {"source_table": "knots", "source_pk": "4", "payload_json": {"content": "knot"}, "payload_hash": "h4"},
            {"source_table": "ar_state", "source_pk": "5", "payload_json": {"content": "state"}, "payload_hash": "h5"},
            {"source_table": "proactive_messages", "source_pk": "6", "payload_json": {"content": "proactive"}, "payload_hash": "h6"},
        ]
        count = _insert_affect_projection(pg, "run-test", "v1", records)
        assert count > 0, "affect_projection must have non-zero count"
        assert pg.execute.call_count == 6

    def test_all_six_projections_have_non_zero_fixture(self):
        """端到端：使用合成 SQLite 数据验证六类投影全部非零。"""
        import sqlite3
        import tempfile
        import os
