"""I-105 — ``games.csv`` names the player activation mode and tool surface.

The game-level ``invalid_tool_calls`` total sat beside ``player_prompt_version``,
``diplomacy_mode`` and ``con_mode`` but not beside ``player.activation_mode`` (D-046) or
``player.tool_surface`` (D-079), so the CSV alone could not tell a ``tool_loop/broad`` count from
a ``single_shot`` or ``available_only`` one. Both come from the manifest's sealed player block;
nothing is inferred for a manifest that predates D-079 (TRD §32: raw data first). Test IDs
``TEST-I105-001…003`` are the task's own (I-105 in ``dev/BACKLOG_RESOLVED.md``).
"""

from __future__ import annotations

import csv
import json


ACTORS = ["a", "b", "c"]


def _attempt(position=1):
    return {
        "position": position, "assignment_id": "assignment-01", "assignment_index": 0,
        "repetition_index": 1, "seed": 424000 + position, "experiment_id": "exp-1",
        "models": {actor: f"provider/{actor}" for actor in ACTORS},
        "completed": True, "status": "completed", "failure_reason": None,
        "failure_category": None, "turn_reached": 6, "actor_affected": None,
    }


def _export(attempt, player=None, *, manifest=True):
    export = {
        "game": {
            "id": f"game-{attempt['position']}", "scenario_id": "fixture",
            "scenario_version": "1", "seed": attempt["seed"], "status": "complete", "turn": 6,
        },
        "scenario_hashes": {}, "actors": [{"id": a} for a in ACTORS],
        "messages": [], "arguments": [], "adjudications": [], "cons": [],
        "resource_access": [], "agent_runs": [], "agent_errors": [], "events": [],
        "run_manifest": {
            "seed": attempt["seed"], "benchmark": {"experiment_id": "exp-1"},
            "player": {"assignments": dict(attempt["models"]), **(player or {})},
            "umpire": {"model": "provider/umpire"},
        },
        "run_summary": {},
    }
    if not manifest:
        export["run_manifest"] = None
    return export


def _game_row(attempt, export):
    from benchmark.outputs import build_tables

    return build_tables([(attempt, export)])["games"][0]


# --- TEST-I105-001 — both sealed fields reach games.csv --------------------------------------


def test_i105_001_games_csv_carries_activation_mode_and_tool_surface(tmp_path) -> None:
    from benchmark.outputs import write_tables

    attempt = _attempt()
    export = _export(attempt, {"activation_mode": "tool_loop", "tool_surface": "available_only"})
    row = _game_row(attempt, export)
    assert row["player_activation_mode"] == "tool_loop"
    assert row["player_tool_surface"] == "available_only"

    directory = tmp_path / "games" / "001-assignment-01-rep01"
    directory.mkdir(parents=True)
    (directory / "attempt.json").write_text(json.dumps(attempt), encoding="utf-8")
    (directory / "export.json").write_text(json.dumps(export), encoding="utf-8")
    written = write_tables(tmp_path)
    with written["games"].open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    assert rows[0]["player_activation_mode"] == "tool_loop"
    assert rows[0]["player_tool_surface"] == "available_only"
    # Beside the existing player-condition provenance columns, before the game-level counts.
    header = list(rows[0])
    assert header.index("player_prompt_hash") < header.index("player_activation_mode")
    assert header.index("player_tool_surface") < header.index("invalid_tool_calls")


# --- TEST-I105-002 — a legacy manifest projects what it states, nothing more ------------------


def test_i105_002_pre_d079_manifest_exports_an_empty_tool_surface() -> None:
    attempt = _attempt()
    row = _game_row(attempt, _export(attempt, {"activation_mode": "tool_loop"}))

    assert row["player_activation_mode"] == "tool_loop"
    assert row["player_tool_surface"] is None   # not an inferred "broad"


def test_i105_002_no_manifest_exports_both_empty() -> None:
    attempt = _attempt()
    row = _game_row(attempt, _export(attempt, manifest=False))

    assert row["player_activation_mode"] is None
    assert row["player_tool_surface"] is None
