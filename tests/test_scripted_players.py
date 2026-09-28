"""T-025 acceptance tests: deterministic ScriptedPlayers and the offline driver.

Covers the four `testing.md §18` player requirements and the "each scripted Actor drives a full
turn without manual intervention" done-condition, independently of the T-026 engine assertions
(`tests/test_integration_six_turn.py`). Everything runs offline against the canonical fixture
with the deterministic ``FakeUmpire`` — no provider, no ``OPENROUTER_API_KEY`` (testing.md §3).

ScriptedPlayers are deterministic policies: their decisions are a pure function of the Actor's
own authorised :class:`~app.visibility.ActorView`. They never choose sequencing — the server
owns the next Actor/turn/phase/completion (AGENTS.md §6, INV-GAME-004/005).
"""

from __future__ import annotations

import uuid

from sqlalchemy import Engine

from agents.scripted import (
    build_scripted_players,
    deterministic_umpire,
    run_deterministic_game,
)
from app import game_service, scenarios, visibility
from app.database import session_scope
from app.models import Argument, Con, ConParticipation, Game, GameStatus, Message
from tests.api_helpers import FIXTURE_DIR
from tests.db_helpers import count_rows



def _started_game(engine: Engine) -> tuple[uuid.UUID, scenarios.LoadedScenario]:
    """Create the canonical fixture game and start it into turn-1 diplomacy."""
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(engine, scenario)
    game_service.start_game(engine, creation.game_id)
    return creation.game_id, scenario


def _view(engine: Engine, game_id: uuid.UUID, actor_id: str) -> visibility.ActorView:
    with session_scope(engine) as session:
        return visibility.get_actor_view(session, game_id, actor_id)


# --- Player decisions are deterministic ------------------------------------------------


def test_scripted_player_decisions_are_deterministic(db_engine: Engine) -> None:
    game_id, scenario = _started_game(db_engine)
    players = build_scripted_players(scenario.game.turn_order)
    view = _view(db_engine, game_id, "arkania")

    player = players["arkania"]
    assert player.diplomacy_messages(view) == player.diplomacy_messages(view)
    assert player.argument(view) == player.argument(view)
    assert player.con_reason(view, "belvar") == player.con_reason(view, "belvar")


def test_scripted_player_references_its_own_private_information(db_engine: Engine) -> None:
    """§18: use at least one private information element — surfaced from the authorised view."""
    game_id, scenario = _started_game(db_engine)
    players = build_scripted_players(scenario.game.turn_order)
    for actor_id in scenario.game.turn_order:
        view = _view(db_engine, game_id, actor_id)
        secret = view.private_information[0]
        messages = players[actor_id].diplomacy_messages(view)
        assert messages, f"{actor_id} sends no diplomacy Message"
        assert any(secret in m.text for m in messages), (
            f"{actor_id} never references its private information"
        )


# --- The driver runs the whole game with no manual intervention ------------------------


def test_driver_completes_the_fixture_game_offline(db_engine: Engine) -> None:
    game_id, scenario = _started_game(db_engine)
    players = build_scripted_players(scenario.game.turn_order)

    run_deterministic_game(db_engine, game_id, players, deterministic_umpire())

    with session_scope(db_engine) as session:
        game = session.get(Game, game_id)
        assert game.status is GameStatus.complete
        assert game.turn == scenario.game.max_turns


def test_scripted_players_satisfy_the_four_player_requirements(db_engine: Engine) -> None:
    """§18: each player sends ≥1 Message, one Argument per turn, and Cons are submitted+passed."""
    game_id, scenario = _started_game(db_engine)
    turn_order = scenario.game.turn_order
    players = build_scripted_players(turn_order)

    run_deterministic_game(db_engine, game_id, players, deterministic_umpire())

    for actor_id in turn_order:
        assert count_rows(db_engine, Message, game_id=game_id, sender_actor_id=actor_id) >= 1
        # exactly one primary Argument per turn → max_turns Arguments per Actor
        assert count_rows(db_engine, Argument, game_id=game_id, actor_id=actor_id) == (
            scenario.game.max_turns
        )
    # Cons are both submitted and explicitly passed during the run.
    assert count_rows(db_engine, Con) > 0
    assert count_rows(db_engine, ConParticipation) > 0
