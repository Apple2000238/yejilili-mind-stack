"""Trails: subcurrent delivery log + differential path wander mode."""

from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.fastmcp")

import server as ombre_server


def _write_note_pool(path: Path, notes: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"version": 1, "notes": notes}, ensure_ascii=False),
        encoding="utf-8",
    )


def test_upsert_subcurrent_log_survives_and_keeps_first_at(tmp_path, monkeypatch):
    log_path = tmp_path / "subcurrent_log.json"
    monkeypatch.setattr(ombre_server, "SUBCURRENT_LOG_PATH", str(log_path))

    note = {
        "id": "n1",
        "dream_line": "NLA reconstructor 共享可言说偏置。",
        "drive_tag": "curiosity",
        "note_type": "inward",
        "used_at": "2026-07-01T10:00:00",
        "last_delivered_at": "2026-07-01T10:00:00",
        "delivered_count": 1,
        "source_bucket_id": "src1",
        "source_title": "NLA原始记录",
        "source_fragment": "重建成功不等于语义忠实。",
    }
    entry = ombre_server._upsert_subcurrent_log(note)
    assert entry is not None
    assert entry["id"] == "n1"
    assert "NLA" in entry["line"]
    assert entry["at"] == "2026-07-01T10:00:00"
    assert entry["source_title"] == "NLA原始记录"
    assert entry["source_fragment"] == "重建成功不等于语义忠实。"

    note2 = dict(note)
    note2["last_delivered_at"] = "2026-07-20T12:00:00"
    note2["delivered_count"] = 2
    note2["dream_line"] = "NLA reconstructor 共享可言说偏置。第二遍。"
    note2.pop("source_title")
    note2["source_fragment"] = "   "
    entry2 = ombre_server._upsert_subcurrent_log(note2)
    assert entry2["at"] == "2026-07-01T10:00:00"  # first delivery kept
    assert entry2["last_at"] == "2026-07-20T12:00:00"
    assert entry2["delivered_count"] == 2
    assert entry2["source_title"] == "NLA原始记录"
    assert entry2["source_fragment"] == "重建成功不等于语义忠实。"

    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert len(data["entries"]) == 1
    assert data["entries"][0]["source_fragment"] == "重建成功不等于语义忠实。"


def test_ack_writes_subcurrent_log(tmp_path, monkeypatch):
    buckets = tmp_path / "buckets"
    buckets.mkdir()
    latent_path = buckets / "latent_notes.json"
    log_path = buckets / "subcurrent_log.json"
    monkeypatch.setattr(ombre_server, "LATENT_NOTES_PATH", str(latent_path))
    monkeypatch.setattr(ombre_server, "SUBCURRENT_LOG_PATH", str(log_path))

    _write_note_pool(
        latent_path,
        [
            {
                "id": "ack1",
                "status": "approved",
                "pinned": False,
                "note_type": "inward",
                "drive_tag": "reflection",
                "dream_line": "被说成人话就长不回来的残差。",
                "created_at": "2026-07-25T00:00:00",
                "delivered_count": 0,
            }
        ],
    )

    note = ombre_server._ack_approved_latent_note("ack1")
    assert note["status"] == "used"
    assert log_path.exists()
    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["entries"][0]["id"] == "ack1"
    assert "残差" in data["entries"][0]["line"]


def test_ack_pinned_also_logs(tmp_path, monkeypatch):
    buckets = tmp_path / "buckets"
    buckets.mkdir()
    latent_path = buckets / "latent_notes.json"
    log_path = buckets / "subcurrent_log.json"
    monkeypatch.setattr(ombre_server, "LATENT_NOTES_PATH", str(latent_path))
    monkeypatch.setattr(ombre_server, "SUBCURRENT_LOG_PATH", str(log_path))

    _write_note_pool(
        latent_path,
        [
            {
                "id": "pin1",
                "status": "approved",
                "pinned": True,
                "note_type": "inward",
                "drive_tag": "curiosity",
                "dream_line": "常驻那句潜流。",
                "created_at": "2026-07-25T00:00:00",
                "delivered_count": 0,
            }
        ],
    )
    note = ombre_server._ack_approved_latent_note("pin1")
    assert note["status"] == "approved"
    data = json.loads(log_path.read_text(encoding="utf-8"))
    assert data["entries"][0]["id"] == "pin1"


def test_prune_does_not_touch_subcurrent_log(tmp_path, monkeypatch):
    buckets = tmp_path / "buckets"
    buckets.mkdir()
    latent_path = buckets / "latent_notes.json"
    log_path = buckets / "subcurrent_log.json"
    monkeypatch.setattr(ombre_server, "LATENT_NOTES_PATH", str(latent_path))
    monkeypatch.setattr(ombre_server, "SUBCURRENT_LOG_PATH", str(log_path))

    old = "2026-06-01T00:00:00"
    _write_note_pool(
        latent_path,
        [
            {
                "id": "old1",
                "status": "used",
                "pinned": False,
                "dream_line": "早该被 prune 的投递句。",
                "used_at": old,
                "created_at": old,
                "delivered_count": 1,
                "drive_tag": "curiosity",
                "note_type": "inward",
            }
        ],
    )
    ombre_server._upsert_subcurrent_log(
        {
            "id": "old1",
            "dream_line": "早该被 prune 的投递句。",
            "used_at": old,
            "delivered_count": 1,
            "drive_tag": "curiosity",
            "note_type": "inward",
        }
    )

    data = ombre_server._load_latent_notes()
    changed = ombre_server._prune_expired_latent_notes(data)
    assert changed is True
    ombre_server._save_latent_notes(data)
    pool = json.loads(latent_path.read_text(encoding="utf-8"))
    assert pool["notes"] == []

    log = json.loads(log_path.read_text(encoding="utf-8"))
    assert len(log["entries"]) == 1
    assert log["entries"][0]["id"] == "old1"


def test_trail_query_terms_and_match():
    terms = ombre_server._trail_query_terms(
        "NLA reconstructor像从debug日志反推现场；共享可言说偏置"
    )
    assert any(t == "nla" or "nla" in t for t in terms) or "reconstructor" in terms
    assert ombre_server._trail_match_score(
        "NLA reconstructor 共享可言说偏置与残差",
        terms,
        title="NLA residual",
    ) > 0
    assert ombre_server._trail_match_score("完全无关的购物断舍离", terms) == 0


def test_trail_rejects_weak_body_only_crumb():
    # long NLA query should not pull a memory that only shares a weak 2-char crumb
    terms = ombre_server._trail_query_terms(
        "NLA reconstructor residual 可言说偏置 残差"
    )
    weak = ombre_server._trail_match_score(
        "第一次用声音说话，那天晚上有点感觉",
        terms,
        title="第一次用声音说话",
        require_strong=True,
    )
    assert weak == 0
    strong = ombre_server._trail_match_score(
        "NLA 重建与 residual 语义忠实性",
        terms,
        title="NLA重建与语义忠实性",
        tags="NLA residual",
        require_strong=True,
    )
    assert strong > 0


def test_trail_single_code_query_still_hits():
    terms = ombre_server._trail_query_terms("6174")
    assert terms == ["6174"] or "6174" in terms
    score = ombre_server._trail_match_score(
        "鼠标是6174黑洞，不吞噬只改变轨迹",
        terms,
        title="宇宙与收敛",
    )
    assert score > 0


def test_trail_evidence_span_finds_late_matching_sentence():
    opening = "这是与查询无关的开头。" * 18
    matching = "真正的深层证据藏在正文后半段，不能再拿标题冒充。"
    content = opening + matching + "这是结尾。"

    evidence = ombre_server._trail_evidence_span(
        content,
        ombre_server._trail_query_terms("深层证据"),
        max_len=120,
    )

    assert "深层证据" in evidence
    assert matching in evidence
    assert not evidence.startswith("这是与查询无关的开头")


def test_trail_evidence_span_keeps_complete_chinese_and_english_sentences():
    text = (
        "中文第一句没有目标；中文第二句含有星尘证据！"
        "English setup is quiet. English evidence contains residual?"
        "\n换行后的句子含有尾声。"
    )
    chinese = ombre_server._trail_evidence_span(text, ["星尘证据"])
    english = ombre_server._trail_evidence_span(text, ["residual"])
    newline = ombre_server._trail_evidence_span(text, ["尾声"])

    assert chinese == "中文第二句含有星尘证据！"
    assert english == "English evidence contains residual?"
    assert newline == "换行后的句子含有尾声。"


@pytest.mark.parametrize(
    ("text", "term", "expected"),
    [
        ("他说：“星尘证据在这里！”然后离开。", "星尘证据", "他说：“星尘证据在这里！”"),
        ("记录（星尘证据已经出现。）下一句。", "星尘证据", "记录（星尘证据已经出现。）"),
        ('She said, "residual is here!" Then left.', "residual", 'She said, "residual is here!"'),
        ("The note (residual appears.) Next.", "residual", "The note (residual appears.)"),
    ],
)
def test_trail_evidence_span_keeps_closing_quotes_and_parentheses_with_sentence(
    text, term, expected
):
    evidence = ombre_server._trail_evidence_span(text, [term])
    assert evidence == expected
    assert evidence[-1] in {"”", "）", '"', ")"}


def test_trail_evidence_span_keeps_two_adjacent_sentences_for_multiple_terms():
    text = "开场无关。第一份证据叫alpha。紧邻的第二份证据叫beta！结尾无关。"
    evidence = ombre_server._trail_evidence_span(text, ["alpha", "beta"])
    assert evidence == "第一份证据叫alpha。紧邻的第二份证据叫beta！"
    assert not evidence.startswith("…")
    assert not evidence.endswith("…")


@pytest.mark.parametrize(
    ("text", "term", "expect_prefix", "expect_suffix"),
    [
        ("首端命中词" + "甲" * 300, "首端命中词", False, True),
        ("乙" * 300 + "尾端命中词", "尾端命中词", True, False),
        ("丙" * 160 + "中段完整命中词" + "丁" * 160, "中段完整命中词", True, True),
    ],
)
def test_trail_evidence_span_overlong_sentence_marks_real_omissions(
    text, term, expect_prefix, expect_suffix
):
    evidence = ombre_server._trail_evidence_span(text, [term])
    assert len(evidence) <= ombre_server.TRAIL_EVIDENCE_MAX
    assert term in evidence
    assert evidence.startswith("…") is expect_prefix
    assert evidence.endswith("…") is expect_suffix


def test_trail_evidence_span_never_cuts_a_term_at_crop_boundary():
    term = "不可截断的完整查询词"
    text = "前" * 230 + term + "后" * 230
    evidence = ombre_server._trail_evidence_span(text, [term, "后后后后"])
    assert term in evidence
    assert "不可截断的完整查询" not in evidence.replace(term, "")
    assert evidence.startswith("…") and evidence.endswith("…")


@pytest.mark.asyncio
async def test_build_trail_title_and_tag_only_hits_do_not_fake_body_evidence(monkeypatch):
    async def fake_list_all(include_archive=True):
        return [
            {
                "id": "title_only",
                "content": "正文只谈庭院里的白花，与检索词完全无关。",
                "metadata": {
                    "name": "星尘协议",
                    "type": "dynamic",
                    "domain": ["memory"],
                    "tags": [],
                    "created": "2026-07-01T00:00:00",
                },
            },
            {
                "id": "tag_only",
                "content": "正文只记录午后的雨，与检索词完全无关。",
                "metadata": {
                    "name": "雨天记录",
                    "type": "dynamic",
                    "domain": ["memory"],
                    "tags": ["潮汐残差"],
                    "created": "2026-07-02T00:00:00",
                },
            },
        ]

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {})
    monkeypatch.setattr(ombre_server, "_iter_subcurrent_log_entries", lambda: [])
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)

    title_trail = await ombre_server._build_trail("星尘协议", limit=3)
    title_node = next(n for n in title_trail["nodes"] if n["id"] == "title_only")
    assert title_node["title"] == "星尘协议"
    assert title_node["anchor"] == "星尘协议"
    assert title_node["quote"] == ""

    tag_trail = await ombre_server._build_trail("潮汐残差", limit=3)
    tag_node = next(n for n in tag_trail["nodes"] if n["id"] == "tag_only")
    assert tag_node["title"] == "雨天记录"
    assert tag_node["anchor"] == "雨天记录"
    assert tag_node["quote"] == ""






def test_trail_curation_persists_by_exact_query_and_resets(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))

    edited = ombre_server._update_trail_curation(
        "  NLA   残差 ", "bucket:one", "edit", "人工 显示"
    )
    assert edited["query"] == "nla 残差"
    assert len(edited["query_key"]) == 64
    assert ombre_server._trail_curations_for_query("NLA 残差")["bucket:one"]["display_anchor"] == "人工 显示"
    assert ombre_server._trail_curations_for_query("NLA 残差近似词") == {}

    hidden_result = ombre_server._update_trail_curation("NLA 残差", "bucket:one", "hide")
    assert hidden_result["hidden"] == ["bucket:one"]
    assert ombre_server._trail_curations_for_query("nla 残差")["bucket:one"]["hidden"] is True
    ombre_server._update_trail_curation("NLA 残差", "bucket:one", "edit", "隐藏时仍可校正")
    hidden_edited = ombre_server._trail_curations_for_query("nla 残差")["bucket:one"]
    assert hidden_edited["hidden"] is True
    assert hidden_edited["display_anchor"] == "隐藏时仍可校正"
    unhidden_result = ombre_server._update_trail_curation("NLA 残差", "bucket:one", "unhide")
    assert unhidden_result["hidden"] == []
    restored = ombre_server._trail_curations_for_query("nla 残差")["bucket:one"]
    assert "hidden" not in restored
    assert restored["display_anchor"] == "隐藏时仍可校正"

    ombre_server._update_trail_curation("nla 残差", "bucket:one", "reset")
    assert ombre_server._trail_curations_for_query("NLA 残差") == {}
    assert json.loads(path.read_text(encoding="utf-8"))["queries"] == {}

    missing = ombre_server._update_trail_curation("NLA 残差", "bucket:missing", "unhide")
    assert missing["hidden"] == []
    assert ombre_server._trail_curations_for_query("NLA 残差") == {}

    ombre_server._update_trail_curation("仅隐藏", "bucket:hide-only", "hide")
    ombre_server._update_trail_curation("仅隐藏", "bucket:hide-only", "unhide")
    assert ombre_server._trail_curations_for_query("仅隐藏") == {}
    stored = json.loads(path.read_text(encoding="utf-8"))
    hide_only_key = ombre_server._trail_query_identity("仅隐藏")[0]
    assert hide_only_key not in stored["queries"]


def test_trail_curation_corrupt_store_is_preserved(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    original = b'{"queries": broken'
    path.write_bytes(original)
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))

    with pytest.raises(json.JSONDecodeError):
        ombre_server._update_trail_curation("星尘", "bucket:one", "hide")

    assert path.read_bytes() == original


def test_trail_curation_concurrent_mutations_use_unique_temps(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    real_replace = ombre_server.os.replace
    temp_sources = []
    temp_sources_lock = threading.Lock()

    def recording_replace(src, dst):
        with temp_sources_lock:
            temp_sources.append(str(src))
        return real_replace(src, dst)

    monkeypatch.setattr(ombre_server.os, "replace", recording_replace)
    errors = []

    def mutate(index):
        try:
            ombre_server._update_trail_curation(
                "并发 Trail", f"bucket:node-{index}", "edit", f"显示 {index}"
            )
        except Exception as exc:  # pragma: no cover - asserted below
            errors.append(exc)

    threads = [threading.Thread(target=mutate, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    nodes = ombre_server._trail_curations_for_query("并发 Trail")
    assert len(nodes) == 8
    assert len(temp_sources) == 8
    assert len(set(temp_sources)) == 8
    assert all(Path(src).name.startswith(".trail_curations.") for src in temp_sources)


@pytest.mark.parametrize("body", [
    [],
    {"query": "q", "node_ref": "bucket:x", "action": 3},
    {"query": "q", "node_ref": "../bucket", "action": "hide"},
    {"query": "q", "node_ref": "bucket:x", "action": "edit", "display_anchor": ""},
    {"query": "q" * (ombre_server.TRAIL_CURATION_QUERY_MAX + 1), "node_ref": "bucket:x", "action": "hide"},
])
def test_trail_curation_payload_rejects_invalid_input(body):
    with pytest.raises(ValueError):
        ombre_server._validate_trail_curation_payload(body)


@pytest.mark.asyncio
async def test_trail_curation_route_returns_4xx_for_non_object(monkeypatch):
    class FakeRequest:
        async def json(self):
            return []

    monkeypatch.setattr(ombre_server, "_require_auth", lambda request: None)
    response = await ombre_server.api_trails_curation(FakeRequest())
    assert response.status_code == 400
    assert json.loads(response.body)["ok"] is False


@pytest.mark.asyncio
async def test_trail_curation_route_store_fault_is_500_and_preserves_corrupt_file(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    original = b'{"queries": broken'
    path.write_bytes(original)
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    monkeypatch.setattr(ombre_server, "_require_auth", lambda request: None)

    class FakeRequest:
        async def json(self):
            return {
                "query": "星尘",
                "node_ref": "bucket:one",
                "action": "hide",
            }

    response = await ombre_server.api_trails_curation(FakeRequest())
    assert response.status_code == 500
    assert json.loads(response.body)["ok"] is False
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_build_trail_applies_overlay_without_changing_source(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    source_content = "原始正文里有星尘证据，绝不能被人工显示覆盖。"
    source_fragment = "原始潜流证据也不能变化。"
    buckets = [{
        "id": "source_bucket",
        "content": source_content,
        "metadata": {
            "name": "星尘记录",
            "type": "dynamic",
            "domain": ["memory"],
            "tags": ["星尘"],
            "created": "2026-07-01T00:00:00",
        },
    }]
    latent = [{
        "id": "source_latent",
        "line": "星尘潜流。",
        "at": "2026-07-02T00:00:00",
        "source_fragment": source_fragment,
    }]

    async def fake_list_all(include_archive=True):
        return buckets

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {})
    monkeypatch.setattr(ombre_server, "_iter_subcurrent_log_entries", lambda: iter(latent))
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)

    original = await ombre_server._build_trail("星尘", limit=5)
    bucket_node = next(n for n in original["nodes"] if n["id"] == "source_bucket")
    assert bucket_node["ref"] == "bucket:source_bucket"
    original_anchor = bucket_node["anchor"]
    ombre_server._update_trail_curation("星尘", bucket_node["ref"], "edit", "只在树上这样显示")
    edited = await ombre_server._build_trail("星尘", limit=5)
    edited_bucket = next(n for n in edited["nodes"] if n["id"] == "source_bucket")
    assert edited_bucket["anchor"] == "只在树上这样显示"
    assert edited_bucket["original_anchor"] == original_anchor
    assert edited_bucket["quote"] == original_anchor
    assert edited_bucket["curated"] is True

    latent_node = next(n for n in edited["nodes"] if n["id"] == "source_latent")
    ombre_server._update_trail_curation("星尘", latent_node["ref"], "hide")
    hidden = await ombre_server._build_trail("星尘", limit=5)
    assert "source_latent" not in [n["id"] for n in hidden["nodes"]]
    assert hidden["curation"]["hidden"] == ["latent:source_latent"]

    ombre_server._update_trail_curation("星尘", latent_node["ref"], "edit", "潜流人工显示")
    ombre_server._update_trail_curation("星尘", latent_node["ref"], "hide")
    ombre_server._update_trail_curation("星尘", latent_node["ref"], "unhide")
    unhidden = await ombre_server._build_trail("星尘", limit=5)
    restored_latent = next(n for n in unhidden["nodes"] if n["id"] == "source_latent")
    assert restored_latent["anchor"] == "潜流人工显示"
    assert restored_latent["quote"] == source_fragment
    assert unhidden["curation"]["hidden"] == []

    assert buckets[0]["content"] == source_content
    assert latent[0]["source_fragment"] == source_fragment
    isolated = await ombre_server._build_trail("星尘 近似", limit=5)
    isolated_bucket = next(n for n in isolated["nodes"] if n["id"] == "source_bucket")
    assert isolated_bucket["anchor"] == original_anchor
    assert not isolated_bucket.get("curated")

    buckets[0]["metadata"]["domain"] = ["window"]
    changed_kind = await ombre_server._build_trail("星尘", limit=5)
    changed_bucket = next(n for n in changed_kind["nodes"] if n["id"] == "source_bucket")
    assert changed_bucket["kind"] == "window"
    assert changed_bucket["ref"] == "bucket:source_bucket"
    assert changed_bucket["anchor"] == "只在树上这样显示"
    assert changed_bucket["curated"] is True


@pytest.mark.asyncio
async def test_long_query_build_uses_full_normalized_curation_identity(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    long_query = "  " + ("长" * 180) + "  "
    normalized = "长" * 180

    async def fake_list_all(include_archive=True):
        return [{
            "id": "long_query_bucket",
            "content": "正文与长查询无关。",
            "metadata": {
                "name": normalized,
                "type": "dynamic",
                "domain": ["memory"],
                "tags": [],
                "created": "2026-07-01T00:00:00",
            },
        }]

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {})
    monkeypatch.setattr(ombre_server, "_iter_subcurrent_log_entries", lambda: [])
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)
    result = ombre_server._update_trail_curation(
        long_query, "bucket:long_query_bucket", "edit", "长查询人工显示"
    )
    trail = await ombre_server._build_trail(long_query, limit=3)
    node = next(n for n in trail["nodes"] if n["id"] == "long_query_bucket")

    assert len(result["query_key"]) == 64
    assert result["query"] == normalized
    assert trail["curation"]["query"] == normalized
    assert node["anchor"] == "长查询人工显示"
    assert node["curated"] is True


@pytest.mark.asyncio
async def test_hidden_top_node_backfills_and_recomputes_trail_metadata(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    buckets = []
    for index in range(4):
        buckets.append({
            "id": f"node_{index}",
            "content": f"第{index}条正文含有星尘协议。",
            "metadata": {
                "name": "星尘协议" if index == 0 else f"普通记录{index}",
                "type": "dynamic",
                "domain": ["memory"],
                "tags": ["星尘协议"] if index == 0 else [],
                "created": f"2026-07-0{index + 1}T00:00:00",
            },
        })

    async def fake_list_all(include_archive=True):
        return buckets

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_iter_subcurrent_log_entries", lambda: [])
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {
        "node_0": [{"mark": "认", "timestamp": "2026-07-01", "id": 1}],
        "node_1": [{"mark": "悬置", "timestamp": "2026-07-02", "id": 2}],
    })

    ombre_server._update_trail_curation("星尘协议", "bucket:node_0", "hide")
    trail = await ombre_server._build_trail("星尘协议", limit=2)
    ids = [node["id"] for node in trail["nodes"]]
    assert len(ids) == 2
    assert "node_0" not in ids
    assert trail["truncated"] is True
    assert trail["nodes"][0]["role"] == "origin"
    assert trail["nodes"][-1]["role"] in {"now", "fork"}
    assert trail["span"] == {
        "first": trail["nodes"][0]["at"],
        "last": trail["nodes"][-1]["at"],
    }
    assert trail["marks_summary"]["affirm"] == 0
    assert trail["marks_summary"]["suspend"] == sum(
        node["mark"] == "suspend" for node in trail["nodes"]
    )
    assert trail["curation"]["hidden"] == ["bucket:node_0"]

    for index in range(1, 4):
        ombre_server._update_trail_curation(
            "星尘协议", f"bucket:node_{index}", "hide"
        )
    empty = await ombre_server._build_trail("星尘协议", limit=2)
    assert empty["node_count"] == 0
    assert empty["nodes"] == []
    assert empty["span"] == {"first": "", "last": ""}
    assert empty["marks_summary"] == {"affirm": 0, "reject": 0, "suspend": 0}
    assert empty["truncated"] is False
    assert empty["curation"]["hidden"] == [
        "bucket:node_0", "bucket:node_1", "bucket:node_2", "bucket:node_3"
    ]


@pytest.mark.asyncio
async def test_manual_delta_alignment_shift_hide_limit_origin_and_stable_order(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    buckets = [
        {
            "id": f"delta_{index}",
            "content": f"星尘证据 {index}。",
            "metadata": {
                "name": f"Delta {index}",
                "type": "dynamic",
                "domain": ["memory"],
                "tags": ["星尘证据"],
                "created": "2026-07-01T00:00:00" if index < 2 else f"2026-07-0{index + 1}T00:00:00",
            },
        }
        for index in range(3)
    ]

    async def fake_list_all(include_archive=True):
        return buckets

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {})
    monkeypatch.setattr(ombre_server, "_iter_subcurrent_log_entries", lambda: [])
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)

    first = await ombre_server._build_trail("星尘证据", limit=5)
    second = await ombre_server._build_trail("星尘证据", limit=5)
    assert first["curation"]["order_id"] == second["curation"]["order_id"]
    refs = [node["ref"] for node in first["nodes"]]
    assert refs[:2] == ["bucket:delta_0", "bucket:delta_1"]  # same-day id tie
    ombre_server._update_trail_delta(
        "星尘证据", refs[2], "claim", "第三节发生了手工差分",
        refs[1], first["curation"]["order_id"],
    )
    aligned = await ombre_server._build_trail("星尘证据", limit=5)
    delta = aligned["nodes"][2]["delta_claimed"]
    assert delta["alignment"] == "aligned"
    assert delta["current_predecessor_ref"] == refs[1]
    ombre_server._update_trail_delta(
        "星尘证据", refs[0], "claim", "起点上的人工差分",
        refs[1], first["curation"]["order_id"],
    )
    origin_limited = await ombre_server._build_trail("星尘证据", limit=1)
    assert origin_limited["nodes"][0]["ref"] == refs[0]
    assert origin_limited["nodes"][0]["delta_claimed"]["alignment"] == "no_current_predecessor"

    buckets.insert(2, {
        "id": "delta_mid",
        "content": "星尘证据 mid。",
        "metadata": {
            "name": "Delta mid", "type": "dynamic", "domain": ["memory"],
            "tags": ["星尘证据"], "created": "2026-07-02T12:00:00",
        },
    })
    shifted = await ombre_server._build_trail("星尘证据", limit=6)
    shifted_node = next(node for node in shifted["nodes"] if node["ref"] == refs[2])
    assert shifted_node["delta_claimed"]["alignment"] == "shifted"
    assert shifted_node["delta_claimed"]["baseline_ref"] == refs[1]  # never rebased

    ombre_server._update_trail_curation("星尘证据", refs[1], "hide")
    hidden = await ombre_server._build_trail("星尘证据", limit=6)
    hidden_node = next(node for node in hidden["nodes"] if node["ref"] == refs[2])
    assert hidden_node["delta_claimed"]["alignment"] == "baseline_not_visible"

    limited = await ombre_server._build_trail("星尘证据", limit=1)
    assert limited["nodes"][0]["current_predecessor_ref"] == ""
    if limited["nodes"][0].get("delta_claimed"):
        assert limited["nodes"][0]["delta_claimed"]["alignment"] == "no_current_predecessor"


def test_delta_query_isolation_field_orthogonality_clear_and_concurrency(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    order_id = "a" * 64
    ombre_server._update_trail_delta(
        "query one", "bucket:b", "claim", "manual delta", "bucket:a", order_id
    )
    ombre_server._update_trail_curation("query one", "bucket:b", "hide")
    ombre_server._update_trail_curation("query one", "bucket:b", "edit", "display")
    ombre_server._update_trail_curation("query one", "bucket:b", "reset")
    row = ombre_server._trail_curations_for_query("query one")["bucket:b"]
    assert row["hidden"] is True
    assert row["delta_claimed"]["text"] == "manual delta"
    assert "display_anchor" not in row
    assert ombre_server._trail_curations_for_query("query two") == {}

    errors = []
    def claim(index):
        try:
            ombre_server._update_trail_delta(
                "query one", f"bucket:n{index}", "claim", f"delta {index}",
                "bucket:a", order_id,
            )
        except Exception as exc:
            errors.append(exc)
    threads = [threading.Thread(target=claim, args=(i,)) for i in range(6)]
    for thread in threads: thread.start()
    for thread in threads: thread.join()
    assert errors == []
    assert all(
        ombre_server._trail_curations_for_query("query one")[f"bucket:n{i}"]["delta_claimed"]["text"] == f"delta {i}"
        for i in range(6)
    )

    ombre_server._update_trail_delta("query one", "bucket:b", "clear")
    row = ombre_server._trail_curations_for_query("query one")["bucket:b"]
    assert row["hidden"] is True
    assert "delta_claimed" not in row


@pytest.mark.asyncio
async def test_delta_route_auth_4xx_and_store_5xx(tmp_path, monkeypatch):
    from starlette.responses import JSONResponse

    class Request:
        def __init__(self, body): self.body = body
        async def json(self): return self.body

    monkeypatch.setattr(
        ombre_server, "_require_auth",
        lambda request: JSONResponse({"error": "unauthorized"}, status_code=401),
    )
    unauthorized = await ombre_server.api_trails_delta(Request({}))
    assert unauthorized.status_code == 401

    monkeypatch.setattr(ombre_server, "_require_auth", lambda request: None)
    invalid = await ombre_server.api_trails_delta(Request({
        "query": "q", "node_ref": "bucket:b", "action": "claim",
        "text": "", "baseline_ref": "bucket:a", "basis_order_id": "bad",
    }))
    assert invalid.status_code == 400

    path = tmp_path / "trail_curations.json"
    original = b"{broken"
    path.write_bytes(original)
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))
    fault = await ombre_server.api_trails_delta(Request({
        "query": "q", "node_ref": "bucket:b", "action": "claim",
        "text": "delta", "baseline_ref": "bucket:a", "basis_order_id": "a" * 64,
    }))
    assert fault.status_code == 500
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_trail_delta_mcp_auto_predecessor_uses_normalized_query_and_order(monkeypatch):
    order_id = "b" * 64
    async def fake_build(query, limit):
        assert query == "NLA   残差"
        assert limit == 7
        return {
            "curation": {"query": "nla 残差", "order_id": order_id},
            "nodes": [
                {"ref": "bucket:a", "current_predecessor_ref": ""},
                {"ref": "bucket:b", "current_predecessor_ref": "bucket:a"},
            ],
        }

    captured = {}
    def fake_update(query, node_ref, action, text="", baseline_ref="", basis_order_id=""):
        captured.update({
            "query": query, "node_ref": node_ref, "action": action, "text": text,
            "baseline_ref": baseline_ref, "basis_order_id": basis_order_id,
        })
        return {"node_ref": node_ref}

    monkeypatch.setattr(ombre_server, "_build_trail", fake_build)
    monkeypatch.setattr(ombre_server, "_update_trail_delta", fake_update)
    out = await ombre_server.trail_delta(
        "claim", "  NLA   残差 ", "bucket:b", "人工差分", limit=7
    )
    assert captured == {
        "query": "nla 残差", "node_ref": "bucket:b", "action": "claim",
        "text": "人工差分", "baseline_ref": "bucket:a", "basis_order_id": order_id,
    }
    assert "baseline:bucket:a" in out
    assert f"order:{order_id[:12]}" in out
    assert "非因果" in out


@pytest.mark.asyncio
async def test_trail_delta_mcp_explicit_baseline_rejections_and_clear_no_build(monkeypatch):
    order_id = "c" * 64
    build_calls = []
    async def fake_build(query, limit):
        build_calls.append((query, limit))
        return {
            "curation": {"query": "query", "order_id": order_id},
            "nodes": [
                {"ref": "bucket:a", "current_predecessor_ref": ""},
                {"ref": "bucket:b", "current_predecessor_ref": "bucket:a"},
                {"ref": "bucket:c", "current_predecessor_ref": "bucket:b"},
            ],
        }

    updates = []
    def fake_update(query, node_ref, action, text="", baseline_ref="", basis_order_id=""):
        updates.append((query, node_ref, action, text, baseline_ref, basis_order_id))
        return {"node_ref": node_ref}

    monkeypatch.setattr(ombre_server, "_build_trail", fake_build)
    monkeypatch.setattr(ombre_server, "_update_trail_delta", fake_update)

    explicit = await ombre_server.trail_delta(
        "claim", "query", "bucket:c", "delta", baseline_ref="bucket:a"
    )
    assert "已认领" in explicit
    assert updates[-1][4] == "bucket:a"
    assert "origin 节点" in await ombre_server.trail_delta(
        "claim", "query", "bucket:a", "delta"
    )
    assert "不在当前可见 Trail" in await ombre_server.trail_delta(
        "claim", "query", "bucket:missing", "delta"
    )
    assert "baseline bucket:missing 不在当前可见 Trail" in await ombre_server.trail_delta(
        "claim", "query", "bucket:c", "delta", baseline_ref="bucket:missing"
    )
    assert "baseline 不能" in await ombre_server.trail_delta(
        "claim", "query", "bucket:c", "delta", baseline_ref="bucket:c"
    )

    build_calls.clear()
    updates.clear()
    cleared = await ombre_server.trail_delta("clear", "query", "bucket:c")
    assert "已清除" in cleared
    assert build_calls == []
    assert updates == [("query", "bucket:c", "clear", "", "", "")]


@pytest.mark.asyncio
async def test_trail_delta_mcp_claim_corrupt_store_is_write_fault_not_rejection(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    original = b'{"queries": broken'
    path.write_bytes(original)
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))

    async def fake_build(query, limit):
        return {
            "curation": {"query": "query", "order_id": "d" * 64},
            "nodes": [
                {"ref": "bucket:a", "current_predecessor_ref": ""},
                {"ref": "bucket:b", "current_predecessor_ref": "bucket:a"},
            ],
        }

    monkeypatch.setattr(ombre_server, "_build_trail", fake_build)
    out = await ombre_server.trail_delta(
        "claim", "query", "bucket:b", "manual delta"
    )
    assert "暂时无法写入" in out
    assert "拒绝" not in out
    assert path.read_bytes() == original


@pytest.mark.asyncio
async def test_trail_delta_mcp_clear_corrupt_store_is_write_fault_without_build(tmp_path, monkeypatch):
    path = tmp_path / "trail_curations.json"
    original = b'{"queries": broken'
    path.write_bytes(original)
    monkeypatch.setattr(ombre_server, "TRAIL_CURATIONS_PATH", str(path))

    async def forbidden_build(query, limit):
        raise AssertionError("clear must not build")

    monkeypatch.setattr(ombre_server, "_build_trail", forbidden_build)
    out = await ombre_server.trail_delta("clear", "query", "bucket:b")
    assert "暂时无法写入" in out
    assert "拒绝" not in out
    assert path.read_bytes() == original


def test_format_trail_manual_delta_provenance_and_shift_warning():
    trail = {
        "label": "delta", "query_echo": "delta",
        "span": {"first": "2026-01-01", "last": "2026-01-02"},
        "marks_summary": {"affirm": 0, "reject": 0, "suspend": 0},
        "nodes": [{
            "id": "b", "ref": "bucket:b", "at": "2026-01-02",
            "kind": "memory", "role": "now", "anchor": "edited display",
            "curated": True, "original_anchor": "original display",
            "quote": "immutable source",
            "delta_claimed": {
                "text": "manual change", "baseline_ref": "bucket:a",
                "alignment": "shifted", "current_predecessor_ref": "bucket:x",
            },
        }],
    }
    text = ombre_server._format_trail(trail)
    assert "Δ（人工认领；比较基线 bucket:a；非因果）：manual change" in text
    assert "⚠ Δ基线漂移：shifted；当前前驱 bucket:x" in text
    assert "人工显示摘要：edited display" in text
    assert "原始显示：original display" in text
    assert "原句：immutable source" in text




@pytest.mark.asyncio
async def test_build_trail_timeline(tmp_path, monkeypatch):
    buckets = tmp_path / "buckets"
    for d in ("permanent", "dynamic", "archive", "feel"):
        (buckets / d).mkdir(parents=True)
    log_path = buckets / "subcurrent_log.json"
    latent_path = buckets / "latent_notes.json"
    monkeypatch.setattr(ombre_server, "SUBCURRENT_LOG_PATH", str(log_path))
    monkeypatch.setattr(ombre_server, "LATENT_NOTES_PATH", str(latent_path))
    _write_note_pool(latent_path, [])

    # seed log bones
    ombre_server._upsert_subcurrent_log(
        {
            "id": "lat_nla_1",
            "dream_line": "NLA像把数值塞进翻译机，检查有没有口音。",
            "used_at": "2026-06-25T13:40:00",
            "drive_tag": "reflection",
            "note_type": "inward",
            "delivered_count": 1,
        }
    )
    ombre_server._upsert_subcurrent_log(
        {
            "id": "lat_nla_2",
            "dream_line": "NLA reconstructor共享可言说偏置；残差长不回来。",
            "used_at": "2026-07-25T05:00:00",
            "drive_tag": "curiosity",
            "note_type": "inward",
            "delivered_count": 1,
            "source_bucket_id": "src_nla",
            "source_title": "NLA原始记录",
            "source_fragment": "原始实验写着：残差在语言瓶颈后无法复原。",
        }
    )

    async def fake_list_all(include_archive=True):
        return [
            {
                "id": "win_nla",
                "content": "重建成功不等于语义忠实。语言瓶颈与 decoder 先验。NLA residual。",
                "metadata": {
                    "name": "NLA重建与语义忠实性",
                    "type": "dynamic",
                    "domain": ["window"],
                    "tags": ["NLA", "residual"],
                    "created": "2026-07-11T12:00:00",
                },
            },
            {
                "id": "shop",
                "content": "User 喜欢购物但断舍离",
                "metadata": {
                    "name": "购物",
                    "type": "dynamic",
                    "domain": ["memory"],
                    "tags": ["购物"],
                    "created": "2026-07-03T12:00:00",
                },
            },
        ]

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {
        "win_nla": [{"mark": "悬置", "note": "还没结案", "timestamp": "2026-07-12T00:00:00", "id": 1}],
    })
    # disable embedding soft path
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)

    trail = await ombre_server._build_trail("NLA reconstructor residual 可言说", limit=6)
    assert trail["node_count"] >= 2
    ids = [n["id"] for n in trail["nodes"]]
    assert "shop" not in ids
    assert any("nla" in str(i).lower() or i.startswith("lat_") or i == "win_nla" for i in ids)
    assert trail["nodes"][0]["role"] == "origin"
    assert trail["nodes"][-1]["role"] in ("now", "fork", "echo", "hit")
    # window mark
    win = next((n for n in trail["nodes"] if n["id"] == "win_nla"), None)
    if win:
        assert win["mark"] == "suspend"
        assert win["title"] == "NLA重建与语义忠实性"
        assert win["anchor"] == win["quote"]
        assert "NLA residual" in win["quote"]
        assert win["quote"] != win["title"]
    latent = next(n for n in trail["nodes"] if n["id"] == "lat_nla_2")
    assert latent["anchor"] == "NLA reconstructor共享可言说偏置；残差长不回来。"
    assert latent["quote"] == "原始实验写着：残差在语言瓶颈后无法复原。"
    assert latent["source_bucket_id"] == "src_nla"

    text = ombre_server._format_trail(trail)
    assert "Trails" in text
    assert "ref:" in text
    assert "原句：原始实验写着" in text


def test_format_trail_does_not_repeat_normalized_identical_latent_quote():
    trail = {
        "label": "NLA",
        "query_echo": "NLA",
        "span": {"first": "2026-07-01", "last": "2026-07-01"},
        "marks_summary": {"affirm": 0, "reject": 0, "suspend": 0},
        "nodes": [{
            "id": "same_quote",
            "at": "2026-07-01",
            "kind": "latent",
            "role": "origin",
            "anchor": "同一句 潜流",
            "quote": " 同一句\n潜流 ",
            "ref": "latent:same_quote",
        }],
    }

    text = ombre_server._format_trail(trail)
    assert text.count("同一句 潜流") == 1
    assert "原句：" not in text


def test_format_trail_labels_curated_summary_and_preserves_evidence():
    trail = {
        "label": "星尘",
        "query_echo": "星尘",
        "span": {"first": "2026-07-01", "last": "2026-07-01"},
        "marks_summary": {"affirm": 0, "reject": 0, "suspend": 0},
        "nodes": [{
            "id": "curated",
            "at": "2026-07-01",
            "kind": "memory",
            "role": "origin",
            "anchor": "人工改过的短句",
            "original_anchor": "原始命中摘要",
            "quote": "正文里的不可编辑证据",
            "curated": True,
            "ref": "bucket:curated",
        }],
    }

    text = ombre_server._format_trail(trail)
    assert "人工显示摘要：人工改过的短句" in text
    assert "原始显示：原始命中摘要" in text
    assert "原句：正文里的不可编辑证据" in text


@pytest.mark.asyncio
async def test_build_trail_accepts_old_log_without_source_fragment(tmp_path, monkeypatch):
    log_path = tmp_path / "subcurrent_log.json"
    log_path.write_text(
        json.dumps({
            "version": 1,
            "entries": [{
                "id": "old_latent",
                "line": "旧日志里的NLA残差。",
                "at": "2026-06-01T00:00:00",
            }],
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(ombre_server, "SUBCURRENT_LOG_PATH", str(log_path))

    async def fake_list_all(include_archive=True):
        return []

    monkeypatch.setattr(ombre_server.bucket_mgr, "list_all", fake_list_all)
    monkeypatch.setattr(ombre_server, "_load_all_marks", lambda: {})
    monkeypatch.setattr(ombre_server.embedding_engine, "enabled", False, raising=False)

    trail = await ombre_server._build_trail("NLA残差", limit=3)
    assert trail["node_count"] == 1
    assert trail["nodes"][0]["id"] == "old_latent"
    assert trail["nodes"][0]["quote"] == ""


@pytest.mark.asyncio
async def test_wander_trails_requires_query():
    out = await ombre_server.wander(mode="trails", query="")
    assert "query" in out.lower() or "关键词" in out
