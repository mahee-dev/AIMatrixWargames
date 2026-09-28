"""I-080 — the Actor-facing event stream never discloses sealed Con participation or completion
order (INV-CON-007, api.md §13.1 "not a side channel", API-INV-002). The raw log and the admin
export are unchanged (state-machine §24).
"""

from __future__ import annotations

import json

from sqlalchemy import Engine
from sqlmodel import select

from app import game_service, scenarios, visibility
from app.database import session_scope
from app.models import GameEvent
from tests.api_helpers import ACTORS, FIXTURE_DIR, bearer, client, seed, start_to_argument, start_to_cons

_PARTICIPATION_EVENTS = {"con_submitted", "con_passed"}


def _visible(c, game_id, token) -> list[dict]:
    response = c.get(f"/games/{game_id}/events", headers=bearer(token))
    assert response.status_code == 200, response.text
    return response.json()["events"]


def _raw_types(engine: Engine, game_id) -> list[str]:  # noqa: ANN001
    with session_scope(engine) as session:
        return [
            e.type for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
            ).all()
        ]


def _assert_no_participation_disclosed(events: list[dict], *, opponents: tuple[str, ...]) -> None:
    assert not [e for e in events if e["type"] in _PARTICIPATION_EVENTS]
    # Nor through any payload, count or ordinal gap (TEST-VIS-003 pattern; §13.1 ordinals).
    for event in events:
        assert "con_id" not in json.dumps(event["payload"]) or event["type"] == "cons_revealed"
        if event["type"] != "cons_revealed":
            assert event.get("actor_id") not in opponents or event["type"] in {
                "actor_ready", "message_sent", "argument_submitted", "argument_adjudicated",
            }
    assert [e["id"] for e in events] == list(range(1, len(events) + 1))


# --- TEST-I080-001: sealed_cons never discloses participation --------------------------------


def test_i080_001_sealed_cons_participation_is_invisible_to_every_actor(db_engine: Engine) -> None:
    creation = seed(db_engine)  # fixture: sealed_cons
    argument = start_to_cons(db_engine, creation)
    game_service.make_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="cestia", reason="c")
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar")
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="cestia")
    c = client(db_engine)

    for actor in ACTORS:  # the author included: its own Con's timing is still completion order
        events = _visible(c, creation.game_id, creation.seat_tokens[actor])
        _assert_no_participation_disclosed(events, opponents=("belvar", "cestia"))
        assert [e["type"] for e in events][-3:] == ["argument_submitted", "phase_started", "phase_started"]
    # The authoritative log still records the cycle (§24); only the projection is sealed.
    assert _raw_types(db_engine, creation.game_id)[-5:] == [
        "phase_started", "con_submitted", "con_passed", "con_passed", "phase_started",
    ]


# --- TEST-I080-002: visible_reply discloses the frozen set at reveal, in canonical order -----


def test_i080_002_visible_reply_reveals_only_the_frozen_set_at_close(db_engine: Engine) -> None:
    creation = scenarios.create_game_from_scenario(
        db_engine, scenarios.load_scenario(FIXTURE_DIR), con_reply_mode="visible_reply", reply_max_points=2,
    )
    start_to_argument(db_engine, creation)
    argument = game_service.make_argument(
        db_engine, creation.game_id, actor_id="arkania", action="Act", desired_effect="Effect", pros=["Pro"],
    )
    c = client(db_engine)
    # cestia answers first, belvar second: completion order is the opposite of canonical order.
    cestia_con = game_service.make_con(
        db_engine, creation.game_id, argument_id=argument.id, actor_id="cestia", reason="first"
    )
    for actor in ACTORS:
        events = _visible(c, creation.game_id, creation.seat_tokens[actor])
        _assert_no_participation_disclosed(events, opponents=("belvar", "cestia"))
        assert "cons_revealed" not in {e["type"] for e in events}

    belvar_con = game_service.make_con(
        db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar", reason="second"
    )
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="cestia")
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar")

    for actor in ACTORS:
        events = _visible(c, creation.game_id, creation.seat_tokens[actor])
        _assert_no_participation_disclosed(events, opponents=("belvar", "cestia"))
        revealed = [e for e in events if e["type"] == "cons_revealed"]
        assert len(revealed) == 1
        assert revealed[0]["payload"] == {
            "argument_id": str(argument.id),
            "con_ids": [str(belvar_con.id), str(cestia_con.id)],  # (actor_id, con_id), not arrival
        }
        assert revealed[0].get("actor_id") is None
        assert [e["type"] for e in events][-2:] == ["cons_revealed", "phase_started"]


# --- TEST-I080-003: the predicate itself --------------------------------------------------------


def test_i080_003_can_view_event_seals_participation_and_admits_the_reveal(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    game_service.make_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar", reason="b")
    game_service.pass_con(db_engine, creation.game_id, argument_id=argument.id, actor_id="cestia")
    with session_scope(db_engine) as session:
        by_type = {
            e.type: e for e in session.exec(
                select(GameEvent).where(GameEvent.game_id == creation.game_id)
            ).all()
        }
        for event_type in _PARTICIPATION_EVENTS:
            for actor in ACTORS:  # including the author of the Con / pass
                assert visibility.can_view_event(session, actor, by_type[event_type]) is False
        reveal = GameEvent(
            game_id=creation.game_id, turn=1, type="cons_revealed",
            payload={"argument_id": str(argument.id), "con_ids": []},
        )
        assert visibility.can_view_event(session, "arkania", reveal) is True
        unknown = GameEvent(game_id=creation.game_id, turn=1, type="owner_reply_submitted", payload={})
        assert visibility.can_view_event(session, "arkania", unknown) is False
