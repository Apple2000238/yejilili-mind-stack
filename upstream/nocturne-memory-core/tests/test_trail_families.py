"""Manual Trail Families: independent query entries and stable ref members."""

from __future__ import annotations

import json
import threading

import pytest

pytest.importorskip("mcp.server.fastmcp")

import server as ombre_server


@pytest.fixture
def family_store(tmp_path, monkeypatch):
    path = tmp_path / "trail_families.json"
    monkeypatch.setattr(ombre_server, "TRAIL_FAMILIES_PATH", str(path))
    return path


def _create(title="Residual family"):
    return ombre_server._mutate_trail_family({
        "action": "create",
        "title": title,
        "core_question": "残差如何改变？",
    })


def _add_entry(family, query=" NLA   残差 "):
    return ombre_server._mutate_trail_family_entry(family["id"], {
        "action": "add",
        "expected_revision": family["revision"],
        "query": query,
        "label": "NLA path",
    })


def test_family_crud_query_entries_and_stable_ref_members(family_store):
    family = _create()
    assert family["id"].startswith("fam_")
    assert family["revision"] == 1
    assert family["query_entries"] == []
    assert family["members"] == []

    entry_result = _add_entry(family)
    entry = entry_result["result"]
    assert entry["id"].startswith("entry_")
    assert entry["query"] == "nla 残差"
    assert len(entry["query_key"]) == 64
    entry_updated = ombre_server._mutate_trail_family_entry(family["id"], {
        "action": "update",
        "expected_revision": entry_result["revision"],
        "entry_id": entry["id"],
        "label": "renamed query entrance",
    })
    assert entry_updated["result"]["label"] == "renamed query entrance"

    member_result = ombre_server._mutate_trail_family_member(family["id"], {
        "action": "add",
        "expected_revision": entry_updated["revision"],
        "query_entry_id": entry["id"],
        "node_ref": "bucket:source-one",
        "observed_at": "2026-07-01",
        "source_order_id": "a" * 64,
        "manual_note": "原始 ref 才是成员",
    })
    member = member_result["result"]
    assert member["id"].startswith("member_")
    assert member["node_ref"] == "bucket:source-one"
    assert member["query_entry_id"] == entry["id"]
    assert member["query"] == "nla 残差"
    assert member["source_order_id"] == "a" * 64

    detail = ombre_server._load_trail_families()["families"][family["id"]]
    assert detail["core_question"] == "残差如何改变？"
    assert len(detail["query_entries"]) == 1
    assert len(detail["members"]) == 1

    with pytest.raises(ombre_server._TrailFamilyError) as duplicate:
        ombre_server._mutate_trail_family_member(family["id"], {
            "action": "add",
            "expected_revision": member_result["revision"],
            "query_entry_id": entry["id"],
            "node_ref": "bucket:source-one",
            "source_order_id": "b" * 64,
        })
    assert duplicate.value.status == 409

    with pytest.raises(ombre_server._TrailFamilyError) as nonempty:
        ombre_server._mutate_trail_family_entry(family["id"], {
            "action": "remove",
            "expected_revision": member_result["revision"],
            "entry_id": entry["id"],
        })
    assert nonempty.value.status == 409

    removed_member = ombre_server._mutate_trail_family_member(family["id"], {
        "action": "remove",
        "expected_revision": member_result["revision"],
        "member_id": member["id"],
    })
    removed_entry = ombre_server._mutate_trail_family_entry(family["id"], {
        "action": "remove",
        "expected_revision": removed_member["revision"],
        "entry_id": entry["id"],
    })
    assert removed_entry["result"]["removed"] is True

    updated = ombre_server._mutate_trail_family({
        "action": "update",
        "family_id": family["id"],
        "expected_revision": removed_entry["revision"],
        "title": "Renamed",
        "core_question": "新的核心问题",
    })
    assert updated["title"] == "Renamed"
    deleted = ombre_server._mutate_trail_family({
        "action": "delete",
        "family_id": family["id"],
        "expected_revision": updated["revision"],
    })
    assert deleted["deleted"] is True


def test_family_revision_conflict_and_validation(family_store):
    family = _create()
    with pytest.raises(ombre_server._TrailFamilyError) as conflict:
        ombre_server._mutate_trail_family({
            "action": "update",
            "family_id": family["id"],
            "expected_revision": 0,
            "title": "stale",
        })
    assert conflict.value.status == 409

    with pytest.raises(ombre_server._TrailFamilyError) as missing:
        ombre_server._mutate_trail_family({
            "action": "delete",
            "family_id": "fam_missing",
            "expected_revision": 1,
        })
    assert missing.value.status == 404

    entry_result = _add_entry(family)
    with pytest.raises(ombre_server._TrailFamilyError) as bad_ref:
        ombre_server._mutate_trail_family_member(family["id"], {
            "action": "add",
            "expected_revision": entry_result["revision"],
            "query_entry_id": entry_result["result"]["id"],
            "node_ref": "../bad",
            "source_order_id": "a" * 64,
        })
    assert bad_ref.value.status == 400


def test_family_corrupt_store_is_not_overwritten(family_store):
    original = b'{"families": broken'
    family_store.write_bytes(original)
    with pytest.raises(json.JSONDecodeError):
        _create()
    assert family_store.read_bytes() == original


def test_family_concurrent_creates_are_locked_and_preserved(family_store):
    errors = []

    def create(index):
        try:
            _create(f"Family {index}")
        except Exception as exc:  # pragma: no cover
            errors.append(exc)

    threads = [threading.Thread(target=create, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    data = ombre_server._load_trail_families()
    assert len(data["families"]) == 8
    assert len(set(data["families"])) == 8
    assert list(family_store.parent.glob(".trail_families.*.tmp")) == []


@pytest.mark.asyncio
async def test_family_routes_auth_and_status_classes(family_store, monkeypatch):
    from starlette.responses import JSONResponse

    class Request:
        def __init__(self, body=None, family_id=""):
            self.body = body
            self.path_params = {"family_id": family_id}

        async def json(self):
            return self.body

    monkeypatch.setattr(
        ombre_server,
        "_require_auth",
        lambda request: JSONResponse({"error": "unauthorized"}, status_code=401),
    )
    assert (await ombre_server.api_trail_families_list(Request())).status_code == 401

    monkeypatch.setattr(ombre_server, "_require_auth", lambda request: None)
    bad = await ombre_server.api_trail_families_mutate(Request([]))
    assert bad.status_code == 400
    missing = await ombre_server.api_trail_family_detail(Request(family_id="fam_missing"))
    assert missing.status_code == 404

    created = await ombre_server.api_trail_families_mutate(Request({
        "action": "create", "title": "API Family", "core_question": "Q?"
    }))
    assert created.status_code == 200
    payload = json.loads(created.body)
    family = payload["result"]
    stale = await ombre_server.api_trail_families_mutate(Request({
        "action": "update",
        "family_id": family["id"],
        "expected_revision": 0,
        "title": "stale",
    }))
    assert stale.status_code == 409

    family_store.write_text('{"families": broken', encoding="utf-8")
    failed = await ombre_server.api_trail_families_list(Request())
    assert failed.status_code == 500
    assert family_store.read_text(encoding="utf-8") == '{"families": broken'




@pytest.mark.asyncio
async def test_trail_family_mcp_crud_query_idempotence_and_pure_reads(family_store, monkeypatch):
    async def forbidden_build(query, limit):
        raise AssertionError("list/read/save_query must not build")

    monkeypatch.setattr(ombre_server, "_build_trail", forbidden_build)
    assert "还没有 Family" in await ombre_server.trail_family("list")
    created_text = await ombre_server.trail_family(
        "create", title="MCP Family", core_question="Q?"
    )
    family_id = created_text.split(" · ")[1]
    assert family_id.startswith("fam_")
    assert "MCP Family" in await ombre_server.trail_family("read", family_id=family_id)
    assert "已更新" in await ombre_server.trail_family(
        "update", family_id=family_id, title="Renamed"
    )
    assert "已更新" in await ombre_server.trail_family(
        "update", family_id=family_id, core_question=""
    )
    saved = await ombre_server.trail_family(
        "save_query", family_id=family_id, query="  NLA   残差 ", label="NLA"
    )
    assert "query 已保存" in saved
    duplicate = await ombre_server.trail_family(
        "save_query", family_id=family_id, query="nla 残差", label="ignored"
    )
    assert "query 已存在" in duplicate
    family = ombre_server._load_trail_families()["families"][family_id]
    assert len(family["query_entries"]) == 1
    assert family["query_entries"][0]["query"] == "nla 残差"


@pytest.mark.asyncio
async def test_trail_family_mcp_add_member_builds_current_provenance_and_is_idempotent(
    family_store, monkeypatch
):
    family = _create("Member family")
    order_id = "e" * 64
    build_calls = []

    async def fake_build(query, limit):
        build_calls.append((query, limit))
        return {
            "label": "NLA trail",
            "curation": {
                "query": "nla 残差",
                "query_key": ombre_server._trail_query_identity("nla 残差")[0],
                "order_id": order_id,
            },
            "nodes": [{"ref": "bucket:visible", "at": "2026-07-28"}],
        }

    monkeypatch.setattr(ombre_server, "_build_trail", fake_build)
    added = await ombre_server.trail_family(
        "add_member",
        family_id=family["id"],
        query="NLA 残差",
        node_ref="bucket:visible",
        limit=9,
    )
    assert "已加入成员" in added
    assert "order:" + order_id[:12] in added
    assert build_calls == [("NLA 残差", 9)]
    stored = ombre_server._load_trail_families()["families"][family["id"]]
    assert len(stored["query_entries"]) == 1
    assert len(stored["members"]) == 1
    member = stored["members"][0]
    assert member["observed_at"] == "2026-07-28"
    assert member["source_order_id"] == order_id

    duplicate = await ombre_server.trail_family(
        "add_member",
        family_id=family["id"],
        query="NLA 残差",
        node_ref="bucket:visible",
    )
    assert "成员已存在" in duplicate
    assert len(ombre_server._load_trail_families()["families"][family["id"]]["members"]) == 1
    rejected = await ombre_server.trail_family(
        "add_member",
        family_id=family["id"],
        query="NLA 残差",
        node_ref="bucket:hidden",
    )
    assert "不在当前 visible Trail" in rejected

    removed = await ombre_server.trail_family(
        "remove_member", family_id=family["id"], member_id=member["id"]
    )
    assert "已移除成员" in removed
    assert "已删除" in await ombre_server.trail_family(
        "delete", family_id=family["id"]
    )


@pytest.mark.asyncio
async def test_trail_family_mcp_conflict_is_not_retried(family_store, monkeypatch):
    family = _create("Conflict family")
    calls = []

    def conflict(family_id, body):
        calls.append((family_id, body))
        raise ombre_server._TrailFamilyError(409, "revision conflict")

    monkeypatch.setattr(ombre_server, "_mutate_trail_family_entry", conflict)
    out = await ombre_server.trail_family(
        "save_query", family_id=family["id"], query="conflict query"
    )
    assert "冲突" in out and "未自动重试" in out
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_trail_family_mcp_read_and_write_fault_classification(family_store, monkeypatch):
    original = b'{"families": broken'
    family_store.write_bytes(original)
    assert "暂时无法读取" in await ombre_server.trail_family("list")
    assert "暂时无法读取" in await ombre_server.trail_family(
        "save_query", family_id="fam_missing", query="q"
    )
    assert family_store.read_bytes() == original

    family_store.unlink()
    family = _create("Write fault")
    monkeypatch.setattr(
        ombre_server,
        "_mutate_trail_family_entry",
        lambda family_id, body: (_ for _ in ()).throw(OSError("disk fault")),
    )
    assert "暂时无法写入" in await ombre_server.trail_family(
        "save_query", family_id=family["id"], query="q"
    )
