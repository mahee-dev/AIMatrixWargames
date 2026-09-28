"""T-054 + T-058 + T-059 — benchmark runner, raw outputs, failure continuation (testing.md §22).

No ``TEST-*`` IDs exist for this area; tests are named after the §22 checklist items and the
task IDs (T-054 in ``dev/WBS.md``).
"""

from __future__ import annotations

import itertools

import pytest

from tests.benchmark_helpers import read_tables


ACTORS = ["a", "b", "c"]
MODELS = ["provider/x", "provider/y", "provider/z"]


# --- T-054-001 — schedule counts are reported before execution ------------------------------


def test_t054_001_schedule_counts_before_execution() -> None:
    from benchmark.runner import build_schedule

    schedule = build_schedule(
        actor_ids=ACTORS, models=MODELS, rotate=True, repetitions=5,
        base_seed=1000, scheduling_seed=7, experiment_id="exp-1",
    )

    assert schedule.counts() == {
        "assignment_count": 6,
        "repetitions_per_assignment": 5,
        "total_games": 30,
    }
    assert len(schedule.attempts) == 30
    # TRD §30 / methodology §6: 3 models × 3 Actors under --rotate is every permutation.
    assignments = {tuple(a.models[actor] for actor in ACTORS) for a in schedule.assignments}
    assert assignments == set(itertools.permutations(MODELS))


def test_t054_001_single_assignment_without_rotate() -> None:
    from benchmark.runner import build_schedule

    positional = build_schedule(
        actor_ids=ACTORS, models=MODELS, rotate=False, repetitions=2,
        base_seed=0, scheduling_seed=0, experiment_id="exp-1",
    )
    assert positional.counts() == {
        "assignment_count": 1, "repetitions_per_assignment": 2, "total_games": 2,
    }
    assert positional.assignments[0].models == dict(zip(ACTORS, MODELS))

    homogeneous = build_schedule(
        actor_ids=ACTORS, models=["provider/x"], rotate=False, repetitions=1,
        base_seed=0, scheduling_seed=0, experiment_id="exp-1",
    )
    assert homogeneous.assignments[0].models == {actor: "provider/x" for actor in ACTORS}


def test_t054_001_rejects_unsupported_model_counts() -> None:
    from benchmark.runner import ScheduleError, build_schedule

    with pytest.raises(ScheduleError):  # D-056: unequal --rotate counts wait for T-055
        build_schedule(
            actor_ids=ACTORS, models=MODELS[:2], rotate=True, repetitions=1,
            base_seed=0, scheduling_seed=0, experiment_id="exp-1",
        )
    with pytest.raises(ScheduleError):  # neither one model for all Seats nor one per Actor
        build_schedule(
            actor_ids=ACTORS, models=MODELS[:2], rotate=False, repetitions=1,
            base_seed=0, scheduling_seed=0, experiment_id="exp-1",
        )
    with pytest.raises(ScheduleError):
        build_schedule(
            actor_ids=ACTORS, models=MODELS, rotate=False, repetitions=0,
            base_seed=0, scheduling_seed=0, experiment_id="exp-1",
        )


# --- T-054-002 — seeds come from identity only; scheduling_seed only orders ------------------


def _schedule(scheduling_seed: int, **overrides):
    from benchmark.runner import build_schedule

    kwargs = dict(
        actor_ids=ACTORS, models=MODELS, rotate=True, repetitions=5,
        base_seed=424000, scheduling_seed=scheduling_seed, experiment_id="exp-1",
    )
    kwargs.update(overrides)
    return build_schedule(**kwargs)


def test_t054_002_attempt_identity_and_seed_policy() -> None:
    schedule = _schedule(7)

    identities = [(a.assignment_id, a.repetition_index) for a in schedule.attempts]
    assert len(set(identities)) == 30
    assert len({a.seed for a in schedule.attempts}) == 30
    for attempt in schedule.attempts:
        # methodology §8: seed = base_seed + assignment_index * 1000 + repetition_index
        assert attempt.seed == 424000 + attempt.assignment_index * 1000 + attempt.repetition_index
    assert [a.position for a in schedule.attempts] == list(range(30))


def test_t054_002_scheduling_seed_changes_order_only() -> None:
    first, second = _schedule(7), _schedule(8)

    seeds_by_identity = lambda s: {  # noqa: E731
        (a.assignment_id, a.repetition_index): a.seed for a in s.attempts
    }
    assert seeds_by_identity(first) == seeds_by_identity(second)
    assert first.assignments == second.assignments
    order = lambda s: [(a.assignment_id, a.repetition_index) for a in s.attempts]  # noqa: E731
    assert order(first) != order(second)
    assert order(first) == order(_schedule(7))  # deterministic for the recorded seed
    # Interleaved, not assignment-major: the first 5 attempts are not one assignment's repetitions.
    assert len({a.assignment_id for a in first.attempts[:5]}) > 1


# --- T-059 — one failed attempt never aborts the batch; failures are retained data -----------


def _fake_export(attempt, *, status: str = "complete", turn: int = 6) -> dict:
    """A minimal export shaped like ``app.export.build_game_export`` output."""
    actor_ids = list(attempt.models)
    return {
        "export_format_version": 5,
        "game": {
            "id": f"game-{attempt.position}", "scenario_id": "fixture", "scenario_version": "1",
            "seed": attempt.seed, "status": status, "turn": turn, "phase": "consequences",
            "active_actor_id": actor_ids[0], "turn_order": actor_ids,
        },
        "scenario_hashes": {"scenario_content_sha256": "abc", "resource_manifest_sha256": "def"},
        "actors": [{"id": a, "name": a.upper()} for a in actor_ids],
        "messages": [], "arguments": [], "adjudications": [], "cons": [],
        "resource_access": [], "agent_runs": [], "agent_errors": [], "events": [],
        "run_manifest": {
            "benchmark": {
                "experiment_id": "exp-1", "assignment_id": attempt.assignment_id,
                "repetition_index": attempt.repetition_index, "run_class": "playtest",
            },
            "player": {"assignments": dict(attempt.models)},
            "umpire": {"model": "provider/umpire"},
        },
        "run_summary": {"input_tokens": 0, "output_tokens": 0, "estimated_cost_usd": None},
    }


def test_t059_001_failed_attempt_does_not_abort_batch(tmp_path) -> None:
    import json

    from benchmark.runner import AttemptFailed, run_batch

    schedule = _schedule(3, rotate=False, models=MODELS, repetitions=3, base_seed=10)
    executed: list[int] = []

    def execute(attempt):
        executed.append(attempt.position)
        if attempt.repetition_index == 1:
            raise AttemptFailed(
                category="agent_stall",
                reason="AutonomousGameStalled: no Actor could progress",
                export=_fake_export(attempt, status="running", turn=3),
                turn_reached=3,
                actor_affected="b",
            )
        return _fake_export(attempt)

    result = run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch",
        experiment={"name": "smoke", "benchmark_mode": None, "scenario_id": "fixture"},
    )

    assert executed == [0, 1, 2]  # the failure did not stop later attempts
    assert result.counts() == {"total_games": 3, "completed_games": 2, "failed_games": 1, "not_run_games": 0}
    batch = json.loads((tmp_path / "batch" / "batch.json").read_text(encoding="utf-8"))
    assert batch["schedule"]["total_games"] == 3
    assert batch["scheduling_seed"] == 3 and batch["base_seed"] == 10
    assert batch["seed_policy"] == "base_seed + assignment_index * 1000 + repetition_index"
    failed = [a for a in batch["attempts"] if not a["completed"]]
    assert len(failed) == 1
    assert failed[0]["failure_category"] == "agent_stall"
    assert failed[0]["failure_reason"] == "AutonomousGameStalled: no Actor could progress"
    assert failed[0]["turn_reached"] == 3 and failed[0]["actor_affected"] == "b"
    assert failed[0]["repetition_index"] == 1
    assert batch["completed_games"] == 2 and batch["failed_games"] == 1
    assert batch["completion_rate"] == pytest.approx(2 / 3)


def test_t059_002_failure_material_is_redacted_and_retained(tmp_path) -> None:
    import json

    from benchmark.runner import AttemptFailed, run_batch

    schedule = _schedule(1, rotate=False, models=["provider/x"], repetitions=1)
    secret = "sk-or-v1-supersecretkey0123456789"

    def execute(attempt):
        raise AttemptFailed(
            category="provider_timeout",
            reason=f"ModelClientError: 401 with key {secret} Bearer tok_abcdefghijkl "
                   "seat_0123456789abcdef",
            export=_fake_export(attempt, status="running", turn=1),
            turn_reached=1, actor_affected="a",
        )

    run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch",
        experiment={"name": "smoke"}, secret_values=[secret],
    )

    written = "\n".join(
        p.read_text(encoding="utf-8") for p in (tmp_path / "batch").rglob("*") if p.is_file()
    )
    assert secret not in written and "tok_abcdefghijkl" not in written
    assert "seat_0123456789abcdef" not in written
    attempt_dirs = sorted((tmp_path / "batch" / "games").iterdir())
    assert len(attempt_dirs) == 1
    record = json.loads((attempt_dirs[0] / "attempt.json").read_text(encoding="utf-8"))
    assert record["failure_reason"].startswith("ModelClientError: 401 with key ***REDACTED***")
    export = json.loads((attempt_dirs[0] / "export.json").read_text(encoding="utf-8"))
    assert export["game"]["status"] == "running"  # the partial export is kept for debugging


def test_t059_003_attempt_without_a_game_still_gets_an_export(tmp_path) -> None:
    import json

    from benchmark.runner import run_batch

    schedule = _schedule(1, rotate=False, models=["provider/x"], repetitions=2)

    def execute(attempt):
        if attempt.repetition_index == 0:
            raise RuntimeError("database locked")  # crashed before any game existed
        return _fake_export(attempt)

    result = run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch", experiment={"name": "smoke"},
    )

    assert result.counts() == {"total_games": 2, "completed_games": 1, "failed_games": 1, "not_run_games": 0}
    attempt_dirs = sorted((tmp_path / "batch" / "games").iterdir())
    assert len(attempt_dirs) == 2
    for directory in attempt_dirs:
        assert (directory / "attempt.json").is_file() and (directory / "export.json").is_file()
    failed_dir = next(
        d for d in attempt_dirs
        if not json.loads((d / "attempt.json").read_text(encoding="utf-8"))["completed"]
    )
    record = json.loads((failed_dir / "attempt.json").read_text(encoding="utf-8"))
    assert record["game_id"] is None
    assert record["failure_category"] == "server_failure"
    assert record["failure_reason"] == "RuntimeError: database locked"
    stub = json.loads((failed_dir / "export.json").read_text(encoding="utf-8"))
    assert stub["game"] is None and stub["run_manifest"] is None
    assert stub["agent_runs"] == [] and stub["arguments"] == []


# --- T-054-003/004 — an offline batch end to end through AutonomousGameRunner ---------------


def _offline_batch(db_engine, tmp_path, scenario, *, plan=None, repetitions: int = 2, fail_position=None):
    """A real single-assignment batch through the shared harness; returns ``(schedule, result)``."""
    from tests.benchmark_helpers import offline_batch

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=scenario, models=["fake/x", "fake/y", "fake/z"],
        repetitions=repetitions, experiment_id="offline-1",
        experiment={"name": "offline", "scenario_id": "fixture"}, analysis_plan=plan,
        fail_position=fail_position,
    )
    assert outcome is None, outcome
    return schedule, result


@pytest.mark.slow
def test_t054_003_offline_batch_runs_end_to_end(db_engine, tmp_path) -> None:
    """Stays on the six-turn canonical game: the suite's real end-to-end batch (I-135)."""
    import json

    from tests.api_helpers import FIXTURE_DIR

    schedule, result = _offline_batch(db_engine, tmp_path, FIXTURE_DIR)

    assert result.counts() == {"total_games": 2, "completed_games": 2, "failed_games": 0, "not_run_games": 0}
    scheduled = {(a.assignment_id, a.repetition_index): a for a in schedule.attempts}
    game_ids = set()
    for record in result.attempts:
        export = json.loads(
            (tmp_path / "batch" / "games" / record.directory / "export.json").read_text()
        )
        attempt = scheduled[(record.assignment_id, record.repetition_index)]
        assert export["game"]["status"] == "complete"
        assert export["game"]["seed"] == attempt.seed == record.seed  # D-009: batch seed wins
        benchmark = export["run_manifest"]["benchmark"]
        assert benchmark["experiment_id"] == "offline-1"
        assert benchmark["assignment_id"] == attempt.assignment_id
        assert benchmark["repetition_index"] == attempt.repetition_index
        assert benchmark["assignment"] == attempt.models
        assert benchmark["scheduling_seed"] == 11 and benchmark["mode"] == "batch"
        assert benchmark["run_class"] == "playtest" and benchmark["analysis_plan"] is None
        assert export["run_manifest"]["player"]["assignments"] == attempt.models
        assert export["run_manifest"]["seed"] == attempt.seed
        game_ids.add(export["game"]["id"])
    assert len(game_ids) == 2


@pytest.mark.slow
def test_t054_004_run_class_and_sealed_plan_reach_every_game(db_engine, tmp_path, short_game) -> None:
    import json

    from benchmark.analysis_plan import load_analysis_plan
    from tests.test_provenance_analysis_plan import VALID_PLAN, _write_plan

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    _, result = _offline_batch(db_engine, tmp_path, short_game, plan=plan)

    for record in result.attempts:
        export = json.loads(
            (tmp_path / "batch" / "games" / record.directory / "export.json").read_text()
        )
        benchmark = export["run_manifest"]["benchmark"]
        assert benchmark["run_class"] == "analytical"
        assert benchmark["analysis_plan"]["sha256"] == plan.sha256
        assert benchmark["assignment_id"] == record.assignment_id
    batch = json.loads((tmp_path / "batch" / "batch.json").read_text())
    assert batch["completed_games"] == 2


# --- T-059 — real failures through the autonomous executor are classified and retained ------


def test_t059_001_failure_inside_a_real_game_is_retained_with_its_export(
    db_engine, tmp_path
) -> None:
    import json

    from fastapi.testclient import TestClient

    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from agents.scripted import deterministic_umpire
    from app.api import create_app
    from app.config import Settings
    from benchmark.runner import autonomous_executor, build_schedule, run_batch
    from tests.agent_fakes import default_policy, model_from_policy
    from tests.api_helpers import FIXTURE_DIR

    def player_factory(tools, actor_id, actor_name, model):
        if model == "fake/broken":
            raise RuntimeError("player construction failed for Bearer tok_secret_value_1")
        player = ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_policy(default_policy),
            config=PlayerConfig(model=model, activation_mode="tool_loop"),
        )
        return LifecycleRunner(player)

    schedule = build_schedule(
        actor_ids=["arkania", "belvar", "cestia"], models=["fake/broken"],
        rotate=False, repetitions=1, base_seed=1, scheduling_seed=1, experiment_id="e",
    )
    settings = Settings(AMAW_ADMIN_TOKEN="admin-secret-token", _env_file=None)
    api = TestClient(
        create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire()),
        raise_server_exceptions=False,
    )
    with api:
        result = run_batch(
            schedule,
            execute=autonomous_executor(
                api, admin_token="admin-secret-token", scenario=FIXTURE_DIR,
                player_factory=player_factory,
            ),
            output_dir=tmp_path / "batch", experiment={"name": "broken"},
        )

    assert result.counts() == {"total_games": 1, "completed_games": 0, "failed_games": 1, "not_run_games": 0}
    record = result.attempts[0]
    assert record.completed is False
    assert record.game_id is not None  # the game existed; its export is the retained artefact
    assert record.failure_category == "game_incompletion"
    assert record.failure_reason == (
        "RuntimeError: player construction failed for Bearer ***REDACTED***"
    )
    export = json.loads(
        (tmp_path / "batch" / "games" / record.directory / "export.json").read_text()
    )
    assert export["game"]["id"] == record.game_id
    assert export["game"]["status"] != "complete"
    assert "tok_secret_value_1" not in json.dumps(export)


def test_t059_001_failure_categories_follow_methodology_21() -> None:
    from agents.autonomous import AutonomousGameError, AutonomousGameStalled
    from benchmark.runner import classify_failure

    game = {"game": {"id": "g", "status": "running", "turn": 2}, "agent_errors": []}
    stalled = AutonomousGameStalled("no Actor could progress")

    def with_error(category):
        return {**game, "agent_errors": [{"actor_id": "b", "provider_metadata": {"category": category}}]}

    assert classify_failure(stalled, game) == "agent_stall"
    assert classify_failure(stalled, with_error("transient")) == "provider_timeout"
    assert classify_failure(stalled, with_error("guardrail")) == "invalid_tool_loop"
    assert classify_failure(stalled, {**game, "game": {**game["game"], "status": "paused"}}) == (
        "umpire_failure"
    )
    assert classify_failure(AutonomousGameError("admin POST /x failed (500)"), None) == (
        "server_failure"
    )
    assert classify_failure(AutonomousGameError("game stopped before completion"), game) == (
        "game_incompletion"
    )
    assert classify_failure(RuntimeError("boom"), None) == "server_failure"
    assert classify_failure(RuntimeError("boom"), game) == "game_incompletion"


# --- T-058 — raw output tables (TRD §32, methodology §25) -----------------------------------



GAMES_COLUMNS = [
    "game_id", "experiment_id", "scenario_id", "scenario_version", "assignment_id",
    "repetition_index", "seed", "scenario_content_hash", "resource_manifest_hash", "completed",
    "failure_reason", "started_at", "completed_at",
]
AGENTS_COLUMNS = [
    "game_id", "actor_id", "model_requested", "model_returned", "provider", "prompt_version",
    "prompt_hash", "model_parameters", "reasoning_configuration", "agent_framework",
    "agent_framework_version", "tool_schema_version", "input_tokens", "output_tokens",
    "tool_calls", "invalid_tool_calls", "stalls", "cost_usd", "latency_ms", "completed",
    "failure_reason",
]
ARGUMENTS_COLUMNS = [
    "game_id", "turn", "argument_id", "actor_id", "action", "desired_effect", "pros_count",
    "umpire_probability", "roll", "success",
]
MESSAGES_COLUMNS = [
    "game_id", "turn", "message_id", "sender_actor_id", "public", "recipient_count", "created_at",
]
RESOURCE_ACCESS_COLUMNS = [
    "game_id", "turn", "actor_id", "operation", "query", "resource_id", "created_at",
]


def test_t058_001_five_tables_with_the_recommended_columns(db_engine, tmp_path, short_game) -> None:
    import json

    schedule, result = _offline_batch(db_engine, tmp_path, short_game, fail_position=1)
    tables = read_tables(tmp_path / "batch")

    for name, columns in (
        ("games", GAMES_COLUMNS), ("agents", AGENTS_COLUMNS), ("arguments", ARGUMENTS_COLUMNS),
        ("messages", MESSAGES_COLUMNS), ("resource_access", RESOURCE_ACCESS_COLUMNS),
    ):
        fieldnames, _ = tables[name]
        # §25's columns are present and lead; extra raw metrics may follow them.
        assert fieldnames[: len(columns)] == columns, name

    _, games = tables["games"]
    assert len(games) == 2  # one row per scheduled attempt: the denominator is the schedule
    failed = [row for row in games if row["completed"] == "false"]
    completed = [row for row in games if row["completed"] == "true"]
    assert len(failed) == 1 and len(completed) == 1
    assert failed[0]["game_id"] == "" and failed[0]["failure_category"] == "server_failure"
    assert failed[0]["failure_reason"] == "RuntimeError: injected before game creation"
    assert failed[0]["experiment_id"] == "offline-1"
    assert {row["seed"] for row in games} == {str(a.seed) for a in schedule.attempts}

    export = json.loads(
        (tmp_path / "batch" / "games" / result.attempts[0].directory / "export.json").read_text()
    )
    game_id = export["game"]["id"]
    assert completed[0]["game_id"] == game_id
    assert completed[0]["scenario_content_hash"] == (
        export["scenario_hashes"]["scenario_content_sha256"]
    )
    assert completed[0]["resource_manifest_hash"] == (
        export["scenario_hashes"]["resource_manifest_sha256"]
    )
    assert completed[0]["run_class"] == "playtest"

    _, agents = tables["agents"]
    assert len(agents) == 6  # three Actors per attempt, including the attempt without a game
    by_game = {row["game_id"] for row in agents}
    assert by_game == {game_id, ""}
    for row in agents:
        if row["game_id"] == "":
            assert row["model_requested"] in {"fake/x", "fake/y", "fake/z"}  # the assignment
            assert row["completed"] == "false" and row["input_tokens"] == ""

    _, arguments = tables["arguments"]
    assert len(arguments) == len(export["arguments"])
    assert {row["game_id"] for row in arguments} == {game_id}
    _, messages = tables["messages"]
    assert len(messages) == len(export["messages"])
    _, access = tables["resource_access"]
    assert len(access) == len(export["resource_access"])


def test_t058_002_agent_and_argument_metrics_reconcile_with_the_export(
    db_engine, tmp_path, short_game
) -> None:
    import json

    _, result = _offline_batch(db_engine, tmp_path, short_game, repetitions=1)
    tables = read_tables(tmp_path / "batch")
    export = json.loads(
        (tmp_path / "batch" / "games" / result.attempts[0].directory / "export.json").read_text()
    )
    runs = [run for run in export["agent_runs"] if run["role"] == "player"]

    _, agents = tables["agents"]
    for row in agents:
        actor_runs = [run for run in runs if run["actor_id"] == row["actor_id"]]
        assert actor_runs, row
        assert int(row["input_tokens"]) == sum(run["input_tokens"] for run in actor_runs)
        assert int(row["output_tokens"]) == sum(run["output_tokens"] for run in actor_runs)
        assert int(row["tool_calls"]) == sum(run["tool_calls"] for run in actor_runs)
        assert int(row["invalid_tool_calls"]) == sum(run["invalid_tool_calls"] for run in actor_runs)
        assert int(row["stalls"]) == sum(run["stalls"] for run in actor_runs)
        assert int(row["latency_ms"]) == sum(run["latency_ms"] or 0 for run in actor_runs)
        assert row["model_requested"] == export["model_assignments"][row["actor_id"]]
        assert row["prompt_version"] == actor_runs[0]["prompt_version"]
        assert row["completed"] == "true"
    # WBS 5.6 game-level minimum metrics reconcile with the export too.
    _, games = tables["games"]
    (game,) = games
    assert int(game["arguments"]) == len(export["arguments"])
    assert int(game["argument_successes"]) == sum(
        1 for adjudication in export["adjudications"] if adjudication["success"]
    )
    assert int(game["messages"]) == len(export["messages"])
    assert int(game["cons"]) == len(export["cons"])
    assert int(game["input_tokens"]) == export["run_summary"]["input_tokens"]

    _, arguments = tables["arguments"]
    adjudications = {a["argument_id"]: a for a in export["adjudications"]}
    for row in arguments:
        adjudication = adjudications[row["argument_id"]]
        assert float(row["umpire_probability"]) == adjudication["probability"]
        assert row["success"] == ("true" if adjudication["success"] else "false")
        assert row["roll"] == ("" if adjudication["roll"] is None else str(adjudication["roll"]))
        argument = next(a for a in export["arguments"] if a["id"] == row["argument_id"])
        assert int(row["pros_count"]) == len(argument["pros"])


# --- T-054-005 — `amaw benchmark` composes the batch and stays framework-free to import -----


def _invoke_benchmark(monkeypatch, tmp_path, args: list[str]):
    from tests.api_helpers import FIXTURE_DIR
    from tests.benchmark_helpers import invoke_benchmark

    # T-057 made --benchmark-mode mandatory; the T-054 behaviour under test is unchanged.
    return invoke_benchmark(monkeypatch, tmp_path, FIXTURE_DIR, ["--benchmark-mode", "model", *args])


def test_t054_005_cli_reports_counts_and_composes_the_batch(monkeypatch, tmp_path) -> None:
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        [
            "--models", "provider/x,provider/y,provider/z", "--rotate", "--repetitions", "2",
            "--base-seed", "7000", "--scheduling-seed", "3", "--experiment-id", "exp-cli",
            "--umpire-model", "provider/umpire", "--player-reasoning-effort", "low",
            "--con-mode", "sampled_sealed", "--con-challengers-per-argument", "1",
        ],
    )

    assert result.exit_code == 0, result.output
    # TRD §30: the three counts are reported before execution.
    assert "assignment_count=6" in result.output
    assert "repetitions_per_assignment=2" in result.output
    assert "total_games=12" in result.output
    schedule = captured["schedule"]
    assert schedule.counts() == {
        "assignment_count": 6, "repetitions_per_assignment": 2, "total_games": 12,
    }
    assert schedule.experiment_id == "exp-cli"
    assert schedule.base_seed == 7000 and schedule.scheduling_seed == 3
    assert set(schedule.assignments[0].models) == {"arkania", "belvar", "cestia"}
    assert captured["output_dir"] == tmp_path / "out"
    assert captured["umpire_options"]["model"] == "provider/umpire"
    experiment = captured["experiment"]
    assert experiment["scenario_id"] == "deterministic-game"
    assert experiment["umpire"]["model"] == "provider/umpire"
    assert experiment["player"]["reasoning_effort"] == "low"
    assert experiment["game_loop"]["con_mode"] == "sampled_sealed"
    assert experiment["run_class"] == "playtest" and experiment["analysis_plan_sha256"] is None
    assert experiment["benchmark_mode"] == "model"  # T-057: declared on the command line
    assert "test-key" in captured["secret_values"]  # failure text is scrubbed with the real key


def test_t054_005_cli_refuses_unsupported_model_counts(monkeypatch, tmp_path) -> None:
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, ["--models", "provider/x,provider/y", "--rotate", "--repetitions", "1"],
    )
    assert result.exit_code == 2
    assert "one model per Actor" in result.output
    assert "schedule" not in captured

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, ["--models", "provider/x,provider/y", "--repetitions", "1"],
    )
    assert result.exit_code == 2
    assert "schedule" not in captured


@pytest.mark.slow
def test_t054_005_cli_runs_a_batch_end_to_end_without_manual_setup(
    monkeypatch, tmp_path, short_game
) -> None:
    """The T-054 done-when: only the provider boundary is faked; the batch machinery is real.

    Plays the short game; TEST-T060-001 walks the same CLI path through six turns.
    """
    import json

    from typer.testing import CliRunner

    import amaw_cli
    from agents.player import ReferencePlayer
    from agents.scripted import deterministic_umpire
    from tests.agent_fakes import default_policy, model_from_policy
    
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)

    def fake_player(tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
        return ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_policy(default_policy), config=config,
        )

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return deterministic_umpire()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.player.ReferencePlayer.for_openrouter", staticmethod(fake_player))

    result = CliRunner().invoke(
        amaw_cli.app,
        [
            "benchmark", str(short_game), "--output-dir", str(tmp_path / "out"),
            "--models", "fake/x", "--repetitions", "2", "--base-seed", "900", "--scheduling-seed", "1",
            "--experiment-id", "cli-e2e", "--activation-mode", "tool_loop",
            "--benchmark-mode", "agent",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout.strip() == str(tmp_path / "out")
    assert "total_games=2" in result.output
    batch = json.loads((tmp_path / "out" / "batch.json").read_text(encoding="utf-8"))
    assert batch["completed_games"] == 2 and batch["failed_games"] == 0
    assert batch["umpire"]["model"] and batch["player"]["prompt_version"]
    for record in batch["attempts"]:
        export = json.loads(
            (tmp_path / "out" / "games" / record["directory"] / "export.json").read_text()
        )
        assert export["game"]["status"] == "complete"
        assert export["game"]["seed"] == record["seed"] == 900 + record["repetition_index"]
        assert export["run_manifest"]["benchmark"]["experiment_id"] == "cli-e2e"
        assert export["run_manifest"]["player"]["assignments"] == {
            "arkania": "fake/x", "belvar": "fake/x", "cestia": "fake/x",
        }
    tables = read_tables(tmp_path / "out")
    assert len(tables["games"][1]) == 2 and len(tables["agents"][1]) == 6


# --- I-078-006 — the output directory is batch-owned: absent or empty, never inherited ---------


@pytest.mark.parametrize(
    "prepare",
    [
        pytest.param(lambda d: (d / "batch.json").write_text("{}"), id="batch.json"),
        pytest.param(lambda d: (d / "games" / "000-stale").mkdir(parents=True), id="stale-games"),
        pytest.param(lambda d: (d / "notes.txt").write_text("x"), id="stray-file"),
    ],
)
def test_i078_006_non_empty_output_directory_is_refused_before_any_game(tmp_path, prepare) -> None:
    from benchmark.runner import ScheduleError, run_batch

    output_dir = tmp_path / "batch"
    output_dir.mkdir()
    prepare(output_dir)
    before = sorted(p.relative_to(output_dir) for p in output_dir.rglob("*"))
    executed: list[int] = []

    def execute(attempt):
        executed.append(attempt.position)
        return _fake_export(attempt)

    with pytest.raises(ScheduleError) as excinfo:
        run_batch(
            _schedule(3, rotate=False, models=MODELS, repetitions=1, base_seed=10),
            execute=execute, output_dir=output_dir,
            experiment={"name": "smoke", "benchmark_mode": None, "scenario_id": "fixture"},
        )

    assert executed == []
    assert str(output_dir) in str(excinfo.value) and "empty" in str(excinfo.value)
    # nothing was written or removed
    assert sorted(p.relative_to(output_dir) for p in output_dir.rglob("*")) == before


def test_i078_006_absent_or_empty_output_directory_still_works(tmp_path) -> None:
    from benchmark.runner import run_batch

    schedule = _schedule(3, rotate=False, models=MODELS, repetitions=1, base_seed=10)
    experiment = {"name": "smoke", "benchmark_mode": None, "scenario_id": "fixture"}
    (tmp_path / "empty").mkdir()
    for output_dir in (tmp_path / "absent" / "nested", tmp_path / "empty"):
        result = run_batch(schedule, execute=_fake_export, output_dir=output_dir, experiment=experiment)
        assert result.counts()["completed_games"] == 1
        assert (output_dir / "batch.json").is_file() and (output_dir / "games.csv").is_file()


def test_i078_006_cli_refuses_a_non_empty_output_directory(monkeypatch, tmp_path) -> None:
    from typer.testing import CliRunner

    import amaw_cli
    from tests.api_helpers import FIXTURE_DIR

    output_dir = tmp_path / "out"
    output_dir.mkdir()
    (output_dir / "games.csv").write_text("game_id\n")
    called: list = []
    monkeypatch.setattr("benchmark.runner.run_batch", lambda *a, **k: called.append(1))
    result = CliRunner().invoke(
        amaw_cli.app,
        ["benchmark", str(FIXTURE_DIR), "--output-dir", str(output_dir), "--models", "p/x",
         "--benchmark-mode", "agent", "--repetitions", "1", "--base-seed", "1",
         "--scheduling-seed", "1"],
    )
    assert result.exit_code == 2, result.output
    assert "empty" in result.output and called == []
