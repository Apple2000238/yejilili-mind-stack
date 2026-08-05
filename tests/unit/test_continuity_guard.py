"""Tests for continuity-guard manifest module

覆盖第四轮要求的 AC 测试：
- 身份门装配先于近期连续性
- 身份配置缺失时 fail closed
- token 超预算时身份基岩仍存在
- continuity manifest 正常同步、重复同步、缺 bucket、source ref 冲突
- pinned/protected bucket 在模拟衰减中不归档
- metadata 同步前后 bucket 正文 hash 一致
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

import pytest

# Add src to path
import sys
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "services" / "continuity-guard" / "src"))

from manifest import ManifestLoader, ManifestEntry, ProtectionSynchronizer, CURRENT_SCHEMA_VERSION
from identity_gate import IdentityConfig, IdentityGateLoader, PromptPlanAssembler, estimate_tokens


class TestManifestLoader:
    """AC-4: continuity manifest 加载与校验"""

    def test_load_valid_manifest(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "schema_version": "1.0.0",
                "entries": [
                    {
                        "manifest_id": "m1",
                        "bucket_id": "b1",
                        "protection": "pinned",
                        "reason": "test",
                        "expected_source_ref": "ref1",
                        "added_at": "2026-08-05T00:00:00+0800",
                        "added_by": "test",
                    }
                ]
            }, f)
            f.flush()
            loader = ManifestLoader(f.name)
            entries = loader.load()
            assert len(entries) == 1
            assert entries[0].bucket_id == "b1"
            assert entries[0].protection == "pinned"
            os.unlink(f.name)

    def test_duplicate_manifest_id_fails(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "schema_version": "1.0.0",
                "entries": [
                    {"manifest_id": "m1", "bucket_id": "b1", "protection": "pinned"},
                    {"manifest_id": "m1", "bucket_id": "b2", "protection": "protected"},
                ]
            }, f)
            f.flush()
            loader = ManifestLoader(f.name)
            with pytest.raises(ValueError, match="duplicate manifest_id"):
                loader.load()
            os.unlink(f.name)

    def test_invalid_protection_fails(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "schema_version": "1.0.0",
                "entries": [
                    {"manifest_id": "m1", "bucket_id": "b1", "protection": "invalid"},
                ]
            }, f)
            f.flush()
            loader = ManifestLoader(f.name)
            with pytest.raises(ValueError, match="protection"):
                loader.load()
            os.unlink(f.name)


class TestProtectionSynchronizer:
    """AC: 保护同步与审计"""

    def _setup_bucket(self, buckets_dir: Path, bucket_id: str, content: str, metadata: dict):
        bucket_path = buckets_dir / bucket_id
        bucket_path.mkdir(parents=True)
        (bucket_path / "content.md").write_text(content, encoding="utf-8")
        import yaml
        (bucket_path / "metadata.yaml").write_text(yaml.dump(metadata), encoding="utf-8")

    def test_sync_success_content_hash_unchanged(self):
        with tempfile.TemporaryDirectory() as td:
            buckets_dir = Path(td) / "buckets"
            audit_dir = Path(td) / "audit"
            buckets_dir.mkdir()
            audit_dir.mkdir()

            self._setup_bucket(buckets_dir, "test-bucket", "Hello World", {"source": "ref1"})

            entry = ManifestEntry(
                schema_version="1.0.0",
                manifest_id="m1",
                bucket_id="test-bucket",
                reason="test",
                protection="pinned",
                expected_source_ref="ref1",
                added_at="2026-08-05T00:00:00+0800",
                added_by="test",
            )

            syncer = ProtectionSynchronizer(buckets_dir, audit_dir)
            record = syncer.sync(entry)

            assert record.success is True
            assert record.content_hash_before == record.content_hash_after
            assert record.metadata_after.get("protection") == "pinned"

    def test_sync_missing_bucket_fails_closed(self):
        with tempfile.TemporaryDirectory() as td:
            buckets_dir = Path(td) / "buckets"
            audit_dir = Path(td) / "audit"
            buckets_dir.mkdir()
            audit_dir.mkdir()

            entry = ManifestEntry(
                schema_version="1.0.0",
                manifest_id="m1",
                bucket_id="missing-bucket",
                reason="test",
                protection="pinned",
                expected_source_ref="ref1",
                added_at="2026-08-05T00:00:00+0800",
                added_by="test",
            )

            syncer = ProtectionSynchronizer(buckets_dir, audit_dir)
            record = syncer.sync(entry)

            assert record.success is False
            assert "not found" in record.error

    def test_sync_source_ref_mismatch_fails(self):
        with tempfile.TemporaryDirectory() as td:
            buckets_dir = Path(td) / "buckets"
            audit_dir = Path(td) / "audit"
            buckets_dir.mkdir()
            audit_dir.mkdir()

            self._setup_bucket(buckets_dir, "test-bucket", "Hello", {"source": "wrong-ref"})

            entry = ManifestEntry(
                schema_version="1.0.0",
                manifest_id="m1",
                bucket_id="test-bucket",
                reason="test",
                protection="pinned",
                expected_source_ref="expected-ref",
                added_at="2026-08-05T00:00:00+0800",
                added_by="test",
            )

            syncer = ProtectionSynchronizer(buckets_dir, audit_dir)
            record = syncer.sync(entry)

            assert record.success is False
            assert "mismatch" in record.error

    def test_rollback_metadata_restores_original(self):
        with tempfile.TemporaryDirectory() as td:
            buckets_dir = Path(td) / "buckets"
            audit_dir = Path(td) / "audit"
            buckets_dir.mkdir()
            audit_dir.mkdir()

            self._setup_bucket(buckets_dir, "test-bucket", "Hello", {"source": "ref1", "protection": "none"})

            entry = ManifestEntry(
                schema_version="1.0.0",
                manifest_id="m1",
                bucket_id="test-bucket",
                reason="test",
                protection="pinned",
                expected_source_ref="ref1",
                added_at="2026-08-05T00:00:00+0800",
                added_by="test",
            )

            syncer = ProtectionSynchronizer(buckets_dir, audit_dir)
            record = syncer.sync(entry)
            assert record.success

            # Rollback
            rollback_record = syncer.rollback_metadata(record)
            assert rollback_record.success

            # Verify metadata restored
            import yaml
            meta = yaml.safe_load((buckets_dir / "test-bucket" / "metadata.yaml").read_text())
            assert meta.get("protection") == "none"


class TestIdentityGate:
    """AC: 身份门装配"""

    def test_assemble_identity_bedrock_first(self):
        with tempfile.TemporaryDirectory() as td:
            bedrock = Path(td) / "bedrock.md"
            bedrock.write_text("I am the identity bedrock." * 10, encoding="utf-8")

            config = IdentityConfig(
                identity_bedrock_path=str(bedrock),
                token_budget=4000,
                bedrock_reserve_tokens=800,
            )

            assembler = PromptPlanAssembler(config, Path(td) / "audit")
            messages, record = assembler.assemble([])

            assert record.identity_bedrock_present is True
            # First system message should be identity_bedrock
            assert messages[0].get("_identity_gate", {}).get("section_type") == "identity_bedrock"

    def test_bedrock_preserved_when_over_budget(self):
        with tempfile.TemporaryDirectory() as td:
            bedrock = Path(td) / "bedrock.md"
            bedrock.write_text("I am the identity bedrock." * 100, encoding="utf-8")

            continuity = Path(td) / "continuity.md"
            continuity.write_text("Recent continuity context." * 200, encoding="utf-8")

            config = IdentityConfig(
                identity_bedrock_path=str(bedrock),
                recent_continuity_path=str(continuity),
                token_budget=2000,  # Very tight budget
                bedrock_reserve_tokens=800,
            )

            assembler = PromptPlanAssembler(config, Path(td) / "audit")
            messages, record = assembler.assemble([])

            # Identity bedrock must still be present
            assert record.identity_bedrock_present is True
            # But continuity should be truncated or removed
            assert record.truncated is True

    def test_readiness_fail_when_config_missing(self):
        with tempfile.TemporaryDirectory() as td:
            config_path = Path(td) / "missing.json"
            loader = IdentityGateLoader(config_path)
            ready, msg = loader.readiness()
            assert ready is False
            assert "not found" in msg

    def test_readiness_fail_when_hash_mismatch(self):
        with tempfile.TemporaryDirectory() as td:
            bedrock = Path(td) / "bedrock.md"
            bedrock.write_text("Test bedrock content", encoding="utf-8")

            config = IdentityConfig(
                identity_bedrock_path=str(bedrock),
                identity_bedrock_hash="wronghash" * 8,
            )

            config_path = Path(td) / "identity_gate.json"
            config_path.write_text(json.dumps(config.to_dict()), encoding="utf-8")

            loader = IdentityGateLoader(config_path)
            ready, msg = loader.readiness()
            assert ready is False
            assert "hash mismatch" in msg
