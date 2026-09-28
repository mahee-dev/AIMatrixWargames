"""I-114 — benchmark batches seal and verify the consequence resolver (D-092).

T-074 made ``amaw benchmark`` refuse ``bounded_effects_v1`` because a batch sealed and verified
one Umpire condition per attempt and had no equivalent for the resolver. D-092 seals the resolver
beside the Umpire, verifies it on every attempt in both benchmark modes, and leaves a ``none``
batch exactly as it was. See I-114 in ``dev/BACKLOG_RESOLVED.md``.
"""

from __future__ import annotations

import csv
import json
import shutil

import pytest

from agents.consequence import FakeConsequenceResolver
from app.consequences import ConsequenceProposal, ProposedConsequence
from app.game_loop import GameLoopCondition

BOUNDED = "bounded_effects_v1"


@pytest.fixture(autouse=True)
def _pinned_code_version(monkeypatch):
    """``code_version`` is a held constant; pin it so an editor cannot trip the check."""
    monkeypatch.setenv("AMAW_CODE_VERSION", "test-build")


def _aggregate(context) -> ConsequenceProposal:  # noqa: ANN001
    """Cite the turn's first adjudicated outcome, so the batch really commits Effects."""
    if not context.outcomes:
        return ConsequenceProposal(effects=[])
    return ConsequenceProposal(effects=[ProposedConsequence(
        description="Markets react to the turn.",
        rationale="The first outcome of the turn.",
        evidence=[{"kind": "event", "id": context.outcomes[0]["event_id"]}],
    )])


def _resolver() -> FakeConsequenceResolver:
    return FakeConsequenceResolver(responder=_aggregate)


def _drifted_resolver() -> FakeConsequenceResolver:
    class Drifted(FakeConsequenceResolver):
        def provenance_configuration(self):
            return {**super().provenance_configuration(), "prompt_version": "fake-consequence-v2"}

    return Drifted(responder=_aggregate)


RECIPE = {
    "model": "fake-consequence-v1", "temperature": None, "max_tokens": None, "timeout": 60.0,
    "reasoning_effort": None, "prompt_version": "fake-consequence-v1",
}


def _offline_batch(
    db_engine,
    tmp_path,
    scenario,
    *,
    benchmark_mode: str,
    consequence_mode: str,
    repetitions: int = 1,
    resolver_for_attempt=None,
):
    """A real two-assignment cyclic batch through the shared harness; only the models are faked.

    Composed as ``amaw benchmark`` composes it: one resume recipe carrying the resolver section,
    the controls declared (Umpire, consequence resolver, expected condition) before game 1, and
    the resolver injected into the application. ``resolver_for_attempt(attempt)`` may swap it.
    Returns ``(schedule, result, outcome, controls)``.
    """
    from agents.autonomous import build_resume_recipe, expected_manifest_configuration
    from agents.inference_profile import INFERENCE_PROFILE_UNIFORM
    from agents.player import PlayerConfig
    from agents.scripted import deterministic_umpire
    from app.scenarios import load_scenario
    from benchmark.controls import (
        BatchControls, ExpectedBatchCondition, seal_consequence_condition, seal_umpire_condition,
    )
    from benchmark.runner import batch_benchmark_section
    from tests.benchmark_helpers import offline_batch

    bounded = consequence_mode != "none"
    condition = GameLoopCondition.from_mapping({"consequence_mode": consequence_mode})
    prototype = PlayerConfig(model="fake/x", activation_mode="tool_loop")
    recipe = build_resume_recipe(
        prototype, assignments={}, max_cycles=1000, cons_concurrency=4,
        diplomacy_concurrency=4, condition=condition,
        consequence=dict(RECIPE) if bounded else None,
    )
    run_configuration = {"resume": recipe}
    umpire = deterministic_umpire()
    resolver = _resolver() if bounded else None
    umpire_condition = seal_umpire_condition(umpire, inference_profile=INFERENCE_PROFILE_UNIFORM)
    consequence_condition = (
        seal_consequence_condition(resolver, resume_consequence=recipe["consequence"])
        if bounded else None
    )
    declared: dict = {}

    def declare(schedule, _prototype, _run_configuration):  # noqa: ANN001
        declared["controls"] = BatchControls.declare(
            benchmark_mode=benchmark_mode,
            roster=["fake/x", "fake/y"],
            umpire_condition=umpire_condition,
            consequence_condition=consequence_condition,
            expected_condition=ExpectedBatchCondition.derive(
                scenario=load_scenario(scenario),
                configuration=expected_manifest_configuration(
                    prototype,
                    run_configuration=run_configuration,
                    benchmark=batch_benchmark_section(schedule, benchmark_mode=benchmark_mode),
                    cons_concurrency=4, diplomacy_concurrency=4, condition=condition,
                ),
                umpire_condition=umpire_condition,
            ),
        )
        return declared["controls"]

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=scenario, models=["fake/x", "fake/y"], rotation="cyclic",
        repetitions=repetitions, base_seed=700000, scheduling_seed=3, experiment_id="i114",
        experiment={"name": "i114", "scenario_id": "fixture"}, benchmark_mode=benchmark_mode,
        umpire=umpire, consequence_resolver=resolver, controls=declare,
        run_configuration=run_configuration,
        executor_options={
            "cons_concurrency": 4, "diplomacy_concurrency": 4, **condition.as_dict(),
        },
        resolver_for_attempt=resolver_for_attempt,
    )
    return schedule, result, outcome, declared["controls"]


def _batch_json(tmp_path) -> dict:
    return json.loads((tmp_path / "batch" / "batch.json").read_text(encoding="utf-8"))


def _exports(tmp_path, result):
    for record in result.attempts:
        path = tmp_path / "batch" / "games" / record.directory / "export.json"
        yield record, (json.loads(path.read_text()) if path.is_file() else None)


def _games_csv(tmp_path):
    with (tmp_path / "batch" / "games.csv").open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        return reader.fieldnames, list(reader)


# --- TEST-I114-001: a bounded batch seals, verifies and names the resolver --------------------


@pytest.mark.parametrize("mode", ["model", "agent"])
@pytest.mark.slow
def test_i114_001_bounded_batch_seals_and_verifies_the_resolver(db_engine, tmp_path, mode, short_game):
    from benchmark.controls import canonical_sha256, observed_consequence_condition

    schedule, result, outcome, _ = _offline_batch(
        db_engine, tmp_path, short_game, benchmark_mode=mode, consequence_mode=BOUNDED,
    )

    assert outcome is None, outcome
    assert result.counts()["completed_games"] == 2
    batch = _batch_json(tmp_path)
    sealed = batch["consequence_condition"]
    assert sealed == {
        "provenance": _resolver().provenance_configuration(), "resume": RECIPE,
    }
    assert batch["consequence_condition_sha256"] == canonical_sha256(sealed)
    assert batch["integrity"] == {
        "ok": True, "umpire_fixed": True, "consequence_fixed": True, "condition_fixed": True,
        "attempts_as_scheduled": True, "failures": [],
    }
    for record, export in _exports(tmp_path, result):
        manifest = export["run_manifest"]
        assert observed_consequence_condition(manifest) == sealed
        assert manifest["game_loop"]["consequence_mode"] == BOUNDED
        assert export["game"]["consequence_mode"] == BOUNDED
        # The pass really ran: every completed turn has its record.
        assert export["consequence_records"]
        assert record.condition_differences == []

    columns, rows = _games_csv(tmp_path)
    assert columns.index("consequence_mode") == columns.index("con_mode") + 1
    assert columns[columns.index("consequence_mode"):columns.index("consequence_mode") + 3] == [
        "consequence_mode", "consequence_model", "consequence_prompt_version",
    ]
    assert {(row["consequence_mode"], row["consequence_model"],
             row["consequence_prompt_version"]) for row in rows} == {
        (BOUNDED, "fake-consequence-v1", "fake-consequence-v1"),
    }


# --- TEST-I114-002: a swapped resolver is an integrity failure in both modes -----------------


@pytest.mark.parametrize("mode", ["model", "agent"])
@pytest.mark.slow
def test_i114_002_resolver_drift_stops_the_batch_and_keeps_the_denominator(
    db_engine, tmp_path, mode, short_game
):
    from benchmark.runner import BatchIntegrityError

    executed: list[int] = []

    def resolver_for_attempt(attempt):  # noqa: ANN001
        executed.append(attempt.position)
        return _drifted_resolver() if attempt.position == 1 else None

    schedule, result, outcome, _ = _offline_batch(
        db_engine, tmp_path, short_game, benchmark_mode=mode, consequence_mode=BOUNDED, repetitions=2,
        resolver_for_attempt=resolver_for_attempt,
    )

    assert isinstance(outcome, BatchIntegrityError), outcome
    assert "consequence_condition" in str(outcome)
    assert executed == [0, 1]
    assert result.counts() == {
        "total_games": 4, "completed_games": 2, "failed_games": 0, "not_run_games": 2,
    }
    by_position = {record.position: record for record in result.attempts}
    assert by_position[0].condition_differences == []
    # The drifted game is retained evidence, not a §21 failure; its Umpire still matched.
    assert by_position[1].completed is True and by_position[1].failure_category is None
    assert by_position[1].umpire_condition_match is True
    assert "provenance.prompt_version" in by_position[1].condition_differences
    assert [by_position[p].status for p in (2, 3)] == ["not_run", "not_run"]

    batch = _batch_json(tmp_path)
    integrity = batch["integrity"]
    assert integrity["ok"] is False and integrity["consequence_fixed"] is False
    assert integrity["umpire_fixed"] is True and integrity["condition_fixed"] is True
    [failure] = integrity["failures"]
    assert failure["kind"] == "consequence_condition"
    assert failure["game_id"] == by_position[1].game_id
    assert failure["differences"] == ["provenance.prompt_version"]


# --- TEST-I114-003: a none batch is exactly what it was --------------------------------------


@pytest.mark.slow
def test_i114_003_none_batch_record_is_unchanged(db_engine, tmp_path, short_game):
    from benchmark.controls import HELD_CONSTANT_PATHS

    schedule, result, outcome, controls = _offline_batch(
        db_engine, tmp_path, short_game, benchmark_mode="model", consequence_mode="none",
    )

    assert outcome is None, outcome
    batch = _batch_json(tmp_path)
    assert not any(key.startswith("consequence") for key in batch)
    assert batch["integrity"] == {
        "ok": True, "umpire_fixed": True, "condition_fixed": True,
        "attempts_as_scheduled": True, "failures": [],
    }
    assert set(batch["expected_condition"]) == set(HELD_CONSTANT_PATHS)
    assert "consequence_condition" not in controls.record()
    for _record, export in _exports(tmp_path, result):
        manifest = export["run_manifest"]
        assert "consequence" not in manifest and "consequence" not in manifest["resume"]
        assert "consequence_mode" not in manifest["game_loop"]

    _, rows = _games_csv(tmp_path)
    assert {(row["consequence_mode"], row["consequence_model"],
             row["consequence_prompt_version"]) for row in rows} == {("none", "", "")}


def test_i114_003_none_batch_refuses_a_manifest_that_carries_a_resolver() -> None:
    from agents.inference_profile import INFERENCE_PROFILE_UNIFORM
    from agents.scripted import deterministic_umpire
    from benchmark.controls import BatchControls, ExpectedBatchCondition, seal_umpire_condition

    umpire_condition = seal_umpire_condition(
        deterministic_umpire(), inference_profile=INFERENCE_PROFILE_UNIFORM
    )
    controls = BatchControls.declare(
        benchmark_mode="agent", roster=["m"], umpire_condition=umpire_condition,
        expected_condition=ExpectedBatchCondition(
            condition={"benchmark.benchmark_mode": "agent"}, sha256="x",
        ),
    )
    assert controls.consequence_condition is None
    assert controls.verify_consequence({"resume": {}}) == []
    assert controls.verify_consequence(
        {"consequence": {"model": "m"}, "resume": {"consequence": {"model": "m"}}}
    ) == ["provenance", "resume"]


def test_i114_003_per_attempt_consequence_override_is_refused() -> None:
    from benchmark.runner import ScheduleError, autonomous_executor

    for key in ("consequence", "consequence_condition"):
        with pytest.raises(ScheduleError, match="one consequence resolver"):
            autonomous_executor(
                object(), admin_token="t", scenario="x", player_factory=lambda *a: None,
                run_configuration={"benchmark": {key: {"model": "other/resolver"}}},
            )


# --- TEST-I114-004: the CLI resolves, refuses and declares the condition --------------------


def _invoke_benchmark(monkeypatch, tmp_path, scenario, *args: str, env: dict | None = None):
    from tests.benchmark_helpers import invoke_benchmark

    return invoke_benchmark(
        monkeypatch, tmp_path, scenario,
        ["--models", "p/x,p/y", "--rotation", "cyclic", "--benchmark-mode", "model",
         "--repetitions", "1", "--base-seed", "10", "--scheduling-seed", "1", *args],
        output_dir=tmp_path / f"out-{len(list(tmp_path.glob('out-*')))}", env=env,
    )


def _declaring_scenario(tmp_path):
    from tests.api_helpers import FIXTURE_DIR

    declared = tmp_path / "declared"
    shutil.copytree(FIXTURE_DIR, declared)
    manifest = declared / "scenario.yaml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            "  max_cons_per_actor: 3\n", f"  max_cons_per_actor: 3\n  consequence_mode: {BOUNDED}\n"
        ),
        encoding="utf-8",
    )
    return declared


def test_i114_004_cli_defaults_a_resolverless_condition_to_the_umpire_model(monkeypatch, tmp_path):
    # D-115 replaced the refusal: from every source of the mode, an unset resolver model is the
    # Umpire model, sealed like an explicit one.
    from tests.api_helpers import FIXTURE_DIR

    umpire = ("--umpire-model", "u/m")
    for scenario, args, env in [
        (FIXTURE_DIR, ("--consequence-mode", BOUNDED), None),
        (FIXTURE_DIR, (), {"AMAW_CONSEQUENCE_MODE": BOUNDED}),
        (_declaring_scenario(tmp_path), (), None),
    ]:
        result, captured = _invoke_benchmark(monkeypatch, tmp_path, scenario, *args, *umpire, env=env)
        assert result.exit_code == 0, result.output
        assert captured["experiment"]["consequence"]["model"] == "u/m"
        assert captured["controls"].consequence_condition["provenance"]["model"] == "u/m"

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, FIXTURE_DIR, "--consequence-model", "c/m"
    )
    assert result.exit_code == 2 and "requires consequence_mode" in result.output


def test_i114_004_cli_declares_and_records_the_consequence_condition(monkeypatch, tmp_path):
    from benchmark.controls import canonical_sha256
    from tests.api_helpers import FIXTURE_DIR

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, FIXTURE_DIR,
        "--consequence-mode", BOUNDED, "--consequence-model", "c/m",
    )
    assert result.exit_code == 0, result.output
    experiment, controls = captured["experiment"], captured["controls"]
    recipe = experiment["consequence"]
    assert recipe["model"] == "c/m" and recipe["prompt_version"] == "consequence-v1.1"
    assert experiment["game_loop"]["consequence_mode"] == BOUNDED
    sealed = experiment["consequence_condition"]
    assert sealed["resume"] == recipe
    assert sealed["provenance"]["model"] == "c/m"
    assert experiment["consequence_condition_sha256"] == canonical_sha256(sealed)
    assert controls.consequence_condition == sealed
    # The resume recipe every game seals carries the same section and the mode.
    assert controls.expected_condition.condition["resume.coordinator"]["consequence_mode"] == BOUNDED
    assert captured["app_options"]["consequence_resolver"] is not None
    assert captured["resolver_options"]["model"] == "c/m"

    # A scenario that declares the mode takes the resolver model from the environment …
    declared = _declaring_scenario(tmp_path)
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, declared, env={"AMAW_CONSEQUENCE_MODEL": "env/c"}
    )
    assert result.exit_code == 0, result.output
    assert captured["experiment"]["consequence"]["model"] == "env/c"
    assert captured["controls"].consequence_condition["provenance"]["model"] == "env/c"
    # … and --consequence-mode none overrides it, declaring no resolver at all.
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, declared, "--consequence-mode", "none"
    )
    assert result.exit_code == 0, result.output
    assert captured["controls"].consequence_condition is None


def test_i114_004_cli_none_batch_declares_no_consequence_condition(monkeypatch, tmp_path):
    from tests.api_helpers import FIXTURE_DIR

    result, captured = _invoke_benchmark(monkeypatch, tmp_path, FIXTURE_DIR)
    assert result.exit_code == 0, result.output
    experiment = captured["experiment"]
    assert not any(key.startswith("consequence") for key in experiment)
    assert "consequence_mode" not in experiment["game_loop"]
    assert captured["controls"].consequence_condition is None
    assert captured["app_options"].get("consequence_resolver") is None
