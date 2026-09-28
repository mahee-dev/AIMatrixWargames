"""I-161 + I-163 — ``swedish-government`` 7.1.0: bounded Diplomacy and internal approval.

Run 22 (7.0.0) closed three of four Diplomacy phases only through the no-progress ceiling, and
drew no roll in 32 rulings: every party that matters is played and the decisive vote is
arithmetic, so after D-118 nothing was left for the dice. 7.1.0 declares bounded Diplomacy
(D-099) and one rule that places real, unplayed uncertainty with each party's own board or
Riksdag group. Test IDs ``TEST-I161-001`` and ``TEST-I163-001`` are the task's own.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

import amaw_cli
from app import scenarios
from tests.cli_helpers import capture_run_cli

REPO = Path(__file__).resolve().parents[1]
PACKAGE = REPO / "scenarios" / "swedish-government"
DECLARED = {"diplomacy_mode": "bounded_rounds", "diplomacy_rounds_per_turn": 3}
NEW_RULE = "intern-foerankring"
# The 7.0.0 manifest; everything but the version, the declared condition and the new rule must
# match it.
BASELINE = REPO / "tests" / "fixtures" / "scenario-baselines" / "swedish-government-7.0.0.yaml"


# --- TEST-I161-001: 7.1.0 declares bounded Diplomacy and changes nothing else it should not ---


def test_i161_001_version_bump_declares_bounded_diplomacy_and_one_rule() -> None:
    definition = scenarios.load_scenario(PACKAGE).definition
    assert definition.scenario.version == "7.1.0"
    assert {field: getattr(definition.game, field) for field in DECLARED} == DECLARED

    current = yaml.safe_load((PACKAGE / "scenario.yaml").read_text(encoding="utf-8"))
    baseline = yaml.safe_load(BASELINE.read_text(encoding="utf-8"))
    assert baseline["scenario"]["version"] == "7.0.0"
    for field in DECLARED:
        assert field not in baseline["game"]
    current["scenario"]["version"] = baseline["scenario"]["version"]
    for field in DECLARED:
        current["game"].pop(field)
    current["rules"] = [rule for rule in current["rules"] if rule["id"] != NEW_RULE]
    assert current == baseline


def test_i161_001_bare_run_selects_bounded_diplomacy_and_a_flag_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in ("AMAW_DIPLOMACY_MODE", "AMAW_DIPLOMACY_ROUNDS_PER_TURN"):
        monkeypatch.delenv(name, raising=False)

    def runner_options(sub: str, *extra: str) -> dict:
        captured = capture_run_cli(tmp_path / sub, monkeypatch)
        result = CliRunner().invoke(amaw_cli.app, [
            "run", str(PACKAGE), "--player-model", "p/m", "--umpire-model", "u/m",
            "--seed", "1", *extra,
        ])
        assert result.exit_code == 0, result.output
        return captured["runner_options"]

    runner = runner_options("bare")
    assert {field: runner[field] for field in DECLARED} == DECLARED
    runner = runner_options("flag", "--diplomacy-mode", "open_diplomacy")
    assert (runner["diplomacy_mode"], runner["diplomacy_rounds_per_turn"]) == ("open_diplomacy", None)


# --- TEST-I163-001: uncertainty sits with unplayed party bodies, never with other Actors ------


def test_i163_001_internal_approval_rule_gives_the_dice_unplayed_uncertainty() -> None:
    rules = {rule.id: rule.text for rule in scenarios.load_scenario(PACKAGE).rules}
    text = " ".join(rules[NEW_RULE].lower().split())
    for trigger in ("regering", "budget", "röd linje"):
        assert trigger in text, trigger
    assert "partistyrelse" in text and "riksdagsgrupp" in text
    assert "inte spelade" in text
    assert "sannolikhet" in text
    assert "andra spelade partiers beslut" in text and "aldrig" in text

    regler = (PACKAGE / "resources" / "regeringsregler.md").read_text(encoding="utf-8")
    section = " ".join(regler.split("## 12.", 1)[1].split("\n## ", 1)[0].lower().split())
    assert "inte egna actors" in section and "sannolikhet" in section
    assert "i båda utfallen" in section, "the leadership's own commitment stands either way"
    assert "simulerar inte partistyrelser" not in regler.lower()

    guide = " ".join((REPO / "guide" / "scenario-authoring.md").read_text(encoding="utf-8").split())
    assert "Give the dice something to decide" in guide
    assert "agents.md §23" in guide
