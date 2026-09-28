"""I-128 — ``skaro-crisis`` 1.2.0 declares its recommended Diplomacy and Con condition.

The scenario declares the condition run 18 validated (bounded Diplomacy with 3 rounds, sampled
Cons with 2 challengers) through the D-099 ``game.*`` defaults, so a bare ``amaw run`` selects it.
Explicit flags still take precedence (scenario-format §6), and the version bump follows §20.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import amaw_cli
from app import scenarios
from tests.cli_helpers import capture_run_cli
from tests.test_scenario_skaro_crisis import SCENARIO_DIR

REPO = Path(__file__).resolve().parents[1]
DECLARED = {
    "diplomacy_mode": "bounded_rounds",
    "diplomacy_rounds_per_turn": 3,
    "con_mode": "sampled_sealed",
    "con_challengers_per_argument": 2,
}
# The last 1.1.0 manifest; everything but the version and the declared condition must match it.
BASELINE = REPO / "tests" / "fixtures" / "scenario-baselines" / "skaro-crisis-1.1.0.yaml"


# --- TEST-I128-001: 1.2.0 declares the run 18 condition and changes nothing else -----------


def test_i128_001_version_bump_declares_only_the_condition() -> None:
    definition = scenarios.load_scenario(SCENARIO_DIR).definition
    assert definition.scenario.version == "1.2.0"
    game = definition.game
    assert {field: getattr(game, field) for field in DECLARED} == DECLARED

    current = yaml.safe_load((SCENARIO_DIR / "scenario.yaml").read_text(encoding="utf-8"))
    baseline = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))
    assert baseline["scenario"]["version"] == "1.1.0"
    for field in DECLARED:
        assert field not in baseline["game"]
    current["scenario"]["version"] = baseline["scenario"]["version"]
    for field in DECLARED:
        current["game"].pop(field)
    assert current == baseline


# --- TEST-I128-002: a bare run resolves the declared condition; flags still override -------


def _runner_options(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str) -> dict:
    captured = capture_run_cli(tmp_path, monkeypatch)
    result = CliRunner().invoke(amaw_cli.app, [
        "run", str(SCENARIO_DIR), "--player-model", "p/m", "--umpire-model", "u/m",
        "--consequence-model", "c/m", "--seed", "1", *extra,
    ])
    assert result.exit_code == 0, result.output
    return captured["runner_options"]


def test_i128_002_bare_run_selects_declared_condition_and_flags_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("AMAW_DIPLOMACY_MODE", "AMAW_DIPLOMACY_ROUNDS_PER_TURN", "AMAW_CON_MODE",
                 "AMAW_CON_CHALLENGERS_PER_ARGUMENT"):
        monkeypatch.delenv(name, raising=False)
    runner = _runner_options(tmp_path / "bare", monkeypatch)
    assert {field: runner[field] for field in DECLARED} == DECLARED

    runner = _runner_options(
        tmp_path / "flags", monkeypatch,
        "--diplomacy-mode", "open_diplomacy", "--con-mode", "open_cons",
    )
    assert (runner["diplomacy_mode"], runner["diplomacy_rounds_per_turn"]) == ("open_diplomacy", None)
    assert (runner["con_mode"], runner["con_challengers_per_argument"]) == ("open_cons", None)
