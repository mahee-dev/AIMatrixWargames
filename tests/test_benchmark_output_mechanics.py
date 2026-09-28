"""I-084 — benchmark runner/output mechanics at large counts and interrupted runs.

Five local fixes on one boundary (``benchmark/runner.py`` + ``benchmark/outputs.py``): the
seed policy's collision-free range is enforced (methodology §8 "no accidental reuse
ambiguity"), attempts load in schedule order past position 999, the completion rate keeps the
planned denominator (§21, D-068), ``turns_completed`` counts completed turns (state-machine
§22), and JSON evidence is written atomically. Test IDs ``TEST-I084-001…005`` are the task's
own (I-084 in ``dev/BACKLOG_RESOLVED.md``).
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest


ACTORS = ["a", "b", "c"]
MODELS = ["provider/x", "provider/y", "provider/z"]


def _schedule(**overrides):
    from benchmark.runner import build_schedule

    kwargs = dict(
        actor_ids=ACTORS, models=MODELS, rotate=True, repetitions=5,
        base_seed=424000, scheduling_seed=7, experiment_id="exp-1",
    )
    kwargs.update(overrides)
    return build_schedule(**kwargs)


def _export(attempt, *, status="complete", turn=6, turns_completed=None, manifest=True):
    """A minimal export shaped like ``app.export.build_game_export`` output."""
    actor_ids = list(attempt.models)
    completed = turn if turns_completed is None else turns_completed
    events = [
        {"id": n + 1, "type": "turn_completed", "turn": n + 1, "actor_id": None,
         "payload": {"turn": n + 1}}
        for n in range(completed)
    ]
    export = {
        "export_format_version": 11,
        "game": {
            "id": f"game-{attempt.position}", "scenario_id": "fixture", "scenario_version": "1",
            "seed": attempt.seed, "status": status, "turn": turn, "phase": "argument",
            "active_actor_id": actor_ids[0], "turn_order": actor_ids,
        },
        "scenario_hashes": {"scenario_content_sha256": "abc", "resource_manifest_sha256": "def"},
        "actors": [{"id": a, "name": a.upper()} for a in actor_ids],
        "messages": [], "arguments": [], "adjudications": [], "cons": [],
        "resource_access": [], "agent_runs": [], "agent_errors": [],
        "events": [{"id": 0, "type": "game_created", "turn": 1, "actor_id": None, "payload": {}}]
        + events,
        "run_manifest": {
            "seed": attempt.seed,
            "benchmark": {
                "experiment_id": "exp-1", "assignment_id": attempt.assignment_id,
                "assignment_index": attempt.assignment_index,
                "repetition_index": attempt.repetition_index, "run_class": "playtest",
            },
            "player": {"assignments": dict(attempt.models)},
            "umpire": {"model": "provider/umpire"},
        },
        "run_summary": {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": None},
    }
    if not manifest:
        export["run_manifest"] = None
    return export


# --- TEST-I084-001 — the recorded seed policy is only offered over its collision-free range ---


def test_i084_001_seed_policy_refuses_the_collision_range() -> None:
    from benchmark.runner import SEED_POLICY, ScheduleError, game_seed

    # methodology §8: "no accidental reuse ambiguity". Under the recorded policy the seeds of
    # (assignment 0, repetition 1000) and (assignment 1, repetition 0) coincide.
    assert SEED_POLICY == "base_seed + assignment_index * 1000 + repetition_index"
    assert game_seed(424000, 0, 1000) == game_seed(424000, 1, 0)

    with pytest.raises(ScheduleError, match="999"):
        _schedule(repetitions=1000)

    # The whole valid range is collision-free, and schedules inside it are unchanged.
    largest = _schedule(repetitions=999)
    assert len(largest.attempts) == 6 * 999
    assert len({a.seed for a in largest.attempts}) == 6 * 999
    assert [a.position for a in largest.attempts] == list(range(6 * 999))
    assert _schedule(repetitions=5) == _schedule(repetitions=5)
    assert [a.seed for a in _schedule(repetitions=2).attempts] == [
        424000 + a.assignment_index * 1000 + a.repetition_index
        for a in _schedule(repetitions=2).attempts
    ]


def test_i084_001_cli_refuses_repetitions_beyond_the_seed_policy(monkeypatch, tmp_path) -> None:
    from tests.test_benchmark_runner import _invoke_benchmark

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", ",".join(MODELS), "--rotate", "--repetitions", "1000"],
    )
    assert result.exit_code == 2
    assert "999" in result.output
    assert "schedule" not in captured

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", ",".join(MODELS), "--rotate", "--repetitions", "999"],
    )
    assert result.exit_code == 0, result.output
    assert captured["schedule"].counts()["total_games"] == 6 * 999


# --- TEST-I084-002 — attempts load in schedule order, not directory-name order ---------------


def test_i084_002_attempts_load_in_integer_position_order(tmp_path) -> None:
    from benchmark.outputs import load_attempts

    for position in (1000, 999, 998, 1001):
        directory = tmp_path / "games" / f"{position:03d}-assignment-01-rep{position:02d}"
        directory.mkdir(parents=True)
        (directory / "attempt.json").write_text(
            json.dumps({"position": position, "assignment_id": "assignment-01"}),
            encoding="utf-8",
        )
        (directory / "export.json").write_text(json.dumps({"game": None}), encoding="utf-8")
    # Lexically "1000-…" < "1001-…" < "998-…" < "999-…"; schedule order is the integer position.
    assert sorted(p.name for p in (tmp_path / "games").iterdir())[0].startswith("1000-")

    assert [attempt["position"] for attempt, _ in load_attempts(tmp_path)] == [
        998, 999, 1000, 1001,
    ]


# --- TEST-I084-003 — the completion rate keeps the planned denominator ---------------------


class _Controls:
    """The narrowest ``BatchControls`` stand-in: declares nothing, verifies nothing.

    ``run_batch`` still verifies each attempt when controls are present, so a completed game
    that produced no manifest is an integrity failure (``missing_manifest``) and stops the batch.
    """

    consequence_condition = None  # D-092: declares no consequence resolver

    def record(self):
        return {}

    def verify_umpire(self, manifest):
        return []

    def verify_consequence(self, manifest):
        return []

    def verify_condition(self, manifest):
        return []


def test_i084_003_completion_rate_keeps_the_planned_denominator(tmp_path) -> None:
    from benchmark.runner import BatchIntegrityError, run_batch

    schedule = _schedule(rotate=False, models=["provider/x"], repetitions=4, base_seed=10)
    output_dir = tmp_path / "batch"
    before_first_game: dict = {}

    def execute(attempt):
        if not before_first_game:
            before_first_game.update(
                json.loads((output_dir / "batch.json").read_text(encoding="utf-8"))
            )
        # One good game — and it carries no manifest, so the batch stops after it (D-058).
        return _export(attempt, manifest=False)

    with pytest.raises(BatchIntegrityError) as failure:
        run_batch(
            schedule, execute=execute, output_dir=output_dir,
            experiment={"name": "i084", "benchmark_mode": None, "scenario_id": "fixture"},
            controls=_Controls(),
        )

    # Before game 1: planned denominator visible, nothing executed, no success rate to report.
    assert before_first_game["schedule"]["total_games"] == 4
    assert before_first_game["executed_games"] == 0
    assert before_first_game["completion_rate"] == 0.0
    assert before_first_game["planned_completion_rate"] == 0.0
    assert before_first_game["execution_success_rate"] is None

    assert failure.value.result.counts() == {
        "total_games": 4, "completed_games": 1, "failed_games": 0, "not_run_games": 3,
    }
    batch = json.loads((output_dir / "batch.json").read_text(encoding="utf-8"))
    assert batch["executed_games"] == 1 and batch["not_run_games"] == 3
    # methodology §21: "Failed games remain part of that denominator"; D-068: ``not_run`` keeps
    # the planned denominator visible. One good game out of four planned is not 100 %.
    assert batch["completion_rate"] == pytest.approx(1 / 4)
    assert batch["planned_completion_rate"] == pytest.approx(1 / 4)
    # …while the separately named execution-success rate says every executed game completed.
    assert batch["execution_success_rate"] == pytest.approx(1.0)


def test_i084_003_rates_agree_when_every_attempt_executed(tmp_path) -> None:
    from benchmark.runner import AttemptFailed, run_batch

    schedule = _schedule(rotate=False, models=["provider/x"], repetitions=3, base_seed=10)

    def execute(attempt):
        if attempt.repetition_index == 1:
            raise AttemptFailed(
                category="agent_stall", reason="stalled",
                export=_export(attempt, status="running", turn=3, turns_completed=2),
                turn_reached=3, actor_affected="b",
            )
        return _export(attempt)

    run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch",
        experiment={"name": "i084", "benchmark_mode": None, "scenario_id": "fixture"},
    )
    batch = json.loads((tmp_path / "batch" / "batch.json").read_text(encoding="utf-8"))
    assert batch["executed_games"] == 3 == batch["schedule"]["total_games"]
    assert batch["completion_rate"] == batch["planned_completion_rate"] == pytest.approx(2 / 3)
    assert batch["execution_success_rate"] == pytest.approx(2 / 3)


# --- TEST-I084-004 — turns_completed counts completed turns -------------------------------


def test_i084_004_turns_completed_counts_turn_completed_events(tmp_path) -> None:
    from benchmark.outputs import build_tables
    from benchmark.runner import AttemptFailed, run_batch

    schedule = _schedule(rotate=False, models=["provider/x"], repetitions=3, base_seed=10)

    def execute(attempt):
        if attempt.repetition_index == 1:
            # Interrupted in turn 3: state-machine §22 has closed turns 1 and 2 only.
            raise AttemptFailed(
                category="agent_stall", reason="stalled",
                export=_export(attempt, status="running", turn=3, turns_completed=2),
                turn_reached=3, actor_affected="b",
            )
        if attempt.repetition_index == 2:
            raise RuntimeError("no game was ever created")
        return _export(attempt, turn=6)

    run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch",
        experiment={"name": "i084", "benchmark_mode": None, "scenario_id": "fixture"},
    )
    from benchmark.outputs import load_attempts

    rows = {
        row["repetition_index"]: row
        for row in build_tables(load_attempts(tmp_path / "batch"))["games"]
    }
    assert rows[0]["turns_completed"] == 6 and rows[0]["turn_reached"] == 6
    assert rows[1]["turns_completed"] == 2 and rows[1]["turn_reached"] == 3
    assert rows[2]["turns_completed"] is None and rows[2]["turn_reached"] is None

    # The CSV carries the same values.
    import csv

    with (tmp_path / "batch" / "games.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = {int(r["repetition_index"]): r for r in csv.DictReader(handle)}
    assert csv_rows[1]["turns_completed"] == "2" and csv_rows[1]["turn_reached"] == "3"


# --- TEST-I084-005 — JSON evidence is written atomically ----------------------------------


def test_i084_005_json_is_replaced_atomically(tmp_path, monkeypatch) -> None:
    from app import export as export_module  # the writer's home since I-097
    from app.export import write_json_atomic as _write_json

    target = tmp_path / "batch.json"
    real_replace = os.replace  # ``export_module.os`` is this module; capture before patching it
    _write_json(target, {"attempts": [1]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"attempts": [1]}
    assert [p.name for p in tmp_path.iterdir()] == ["batch.json"]

    # A crash after the temp file is written but before it replaces the target (a kill
    # mid-write) leaves the previous evidence intact and no temp file behind.
    def crash(src, dst, *args, **kwargs):
        raise OSError("killed mid-write")

    monkeypatch.setattr(export_module.os, "replace", crash)
    with pytest.raises(OSError, match="killed"):
        _write_json(target, {"attempts": [1, 2]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"attempts": [1]}
    assert [p.name for p in tmp_path.iterdir()] == ["batch.json"]

    # The happy path goes through ``os.replace`` of a sibling temp file (same directory, so the
    # rename is atomic on POSIX) and leaves only the target behind.
    replaced: list[tuple[Path, Path]] = []

    def observe(src, dst, *args, **kwargs):
        replaced.append((Path(src), Path(dst)))
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(export_module.os, "replace", observe)
    _write_json(target, {"attempts": [1, 2]})
    assert json.loads(target.read_text(encoding="utf-8")) == {"attempts": [1, 2]}
    [(source, destination)] = replaced
    assert destination == target and source.parent == target.parent and source != target
    assert [p.name for p in tmp_path.iterdir()] == ["batch.json"]


def test_i084_005_batch_outputs_are_written_through_the_atomic_writer(tmp_path, monkeypatch) -> None:
    from app import export as export_module
    from benchmark.runner import run_batch

    schedule = _schedule(rotate=False, models=["provider/x"], repetitions=2, base_seed=10)
    replaced: list[str] = []
    real_replace = os.replace

    def observe(src, dst, *args, **kwargs):
        replaced.append(Path(dst).name)
        return real_replace(src, dst, *args, **kwargs)

    monkeypatch.setattr(export_module.os, "replace", observe)
    run_batch(
        schedule, execute=lambda attempt: _export(attempt), output_dir=tmp_path / "batch",
        experiment={"name": "i084", "benchmark_mode": None, "scenario_id": "fixture"},
    )
    # batch.json before game 1 and after each of the two attempts; export + attempt per game.
    assert replaced.count("batch.json") == 3
    assert replaced.count("export.json") == 2 and replaced.count("attempt.json") == 2
    leftovers = [p for p in (tmp_path / "batch").rglob("*") if p.is_file() and ".tmp" in p.name]
    assert leftovers == []
