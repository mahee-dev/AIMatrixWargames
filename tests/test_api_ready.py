"""T-034 acceptance: the readiness endpoint (api.md §12.1).

`POST /ready` signals the Actor has no further useful action this phase. Readiness is legal in
diplomacy; when all Actors are ready the server closes diplomacy to the Argument phase. `ready`
is a distinct endpoint from `pass` (Con participation). Keyed ready is idempotent (T-023);
keyless ready is not.

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

from sqlalchemy import Engine

from tests.api_helpers import bearer, client, seed, start_to_argument, start_to_diplomacy


def _ready(c, game_id, token, key=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(f"/games/{game_id}/ready", headers=headers, json={})


def test_ready_returns_documented_shape(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    resp = _ready(client(db_engine), creation.game_id, creation.seat_tokens["arkania"])
    assert resp.status_code == 200
    assert resp.json() == {"ready": True, "turn": 1, "phase": "diplomacy"}


def test_all_ready_closes_diplomacy(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)

    _ready(c, creation.game_id, creation.seat_tokens["arkania"])
    _ready(c, creation.game_id, creation.seat_tokens["belvar"])
    last = _ready(c, creation.game_id, creation.seat_tokens["cestia"])
    # The closing readiness reports the phase it opened (state-machine §16 → §17).
    assert last.status_code == 200
    assert last.json()["phase"] == "argument"


def test_ready_wrong_phase_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # already past diplomacy
    resp = _ready(client(db_engine), creation.game_id, creation.seat_tokens["belvar"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "wrong_phase"


def test_double_ready_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    assert _ready(c, creation.game_id, token).status_code == 200
    second = _ready(c, creation.game_id, token)  # keyless ready is non-idempotent
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "state_conflict"


def test_keyed_ready_replays(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    first = _ready(c, creation.game_id, token, key="R1")
    replay = _ready(c, creation.game_id, token, key="R1")
    assert first.status_code == 200 and replay.status_code == 200
    assert first.json() == replay.json()


def test_ready_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = client(db_engine).post(f"/games/{creation.game_id}/ready", json={})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_ready_cross_game_forbidden(db_engine: Engine) -> None:
    creation = seed(db_engine)
    other = seed(db_engine)
    start_to_diplomacy(db_engine, other)
    resp = _ready(client(db_engine), other.game_id, creation.seat_tokens["arkania"])
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
