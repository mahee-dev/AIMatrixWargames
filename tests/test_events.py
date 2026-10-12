"""T-022 acceptance tests: the authoritative append-only Event log.

Proves the minimum Event history (state-machine §24, TRD §7.8): every authoritative mutation
from T-013 onward emits its documented event(s), ids are monotonic, ordering is causal and
matches §24, required events are transactional with the state mutation, and history is scoped
per game. Everything runs offline through the FakeUmpire (testing.md §3).
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine
from sqlmodel import select

from app import game_service, persistence
from app.database import session_scope
from app.models import (
    AdjudicationMode,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    Seat,
)
from app.umpire import AdjudicationProposal
from agents.umpire import FakeUmpire
from tests.db_helpers import actor_row

ACTORS = ["arkania", "belvar", "cestia"]
SEED = 424242
_OK = AdjudicationProposal(
    mode=AdjudicationMode.automatic_success, rationale="clear", success_effects=["It works."]
)


def _lobby_game(
    engine: Engine, *, max_turns: int = 6, max_cons_per_actor: int = 3
) -> uuid.UUID:
    """A LOBBY game with Actors and Seats, ready for start_game."""
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="events",
                turn=0,
                max_turns=max_turns,
                phase=GamePhase.diplomacy,
                status=GameStatus.lobby,
                turn_order=list(ACTORS),
                seed=SEED,
                primary_arguments_per_actor=1,
                max_cons_per_actor=max_cons_per_actor,
            ),
        )
        for actor_id in ACTORS:
            persistence.create(session, actor_row(game.id, actor_id))
            persistence.create(
                session,
                Seat(
                    game_id=game.id,
                    actor_id=actor_id,
                    token_hash=f"hash-{game.id}-{actor_id}",
                ),
            )
        return game.id


def _events(engine: Engine, game_id: uuid.UUID) -> list[GameEvent]:
    with session_scope(engine) as session:
        rows = session.exec(
            select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
        ).all()
        for row in rows:
            session.expunge(row)
        return list(rows)


def _types(engine: Engine, game_id: uuid.UUID) -> list[str]:
    return [e.type for e in _events(engine, game_id)]


def _start_to_argument(engine: Engine, game_id: uuid.UUID) -> None:
    game_service.start_game(engine, game_id)
    for actor_id in ACTORS:
        game_service.ready(engine, game_id, actor_id)


def _resolve_active(engine: Engine, game_id: uuid.UUID) -> uuid.UUID:
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        owner = game.active_actor_id
        order = list(game.turn_order)
    argument_id = game_service.make_argument(
        engine, game_id, actor_id=owner, action="Act", desired_effect="Effect", pros=["Because"]
    ).id
    for opponent in [a for a in order if a != owner]:
        game_service.pass_con(engine, game_id, argument_id=argument_id, actor_id=opponent)
    game_service.resolve_adjudication(engine, game_id, FakeUmpire(_OK))
    return argument_id


# --- Monotonic ids ---------------------------------------------------------------------


def test_event_ids_are_monotonic(db_engine: Engine) -> None:
    """Event ids strictly increase in causal order (TRD §7.8)."""
    game_id = _lobby_game(db_engine)
    _start_to_argument(db_engine, game_id)
    ids = [e.id for e in _events(db_engine, game_id)]
    assert ids == sorted(ids)
    assert len(set(ids)) == len(ids)
    assert all(ids[i] < ids[i + 1] for i in range(len(ids) - 1))


# --- Game start ------------------------------------------------------------------------


def test_start_game_emits_game_started_then_phase_started(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine)
    game_service.start_game(db_engine, game_id)
    events = _events(db_engine, game_id)
    assert [e.type for e in events] == ["game_started", "phase_started"]
    assert events[1].payload["phase"] == "diplomacy"
    assert events[0].turn == 1


# --- Diplomacy -------------------------------------------------------------------------


def test_message_and_ready_events(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine)
    game_service.start_game(db_engine, game_id)
    msg = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="hello", public=True
    )
    game_service.ready(db_engine, game_id, "arkania")

    events = _events(db_engine, game_id)
    by_type = {e.type: e for e in events}
    assert "message_sent" in by_type
    assert by_type["message_sent"].payload["message_id"] == str(msg.id)
    assert by_type["message_sent"].payload["public"] is True
    assert by_type["message_sent"].actor_id == "arkania"
    assert "actor_ready" in by_type
    assert by_type["actor_ready"].actor_id == "arkania"


def test_diplomacy_close_emits_phase_started_argument(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine)
    _start_to_argument(db_engine, game_id)
    phase_events = [
        e.payload.get("phase") for e in _events(db_engine, game_id) if e.type == "phase_started"
    ]
    assert "argument" in phase_events


# --- Argument / Cons -------------------------------------------------------------------


def test_argument_and_con_events(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine)
    _start_to_argument(db_engine, game_id)
    argument_id = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="a", desired_effect="d", pros=["p"]
    ).id
    con = game_service.make_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar", reason="no"
    )
    game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id="belvar")
    game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id="cestia")

    events = _events(db_engine, game_id)
    by_type = {e.type: e for e in events}
    assert by_type["argument_submitted"].payload["argument_id"] == str(argument_id)
    assert by_type["con_submitted"].payload["con_id"] == str(con.id)
    assert by_type["con_submitted"].payload["argument_id"] == str(argument_id)
    assert "con_passed" in by_type
    phases = [e.payload.get("phase") for e in events if e.type == "phase_started"]
    assert "cons" in phases
    assert "adjudication" in phases


def test_i030_exhausted_con_emits_submission_then_adjudication_not_pass(
    db_engine: Engine,
) -> None:
    game_id = _lobby_game(db_engine, max_cons_per_actor=1)
    _start_to_argument(db_engine, game_id)
    argument_id = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="a", desired_effect="d", pros=["p"]
    ).id
    game_service.make_con(
        db_engine, game_id, argument_id=argument_id, actor_id="belvar", reason="b"
    )
    game_service.make_con(
        db_engine, game_id, argument_id=argument_id, actor_id="cestia", reason="c"
    )

    tail = _events(db_engine, game_id)[-3:]
    assert [event.type for event in tail] == [
        "con_submitted",
        "con_submitted",
        "phase_started",
    ]
    assert tail[-1].payload == {"phase": "adjudication"}


# --- Adjudication / Effects ------------------------------------------------------------


def test_adjudication_and_effect_events(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine)
    _start_to_argument(db_engine, game_id)
    argument_id = _resolve_active(db_engine, game_id)

    events = _events(db_engine, game_id)
    by_type = {e.type: e for e in events}
    adj = by_type["argument_adjudicated"]
    assert adj.payload["argument_id"] == str(argument_id)
    assert adj.payload["mode"] == "automatic_success"
    assert adj.payload["success"] is True
    created = [e for e in events if e.type == "effect_created"]
    assert len(created) == 1
    assert created[0].payload["source_argument_id"] == str(argument_id)


def test_supersession_emits_created_then_deactivated(db_engine: Engine) -> None:
    """A superseding resolution emits effect_created then effect_deactivated(superseded)."""
    game_id = _lobby_game(db_engine)
    _start_to_argument(db_engine, game_id)
    # Resolve arkania creating an Effect, advance to belvar, then supersede it.
    _resolve_active(db_engine, game_id)
    with session_scope(db_engine) as session:
        from app.models import Effect

        old = session.exec(select(Effect).where(Effect.game_id == game_id)).first()
        old_id = old.id
    game_service.advance_after_resolution(db_engine, game_id)  # → belvar, argument
    argument_id = game_service.make_argument(
        db_engine, game_id, actor_id="belvar", action="a", desired_effect="d", pros=["p"]
    ).id
    for opponent in ["arkania", "cestia"]:
        game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id=opponent)
    game_service.resolve_adjudication(
        db_engine,
        game_id,
        FakeUmpire(
            AdjudicationProposal(
                mode=AdjudicationMode.automatic_success,
                rationale="supersede",
                success_effects=["replacement"],
                success_supersedes_effect_ids=[old_id],
            )
        ),
    )
    events = _events(db_engine, game_id)
    deactivated = [e for e in events if e.type == "effect_deactivated"]
    assert any(
        e.payload["effect_id"] == str(old_id) and e.payload["reason"] == "superseded"
        for e in deactivated
    )


# --- Turn / game completion ------------------------------------------------------------


def test_turn_completed_and_phase_started_diplomacy_on_advance(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine, max_turns=6)
    _start_to_argument(db_engine, game_id)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)
    types = _types(db_engine, game_id)
    assert types.count("turn_completed") == 1
    # the last two lifecycle events at the boundary are turn_completed then phase_started(diplomacy)
    tail = [e for e in _events(db_engine, game_id) if e.type in {"turn_completed", "phase_started"}]
    assert tail[-2].type == "turn_completed"
    assert tail[-1].type == "phase_started" and tail[-1].payload["phase"] == "diplomacy"


def test_game_completed_emitted_once(db_engine: Engine) -> None:
    game_id = _lobby_game(db_engine, max_turns=1)
    _start_to_argument(db_engine, game_id)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)
    types = _types(db_engine, game_id)
    assert types.count("game_completed") == 1
    assert types.count("turn_completed") == 1
    assert types[-1] == "game_completed"


# --- Full one-turn sequence (§24) ------------------------------------------------------


def test_full_single_turn_event_sequence(db_engine: Engine) -> None:
    """A one-turn game produces the documented §24 event sequence, in order."""
    game_id = _lobby_game(db_engine, max_turns=1)
    game_service.start_game(db_engine, game_id)
    for actor_id in ACTORS:
        game_service.ready(db_engine, game_id, actor_id)
    for _ in ACTORS:
        _resolve_active(db_engine, game_id)
        game_service.advance_after_resolution(db_engine, game_id)

    assert _types(db_engine, game_id) == [
        "game_started",
        "phase_started",  # diplomacy
        "actor_ready",
        "actor_ready",
        "actor_ready",
        "diplomacy_closed",
        "phase_started",  # argument (A) — diplomacy close
        # --- Actor A cycle
        "argument_submitted",
        "phase_started",  # cons
        "con_passed",
        "con_passed",
        "phase_started",  # adjudication
        "argument_adjudicated",
        "effect_created",
        "phase_started",  # argument (B) — advancement
        # --- Actor B cycle
        "argument_submitted",
        "phase_started",  # cons
        "con_passed",
        "con_passed",
        "phase_started",  # adjudication
        "argument_adjudicated",
        "effect_created",
        "phase_started",  # argument (C) — advancement
        # --- Actor C cycle
        "argument_submitted",
        "phase_started",  # cons
        "con_passed",
        "con_passed",
        "phase_started",  # adjudication
        "argument_adjudicated",
        "effect_created",
        # --- final turn completion
        "turn_completed",
        "game_completed",
    ]


# --- Transactional integration ---------------------------------------------------------


def test_event_and_state_roll_back_together(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If emitting a required Event fails, the state mutation it describes also rolls back."""
    game_id = _lobby_game(db_engine)
    game_service.start_game(db_engine, game_id)
    baseline = len(_events(db_engine, game_id))

    real_create = persistence.create

    def failing_create(session, entity):
        if isinstance(entity, GameEvent) and entity.type == "message_sent":
            raise RuntimeError("injected event failure")
        return real_create(session, entity)

    monkeypatch.setattr(persistence, "create", failing_create)
    with pytest.raises(RuntimeError):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania", text="hi", public=True
        )
    monkeypatch.undo()

    # Neither the Message nor any event persisted: state and history stayed consistent.
    from app.models import Message

    with session_scope(db_engine) as session:
        messages = session.exec(select(Message).where(Message.game_id == game_id)).all()
    assert messages == []
    assert len(_events(db_engine, game_id)) == baseline


# --- Cross-game isolation --------------------------------------------------------------


def test_events_are_scoped_per_game(db_engine: Engine) -> None:
    game_a = _lobby_game(db_engine)
    game_b = _lobby_game(db_engine)
    game_service.start_game(db_engine, game_a)
    game_service.start_game(db_engine, game_b)
    game_service.send_message(
        db_engine, game_a, sender_actor_id="arkania", text="only A", public=True
    )
    a_ids = {e.id for e in _events(db_engine, game_a)}
    b_ids = {e.id for e in _events(db_engine, game_b)}
    assert a_ids.isdisjoint(b_ids)
    assert all(e.game_id == game_a for e in _events(db_engine, game_a))
    assert "message_sent" not in _types(db_engine, game_b)
