"""I-047 predeclared analysis and assurance plan for analytical runs (testing.md §28)."""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
import yaml


VALID_PLAN: dict = {
    "schema": "analysis-plan-v1",
    "plan_id": "swedish-con-opposition-2026-09",
    "title": "Sampled-sealed versus open Con opposition",
    "research_question": "Does sealed K=3 opposition change Argument success rates?",
    "hypotheses": [
        {"id": "H1", "statement": "Argument success rate does not differ between conditions."},
    ],
    "experimental_unit": "game",
    "treatment": {"factor": "con_mode", "levels": ["open_cons", "sampled_sealed"]},
    "fixed_conditions": [
        "scenario swedish-government v6",
        "umpire model, prompt and settings",
        "player prompt version",
        "diplomacy policy open_diplomacy",
    ],
    "outcome_measures": [
        {
            "id": "argument_success_rate",
            "definition": "Successful Arguments divided by resolved Arguments per game.",
            "source": "run_summary.arguments.success_rate",
            "role": "primary",
        },
        {
            "id": "cons_per_argument",
            "definition": "Committed Cons divided by Arguments per game.",
            "source": "run_summary.con_condition",
            "role": "secondary",
        },
    ],
    "estimand": (
        "Difference in mean per-game Argument success rate, sampled_sealed minus open_cons, "
        "under the fixed conditions above."
    ),
    "design": {
        "planned_attempts": 10,
        "repetitions_per_assignment": 5,
        "seed_policy": "base_seed + assignment_index * 1000 + repetition_index",
        "rotation_policy": "single model; no role rotation",
    },
    "failure_rules": {
        "failed_games": "Retained in the denominator and reported by failure category.",
        "exclusion_criteria": [],
        "missing_data": "A game without a resolved Argument contributes no success-rate value.",
    },
    "analysis_method": "Descriptive per-condition means with bootstrap confidence intervals.",
    "assurance_checks": [
        {
            "id": "umpire_sensitivity",
            "description": "Repeat two games per condition under a second fixed Umpire.",
        },
    ],
    "authored_at": "2026-09-16",
}


def _write_plan(path: Path, plan: dict, *, as_json: bool = False) -> Path:
    if as_json:
        path.write_text(json.dumps(plan, indent=2), encoding="utf-8")
    else:
        path.write_text(yaml.safe_dump(plan, sort_keys=False), encoding="utf-8")
    return path


def test_i047_001_valid_plan_loads_with_stable_hash(tmp_path: Path) -> None:
    from benchmark.analysis_plan import load_analysis_plan

    yaml_path = _write_plan(tmp_path / "plan.yaml", VALID_PLAN)
    reordered = dict(reversed(list(VALID_PLAN.items())))
    json_path = _write_plan(tmp_path / "plan.json", reordered, as_json=True)

    first = load_analysis_plan(yaml_path)
    second = load_analysis_plan(json_path)

    assert first.plan_id == "swedish-con-opposition-2026-09"
    assert first.content == VALID_PLAN
    assert len(first.sha256) == 64
    assert first.sha256 == second.sha256


def _without(plan: dict, dotted: str, *, empty: bool = False) -> dict:
    """Drop (or blank) a nested declaration so the validator must name it."""
    mutated = copy.deepcopy(plan)
    *parents, leaf = dotted.split(".")
    target = mutated
    for key in parents:
        target = target[key]
    if empty:
        target[leaf] = [] if isinstance(target[leaf], list) else ""
    else:
        del target[leaf]
    return mutated


@pytest.mark.parametrize(
    "dotted, empty",
    [
        ("research_question", False),
        ("research_question", True),
        ("hypotheses", True),
        ("experimental_unit", False),
        ("treatment", False),
        ("treatment.levels", True),
        ("fixed_conditions", True),
        ("outcome_measures", True),
        ("estimand", False),
        ("design", False),
        ("design.planned_attempts", False),
        ("design.seed_policy", True),
        ("design.rotation_policy", False),
        ("failure_rules", False),
        ("failure_rules.exclusion_criteria", False),
        ("failure_rules.missing_data", False),
        ("analysis_method", True),
        ("assurance_checks", True),
        ("authored_at", False),
    ],
)
def test_i047_002_missing_declaration_is_rejected_by_name(
    tmp_path: Path, dotted: str, empty: bool
) -> None:
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan

    path = _write_plan(tmp_path / "plan.yaml", _without(VALID_PLAN, dotted, empty=empty))

    with pytest.raises(AnalysisPlanError) as excinfo:
        load_analysis_plan(path)
    assert dotted.split(".")[-1] in str(excinfo.value)


def test_i047_002_primary_outcome_and_hypothesis_shape_are_required(tmp_path: Path) -> None:
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan

    secondary_only = copy.deepcopy(VALID_PLAN)
    for measure in secondary_only["outcome_measures"]:
        measure["role"] = "secondary"
    with pytest.raises(AnalysisPlanError, match="primary"):
        load_analysis_plan(_write_plan(tmp_path / "secondary.yaml", secondary_only))

    bare_hypothesis = copy.deepcopy(VALID_PLAN)
    bare_hypothesis["hypotheses"] = ["success does not differ"]
    with pytest.raises(AnalysisPlanError, match="hypotheses"):
        load_analysis_plan(_write_plan(tmp_path / "hypothesis.yaml", bare_hypothesis))

    wrong_schema = copy.deepcopy(VALID_PLAN)
    wrong_schema["schema"] = "analysis-plan-v0"
    with pytest.raises(AnalysisPlanError, match="schema"):
        load_analysis_plan(_write_plan(tmp_path / "schema.yaml", wrong_schema))


def test_i047_003_unknown_and_credential_keys_rejected_and_measures_classified(
    tmp_path: Path,
) -> None:
    from benchmark.analysis_plan import (
        AnalysisPlanError,
        classify_measure,
        load_analysis_plan,
    )

    unknown = copy.deepcopy(VALID_PLAN)
    unknown["aggression_score"] = 0.81
    with pytest.raises(AnalysisPlanError, match="aggression_score"):
        load_analysis_plan(_write_plan(tmp_path / "unknown.yaml", unknown))

    credential = copy.deepcopy(VALID_PLAN)
    credential["notes"] = {"api_key": "sk-nope"}
    with pytest.raises(AnalysisPlanError, match="api_key"):
        load_analysis_plan(_write_plan(tmp_path / "credential.yaml", credential))

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    assert classify_measure(plan, "argument_success_rate") == "outcome"
    assert classify_measure(plan, "cons_per_argument") == "outcome"
    # Recorded because it is available, never declared: descriptive telemetry, not a finding.
    assert classify_measure(plan, "input_tokens") == "telemetry"
    assert plan.outcome_ids() == ("argument_success_rate", "cons_per_argument")
    assert plan.primary_outcome_ids() == ("argument_success_rate",)


# --- TEST-I047-004/005/007 — sealing into the immutable manifest; resume reconstruction --------


def _runner(engine, plan=None, **kwargs):
    from fastapi.testclient import TestClient

    from agents.autonomous import AutonomousGameRunner
    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from agents.scripted import deterministic_umpire
    from agents.tools import PlayerTools
    from app.api import create_app
    from app.config import Settings
    from tests.agent_fakes import default_policy, model_from_policy
    from tests.api_helpers import FIXTURE_DIR

    def player_factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        player = ReferencePlayer(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model_from_policy(default_policy),
            config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
        )
        return LifecycleRunner(player)

    settings = Settings(AMAW_ADMIN_TOKEN="admin-secret-token", _env_file=None)
    api = TestClient(
        create_app(engine=engine, settings=settings, umpire=deterministic_umpire()),
        raise_server_exceptions=False,
    )
    runner = AutonomousGameRunner(
        api, admin_token="admin-secret-token", scenario=kwargs.pop("scenario", FIXTURE_DIR),
        player_factory=player_factory, seed=kwargs.pop("seed", 20260916),
        analysis_plan=plan, **kwargs,
    )
    return api, runner


@pytest.mark.slow
def test_i047_004_run_without_plan_is_a_playtest(db_engine, short_game) -> None:
    _, runner = _runner(db_engine, scenario=short_game)
    export = runner.run().export

    benchmark = export["run_manifest"]["benchmark"]
    assert benchmark["run_class"] == "playtest"
    assert benchmark["analysis_plan"] is None
    assert benchmark["mode"] == "single"
    assert benchmark["assignment_id"] is None and benchmark["repetition_index"] is None


def test_i047_005_run_with_plan_seals_it_before_execution(
    db_engine, tmp_path: Path, short_game: Path
) -> None:
    from benchmark.analysis_plan import load_analysis_plan

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    _, runner = _runner(db_engine, plan=plan, scenario=short_game)
    export = runner.run().export

    benchmark = export["run_manifest"]["benchmark"]
    assert benchmark["run_class"] == "analytical"
    assert benchmark["analysis_plan"] == {
        "schema": "analysis-plan-v1",
        "plan_id": "swedish-con-opposition-2026-09",
        "sha256": plan.sha256,
        "content": VALID_PLAN,
    }
    # Predeclaration evidence: the immutable manifest exists before any activation was recorded.
    assert export["agent_runs"]
    assert all(
        export["run_manifest"]["created_at"] <= run["created_at"] for run in export["agent_runs"]
    )
    assert len(export["run_manifest"]["manifest_sha256"]) == 64


@pytest.mark.slow
def test_i047_007_resume_reconstructs_sealed_plan_and_refuses_mismatch(
    db_engine, tmp_path: Path, short_game: Path
) -> None:
    from agents.autonomous import AutonomousGameError
    from benchmark.analysis_plan import load_analysis_plan
    from tests.api_helpers import bearer

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    other = copy.deepcopy(VALID_PLAN)
    other["plan_id"] = "another-plan"
    other_plan = load_analysis_plan(_write_plan(tmp_path / "other.yaml", other))

    api, interrupted = _runner(db_engine, plan=plan, scenario=short_game)
    original_sweep = interrupted._sweep
    sweeps = 0

    def lose_midway(*args, **kwargs):  # noqa: ANN002, ANN003
        nonlocal sweeps
        sweeps += 1
        if sweeps == 8:
            raise AutonomousGameError("simulated loss")
        return original_sweep(*args, **kwargs)

    interrupted._sweep = lose_midway  # type: ignore[method-assign]
    with pytest.raises(AutonomousGameError, match="simulated loss"):
        interrupted.run()
    game_id = interrupted.last_game_id
    assert game_id is not None
    descriptor = api.get(f"/admin/games/{game_id}/resume", headers=bearer("admin-secret-token"))
    recipe = descriptor.json()["manifest"]["resume"]

    _, wrong = _runner(db_engine, plan=other_plan, scenario="unused", run_configuration={"resume": recipe})
    with pytest.raises(AutonomousGameError, match="analysis plan"):
        wrong.resume(game_id)

    _, resumed = _runner(db_engine, scenario="unused", run_configuration={"resume": recipe})
    export = resumed.resume(game_id).export
    benchmark = export["run_manifest"]["benchmark"]
    assert benchmark["run_class"] == "analytical"
    assert benchmark["analysis_plan"]["sha256"] == plan.sha256
    assert export["game"]["status"] == "complete"


# --- TEST-I047-006 — CLI: plan sealed through `amaw run`; invalid combinations fail early -------


def _cli_fakes(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> dict:
    from types import SimpleNamespace

    package = tmp_path / "example-scenario"
    package.mkdir(exist_ok=True)
    (package / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)
    captured: dict = {"package": package}

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return cls()

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001
            captured["runner_options"] = kwargs

        def run(self):
            return SimpleNamespace(game_id="completed-game", export={"game": {}})

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, **kwargs):  # noqa: ANN001
            return cls()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    return captured


def test_i047_006_cli_seals_plan_and_defaults_to_playtest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import amaw_cli

    captured = _cli_fakes(monkeypatch, tmp_path)
    plan_path = _write_plan(tmp_path / "plan.yaml", VALID_PLAN)

    result = CliRunner().invoke(amaw_cli.app, ["run", str(captured["package"])])
    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["analysis_plan"] is None

    result = CliRunner().invoke(
        amaw_cli.app,
        ["run", str(captured["package"]), "--analysis-plan", str(plan_path)],
    )
    assert result.exit_code == 0, result.output
    sealed = captured["runner_options"]["analysis_plan"]
    assert sealed.plan_id == "swedish-con-opposition-2026-09"
    assert sealed.content == VALID_PLAN

    result = CliRunner().invoke(
        amaw_cli.app,
        ["run", str(captured["package"]), "--analysis-plan", str(plan_path),
         "--run-class", "analytical"],
    )
    assert result.exit_code == 0, result.output


def test_i047_006_cli_rejects_invalid_plan_combinations_before_any_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from typer.testing import CliRunner

    import amaw_cli

    captured = _cli_fakes(monkeypatch, tmp_path)
    package = str(captured["package"])
    plan_path = _write_plan(tmp_path / "plan.yaml", VALID_PLAN)
    broken = _write_plan(tmp_path / "broken.yaml", _without(VALID_PLAN, "estimand"))

    cases = [
        (["run", package, "--analysis-plan", str(broken)], "estimand"),
        (["run", package, "--run-class", "analytical"], "--analysis-plan"),
        (["run", package, "--run-class", "playtest", "--analysis-plan", str(plan_path)], "playtest"),
        (["run", package, "--run-class", "exploratory"], "run-class"),
        (["run", "--resume-game", "00000000-0000-0000-0000-000000000000",
          "--analysis-plan", str(plan_path)], "--resume-game"),
        (["run", package, "--analysis-plan", str(tmp_path / "missing.yaml")], "missing.yaml"),
    ]
    for args, expected in cases:
        captured.pop("runner_options", None)
        result = CliRunner().invoke(amaw_cli.app, args)
        assert result.exit_code == 2, (args, result.output)
        assert expected in result.output, (args, result.output)
        assert "runner_options" not in captured, args
    assert not (tmp_path / "amaw.db").exists()


# --- TEST-I047-008/009 — registration verification over exports; legacy exports ---------------


def _export(game_id: str, benchmark: dict | None, *, legacy: bool = False) -> dict:
    manifest = None
    if benchmark is not None:
        manifest = {
            "manifest_sha256": "c" * 64,
            "created_at": "2026-09-16T08:00:00+00:00",
            "game_id": game_id,
            "benchmark": benchmark,
        }
    if legacy:
        manifest = {"manifest_sha256": "d" * 64, "created_at": "2026-09-12T08:00:00+00:00"}
    return {
        "export_format_version": 9,
        "game": {"id": game_id, "status": "complete"},
        "run_manifest": manifest,
    }


def test_i047_008_verify_registration_classifies_each_game(tmp_path: Path) -> None:
    from benchmark.analysis_plan import load_analysis_plan, verify_registration

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    other = copy.deepcopy(VALID_PLAN)
    other["title"] = "Changed after the results were seen"
    other_plan = load_analysis_plan(_write_plan(tmp_path / "other.yaml", other))
    sealed = {
        "mode": "single", "assignment_id": None, "repetition_index": None,
        "run_class": "analytical",
        "analysis_plan": {
            "schema": "analysis-plan-v1", "plan_id": plan.plan_id,
            "sha256": plan.sha256, "content": VALID_PLAN,
        },
    }
    exports = [
        _export("g-predeclared", sealed),
        _export("g-other", {**sealed, "analysis_plan": {**sealed["analysis_plan"], "sha256": other_plan.sha256}}),
        _export("g-playtest", {"mode": "single", "run_class": "playtest", "analysis_plan": None}),
    ]

    report = verify_registration(plan, exports)

    assert report.plan_sha256 == plan.sha256
    assert [(g.game_id, g.registration) for g in report.games] == [
        ("g-predeclared", "predeclared"),
        ("g-other", "post_hoc_plan"),
        ("g-playtest", "unplanned"),
    ]
    assert report.games[0].sealed_at == "2026-09-16T08:00:00+00:00"
    assert report.all_predeclared is False
    assert report.counts == {"predeclared": 1, "post_hoc_plan": 1, "unplanned": 1}
    assert verify_registration(plan, exports[:1]).all_predeclared is True
    # Verifying the amended plan against the same exports shows the swap rather than hiding it.
    assert verify_registration(other_plan, exports[:1]).games[0].registration == "post_hoc_plan"

    as_dict = report.to_dict()
    assert as_dict["all_predeclared"] is False
    assert as_dict["games"][0] == {
        "game_id": "g-predeclared", "registration": "predeclared",
        "sealed_at": "2026-09-16T08:00:00+00:00", "sealed_plan_sha256": plan.sha256,
        "sealed_before_start": None,  # I-078: no game_started event in this export to time it
    }


def test_i047_009_legacy_exports_read_as_playtests(tmp_path: Path) -> None:
    from benchmark.analysis_plan import load_analysis_plan, run_class_of, verify_registration

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    legacy_manifest = _export("g-legacy", None, legacy=True)
    no_manifest = _export("g-none", None)
    pre_i047 = _export("g-pre", {"mode": "single", "assignment_id": None, "repetition_index": None})

    assert run_class_of(legacy_manifest) == "playtest"
    assert run_class_of(no_manifest) == "playtest"
    assert run_class_of(pre_i047) == "playtest"
    report = verify_registration(plan, [legacy_manifest, no_manifest, pre_i047])
    assert {g.registration for g in report.games} == {"unplanned"}
    assert report.all_predeclared is False
    assert verify_registration(plan, []).all_predeclared is False


def test_i047_008_cli_helper_validates_and_verifies(tmp_path: Path) -> None:
    from typer.testing import CliRunner

    import amaw_cli
    from benchmark.analysis_plan import load_analysis_plan

    plan_path = _write_plan(tmp_path / "plan.yaml", VALID_PLAN)
    plan = load_analysis_plan(plan_path)
    broken = _write_plan(tmp_path / "broken.yaml", _without(VALID_PLAN, "assurance_checks", empty=True))

    result = CliRunner().invoke(amaw_cli.app, ["analysis-plan", "validate", str(plan_path)])
    assert result.exit_code == 0, result.output
    assert plan.sha256 in result.stdout and plan.plan_id in result.stdout

    result = CliRunner().invoke(amaw_cli.app, ["analysis-plan", "validate", str(broken)])
    assert result.exit_code == 2
    assert "assurance_checks" in result.output

    sealed = {
        "mode": "single", "run_class": "analytical",
        "analysis_plan": {"schema": "analysis-plan-v1", "plan_id": plan.plan_id,
                          "sha256": plan.sha256, "content": VALID_PLAN},
    }
    predeclared = tmp_path / "predeclared.json"
    predeclared.write_text(json.dumps(_export("g-predeclared", sealed)), encoding="utf-8")
    playtest = tmp_path / "playtest.json"
    playtest.write_text(json.dumps(_export("g-playtest", {"run_class": "playtest"})), encoding="utf-8")

    result = CliRunner().invoke(
        amaw_cli.app, ["analysis-plan", "verify", str(plan_path), str(predeclared)]
    )
    assert result.exit_code == 0, result.output
    verdict = json.loads(result.stdout)
    assert verdict["all_predeclared"] is True
    assert verdict["games"][0]["registration"] == "predeclared"

    result = CliRunner().invoke(
        amaw_cli.app,
        ["analysis-plan", "verify", str(plan_path), str(predeclared), str(playtest)],
    )
    assert result.exit_code == 1, result.output
    verdict = json.loads(result.stdout)
    assert verdict["all_predeclared"] is False
    assert verdict["counts"] == {"predeclared": 1, "post_hoc_plan": 0, "unplanned": 1}


def test_i047_001_shipped_example_plan_validates() -> None:
    from benchmark.analysis_plan import load_analysis_plan

    example = Path(__file__).parent.parent / "docs" / "analysis-plan-example.yaml"
    plan = load_analysis_plan(example)
    assert plan.content["schema"] == "analysis-plan-v1"
    assert plan.primary_outcome_ids()


# --- I-078-005 — registration also checks that the plan was sealed before play started -------


def _started(export: dict, at: str) -> dict:
    return {
        **export,
        "events": [{"id": 1, "turn": 1, "type": "game_started", "actor_id": None, "payload": {}, "created_at": at}],
    }


def test_i078_005_a_plan_sealed_after_game_started_is_post_hoc(tmp_path: Path) -> None:
    """Same SHA is not enough: the manifest must have been created before the ``game_started``
    event. This stays an assurance check for legacy/unflagged games; the server's lobby-only
    sealing (TEST-I078-007) is the prevention."""
    from benchmark.analysis_plan import load_analysis_plan, verify_registration

    plan = load_analysis_plan(_write_plan(tmp_path / "plan.yaml", VALID_PLAN))
    sealed = {
        "mode": "single", "run_class": "analytical",
        "analysis_plan": {
            "schema": "analysis-plan-v1", "plan_id": plan.plan_id,
            "sha256": plan.sha256, "content": VALID_PLAN,
        },
    }
    exports = [
        _started(_export("g-before", sealed), "2026-09-16T08:00:01+00:00"),   # sealed 08:00:00
        _started(_export("g-same-instant", sealed), "2026-09-16T08:00:00+00:00"),
        _started(_export("g-after", sealed), "2026-09-16T07:59:59+00:00"),
        _export("g-no-events", sealed),  # a legacy export without events cannot be timed
    ]

    report = verify_registration(plan, exports)

    assert [(g.game_id, g.registration, g.sealed_before_start) for g in report.games] == [
        ("g-before", "predeclared", True),
        ("g-same-instant", "predeclared", True),
        ("g-after", "post_hoc_plan", False),
        ("g-no-events", "predeclared", None),
    ]
    assert report.games[2].sealed_plan_sha256 == plan.sha256  # the SHA did match; time did not
    assert report.all_predeclared is False
    assert report.to_dict()["games"][2]["sealed_before_start"] is False
