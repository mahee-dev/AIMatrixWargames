"""T-026 — the deterministic six-turn integration test (testing.md §18).

The authoritative engine-integration run and the **primary release gate for MVP-0** (Gate 1
exit). Three deterministic :class:`~agents.scripted.ScriptedPlayer`s and the deterministic
``FakeUmpire`` play the canonical fixture at ``tests/fixtures/deterministic-game/`` to
completion, offline, and every §18 expectation is asserted:

    game completes · turn == 6 · 18 primary Arguments accepted · no Argument budget violations ·
    all expected adjudications exist · RNG sequence matches fixture · final active Effects match
    fixture · private data never leaked · event sequence structurally valid.

**Determinism approach:** the run is asserted on *normalized* state — structure and stable
values, with generated UUIDs and timestamps never asserted directly (WBS T-026). The RNG
sequence is checked against a freshly reconstructed ``random.Random(seed)`` stream, and the
Effect set against the golden schedule the deterministic Umpire is built from
(:mod:`agents.scripted`). Private data is checked **only** through :mod:`app.visibility`.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator

import pytest

from sqlalchemy import Engine
from sqlmodel import select

from agents.scripted import (
    build_scripted_players,
    deterministic_umpire,
    run_deterministic_game,
)
from app import database, game_service, scenarios, visibility
from app.database import session_scope
from app.models import (
    Effect,
    Game,
    GamePhase,
    GameStatus,
    Message,
)

from tests import golden
from tests.api_helpers import FIXTURE_DIR


@pytest.fixture(scope="module")
def played(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Engine, scenarios.LoadedScenario, uuid.UUID]]:
    """Play the fixture once for the module; every test below only reads the finished game.

    The game is deterministic, so one play serves every §18 expectation (I-135). The tests
    must stay read-only, which keeps them independent of execution order (testing.md §3).
    """
    engine = database.engine_for_path(tmp_path_factory.mktemp("int-18") / "amaw-test.db")
    database.initialize_database(engine)
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(engine, scenario)
    game_service.start_game(engine, creation.game_id)
    players = build_scripted_players(scenario.game.turn_order)
    run_deterministic_game(engine, creation.game_id, players, deterministic_umpire())
    try:
        yield engine, scenario, creation.game_id
    finally:
        engine.dispose()


# --- game completes / turn == 6 --------------------------------------------------------


def test_int_18_game_completes_at_turn_six(played) -> None:
    db_engine, _scenario, game_id = played
    with session_scope(db_engine) as session:
        game = session.get(Game, game_id)
        assert game.status is GameStatus.complete
        assert game.phase is GamePhase.complete
        assert game.turn == 6
        assert game.active_actor_id is None


# --- 18 primary Arguments accepted / no budget violations ------------------------------


def test_int_18_eighteen_arguments_and_no_budget_violations(played) -> None:
    db_engine, scenario, game_id = played
    golden.assert_arguments_and_budget(db_engine, game_id, scenario.game.turn_order)


# --- all expected adjudications exist --------------------------------------------------


def test_int_18_all_eighteen_adjudications_exist(played) -> None:
    db_engine, _scenario, game_id = played
    golden.assert_adjudications(db_engine, game_id)


# --- RNG sequence matches fixture ------------------------------------------------------


def test_int_18_rng_sequence_matches_fixture(played) -> None:
    db_engine, scenario, game_id = played
    golden.assert_rng_sequence(db_engine, game_id, scenario.game.turn_order)


# --- final active Effects match fixture ------------------------------------------------


def test_int_18_final_active_effects_match_fixture(played) -> None:
    db_engine, scenario, game_id = played
    golden.assert_final_effects(db_engine, game_id, scenario)


# --- private data never leaked (via app.visibility only) -------------------------------


def test_int_18_private_data_never_leaks(played) -> None:
    db_engine, scenario, game_id = played
    turn_order = list(scenario.game.turn_order)

    with session_scope(db_engine) as session:
        # Other Actors' private brief/objectives/private_information are unreachable in a view.
        secrets = {
            actor_id: {
                *scenario.actors[actor_id].objectives,
                *scenario.actors[actor_id].private_information,
                scenario.actors[actor_id].private_brief,
            }
            for actor_id in turn_order
        }
        for viewer in turn_order:
            view = visibility.get_actor_view(session, game_id, viewer)
            for other in view.others:
                assert not hasattr(other, "private_brief")  # public projection only
                for foreign in turn_order:
                    if foreign == viewer:
                        continue
                    # No other Actor's secret text is present on the public projection.
                    blob = f"{other.id}|{other.name}|{other.public_description}"
                    assert not any(s in blob for s in secrets[foreign])

        # Private Messages: a third Actor can never see a private Message it is not party to.
        all_messages = session.exec(
            select(Message).where(Message.game_id == game_id)
        ).all()
        private_messages = [m for m in all_messages if not m.public]
        assert private_messages, "fixture run produced no private Messages to check"
        for viewer in turn_order:
            visible = visibility.visible_messages(session, game_id, viewer)
            for message in visible:
                if not message.public:
                    assert viewer == message.sender_actor_id or viewer in message.recipients
            # Concretely: a non-party Actor is excluded from someone else's private Message.
            for message in private_messages:
                party = {message.sender_actor_id, *message.recipients}
                if viewer not in party:
                    assert message.id not in {m.id for m in visible}

        # Private Effects: the arkania-only starting Effect is visible to arkania alone.
        for viewer in turn_order:
            visible_ids = {
                e.id for e in visibility.visible_effects(session, game_id, viewer)
            }
            private_effect = next(
                e
                for e in session.exec(select(Effect).where(Effect.game_id == game_id)).all()
                if e.visibility == "private" and "arkania" in e.visible_to
            )
            if viewer == "arkania":
                assert private_effect.id in visible_ids
            else:
                assert private_effect.id not in visible_ids

        # Notes: none were created; no Actor sees another's Notes.
        for viewer in turn_order:
            assert visibility.visible_notes(session, game_id, viewer) == []


# --- event sequence structurally valid -------------------------------------------------


def test_int_18_event_sequence_structurally_valid(played) -> None:
    db_engine, _scenario, game_id = played
    golden.assert_event_structure(db_engine, game_id)
