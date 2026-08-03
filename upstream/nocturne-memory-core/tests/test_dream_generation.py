import json
from types import SimpleNamespace

import pytest

import server


def _dream_text(length):
    seed = "我推开会呼吸的门，桃香沿着楼梯往上走，影子踩住一片发热的月光。"
    return (seed * ((length // len(seed)) + 1))[:length]


class _FakeCompletions:
    responses = []
    calls = []

    async def create(self, **kwargs):
        type(self).calls.append(kwargs)
        index = min(len(type(self).calls) - 1, len(type(self).responses) - 1)
        message = SimpleNamespace(content=type(self).responses[index])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])


@pytest.fixture
def dream_setup(monkeypatch, tmp_path):
    bucket = {
        "id": "memory-1",
        "content": "Human把一只脆桃递过来，果肉边缘是浅粉到乳白的渐变。",
        "metadata": {"created": "2026-07-27T09:00:00", "name": "脆桃", "type": "dynamic"},
    }

    async def list_all(**kwargs): return [bucket]

    _FakeCompletions.responses = []
    _FakeCompletions.calls = []
    monkeypatch.setattr(server.bucket_mgr, "list_all", list_all)
    monkeypatch.setattr(server, "BUCKETS_DIR", str(tmp_path))
    monkeypatch.setattr(server.dehydrator, "api_available", True)
    monkeypatch.setattr(server.dehydrator, "client", SimpleNamespace(chat=SimpleNamespace(completions=_FakeCompletions())))
    return tmp_path


@pytest.mark.asyncio
async def test_dream_first_full_paragraph_is_cached_without_retry(dream_setup):
    compliant = _dream_text(140)
    _FakeCompletions.responses = [compliant]
    dream, _, recent, _ = await server._refresh_dream_cache()
    assert len(_FakeCompletions.calls) == 1
    assert "sourced memory fragments" in _FakeCompletions.calls[0]["messages"][0]["content"]
    assert dream == compliant
    assert [item["id"] for item in recent] == ["memory-1"]
    assert json.loads((dream_setup / "latest_dream.json").read_text())["dream"] == compliant


@pytest.mark.asyncio
async def test_dream_120_character_first_answer_does_not_retry(dream_setup):
    boundary = _dream_text(120)
    _FakeCompletions.responses = [boundary]
    dream, _, _, _ = await server._refresh_dream_cache()
    assert len(_FakeCompletions.calls) == 1
    assert dream == boundary


@pytest.mark.asyncio
async def test_dream_119_character_first_answer_retries(dream_setup):
    _FakeCompletions.responses = [_dream_text(119), _dream_text(130)]
    dream, _, _, _ = await server._refresh_dream_cache()
    assert len(_FakeCompletions.calls) == 2
    assert dream == _dream_text(130)


@pytest.mark.asyncio
async def test_dream_short_first_answer_is_rewritten_once_and_cached(dream_setup):
    short, rewritten = _dream_text(64), _dream_text(150)
    _FakeCompletions.responses = [short, rewritten]
    dream, _, _, _ = await server._refresh_dream_cache()
    assert len(_FakeCompletions.calls) == 2
    retry = _FakeCompletions.calls[1]["messages"]
    assert retry[1] == {"role": "assistant", "content": short}
    assert dream == rewritten
    assert json.loads((dream_setup / "latest_dream.json").read_text())["dream"] == rewritten


@pytest.mark.asyncio
async def test_dream_never_requests_more_than_two_answers(dream_setup):
    _FakeCompletions.responses = [_dream_text(64), _dream_text(80), _dream_text(140)]
    dream, _, _, _ = await server._refresh_dream_cache()
    assert len(_FakeCompletions.calls) == 2
    assert dream == _dream_text(80)
