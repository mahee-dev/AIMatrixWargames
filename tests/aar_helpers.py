"""Exports for after-action report tests: the canonical game played offline, and small
hand-built exports for cases the canonical game never produces (#16)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from app import database, game_service, scenarios
from app.export import build_game_export
from tests.api_helpers import FIXTURE_DIR


def canonical_export(db_path: Path) -> dict[str, Any]:
    """Play the canonical fixture to completion offline and return its administrative export."""
    engine = database.engine_for_path(db_path)
    database.initialize_database(engine)
    try:
        scenario = scenarios.load_scenario(FIXTURE_DIR)
        creation = scenarios.create_game_from_scenario(engine, scenario)
        game_service.start_game(engine, creation.game_id)
        players = build_scripted_players(scenario.game.turn_order)
        run_deterministic_game(engine, creation.game_id, players, deterministic_umpire())
        return build_game_export(engine, creation.game_id)
    finally:
        engine.dispose()


def minimal_export(**overrides: Any) -> dict[str, Any]:
    """The smallest export shape ``aar.model`` reads; tests overwrite the parts they exercise."""
    base: dict[str, Any] = {
        "export_format_version": 1,
        "game": {
            "id": "g-1", "scenario_id": "s", "scenario_version": 1,
            "scenario_summary": "A test.", "seed": 1, "max_turns": 2, "turn": 2,
            "status": "complete", "turn_order": ["a", "b"],
        },
        "actors": [
            {"id": "a", "name": "Alpha", "public_description": "", "objectives": ["Win"]},
            {"id": "b", "name": "Beta", "public_description": "", "objectives": ["Hold"]},
        ],
        "messages": [], "arguments": [], "cons": [], "argument_replies": [],
        "adjudications": [], "effects": [], "model_assignments": None, "agent_runs": [],
    }
    base.update(overrides)
    return base
