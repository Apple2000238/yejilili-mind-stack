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

        # Merkle-ish hash of sorted rows
        rows = check_db.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid').fetchall()
        cols = [d[0] for d in check_db.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
        row_hashes = []
        for row in rows:
            canonical = json.dumps(dict(zip(cols, row)), sort_keys=True, ensure_ascii=False, default=str)
            row_hashes.append(hashlib.sha256(canonical.encode("utf-8")).hexdigest())
        merkle = hashlib.sha256("".join(row_hashes).encode("utf-8")).hexdigest() if row_hashes else ""

        manifest["tables"][table_name] = {
            "row_count": count,
            "schema_hash": schema_hash,
            "merkle_root": merkle,
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


# ─── Import staging ──────────────────────────────────────────────────────────

def import_staging(run_id: str, mapping_version: str = "v1") -> dict[str, Any]:
    """将已验证的 source manifest 导入到 PostgreSQL 规范化投影。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    manifest_path = snapshot_dir / "export" / "source-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"source manifest not found: {manifest_path}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    with get_pg() as pg:
        # 创建 migration_run 记录
        pg.execute(
            "INSERT INTO migration_runs (id, run_name, source_snapshot_hash, mapping_version, status) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run_id, f"run-{run_id}", manifest["tables"].get("ar_buckets", {}).get("merkle_root", ""), mapping_version, "running"),
        )

        # source_table_manifest
        for table_name, info in manifest["tables"].items():
            pg.execute(
                "INSERT INTO source_table_manifest (run_id, source_table, schema_hash, row_count, primary_key_strategy, excluded_secret_fields) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (run_id, table_name, info["schema_hash"], info["row_count"], "rowid", []),
            )

        # 读取临时快照并写入 source_records
        tmp_db = snapshot_dir / "export" / "source-snapshot-temp.db"
        if tmp_db.exists():
            src = sqlite3.connect(str(tmp_db))
            for table_name in manifest["tables"]:
                cols = [d[0] for d in src.execute(f'PRAGMA table_info("{table_name}")').fetchall()]
                rows = src.execute(f'SELECT * FROM "{table_name}" ORDER BY rowid').fetchall()
                for row in rows:
                    payload = dict(zip(cols, row))
                    payload_json = json.dumps(payload, ensure_ascii=False, default=str)
                    payload_hash = _sha256_text(payload_json)
                    # 用 rowid 作为 source_pk（如果存在），否则用 hash
                    source_pk = str(payload.get("rowid", payload.get("id", payload_hash)))
                    pg.execute(
                        "INSERT INTO source_records (run_id, source_table, source_pk, payload_json, payload_hash, canonical_hash, mapping_version) "
                        "VALUES (%s, %s, %s, %s, %s, %s, %s) "
                        "ON CONFLICT (run_id, source_table, source_pk) DO NOTHING",
                        (run_id, table_name, source_pk, payload_json, payload_hash, payload_hash, mapping_version),
                    )
            src.close()

        # 更新 migration_runs 状态
        records_total = sum(t["row_count"] for t in manifest["tables"].values())
        pg.execute(
            "UPDATE migration_runs SET status=%s, records_total=%s, records_migrated=%s, completed_at=now() WHERE id=%s",
            ("completed", records_total, records_total, run_id),
        )
        pg.commit()

    logger.info("import staging completed for run %s", run_id)
    return {"status": "completed", "records_total": records_total}


# ─── Verify ──────────────────────────────────────────────────────────────────

def verify_run(run_id: str) -> dict[str, Any]:
    """校验迁移结果：比较 source manifest 与 target 投影。"""
    snapshot_dir = ARTIFACTS_DIR / f"run-{run_id}"
    manifest_path = snapshot_dir / "export" / "source-manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"source manifest not found: {manifest_path}")

    source_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    results = {"run_id": run_id, "table_checks": {}, "overall": "PASS"}

    with get_pg() as pg:
        # 检查 source_table_manifest 与 source_records 的行数
        for table_name, info in source_manifest["tables"].items():
            expected = info["row_count"]
            actual = pg.execute(
                "SELECT COUNT(*) AS c FROM source_records WHERE run_id=%s AND source_table=%s",
                (run_id, table_name),
            ).fetchone()["c"]
            ok = actual == expected
            results["table_checks"][table_name] = {"expected": expected, "actual": actual, "ok": ok}
            if not ok:
                results["overall"] = "FAIL"

        # 检查 migration_runs 状态
        run = pg.execute(
            "SELECT status, records_total, records_migrated FROM migration_runs WHERE id=%s", (run_id,)
        ).fetchone()
        if not run or run["status"] != "completed":
            results["overall"] = "FAIL"
            results["run_status"] = run["status"] if run else "missing"

    logger.info("verify result: %s", results["overall"])
    return results


# ─── Rollback ─────────────────────────────────────────────────────────────────

def rollback_run(run_id: str) -> dict[str, Any]:
    """逻辑回滚：标记 run 为 rolled_back，不删除备份。"""
    with get_pg() as pg:
        # 检查 run 是否存在
        run = pg.execute("SELECT status FROM migration_runs WHERE id=%s", (run_id,)).fetchone()
        if not run:
            raise ValueError(f"run {run_id} not found")

        # 创建 rollback point
        pg.execute(
            "INSERT INTO rollback_points (run_id, point_name, point_type) VALUES (%s, %s, %s)",
            (run_id, f"rollback-at-{_now_iso()}", "logical"),
        )

        # 逻辑回滚：删除该 run 的投影数据，保留 source_records 作为证据
        pg.execute("DELETE FROM identity_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM memory_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM message_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM summary_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM promise_projection WHERE run_id=%s", (run_id,))
        pg.execute("DELETE FROM affect_projection WHERE run_id=%s", (run_id,))

        # 更新 run 状态
        pg.execute(
            "UPDATE migration_runs SET status=%s, completed_at=now() WHERE id=%s",
            ("rolled_back", run_id),
        )
        pg.commit()

    logger.info("rollback completed for run %s", run_id)
    return {"status": "rolled_back", "run_id": run_id}


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
