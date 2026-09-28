"""I-053: explicit, persisted fixed Diplomacy-round experimental condition."""

from __future__ import annotations

import concurrent.futures

import pytest
from sqlalchemy import Engine
from sqlmodel import Session, select

from app import game_service
from app.config import Settings
from app.models import Game, GameEvent, GamePhase
from tests.api_helpers import seed, start_to_diplomacy


def _bounded_game(engine: Engine, rounds: int = 1):
    creation = seed(engine)
    with Session(engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None
        game.diplomacy_mode = "bounded_rounds"
        game.diplomacy_rounds_per_turn = rounds
        session.add(game)
        session.commit()
    start_to_diplomacy(engine, creation)
    return creation.game_id


def test_i053_001_open_diplomacy_does_not_close_on_round_completion(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    result = game_service.complete_diplomacy_round(
        db_engine, creation.game_id, turn=1, round_number=1
    )

    assert result.closed is False
    assert result.close_reason is None
    with Session(db_engine) as session:
        game = session.get(Game, creation.game_id)
        assert game is not None
        assert game.phase is GamePhase.diplomacy
        assert game.diplomacy_rounds_completed == 1


def test_i053_002_bounded_one_closes_after_one_complete_round(db_engine: Engine) -> None:
    game_id = _bounded_game(db_engine)

    result = game_service.complete_diplomacy_round(
        db_engine, game_id, turn=1, round_number=1
    )

    assert result.closed is True
    assert result.close_reason == "round_bound_reached"
    with Session(db_engine) as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.phase is GamePhase.argument
        events = list(session.exec(select(GameEvent).where(GameEvent.game_id == game_id)))
    assert [event.type for event in events].count("diplomacy_round_completed") == 1
    assert [event.type for event in events].count("diplomacy_closed") == 1


def test_i053_006_round_completion_is_concurrent_and_replay_safe(db_engine: Engine) -> None:
    game_id = _bounded_game(db_engine)

    def complete():
        return game_service.complete_diplomacy_round(
            db_engine, game_id, turn=1, round_number=1
        )

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        results = [future.result() for future in [pool.submit(complete), pool.submit(complete)]]

    assert all(result.closed for result in results)
    with Session(db_engine) as session:
        game = session.get(Game, game_id)
        assert game is not None
        assert game.diplomacy_rounds_completed == 1
        events = list(session.exec(select(GameEvent).where(GameEvent.game_id == game_id)))
    assert [event.type for event in events].count("diplomacy_round_completed") == 1
    assert [event.type for event in events].count("diplomacy_closed") == 1


def test_i053_007_new_turn_resets_round_counter(db_engine: Engine) -> None:
    game_id = _bounded_game(db_engine, rounds=3)
    game_service.complete_diplomacy_round(db_engine, game_id, turn=1, round_number=1)
    with Session(db_engine) as session:
        game = session.get(Game, game_id)
        assert game is not None
        game.diplomacy_rounds_completed = 2
        game.turn = 2
        game_service._start_turn(session, game, turn=2)
        session.add(game)
        session.commit()
    with Session(db_engine) as session:
        assert session.get(Game, game_id).diplomacy_rounds_completed == 0


def test_i053_010_configuration_defaults_and_invalid_combinations(monkeypatch) -> None:
    settings = Settings(_env_file=None)
    assert settings.diplomacy_mode == "open_diplomacy"
    assert settings.diplomacy_rounds_per_turn is None

    monkeypatch.setenv("AMAW_DIPLOMACY_MODE", "bounded_rounds")
    monkeypatch.setenv("AMAW_DIPLOMACY_ROUNDS_PER_TURN", "0")
    with pytest.raises(ValueError):
        Settings(_env_file=None)
