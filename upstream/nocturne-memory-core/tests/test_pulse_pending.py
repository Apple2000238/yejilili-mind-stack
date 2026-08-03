"""Heartbeat pulse pending: delivery registers, write engages, timeout soft-passes."""

import time

import pytest

from desire_engine import (
    DesireEngine,
    PULSE_PENDING_TTL_SEC,
    normalize_drive_key,
)


@pytest.fixture
def engine(tmp_path):
    return DesireEngine(db_path=str(tmp_path / "pulse-pending.db"))


def test_note_delivery_registers_pending_without_full_satisfy(engine):
    before = engine.store.load_state().drives["curiosity"]
    result = engine.note_pulse_delivery("curiosity", source="heartbeat")

    assert result["mode"] == "pending"
    assert result["acked"] == "curiosity"
    assert result["soft_bleed"] is True
    pending = engine.store.load_pulse_pending()
    assert pending is not None
    assert pending["drive_key"] == "curiosity"
    after = engine.store.load_state().drives["curiosity"]
    # light bleed only — not full satisfy collapse
    assert after < before
    assert after > before * 0.5


def test_hold_like_engage_satisfies_pending(engine):
    engine.note_pulse_delivery("stewardship")
    engaged = engine.engage_pulse_pending(via="hold")

    assert engaged is not None
    assert engaged["engaged"] == "stewardship"
    assert engine.store.load_pulse_pending() is None
    # satisfy sets refractory
    refractory = engine.store.load_refractory()
    assert refractory.get("stewardship", 0) > 0


def test_stir_same_drive_clears_without_double_settle(engine):
    engine.note_pulse_delivery("attachment")
    mid = engine.store.load_state().drives["attachment"]
    # raise it first like a real stir
    engine.pulse("attachment", 0.2)
    raised = engine.store.load_state().drives["attachment"]
    assert raised >= mid

    engaged = engine.engage_pulse_pending(via="stir", drive_key="attachment")
    assert engaged["cleared_only"] is True
    assert engine.store.load_pulse_pending() is None
    # should not have been knocked back down by a second satisfy
    assert engine.store.load_state().drives["attachment"] >= raised * 0.95


def test_break_same_drive_clears_after_refuse(engine):
    engine.note_pulse_delivery("social")
    engine.refuse("social", reason="不想")
    engaged = engine.engage_pulse_pending(via="break", drive_key="social")
    assert engaged["cleared_only"] is True
    assert engine.store.load_pulse_pending() is None


def test_expire_pending_soft_passes(engine):
    engine.note_pulse_delivery("reflection")
    pending = engine.store.load_pulse_pending()
    # backdate
    engine.store.set_pulse_pending(
        pending["drive_key"],
        source=pending["source"],
        delivered_at=time.time() - PULSE_PENDING_TTL_SEC - 10,
    )
    expired = engine.expire_pulse_pending()
    assert expired is not None
    assert expired["mode"] == "soft_pass"
    assert engine.store.load_pulse_pending() is None
    penalties = engine.store.load_intent_penalties()
    assert "reflection" in {normalize_drive_key(k) for k in penalties}


def test_next_delivery_expires_previous(engine):
    engine.note_pulse_delivery("curiosity")
    engine.store.set_pulse_pending(
        "curiosity",
        source="heartbeat",
        delivered_at=time.time() - PULSE_PENDING_TTL_SEC - 5,
    )
    result = engine.note_pulse_delivery("stewardship")
    assert result["pending"]["drive_key"] == "stewardship"
    assert result.get("expired_previous", {}).get("expired") == "curiosity"
