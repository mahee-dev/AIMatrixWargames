"""T-023 acceptance tests: idempotency keys for Actor-facing mutations.

Covers TEST-IDEM-001…004 and the batch's contract rules (api.md §4/§4.1, INV-IDEM-001/002/003):
the same key + same *semantic* payload replays the original result and mutates once; the same
key + a different payload is a conflict; keys are scoped by ``game_id + actor_id + operation``
and are restart-safe; a rolled-back mutation claims no key. Everything runs offline.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine
from sqlmodel import func, select

from app import database, game_service, persistence
from app.database import session_scope
from app.models import (
    Argument,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    Message,
)
from tests.db_helpers import actor_row, count_rows

ACTORS = ["arkania", "belvar", "cestia"]
SEED = 424242


def _running_game(engine: Engine, *, phase: GamePhase, active: str | None) -> uuid.UUID:
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="idem",
                turn=1,
                max_turns=6,
                phase=phase,
                status=GameStatus.running,
                turn_order=list(ACTORS),
                active_actor_id=active,
                seed=SEED,
                primary_arguments_per_actor=1,
                max_cons_per_actor=3,
            ),
        )
        for actor_id in ACTORS:
            persistence.create(session, actor_row(game.id, actor_id))
        return game.id


def _diplomacy_game(engine: Engine) -> uuid.UUID:
    return _running_game(engine, phase=GamePhase.diplomacy, active=None)


def _argument_game(engine: Engine) -> uuid.UUID:
    return _running_game(engine, phase=GamePhase.argument, active=ACTORS[0])


def _event_count(engine: Engine, game_id: uuid.UUID, event_type: str) -> int:
    with session_scope(engine) as session:
        return session.exec(
            select(func.count())
            .select_from(GameEvent)
            .where(GameEvent.game_id == game_id, GameEvent.type == event_type)
        ).one()


# --- TEST-IDEM-001 — Duplicate Message request -----------------------------------------


def test_idem_001_duplicate_message_creates_one_and_returns_same_result(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    first = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="hello", public=True,
        idempotency_key=key,
    )
    second = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="hello", public=True,
        idempotency_key=key,
    )

    assert second.id == first.id  # same original result replayed
    assert count_rows(db_engine, Message, game_id=game_id) == 1  # exactly one Message


# --- TEST-IDEM-002 — Duplicate Argument request ----------------------------------------


def test_idem_002_duplicate_argument_creates_one(db_engine: Engine) -> None:
    game_id = _argument_game(db_engine)
    key = str(uuid.uuid4())
    payload = dict(action="Act", desired_effect="Effect", pros=["Because"])
    first = game_service.make_argument(db_engine, game_id, actor_id="arkania", **payload, idempotency_key=key)
    second = game_service.make_argument(db_engine, game_id, actor_id="arkania", **payload, idempotency_key=key)

    assert second.id == first.id
    assert count_rows(db_engine, Argument, game_id=game_id) == 1
    # The replay must not re-run the mutation: the phase advanced only once (to cons).
    assert game_service.advance_after_resolution  # sanity: symbol exists
    with session_scope(db_engine) as session:
        assert session.get(Game, game_id).phase is GamePhase.cons


# --- TEST-IDEM-003 — Key conflict ------------------------------------------------------


def test_idem_003_same_key_different_payload_conflicts(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="hello", public=True,
        idempotency_key=key,
    )
    with pytest.raises(game_service.IdempotencyConflict):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania", text="DIFFERENT", public=True,
            idempotency_key=key,
        )
    assert count_rows(db_engine, Message, game_id=game_id) == 1  # conflict wrote nothing new


# --- TEST-IDEM-004 — Duplicate ready ---------------------------------------------------


def test_idem_004_duplicate_ready_does_not_corrupt_transition_counts(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    first = game_service.ready(db_engine, game_id, "arkania", idempotency_key=key)
    second = game_service.ready(db_engine, game_id, "arkania", idempotency_key=key)

    assert second.ready == first.ready
    assert second.diplomacy_closed == first.diplomacy_closed
    # Readiness recorded once, and exactly one actor_ready event emitted.
    with session_scope(db_engine) as session:
        assert session.get(Game, game_id).diplomacy_ready_actor_ids == ["arkania"]
    assert _event_count(db_engine, game_id, "actor_ready") == 1


def test_idem_ready_replay_survives_phase_close(db_engine: Engine) -> None:
    """A replayed ready returns its original result even after diplomacy has closed."""
    game_id = _diplomacy_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    game_service.ready(db_engine, game_id, "belvar")
    key = str(uuid.uuid4())
    closing = game_service.ready(db_engine, game_id, "cestia", idempotency_key=key)
    assert closing.diplomacy_closed is True  # this call closed diplomacy → phase argument

    replay = game_service.ready(db_engine, game_id, "cestia", idempotency_key=key)
    assert replay.diplomacy_closed is True
    assert replay.phase is GamePhase.argument
    with session_scope(db_engine) as session:
        assert session.get(Game, game_id).phase is GamePhase.argument  # not advanced twice


# --- Key scoping (api.md §4: game_id + actor_id + operation family) ---------------------


def test_same_key_different_actor_is_not_a_replay(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    game_service.send_message(db_engine, game_id, sender_actor_id="arkania", text="hi", public=True, idempotency_key=key)
    game_service.send_message(db_engine, game_id, sender_actor_id="belvar", text="hi", public=True, idempotency_key=key)
    assert count_rows(db_engine, Message, game_id=game_id) == 2


def test_same_key_different_operation_is_not_a_replay(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    game_service.send_message(db_engine, game_id, sender_actor_id="arkania", text="hi", public=True, idempotency_key=key)
    # Same key, same actor, different operation family (ready) → independent, no conflict.
    result = game_service.ready(db_engine, game_id, "arkania", idempotency_key=key)
    assert result.ready is True
    assert count_rows(db_engine, Message, game_id=game_id) == 1


def test_same_key_different_game_is_not_a_replay(db_engine: Engine) -> None:
    game_a = _diplomacy_game(db_engine)
    game_b = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    game_service.send_message(db_engine, game_a, sender_actor_id="arkania", text="hi", public=True, idempotency_key=key)
    game_service.send_message(db_engine, game_b, sender_actor_id="arkania", text="hi", public=True, idempotency_key=key)
    assert count_rows(db_engine, Message, game_id=game_a) == 1
    assert count_rows(db_engine, Message, game_id=game_b) == 1


# --- Atomicity: a failed mutation claims no key ----------------------------------------


def test_failed_mutation_does_not_claim_the_key(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    with pytest.raises(game_service.InvalidMessage):
        game_service.send_message(
            db_engine, game_id, sender_actor_id="arkania", text="   ", public=True,
            idempotency_key=key,
        )
    # The key was never committed, so a later valid call with the same key proceeds normally.
    result = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="now valid", public=True,
        idempotency_key=key,
    )
    assert result.public is True
    assert count_rows(db_engine, Message, game_id=game_id) == 1


# --- Restart safety --------------------------------------------------------------------


def test_idempotency_replay_survives_a_fresh_engine(db_engine: Engine, db_path) -> None:
    game_id = _diplomacy_game(db_engine)
    key = str(uuid.uuid4())
    first = game_service.send_message(
        db_engine, game_id, sender_actor_id="arkania", text="persist me", public=True,
        idempotency_key=key,
    )
    db_engine.dispose()

    reopened = database.engine_for_path(db_path)
    try:
        replay = game_service.send_message(
            reopened, game_id, sender_actor_id="arkania", text="persist me", public=True,
            idempotency_key=key,
        )
        assert replay.id == first.id
        assert count_rows(reopened, Message, game_id=game_id) == 1
    finally:
        reopened.dispose()


# --- No key → current non-idempotent behaviour is unchanged ----------------------------


def test_ready_without_key_is_still_non_idempotent(db_engine: Engine) -> None:
    game_id = _diplomacy_game(db_engine)
    game_service.ready(db_engine, game_id, "arkania")
    with pytest.raises(game_service.AlreadyReady):
        game_service.ready(db_engine, game_id, "arkania")
