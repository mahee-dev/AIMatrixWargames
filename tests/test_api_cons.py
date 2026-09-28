"""T-032 acceptance: the Con and pass endpoints (api.md §9.1/§9.2).

`POST /arguments/{argument_id}/cons` records an eligible opponent's Con against the current
Argument; `POST /arguments/{argument_id}/pass` completes participation without a Con. `pass` is
a distinct endpoint from `ready`. Re-asserts TEST-CON-001…005 at the API boundary.

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine

from tests.api_helpers import bearer, client, seed, start_to_cons

REASON = {"reason": "Rail capacity is reduced by ongoing industrial disruption."}


def _con(c, game_id, arg_id, token, payload=REASON, key=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(f"/games/{game_id}/arguments/{arg_id}/cons", headers=headers, json=payload)


def _pass(c, game_id, arg_id, token, key=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(f"/games/{game_id}/arguments/{arg_id}/pass", headers=headers, json={})


# --- TEST-CON-001 — opponent Con accepted ----------------------------------------------


def test_con_001_opponent_con_accepted(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    resp = _con(client(db_engine), creation.game_id, arg.id, creation.seat_tokens["belvar"])

    assert resp.status_code == 200
    con = resp.json()["con"]
    assert con["argument_id"] == str(arg.id)
    assert con["actor_id"] == "belvar"
    assert con["reason"] == REASON["reason"]


# --- TEST-CON-002 — self-Con rejected --------------------------------------------------


def test_con_002_self_con_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)  # owner == arkania
    resp = _con(client(db_engine), creation.game_id, arg.id, creation.seat_tokens["arkania"])
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# --- TEST-CON-003 — Con limit enforced (max_cons_per_actor == 3) ------------------------


def test_con_003_con_limit_enforced(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["belvar"]

    for _ in range(3):
        assert _con(c, creation.game_id, arg.id, token).status_code == 200
    over_limit = _con(c, creation.game_id, arg.id, token)
    assert over_limit.status_code == 409
    assert over_limit.json()["error"]["code"] == "already_participated"


# --- TEST-CON-004 — pass closes participation; all-passed opens adjudication ------------


def test_con_004_pass_closes_participation_and_opens_adjudication(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    c = client(db_engine)

    resp = _pass(c, creation.game_id, arg.id, creation.seat_tokens["belvar"])
    assert resp.status_code == 200
    assert resp.json() == {"passed": True, "argument_id": str(arg.id), "actor_id": "belvar"}

    # When the last eligible opponent also passes, the server advances to adjudication.
    _pass(c, creation.game_id, arg.id, creation.seat_tokens["cestia"])
    ctx = c.get(
        f"/games/{creation.game_id}/context", headers=bearer(creation.seat_tokens["arkania"])
    ).json()
    assert ctx["phase"] == "adjudication"


# --- TEST-CON-005 — stale/foreign Argument rejected ------------------------------------


def test_con_005_stale_argument_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_cons(db_engine, creation)
    stale = uuid.uuid4()
    resp = _con(client(db_engine), creation.game_id, stale, creation.seat_tokens["belvar"])
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "state_conflict"


# --- idempotency + auth ----------------------------------------------------------------


def test_con_idempotent_replay(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["belvar"]

    first = _con(c, creation.game_id, arg.id, token, key="C1")
    replay = _con(c, creation.game_id, arg.id, token, key="C1")
    assert first.status_code == 200 and replay.status_code == 200
    assert first.json()["con"]["id"] == replay.json()["con"]["id"]


def test_i030_final_con_idempotent_replay_after_phase_close(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    c = client(db_engine)
    belvar = creation.seat_tokens["belvar"]
    cestia = creation.seat_tokens["cestia"]

    for number in range(3):
        assert _con(
            c,
            creation.game_id,
            arg.id,
            belvar,
            payload={"reason": f"Belvar {number}"},
        ).status_code == 200
    for number in range(2):
        assert _con(
            c,
            creation.game_id,
            arg.id,
            cestia,
            payload={"reason": f"Cestia {number}"},
        ).status_code == 200

    final = _con(
        c,
        creation.game_id,
        arg.id,
        cestia,
        payload={"reason": "Cestia final"},
        key="final-con",
    )
    replay = _con(
        c,
        creation.game_id,
        arg.id,
        cestia,
        payload={"reason": "Cestia final"},
        key="final-con",
    )

    assert final.status_code == 200 and replay.status_code == 200
    assert final.json() == replay.json()
    context = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert context["phase"] == "adjudication"


def test_pass_idempotent_replay(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["belvar"]

    first = _pass(c, creation.game_id, arg.id, token, key="P1")
    replay = _pass(c, creation.game_id, arg.id, token, key="P1")
    assert first.status_code == 200 and replay.status_code == 200
    assert first.json() == replay.json()


def test_con_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    resp = client(db_engine).post(
        f"/games/{creation.game_id}/arguments/{arg.id}/cons", json=REASON
    )
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"
