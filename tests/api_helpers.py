"""Shared helpers for the player-endpoint acceptance tests (T-029…T-035).

Seed the canonical fixture, build a ``TestClient`` bound to an isolated engine, and drive the
authoritative :mod:`app.game_service` into a target phase so an endpoint can be exercised in
that phase. Phase-driving goes through the service (never by poking rows), so these helpers
reach exactly the states the state machine allows.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app import game_service, scenarios
from app.api import create_app

# Paths are anchored on this file, never the working directory, so the suite runs from any cwd.
REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = REPO_ROOT / "tests" / "fixtures" / "deterministic-game"

# Fixture actors in turn order (scenario.yaml: turn_order arkania → belvar → cestia).
ACTORS = ("arkania", "belvar", "cestia")


def seed(engine: Engine) -> scenarios.GameCreation:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    return scenarios.create_game_from_scenario(engine, scenario)


def client(engine: Engine) -> TestClient:
    return TestClient(create_app(engine=engine), raise_server_exceptions=False)


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def start_to_diplomacy(engine: Engine, creation: scenarios.GameCreation) -> None:
    """Start the game — turn 1, DIPLOMACY, no active Actor (state-machine §15)."""
    game_service.start_game(engine, creation.game_id)


def start_to_argument(engine: Engine, creation: scenarios.GameCreation) -> None:
    """Advance to ARGUMENT with the first turn-order Actor (arkania) active."""
    start_to_diplomacy(engine, creation)
    for actor in ACTORS:
        game_service.ready(engine, creation.game_id, actor)


def start_to_cons(
    engine: Engine, creation: scenarios.GameCreation
) -> game_service.ArgumentResult:
    """Advance to CONS with arkania's primary Argument open_for_cons; return it."""
    start_to_argument(engine, creation)
    return game_service.make_argument(
        engine,
        creation.game_id,
        actor_id="arkania",
        action="Begin mobilisation of reserve forces.",
        desired_effect="Reserve formations begin deploying toward the frontier.",
        pros=["Mobilisation plans already exist."],
    )
