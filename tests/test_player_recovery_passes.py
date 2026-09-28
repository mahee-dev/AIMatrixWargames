"""I-150: a Con pass the runner took after a failed activation is told apart from a chosen pass.

When a Con activation fails and ``pass_con`` is legal, the runner passes to keep the game moving
(agents.md §18 "Failure recovery"). That pass is not the Actor's choice, so the failure record
names the recovery and its Argument, and the run summary counts recovery passes separately
(benchmark-methodology §4, D-105).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart, ToolCallPart
from sqlalchemy import Engine

from agents.autonomous import AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import PlayerConfig, ReferencePlayer
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from app.api import create_app
from app.config import Settings
from tests.agent_fakes import default_policy, model_from_policy

ADMIN_TOKEN = "admin-secret-token"
SEED = 20260926
STALLER = "belvar"


def _stall_in_cons(packet: dict, tool_names: list[str], idx: int) -> list[Any]:
    """Belvar makes no progress in any Con activation; every other decision is the default."""
    if "pass_con" in packet.get("available_actions", []) and packet.get("you", {}).get("actor_id") == STALLER:
        return [TextPart(content="thinking")]
    return default_policy(packet, tool_names, idx)


def _factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
    player = ReferencePlayer(
        tools,
        actor_id=actor_id,
        actor_name=actor_name,
        model=model_from_policy(_stall_in_cons),
        config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
    )
    return LifecycleRunner(player, sleep=lambda: None)


def _run(db_engine: Engine, scenario: Path, factory=_factory):  # noqa: ANN001, ANN202
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    app = create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire())
    api = TestClient(app, raise_server_exceptions=False)
    return AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=scenario, player_factory=factory, seed=SEED,
    ).run()


@pytest.mark.slow
def test_i150_001_recovery_pass_is_recorded_and_reported_apart(
    db_engine: Engine, short_game: Path
) -> None:
    result = _run(db_engine, short_game)
    export = result.export

    recovered = [
        row for row in export["agent_errors"]
        if row["actor_id"] == STALLER and row["phase"] == "cons"
        and (row["provider_metadata"] or {}).get("recovery", {}).get("action") == "pass_con"
    ]
    assert recovered, "Belvar's failed Con activations were recovered by pass_con"
    argument_ids = {argument["id"] for argument in export["arguments"]}
    recovered_arguments = {row["provider_metadata"]["recovery"]["argument_id"] for row in recovered}
    assert recovered_arguments <= argument_ids

    condition = export["run_summary"]["con_condition"]
    by_argument = {entry["argument_id"]: entry for entry in condition["arguments"]}
    for argument_id in recovered_arguments:
        assert by_argument[argument_id]["recovery_passes"] == 1
    totals = condition["totals"]
    assert totals["recovery_passes"] == len(recovered_arguments)
    # Every recovery pass is also a pass; the chosen passes are the difference.
    assert totals["passes"] >= totals["recovery_passes"]
    assert f"({totals['recovery_passes']} after failed activations)" in result.summary_text


def test_i150_002_a_game_without_failures_reports_no_recovery_passes(
    db_engine: Engine, short_game: Path
) -> None:
    def clean(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_policy(default_policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
        )
        return LifecycleRunner(player, sleep=lambda: None)

    result = _run(db_engine, short_game, clean)
    condition = result.export["run_summary"]["con_condition"]
    assert condition["totals"]["recovery_passes"] == 0
    assert all(entry["recovery_passes"] == 0 for entry in condition["arguments"])
    assert "after failed activations" not in result.summary_text
