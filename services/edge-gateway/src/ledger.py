"""Ledger provenance 记录模块

将每次 LLM 转发的元数据记录到 continuity-ledger 的 adapter_provenance 表，
包括 timestamp, session_id, model, tokens, latency 等。
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import uuid
from typing import Any, Optional

import psycopg

logger = logging.getLogger("gateway.ledger")


class LedgerClient:
    """连续性账本客户端"""

    def __init__(self, dsn: str | None):
        self.dsn = dsn
        self._available = bool(dsn)
        if not self._available:
            logger.warning("ledger DSN not configured, provenance recording disabled")

    def _get_connection(self) -> psycopg.Connection | None:
        if not self._available:
            return None
        try:
            return psycopg.connect(self.dsn)
        except Exception as e:
            logger.error("failed to connect to ledger: %s", e)
            return None

    def record_provenance(
        self,
        *,
        event_id: str,
        tool_name: str,
        caller_subject: str,
        input_hash: str,
        target_kind: str,
        target_ref: Optional[str] = None,
        result_hash: Optional[str] = None,
        latency_ms: int = 0,
        token_usage: Optional[dict[str, int]] = None,
        session_id: Optional[str] = None,
        model: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        记录一次 adapter provenance 事件。

        对应表: adapter_provenance (001_init_adapter_provenance.sql)
        """
        conn = self._get_connection()
        if not conn:
            return False

        record_id = str(uuid.uuid4())
        meta = metadata or {}
        if token_usage:
            meta["token_usage"] = token_usage
        if model:
            meta["model"] = model
        if session_id:
            meta["session_id"] = session_id

        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO adapter_provenance (
                        id, event_id, tool_name, caller_subject,
                        input_hash, target_kind, target_ref, result_hash,
                        idempotency_status, error, metadata
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (event_id, tool_name, input_hash) WHERE idempotency_status IN ('new', 'duplicate')
                    DO UPDATE SET
                        result_hash = EXCLUDED.result_hash,
                        metadata = adapter_provenance.metadata || EXCLUDED.metadata
                    """,
                    (
                        record_id,
                        event_id,
                        tool_name,
                        caller_subject,
                        input_hash,
                        target_kind,
                        target_ref,
                        result_hash,
                        "new",
                        None,
                        json.dumps(meta),
                    ),
                )
            logger.debug("provenance recorded: event_id=%s", event_id)
            return True
        except Exception as e:
            logger.error("failed to record provenance: %s", e)
            return False
        finally:
            conn.close()

    def upsert_session(
        self,
        session_id: str,
        namespace: str,
        platform: str = "unknown",
        room: str = "default",
    ) -> bool:
        """
        更新或创建 conversation_sessions 记录。
        """
        conn = self._get_connection()
        if not conn:
            return False

        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO conversation_sessions (session_id, namespace, platform, room, last_active_at)
                    VALUES (%s, %s, %s, %s, now())
                    ON CONFLICT (session_id) DO UPDATE SET
                        last_active_at = now(),
                        namespace = EXCLUDED.namespace
                    """,
                    (session_id, namespace, platform, room),
                )
            return True
        except Exception as e:
            logger.error("failed to upsert session: %s", e)
            return False
        finally:
            conn.close()

    def record_message(
        self,
        session_id: str,
        message_index: int,
        role: str,
        content_hash: str,
        token_count: Optional[int] = None,
        source_refs: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        记录一条 conversation_messages。
        """
        conn = self._get_connection()
        if not conn:
            return False

        try:
            with conn:
                conn.execute(
                    """
                    INSERT INTO conversation_messages (session_id, message_index, role, content_hash, token_count, source_refs)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (session_id, message_index) DO NOTHING
                    """,
                    (session_id, message_index, role, content_hash, token_count, json.dumps(source_refs) if source_refs else None),
                )
            return True
        except Exception as e:
            logger.error("failed to record message: %s", e)
            return False
        finally:
            conn.close()


def hash_request(body: dict[str, Any]) -> str:
    """计算请求 body 的 hash，用于 provenance input_hash。"""
    canonical = json.dumps(body, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def hash_response(resp: dict[str, Any]) -> str:
    """计算响应的 hash，用于 provenance result_hash。"""
    canonical = json.dumps(resp, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
