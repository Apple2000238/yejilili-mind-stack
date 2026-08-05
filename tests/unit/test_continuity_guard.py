"""Tests for continuity-guard — 静态验收修复后版本

覆盖场景：
- Manifest 加载与 fail-closed 语义（P0-03）
- Nocturne frontmatter 格式保护同步（P0-01/P0-02）
- 身份门五段装配顺序（P0-05）
- 事件 envelope payload_hash 校验（P1-01）
- Drive → 心潮十二维映射（P0-09）
- 回环抑制 causation 检查（P0-08）
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


def _make_frontmatter_md(path: Path, metadata: dict, body: str) -> None:
    """创建 Nocturne 格式的 frontmatter + body Markdown 文件。"""
    try:
        import frontmatter
        post = frontmatter.Post(body, **metadata)
        path.write_text(frontmatter.dumps(post), encoding="utf-8")
    except ImportError:
        # Fallback: manual YAML frontmatter
        yaml_lines = ["---"]
        for k, v in metadata.items():
            if isinstance(v, list):
                yaml_lines.append(f"{k}:")
                for item in v:
                    yaml_lines.append(f"  - {item}")
            elif isinstance(v, bool):
                yaml_lines.append(f"{k}: {str(v).lower()}")
            else:
                yaml_lines.append(f"{k}: {v}")
        yaml_lines.extend(["---", "", body])
        path.write_text("\n".join(yaml_lines), encoding="utf-8")


class TestManifestLoader:
    """P0-03: manifest 缺失/损坏时 fail closed"""

    def test_manifest_missing_readiness_fails(self):
        from manifest import ManifestLoader
        loader = ManifestLoader("/nonexistent/path/manifest.json")
        ready, msg = loader.readiness()
        assert ready is False
        assert "not found" in msg

    def test_manifest_empty_entries_readiness_fails(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"schema_version": "1.0.0", "entries": []}, f)
            f.flush()
            loader = ManifestLoader(f.name)
            ready, msg = loader.readiness()
            assert ready is False
            assert "empty" in msg or "no entries" in msg
            os.unlink(f.name)

    def test_manifest_schema_mismatch_readiness_fails(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({"schema_version": "0.9.0", "entries": [{"manifest_id": "m1", "bucket_id": "b1", "protection": "pinned"}]}, f)
            f.flush()
            loader = ManifestLoader(f.name)
            ready, msg = loader.readiness()
            assert ready is False
            assert "mismatch" in msg
            os.unlink(f.name)

    def test_manifest_valid_readiness_passes(self):
        from manifest import ManifestLoader
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump({
                "schema_version": "1.0.0",
                "entries": [{"manifest_id": "m1", "bucket_id": "b1", "protection": "pinned", "reason": "", "expected_source_ref": "", "added_at": "", "added_by": ""}]
            }, f)
            f.flush()
            loader = ManifestLoader(f.name)
            ready, msg = loader.readiness()
            assert ready is True
            assert "1 entries" in msg
            os.unlink(f.name)


class TestNocturneFrontmatterSync:
    """P0-01/P0-02: Nocturne 原生 frontmatter 格式 + 布尔 pinned/protected"""

    def test_sync_sets_pinned_bool(self):
        from manifest import ManifestLoader, ManifestEntry, ProtectionSynchronizer
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "buckets"
            perm = base / "permanent" / "test-domain"
            perm.mkdir(parents=True)
            md_file = perm / "mybucket_b1.md"
            _make_frontmatter_md(md_file, {"id": "b1", "source": "ref1"}, "Hello body")

            entry = ManifestEntry(
                schema_version="1.0.0", manifest_id="m1", bucket_id="b1",
                reason="test", protection="pinned", expected_source_ref="ref1",
                added_at="2026-08-05T00:00:00+0800", added_by="test",
            )
            syncer = ProtectionSynchronizer(base, Path(td) / "audit")
            record = syncer.sync(entry)

            assert record.success is True
            # 验证 frontmatter 中 pinned=True（布尔）
            import frontmatter
            post = frontmatter.load(str(md_file))
            assert post.get("pinned") is True
            assert "protection" not in post.metadata  # 不应有字符串 protection

    def test_sync_content_hash_unchanged(self):
        from manifest import ManifestLoader, ManifestEntry, ProtectionSynchronizer
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "buckets"
            perm = base / "permanent" / "domain"
            perm.mkdir(parents=True)
            md_file = perm / "b1.md"
            body = "Original body content\nLine 2"
            _make_frontmatter_md(md_file, {"id": "b1", "source": "ref1"}, body)

            entry = ManifestEntry(
                schema_version="1.0.0", manifest_id="m1", bucket_id="b1",
                reason="test", protection="protected", expected_source_ref="ref1",
                added_at="2026-08-05T00:00:00+0800", added_by="test",
            )
            syncer = ProtectionSynchronizer(base, Path(td) / "audit")
            record = syncer.sync(entry)

            assert record.success is True
            # body hash 前后一致
            import frontmatter
            post = frontmatter.load(str(md_file))
            assert post.content == body

    def test_sync_rollback_restores_frontmatter(self):
        from manifest import ManifestLoader, ManifestEntry, ProtectionSynchronizer
        with tempfile.TemporaryDirectory() as td:
            base = Path(td) / "buckets"
            perm = base / "permanent" / "domain"
            perm.mkdir(parents=True)
            md_file = perm / "b1.md"
            _make_frontmatter_md(md_file, {"id": "b1", "source": "ref1", "pinned": False}, "Body")

            entry = ManifestEntry(
                schema_version="1.0.0", manifest_id="m1", bucket_id="b1",
                reason="test", protection="pinned", expected_source_ref="ref1",
                added_at="2026-08-05T00:00:00+0800", added_by="test",
            )
            syncer = ProtectionSynchronizer(base, Path(td) / "audit")
            record = syncer.sync(entry)
            assert record.success

            rollback = syncer.rollback_metadata(record)
            assert rollback.success is True

            import frontmatter
            post = frontmatter.load(str(md_file))
            assert post.get("pinned") is False


class TestIdentityGateAssembly:
    """P0-05: 五段装配顺序"""

    def test_five_section_order(self):
        from identity_gate import IdentityConfig, PromptPlanAssembler
        with tempfile.TemporaryDirectory() as td:
            core = Path(td) / "core.md"
            core.write_text("Core instruction", encoding="utf-8")
            bedrock = Path(td) / "bedrock.md"
            bedrock.write_text("Identity bedrock", encoding="utf-8")
            memory = Path(td) / "memory.md"
            memory.write_text("Long term memory", encoding="utf-8")
            continuity = Path(td) / "continuity.md"
            continuity.write_text("Recent continuity", encoding="utf-8")

            config = IdentityConfig(
                core_instruction_path=str(core),
                identity_bedrock_path=str(bedrock),
                identity_bedrock_hash="",
                long_term_memory_path=str(memory),
                recent_continuity_path=str(continuity),
                token_budget=4000,
                bedrock_reserve_tokens=800,
                model_hard_limit=8192,
            )
            assembler = PromptPlanAssembler(config, Path(td) / "audit")
            messages, record = assembler.assemble([])

            # 验证五段顺序
            sections = [s["section_type"] for s in record.sections]
            assert sections == ["core_instruction", "identity_bedrock", "long_term_memory", "recent_continuity"]

    def test_core_instruction_not_truncatable(self):
        from identity_gate import IdentityConfig, PromptPlanAssembler
        with tempfile.TemporaryDirectory() as td:
            core = Path(td) / "core.md"
            core.write_text("Core" * 500, encoding="utf-8")  # 大文件
            bedrock = Path(td) / "bedrock.md"
            bedrock.write_text("Bedrock" * 500, encoding="utf-8")

            config = IdentityConfig(
                core_instruction_path=str(core),
                identity_bedrock_path=str(bedrock),
                identity_bedrock_hash="",
                token_budget=100,  # 极小预算
                bedrock_reserve_tokens=800,
                model_hard_limit=8192,
            )
            assembler = PromptPlanAssembler(config, Path(td) / "audit")
            messages, record = assembler.assemble([])

            # core_instruction 和 identity_bedrock 都存在（不可截断）
            sections = [s["section_type"] for s in record.sections]
            assert "core_instruction" in sections
            assert "identity_bedrock" in sections
            assert record.overflow is True  # 超出硬预算


class TestEventEnvelope:
    """P1-01: payload_hash 校验"""

    def test_payload_hash_mismatch_rejected(self):
        from event_bridge import EventEnvelope
        with pytest.raises(ValueError, match="payload_hash mismatch"):
            EventEnvelope.from_dict({
                "event_id": "e1",
                "origin": "nocturne",
                "event_type": "drive_event_v2",
                "payload_hash": "wronghash",
                "payload": {"drive_name": "curiosity", "intensity": 5},
            })

    def test_valid_envelope_accepted(self):
        from event_bridge import EventEnvelope, _compute_payload_hash
        payload = {"drive_name": "curiosity", "intensity": 5}
        envelope = EventEnvelope.from_dict({
            "event_id": "e1",
            "origin": "nocturne",
            "event_type": "drive_event_v2",
            "payload_hash": _compute_payload_hash(payload),
            "payload": payload,
        })
        assert envelope.event_id == "e1"


class TestDriveMapping:
    """P0-09: Drive → 心潮十二维映射"""

    def test_known_drive_mapped(self):
        from event_bridge import _map_drive
        result = _map_drive("curiosity", 5.0, satisfied=False)
        assert result is not None
        assert result["dimension"] == "curiosity"
        assert result["mapping_version"] == "1.0.0"

    def test_unknown_drive_rejected(self):
        from event_bridge import _map_drive
        result = _map_drive("unknown_drive", 5.0, satisfied=False)
        assert result is None

    def test_satisfied_drive_zero_delta(self):
        from event_bridge import _map_drive
        result = _map_drive("possess", 8.0, satisfied=True)
        assert result is not None
        assert result["delta"] == 0.0

    def test_delta_clamped(self):
        from event_bridge import _map_drive
        result = _map_drive("curiosity", 100.0, satisfied=False)
        assert result is not None
        assert result["delta"] <= 0.30  # 限幅


class TestLoopSuppressor:
    """P0-08: 回环抑制"""

    def test_hop_depth_exceeded(self):
        from event_bridge import LoopSuppressor, EventEnvelope, create_envelope
        # 创建一个很深的 derived_from 链
        ev = create_envelope(
            event_id="e-deep",
            origin="nocturne",
            event_type="drive_event_v2",
            payload={"drive_name": "curiosity", "intensity": 1},
            derived_from="nocturne:e1,xinchao:e2,nocturne:e3,xinchao:e4",
        )
        loop = LoopSuppressor("", max_depth=3)
        ok, reason = loop.check(ev)
        assert ok is False
        assert "depth" in reason

    def test_back_loop_detected(self):
        from event_bridge import LoopSuppressor, EventEnvelope, create_envelope
        ev = create_envelope(
            event_id="e-back",
            origin="xinchao",
            event_type="conversation_event",
            payload={"content": "hi"},
            derived_from="nocturne:e1",
        )
        loop = LoopSuppressor("", max_depth=3)
        ok, reason = loop.check(ev)
        assert ok is False
        assert "back-loop" in reason


class TestDashboardAuth:
    """Dashboard 鉴权测试"""

    def test_authenticate_with_correct_token(self):
        from dashboard import DashboardService
        svc = DashboardService("fake-dsn", dashboard_token="secret123")
        assert svc.authenticate("secret123") is True

    def test_authenticate_with_wrong_token(self):
        from dashboard import DashboardService
        svc = DashboardService("fake-dsn", dashboard_token="secret123")
        assert svc.authenticate("wrong") is False

    def test_authenticate_with_empty_token(self):
        from dashboard import DashboardService
        svc = DashboardService("fake-dsn", dashboard_token="secret123")
        assert svc.authenticate("") is False


class TestIdentityGateObservability:
    """P1-05: 长期记忆/近期连续性缺失可观测性"""

    def test_missing_long_term_memory_logs_warning(self, caplog):
        import logging
        from identity_gate import IdentityConfig, PromptPlanAssembler
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as td:
            core = Path(td) / "core.md"
            core.write_text("Core", encoding="utf-8")
            bedrock = Path(td) / "bedrock.md"
            bedrock.write_text("Bedrock", encoding="utf-8")

            config = IdentityConfig(
                core_instruction_path=str(core),
                identity_bedrock_path=str(bedrock),
                identity_bedrock_hash="",
                long_term_memory_path=str(Path(td) / "nonexistent_memory.md"),
                recent_continuity_path=str(Path(td) / "nonexistent_continuity.md"),
                token_budget=4000,
                bedrock_reserve_tokens=800,
                model_hard_limit=8192,
            )
            assembler = PromptPlanAssembler(config, Path(td) / "audit")
            with caplog.at_level(logging.WARNING):
                messages, record = assembler.assemble([])
            assert "long_term_memory missing" in caplog.text or "memory" in caplog.text.lower()
