"""I-120: the condition and recipe hand-offs are typed, checked, and serialise exactly as before.

- The recipe sections have one definition each, as ``TypedDict``s in ``amaw_contracts``. The
  ``RESUME_*_FIELDS`` vocabulary the resume validator uses is derived from them and must keep its
  released values.
- ``TypedDict``s are plain dicts at runtime. What ``amaw run`` and ``amaw benchmark`` hand the
  runner and the batch must stay byte-identical to what they handed over before the types
  existed. The fixtures under ``tests/fixtures/i120/`` were captured from the pre-change commit.
- mypy checks the declared scope in ``pyproject.toml``, and this test makes a violation fail the
  suite.
"""

from __future__ import annotations

import dataclasses
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from typer.testing import CliRunner

import amaw_cli
import amaw_contracts
from amaw_contracts import canonical_json

REPO = Path(__file__).resolve().parents[1]
FIXTURES = REPO / "tests" / "fixtures" / "i120"
NORTHERN_STRAIT = str(REPO / "scenarios" / "northern-strait")
SKARO = str(REPO / "scenarios" / "skaro-crisis")
LEGACY_GAME = "27b9418d-6a85-49c8-a477-5cf6e9f4c1d7"


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes().rstrip(b"\n")


@pytest.mark.slow
def test_i120_001_type_checker_passes_on_the_declared_scope() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "mypy"], cwd=REPO, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # The configured module set is asserted by TEST-I127-001, not by mypy's file count.
    assert "Success: no issues found in " in result.stdout


def test_i120_002_derived_field_vocabulary_keeps_its_released_values() -> None:
    assert amaw_contracts.RESUME_PLAYER_FIELDS == (
        "assignments", "temperature", "max_tokens", "timeout", "reasoning_effort",
        "activation_mode", "prompt_version", "guardrails",
    )
    assert amaw_contracts.RESUME_PLAYER_OPTIONAL_FIELDS == (
        "research_mode", "research_history_mode", "search_contract", "tool_surface",
    )
    assert amaw_contracts.RESUME_UMPIRE_FIELDS == (
        "model", "temperature", "max_tokens", "timeout", "reasoning_effort",
    )
    assert amaw_contracts.RESUME_UMPIRE_OPTIONAL_FIELDS == ("prompt_version",)
    assert amaw_contracts.RESUME_CONSEQUENCE_FIELDS == (
        "model", "temperature", "max_tokens", "timeout", "reasoning_effort", "prompt_version",
    )
    assert amaw_contracts.RESUME_COORDINATOR_FIELDS == (
        "max_cycles", "cons_concurrency", "diplomacy_concurrency",
    )


def _environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(__import__("os").environ):
        if name.startswith("AMAW_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], **patches: Any) -> dict[str, Any]:
    """Invoke ``amaw run`` with the runner and composition captured, not executed."""
    captured: dict[str, Any] = {}

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001, ANN003
            captured["runner"] = kwargs

        def run(self):  # noqa: ANN202
            return SimpleNamespace(game_id="g", export={})

        def resume(self, game_id):  # noqa: ANN001, ANN202
            return SimpleNamespace(game_id=game_id, export={})

    def umpire(cls, settings, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["umpire_kwargs"] = kwargs
        return SimpleNamespace()

    def resolver(cls, settings, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["resolver_kwargs"] = kwargs
        return SimpleNamespace()

    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr("agents.umpire.AIUmpire.for_openrouter", classmethod(umpire))
    monkeypatch.setattr(
        "agents.consequence.AIConsequenceResolver.for_openrouter", classmethod(resolver)
    )
    for target, value in patches.items():
        monkeypatch.setattr(target.replace("__", "."), value)
    result = CliRunner().invoke(amaw_cli.app, argv)
    assert result.exit_code == 0, result.output
    return captured


_FULL_FLAGS = [
    "--player-model", "p/p", "--umpire-model", "u/u", "--seed", "17", "--max-cycles", "23",
    "--request-timeout-seconds", "300", "--log-level", "DEBUG", "--stream-game-details",
    "--cons-concurrency", "2", "--diplomacy-concurrency", "3", "--diplomacy-mode",
    "bounded_rounds", "--diplomacy-rounds-per-turn", "2", "--con-mode", "sampled_sealed",
    "--con-challengers-per-argument", "1", "--con-reply-mode", "visible_reply",
    "--reply-max-points", "2", "--activation-mode", "tool_loop", "--research-mode", "batched-v1",
    "--search-contract", "search-v1", "--research-history-mode", "compact-success-v1",
    "--tool-surface", "available_only", "--max-messages-per-activation", "2",
    "--max-llm-iterations", "24", "--max-tool-calls", "40", "--max-research-tool-calls", "5",
    "--max-research-operations", "6", "--max-messages-per-phase", "7",
    "--max-activations-per-opportunity", "3", "--player-temperature", "0.3",
    "--player-max-tokens", "999", "--player-reasoning-effort", "low",
    "--umpire-temperature", "0.1", "--umpire-max-tokens", "888",
    "--umpire-reasoning-effort", "high",
]


def test_i120_003_new_run_hand_off_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    captured = _run(monkeypatch, ["run", NORTHERN_STRAIT, *_FULL_FLAGS])
    observed = {
        "run_configuration": captured["runner"]["run_configuration"],
        "umpire_kwargs": captured["umpire_kwargs"],
    }
    assert canonical_json(observed) == _fixture("run_full")
    # Canonical bytes sort keys; the sealed recipe must also keep the builder's section order.
    assert list(captured["runner"]["run_configuration"]) == ["runtime", "resume"]
    assert list(captured["runner"]["run_configuration"]["resume"]) == [
        "schema", "player", "umpire", "inference_profile", "coordinator",
    ]


def test_i120_004_bounded_effects_run_hand_off_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    # skaro-crisis 1.2.0 declares bounded Diplomacy and sampled Cons (I-128). The explicit
    # released condition keeps this hand-off identical to the pinned fixture.
    captured = _run(monkeypatch, [
        "run", SKARO, "--consequence-model", "c/c",
        "--diplomacy-mode", "open_diplomacy", "--con-mode", "open_cons",
    ])
    observed = {
        "run_configuration": captured["runner"]["run_configuration"],
        "umpire_kwargs": captured["umpire_kwargs"],
        "resolver_kwargs": captured["resolver_kwargs"],
    }
    assert canonical_json(observed) == _fixture("run_skaro_consequence")
    assert list(captured["runner"]["run_configuration"]["resume"]) == [
        "schema", "player", "umpire", "consequence", "inference_profile", "coordinator",
    ]


def test_i120_005_legacy_resume_is_accepted_and_reconstructed_identically(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from agents.guardrails import GuardrailConfig

    _environment(monkeypatch, tmp_path)
    guardrails = dataclasses.asdict(GuardrailConfig())
    guardrails.pop("max_research_tool_calls")
    legacy = {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "provider/original"}, "temperature": 0.2,
            "max_tokens": 4321, "timeout": 222.0, "reasoning_effort": "low",
            "activation_mode": "single_shot", "prompt_version": "player-v1.6",
            "guardrails": guardrails,
        },
        "umpire": {"model": "u/orig", "temperature": 0.1, "max_tokens": 8765, "timeout": 222.0,
                   "reasoning_effort": "medium"},
        "coordinator": {"max_cycles": 777, "cons_concurrency": 2, "diplomacy_concurrency": 3},
    }
    runtime = {"request_timeout_seconds": 222.0, "max_cycles": 777, "log_level": "INFO",
               "stream_game_details": False, "guardrails": guardrails}
    captured = _run(
        monkeypatch, ["run", "--resume-game", LEGACY_GAME],
        app__resumption__describe=lambda engine, parsed: SimpleNamespace(
            manifest={"resume": legacy, "runtime": runtime}
        ),
    )
    observed = {
        "run_configuration": captured["runner"]["run_configuration"],
        "umpire_kwargs": captured["umpire_kwargs"],
    }
    assert canonical_json(observed) == _fixture("run_resume_legacy")
    # The sealed recipe and runtime are handed back as the same objects, never rebuilt.
    assert captured["runner"]["run_configuration"]["resume"] is legacy
    assert captured["runner"]["run_configuration"]["runtime"] is runtime


def test_i120_006_benchmark_hand_off_is_byte_identical(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    captured: dict[str, Any] = {}

    def executor(client, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["executor"] = kwargs
        return "executor"

    def run_batch(schedule, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["controls"] = kwargs["controls"].record()
        return SimpleNamespace(counts=lambda: {
            "total_games": 0, "completed_games": 0, "failed_games": 0, "not_run_games": 0,
        })

    monkeypatch.setattr("benchmark.runner.autonomous_executor", executor)
    monkeypatch.setattr("benchmark.runner.run_batch", run_batch)
    result = CliRunner().invoke(amaw_cli.app, [
        "benchmark", SKARO, "--models", "a/a", "--benchmark-mode", "agent",
        "--consequence-model", "c/c", "--player-temperature", "0.4",
        "--diplomacy-mode", "open_diplomacy",  # skaro-crisis 1.2.0 declares bounded (I-128)
        "--umpire-reasoning-effort", "low", "--con-mode", "sampled_sealed",
        "--con-challengers-per-argument", "2", "--output-dir", str(tmp_path / "batch"),
        "--experiment-id", "exp", "--base-seed", "1", "--scheduling-seed", "2",
    ])
    assert result.exit_code == 0, result.output
    observed = {
        "run_configuration": captured["executor"]["run_configuration"],
        "umpire_condition": captured["controls"]["umpire_condition"],
        "consequence_condition": captured["controls"]["consequence_condition"],
    }
    assert canonical_json(observed) == _fixture("bench_skaro")


def test_i120_007_resolved_condition_is_frozen_and_flags_cover_both_commands() -> None:
    import inspect

    fields = {field.name for field in dataclasses.fields(amaw_cli.ConditionFlags)}
    for command in (amaw_cli.run, amaw_cli.benchmark):
        assert fields <= set(inspect.signature(command).parameters)
    assert amaw_cli.ResolvedCondition.__dataclass_params__.frozen  # type: ignore[attr-defined]
    assert amaw_cli.ConditionFlags.__dataclass_params__.frozen  # type: ignore[attr-defined]
    # Every recipe section's keys are exactly the typed contract's keys.
    recipe = json.loads(_fixture("run_skaro_consequence"))["run_configuration"]["resume"]
    assert set(recipe["umpire"]) == set(amaw_contracts.UmpireRecipe.__annotations__)
    assert set(recipe["consequence"]) == set(amaw_contracts.ConsequenceRecipe.__annotations__)
    assert set(recipe) == set(amaw_contracts.ResumeRecipe.__annotations__)
