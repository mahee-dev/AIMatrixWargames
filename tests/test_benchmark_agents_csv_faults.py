"""I-102 — ``agents.csv`` keeps the §21 attempt outcome apart from per-actor activation faults.

``benchmark/outputs.py`` used to put an Actor's *last failed activation's* reason under
``failure_reason`` beside ``completed=true`` (run 11: Belvar "no progress after 3 activations"
in a finished game), and took ``agent_framework_version`` from the Actor's first ``AgentRun``
— ``None`` when that run was the failure row (T-053). ``completed``/``failure_reason`` are now
the methodology §21 attempt outcome attributed to ``actor_affected``; a recovered fault shows
under ``failed_activations`` + ``last_activation_failure_reason``; identity comes from the
manifest's sealed player block, never from a failure row. Test IDs ``TEST-I102-001…004`` are
the task's own (I-102 in ``dev/BACKLOG_RESOLVED.md``).
"""

from __future__ import annotations

import re

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
ACTORS = ["a", "b", "c"]
STALL = "no progress after 3 activations in diplomacy (turn 4)"
CEILING = "activation ceiling hit for 'a': The next request would exceed the request ceiling"


def _attempt(**overrides):
    attempt = {
        "position": 1, "assignment_id": "assignment-01", "assignment_index": 0,
        "repetition_index": 1, "seed": 424001, "experiment_id": "exp-1",
        "models": {actor: f"provider/{actor}" for actor in ACTORS},
        "completed": True, "status": "completed", "failure_reason": None,
        "failure_category": None, "turn_reached": 6, "actor_affected": None,
    }
    attempt.update(overrides)
    return attempt


def _run(actor_id, *, completed=True, failure_reason=None, framework_version="2.42.0"):
    """An ``AgentRun`` row as ``agents/autonomous.py`` records it: the shared body on both
    paths, ``agent_framework_version`` deliberately ``None`` on the failure path (T-053)."""
    return {
        "role": "player", "actor_id": actor_id, "phase": "diplomacy", "turn": 4,
        "prompt_version": "player-v1.13", "prompt_hash": "abc", "model_parameters": {"t": 0},
        "reasoning_configuration": None, "agent_framework": "pydantic-ai",
        "agent_framework_version": framework_version if completed else None,
        "tool_schema_version": "v13", "input_tokens": 10, "output_tokens": 5,
        "tool_calls": 1, "invalid_tool_calls": 0, "stalls": 0 if completed else 1,
        "latency_ms": 100, "completed": completed, "failure_reason": failure_reason,
    }


def _export(attempt, agent_runs, *, manifest=True):
    export = {
        "game": {
            "id": "game-1", "scenario_id": "fixture", "scenario_version": "1",
            "seed": attempt["seed"], "status": "complete", "turn": 6, "phase": "argument",
        },
        "scenario_hashes": {}, "actors": [{"id": a} for a in ACTORS],
        "messages": [], "arguments": [], "adjudications": [], "cons": [],
        "resource_access": [], "agent_runs": agent_runs, "agent_errors": [], "events": [],
        "run_manifest": {
            "seed": attempt["seed"],
            "benchmark": {"experiment_id": "exp-1"},
            "player": {
                "assignments": dict(attempt["models"]),
                "agent_framework": "pydantic-ai", "agent_framework_version": "2.42.0",
            },
            "umpire": {"model": "provider/umpire"},
        },
        "run_summary": {},
    }
    if not manifest:
        export["run_manifest"] = None
    return export


def _rows(attempt, export):
    from benchmark.outputs import build_tables

    return {row["actor_id"]: row for row in build_tables([(attempt, export)])["agents"]}


# --- TEST-I102-001 — a recovered stall is an activation fault, not an attempt failure ---------


def test_i102_001_recovered_stall_is_not_an_attempt_failure() -> None:
    attempt = _attempt()
    export = _export(attempt, [
        _run("a", completed=False, failure_reason=STALL),
        _run("a"),
        _run("b"), _run("c"),
    ])

    row = _rows(attempt, export)["a"]

    # §21: the attempt completed and no Actor is affected — ``failure_reason`` is empty.
    assert row["completed"] is True
    assert row["failure_reason"] is None
    # The fault the coordinator recovered from is still data, under its own fields.
    assert row["failed_activations"] == 1
    assert row["last_activation_failure_reason"] == STALL
    assert _rows(attempt, export)["b"]["last_activation_failure_reason"] is None


def test_i102_001_last_activation_failure_is_the_last_one() -> None:
    attempt = _attempt()
    export = _export(attempt, [
        _run("a", completed=False, failure_reason=STALL),
        _run("a"),
        _run("a", completed=False, failure_reason=CEILING),
        _run("a"),
    ])

    row = _rows(attempt, export)["a"]

    assert row["failed_activations"] == 2
    assert row["last_activation_failure_reason"] == CEILING


# --- TEST-I102-002 — identity comes from the sealed manifest, never from a failure row -------


def test_i102_002_framework_version_from_manifest_when_first_run_failed() -> None:
    attempt = _attempt()
    export = _export(attempt, [
        _run("a", completed=False, failure_reason=STALL),   # framework version None (T-053)
        _run("a"),
        _run("b"), _run("c"),
    ])

    rows = _rows(attempt, export)

    assert rows["a"]["agent_framework_version"] == "2.42.0"
    assert all(row["agent_framework_version"] == "2.42.0" for row in rows.values())


def test_i102_002_framework_version_falls_back_to_completed_runs_without_manifest() -> None:
    attempt = _attempt()
    export = _export(attempt, [
        _run("a", completed=False, failure_reason=STALL),
        _run("a", framework_version="2.41.0"),
    ], manifest=False)

    row = _rows(attempt, export)["a"]

    # No manifest: the version of a *completed* run; the failure row's ``None`` never wins.
    assert row["agent_framework_version"] == "2.41.0"


def test_i102_002_no_completed_attempt_exports_a_null_framework_version() -> None:
    attempt = _attempt()
    export = _export(attempt, [_run("a", completed=False, failure_reason=STALL), _run("a")])

    rows = _rows(attempt, export)

    assert attempt["completed"] is True
    assert None not in {row["agent_framework_version"] for row in rows.values()}


# --- TEST-I102-003 — an attempt failure is attributed to the affected Actor only --------------


def test_i102_003_attempt_failure_lands_on_the_affected_actor_only() -> None:
    attempt = _attempt(
        completed=False, status="failed", failure_reason="agent stall: b",
        failure_category="stall", turn_reached=4, actor_affected="b",
    )
    export = _export(attempt, [
        _run("a", completed=False, failure_reason=CEILING),   # A recovered from its own fault
        _run("a"),
        _run("b", completed=False, failure_reason="agent stall: b"),
    ])

    rows = _rows(attempt, export)

    assert rows["b"]["completed"] is False
    assert rows["b"]["failure_reason"] == "agent stall: b"
    assert rows["b"]["last_activation_failure_reason"] == "agent stall: b"
    # A's recovered ceiling hit is *not* the attempt failure.
    assert rows["a"]["completed"] is False
    assert rows["a"]["failure_reason"] is None
    assert rows["a"]["last_activation_failure_reason"] == CEILING
    assert rows["c"]["failure_reason"] is None
    assert rows["c"]["last_activation_failure_reason"] is None


def test_i102_003_non_actor_failure_leaves_every_actor_row_unattributed() -> None:
    # An Umpire/server failure has no ``actor_affected``; ``games.csv`` carries the reason.
    attempt = _attempt(
        completed=False, status="failed", failure_reason="adjudicator_unavailable",
        failure_category="umpire_failure", turn_reached=3, actor_affected=None,
    )
    export = _export(attempt, [_run("a", completed=False, failure_reason=STALL), _run("a")])

    rows = _rows(attempt, export)

    assert all(row["completed"] is False for row in rows.values())
    assert all(row["failure_reason"] is None for row in rows.values())
    assert rows["a"]["last_activation_failure_reason"] == STALL


# --- TEST-I102-004 / TEST-I105-003 — the §25 column lists match the written tables ---------


_SUGGESTED_TABLE = re.compile(r"Suggested `(\w+)\.csv`[^\n]*:\n\n```text\n(.*?)```", re.S)


def test_i102_004_i105_003_methodology_column_lists_name_written_columns() -> None:
    """Every column benchmark-methodology §25 suggests is one ``benchmark.outputs`` writes.

    The §25 lists are a documented subset, so the check is doc ⊆ code for every table. The
    columns I-102 (``agents.csv``) and I-105 (``games.csv``) added must appear in the doc.
    """
    from benchmark.outputs import _TABLE_COLUMNS

    text = (ROOT / "docs/benchmark-methodology.md").read_text(encoding="utf-8")
    documented = {
        table: [line.split()[0] for line in block.splitlines() if line and not line[0].isspace()]
        for table, block in _SUGGESTED_TABLE.findall(text)
    }
    assert {"games", "agents", "arguments", "messages", "resource_access"} <= set(documented)
    for table, columns in documented.items():
        unwritten = set(columns) - set(_TABLE_COLUMNS[table])
        assert not unwritten, f"§25 {table}.csv lists columns never written: {unwritten}"
    assert "last_activation_failure_reason" in documented["agents"]
    assert {"player_activation_mode", "player_tool_surface"} <= set(documented["games"])
