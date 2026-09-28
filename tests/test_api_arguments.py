"""T-031 acceptance: the primary Argument endpoint (api.md §8.1).

`POST /arguments` submits the active Actor's primary Matrix Argument, enforcing the T-015
invariants at the API boundary: running game, argument phase, authenticated Actor == active
Actor (API-INV-003), an unused slot, non-empty action/desired_effect and 1–max Pros. On
success the server opens the Cons phase for that Argument. Re-asserts TEST-ARG-001…007.

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

from sqlalchemy import Engine

from tests.api_helpers import bearer, client, seed, start_to_argument, start_to_diplomacy

VALID = {
    "action": "Begin mobilisation of reserve forces.",
    "desired_effect": "Reserve formations begin deploying toward the frontier.",
    "pros": ["Mobilisation plans already exist.", "Rail capacity is available."],
}


def _post_argument(c, game_id, token, payload, key=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(f"/games/{game_id}/arguments", headers=headers, json=payload)


# --- TEST-ARG-001 — active Actor can submit; server opens Cons -------------------------


def test_arg_001_active_actor_can_submit(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    c = client(db_engine)

    resp = _post_argument(c, creation.game_id, creation.seat_tokens["arkania"], VALID)

    assert resp.status_code == 200
    argument = resp.json()["argument"]
    assert argument["actor_id"] == "arkania"
    assert argument["action"] == VALID["action"]
    assert argument["desired_effect"] == VALID["desired_effect"]
    assert argument["pros"] == VALID["pros"]
    assert argument["status"] == "open_for_cons"
    # The server transitioned to the Cons phase for that Argument (api.md §8.1).
    ctx = c.get(
        f"/games/{creation.game_id}/context", headers=bearer(creation.seat_tokens["arkania"])
    ).json()
    assert ctx["phase"] == "cons"


# --- TEST-ARG-002 — wrong Actor rejected -----------------------------------------------


def test_arg_002_non_active_actor_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # active == arkania
    resp = _post_argument(
        client(db_engine), creation.game_id, creation.seat_tokens["belvar"], VALID
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "not_active_actor"


# --- TEST-ARG-003 — wrong phase rejected -----------------------------------------------


def test_arg_003_wrong_phase_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)  # diplomacy, not argument
    resp = _post_argument(
        client(db_engine), creation.game_id, creation.seat_tokens["arkania"], VALID
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "wrong_phase"


# --- TEST-ARG-004…007 — structural validation ------------------------------------------


def test_arg_004_005_006_007_structural_validation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]

    cases = [
        {**VALID, "action": ""},  # ARG-004 empty action
        {**VALID, "desired_effect": ""},  # ARG-005 empty desired_effect
        {**VALID, "pros": []},  # ARG-006 zero Pros
        {**VALID, "pros": [f"pro {i}" for i in range(6)]},  # ARG-007 too many Pros
    ]
    for payload in cases:
        resp = _post_argument(c, creation.game_id, token, payload)
        assert resp.status_code == 422, payload
        assert resp.json()["error"]["code"] == "validation_error", payload


# --- TEST-ARG-008 (boundary) — no second primary Argument this turn --------------------


def test_arg_008_second_submission_refused(db_engine: Engine) -> None:
    """With the default budget of 1, submitting opens Cons; a second submission is refused."""
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]

    assert _post_argument(c, creation.game_id, token, VALID).status_code == 200
    second = _post_argument(c, creation.game_id, token, VALID)
    assert second.status_code == 409
    assert second.json()["error"]["code"] in {"wrong_phase", "argument_budget_exhausted"}


# --- idempotency + auth ----------------------------------------------------------------


def test_argument_idempotent_replay_and_conflict(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]

    first = _post_argument(c, creation.game_id, token, VALID, key="A1")
    replay = _post_argument(c, creation.game_id, token, VALID, key="A1")
    assert first.status_code == 200 and replay.status_code == 200
    assert first.json()["argument"]["id"] == replay.json()["argument"]["id"]

    conflict = _post_argument(
        c, creation.game_id, token, {**VALID, "action": "Something else."}, key="A1"
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_argument_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    resp = client(db_engine).post(f"/games/{creation.game_id}/arguments", json=VALID)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_argument_cross_game_forbidden(db_engine: Engine) -> None:
    creation = seed(db_engine)
    other = seed(db_engine)
    start_to_argument(db_engine, other)
    resp = _post_argument(
        client(db_engine), other.game_id, creation.seat_tokens["arkania"], VALID
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
