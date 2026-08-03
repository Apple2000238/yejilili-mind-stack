"""Provenance 账本：记录 adapter 的每一次 MCP 调用来源与结果。"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timezone
from typing import Any

import psycopg


INIT_SQL = """
CREATE TABLE IF NOT EXISTS adapter_provenance (
    id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    caller_subject TEXT NOT NULL,
    auto BOOLEAN,
    source TEXT,
    input_hash TEXT NOT NULL,
    target_kind TEXT NOT NULL,
    target_ref TEXT,
    result_hash TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    idempotency_status TEXT NOT NULL DEFAULT 'new',
    error TEXT,
    metadata JSONB
);

CREATE INDEX IF NOT EXISTS idx_prov_event_id ON adapter_provenance(event_id);
CREATE INDEX IF NOT EXISTS idx_prov_tool ON adapter_provenance(tool_name);
CREATE INDEX IF NOT EXISTS idx_prov_caller ON adapter_provenance(caller_subject);
CREATE INDEX IF NOT EXISTS idx_prov_created ON adapter_provenance(created_at);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_json(obj: Any) -> str:
    """确定性 JSON：按键排序，无缩进，确保相同内容产生相同 hash。"""
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _hash_json(obj: Any) -> str:
    return hashlib.sha256(_canonical_json(obj).encode("utf-8")).hexdigest()


class ProvenanceStore:
    """Adapter provenance 存储。"""

    def __init__(self, dsn: str):
        self.dsn = dsn
        self._init_db()

    def _init_db(self) -> None:
        with psycopg.connect(self.dsn) as conn:
            conn.execute(INIT_SQL)
            conn.commit()

    def record(
        self,
        *,
        event_id: str,
        tool_name: str,
        caller_subject: str,
        auto: bool | None,
        source: str | None,
        input_payload: dict,
        target_kind: str,
        target_ref: str | None = None,
        result_payload: dict | None = None,
        idempotency_status: str = "new",
        error: str | None = None,
        metadata: dict | None = None,
    ) -> str:
        """
        记录一次 adapter 调用。
        返回 provenance 记录 ID。
        """
        prov_id = uuid.uuid4().hex[:16]
        input_hash = _hash_json(input_payload)
        result_hash = _hash_json(result_payload) if result_payload else None

        with psycopg.connect(self.dsn) as conn:
            conn.execute(
                """
                INSERT INTO adapter_provenance
                (id, event_id, tool_name, caller_subject, auto, source,
                 input_hash, target_kind, target_ref, result_hash,
                 idempotency_status, error, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (event_id, tool_name, input_hash)
                DO UPDATE SET
                    idempotency_status = 'duplicate',
                    created_at = EXCLUDED.created_at
                """,
                (
                    prov_id,
                    event_id,
                    tool_name,
                    caller_subject,
                    auto,
                    source,
                    input_hash,
                    target_kind,
                    target_ref,
                    result_hash,
                    idempotency_status,
                    error,
                    json.dumps(metadata or {}),
                ),
            )
            conn.commit()
        return prov_id

    def find_by_event(self, event_id: str) -> list[dict]:
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT * FROM adapter_provenance WHERE event_id = %s ORDER BY created_at",
                    (event_id,),
                )
                cols = [c.name for c in cur.description]
                return [dict(zip(cols, row)) for row in cur.fetchall()]

    def check_idempotency(self, event_id: str, input_payload: dict) -> dict | None:
        """检查同一 event_id + input_hash 是否已存在。"""
        input_hash = _hash_json(input_payload)
        with psycopg.connect(self.dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT target_ref, result_hash FROM adapter_provenance WHERE event_id = %s AND input_hash = %s LIMIT 1",
                    (event_id, input_hash),
                )
                row = cur.fetchone()
                if row:
                    return {"target_ref": row[0], "result_hash": row[1]}
        return None
