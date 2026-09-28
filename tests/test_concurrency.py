"""T-024 acceptance tests: concurrency and race guards.

Covers TEST-RACE-001…003 (state-machine §25, INV-GAME-005). SQLite serialises writes and the
harness is single-threaded, so races are exercised by deterministic interleaving: drive an
operation so it commits and moves the state, then issue the second operation whose guard
re-reads that freshly committed state and refuses it (testing.md §17: "SQLite transactions/
state revalidation are sufficient"). Each test asserts both the refusal and that the losing
operation left **no** partial write — row counts, phase, RNG index and event count unchanged.
"""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import Engine
from sqlmodel import func, select

from app import game_service, persistence
from app.database import session_scope
from app.models import (
    AdjudicationMode,
    AdjudicationRecord,
    Argument,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
)
from app.umpire import AdjudicationProposal
from agents.umpire import FakeUmpire
from tests.api_helpers import seed, start_to_diplomacy
from tests.db_helpers import actor_row, count_rows

ACTORS = ["arkania", "belvar", "cestia"]
SEED = 424242
_PROB = AdjudicationProposal(
    mode=AdjudicationMode.probabilistic, probability=1.0, rationale="likely",
    success_effects=["It works."],
)


def _running_game(engine: Engine, *, phase: GamePhase, active: str | None) -> uuid.UUID:
    with session_scope(engine) as session:
        game = persistence.create(
            session,
            Game(
                scenario_id="race",
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


def _game(engine: Engine, game_id: uuid.UUID) -> Game:
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        session.expunge(game)
        return game


def _event_count(engine: Engine, game_id: uuid.UUID, event_type: str | None = None) -> int:
    with session_scope(engine) as session:
        stmt = select(func.count()).select_from(GameEvent).where(GameEvent.game_id == game_id)
        if event_type is not None:
            stmt = stmt.where(GameEvent.type == event_type)
        return session.exec(stmt).one()


def _to_adjudication(engine: Engine, game_id: uuid.UUID) -> uuid.UUID:
    """Drive the active Actor's Argument through Cons into ADJUDICATION; return its id."""
    game = _game(engine, game_id)
    owner = game.active_actor_id
    argument_id = game_service.make_argument(
        engine, game_id, actor_id=owner, action="Act", desired_effect="Effect", pros=["Because"]
    ).id
    for opponent in [a for a in ACTORS if a != owner]:
        game_service.pass_con(engine, game_id, argument_id=argument_id, actor_id=opponent)
    assert _game(engine, game_id).phase is GamePhase.adjudication
    return argument_id


# --- TEST-RACE-001 — Simultaneous Arguments --------------------------------------------


def test_race_001_simultaneous_arguments_admit_exactly_one(db_engine: Engine) -> None:
    game_id = _running_game(db_engine, phase=GamePhase.argument, active="arkania")
    game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="A", desired_effect="d", pros=["p"]
    )
    # The second near-simultaneous Argument re-reads committed state: phase is now cons.
    with pytest.raises(game_service.GameServiceError):
        game_service.make_argument(
            db_engine, game_id, actor_id="arkania", action="B", desired_effect="d", pros=["p"]
        )
    assert count_rows(db_engine, Argument, game_id=game_id) == 1  # exactly one accepted


# --- TEST-RACE-002 — Double phase close ------------------------------------------------


def test_race_002_double_diplomacy_close_advances_once(db_engine: Engine) -> None:
    game_id = _running_game(db_engine, phase=GamePhase.diplomacy, active=None)
    for actor_id in ACTORS[:-1]:
        game_service.ready(db_engine, game_id, actor_id)
    game_service.ready(db_engine, game_id, "cestia")  # closes diplomacy → argument
    assert _game(db_engine, game_id).phase is GamePhase.argument

    # A second close attempt sees a non-diplomacy phase and is refused; nothing advances twice.
    with pytest.raises(game_service.InvalidPhase):
        game_service.ready(db_engine, game_id, "cestia")
    game = _game(db_engine, game_id)
    assert game.phase is GamePhase.argument
    assert game.active_actor_id == "arkania"
    assert _event_count(db_engine, game_id, "phase_started") == 1  # only the argument open


def test_race_002_double_cons_close_advances_once(db_engine: Engine) -> None:
    game_id = _running_game(db_engine, phase=GamePhase.argument, active="arkania")
    argument_id = game_service.make_argument(
        db_engine, game_id, actor_id="arkania", action="A", desired_effect="d", pros=["p"]
    ).id
    game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id="belvar")
    game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id="cestia")  # closes cons
    assert _game(db_engine, game_id).phase is GamePhase.adjudication
    adjudication_opens = _event_count(db_engine, game_id, "phase_started")

    with pytest.raises(game_service.InvalidPhase):
        game_service.pass_con(db_engine, game_id, argument_id=argument_id, actor_id="cestia")
    assert _game(db_engine, game_id).phase is GamePhase.adjudication
    assert _event_count(db_engine, game_id, "phase_started") == adjudication_opens


# --- TEST-RACE-003 — Double adjudication callback --------------------------------------


def test_race_003_double_adjudication_keeps_one_authoritative(db_engine: Engine) -> None:
    game_id = _running_game(db_engine, phase=GamePhase.argument, active="arkania")
    argument_id = _to_adjudication(db_engine, game_id)
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(_PROB))

    rng_after_first = _game(db_engine, game_id).rng_draw_index
    events_after_first = _event_count(db_engine, game_id)

    # The second resolution attempt is refused; the loser draws no RNG and writes nothing.
    with pytest.raises(game_service.AlreadyAdjudicated):
        game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(_PROB))

    assert count_rows(db_engine, AdjudicationRecord, argument_id=argument_id) == 1
    assert _game(db_engine, game_id).rng_draw_index == rng_after_first  # no extra draw
    assert _event_count(db_engine, game_id) == events_after_first  # no extra events


def test_race_003_double_advancement_advances_once(db_engine: Engine) -> None:
    game_id = _running_game(db_engine, phase=GamePhase.argument, active="arkania")
    _to_adjudication(db_engine, game_id)
    game_service.resolve_adjudication(db_engine, game_id, FakeUmpire(_PROB))
    result = game_service.advance_after_resolution(db_engine, game_id)
    assert result.active_actor_id == "belvar"

    # A second advancement re-reads the moved-on state (phase argument) and is refused.
    with pytest.raises(game_service.InvalidPhase):
        game_service.advance_after_resolution(db_engine, game_id)
    game = _game(db_engine, game_id)
    assert game.active_actor_id == "belvar"  # not advanced twice
    assert game.phase is GamePhase.argument


# --- simultaneous readiness updates are not lost ------------------------------------------


def test_i005_simultaneous_ready_updates_are_not_lost(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    barrier = threading.Barrier(2)

    def mark(actor_id: str) -> None:
        barrier.wait()
        game_service.ready(db_engine, creation.game_id, actor_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(mark, ["arkania", "belvar"]))

    with session_scope(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert set(game.diplomacy_ready_actor_ids) == {"arkania", "belvar"}
