"""T-035 acceptance: the event polling endpoint (api.md §13.1).

`GET /events?after=` returns Actor-visible Events after the given id, with `last_event_id`.
The endpoint must not become a side channel for hidden information (api.md §13, API-INV-010):
public lifecycle events reach everyone, but a private `message_sent` event never reaches a
non-party (TEST-VIS-003 — the "events" vector). The event-visibility policy lives in the one
visibility service (AGENTS.md §6.4); it is exercised here directly and through the router.

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations


from sqlalchemy import Engine
from sqlmodel import select

from app import game_service, visibility
from app.database import session_scope
from app.models import GameEvent
from tests.api_helpers import bearer, client, seed, start_to_diplomacy


def _events(c, game_id, token, after=None):
    url = f"/games/{game_id}/events"
    if after is not None:
        url += f"?after={after}"
    return c.get(url, headers=bearer(token))


# --- endpoint shape + cursor (api.md §13.1) --------------------------------------------


def test_events_shape_and_cursor(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)  # emits game_started, phase_started, …
    c = client(db_engine)

    resp = _events(c, creation.game_id, creation.seat_tokens["arkania"])
    assert resp.status_code == 200
    body = resp.json()
    assert isinstance(body["events"], list) and body["events"]
    for event in body["events"]:
        assert set(event) == {"id", "turn", "type", "actor_id", "payload", "created_at"}
    assert body["last_event_id"] == body["events"][-1]["id"]

    # Polling past the last id returns nothing new and holds the cursor.
    after = body["last_event_id"]
    tail = _events(c, creation.game_id, creation.seat_tokens["arkania"], after=after).json()
    assert tail["events"] == []
    assert tail["last_event_id"] == after


def test_public_lifecycle_events_visible_to_all(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    for actor in ("arkania", "belvar", "cestia"):
        types = {
            e["type"] for e in _events(c, creation.game_id, creation.seat_tokens[actor]).json()["events"]
        }
        assert "game_started" in types
        assert "phase_started" in types


# --- the event endpoint is not a side channel (api.md §13, TEST-VIS-003) ---------------


def test_private_message_event_not_leaked_to_non_party(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    gid = creation.game_id
    # A private message arkania → belvar emits a message_sent event.
    game_service.send_message(
        db_engine, gid, sender_actor_id="arkania", text="secret", public=False,
        recipients=["belvar"],
    )
    c = client(db_engine)

    def message_events(token):
        return [
            e for e in _events(c, gid, token).json()["events"] if e["type"] == "message_sent"
        ]

    # sender and recipient see it; the non-party sees no message_sent event at all.
    assert len(message_events(creation.seat_tokens["arkania"])) == 1
    assert len(message_events(creation.seat_tokens["belvar"])) == 1
    cestia_body = _events(c, gid, creation.seat_tokens["cestia"]).json()
    assert message_events(creation.seat_tokens["cestia"]) == []
    # Not even indirectly through any payload/count (TEST-VIS-003).
    assert "belvar" not in {
        r for e in cestia_body["events"] for r in e["payload"].get("recipients", [])
    }


def test_public_message_event_visible_to_all(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    gid = creation.game_id
    game_service.send_message(
        db_engine, gid, sender_actor_id="arkania", text="public", public=True, recipients=[]
    )
    c = client(db_engine)
    cestia = [
        e for e in _events(c, gid, creation.seat_tokens["cestia"]).json()["events"]
        if e["type"] == "message_sent"
    ]
    assert len(cestia) == 1


# --- the visibility policy itself (TDD'd at its layer) ---------------------------------


def test_visible_events_and_predicate(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    gid = creation.game_id
    game_service.send_message(
        db_engine, gid, sender_actor_id="arkania", text="secret", public=False,
        recipients=["belvar"],
    )
    with session_scope(db_engine) as session:
        msg_event = session.exec(
            select(GameEvent).where(
                GameEvent.game_id == gid, GameEvent.type == "message_sent"
            )
        ).first()
        # can_view_event: sender + recipient yes, non-party no.
        assert visibility.can_view_event(session, "arkania", msg_event) is True
        assert visibility.can_view_event(session, "belvar", msg_event) is True
        assert visibility.can_view_event(session, "cestia", msg_event) is False
        # visible_events excludes it for the non-party; the `after` cursor filters by id.
        cestia_ids = [e.id for e in visibility.visible_events(session, gid, "cestia")]
        assert msg_event.id not in cestia_ids
        assert all(
            e.id > 1 for e in visibility.visible_events(session, gid, "arkania", after=1)
        )


# --- authentication --------------------------------------------------------------------


def test_events_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = client(db_engine).get(f"/games/{creation.game_id}/events")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_events_cross_game_forbidden(db_engine: Engine) -> None:
    creation = seed(db_engine)
    other = seed(db_engine)
    resp = _events(client(db_engine), other.game_id, creation.seat_tokens["arkania"])
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# --- a hidden event leaves no gap in an Actor's cursor ------------------------------------


def test_i008_hidden_event_creates_no_actor_cursor_gap(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    game_service.send_message(
        db_engine,
        creation.game_id,
        sender_actor_id="arkania",
        text="secret",
        public=False,
        recipients=["belvar"],
    )
    game_service.send_message(
        db_engine,
        creation.game_id,
        sender_actor_id="arkania",
        text="public",
        public=True,
        recipients=[],
    )
    body = client(db_engine).get(
        f"/games/{creation.game_id}/events", headers=bearer(creation.seat_tokens["cestia"])
    ).json()
    assert [event["id"] for event in body["events"]] == list(
        range(1, len(body["events"]) + 1)
    )
