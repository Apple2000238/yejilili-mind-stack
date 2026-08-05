"""Continuity Manifest — 连续性保护清单

Schema 定义、加载器、校验器、同步与审计。

纪律：
    - 清单是保护策略的唯一配置来源
    - 缺失、损坏或 schema 不兼容时必须 fail closed
    - 同步属于受审计的受控 metadata 更新，不是正文修改
    - 正文 hash 必须前后一致
    - 使用 Nocturne 原生 Markdown + YAML frontmatter 格式
    - fail closed：任何异常都不继续
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("continuity-guard.manifest")

# ─── Schema 常量 ──────────────────────────────────────────────────────────────

CURRENT_SCHEMA_VERSION = "1.0.0"
PROTECTION_LEVELS = {"pinned", "protected"}


# ─── 数据类 ───────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ManifestEntry:
    """清单单条目 — 不可变值对象。"""

    schema_version: str
    manifest_id: str
    bucket_id: str
    reason: str
    protection: str  # "pinned" | "protected"
    expected_source_ref: str
    added_at: str
    added_by: str
    active: bool = True

    def __post_init__(self) -> None:
        if self.protection not in PROTECTION_LEVELS:
            raise ValueError(f"protection must be one of {PROTECTION_LEVELS}, got '{self.protection}'")
        if not self.manifest_id:
            raise ValueError("manifest_id is required")
        if not self.bucket_id:
            raise ValueError("bucket_id is required")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "manifest_id": self.manifest_id,
            "bucket_id": self.bucket_id,
            "reason": self.reason,
            "protection": self.protection,
            "expected_source_ref": self.expected_source_ref,
            "added_at": self.added_at,
            "added_by": self.added_by,
            "active": self.active,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ManifestEntry":
        return cls(
            schema_version=d.get("schema_version", CURRENT_SCHEMA_VERSION),
            manifest_id=d["manifest_id"],
            bucket_id=d["bucket_id"],
            reason=d.get("reason", ""),
            protection=d["protection"],
            expected_source_ref=d.get("expected_source_ref", ""),
            added_at=d.get("added_at", ""),
            added_by=d.get("added_by", ""),
            active=d.get("active", True),
        )


@dataclass
class SyncAuditRecord:
    """单次同步审计记录。"""

    sync_id: str
    manifest_id: str
    bucket_id: str
    operation: str  # "sync_pinned" | "sync_protected" | "rollback_metadata"
    content_hash_before: str
    content_hash_after: str
    metadata_before: dict[str, Any]
    metadata_after: dict[str, Any]
    manifest_entry: dict[str, Any]
    started_at: str
    completed_at: str
    success: bool
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sync_id": self.sync_id,
            "manifest_id": self.manifest_id,
            "bucket_id": self.bucket_id,
            "operation": self.operation,
            "content_hash_before": self.content_hash_before,
            "content_hash_after": self.content_hash_after,
            "metadata_before": self.metadata_before,
            "metadata_after": self.metadata_after,
            "manifest_entry": self.manifest_entry,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "success": self.success,
            "error": self.error,
        }


# ─── 清单加载器 ───────────────────────────────────────────────────────────────

class ManifestLoader:
    """加载并校验 continuity_manifest.json。"""

    def __init__(self, manifest_path: Path | str) -> None:
        self.manifest_path = Path(manifest_path)
        self._entries: list[ManifestEntry] = []
        self._loaded = False
        self._load_error: str | None = None

    def load(self) -> list[ManifestEntry]:
        """加载清单文件，校验 schema，返回条目列表。

        失败时记录错误并返回空列表（调用方应检查 readiness）。
        """
        if self._loaded:
            return self._entries

        if not self.manifest_path.exists():
            self._load_error = f"manifest file not found: {self.manifest_path}"
            logger.error(self._load_error)
            self._loaded = True
            return []

        try:
            raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as e:
            self._load_error = f"manifest JSON parse error: {e}"
            logger.error(self._load_error)
            self._loaded = True
            return []

        # 顶层校验
        if not isinstance(raw, dict):
            self._load_error = "manifest root must be an object"
            logger.error(self._load_error)
            self._loaded = True
            return []

        schema_version = raw.get("schema_version", "unknown")
        if schema_version != CURRENT_SCHEMA_VERSION:
            self._load_error = f"manifest schema version mismatch: {schema_version} vs {CURRENT_SCHEMA_VERSION}"
            logger.error(self._load_error)
            self._loaded = True
            return []

        entries_raw = raw.get("entries", [])
        if not isinstance(entries_raw, list):
            self._load_error = "manifest.entries must be a list"
            logger.error(self._load_error)
            self._loaded = True
            return []

        if len(entries_raw) == 0:
            self._load_error = "manifest.entries is empty"
            logger.error(self._load_error)
            self._loaded = True
            return []

        entries: list[ManifestEntry] = []
        seen_ids: set[str] = set()
        seen_buckets: set[str] = set()

        for idx, e in enumerate(entries_raw):
            if not isinstance(e, dict):
                self._load_error = f"entry[{idx}] is not an object"
                logger.error(self._load_error)
                self._loaded = True
                return []

            # 必填字段
            for required in ("manifest_id", "bucket_id", "protection"):
                if required not in e:
                    self._load_error = f"entry[{idx}] missing required field: {required}"
                    logger.error(self._load_error)
                    self._loaded = True
                    return []

            # 保护级别白名单
            if e["protection"] not in PROTECTION_LEVELS:
                self._load_error = f"entry[{idx}] protection '{e['protection']}' not in {PROTECTION_LEVELS}"
                logger.error(self._load_error)
                self._loaded = True
                return []

            # 唯一性
            mid = e["manifest_id"]
            bid = e["bucket_id"]
            if mid in seen_ids:
                self._load_error = f"duplicate manifest_id: {mid}"
                logger.error(self._load_error)
                self._loaded = True
                return []
            if bid in seen_buckets:
                self._load_error = f"duplicate bucket_id: {bid}"
                logger.error(self._load_error)
                self._loaded = True
                return []
            seen_ids.add(mid)
            seen_buckets.add(bid)

            entry = ManifestEntry.from_dict(e)
            entries.append(entry)

        self._entries = entries
        self._loaded = True
        logger.info("manifest loaded: %s entries from %s", len(entries), self.manifest_path)
        return entries

    def readiness(self) -> tuple[bool, str]:
        """Readiness 检查：清单必须存在、可解析、有有效条目。"""
        if not self._loaded:
            self.load()
        if self._load_error:
            return False, f"manifest not ready: {self._load_error}"
        if not self._entries:
            return False, "manifest not ready: no entries"
        return True, f"manifest ready: {len(self._entries)} entries"

    def get_active(self) -> list[ManifestEntry]:
        """返回 active=True 的条目。"""
        return [e for e in self.load() if e.active]

    def get_by_bucket(self, bucket_id: str) -> ManifestEntry | None:
        """按 bucket_id 查找条目。"""
        for e in self.load():
            if e.bucket_id == bucket_id:
                return e
        return None

    def get_pinned_buckets(self) -> set[str]:
        """返回所有 pinned/protected 的 bucket_id 集合。"""
        return {e.bucket_id for e in self.get_active() if e.protection in PROTECTION_LEVELS}


# ─── Nocturne Bucket 定位器（与上游 BucketManager._find_bucket_file 一致）──────

class NocturneBucketLocator:
    """在 Nocturne 的 permanent/dynamic/archive/feel 目录中定位 bucket 文件。"""

    DIRS = ["permanent", "dynamic", "archive", "feel"]

    def __init__(self, buckets_base_dir: Path | str) -> None:
        self.base_dir = Path(buckets_base_dir)

    def find_bucket_file(self, bucket_id: str) -> Path | None:
        """递归查找 bucket ID 对应的 .md 文件。

        匹配逻辑与 Nocturne BucketManager._find_bucket_file 一致：
        - 文件名 == bucket_id + ".md"
        - 文件名以 "_" + bucket_id + ".md" 结尾
        """
        if not bucket_id:
            return None
        for subdir in self.DIRS:
            dir_path = self.base_dir / subdir
            if not dir_path.exists():
                continue
            for root, _dirs, files in os.walk(str(dir_path)):
                for fname in files:
                    if not fname.endswith(".md"):
                        continue
                    name_part = fname[:-3]  # remove .md
                    if name_part == bucket_id or name_part.endswith(f"_{bucket_id}"):
                        return Path(root) / fname
        return None


# ─── 文件 hash ────────────────────────────────────────────────────────────────

def _hash_file(path: Path) -> str:
    """计算文件 SHA256。"""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _hash_text(text: str) -> str:
    """计算文本 SHA256。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# ─── Frontmatter 读写（使用 python-frontmatter）────────────────────────────────

def _read_frontmatter(file_path: Path) -> tuple[dict[str, Any], str, str]:
    """从 Markdown 文件读取 frontmatter metadata 和 body。

    返回：(metadata_dict, body_text, full_file_hash)
    """
    try:
        import frontmatter
    except ImportError as e:
        raise RuntimeError("python-frontmatter is required") from e

    post = frontmatter.load(str(file_path))
    metadata = dict(post.metadata)
    body = post.content
    full_hash = _hash_file(file_path)
    return metadata, body, full_hash


def _write_frontmatter(
    file_path: Path,
    metadata: dict[str, Any],
    body: str,
    verify: bool = True,
) -> None:
    """原子写入 Markdown 文件：临时文件 → 回读校验 → 替换。

    保持 frontmatter + body 格式不变，只修改 metadata。
    """
    try:
        import frontmatter
    except ImportError as e:
        raise RuntimeError("python-frontmatter is required") from e

    # 使用与文件同目录的临时文件（保证同文件系统，os.replace 原子）
    parent_dir = file_path.parent
    fd, tmp_path = tempfile.mkstemp(dir=str(parent_dir), suffix=".md.tmp")
    try:
        os.close(fd)
        tmp_file = Path(tmp_path)

        post = frontmatter.Post(body, **metadata)
        tmp_file.write_text(frontmatter.dumps(post), encoding="utf-8")

        if verify:
            # 回读校验
            verify_post = frontmatter.load(str(tmp_file))
            if dict(verify_post.metadata) != metadata:
                raise RuntimeError("frontmatter round-trip metadata mismatch")
            if verify_post.content != body:
                raise RuntimeError("frontmatter round-trip body mismatch")

        # 原子替换
        os.replace(tmp_file, file_path)
    except Exception:
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
        raise


# ─── 保护同步器 ───────────────────────────────────────────────────────────────

class ProtectionSynchronizer:
    """将 manifest 的 pinned/protected 同步到 Nocturne bucket frontmatter。

    约束：
    1. 使用 Nocturne 原生 Markdown + frontmatter 格式
    2. 只修改 frontmatter 中的 pinned/protected 布尔字段，不碰正文 body
    3. 正文 body hash 前后必须一致
    4. 使用同目录临时文件 + flush/fsync + 回读校验 + 原子替换
    5. bucket 不存在 / source ref 不符 / frontmatter 损坏 / 写入失败 → fail closed
    6. 记录完整审计日志（数据库 + 本地 JSONL）
    """

    def __init__(
        self,
        buckets_base_dir: Path | str,
        audit_dir: Path | str,
        pg_dsn: str | None = None,
    ) -> None:
        self.locator = NocturneBucketLocator(buckets_base_dir)
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)
        self.pg_dsn = pg_dsn

    def sync(self, entry: ManifestEntry) -> SyncAuditRecord:
        """执行单次同步，返回审计记录。"""
        import uuid

        sync_id = f"sync-{uuid.uuid4().hex[:16]}"
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        content_hash_before = ""
        content_hash_after = ""
        metadata_before: dict[str, Any] = {}
        metadata_after: dict[str, Any] = {}
        success = False
        error: str | None = None

        try:
            # 1. 定位 bucket 文件
            file_path = self.locator.find_bucket_file(entry.bucket_id)
            if not file_path:
                raise FileNotFoundError(f"bucket file not found for id={entry.bucket_id}")

            # 2. 读取当前 frontmatter + body
            metadata_before, body, content_hash_before = _read_frontmatter(file_path)

            # 3. source ref 校验（如果 expected_source_ref 非空）
            current_source = metadata_before.get("source", "")
            if entry.expected_source_ref and current_source != entry.expected_source_ref:
                raise ValueError(
                    f"source ref mismatch: expected '{entry.expected_source_ref}', got '{current_source}'"
                )

            # 4. 准备新 metadata（深拷贝，只改 pinned/protected 布尔字段）
            metadata_after = copy.deepcopy(metadata_before)

            # 按 entry.protection 设置布尔字段
            if entry.protection == "pinned":
                metadata_after["pinned"] = True
                metadata_after.pop("protected", None)  # 清理互斥字段
            elif entry.protection == "protected":
                metadata_after["protected"] = True
                metadata_after.pop("pinned", None)

            metadata_after["manifest_id"] = entry.manifest_id
            metadata_after["protection_synced_at"] = started_at

            # 5. 原子写入（保持 body 不变）
            _write_frontmatter(file_path, metadata_after, body, verify=True)

            # 6. 验证：重新读取并比对 frontmatter
            re_read_meta, re_read_body, content_hash_after = _read_frontmatter(file_path)
            protection_ok = False
            if entry.protection == "pinned":
                protection_ok = re_read_meta.get("pinned") is True
            elif entry.protection == "protected":
                protection_ok = re_read_meta.get("protected") is True
            if not protection_ok:
                raise RuntimeError("frontmatter write verification failed: protection bool not set")

            # 7. body hash 必须一致
            body_hash_before = _hash_text(body)
            body_hash_after = _hash_text(re_read_body)
            if body_hash_before != body_hash_after:
                # 严重错误：正文被修改了，尝试回滚 frontmatter
                _write_frontmatter(file_path, metadata_before, body, verify=False)
                raise RuntimeError(
                    f"CONTENT CORRUPTION DETECTED: body hash before={body_hash_before} "
                    f"after={body_hash_after}. Frontmatter rolled back."
                )

            success = True
            logger.info("sync success: bucket=%s protection=%s", entry.bucket_id, entry.protection)

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error("sync failed: bucket=%s error=%s", entry.bucket_id, error, exc_info=True)

        completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        record = SyncAuditRecord(
            sync_id=sync_id,
            manifest_id=entry.manifest_id,
            bucket_id=entry.bucket_id,
            operation=f"sync_{entry.protection}",
            content_hash_before=content_hash_before,
            content_hash_after=content_hash_after,
            metadata_before=metadata_before,
            metadata_after=metadata_after,
            manifest_entry=entry.to_dict(),
            started_at=started_at,
            completed_at=completed_at,
            success=success,
            error=error,
        )

        # 写入审计（数据库 + 本地 JSONL）
        self._append_audit(record)
        self._append_db_audit(record)
        return record

    def sync_all(self, manifest: ManifestLoader) -> list[SyncAuditRecord]:
        """同步清单中所有 active 条目，返回审计记录列表。"""
        records: list[SyncAuditRecord] = []
        for entry in manifest.get_active():
            rec = self.sync(entry)
            records.append(rec)
            if not rec.success:
                # fail closed：第一个失败就停止
                logger.error("sync_all halted due to failure on bucket=%s", entry.bucket_id)
                break
        return records

    def rollback_metadata(self, audit_record: SyncAuditRecord) -> SyncAuditRecord:
        """根据审计记录回滚 frontmatter 到同步前状态。"""
        import uuid

        sync_id = f"rollback-{uuid.uuid4().hex[:16]}"
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        success = False
        error: str | None = None

        try:
            file_path = self.locator.find_bucket_file(audit_record.bucket_id)
            if not file_path:
                raise FileNotFoundError(f"bucket file not found for id={audit_record.bucket_id}")

            # 读取当前状态
            current_meta, current_body, _ = _read_frontmatter(file_path)
            current_body_hash = _hash_text(current_body)

            # 回滚到同步前的 metadata（保持当前 body）
            _write_frontmatter(file_path, audit_record.metadata_before, current_body, verify=True)

            # 验证
            re_read_meta, re_read_body, _ = _read_frontmatter(file_path)
            re_read_body_hash = _hash_text(re_read_body)

            # 确认回滚后的 body 不变
            if current_body_hash != re_read_body_hash:
                raise RuntimeError(f"rollback corrupted body: hash changed")

            # 确认保护字段已恢复
            before_pinned = audit_record.metadata_before.get("pinned", False)
            before_protected = audit_record.metadata_before.get("protected", False)
            if before_pinned and not re_read_meta.get("pinned"):
                raise RuntimeError("rollback verification failed: pinned not restored")
            if before_protected and not re_read_meta.get("protected"):
                raise RuntimeError("rollback verification failed: protected not restored")

            success = True
            logger.info("rollback success: bucket=%s", audit_record.bucket_id)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error("rollback failed: bucket=%s error=%s", audit_record.bucket_id, error, exc_info=True)

        completed_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        record = SyncAuditRecord(
            sync_id=sync_id,
            manifest_id=audit_record.manifest_id,
            bucket_id=audit_record.bucket_id,
            operation="rollback_metadata",
            content_hash_before=audit_record.content_hash_after,
            content_hash_after=audit_record.content_hash_before,
            metadata_before=audit_record.metadata_after,
            metadata_after=audit_record.metadata_before,
            manifest_entry=audit_record.manifest_entry,
            started_at=started_at,
            completed_at=completed_at,
            success=success,
            error=error,
        )
        self._append_audit(record)
        self._append_db_audit(record)
        return record

    def _append_audit(self, record: SyncAuditRecord) -> None:
        """追加审计记录到日文件。"""
        date_str = time.strftime("%Y%m%d")
        audit_file = self.audit_dir / f"manifest-sync-audit-{date_str}.jsonl"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")

    def _append_db_audit(self, record: SyncAuditRecord) -> None:
        """追加审计记录到 PostgreSQL manifest_sync_audit 表。"""
        if not self.pg_dsn:
            return
        try:
            import psycopg
            with psycopg.connect(self.pg_dsn) as pg:
                pg.execute(
                    """
                    INSERT INTO manifest_sync_audit
                    (sync_id, manifest_id, bucket_id, operation, content_hash_before, content_hash_after,
                     metadata_before, metadata_after, manifest_entry, started_at, completed_at, success, error)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (sync_id) DO NOTHING
                    """,
                    (
                        record.sync_id, record.manifest_id, record.bucket_id, record.operation,
                        record.content_hash_before, record.content_hash_after,
                        json.dumps(record.metadata_before, ensure_ascii=False, default=str),
                        json.dumps(record.metadata_after, ensure_ascii=False, default=str),
                        json.dumps(record.manifest_entry, ensure_ascii=False, default=str),
                        record.started_at, record.completed_at, record.success, record.error,
                    ),
                )
                pg.commit()
        except Exception as e:
            logger.error("failed to write manifest audit to DB: %s", e, exc_info=True)
