"""T-055 + T-056 + T-057 — role rotation, fixed Umpire policy, benchmark modes (testing.md §22).

The experimental controls over the T-054 runner: "role assignment recorded" and "fixed Umpire
metadata constant". Test names carry the task IDs (T-055 in
``dev/WBS.md``).
"""

from __future__ import annotations

import itertools

import pytest

from tests.benchmark_helpers import offline_batch, read_tables


ACTORS = ["a", "b", "c"]
MODELS = ["provider/x", "provider/y", "provider/z"]


@pytest.fixture(autouse=True)
def _pinned_code_version(monkeypatch):
    """``code_version`` is a §5.1 held constant. The runner derives it from HEAD plus a digest of
    the dirty working tree, so an edit made while an offline batch runs would — correctly — trip
    the model-mode check. Pin it so these tests exercise the control, not the editor."""
    monkeypatch.setenv("AMAW_CODE_VERSION", "test-build")


def _roster(assignment, actors=ACTORS) -> tuple[str, ...]:
    return tuple(assignment.models[actor] for actor in actors)


# --- T-055-001 — the existing 3×3 permutation set is untouched; cyclic is a distinct policy ---


def test_t055_001_existing_permutations_unchanged_and_cyclic_is_distinct() -> None:
    from benchmark.runner import build_assignments, build_schedule, game_seed

    # T-054 pinned: --rotate is every permutation in itertools order with these indices.
    legacy = build_assignments(ACTORS, MODELS, rotate=True)
    assert [_roster(a) for a in legacy] == list(itertools.permutations(MODELS))
    assert [a.assignment_index for a in legacy] == list(range(6))
    assert [a.assignment_id for a in legacy] == [f"assignment-{i:02d}" for i in range(1, 7)]
    # The explicit spelling of the same policy is byte-identical to --rotate.
    assert build_assignments(ACTORS, MODELS, rotation="permutations") == legacy
    # And the derived seeds under one base seed do not move.
    schedule = build_schedule(
        actor_ids=ACTORS, models=MODELS, rotate=True, repetitions=2,
        base_seed=424000, scheduling_seed=7, experiment_id="exp-1",
    )
    assert schedule.rotation_policy == "permutations"
    assert {(a.assignment_id, a.repetition_index): a.seed for a in schedule.attempts} == {
        (f"assignment-{k + 1:02d}", r): game_seed(424000, k, r) for k in range(6) for r in range(2)
    }
    assert build_assignments(ACTORS, [MODELS[0]], rotate=False)[0].models == {
        actor: MODELS[0] for actor in ACTORS
    }

    # Explicit cyclic 3×3 is the methodology §6 minimum rotation, not the permutation set.
    cyclic = build_assignments(ACTORS, MODELS, rotation="cyclic")
    assert [_roster(a) for a in cyclic] == [
        ("provider/x", "provider/y", "provider/z"),
        ("provider/y", "provider/z", "provider/x"),
        ("provider/z", "provider/x", "provider/y"),
    ]
    assert [a.assignment_index for a in cyclic] == [0, 1, 2]
    assert [_roster(a) for a in cyclic] != [_roster(a) for a in legacy[:3]]


def test_t055_001_rotate_and_rotation_do_not_contradict() -> None:
    from benchmark.runner import ScheduleError, build_assignments

    with pytest.raises(ScheduleError):
        build_assignments(ACTORS, MODELS, rotate=True, rotation="cyclic")
    with pytest.raises(ScheduleError):
        build_assignments(ACTORS, MODELS, rotation="spiral")
    # No rotation at all is the recorded single-assignment policy.
    single = build_assignments(ACTORS, MODELS, rotate=False)
    assert len(single) == 1 and single[0].models == dict(zip(ACTORS, MODELS))


# --- T-055-002 — cyclic seat balance for unequal model/Actor counts --------------------------


@pytest.mark.parametrize(
    "actors, models",
    [
        (["a", "b", "c", "d"], ["m1", "m2"]),                 # M < N
        (["a", "b"], ["m1", "m2", "m3", "m4", "m5"]),         # M > N
        (["a", "b", "c"], ["m1", "m2", "m3", "m4"]),          # M > N, coprime
        (["a", "b", "c", "d", "e", "f"], ["m1", "m2", "m3"]), # M < N, N multiple of M
    ],
)
def test_t055_002_cyclic_policy_puts_every_model_in_every_seat_exactly_once(actors, models):
    from benchmark.runner import ROTATION_POLICIES, build_assignments, describe_rotation

    assignments = build_assignments(actors, models, rotation="cyclic")

    assert len(assignments) == len(models)
    assert [a.assignment_index for a in assignments] == list(range(len(models)))
    for k, assignment in enumerate(assignments):
        # Seat j → models[(k + j) % M]: the recorded formula, verbatim.
        assert assignment.models == {
            actor: models[(k + j) % len(models)] for j, actor in enumerate(actors)
        }
    for actor in actors:
        seat_history = [a.models[actor] for a in assignments]
        assert sorted(seat_history) == sorted(models), f"seat {actor} is not balanced"
    assert build_assignments(actors, models, rotation="cyclic") == assignments  # deterministic

    record = describe_rotation("cyclic", actor_ids=actors, models=models)
    assert record["policy"] == "cyclic"
    assert record["model_count"] == len(models) and record["seat_count"] == len(actors)
    assert record["assignment_count"] == len(models)
    assert "every listed model occupies every Actor Seat exactly once" in record["balance_guarantee"]
    assert "opponent" in record["not_guaranteed"]
    assert record["formula"] == "Actor[j] -> models[(k + j) % M] for k in 0..M-1"
    assert set(ROTATION_POLICIES) == {"single", "permutations", "cyclic"}


def test_t055_002_unequal_counts_still_refused_without_cyclic() -> None:
    from benchmark.runner import ScheduleError, build_assignments

    with pytest.raises(ScheduleError):  # D-056 stands for the legacy spellings
        build_assignments(["a", "b", "c"], ["m1", "m2"], rotate=True)
    with pytest.raises(ScheduleError):
        build_assignments(["a", "b", "c"], ["m1", "m2"], rotation="permutations")
    with pytest.raises(ScheduleError):
        build_assignments(["a", "b", "c"], ["m1", "m2"], rotation="single")
    with pytest.raises(ScheduleError):  # a cyclic roster needs at least two distinct positions
        build_assignments(["a", "b", "c"], [], rotation="cyclic")


def _batch_json(tmp_path):
    import json

    return json.loads((tmp_path / "batch" / "batch.json").read_text(encoding="utf-8"))


def _exports(tmp_path, result):
    import json

    for record in result.attempts:
        path = tmp_path / "batch" / "games" / record.directory / "export.json"
        yield record, (json.loads(path.read_text()) if path.is_file() else None)


# --- T-055-003 — an end-to-end cyclic batch records the actual assignment everywhere ---------


@pytest.mark.slow
def test_t055_003_cyclic_batch_records_scheduled_assignment_in_every_game(db_engine, tmp_path, short_game):
    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic",
    )

    assert outcome is None
    assert schedule.rotation_policy == "cyclic" and len(schedule.assignments) == 2
    assert result.counts()["completed_games"] == 2
    scheduled = {a.assignment_id: a for a in schedule.attempts}
    for record, export in _exports(tmp_path, result):
        benchmark = export["run_manifest"]["benchmark"]
        attempt = scheduled[record.assignment_id]
        assert benchmark["assignment"] == attempt.models == record.models
        assert export["run_manifest"]["player"]["assignments"] == attempt.models
        assert benchmark["rotation_policy"] == "cyclic"
        assert benchmark["mode"] == "batch"
    batch = _batch_json(tmp_path)
    assert batch["rotation_policy"] == "cyclic"
    assert batch["rotation"]["policy"] == "cyclic"
    assert batch["rotation"]["formula"] == "Actor[j] -> models[(k + j) % M] for k in 0..M-1"
    assert "every listed model occupies every Actor Seat exactly once" in (
        batch["rotation"]["balance_guarantee"]
    )
    assert [a["assignment_index"] for a in batch["schedule"]["assignments"]] == [0, 1]
    assert [a["models"] for a in batch["schedule"]["assignments"]] == [
        {"arkania": "fake/x", "belvar": "fake/y", "cestia": "fake/x"},
        {"arkania": "fake/y", "belvar": "fake/x", "cestia": "fake/y"},
    ]

    columns, rows = read_tables(tmp_path / "batch")["games"]
    # §25 prefix untouched; the new columns follow it.
    assert columns[:13] == [
        "game_id", "experiment_id", "scenario_id", "scenario_version", "assignment_id",
        "repetition_index", "seed", "scenario_content_hash", "resource_manifest_hash",
        "completed", "failure_reason", "started_at", "completed_at",
    ]
    assert columns.index("rotation_policy") > 13 and columns.index("roster") > 13
    by_assignment = {row["assignment_id"]: row for row in rows}
    assert by_assignment["assignment-01"]["rotation_policy"] == "cyclic"
    assert by_assignment["assignment-01"]["roster"] == "arkania=fake/x;belvar=fake/y;cestia=fake/x"
    assert by_assignment["assignment-02"]["roster"] == "arkania=fake/y;belvar=fake/x;cestia=fake/y"


# --- T-056-001 — the Umpire condition is sealed before the first game and matches every one ---


def _controls(benchmark_mode: str, scenario, *, umpire=None, models=("fake/x", "fake/y"), profile=None):
    """The Umpire and a ``controls(schedule, player_config, run_configuration)`` declarer.

    I-078: the batch declares its complete expected condition — the manifest configuration the
    runner will submit for every attempt, projected onto the condition paths — before the first
    game, so the declaration needs the schedule and the prototype player configuration.
    """
    from agents.autonomous import expected_manifest_configuration
    from agents.inference_profile import INFERENCE_PROFILE_UNIFORM
    from agents.scripted import deterministic_umpire
    from app.scenarios import load_scenario
    from benchmark.controls import BatchControls, ExpectedBatchCondition, seal_umpire_condition
    from benchmark.runner import batch_benchmark_section

    umpire = umpire or deterministic_umpire()
    umpire_condition = seal_umpire_condition(
        umpire, inference_profile=profile or INFERENCE_PROFILE_UNIFORM
    )

    def declare(schedule, player_config, run_configuration=None):
        return BatchControls.declare(
            benchmark_mode=benchmark_mode,
            roster=list(models),
            umpire_condition=umpire_condition,
            expected_condition=ExpectedBatchCondition.derive(
                scenario=load_scenario(scenario),
                configuration=expected_manifest_configuration(
                    player_config,
                    run_configuration=run_configuration,
                    benchmark=batch_benchmark_section(schedule, benchmark_mode=benchmark_mode),
                ),
                umpire_condition=umpire_condition,
            ),
        )

    return umpire, declare


@pytest.mark.slow
def test_t056_001_umpire_condition_is_sealed_and_matches_every_clean_game(db_engine, tmp_path, short_game):
    from benchmark.controls import canonical_sha256, observed_umpire_condition

    umpire, controls = _controls("model", short_game)
    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic",
        benchmark_mode="model", umpire=umpire, controls=controls,
    )

    assert outcome is None and result.counts()["completed_games"] == 2
    batch = _batch_json(tmp_path)
    sealed = batch["umpire_condition"]
    assert sealed["provenance"] == umpire.provenance_configuration()
    assert sealed["inference_profile_umpire"] == {}
    assert sealed["resume"]["model"] == sealed["provenance"]["model"]
    assert batch["umpire_condition_sha256"] == canonical_sha256(sealed)
    assert batch["integrity"] == {
        "ok": True, "umpire_fixed": True, "condition_fixed": True,
        "attempts_as_scheduled": True, "failures": [],
    }
    for record, export in _exports(tmp_path, result):
        manifest = export["run_manifest"]
        assert manifest["umpire"] == sealed["provenance"]
        assert observed_umpire_condition(manifest) == sealed
        assert record.umpire_condition_match is True and record.condition_differences == []
    assert all(a["umpire_condition_match"] is True for a in batch["attempts"])


def test_t056_001_umpire_condition_hash_is_canonical() -> None:
    import httpx

    from agents.model_client import OpenRouterClient
    from agents.inference_profile import INFERENCE_PROFILE_UNIFORM, InferenceProfile
    from agents.scripted import deterministic_umpire
    from agents.umpire import AIUmpire
    from benchmark.controls import canonical_sha256, seal_umpire_condition

    def ai_umpire(**kwargs):
        client = OpenRouterClient(
            api_key="sk-test", transport=httpx.MockTransport(lambda _r: httpx.Response(500))
        )
        return AIUmpire(client, model="test/umpire", **kwargs)

    # Two equivalent conditions hash identically, whatever the dict ordering of their content.
    one = seal_umpire_condition(deterministic_umpire(), inference_profile=INFERENCE_PROFILE_UNIFORM)
    two = seal_umpire_condition(deterministic_umpire(), inference_profile=INFERENCE_PROFILE_UNIFORM)
    assert canonical_sha256(one) == canonical_sha256(two)
    reordered = {key: one[key] for key in reversed(list(one))}
    assert canonical_sha256(reordered) == canonical_sha256(one)

    baseline = seal_umpire_condition(
        ai_umpire(temperature=0.0, reasoning_effort="low"),
        inference_profile=INFERENCE_PROFILE_UNIFORM,
    )
    assert baseline["provenance"]["adjudication_schema"]["name"] == "amaw_adjudication_proposal"
    assert len(baseline["provenance"]["adjudication_schema"]["sha256"]) == 64
    same = seal_umpire_condition(
        ai_umpire(temperature=0.0, reasoning_effort="low"),
        inference_profile=INFERENCE_PROFILE_UNIFORM,
    )
    assert canonical_sha256(same) == canonical_sha256(baseline)
    # Each meaningful field moves the hash on its own (agents.md §27, methodology §9).
    variants = [
        ai_umpire(temperature=0.5, reasoning_effort="low"),
        ai_umpire(temperature=0.0, reasoning_effort="high"),
        ai_umpire(temperature=0.0, reasoning_effort="low", prompt_version="umpire-v1.4"),
        ai_umpire(temperature=0.0, reasoning_effort="low", max_tokens=1234),
        ai_umpire(temperature=0.0, reasoning_effort="low", prompt="a different prompt"),
    ]
    hashes = {
        canonical_sha256(seal_umpire_condition(u, inference_profile=INFERENCE_PROFILE_UNIFORM))
        for u in variants
    }
    assert len(hashes) == len(variants) and canonical_sha256(baseline) not in hashes
    # The inference profile's umpire entry is part of the condition (D-055, methodology §9).
    profiled = seal_umpire_condition(
        ai_umpire(temperature=0.0, reasoning_effort="low"),
        inference_profile=InferenceProfile(name="p", umpire={"reasoning_effort": "high"}),
    )
    assert canonical_sha256(profiled) != canonical_sha256(baseline)


# --- T-056-002 — Umpire drift is an experiment-integrity failure, in either mode -------------


def _drifted_umpire():
    """A deterministic Umpire whose provenance says a different prompt version."""
    from agents.scripted import deterministic_umpire

    class DriftedUmpire(type(deterministic_umpire())):
        def provenance_configuration(self):
            configuration = super().provenance_configuration()
            return {**configuration, "prompt_version": "fake-umpire-v2"}

    drifted = deterministic_umpire()
    drifted.__class__ = DriftedUmpire
    return drifted


@pytest.mark.parametrize("mode", ["model", "agent"])
@pytest.mark.slow
def test_t056_002_umpire_drift_stops_the_batch_and_keeps_the_denominator(db_engine, tmp_path, mode, short_game):
    from benchmark.runner import BatchIntegrityError

    umpire, controls = _controls(mode, short_game)
    drifted = _drifted_umpire()
    executed: list[int] = []

    def umpire_for_attempt(attempt):
        executed.append(attempt.position)
        return drifted if attempt.position == 1 else None

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode=mode, umpire=umpire, controls=controls, umpire_for_attempt=umpire_for_attempt,
    )

    assert isinstance(outcome, BatchIntegrityError), outcome
    assert "umpire" in str(outcome).lower()
    assert executed == [0, 1]  # nothing after the drifted attempt was executed
    assert result is not None and len(result.attempts) == len(schedule.attempts) == 4
    assert result.counts() == {
        "total_games": 4, "completed_games": 2, "failed_games": 0, "not_run_games": 2,
    }
    by_position = {record.position: record for record in result.attempts}
    clean, drifted_record = by_position[0], by_position[1]
    assert clean.status == "completed" and clean.umpire_condition_match is True
    # The drifted game is retained with its evidence; it is not a §21 game failure.
    assert drifted_record.status == "completed" and drifted_record.completed is True
    assert drifted_record.failure_category is None
    assert drifted_record.umpire_condition_match is False
    assert drifted_record.condition_differences == [
        "provenance.prompt_version", "resume.model",
    ] or "provenance.prompt_version" in drifted_record.condition_differences
    for position in (2, 3):
        record = by_position[position]
        assert record.status == "not_run" and record.completed is False
        assert record.game_id is None and record.failure_category is None
        assert "integrity" in (record.failure_reason or "")
    exports = {record.position: export for record, export in _exports(tmp_path, result)}
    assert exports[1]["run_manifest"]["umpire"]["prompt_version"] == "fake-umpire-v2"
    assert exports[2]["game"] is None  # the stub, so tables stay a pure projection

    batch = _batch_json(tmp_path)
    assert batch["benchmark_mode"] == mode
    assert batch["integrity"]["ok"] is False and batch["integrity"]["umpire_fixed"] is False
    assert batch["integrity"]["condition_fixed"] is True
    assert batch["integrity"]["attempts_as_scheduled"] is True
    [failure] = batch["integrity"]["failures"]
    assert failure["kind"] == "umpire_condition"
    assert failure["game_id"] == drifted_record.game_id
    assert "provenance.prompt_version" in failure["differences"]
    assert batch["schedule"]["total_games"] == 4
    assert batch["executed_games"] == 2 and batch["not_run_games"] == 2
    assert [a["status"] for a in batch["attempts"]] == ["completed", "completed", "not_run", "not_run"]

    columns, rows = read_tables(tmp_path / "batch")["games"]
    assert len(rows) == 4
    assert [row["status"] for row in rows] == ["completed", "completed", "not_run", "not_run"]
    assert [row["umpire_condition_match"] for row in rows] == ["true", "false", "", ""]


# --- T-056-003 — a model benchmark seals and verifies the §5.1 held-constant set -------------


REQUIRED_HELD_CONSTANT_PATHS = {
    # scenario version, Actor definitions, turn order, action budget, resource set
    "scenario.id", "scenario.version", "scenario.content_sha256",
    "scenario.resource_manifest_sha256",
    # player prompt, framework, tool schemas, memory, context delivery, harness modes
    "player.prompt_version", "player.prompt_hash", "player.agent_framework",
    "player.agent_framework_version", "player.tool_schema_version", "player.memory_version",
    "player.context_mode", "player.activation_mode", "player.research_mode",
    "player.research_history_mode",
    # per-activation Message budget, guardrails, inference profile, player settings
    "player.max_messages_per_activation", "player.inference_profile",
    "resume.player.guardrails", "resume.player.temperature", "resume.player.max_tokens",
    "resume.player.reasoning_effort", "resume.inference_profile",
    # Umpire model / prompt / settings
    "umpire", "resume.umpire",
    # Diplomacy and Con conditions, coordinator, code, runtime (I-085), classification,
    # interpretation
    "game_loop", "coordinator", "code_version", "environment.sha256", "benchmark.run_class",
    "benchmark.analysis_plan", "benchmark.benchmark_mode", "benchmark.rotation_policy",
    "benchmark.experiment_id",
}


@pytest.mark.slow
def test_t056_003_model_batch_declares_and_verifies_held_constants(db_engine, tmp_path, short_game):
    from benchmark.controls import (
        HELD_CONSTANT_PATHS, canonical_sha256, held_constant_condition,
    )

    assert REQUIRED_HELD_CONSTANT_PATHS <= set(HELD_CONSTANT_PATHS)
    umpire, controls = _controls("model", short_game)
    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode="model", umpire=umpire, controls=controls,
    )

    assert outcome is None and result.counts()["completed_games"] == 4
    batch = _batch_json(tmp_path)
    # I-078: the §5.1 set is *declared* before the first game, never sealed from it.
    expected = batch["expected_condition"]
    assert set(expected) == set(HELD_CONSTANT_PATHS)
    assert batch["expected_condition_sha256"] == canonical_sha256(expected)
    assert "held_constant_sealed_from" not in batch and "held_constant_condition" not in batch
    assert expected["umpire"] == batch["umpire_condition"]["provenance"]
    assert expected["benchmark.benchmark_mode"] == "model"
    assert expected["benchmark.rotation_policy"] == "cyclic"
    assert expected["player.prompt_hash"] and expected["scenario.content_sha256"]
    assert batch["integrity"]["condition_fixed"] is True
    scheduled = {(a.assignment_id, a.repetition_index): a for a in schedule.attempts}
    for record, export in _exports(tmp_path, result):
        manifest = export["run_manifest"]
        # Every held-constant path equals the declaration; the assignment differs only as scheduled.
        assert held_constant_condition(manifest) == expected
        attempt = scheduled[(record.assignment_id, record.repetition_index)]
        assert manifest["player"]["assignments"] == attempt.models
        assert manifest["resume"]["player"]["assignments"] == attempt.models
        assert manifest["benchmark"]["assignment"] == attempt.models
        assert manifest["seed"] == attempt.seed
        assert record.condition_differences == []


@pytest.mark.slow
def test_t056_003_held_constant_drift_is_an_integrity_failure_in_model_mode(db_engine, tmp_path, short_game):
    import dataclasses

    from benchmark.runner import BatchIntegrityError

    umpire, controls = _controls("model", short_game)

    def config_for_attempt(attempt, config):
        if attempt.position == 1:  # a different research mode: an agent-harness change
            return dataclasses.replace(config, research_mode="batched-v1")
        return config

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode="model", umpire=umpire, controls=controls,
        config_for_attempt=config_for_attempt,
    )

    assert isinstance(outcome, BatchIntegrityError)
    assert result.counts() == {
        "total_games": 4, "completed_games": 2, "failed_games": 0, "not_run_games": 2,
    }
    drifted = result.attempts[1]
    assert drifted.umpire_condition_match is True  # the Umpire did not move
    assert "player.research_mode" in drifted.condition_differences
    batch = _batch_json(tmp_path)
    assert batch["integrity"]["umpire_fixed"] is True
    assert batch["integrity"]["condition_fixed"] is False
    [failure] = batch["integrity"]["failures"]
    assert failure["kind"] == "expected_condition"
    assert "player.research_mode" in failure["differences"]


@pytest.mark.slow
def test_t056_003_agent_mode_records_but_does_not_hold_player_constants(db_engine, tmp_path, short_game):
    import dataclasses

    umpire, controls = _controls("agent", short_game)

    def config_for_attempt(attempt, config):
        if attempt.position == 1:
            return dataclasses.replace(config, research_mode="batched-v1")
        return config

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic",
        benchmark_mode="agent", umpire=umpire, controls=controls,
        config_for_attempt=config_for_attempt,
    )

    # Agent mode: the Umpire is still sealed and the common condition held (I-078); the player
    # configuration is declared and recorded but may vary (§5.2, D-059).
    assert outcome is None and result.counts()["completed_games"] == 2
    batch = _batch_json(tmp_path)
    assert batch["integrity"]["ok"] is True
    assert batch["expected_condition"]["player.research_mode"] != "batched-v1"
    assert batch["benchmark_mode"] == "agent"
    assert all(a["umpire_condition_match"] is True for a in batch["attempts"])


def test_t056_001_per_attempt_umpire_override_is_refused_up_front() -> None:
    from benchmark.runner import ScheduleError, autonomous_executor

    with pytest.raises(ScheduleError, match="one Umpire"):
        autonomous_executor(
            object(), admin_token="t", scenario="x", player_factory=lambda *a: None,
            run_configuration={"benchmark": {"umpire": {"model": "other/umpire"}}},
        )
    with pytest.raises(ScheduleError, match="one Umpire"):
        autonomous_executor(
            object(), admin_token="t", scenario="x", player_factory=lambda *a: None,
            run_configuration={"benchmark": {"umpire_condition": {"model": "other/umpire"}}},
        )


# --- T-057-001 — the interpretation label lives everywhere results live ---------------------


@pytest.mark.parametrize("mode", ["model", "agent"])
@pytest.mark.slow
def test_t057_001_benchmark_mode_is_persisted_everywhere(db_engine, tmp_path, mode, short_game):
    from benchmark.controls import INTERPRETATIONS

    umpire, controls = _controls(mode, short_game)
    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic",
        benchmark_mode=mode, umpire=umpire, controls=controls,
    )

    assert outcome is None
    batch = _batch_json(tmp_path)
    assert batch["benchmark_mode"] == mode
    assert batch["roster"] == ["fake/x", "fake/y"]
    assert batch["interpretation"] == INTERPRETATIONS[mode]
    assert batch["interpretation"].startswith(f"{mode} benchmark:")
    if mode == "agent":
        assert "agent-configuration comparisons" in batch["interpretation"]
        assert "no other agent component is claimed to have varied" in batch["interpretation"]
    else:
        assert "held fixed and verified" in batch["interpretation"]
    for record, export in _exports(tmp_path, result):
        benchmark = export["run_manifest"]["benchmark"]
        assert benchmark["benchmark_mode"] == mode  # the interpretation …
        assert benchmark["mode"] == "batch"          # … is not the execution kind
        assert record.benchmark_mode == mode
    tables = read_tables(tmp_path / "batch")
    games_columns, games = tables["games"]
    agents_columns, agents = tables["agents"]
    assert games_columns.index("benchmark_mode") > 13
    assert agents_columns.index("benchmark_mode") > 21  # after the §25 agents prefix
    assert {row["benchmark_mode"] for row in games} == {mode}
    assert {row["benchmark_mode"] for row in agents} == {mode} and len(agents) == 6


# --- T-057-002 — CLI: the mode is explicit, rotation is named, integrity failures exit 1 -----


def _invoke_benchmark(monkeypatch, tmp_path, args: list[str], *, integrity_failure=False):
    from tests.api_helpers import FIXTURE_DIR
    from tests.benchmark_helpers import invoke_benchmark

    return invoke_benchmark(
        monkeypatch, tmp_path, FIXTURE_DIR, args, integrity_failure=integrity_failure
    )


BASE_ARGS = ["--repetitions", "1", "--base-seed", "10", "--scheduling-seed", "1"]


def test_t057_002_benchmark_mode_is_required_and_validated(monkeypatch, tmp_path) -> None:
    import inspect

    import typer

    import amaw_cli

    # The invocation below supplies the required scenario, models and output directory through
    # the helper and omits only this option. Assert the parser contract directly: Rich may
    # abbreviate the rendered option name at CI terminal widths (I-107).
    mode_parameter = inspect.signature(amaw_cli.benchmark).parameters["benchmark_mode"]
    assert isinstance(mode_parameter.default, typer.models.OptionInfo)
    assert mode_parameter.default.default is ...
    assert "--benchmark-mode" in mode_parameter.default.param_decls

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, ["--models", "provider/x", *BASE_ARGS],
    )
    assert result.exit_code == 2
    assert "schedule" not in captured

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, ["--models", "provider/x", "--benchmark-mode", "hybrid", *BASE_ARGS],
    )
    assert result.exit_code == 2
    assert "model" in result.output and "agent" in result.output
    assert "schedule" not in captured


def test_t057_002_model_mode_refuses_rotated_duplicate_identities(monkeypatch, tmp_path) -> None:
    for rotation_args in (["--rotate"], ["--rotation", "cyclic"], ["--rotation", "permutations"]):
        result, captured = _invoke_benchmark(
            monkeypatch, tmp_path,
            ["--models", "provider/x,provider/x,provider/y", "--benchmark-mode", "model",
             *rotation_args, *BASE_ARGS],
        )
        assert result.exit_code == 2, result.output
        assert "distinct" in result.output
        assert "schedule" not in captured
    # The same roster is legal as an agent batch (the roster is what it is, and says so) …
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "provider/x,provider/x,provider/y", "--benchmark-mode", "agent",
         "--rotation", "cyclic", *BASE_ARGS],
    )
    assert result.exit_code == 0, result.output
    assert captured["controls"].roster == ("provider/x", "provider/y")
    assert captured["experiment"]["models"] == ["provider/x", "provider/x", "provider/y"]


def test_t057_002_valid_model_batch_declares_its_controls(monkeypatch, tmp_path) -> None:
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "provider/x,provider/y", "--benchmark-mode", "model", "--rotation", "cyclic",
         "--umpire-model", "provider/umpire", "--umpire-reasoning-effort", "low",
         "--umpire-temperature", "0", *BASE_ARGS],
    )

    assert result.exit_code == 0, result.output
    assert "benchmark_mode=model" in result.output and "rotation_policy=cyclic" in result.output
    assert "assignment_count=2" in result.output
    schedule, experiment, controls = captured["schedule"], captured["experiment"], captured["controls"]
    assert schedule.rotation_policy == "cyclic" and len(schedule.assignments) == 2
    assert controls.benchmark_mode == "model" and controls.roster == ("provider/x", "provider/y")
    assert experiment["benchmark_mode"] == "model"
    assert experiment["umpire_condition"]["provenance"]["model"] == "provider/umpire"
    assert experiment["umpire_condition"]["provenance"]["settings"]["reasoning"] == {"effort": "low"}
    assert experiment["umpire_condition"]["resume"] == experiment["umpire"]
    assert experiment["umpire_condition"]["inference_profile_umpire"] == {}
    assert len(experiment["umpire_condition_sha256"]) == 64
    assert experiment["interpretation"].startswith("model benchmark:")
    assert experiment["rotate"] is False


def test_t057_002_legacy_rotate_and_single_assignment_still_work(monkeypatch, tmp_path) -> None:
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "provider/x,provider/y,provider/z", "--benchmark-mode", "model", "--rotate",
         *BASE_ARGS],
    )
    assert result.exit_code == 0, result.output
    assert captured["schedule"].rotation_policy == "permutations"
    assert len(captured["schedule"].assignments) == 6 and captured["experiment"]["rotate"] is True

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, ["--models", "provider/x", "--benchmark-mode", "agent", *BASE_ARGS],
    )
    assert result.exit_code == 0, result.output
    assert captured["schedule"].rotation_policy == "single"
    assert captured["controls"].roster == ("provider/x",)

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "provider/x,provider/y", "--benchmark-mode", "model", "--rotate",
         "--rotation", "cyclic", *BASE_ARGS],
    )
    assert result.exit_code == 2 and "schedule" not in captured


def test_t057_002_agent_mode_keeps_one_player_configuration(monkeypatch, tmp_path) -> None:
    import dataclasses

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "provider/x,provider/y", "--benchmark-mode", "agent", "--rotation", "cyclic",
         "--player-reasoning-effort", "low", *BASE_ARGS],
    )
    assert result.exit_code == 0, result.output
    experiment = captured["experiment"]
    assert experiment["benchmark_mode"] == "agent"
    assert experiment["interpretation"].startswith("agent benchmark:")
    # One batch-level player condition, recorded once — nothing per assignment.
    assert experiment["player"]["reasoning_effort"] == "low"
    assert not any(key.startswith("agent_config") for key in experiment)
    assert "player_configurations" not in experiment and "assignments" not in experiment["player"]
    # Building players for an attempt only swaps the model into that one configuration.
    attempt = captured["schedule"].attempts[0]
    class Tools:  # noqa: D401 - stand-in; the factory never touches it
        pass
    with pytest.raises(Exception):  # the fake player is not a LifecycleRunner-compatible player
        captured["execute"](attempt)
    configs = [config for _actor, config in captured.get("player_configs", [])]
    assert configs, "no player was constructed"
    models = {config.model for config in configs}
    assert models <= {"provider/x", "provider/y"}
    stripped = [dataclasses.replace(config, model="same") for config in configs]
    assert all(config == stripped[0] for config in stripped)


def test_t057_002_integrity_failure_exits_non_zero(monkeypatch, tmp_path) -> None:
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "provider/x,provider/y", "--benchmark-mode", "agent", "--rotation", "cyclic",
         *BASE_ARGS],
        integrity_failure=True,
    )
    assert result.exit_code == 1, result.output
    assert "integrity" in result.output and "not_run" in result.output
    assert "controls" in captured


# --- I-078 — the experiment declared before execution is the one that ran --------------------
#
# One pre-run expected-condition model (I-078): the
# batch declares its complete expected condition and one expected attempt per scheduled game
# before the first game exists, and every authoritative manifest — attempt 0 included — is
# verified against them. Under D-058 the first manifest *was* the condition.


def test_i078_001_expected_condition_is_declared_before_the_first_game_and_attempt_0_is_verified(
    db_engine, tmp_path, short_game
):
    import dataclasses

    from benchmark.controls import canonical_sha256
    from benchmark.runner import BatchIntegrityError

    umpire, controls = _controls("model", short_game)
    declared_before_first_game: dict = {}

    def umpire_for_attempt(attempt):
        # Probe: what batch.json says at the moment attempt 0 is about to run.
        if attempt.position == 0:
            declared_before_first_game.update(_batch_json(tmp_path))
        return None

    def config_for_attempt(attempt, config):
        if attempt.position == 0:  # the *first* game is the misconfigured one
            return dataclasses.replace(config, research_mode="batched-v1")
        return config

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode="model", umpire=umpire, controls=controls,
        umpire_for_attempt=umpire_for_attempt, config_for_attempt=config_for_attempt,
    )

    # The expectation existed before any game did, with its identity.
    expected = declared_before_first_game["expected_condition"]
    assert declared_before_first_game["expected_condition_sha256"] == canonical_sha256(expected)
    assert expected["player.research_mode"] != "batched-v1"
    assert expected["benchmark.experiment_id"] == "controls-1"
    assert expected["benchmark.benchmark_mode"] == "model"
    assert expected["scenario.content_sha256"] and expected["player.prompt_hash"]
    assert "held_constant_sealed_from" not in declared_before_first_game

    # Attempt 0 is verified against it — it does not get to define the condition.
    assert isinstance(outcome, BatchIntegrityError), outcome
    assert result.counts() == {
        "total_games": 4, "completed_games": 1, "failed_games": 0, "not_run_games": 3,
    }
    drifted = result.attempts[0]
    assert drifted.status == "completed" and drifted.umpire_condition_match is True
    assert "player.research_mode" in drifted.condition_differences
    batch = _batch_json(tmp_path)
    assert batch["expected_condition"] == expected
    assert batch["integrity"]["ok"] is False
    assert batch["integrity"]["umpire_fixed"] is True
    assert batch["integrity"]["condition_fixed"] is False
    [failure] = batch["integrity"]["failures"]
    assert failure["kind"] == "expected_condition"
    assert failure["game_id"] == drifted.game_id
    assert "player.research_mode" in failure["differences"]


def _swap_assignment(attempt):
    import dataclasses

    actors = list(attempt.models)
    swapped = dict(attempt.models)
    swapped[actors[0]], swapped[actors[1]] = attempt.models[actors[1]], attempt.models[actors[0]]
    return dataclasses.replace(attempt, models=swapped)


def _strip_manifest(attempt, export):
    return {**export, "run_manifest": None}


@pytest.mark.parametrize(
    ("defect", "attempt_for_execution", "export_for_attempt", "difference"),
    [
        (
            "seed",
            lambda attempt: __import__("dataclasses").replace(attempt, seed=attempt.seed + 1),
            None,
            "seed",
        ),
        ("assignment", _swap_assignment, None, "player.assignments"),
        ("missing_manifest", None, _strip_manifest, "run_manifest"),
    ],
)
@pytest.mark.slow
def test_i078_002_planned_and_observed_attempt_are_reconciled(
    db_engine, tmp_path, defect, attempt_for_execution, export_for_attempt, difference, short_game
):
    from benchmark.runner import BatchIntegrityError

    umpire, controls = _controls("model", short_game)

    def only_at_position_1(hook):
        if hook is None:
            return None

        def wrapped(attempt, *rest):
            if attempt.position == 1:
                return hook(attempt, *rest)
            return rest[0] if rest else attempt

        return wrapped

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode="model", umpire=umpire, controls=controls,
        attempt_for_execution=only_at_position_1(attempt_for_execution),
        export_for_attempt=only_at_position_1(export_for_attempt),
    )

    assert isinstance(outcome, BatchIntegrityError), outcome
    assert result.counts() == {
        "total_games": 4, "completed_games": 2, "failed_games": 0, "not_run_games": 2,
    }
    clean, wrong = result.attempts[0], result.attempts[1]
    assert clean.condition_differences == []
    # The record keeps the *scheduled* identity: the batch never adopts what actually ran.
    scheduled = schedule.attempts[1]
    assert wrong.seed == scheduled.seed and wrong.models == scheduled.models
    assert any(d == difference or d.startswith(difference + ".") for d in wrong.condition_differences), (
        wrong.condition_differences
    )
    batch = _batch_json(tmp_path)
    assert batch["integrity"]["ok"] is False
    assert batch["integrity"]["umpire_fixed"] is True
    assert batch["integrity"]["attempts_as_scheduled"] is False
    [failure] = batch["integrity"]["failures"]
    assert failure["kind"] == ("missing_manifest" if defect == "missing_manifest" else "expected_attempt")
    assert [a["status"] for a in batch["attempts"]] == ["completed", "completed", "not_run", "not_run"]
    if defect != "missing_manifest":
        # The authoritative export shows what really ran; the batch compared against it.
        export = {record.position: export for record, export in _exports(tmp_path, result)}[1]
        manifest = export["run_manifest"]
        if defect == "seed":
            assert manifest["seed"] == scheduled.seed + 1
        else:
            assert manifest["player"]["assignments"] != scheduled.models


@pytest.mark.slow
def test_i078_003_agent_mode_holds_the_common_condition(db_engine, tmp_path, monkeypatch, short_game):
    """§5.2 lets an agent benchmark vary the player; nothing lets it vary the code identity,
    the scenario, the Umpire, the game loop or the coordinator. Player drift is exercised by
    ``test_t056_003_agent_mode_records_but_does_not_hold_player_constants``."""
    from benchmark.runner import BatchIntegrityError

    umpire, controls = _controls("agent", short_game)

    def umpire_for_attempt(attempt):
        if attempt.position == 1:  # the code identity moves under the batch
            monkeypatch.setenv("AMAW_CODE_VERSION", "test-build-edited")
        return None

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode="agent", umpire=umpire, controls=controls,
        umpire_for_attempt=umpire_for_attempt,
    )

    assert isinstance(outcome, BatchIntegrityError), outcome
    assert result.counts() == {
        "total_games": 4, "completed_games": 2, "failed_games": 0, "not_run_games": 2,
    }
    drifted = result.attempts[1]
    assert drifted.umpire_condition_match is True
    assert drifted.condition_differences == ["code_version"]
    batch = _batch_json(tmp_path)
    assert batch["benchmark_mode"] == "agent"
    assert batch["expected_condition"]["code_version"] == "test-build"
    assert batch["integrity"]["condition_fixed"] is False
    [failure] = batch["integrity"]["failures"]
    assert failure["kind"] == "expected_condition" and failure["differences"] == ["code_version"]


# --- I-078-004 — the declared plan is reconciled with the built schedule before any game ------


def _plan(tmp_path, **design_overrides):
    """A valid plan whose design describes the reconciled batch unless overridden."""
    import copy

    import yaml

    from benchmark.analysis_plan import load_analysis_plan
    from tests.test_provenance_analysis_plan import VALID_PLAN

    content = copy.deepcopy(VALID_PLAN)
    content["treatment"] = {"factor": "player_model", "levels": ["fake/x", "fake/y"]}
    content["design"].update({"planned_attempts": 4, "repetitions_per_assignment": 2})
    content["design"].update(design_overrides.pop("design", {}))
    content.update(design_overrides)
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return load_analysis_plan(path)


def _schedule(models=("fake/x", "fake/y"), repetitions=2, rotation="cyclic"):
    from app.scenarios import load_scenario
    from benchmark.runner import build_schedule
    from tests.api_helpers import FIXTURE_DIR

    return build_schedule(
        actor_ids=list(load_scenario(FIXTURE_DIR).game.turn_order), models=list(models),
        rotation=rotation, repetitions=repetitions, base_seed=1, scheduling_seed=1,
        experiment_id="plan-1",
    )


def test_i078_004_matching_plan_is_reconciled_and_recorded(tmp_path) -> None:
    from benchmark.controls import reconcile_plan

    plan = _plan(tmp_path)
    record = reconcile_plan(plan, schedule=_schedule(), roster=["fake/x", "fake/y"])
    assert record == {
        "plan_id": plan.plan_id,
        "plan_sha256": plan.sha256,
        "planned_attempts": 4,
        "repetitions_per_assignment": 2,
        "batch_total_games": 4,
        "plan_scope": "experiment",
        "treatment_levels": ["fake/x", "fake/y"],
        "plan_seed_policy": "base_seed + assignment_index * 1000 + repetition_index",
        "plan_rotation_policy": "single model; no role rotation",
        "rotation_policy": "cyclic",
        "seed_policy": "base_seed + assignment_index * 1000 + repetition_index",
    }


def test_i078_004_one_cell_of_a_multi_level_plan_is_reconciled(tmp_path) -> None:
    """The shipped example's shape: a treatment varied *across* batches (one per level), each
    batch a cell with the plan's repetitions; the roster is then a fixed condition."""
    from benchmark.controls import reconcile_plan

    plan = _plan(
        tmp_path,
        treatment={"factor": "con_mode", "levels": ["open_cons", "sampled_sealed"]},
        design={"planned_attempts": 8},
    )
    record = reconcile_plan(plan, schedule=_schedule(), roster=["fake/x", "fake/y"])
    assert record["plan_scope"] == "cell" and record["planned_attempts"] == 8
    assert record["batch_total_games"] == 4


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"design": {"planned_attempts": 5}}, "planned_attempts"),
        ({"design": {"planned_attempts": 12}}, "planned_attempts"),
        ({"design": {"repetitions_per_assignment": 3, "planned_attempts": 4}}, "repetitions_per_assignment"),
        ({"treatment": {"factor": "player_model", "levels": ["fake/x", "fake/z"]}}, "treatment"),
        ({"treatment": {"factor": "player_model", "levels": ["fake/x"]}}, "treatment"),
    ],
)
def test_i078_004_disagreeing_plan_is_refused_by_name(tmp_path, overrides, named) -> None:
    from benchmark.controls import ControlError, reconcile_plan

    plan = _plan(tmp_path, **overrides)
    with pytest.raises(ControlError) as excinfo:
        reconcile_plan(plan, schedule=_schedule(), roster=["fake/x", "fake/y"])
    assert named in str(excinfo.value)


def test_i078_004_cli_refuses_a_disagreeing_plan_before_any_game(monkeypatch, tmp_path) -> None:
    plan_path = tmp_path / "plan.yaml"
    _plan(tmp_path, design={"planned_attempts": 5})  # writes plan.yaml
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "fake/x,fake/y", "--rotation", "cyclic", "--benchmark-mode", "model",
         "--repetitions", "2", "--base-seed", "10", "--scheduling-seed", "1",
         "--analysis-plan", str(plan_path), "--run-class", "analytical"],
    )
    assert result.exit_code == 2, result.output
    assert "planned_attempts" in result.output
    assert "execute" not in captured  # run_batch was never reached

    _plan(tmp_path)  # a matching plan
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        ["--models", "fake/x,fake/y", "--rotation", "cyclic", "--benchmark-mode", "model",
         "--repetitions", "2", "--base-seed", "10", "--scheduling-seed", "1",
         "--analysis-plan", str(plan_path), "--run-class", "analytical"],
    )
    assert result.exit_code == 0, result.output
    assert captured["experiment"]["analysis_plan"]["plan_scope"] == "experiment"
    assert captured["experiment"]["analysis_plan"]["planned_attempts"] == 4


# --- I-078-007g / I-078-008 — the runner opts in; tables separate planned from observed -------


@pytest.mark.slow
def test_i078_008_tables_carry_observed_seed_and_assignment_beside_the_planned(db_engine, tmp_path, short_game):
    umpire, controls = _controls("model", short_game)
    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic",
        benchmark_mode="model", umpire=umpire, controls=controls,
    )
    assert outcome is None and result.counts()["completed_games"] == 2

    # (g) every autonomous game opted into the server-enforced pre-commitment
    for _record, export in _exports(tmp_path, result):
        assert export["game"]["require_run_manifest"] is True
        assert export["run_manifest"]["created_at"] <= next(
            e["created_at"] for e in export["events"] if e["type"] == "game_started"
        )

    tables = read_tables(tmp_path / "batch")
    games_columns, games = tables["games"]
    agents_columns, agents = tables["agents"]
    # §25 prefix untouched; the observed columns follow it
    assert games_columns[:13] == [
        "game_id", "experiment_id", "scenario_id", "scenario_version", "assignment_id",
        "repetition_index", "seed", "scenario_content_hash", "resource_manifest_hash",
        "completed", "failure_reason", "started_at", "completed_at",
    ]
    assert {"seed_observed", "condition_match"} <= set(games_columns)
    assert "model_assigned" in agents_columns
    assert agents_columns.index("model_assigned") > agents_columns.index("model_returned")
    for row in games:
        assert row["seed_observed"] == row["seed"] != ""
        assert row["condition_match"] == "true"
    by_game = {r.game_id: r for r in result.attempts}
    for row in agents:
        assert row["model_assigned"] == row["model_requested"]
        assert row["model_assigned"] == by_game[row["game_id"]].models[row["actor_id"]]


def test_i078_008_observed_columns_show_a_mismatch_the_planned_columns_hide(db_engine, tmp_path, short_game):
    """The composition bug I-078 was filed for: planned columns alone would look clean."""
    import dataclasses

    umpire, controls = _controls("model", short_game)
    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic",
        benchmark_mode="model", umpire=umpire, controls=controls,
        attempt_for_execution=lambda a: (
            _swap_assignment(dataclasses.replace(a, seed=a.seed + 7)) if a.position == 0 else a
        ),
    )
    assert outcome is not None
    tables = read_tables(tmp_path / "batch")
    _, games = tables["games"]
    _, agents = tables["agents"]
    wrong = [r for r in games if r["attempt_position"] == "0"][0]
    assert wrong["seed"] == str(schedule.attempts[0].seed)
    assert wrong["seed_observed"] == str(schedule.attempts[0].seed + 7)
    assert wrong["condition_match"] == "false"
    wrong_agents = [r for r in agents if r["game_id"] == wrong["game_id"]]
    assert any(r["model_assigned"] != r["model_requested"] for r in wrong_agents)
