"""T-033 acceptance: the Note endpoints (api.md §11.1/§11.2, TEST-NOTE-001/002).

`GET /notes` returns only the current Actor's Notes; `POST /notes` creates one, private and
Actor-scoped, idempotent (API-INV-007). Another Actor's Notes are never reachable through a
Seat-authenticated endpoint.

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

from sqlalchemy import Engine

from tests.api_helpers import bearer, client, seed


def _post_note(c, game_id, token, payload, key=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(f"/games/{game_id}/notes", headers=headers, json=payload)


def _get_notes(c, game_id, token):
    return c.get(f"/games/{game_id}/notes", headers=bearer(token))


def test_post_note_returns_documented_shape(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = _post_note(
        client(db_engine),
        creation.game_id,
        creation.seat_tokens["arkania"],
        {"text": "Cestia may remain neutral if offered guarantees."},
    )
    assert resp.status_code == 200
    note = resp.json()["note"]
    assert set(note) == {"id", "text", "created_at"}
    assert note["text"] == "Cestia may remain neutral if offered guarantees."


def test_notes_are_actor_scoped(db_engine: Engine) -> None:
    """TEST-NOTE-001/002: a Note is visible to its owner, invisible to B and C."""
    creation = seed(db_engine)
    c = client(db_engine)
    gid = creation.game_id

    _post_note(c, gid, creation.seat_tokens["arkania"], {"text": "ARKANIA-ONLY"})

    ark = _get_notes(c, gid, creation.seat_tokens["arkania"]).json()["notes"]
    assert [n["text"] for n in ark] == ["ARKANIA-ONLY"]
    for other in ("belvar", "cestia"):
        assert _get_notes(c, gid, creation.seat_tokens[other]).json()["notes"] == []


def test_post_note_body_cannot_reassign_owner(db_engine: Engine) -> None:
    creation = seed(db_engine)
    c = client(db_engine)
    gid = creation.game_id
    # A bogus actor_id/owner in the body must be ignored (INV-ACTOR-001).
    _post_note(
        c, gid, creation.seat_tokens["arkania"],
        {"text": "MINE", "actor_id": "belvar", "owner": "belvar"},
    )
    assert _get_notes(c, gid, creation.seat_tokens["belvar"]).json()["notes"] == []
    assert [n["text"] for n in _get_notes(c, gid, creation.seat_tokens["arkania"]).json()["notes"]] == ["MINE"]


def test_post_note_empty_text_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = _post_note(
        client(db_engine), creation.game_id, creation.seat_tokens["arkania"], {"text": "   "}
    )
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "validation_error"


def test_post_note_idempotent_replay_and_conflict(db_engine: Engine) -> None:
    creation = seed(db_engine)
    c = client(db_engine)
    gid = creation.game_id

    first = _post_note(c, gid, creation.seat_tokens["arkania"], {"text": "once"}, key="NK1")
    replay = _post_note(c, gid, creation.seat_tokens["arkania"], {"text": "once"}, key="NK1")
    assert first.status_code == 200 and replay.status_code == 200
    assert first.json()["note"]["id"] == replay.json()["note"]["id"]
    assert len(_get_notes(c, gid, creation.seat_tokens["arkania"]).json()["notes"]) == 1

    conflict = _post_note(c, gid, creation.seat_tokens["arkania"], {"text": "other"}, key="NK1")
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_notes_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = client(db_engine).get(f"/games/{creation.game_id}/notes")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_notes_cross_game_forbidden(db_engine: Engine) -> None:
    creation = seed(db_engine)
    other = seed(db_engine)
    resp = _post_note(
        client(db_engine), other.game_id, creation.seat_tokens["arkania"], {"text": "x"}
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
