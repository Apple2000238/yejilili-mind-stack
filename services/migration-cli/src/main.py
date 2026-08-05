"""Migration CLI — 连续性数据迁移工具

命令：
    python -m src.main snapshot-pre  --run-id <uuid> --source-db <path>
    python -m src.main snapshot-post --run-id <uuid>
    python -m src.main export-source --source-db <path> --run-id <uuid>
    python -m src.main import-staging --run-id <uuid>
    python -m src.main verify        --run-id <uuid>
    python -m src.main rollback      --run-id <uuid>
    python -m src.main list-runs

纪律：
    - 任何命令自动执行 pre/post snapshot
    - snapshot 失败则整体命令失败
    - 不触碰生产 /opt/afterrain-api
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sqlite3
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import psycopg

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("migration-cli")

# ─── 配置 ────────────────────────────────────────────────────────────────────
POSTGRES_HOST = os.environ.get("POSTGRES_HOST", "continuity-ledger")
POSTGRES_PORT = os.environ.get("POSTGRES_PORT", "5432")
POSTGRES_DB = os.environ.get("POSTGRES_DB", "continuity_ledger")
POSTGRES_USER = os.environ.get("POSTGRES_USER", "continuity")
POSTGRES_PASSWORD_FILE = os.environ.get("POSTGRES_PASSWORD_FILE", "/run/secrets/postgres_password")
ARTIFACTS_DIR = Path("/artifacts")


# ─── 源表 → 投影映射规则 ─────────────────────────────────────────────────────
# 确定性映射：每张源表归属唯一投影类别，字段转换显式定义
IDENTITY_TABLES = {"persona", "memory_layers"}
MEMORY_TABLES = {"ar_buckets"}
MESSAGE_TABLES = {"message_archive", "message_buffer", "chat_sessions"}
SUMMARY_TABLES = {"daily_summaries", "weekly_summaries"}
PROMISE_TABLES = {"promises"}
AFFECT_TABLES = {"ar_dreams", "ar_whispers", "diary", "knots", "ar_state", "proactive_messages", "room_visits"}


def _read_password() -> str:
    try:
        return Path(POSTGRES_PASSWORD_FILE).read_text().strip()
    except Exception as e:
        logger.error("cannot read postgres password file: %s", e)
        raise SystemExit(1)


def get_dsn() -> str:
    pw = _read_password()
    return f"postgresql://{POSTGRES_USER}:{pw}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"


def get_pg() -> psycopg.Connection:
    return psycopg.connect(get_dsn(), row_factory=psycopg.rows.dict_row)


# ─── Snapshot ────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return time.strftime("%Y%m%d-%H%M%S-CST")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_pre(run_id: str, source_db: str | None) -> dict[str, Any]:
    """pre snapshot：记录 run ID、Git commit、磁盘余量、schema hash。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    pre_dir = snapshot_dir / "pre"
    pre_dir.mkdir(exist_ok=True)

    # Git commit
    git_commit = "unknown"
    try:
        import subprocess
        git_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
    except Exception:
        pass

    # Compose config hash
    compose_path = Path("docker-compose.yml")
    compose_hash = _sha256_file(compose_path) if compose_path.exists() else ""

    # Disk free
    disk_free = 0
    try:
        stat = os.statvfs("/artifacts")
        disk_free = stat.f_frsize * stat.f_bavail
    except Exception:
        pass

    # Postgres schema hash
    schema_hash = ""
    try:
        with get_pg() as pg:
            tables = pg.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"
            ).fetchall()
            schema_parts = []
            for row in tables:
                cols = pg.execute(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name=%s ORDER BY ordinal_position",
                    (row["tablename"],),
                ).fetchall()
                schema_parts.append(f"{row['tablename']}:{','.join(f'{c['column_name']}({c['data_type']})' for c in cols)}")
            schema_hash = _sha256_text("\n".join(schema_parts))
    except Exception as e:
        logger.warning("schema hash failed: %s", e)

    pre_manifest = {
        "snapshot_type": "pre",
        "run_id": run_id,
        "git_commit": git_commit,
        "compose_config_hash": compose_hash,
        "schema_hash": schema_hash,
        "disk_free_bytes": disk_free,
        "source_db": source_db,
        "timestamp": _now_iso(),
    }
    manifest_path = pre_dir / "snapshot-manifest.json"
    manifest_path.write_text(json.dumps(pre_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # 写入 run-index.jsonl
    index_path = ARTIFACTS_DIR / "run-index.jsonl"
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "phase": "pre", "manifest_sha256": _sha256_file(manifest_path), "at": _now_iso()}, ensure_ascii=False) + "\n")

    # ── Nocturne buckets frontmatter 快照 ────────────────────────────────
    buckets_dir = Path(os.environ.get("NOCTURNE_BUCKETS_DIR", "/data/buckets"))
    bucket_snapshots = {}
    if buckets_dir.exists():
        for md_file in buckets_dir.rglob("*.md"):
            rel = md_file.relative_to(buckets_dir).as_posix()
            try:
                import frontmatter
                post = frontmatter.load(str(md_file))
                bucket_snapshots[rel] = {
                    "frontmatter": post.metadata,
                    "content_preview": post.content[:500] if post.content else "",
                    "sha256": _sha256_file(md_file),
                }
            except Exception as e:
                logger.warning("bucket snapshot failed for %s: %s", rel, e)
        buckets_path = pre_dir / "buckets-snapshot.json"
        buckets_path.write_text(json.dumps(bucket_snapshots, ensure_ascii=False, default=str), encoding="utf-8")

    # ── State 文件快照 ───────────────────────────────────────────────────
    state_dir = Path(os.environ.get("XINCHAO_STATE_DIR", "/data/xinchao/state"))
    state_snapshots = {}
    if state_dir.exists():
        for sf in state_dir.rglob("*"):
            if sf.is_file() and sf.suffix in (".json", ".md", ".yaml", ".yml"):
                rel = sf.relative_to(state_dir).as_posix()
                state_snapshots[rel] = {
                    "sha256": _sha256_file(sf),
                    "size": sf.stat().st_size,
                }
                # 小文件直接备份内容
                if sf.stat().st_size < 1024 * 1024:  # 1MB
                    backup_dir = pre_dir / "state_backup"
                    backup_dir.mkdir(exist_ok=True)
                    backup_file = backup_dir / rel.replace("/", "__")
                    try:
                        backup_file.write_bytes(sf.read_bytes())
                    except Exception as e:
                        logger.warning("state backup failed for %s: %s", rel, e)
        state_manifest_path = pre_dir / "state-snapshot.json"
        state_manifest_path.write_text(json.dumps(state_snapshots, ensure_ascii=False, default=str), encoding="utf-8")

    # ── Ledger 关键表快照（非投影表）──────────────────────────────────────
    ledger_snapshots = {}
    try:
        with get_pg() as pg:
            for tbl in ["continuity_manifest", "identity_assembly_audit"]:
                try:
                    cnt = pg.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
                    ledger_snapshots[tbl] = {"row_count": cnt}
                except Exception:
                    pass
        ledger_path = pre_dir / "ledger-snapshot.json"
        ledger_path.write_text(json.dumps(ledger_snapshots, ensure_ascii=False, default=str), encoding="utf-8")
    except Exception as e:
        logger.warning("ledger snapshot failed: %s", e)

    logger.info("pre snapshot created: %s", manifest_path)
    return pre_manifest


def snapshot_post(run_id: str, exit_code: int, error_summary: str = "") -> dict[str, Any]:
    """post snapshot：记录结果、差异、退出码。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    post_dir = snapshot_dir / "post"
    post_dir.mkdir(exist_ok=True)

    disk_free = 0
    try:
        stat = os.statvfs("/artifacts")
        disk_free = stat.f_frsize * stat.f_bavail
    except Exception:
        pass

    post_manifest = {
        "snapshot_type": "post",
        "run_id": run_id,
        "exit_code": exit_code,
        "error_summary": error_summary,
        "disk_free_bytes": disk_free,
        "timestamp": _now_iso(),
    }
    manifest_path = post_dir / "snapshot-manifest.json"
    manifest_path.write_text(json.dumps(post_manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    index_path = ARTIFACTS_DIR / "run-index.jsonl"
    with open(index_path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"run_id": run_id, "phase": "post", "manifest_sha256": _sha256_file(manifest_path), "at": _now_iso()}, ensure_ascii=False) + "\n")

    logger.info("post snapshot created: %s", manifest_path)
    return post_manifest


# ─── Export source ───────────────────────────────────────────────────────────

def export_source(source_db: str, run_id: str) -> dict[str, Any]:
    """从 SQLite 只读 source 导出为一致性快照和 manifest。"""
    source_path = Path(source_db)
    if not source_db or not source_path.is_absolute() or not source_path.exists():
        raise ValueError(f"source_db must be an absolute path to an existing SQLite file: {source_db}")
    if source_path.stat().st_size == 0:
        raise ValueError("source_db is 0 bytes")

    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    export_dir = snapshot_dir / "export"
    export_dir.mkdir(parents=True, exist_ok=True)

    # 使用 SQLite backup API 生成一致性临时快照
    tmp_path = export_dir / "source-snapshot-temp.db"
    src = sqlite3.connect(f"file:{source_db}?mode=ro", uri=True)
    dst = sqlite3.connect(str(tmp_path))
    src.backup(dst)
    src.close()
    dst.close()

    # PRAGMA quick_check
    check_db = sqlite3.connect(str(tmp_path))
    quick_check = check_db.execute("PRAGMA quick_check").fetchone()[0]
    if quick_check != "ok":
        raise RuntimeError(f"PRAGMA quick_check failed: {quick_check}")

    # Schema fingerprint + row counts
    tables = check_db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()

    manifest = {
        "run_id": run_id,
        "source_db": source_db,
        "quick_check": quick_check,
        "tables": {},
        "total_rows": 0,
    }

    for (table_name,) in tables:
        # row count
        count = check_db.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0]
        # schema
        schema = check_db.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        schema_text = json.dumps(schema, sort_keys=True, ensure_ascii=False)
        schema_hash = _sha256_text(schema_text)

        # Merkle-ish hash of sorted rows（含 rowid 以确保稳定主键语义）
        pragma_info = check_db.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        has_explicit_pk = any(p[5] == 1 for p in pragma_info)  # pk column
        if has_explicit_pk:
            rows = check_db.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid').fetchall()
        else:
            rows = check_db.execute(f'SELECT rowid, * FROM "{table_name}" ORDER BY rowid').fetchall()
        cols = [d[0] for d in check_db.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
        if not has_explicit_pk:
            cols = ["rowid"] + cols
        row_hashes = []
        for row in rows:
            canonical = json.dumps(dict(zip(cols, row)), sort_keys=True, ensure_ascii=False, default=str)
            row_hashes.append(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        merkle = hashlib.sha256("".join(row_hashes).encode("utf-8")).hexdigest() if row_hashes else ""

        manifest["tables"][table_name] = {
            "row_count": count,
            "schema_hash": schema_hash,
            "merkle_root": merkle,
            "has_explicit_pk": has_explicit_pk,
        }
        manifest["total_rows"] += count

    check_db.close()

    # 保存 manifest
    manifest_path = export_dir / "source-manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # 计算并保存快照 hash
    snapshot_hash = _sha256_file(tmp_path)
    hash_path = export_dir / "source-snapshot.sha256"
    hash_path.write_text(snapshot_hash, encoding="utf-8")

    logger.info("export completed: %s tables, %s rows", len(tables), manifest["total_rows"])
    return manifest


# ─── 稳定主键策略 ────────────────────────────────────────────────────────────

def _resolve_source_pk(table_name: str, payload: dict[str, Any], has_explicit_pk: bool) -> str:
    """返回稳定的 source_pk。

    策略：
    1. 显式 INTEGER PRIMARY KEY 表 → 使用 'id' 字段值；
    2. 无显式 PK 表 → 使用 SELECT 时显式带出的 rowid；
    3. 若以上均不可得 → 抛出异常（禁止静默回退到整行 hash）。
    """
    if has_explicit_pk:
        pk_val = payload.get("id")
        if pk_val is not None:
            return str(pk_val)
        raise ValueError(f"table {table_name} has explicit PK but 'id' is missing in payload")
    # 无显式 PK：export_source 已强制 SELECT rowid
    rowid_val = payload.get("rowid")
    if rowid_val is not None:
        return str(rowid_val)
    raise ValueError(f"table {table_name} lacks explicit PK and rowid is missing; cannot determine stable source_pk")


# ─── Projection 映射 ─────────────────────────────────────────────────────────

def _insert_identity_projection(pg: psycopg.Connection, run_id: str, mapping_version: str, records: list[dict]) -> int:
    """将 persona / memory_layers 映射到 identity_projection。"""
    inserted = 0
    for rec in records:
        table = rec["source_table"]
        pk = rec["source_pk"]
        payload = rec["payload_json"]
        content_hash = rec["payload_hash"]

        persona_json = None
        protected_layers = None

        if table == "persona":
            persona_json = json.dumps({
                "name": payload.get("name"),
                "role": payload.get("role"),
                "content": payload.get("content"),
                "created_at": payload.get("created_at"),
            }, ensure_ascii=False, default=str)
        elif table == "memory_layers":
            protected_layers = json.dumps({
                "layer_type": payload.get("layer_type"),
                "layer_key": payload.get("layer_key"),
                "content": payload.get("content"),
                "protected": bool(payload.get("protected")),
            }, ensure_ascii=False, default=str)

        if persona_json or protected_layers:
            pg.execute(
                """
                INSERT INTO identity_projection
                (run_id, source_table, source_pk, source_content_hash, mapping_version, persona_json, protected_layers)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, source_table, source_pk)
                DO UPDATE SET
                    source_content_hash = EXCLUDED.source_content_hash,
                    mapping_version = EXCLUDED.mapping_version,
                    persona_json = EXCLUDED.persona_json,
                    protected_layers = EXCLUDED.protected_layers,
                    updated_at = now()
                """,
                (run_id, table, pk, content_hash, mapping_version, persona_json, protected_layers),
            )
            inserted += 1
    return inserted


def _insert_memory_projection(pg: psycopg.Connection, run_id: str, mapping_version: str, records: list[dict]) -> int:
    """将 ar_buckets 映射到 memory_projection。"""
    inserted = 0
    for rec in records:
        payload = rec["payload_json"]
        pk = rec["source_pk"]
        content_hash = rec["payload_hash"]

        bucket_yaml = json.dumps({
            "name": payload.get("name"),
            "type": payload.get("type"),
            "domain": payload.get("domain"),
            "tags": payload.get("tags"),
            "valence": payload.get("valence"),
            "arousal": payload.get("arousal"),
            "importance": payload.get("importance"),
            "confidence": payload.get("confidence"),
            "resolved": bool(payload.get("resolved")),
            "pinned": bool(payload.get("pinned")),
            "anchor": bool(payload.get("anchor")),
            "content_preview": payload.get("content_preview"),
            "content_full": payload.get("content_full"),
            "feel_text": payload.get("feel_text"),
            "source": payload.get("source"),
        }, ensure_ascii=False, default=str)

        nocturne_ref = f"bucket-{pk}"
        layer_type = payload.get("type", "memory")
        protected = bool(payload.get("anchor") or payload.get("pinned"))

        pg.execute(
            """
            INSERT INTO memory_projection
            (run_id, source_table, source_pk, source_content_hash, mapping_version, bucket_yaml, nocturne_ref, layer_type, protected, embedding_status)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, source_table, source_pk)
            DO UPDATE SET
                source_content_hash = EXCLUDED.source_content_hash,
                mapping_version = EXCLUDED.mapping_version,
                bucket_yaml = EXCLUDED.bucket_yaml,
                nocturne_ref = EXCLUDED.nocturne_ref,
                layer_type = EXCLUDED.layer_type,
                protected = EXCLUDED.protected,
                updated_at = now()
            """,
            (run_id, "ar_buckets", pk, content_hash, mapping_version, bucket_yaml, nocturne_ref, layer_type, protected, "pending"),
        )
        inserted += 1
    return inserted


def _insert_message_projection(pg: psycopg.Connection, run_id: str, mapping_version: str, records: list[dict]) -> int:
    """将 message_archive / message_buffer / chat_sessions 映射到 message_projection。"""
    inserted = 0
    for rec in records:
        table = rec["source_table"]
        payload = rec["payload_json"]
        pk = rec["source_pk"]
        content_hash = rec["payload_hash"]

        if table == "message_archive":
            role = payload.get("role", "unknown")
            session_id = payload.get("session_id")
            room = payload.get("room")
            platform = payload.get("platform")
            content = payload.get("content")
            archived = bool(payload.get("archived"))
        elif table == "message_buffer":
            role = "buffer"
            session_id = None
            room = None
            platform = None
            content = payload.get("content")
            archived = False
        elif table == "chat_sessions":
            role = "session_meta"
            session_id = payload.get("session_id")
            room = payload.get("room")
            platform = payload.get("platform")
            content = None
            archived = False
        else:
            continue

        pg.execute(
            """
            INSERT INTO message_projection
            (run_id, source_table, source_pk, source_content_hash, mapping_version, role, session_id, room, platform, content, archived)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, source_table, source_pk)
            DO UPDATE SET
                source_content_hash = EXCLUDED.source_content_hash,
                mapping_version = EXCLUDED.mapping_version,
                role = EXCLUDED.role,
                session_id = EXCLUDED.session_id,
                room = EXCLUDED.room,
                platform = EXCLUDED.platform,
                content = EXCLUDED.content,
                archived = EXCLUDED.archived,
                updated_at = now()
            """,
            (run_id, table, pk, content_hash, mapping_version, role, session_id, room, platform, content, archived),
        )
        inserted += 1
    return inserted


def _insert_summary_projection(pg: psycopg.Connection, run_id: str, mapping_version: str, records: list[dict]) -> int:
    """将 daily_summaries / weekly_summaries 映射到 summary_projection。"""
    inserted = 0
    for rec in records:
        table = rec["source_table"]
        payload = rec["payload_json"]
        pk = rec["source_pk"]
        content_hash = rec["payload_hash"]

        if table == "daily_summaries":
            summary_type = "daily"
        elif table == "weekly_summaries":
            summary_type = "weekly"
        else:
            continue

        summary_text = payload.get("summary_text")
        batch_id = payload.get("batch_id")

        pg.execute(
            """
            INSERT INTO summary_projection
            (run_id, source_table, source_pk, source_content_hash, mapping_version, summary_type, summary_text, batch_id)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, source_table, source_pk)
            DO UPDATE SET
                source_content_hash = EXCLUDED.source_content_hash,
                mapping_version = EXCLUDED.mapping_version,
                summary_type = EXCLUDED.summary_type,
                summary_text = EXCLUDED.summary_text,
                batch_id = EXCLUDED.batch_id,
                updated_at = now()
            """,
            (run_id, table, pk, content_hash, mapping_version, summary_type, summary_text, batch_id),
        )
        inserted += 1
    return inserted


def _insert_promise_projection(pg: psycopg.Connection, run_id: str, mapping_version: str, records: list[dict]) -> int:
    """将 promises 映射到 promise_projection。"""
    inserted = 0
    for rec in records:
        payload = rec["payload_json"]
        pk = rec["source_pk"]
        content_hash = rec["payload_hash"]

        promise_text = payload.get("promise_text", "")
        status = payload.get("status", "active")
        due_date = payload.get("due_date")
        fulfilled_at = payload.get("fulfilled_at")
        emotion_weight = payload.get("emotion_weight")

        pg.execute(
            """
            INSERT INTO promise_projection
            (run_id, source_table, source_pk, source_content_hash, mapping_version, promise_text, status, due_date, fulfilled_at, emotion_weight)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, source_table, source_pk)
            DO UPDATE SET
                source_content_hash = EXCLUDED.source_content_hash,
                mapping_version = EXCLUDED.mapping_version,
                promise_text = EXCLUDED.promise_text,
                status = EXCLUDED.status,
                due_date = EXCLUDED.due_date,
                fulfilled_at = EXCLUDED.fulfilled_at,
                emotion_weight = EXCLUDED.emotion_weight,
                updated_at = now()
            """,
            (run_id, "promises", pk, content_hash, mapping_version, promise_text, status, due_date, fulfilled_at, emotion_weight),
        )
        inserted += 1
    return inserted


AFFECT_TYPE_MAP = {
    "ar_dreams": "dream",
    "ar_whispers": "whisper",
    "diary": "diary",
    "knots": "knot",
    "ar_state": "state",
    "proactive_messages": "proactive",
    "room_visits": "state",
}


def _insert_affect_projection(pg: psycopg.Connection, run_id: str, mapping_version: str, records: list[dict]) -> int:
    """将 ar_dreams / ar_whispers / diary / knots / ar_state / proactive_messages 映射到 affect_projection。"""
    inserted = 0
    for rec in records:
        table = rec["source_table"]
        payload = rec["payload_json"]
        pk = rec["source_pk"]
        content_hash = rec["payload_hash"]

        affect_type = AFFECT_TYPE_MAP.get(table, "unknown")
        content = payload.get("content")
        pinned = bool(payload.get("pinned", 0))
        deleted = bool(payload.get("deleted", 0))

        pg.execute(
            """
            INSERT INTO affect_projection
            (run_id, source_table, source_pk, source_content_hash, mapping_version, affect_type, content, pinned, deleted)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (run_id, source_table, source_pk)
            DO UPDATE SET
                source_content_hash = EXCLUDED.source_content_hash,
                mapping_version = EXCLUDED.mapping_version,
                affect_type = EXCLUDED.affect_type,
                content = EXCLUDED.content,
                pinned = EXCLUDED.pinned,
                deleted = EXCLUDED.deleted,
                updated_at = now()
            """,
            (run_id, table, pk, content_hash, mapping_version, affect_type, content, pinned, deleted),
        )
        inserted += 1
    return inserted


# ─── Import staging ──────────────────────────────────────────────────────────

def import_staging(run_id: str, mapping_version: str = "v1") -> dict[str, Any]:
    """将已验证的 source manifest 导入到 PostgreSQL 规范化投影。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    manifest_path = snapshot_dir / "export" / "source-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"source manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with get_pg() as pg:
        # 创建 migration_run 记录（幂等：已存在则跳过）
        pg.execute(
            """
            INSERT INTO migration_runs (id, run_name, source_snapshot_hash, mapping_version, status)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (id) DO NOTHING
            """,
            (run_id, f"run-{run_id}", manifest["tables"].get("ar_buckets", {}).get("merkle_root", ""), mapping_version, "running"),
        )

        # source_table_manifest
        for table_name, info in manifest["tables"].items():
            pg.execute(
                """
                INSERT INTO source_table_manifest (run_id, source_table, schema_hash, row_count, primary_key_strategy, excluded_secret_fields)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (run_id, source_table) DO NOTHING
                """,
                (run_id, table_name, info["schema_hash"], info["row_count"], "explicit_id" if info.get("has_explicit_pk") else "rowid", []),
            )

        # 读取临时快照并写入 source_records（含稳定主键）
        tmp_db = snapshot_dir / "export" / "source-snapshot-temp.db"
        if tmp_db.exists():
            src = sqlite3.connect(str(tmp_db))
            for table_name, info in manifest["tables"].items():
                has_explicit_pk = info.get("has_explicit_pk", True)
                if has_explicit_pk:
                    rows = src.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid').fetchall()
                else:
                    rows = src.execute(f'SELECT rowid, * FROM "{table_name}" ORDER BY rowid').fetchall()
                cols = [d[0] for d in src.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
                if not has_explicit_pk:
                    cols = ["rowid"] + cols

                for row in rows:
                    payload = dict(zip(cols, row))
                    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
                    payload_hash = _sha256_text(payload_json)
                    source_pk = _resolve_source_pk(table_name, payload, has_explicit_pk)
                    pg.execute(
                        """
                        INSERT INTO source_records (run_id, source_table, source_pk, payload_json, payload_hash, canonical_hash, mapping_version)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        ON CONFLICT (run_id, source_table, source_pk)
                        DO UPDATE SET
                            payload_json = EXCLUDED.payload_json,
                            payload_hash = EXCLUDED.payload_hash,
                            canonical_hash = EXCLUDED.canonical_hash,
                            mapping_version = EXCLUDED.mapping_version,
                            updated_at = now()
                        """,
                        (run_id, table_name, source_pk, payload_json, payload_hash, payload_hash, mapping_version),
                    )
            src.close()

        # 按投影类别分组读取 source_records 并写入六张投影表
        projection_counts: dict[str, int] = {}

        # identity
        identity_rows = pg.execute(
            "SELECT source_table, source_pk, payload_json, payload_hash FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
            (run_id, list(IDENTITY_TABLES)),
        ).fetchall()
        projection_counts["identity_projection"] = _insert_identity_projection(
            pg, run_id, mapping_version, [dict(r) for r in identity_rows]
        )

        # memory
        memory_rows = pg.execute(
            "SELECT source_table, source_pk, payload_json, payload_hash FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
            (run_id, list(MEMORY_TABLES)),
        ).fetchall()
        projection_counts["memory_projection"] = _insert_memory_projection(
            pg, run_id, mapping_version, [dict(r) for r in memory_rows]
        )

        # message
        message_rows = pg.execute(
            "SELECT source_table, source_pk, payload_json, payload_hash FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
            (run_id, list(MESSAGE_TABLES)),
        ).fetchall()
        projection_counts["message_projection"] = _insert_message_projection(
            pg, run_id, mapping_version, [dict(r) for r in message_rows]
        )

        # summary
        summary_rows = pg.execute(
            "SELECT source_table, source_pk, payload_json, payload_hash FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
            (run_id, list(SUMMARY_TABLES)),
        ).fetchall()
        projection_counts["summary_projection"] = _insert_summary_projection(
            pg, run_id, mapping_version, [dict(r) for r in summary_rows]
        )

        # promise
        promise_rows = pg.execute(
            "SELECT source_table, source_pk, payload_json, payload_hash FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
            (run_id, list(PROMISE_TABLES)),
        ).fetchall()
        projection_counts["promise_projection"] = _insert_promise_projection(
            pg, run_id, mapping_version, [dict(r) for r in promise_rows]
        )

        # affect
        affect_rows = pg.execute(
            "SELECT source_table, source_pk, payload_json, payload_hash FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
            (run_id, list(AFFECT_TABLES)),
        ).fetchall()
        projection_counts["affect_projection"] = _insert_affect_projection(
            pg, run_id, mapping_version, [dict(r) for r in affect_rows]
        )

        # 统计不可映射记录
        unmappable_rows = pg.execute(
            "SELECT source_table, COUNT(*) AS c FROM source_records WHERE run_id=%s AND source_table NOT IN (SELECT UNNEST(%s)) GROUP BY source_table",
            (run_id, list(IDENTITY_TABLES | MEMORY_TABLES | MESSAGE_TABLES | SUMMARY_TABLES | PROMISE_TABLES | AFFECT_TABLES)),
        ).fetchall()
        unmappable = {r["source_table"]: r["c"] for r in unmappable_rows}

        # 更新 migration_runs 状态与计数
        records_total = sum(t["row_count"] for t in manifest["tables"].values())
        records_migrated = sum(projection_counts.values())
        pg.execute(
            """
            UPDATE migration_runs
            SET status=%s, records_total=%s, records_migrated=%s, completed_at=now(), metadata=%s
            WHERE id=%s
            """,
            ("completed", records_total, records_migrated, json.dumps({"projections": projection_counts, "unmappable": unmappable}, ensure_ascii=False), run_id),
        )
        pg.commit()

    logger.info("import staging completed for run %s: projections=%s", run_id, projection_counts)
    return {"status": "completed", "records_total": records_total, "projections": projection_counts, "unmappable": unmappable}


# ─── Verify ──────────────────────────────────────────────────────────────────

def verify_run(run_id: str) -> dict[str, Any]:
    """校验迁移结果：比较 source manifest 与 target 投影。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    manifest_path = snapshot_dir / "export" / "source-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"source manifest not found: {manifest_path}")

    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results: dict[str, Any] = {
        "run_id": run_id,
        "source_checks": {},
        "projection_checks": {},
        "idempotency_check": {},
        "overall": "PASS",
    }

    with get_pg() as pg:
        # 1) source_records 完整性
        for table_name, info in source_manifest["tables"].items():
            expected = info["row_count"]
            actual = pg.execute(
                "SELECT COUNT(*) AS c FROM source_records WHERE run_id=%s AND source_table=%s",
                (run_id, table_name),
            ).fetchone()["c"]
            ok = actual == expected
            results["source_checks"][table_name] = {"expected": expected, "actual": actual, "ok": ok}
            if not ok:
                results["overall"] = "FAIL"

        # 2) 六类投影数量与必填字段
        projection_tables = [
            ("identity_projection", IDENTITY_TABLES),
            ("memory_projection", MEMORY_TABLES),
            ("message_projection", MESSAGE_TABLES),
            ("summary_projection", SUMMARY_TABLES),
            ("promise_projection", PROMISE_TABLES),
            ("affect_projection", AFFECT_TABLES),
        ]

        for proj_table, source_tables in projection_tables:
            expected = pg.execute(
                "SELECT COUNT(*) AS c FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
                (run_id, list(source_tables)),
            ).fetchone()["c"]
            actual = pg.execute(
                f"SELECT COUNT(*) AS c FROM {proj_table} WHERE run_id=%s",
                (run_id,),
            ).fetchone()["c"]
            ok = actual == expected
            results["projection_checks"][proj_table] = {"expected": expected, "actual": actual, "ok": ok}
            if not ok:
                results["overall"] = "FAIL"

            # 必填字段非空抽查（前 10 条）
            sample = pg.execute(
                f"SELECT * FROM {proj_table} WHERE run_id=%s LIMIT 10",
                (run_id,),
            ).fetchall()
            for row in sample:
                if not row.get("source_pk") or not row.get("source_content_hash"):
                    results["overall"] = "FAIL"
                    results["projection_checks"][proj_table]["required_fields_ok"] = False
                    break
            else:
                results["projection_checks"][proj_table]["required_fields_ok"] = True

        # 3) migration_runs 状态
        run = pg.execute(
            "SELECT status, records_total, records_migrated, metadata FROM migration_runs WHERE id=%s", (run_id,)
        ).fetchone()
        if not run or run["status"] != "completed":
            results["overall"] = "FAIL"
            results["run_status"] = run["status"] if run else "missing"
        else:
            results["run_status"] = run["status"]
            # 校验 metadata 中的 projection 计数与真实表计数一致
            meta = json.loads(run["metadata"] or "{}")
            meta_projs = meta.get("projections", {})
            for proj_name, meta_count in meta_projs.items():
                real_count = pg.execute(
                    f"SELECT COUNT(*) AS c FROM {proj_name} WHERE run_id=%s", (run_id,)
                ).fetchone()["c"]
                if real_count != meta_count:
                    results["overall"] = "FAIL"
                    results["projection_checks"][proj_name]["metadata_mismatch"] = {"metadata": meta_count, "actual": real_count}

        # 4) 幂等性：projection 行数应等于 source_records 中对应源表行数（重跑不叠加）
        for proj_table, source_tables in projection_tables:
            src_count = pg.execute(
                "SELECT COUNT(DISTINCT source_pk) AS c FROM source_records WHERE run_id=%s AND source_table = ANY(%s)",
                (run_id, list(source_tables)),
            ).fetchone()["c"]
            proj_count = pg.execute(
                f"SELECT COUNT(*) AS c FROM {proj_table} WHERE run_id=%s",
                (run_id,),
            ).fetchone()["c"]
            results["idempotency_check"][proj_table] = {"source_pk_distinct": src_count, "projection_rows": proj_count, "ok": src_count == proj_count}
            if src_count != proj_count:
                results["overall"] = "FAIL"

    logger.info("verify result: %s", results["overall"])
    return results


# ─── Rollback ─────────────────────────────────────────────────────────────────

def rollback_run(run_id: str) -> dict[str, Any]:
    """完整回滚：删除投影 + 恢复 buckets frontmatter / state 文件 / ledger。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    pre_dir = snapshot_dir / "pre"
    restored = {"projections_deleted": False, "buckets": 0, "state_files": 0, "ledger_warnings": []}

    with get_pg() as pg:
        # 检查 run 是否存在
        run = pg.execute("SELECT status FROM migration_runs WHERE id=%s", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"run {run_id} not found")

        # 创建 rollback point
        pg.execute(
            "INSERT INTO rollback_points (run_id, point_name, point_type) VALUES (%s, %s, %s)",
            (run_id, f"rollback-at-{_now_iso()}", "full"),
        )

        # 1. 删除该 run 的投影数据
        pg.execute("DELETE FROM identity_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM memory_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM message_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM summary_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM promise_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM affect_projection WHERE run_id=%s", (run_id,))
        restored["projections_deleted"] = True

        # 2. 恢复 Nocturne buckets frontmatter（如果 pre 快照存在）
        buckets_snapshot_path = pre_dir / "buckets-snapshot.json"
        if buckets_snapshot_path.exists():
            buckets_dir = Path(os.environ.get("NOCTURNE_BUCKETS_DIR", "/data/buckets"))
            bucket_snapshots = json.loads(buckets_snapshot_path.read_text(encoding="utf-8"))
            for rel_path, snapshot in bucket_snapshots.items():
                target = buckets_dir / rel_path
                if target.exists():
                    current_hash = _sha256_file(target)
                    if current_hash != snapshot.get("sha256"):
                        # 文件被修改过，尝试恢复 frontmatter
                        try:
                            import frontmatter
                            post = frontmatter.load(str(target))
                            post.metadata = snapshot["frontmatter"]
                            # 保留当前 content（只恢复 frontmatter，不覆盖正文）
                            frontmatter.dump(post, str(target))
                            restored["buckets"] += 1
                            logger.info("restored bucket frontmatter: %s", rel_path)
                        except Exception as e:
                            logger.error("bucket restore failed for %s: %s", rel_path, e)
                            restored["ledger_warnings"].append(f"bucket_restore_failed:{rel_path}")

        # 3. 恢复 state 文件（如果 pre 备份存在）
        state_backup_dir = pre_dir / "state_backup"
        state_manifest_path = pre_dir / "state-snapshot.json"
        if state_manifest_path.exists() and state_backup_dir.exists():
            state_dir = Path(os.environ.get("XINCHAO_STATE_DIR", "/data/xinchao/state"))
            state_manifest = json.loads(state_manifest_path.read_text(encoding="utf-8"))
            for rel_path, info in state_manifest.items():
                backup_file = state_backup_dir / rel_path.replace("/", "__")
                target = state_dir / rel_path
                if backup_file.exists():
                    try:
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(backup_file.read_bytes())
                        restored["state_files"] += 1
                        logger.info("restored state file: %s", rel_path)
                    except Exception as e:
                        logger.error("state restore failed for %s: %s", rel_path, e)
                        restored["ledger_warnings"].append(f"state_restore_failed:{rel_path}")

        # 4. 检查 ledger 行数变化（可观测性，不自动恢复）
        ledger_snapshot_path = pre_dir / "ledger-snapshot.json"
        if ledger_snapshot_path.exists():
            ledger_pre = json.loads(ledger_snapshot_path.read_text(encoding="utf-8"))
            for tbl, pre_info in ledger_pre.items():
                try:
                    current_cnt = pg.execute(f"SELECT COUNT(*) AS c FROM {tbl}").fetchone()["c"]
                    if current_cnt != pre_info.get("row_count", current_cnt):
                        restored["ledger_warnings"].append(
                            f"ledger_changed:{tbl}:pre={pre_info['row_count']}:now={current_cnt}"
                        )
                        logger.warning("ledger table %s changed during migration: %s -> %s", tbl, pre_info["row_count"], current_cnt)
                except Exception as e:
                    logger.warning("ledger check failed for %s: %s", tbl, e)

        # 更新 run 状态
        pg.execute(
            "UPDATE migration_runs SET status=%s, completed_at=now(), metadata=coalesce(metadata,%s::jsonb) || %s::jsonb WHERE id=%s",
            ("rolled_back", "{}", json.dumps({"rollback_restored": restored}, ensure_ascii=False), run_id),
        )
        pg.commit()

    logger.info("rollback completed for run %s: restored=%s", run_id, restored)
    return {"status": "rolled_back", "run_id": run_id, "restored": restored}


# ─── List runs ───────────────────────────────────────────────────────────────

def list_runs() -> list[dict[str, Any]]:
    with get_pg() as pg:
        rows = pg.execute(
            "SELECT id, run_name, status, source_snapshot_hash, started_at, completed_at, records_total, records_migrated "
            "FROM migration_runs ORDER BY started_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Migration CLI for continuity migration")
    sub = parser.add_subparsers(dest="cmd", required=True)

    # snapshot-pre
    p_pre = sub.add_parser("snapshot-pre", help="Create pre-run snapshot")
    p_pre.add_argument("--run-id", required=True)
    p_pre.add_argument("--source-db", default=None)

    # snapshot-post
    p_post = sub.add_parser("snapshot-post", help="Create post-run snapshot")
    p_post.add_argument("--run-id", required=True)
    p_post.add_argument("--exit-code", type=int, default=0)
    p_post.add_argument("--error-summary", default="")

    # export-source
    p_exp = sub.add_parser("export-source", help="Export SQLite source to snapshot")
    p_exp.add_argument("--source-db", required=True)
    p_exp.add_argument("--run-id", required=True)

    # import-staging
    p_imp = sub.add_parser("import-staging", help="Import verified source manifest to PostgreSQL")
    p_imp.add_argument("--run-id", required=True)
    p_imp.add_argument("--mapping-version", default="v1")

    # verify
    p_ver = sub.add_parser("verify", help="Verify migration results")
    p_ver.add_argument("--run-id", required=True)

    # rollback
    p_rb = sub.add_parser("rollback", help="Rollback a migration run")
    p_rb.add_argument("--run-id", required=True)

    # list-runs
    sub.add_parser("list-runs", help="List all migration runs")

    args = parser.parse_args()
    exit_code = 0
    error_summary = ""

    try:
        if args.cmd == "snapshot-pre":
            snapshot_pre(args.run_id, args.source_db)
        elif args.cmd == "snapshot-post":
            snapshot_post(args.run_id, args.exit_code, args.error_summary)
        elif args.cmd == "export-source":
            export_source(args.source_db, args.run_id)
        elif args.cmd == "import-staging":
            import_staging(args.run_id, args.mapping_version)
        elif args.cmd == "verify":
            result = verify_run(args.run_id)
            print(json.dumps(result, indent=2, ensure_ascii=False))
            if result["overall"] != "PASS":
                exit_code = 1
        elif args.cmd == "rollback":
            rollback_run(args.run_id)
        elif args.cmd == "list-runs":
            runs = list_runs()
            print(json.dumps(runs, indent=2, ensure_ascii=False, default=str))
    except Exception as e:
        logger.error("command failed: %s", e, exc_info=True)
        error_summary = str(e)
        exit_code = 1

    # 自动 snapshot-post（如果命令不是 snapshot-post 本身）
    if args.cmd not in ("snapshot-pre", "snapshot-post") and exit_code != 0:
        try:
            snapshot_post(args.run_id, exit_code, error_summary)
        except Exception as e2:
            logger.error("post-snapshot also failed: %s", e2)

    sys.exit(exit_code)


if __name__ == "__main__":
    main()
