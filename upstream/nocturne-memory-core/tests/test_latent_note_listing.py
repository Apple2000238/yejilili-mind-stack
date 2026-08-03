from latent_note_view import latent_notes_for_display


def _note(
    note_id: str,
    *,
    status: str = "approved",
    pinned: bool = False,
    created_at: str = "2026-07-01T00:00:00",
    updated_at: str = "",
) -> dict:
    return {
        "id": note_id,
        "status": status,
        "pinned": pinned,
        "created_at": created_at,
        "updated_at": updated_at,
    }


def test_unpinned_note_stays_visible_as_recently_updated():
    notes = [
        _note(f"pin-{i}", pinned=True, created_at=f"2026-07-{i + 1:02d}T00:00:00")
        for i in range(3)
    ]
    notes.extend(
        _note(f"ordinary-{i}", created_at=f"2026-06-{i + 1:02d}T00:00:00")
        for i in range(8)
    )
    # This used to vanish after unpin: the bounded list sorted by created_at,
    # so an old note immediately fell behind the response cutoff.
    notes.append(
        _note(
            "just-unpinned",
            pinned=False,
            created_at="2026-01-01T00:00:00",
            updated_at="2026-07-27T18:00:00",
        )
    )

    visible = latent_notes_for_display({"notes": notes}, limit=5)

    assert [note["id"] for note in visible] == [
        "pin-2",
        "pin-1",
        "pin-0",
        "just-unpinned",
        "ordinary-7",
    ]


def test_deleted_notes_do_not_take_live_list_slots_even_when_still_marked_pinned():
    notes = [
        _note(f"deleted-{i}", status="deleted", pinned=True)
        for i in range(10)
    ]
    notes.extend(
        [
            _note("draft", status="draft"),
            _note("approved", status="approved"),
            _note("used", status="used"),
        ]
    )

    visible = latent_notes_for_display({"notes": notes}, limit=3)

    assert {note["id"] for note in visible} == {"draft", "approved", "used"}
