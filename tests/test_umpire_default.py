"""I-159 acceptance: ``umpire-v1.7`` is the default Umpire prompt for every game (D-114).

An unpinned game resolves to v1.7 whatever its rules and Con-reply treatment. Pins still win,
the refusals that protect a condition still hold, and a resumed game keeps the version its recipe
recorded (a legacy recipe without one ran, and still resolves to, released v1.3).
"""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

import amaw_cli.conditions
from agents.guardrails import GuardrailConfig
from agents.player import PlayerConfig
from tests.cli_helpers import capture_run_cli, invoke_run
from tests.test_scenario_rules import _package

V17 = "umpire-v1.7"
VISIBLE = ("--con-reply-mode", "visible_reply", "--reply-max-points", "2")


@pytest.mark.parametrize("rules", [True, False], ids=["rules", "rule-free"])
@pytest.mark.parametrize("reply", [(), VISIBLE], ids=["sealed_cons", "visible_reply"])
def test_i159_001_an_unpinned_game_resolves_to_v17(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, rules: bool, reply: tuple[str, ...]
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    package = _package(tmp_path) if rules else _package(tmp_path, None)

    result = invoke_run(package, *reply)

    assert result.exit_code == 0, result.output
    assert captured["umpire_options"]["prompt_version"] == V17
    resume = captured["runner_options"]["run_configuration"]["resume"]
    assert resume["umpire"]["prompt_version"] == V17


def test_i159_002_pins_win_and_the_condition_refusals_hold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    ruled = _package(tmp_path, name="ruled")
    plain = _package(tmp_path, None, name="plain")

    for pinned in ("umpire-v1.3", "umpire-v1.4", "umpire-v1.6"):
        assert invoke_run(plain, "--umpire-prompt-version", pinned).exit_code == 0
        assert captured["umpire_options"]["prompt_version"] == pinned
    assert invoke_run(ruled, "--umpire-prompt-version", "umpire-v1.6").exit_code == 0
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.6"

    monkeypatch.setenv("AMAW_UMPIRE_PROMPT_VERSION", "umpire-v1.4")
    assert invoke_run(plain).exit_code == 0
    assert captured["umpire_options"]["prompt_version"] == "umpire-v1.4"
    monkeypatch.delenv("AMAW_UMPIRE_PROMPT_VERSION")

    refused = [
        (plain, ("--umpire-prompt-version", "umpire-v1.3", *VISIBLE)),
        (ruled, ("--umpire-prompt-version", "umpire-v1.3")),
        (ruled, ("--umpire-prompt-version", "umpire-v1.4")),
    ]
    for package, flags in refused:
        captured.clear()
        result = invoke_run(package, *flags)
        assert result.exit_code == 2, result.output
        assert "runner_options" not in captured


def _recipe(umpire: dict) -> dict:
    return {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "provider/original"},
            "temperature": None,
            "max_tokens": None,
            "timeout": 60.0,
            "reasoning_effort": None,
            "activation_mode": "single_shot",
            "prompt_version": "player-v1.11",
            "guardrails": dataclasses.asdict(GuardrailConfig()),
        },
        "umpire": umpire,
        "coordinator": {"max_cycles": 100, "cons_concurrency": 2, "diplomacy_concurrency": 2},
    }


@pytest.mark.parametrize(
    ("recorded", "expected"),
    [(None, "umpire-v1.3"), ("umpire-v1.6", "umpire-v1.6"), ("umpire-v1.3", "umpire-v1.3")],
)
def test_i159_003_a_resumed_game_keeps_its_recorded_prompt(recorded: str | None, expected: str) -> None:
    umpire = {"model": "u/m", "temperature": None, "max_tokens": None, "timeout": 60.0,
              "reasoning_effort": None}
    if recorded is not None:
        umpire["prompt_version"] = recorded
    recipe = _recipe(umpire)
    config = PlayerConfig.from_resume_recipe(recipe, model="provider/original")

    condition = amaw_cli.conditions._resolve_sealed_condition(recipe, config)

    assert condition.umpire_recipe["prompt_version"] == expected
