"""``analysis-plan-v2``: a structured ``design`` whose policies reconcile with the schedule.

benchmark-methodology.md §4.2 (0.3.11, D-109). v2 is v1 with ``design.scope`` (``experiment`` or
``cell``), a ``seed_policy`` equal to the runner's formula and a ``rotation_policy`` from the
runner's vocabulary. ``amaw benchmark`` refuses a v2 plan that disagrees with its schedule, and a
``cell`` batch names its level with ``--plan-level``. v1 is unchanged (TEST-I047-*, TEST-I078-*).

- TEST-I090-001: the v2 design is validated at load time, by name.
- TEST-I090-002: ``reconcile_plan`` compares the declared scope and policies with the schedule.
- TEST-I090-003: ``--plan-level`` names a cell's level and is refused everywhere else.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml

from benchmark.analysis_plan import AnalysisPlan, AnalysisPlanError, load_analysis_plan
from benchmark.controls import ControlError, reconcile_plan
from benchmark.runner import SEED_POLICY
from tests.test_benchmark_controls import _invoke_benchmark, _schedule
from tests.test_provenance_analysis_plan import VALID_PLAN

V2_EXAMPLE = Path(__file__).resolve().parents[1] / "docs" / "analysis-plan-v2-example.yaml"


def _v2_content(**overrides: Any) -> dict:
    """A v2 plan describing the two-model cyclic ``_schedule()`` batch (4 games) as the whole
    experiment, unless overridden."""
    content = copy.deepcopy(VALID_PLAN)
    content["schema"] = "analysis-plan-v2"
    content["treatment"] = {"factor": "player_model", "levels": ["fake/x", "fake/y"]}
    content["design"] = {
        "scope": "experiment",
        "planned_attempts": 4,
        "repetitions_per_assignment": 2,
        "seed_policy": SEED_POLICY,
        "rotation_policy": "cyclic",
    }
    content["design"].update(overrides.pop("design", {}))
    content.update(overrides)
    return content


def _write(tmp_path: Path, content: dict) -> Path:
    path = tmp_path / "plan.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def _v2_plan(tmp_path: Path, **overrides: Any) -> AnalysisPlan:
    return load_analysis_plan(_write(tmp_path, _v2_content(**overrides)))


def _cell_plan(tmp_path: Path) -> AnalysisPlan:
    """One cell of a Con-mode experiment: each level's batch is the 4-game schedule."""
    return _v2_plan(
        tmp_path,
        treatment={"factor": "con_mode", "levels": ["open_cons", "sampled_sealed"]},
        design={"scope": "cell", "planned_attempts": 8},
    )


# --- TEST-I090-001 — the v2 design is validated at load time -------------------------------


def test_i090_001_v2_plan_loads_and_its_design_is_structured(tmp_path: Path) -> None:
    example = load_analysis_plan(V2_EXAMPLE)
    assert example.content["schema"] == "analysis-plan-v2"
    assert example.content["design"]["scope"] == "cell"

    plan = _v2_plan(tmp_path)
    assert plan.content["design"]["rotation_policy"] == "cyclic"
    assert plan.content["design"]["seed_policy"] == SEED_POLICY


@pytest.mark.parametrize(
    ("design", "named"),
    [
        ({"scope": "batch"}, "scope"),
        ({"rotation_policy": "one model in every Seat"}, "rotation_policy"),
        ({"seed_policy": "base_seed + repetition_index"}, "seed_policy"),
        ({"planned_attempts": 0}, "planned_attempts"),
        ({"extra": "x"}, "extra"),
    ],
)
def test_i090_001_v2_design_values_are_refused_by_name(
    tmp_path: Path, design: dict, named: str
) -> None:
    with pytest.raises(AnalysisPlanError, match=named):
        _v2_plan(tmp_path, design=design)


@pytest.mark.parametrize("missing", ["scope", "seed_policy", "rotation_policy"])
def test_i090_001_v2_design_keys_are_required(tmp_path: Path, missing: str) -> None:
    content = _v2_content()
    del content["design"][missing]
    with pytest.raises(AnalysisPlanError, match=missing):
        load_analysis_plan(_write(tmp_path, content))


def test_i090_001_a_cell_plan_declares_at_least_two_levels(tmp_path: Path) -> None:
    with pytest.raises(AnalysisPlanError, match="cell"):
        _v2_plan(
            tmp_path,
            treatment={"factor": "con_mode", "levels": ["sampled_sealed"]},
            design={"scope": "cell"},
        )


def test_i090_001_v1_keeps_its_prose_design_and_refuses_v2_keys(tmp_path: Path) -> None:
    content = copy.deepcopy(VALID_PLAN)
    assert load_analysis_plan(_write(tmp_path, content)).content["schema"] == "analysis-plan-v1"
    content["design"]["scope"] = "experiment"
    with pytest.raises(AnalysisPlanError, match="scope"):
        load_analysis_plan(_write(tmp_path, content))


# --- TEST-I090-002 — the declared scope and policies reconcile with the schedule ---------------


def test_i090_002_matching_v2_plan_is_reconciled_and_recorded(tmp_path: Path) -> None:
    plan = _v2_plan(tmp_path)
    assert reconcile_plan(plan, schedule=_schedule(), roster=["fake/x", "fake/y"]) == {
        "plan_id": plan.plan_id,
        "plan_sha256": plan.sha256,
        "plan_schema": "analysis-plan-v2",
        "planned_attempts": 4,
        "repetitions_per_assignment": 2,
        "batch_total_games": 4,
        "plan_scope": "experiment",
        "plan_level": None,
        "treatment_levels": ["fake/x", "fake/y"],
        "plan_seed_policy": SEED_POLICY,
        "plan_rotation_policy": "cyclic",
        "rotation_policy": "cyclic",
        "seed_policy": SEED_POLICY,
    }

    cell = _cell_plan(tmp_path)
    record = reconcile_plan(
        cell, schedule=_schedule(), roster=["fake/x", "fake/y"], plan_level="sampled_sealed"
    )
    assert record["plan_scope"] == "cell" and record["plan_level"] == "sampled_sealed"


@pytest.mark.parametrize(
    ("overrides", "named"),
    [
        ({"design": {"rotation_policy": "permutations"}}, "rotation_policy"),
        ({"design": {"rotation_policy": "single"}}, "rotation_policy"),
        # A cell plan whose counts describe the whole experiment, and the reverse.
        (
            {
                "treatment": {"factor": "con_mode", "levels": ["open_cons", "sampled_sealed"]},
                "design": {"scope": "cell", "planned_attempts": 4},
            },
            "scope",
        ),
        ({"design": {"scope": "experiment", "planned_attempts": 8}}, "planned_attempts"),
    ],
)
def test_i090_002_disagreeing_v2_policy_is_refused_by_name(
    tmp_path: Path, overrides: dict, named: str
) -> None:
    plan = _v2_plan(tmp_path, **overrides)
    with pytest.raises(ControlError, match=named):
        reconcile_plan(
            plan, schedule=_schedule(), roster=["fake/x", "fake/y"],
            plan_level="open_cons" if plan.content["design"]["scope"] == "cell" else None,
        )


def test_i090_002_seed_policy_is_compared_with_the_runner_formula(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The plan's formula must be the one the runner will use; if the runner's changed, a plan
    written for the old one is refused rather than silently recorded."""
    plan = _v2_plan(tmp_path)
    monkeypatch.setattr("benchmark.runner.SEED_POLICY", "base_seed + repetition_index")
    with pytest.raises(ControlError, match="seed_policy"):
        reconcile_plan(plan, schedule=_schedule(), roster=["fake/x", "fake/y"])


# --- TEST-I090-003 — ``--plan-level`` names a cell's level -----------------------------------


def test_i090_003_plan_level_is_checked_by_reconcile_plan(tmp_path: Path) -> None:
    cell = _cell_plan(tmp_path)
    roster = ["fake/x", "fake/y"]
    with pytest.raises(ControlError, match="--plan-level"):
        reconcile_plan(cell, schedule=_schedule(), roster=roster)
    with pytest.raises(ControlError, match="treatment levels"):
        reconcile_plan(cell, schedule=_schedule(), roster=roster, plan_level="closed_cons")

    whole = _v2_plan(tmp_path)
    with pytest.raises(ControlError, match="--plan-level"):
        reconcile_plan(whole, schedule=_schedule(), roster=roster, plan_level="fake/x")

    v1_path = _write(tmp_path, copy.deepcopy(VALID_PLAN) | {
        "treatment": {"factor": "player_model", "levels": ["fake/x", "fake/y"]},
        "design": VALID_PLAN["design"] | {"planned_attempts": 4, "repetitions_per_assignment": 2},
    })
    v1 = load_analysis_plan(v1_path)
    assert "plan_level" not in reconcile_plan(v1, schedule=_schedule(), roster=roster)
    with pytest.raises(ControlError, match="--plan-level"):
        reconcile_plan(v1, schedule=_schedule(), roster=roster, plan_level="fake/x")


BATCH = [
    "--models", "fake/x,fake/y", "--rotation", "cyclic", "--benchmark-mode", "model",
    "--repetitions", "2", "--base-seed", "10", "--scheduling-seed", "1",
]


def test_i090_003_cli_records_the_level_and_refuses_before_any_game(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _cell_plan(tmp_path)
    plan_path = str(tmp_path / "plan.yaml")

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, [*BATCH, "--analysis-plan", plan_path]
    )
    assert result.exit_code == 2, result.output
    assert "--plan-level" in result.output
    assert "execute" not in captured

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, [*BATCH, "--analysis-plan", plan_path, "--plan-level", "nope"]
    )
    assert result.exit_code == 2, result.output
    assert "execute" not in captured

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, [*BATCH, "--plan-level", "open_cons"]
    )
    assert result.exit_code == 2, result.output
    assert "--plan-level" in result.output and "execute" not in captured

    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path,
        [*BATCH, "--analysis-plan", plan_path, "--plan-level", "open_cons"],
    )
    assert result.exit_code == 0, result.output
    record = captured["experiment"]["analysis_plan"]
    assert record["plan_scope"] == "cell" and record["plan_level"] == "open_cons"
