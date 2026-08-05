"""Continuity Manifest — 连续性保护清单

Schema 定义、加载器、校验器、同步与审计。

纪律：
    - 清单是保护策略的唯一配置来源
    - 同步属于受审计的 YAML metadata 更新，不是正文修改
    - 正文 hash 必须前后一致
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

    def load(self) -> list[ManifestEntry]:
        """加载清单文件，校验 schema，返回条目列表。"""
        if self._loaded:
            return self._entries

        if not self.manifest_path.exists():
            logger.warning("manifest file not found: %s", self.manifest_path)
            self._loaded = True
            return []

        raw = json.loads(self.manifest_path.read_text(encoding="utf-8"))

        # 顶层校验
        if not isinstance(raw, dict):
            raise ValueError("manifest root must be an object")

        schema_version = raw.get("schema_version", "unknown")
        if schema_version != CURRENT_SCHEMA_VERSION:
            logger.warning("manifest schema version mismatch: %s vs %s", schema_version, CURRENT_SCHEMA_VERSION)

        entries_raw = raw.get("entries", [])
        if not isinstance(entries_raw, list):
            raise ValueError("manifest.entries must be a list")

        entries: list[ManifestEntry] = []
        seen_ids: set[str] = set()
        seen_buckets: set[str] = set()

        for idx, e in enumerate(entries_raw):
            if not isinstance(e, dict):
                raise ValueError(f"entry[{idx}] is not an object")

            # 必填字段
            for required in ("manifest_id", "bucket_id", "protection"):
                if required not in e:
                    raise ValueError(f"entry[{idx}] missing required field: {required}")

            # 保护级别白名单
            if e["protection"] not in PROTECTION_LEVELS:
                raise ValueError(
                    f"entry[{idx}] protection '{e['protection']}' not in {PROTECTION_LEVELS}"
                )

            # 唯一性
            mid = e["manifest_id"]
            bid = e["bucket_id"]
            if mid in seen_ids:
                raise ValueError(f"duplicate manifest_id: {mid}")
            if bid in seen_buckets:
                raise ValueError(f"duplicate bucket_id: {bid}")
            seen_ids.add(mid)
            seen_buckets.add(bid)

            entry = ManifestEntry.from_dict(e)
            entries.append(entry)

        self._entries = entries
        self._loaded = True
        logger.info("manifest loaded: %s entries from %s", len(entries), self.manifest_path)
        return entries

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


# ─── Bucket 内容 hash ─────────────────────────────────────────────────────────

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


# ─── YAML metadata 读取/写入（简化实现，实际使用 ruamel.yaml 或 PyYAML）───

def _read_yaml_metadata(bucket_path: Path) -> dict[str, Any]:
    """从 bucket 目录读取 metadata.yaml。"""
    meta_path = bucket_path / "metadata.yaml"
    if not meta_path.exists():
        return {}

    try:
        import yaml
        return yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
    except Exception as e:
        raise RuntimeError(f"YAML parse error in {meta_path}: {e}") from e


def _write_yaml_metadata(bucket_path: Path, metadata: dict[str, Any]) -> None:
    """原子写入 metadata.yaml：临时文件 → 校验 → 替换。"""
    meta_path = bucket_path / "metadata.yaml"

    # 使用临时文件
    fd, tmp_path = tempfile.mkstemp(dir=str(bucket_path), suffix=".yaml.tmp")
    try:
        os.close(fd)
        tmp_file = Path(tmp_path)

        import yaml
        text = yaml.dump(metadata, default_flow_style=False, allow_unicode=True, sort_keys=False)
        tmp_file.write_text(text, encoding="utf-8")

        # 校验：重新读取确保可解析
        parsed = yaml.safe_load(tmp_file.read_text(encoding="utf-8"))
        if parsed is None and metadata:
            raise RuntimeError("YAML round-trip validation failed: parsed is None")

        # 原子替换
        os.replace(tmp_file, meta_path)
    except Exception:
        # 清理临时文件
        if Path(tmp_path).exists():
            Path(tmp_path).unlink()
        raise


# ─── 保护同步器 ───────────────────────────────────────────────────────────────

class ProtectionSynchronizer:
    """将 manifest 的 pinned/protected 同步到 Nocturne bucket metadata。

    约束：
    1. 只修改 metadata，不碰正文
    2. 正文 hash 前后必须一致
    3. 使用临时文件 + 原子替换
    4. bucket 不存在 / source ref 不符 / YAML 损坏 / 写入失败 → fail closed
    5. 记录完整审计日志
    """

    def __init__(self, buckets_dir: Path | str, audit_dir: Path | str) -> None:
        self.buckets_dir = Path(buckets_dir)
        self.audit_dir = Path(audit_dir)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def sync(self, entry: ManifestEntry) -> SyncAuditRecord:
        """执行单次同步，返回审计记录。"""
        sync_id = f"sync-{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")

        bucket_path = self.buckets_dir / entry.bucket_id
        content_hash_before = ""
        content_hash_after = ""
        metadata_before: dict[str, Any] = {}
        metadata_after: dict[str, Any] = {}
        success = False
        error: str | None = None

        try:
            # 1. bucket 必须存在
            if not bucket_path.exists():
                raise FileNotFoundError(f"bucket not found: {bucket_path}")

            # 2. 计算正文 hash（同步前）
            content_file = bucket_path / "content.md"
            if content_file.exists():
                content_hash_before = _hash_file(content_file)
            else:
                content_hash_before = "no-content"

            # 3. 读取当前 metadata
            metadata_before = _read_yaml_metadata(bucket_path)

            # 4. source ref 校验（如果 expected_source_ref 非空）
            current_source = metadata_before.get("source", "")
            if entry.expected_source_ref and current_source != entry.expected_source_ref:
                raise ValueError(
                    f"source ref mismatch: expected '{entry.expected_source_ref}', got '{current_source}'"
                )

            # 5. 准备新 metadata（深拷贝，只改 protection 相关字段）
            metadata_after = copy.deepcopy(metadata_before)
            metadata_after["protection"] = entry.protection
            metadata_after["manifest_id"] = entry.manifest_id
            metadata_after["protection_synced_at"] = started_at

            # 6. 原子写入
            _write_yaml_metadata(bucket_path, metadata_after)

            # 7. 验证：重新读取并比对
            re_read = _read_yaml_metadata(bucket_path)
            if re_read.get("protection") != entry.protection:
                raise RuntimeError("metadata write verification failed: protection not set")

            # 8. 重新计算正文 hash（必须一致）
            if content_file.exists():
                content_hash_after = _hash_file(content_file)
            else:
                content_hash_after = "no-content"

            if content_hash_before != content_hash_after:
                # 严重错误：正文被修改了，尝试回滚 metadata
                _write_yaml_metadata(bucket_path, metadata_before)
                raise RuntimeError(
                    f"CONTENT CORRUPTION DETECTED: hash before={content_hash_before} after={content_hash_after}. "
                    f"Metadata rolled back."
                )

            success = True
            logger.info("sync success: bucket=%s protection=%s", entry.bucket_id, entry.protection)

        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error("sync failed: %s", error)

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

        # 写入审计日志
        self._append_audit(record)
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
        """根据审计记录回滚 metadata 到同步前状态。"""
        sync_id = f"rollback-{hashlib.sha256(os.urandom(16)).hexdigest()[:16]}"
        started_at = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        bucket_path = self.buckets_dir / audit_record.bucket_id
        success = False
        error: str | None = None

        try:
            if not bucket_path.exists():
                raise FileNotFoundError(f"bucket not found: {bucket_path}")

            # 回滚到同步前的 metadata
            _write_yaml_metadata(bucket_path, audit_record.metadata_before)

            # 验证
            re_read = _read_yaml_metadata(bucket_path)
            if re_read.get("protection") == audit_record.metadata_before.get("protection"):
                success = True
                logger.info("rollback success: bucket=%s", audit_record.bucket_id)
            else:
                raise RuntimeError("rollback verification failed")
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            logger.error("rollback failed: %s", error)

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
        return record

    def _append_audit(self, record: SyncAuditRecord) -> None:
        """追加审计记录到日文件。"""
        date_str = time.strftime("%Y%m%d")
        audit_file = self.audit_dir / f"manifest-sync-audit-{date_str}.jsonl"
        with open(audit_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(record.to_dict(), ensure_ascii=False, default=str) + "\n")
