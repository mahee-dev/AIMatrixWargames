"""T-030 acceptance: the Message endpoints (api.md §7.1/§7.2).

`GET /messages` returns only the Messages the current Actor may read (via the visibility
service); `POST /messages` creates one with the sender taken from the Seat token, honouring the
public/private recipient rules, idempotency (API-INV-007) and the diplomacy readiness-clearing
rule (TEST-PHASE-003). Private Messages never leak to a non-party (TEST-VIS-001/002/003).

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

import json

from sqlalchemy import Engine

from app import game_service
from tests.api_helpers import bearer, client, seed, start_to_diplomacy


def _post_message(c, game_id, token, payload, key=None):
    headers = bearer(token)
    if key is not None:
        headers["Idempotency-Key"] = key
    return c.post(f"/games/{game_id}/messages", headers=headers, json=payload)


# --- POST (api.md §7.2) ----------------------------------------------------------------


def test_post_private_message_sender_from_token(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    resp = _post_message(
        client(db_engine),
        creation.game_id,
        creation.seat_tokens["arkania"],
        # A bogus sender/actor_id in the body must be ignored (INV-ACTOR-001).
        {
            "recipients": ["belvar"],
            "public": False,
            "text": "We are prepared to discuss guarantees.",
            "sender_actor_id": "cestia",
            "actor_id": "cestia",
        },
    )

    assert resp.status_code == 200
    message = resp.json()["message"]
    assert message["sender_actor_id"] == "arkania"
    assert message["recipients"] == ["belvar"]
    assert message["public"] is False
    assert message["text"] == "We are prepared to discuss guarantees."


def test_post_public_message(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    resp = _post_message(
        client(db_engine),
        creation.game_id,
        creation.seat_tokens["arkania"],
        {"recipients": [], "public": True, "text": "Arkania remains committed to stability."},
    )

    assert resp.status_code == 200
    assert resp.json()["message"]["public"] is True


def test_post_public_message_with_recipients_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    resp = _post_message(
        client(db_engine),
        creation.game_id,
        creation.seat_tokens["arkania"],
        {"recipients": ["belvar"], "public": True, "text": "hi"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_post_private_message_without_recipients_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    resp = _post_message(
        client(db_engine),
        creation.game_id,
        creation.seat_tokens["arkania"],
        {"recipients": [], "public": False, "text": "hi"},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_request"


def test_post_message_wrong_phase_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)  # game still in LOBBY, not running
    resp = _post_message(
        client(db_engine),
        creation.game_id,
        creation.seat_tokens["arkania"],
        {"recipients": [], "public": True, "text": "hi"},
    )
    assert resp.status_code == 409
    assert resp.json()["error"]["code"] == "wrong_phase"


# --- GET + visibility (api.md §7.1, TEST-VIS-001/002/003) -------------------------------


def test_get_messages_visibility(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    gid = creation.game_id

    _post_message(
        c, gid, creation.seat_tokens["arkania"],
        {"recipients": [], "public": True, "text": "PUBLIC-STABILITY"},
    )
    _post_message(
        c, gid, creation.seat_tokens["arkania"],
        {"recipients": ["belvar"], "public": False, "text": "PRIVATE-TO-BELVAR"},
    )

    def texts(token: str) -> set[str]:
        resp = c.get(f"/games/{gid}/messages", headers=bearer(token))
        assert resp.status_code == 200
        return {m["text"] for m in resp.json()["messages"]}

    # TEST-VIS-001: public visible to all. TEST-VIS-002: private only to sender + recipient.
    assert texts(creation.seat_tokens["arkania"]) == {"PUBLIC-STABILITY", "PRIVATE-TO-BELVAR"}
    assert texts(creation.seat_tokens["belvar"]) == {"PUBLIC-STABILITY", "PRIVATE-TO-BELVAR"}
    # TEST-VIS-003: the private Message never appears for a non-party — not even indirectly.
    cestia_resp = c.get(f"/games/{gid}/messages", headers=bearer(creation.seat_tokens["cestia"]))
    assert "PRIVATE-TO-BELVAR" not in json.dumps(cestia_resp.json())


# --- idempotency (API-INV-007, TEST-IDEM-001) ------------------------------------------


def test_post_message_idempotent_replay(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    payload = {"recipients": [], "public": True, "text": "once"}

    first = _post_message(c, creation.game_id, creation.seat_tokens["arkania"], payload, key="K1")
    second = _post_message(c, creation.game_id, creation.seat_tokens["arkania"], payload, key="K1")

    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["message"]["id"] == second.json()["message"]["id"]
    # Only one Message was actually created.
    listed = c.get(
        f"/games/{creation.game_id}/messages", headers=bearer(creation.seat_tokens["arkania"])
    ).json()["messages"]
    assert sum(1 for m in listed if m["text"] == "once") == 1


def test_post_message_idempotency_conflict(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)

    _post_message(
        c, creation.game_id, creation.seat_tokens["arkania"],
        {"recipients": [], "public": True, "text": "first"}, key="K2",
    )
    conflict = _post_message(
        c, creation.game_id, creation.seat_tokens["arkania"],
        {"recipients": [], "public": True, "text": "different"}, key="K2",
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


# --- readiness-clearing rule (api.md §12.1, TEST-PHASE-003) ----------------------------


def test_private_message_clears_recipient_readiness(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    gid = creation.game_id

    # belvar signals ready via the service, then arkania privately messages belvar.
    game_service.ready(db_engine, gid, "belvar")
    _post_message(
        c, gid, creation.seat_tokens["arkania"],
        {"recipients": ["belvar"], "public": False, "text": "new terms"},
    )

    # belvar's readiness was cleared before the close was evaluated: it is waited on again.
    belvar_ctx = c.get(f"/games/{gid}/context", headers=bearer(creation.seat_tokens["belvar"])).json()
    assert belvar_ctx["phase"] == "diplomacy"
    assert "ready" in belvar_ctx["available_actions"]
    assert belvar_ctx["requires_input"] is True


# --- authentication --------------------------------------------------------------------


def test_get_messages_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = client(db_engine).get(f"/games/{creation.game_id}/messages")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_post_message_cross_game_forbidden(db_engine: Engine) -> None:
    creation = seed(db_engine)
    other = seed(db_engine)
    resp = _post_message(
        client(db_engine),
        other.game_id,
        creation.seat_tokens["arkania"],
        {"recipients": [], "public": True, "text": "hi"},
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"
