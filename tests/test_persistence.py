"""Persistence operations and transactions (T-008, WBS 2.2.2–2.2.3).

These prove the GameService-facing data layer on top of the T-007 session lifecycle:

- create / get / list / update round-trip every one of the nine TRD §7.1–7.9 entities;
- a Game plus related rows written through one engine read back identically through a
  *fresh* engine on the same file — restart safety, nothing relying on in-memory state
  (TRD §26, §7.1);
- the guarded transactional wrapper re-reads authoritative Game state inside one
  transaction, commits only when the caller's phase/eligibility guard holds against that
  fresh state, and on failure raises leaving zero partial writes (INV-GAME-004);
- applying the same guarded advance twice is rejected the second time — no double
  advancement at the single-transaction level (INV-GAME-005).

Per-phase legality predicates and request-level race guards are supplied by later tasks
(T-013–T-021, T-024); T-008 delivers the mechanism they run inside. Each test runs on its
own isolated database (the T-007 ``db_engine`` fixture), so the suite is order-independent
and key-free.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import Engine

from app import persistence
from app.database import engine_for_path, session_scope
from app.models import (
    Actor,
    Argument,
    Con,
    Effect,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    Message,
    Note,
    Seat,
)


def _make_game(**overrides) -> Game:
    fields = dict(scenario_id="scn", max_turns=6, seed=1234, turn_order=["A", "B", "C"])
    fields.update(overrides)
    return Game(**fields)


def _make_actor(game_id: uuid.UUID, actor_id: str = "A") -> Actor:
    return Actor(
        game_id=game_id,
        id=actor_id,
        name=f"Actor {actor_id}",
        public_description="public",
        private_brief="secret",
    )


def _seed_game_and_actor(session, actor_id: str = "A") -> tuple[Game, Actor]:
    game = persistence.create(session, _make_game())
    actor = persistence.create(session, _make_actor(game.id, actor_id))
    return game, actor


# --- Round-trip: create / read / list / update ------------------------------------------


def test_create_get_round_trips_all_nine_entities(db_engine: Engine) -> None:
    """Each of the nine GameService entities persists and reads back by primary key."""
    with session_scope(db_engine) as session:
        game, actor = _seed_game_and_actor(session)
        seat = persistence.create(
            session, Seat(game_id=game.id, actor_id=actor.id, token_hash="h")
        )
        message = persistence.create(
            session,
            Message(
                game_id=game.id,
                turn=1,
                sender_actor_id=actor.id,
                recipients=["B"],
                text="hi",
                public=False,
            ),
        )
        argument = persistence.create(
            session,
            Argument(
                game_id=game.id,
                turn=1,
                actor_id=actor.id,
                action="advance",
                desired_effect="gain ground",
                pros=["p1"],
            ),
        )
        con = persistence.create(
            session, Con(argument_id=argument.id, actor_id="B", reason="no")
        )
        effect = persistence.create(
            session,
            Effect(
                game_id=game.id,
                description="ground gained",
                visibility="public",
                created_turn=1,
            ),
        )
        event = persistence.create(
            session, GameEvent(game_id=game.id, turn=1, type="phase_started")
        )
        note = persistence.create(
            session, Note(game_id=game.id, actor_id=actor.id, text="mine")
        )

        assert persistence.get(session, Game, game.id).scenario_id == "scn"
        assert persistence.get(session, Actor, {"game_id": game.id, "id": "A"}).name == "Actor A"
        assert persistence.get(session, Seat, seat.id).token_hash == "h"
        assert persistence.get(session, Message, message.id).text == "hi"
        assert persistence.get(session, Argument, argument.id).action == "advance"
        assert persistence.get(session, Con, con.id).reason == "no"
        assert persistence.get(session, Effect, effect.id).description == "ground gained"
        assert persistence.get(session, GameEvent, event.id).type == "phase_started"
        assert persistence.get(session, Note, note.id).text == "mine"


def test_list_by_game_scopes_to_one_game(db_engine: Engine) -> None:
    """list_by_game returns only rows belonging to the given game."""
    with session_scope(db_engine) as session:
        g1, a1 = _seed_game_and_actor(session, "A")
        g2, a2 = _seed_game_and_actor(session, "A")
        persistence.create(
            session,
            Message(game_id=g1.id, turn=1, sender_actor_id="A", text="one", public=True),
        )
        persistence.create(
            session,
            Message(game_id=g1.id, turn=1, sender_actor_id="A", text="two", public=True),
        )
        persistence.create(
            session,
            Message(game_id=g2.id, turn=1, sender_actor_id="A", text="other", public=True),
        )

        g1_messages = persistence.list_by_game(session, Message, g1.id)
        assert {m.text for m in g1_messages} == {"one", "two"}
        assert len(persistence.list_by_game(session, Actor, g1.id)) == 1


def test_list_by_argument_returns_cons_for_that_argument(db_engine: Engine) -> None:
    """Con has no game_id; it is listed by its Argument (state-machine §7)."""
    with session_scope(db_engine) as session:
        game, actor = _seed_game_and_actor(session)
        arg = persistence.create(
            session,
            Argument(
                game_id=game.id,
                turn=1,
                actor_id="A",
                action="x",
                desired_effect="y",
                pros=["p"],
            ),
        )
        persistence.create(session, Con(argument_id=arg.id, actor_id="B", reason="b"))
        persistence.create(session, Con(argument_id=arg.id, actor_id="C", reason="c"))

        cons = persistence.list_by_argument(session, Con, arg.id)
        assert {c.actor_id for c in cons} == {"B", "C"}


def test_update_writes_mutable_game_transition_fields(db_engine: Engine) -> None:
    """A Game's turn/phase/status/active_actor/rng progress/ready set are updatable."""
    with session_scope(db_engine) as session:
        game = persistence.create(session, _make_game())
        persistence.update(
            session,
            game,
            turn=2,
            phase=GamePhase.argument,
            status=GameStatus.running,
            active_actor_id="B",
            rng_draw_index=5,
            diplomacy_ready_actor_ids=["A", "C"],
        )
        game_id = game.id

    with session_scope(db_engine) as session:
        reloaded = persistence.get(session, Game, game_id)
        assert reloaded.turn == 2
        assert reloaded.phase is GamePhase.argument
        assert reloaded.status is GameStatus.running
        assert reloaded.active_actor_id == "B"
        assert reloaded.rng_draw_index == 5
        assert reloaded.diplomacy_ready_actor_ids == ["A", "C"]


def test_effect_deactivation_is_an_update_not_a_delete(db_engine: Engine) -> None:
    """Supersession deactivates; the row stays in history (INV-EFFECT-004). No hard delete."""
    with session_scope(db_engine) as session:
        game, _ = _seed_game_and_actor(session)
        effect = persistence.create(
            session,
            Effect(
                game_id=game.id, description="d", visibility="public", created_turn=1
            ),
        )
        persistence.update(session, effect, active=False)
        effect_id = effect.id

    with session_scope(db_engine) as session:
        reloaded = persistence.get(session, Effect, effect_id)
        assert reloaded is not None
        assert reloaded.active is False


# --- Restart safety ---------------------------------------------------------------------


def test_state_survives_a_fresh_engine_on_the_same_file(db_path) -> None:
    """Rows written through one engine read back identically through a fresh one (TRD §26)."""
    from app.database import create_db_and_tables

    engine_a = engine_for_path(db_path)
    create_db_and_tables(engine_a)
    with session_scope(engine_a) as session:
        game, actor = _seed_game_and_actor(session)
        persistence.update(
            session, game, turn=3, rng_draw_index=7, diplomacy_ready_actor_ids=["A"]
        )
        persistence.create(
            session,
            Argument(
                game_id=game.id,
                turn=3,
                actor_id="A",
                action="hold",
                desired_effect="stay",
                pros=["p"],
            ),
        )
        game_id = game.id
    engine_a.dispose()

    engine_b = engine_for_path(db_path)
    try:
        with session_scope(engine_b) as session:
            reloaded = persistence.get(session, Game, game_id)
            assert reloaded.turn == 3
            assert reloaded.rng_draw_index == 7
            assert reloaded.diplomacy_ready_actor_ids == ["A"]
            assert len(persistence.list_by_game(session, Argument, game_id)) == 1
    finally:
        engine_b.dispose()


# --- Transactional guard: atomicity, revalidation, no double advancement -----------------


def test_guarded_op_leaves_zero_partial_writes_when_mutate_raises(db_engine: Engine) -> None:
    """A guarded op that raises partway leaves none of its writes behind (INV-GAME-004)."""
    with session_scope(db_engine) as session:
        game, _ = _seed_game_and_actor(session)
        game_id = game.id
    marker = uuid.uuid4()

    def mutate(game: Game, session) -> None:
        session.add(
            Note(id=marker, game_id=game.id, actor_id="A", text="half-written")
        )
        session.flush()  # first-half write reaches the transaction
        raise RuntimeError("boom after first write")

    with pytest.raises(RuntimeError, match="boom"):
        persistence.apply_guarded(
            db_engine, game_id, guard=lambda g, s: True, mutate=mutate
        )

    with session_scope(db_engine) as session:
        assert persistence.get(session, Note, marker) is None


def test_guard_refuses_to_commit_against_stale_state(db_engine: Engine) -> None:
    """The wrapper re-reads Game state and, when the guard fails, raises and writes nothing."""
    with session_scope(db_engine) as session:
        game, _ = _seed_game_and_actor(session)
        persistence.update(session, game, phase=GamePhase.diplomacy)
        game_id = game.id
    marker = uuid.uuid4()

    def guard(game: Game, session) -> bool:
        return game.phase is GamePhase.argument  # never true — game is in diplomacy

    def mutate(game: Game, session) -> None:
        session.add(Note(id=marker, game_id=game.id, actor_id="A", text="should not land"))

    with pytest.raises(persistence.GuardFailed):
        persistence.apply_guarded(db_engine, game_id, guard=guard, mutate=mutate)

    with session_scope(db_engine) as session:
        assert persistence.get(session, Note, marker) is None


def test_guard_commits_when_it_holds_against_fresh_state(db_engine: Engine) -> None:
    """When the guard holds against the freshly-read Game, the mutation commits."""
    with session_scope(db_engine) as session:
        game, _ = _seed_game_and_actor(session)
        persistence.update(session, game, phase=GamePhase.argument, active_actor_id="A")
        game_id = game.id

    def guard(game: Game, session) -> bool:
        return game.phase is GamePhase.argument and game.active_actor_id == "A"

    def mutate(game: Game, session) -> None:
        game.turn += 1

    persistence.apply_guarded(db_engine, game_id, guard=guard, mutate=mutate)

    with session_scope(db_engine) as session:
        assert persistence.get(session, Game, game_id).turn == 1


def test_guarded_advance_is_rejected_the_second_time(db_engine: Engine) -> None:
    """Applying a guarded advance twice is rejected the second time (INV-GAME-005)."""
    with session_scope(db_engine) as session:
        game, _ = _seed_game_and_actor(session)
        persistence.update(session, game, turn=1, status=GameStatus.running)
        game_id = game.id

    def advance_from_turn_1(game: Game, session) -> bool:
        return game.turn == 1  # guard is the fresh-state precondition

    def bump_turn(game: Game, session) -> None:
        game.turn += 1

    # First advance holds and commits.
    persistence.apply_guarded(db_engine, game_id, guard=advance_from_turn_1, mutate=bump_turn)
    # Second advance re-reads turn == 2, guard fails: no double advancement.
    with pytest.raises(persistence.GuardFailed):
        persistence.apply_guarded(
            db_engine, game_id, guard=advance_from_turn_1, mutate=bump_turn
        )

    with session_scope(db_engine) as session:
        assert persistence.get(session, Game, game_id).turn == 2


def test_apply_guarded_raises_when_game_absent(db_engine: Engine) -> None:
    """A guarded op against a nonexistent game fails closed rather than committing blind."""
    with pytest.raises(persistence.GuardFailed):
        persistence.apply_guarded(
            db_engine, uuid.uuid4(), guard=lambda g, s: True, mutate=lambda g, s: None
        )
