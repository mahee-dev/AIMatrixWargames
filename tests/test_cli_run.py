"""User-facing autonomous run command (I-035)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from typer.testing import CliRunner

import amaw_cli.conditions


def test_i048_007_and_i094_001_cli_resume_reconstructs_condition_and_honours_stream_override(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import dataclasses

    from agents.guardrails import GuardrailConfig

    game_id = "27b9418d-6a85-49c8-a477-5cf6e9f4c1d7"
    legacy_guardrails = dataclasses.asdict(GuardrailConfig())
    legacy_guardrails.pop("max_research_tool_calls")
    recipe = {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "provider/original"},
            "temperature": 0.2,
            "max_tokens": 4321,
            "timeout": 222.0,
            "reasoning_effort": "low",
            "activation_mode": "single_shot",
            "prompt_version": "player-v1.6",
            "guardrails": legacy_guardrails,
        },
        "umpire": {
            "model": "provider/original-umpire",
            "temperature": 0.1,
            "max_tokens": 8765,
            "timeout": 222.0,
            "reasoning_effort": "medium",
        },
        "coordinator": {
            "max_cycles": 777,
            "cons_concurrency": 2,
            "diplomacy_concurrency": 3,
        },
    }
    runtime = {
        "request_timeout_seconds": 222.0,
        "max_cycles": 777,
        "log_level": "INFO",
        "stream_game_details": False,
        "guardrails": recipe["player"]["guardrails"],
    }
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "resume.db"))
    captured: dict[str, object] = {}

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            captured["umpire"] = kwargs
            return cls()

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001
            captured["runner"] = kwargs
            self.last_game_id = None

        def resume(self, value: str):
            captured["resumed"] = value
            self.last_game_id = value
            return SimpleNamespace(game_id=value, export={"game": {"id": value}})

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
            captured["player"] = config
            return cls()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    monkeypatch.setattr(
        "app.resumption.describe",
        lambda engine, parsed: SimpleNamespace(manifest={"resume": recipe, "runtime": runtime}),
    )

    result = CliRunner().invoke(amaw_cli.app, ["run", "--resume-game", game_id])

    assert result.exit_code == 0, result.output
    assert result.stdout == f"{game_id}\n"
    assert captured["resumed"] == game_id
    assert captured["runner"]["scenario"] == "."
    assert captured["runner"]["max_cycles"] == 777
    assert captured["runner"]["run_configuration"] == {
        "runtime": runtime,
        "resume": recipe,
    }
    captured["runner"]["player_factory"](object(), "arkania", "Arkania")
    assert captured["player"].model == "provider/original"
    assert captured["player"].prompt_version == "player-v1.6"
    assert captured["player"].guardrails.max_research_tool_calls == 4
    assert captured["umpire"]["model"] == "provider/original-umpire"

    # I-094: an explicit presentation override controls this invocation, but the immutable
    # manifest/runtime record remains the one sealed by the first process.
    captured.clear()
    result = CliRunner().invoke(
        amaw_cli.app, ["run", "--resume-game", game_id, "--stream-game-details"]
    )
    assert result.exit_code == 0, result.output
    assert captured["runner"]["stream_game_details"] is True
    assert captured["runner"]["run_configuration"]["runtime"] is runtime

    runtime["stream_game_details"] = True
    captured.clear()
    result = CliRunner().invoke(
        amaw_cli.app, ["run", "--resume-game", game_id, "--no-stream-game-details"]
    )
    assert result.exit_code == 0, result.output
    assert captured["runner"]["stream_game_details"] is False
    assert captured["runner"]["run_configuration"]["runtime"] is runtime


def test_i048_007_cli_resume_refuses_condition_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    result = CliRunner().invoke(
        amaw_cli.app,
        ["run", "--resume-game", str(uuid.uuid4()), "--player-model", "changed/model"],
    )
    assert result.exit_code == 2
    assert "reconstructs the experimental condition" in result.output


def _scenario_package(tmp_path: Path) -> Path:
    package = tmp_path / "example-scenario"
    package.mkdir()
    (package / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")
    return package


def test_cli_run_starts_autonomous_runner_and_prints_game_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _scenario_package(tmp_path)
    database = tmp_path / "amaw.db"
    export_path = tmp_path / "overnight" / "export.json"
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(database))
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)

    captured: dict[str, object] = {}

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            captured["umpire_settings"] = settings
            captured["umpire_options"] = kwargs
            return cls()

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001
            captured["client"] = client
            captured["runner_options"] = kwargs
            self.last_game_id = None

        def run(self):
            self.last_game_id = "created-game"
            return SimpleNamespace(
                game_id="completed-game", export={"game": {"id": "completed-game"}}
            )

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
            captured["player_config"] = config
            return cls()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)

    result = CliRunner().invoke(
        amaw_cli.app,
        [
            "run",
            str(package),
            "--player-model",
            "provider/player",
            "--umpire-model",
            "provider/umpire",
            "--seed",
            "17",
            "--max-cycles",
            "23",
            "--request-timeout-seconds",
            "300",
            "--log-level",
            "DEBUG",
            "--log-file",
            str(tmp_path / "overnight" / "run.log"),
            "--stream-game-details",
            "--export-path",
            str(export_path),
            "--cons-concurrency",
            "2",
            "--diplomacy-concurrency",
            "3",
            "--diplomacy-mode",
            "bounded_rounds",
            "--diplomacy-rounds-per-turn",
            "2",
            "--con-mode",
            "sampled_sealed",
            "--con-challengers-per-argument",
            "1",
            "--activation-mode",
            "tool_loop",
            "--research-mode",
            "batched-v1",
            "--research-history-mode",
            "compact-success-v1",
            "--tool-surface",
            "available_only",
            "--max-messages-per-activation",
            "2",
            "--max-llm-iterations",
            "24",
                "--max-tool-calls",
                "40",
                "--max-research-tool-calls",
                "5",
            "--max-research-operations",
            "6",
            "--max-messages-per-phase",
            "30",
            "--max-activations-per-opportunity",
            "12",
            "--player-temperature",
            "0",
            "--player-max-tokens",
            "8000",
            "--player-reasoning-effort",
            "low",
            "--umpire-temperature",
            "0",
            "--umpire-max-tokens",
            "16000",
            "--umpire-reasoning-effort",
            "low",
        ],
    )

    assert result.exit_code == 0, result.output
    assert result.stdout == "completed-game\n"
    assert json.loads(export_path.read_text(encoding="utf-8")) == {
        "game": {"id": "completed-game"}
    }
    assert captured["runner_options"]["scenario"] == package.resolve()
    assert captured["runner_options"]["seed"] == 17
    assert captured["runner_options"]["max_cycles"] == 23
    assert captured["runner_options"]["stream_game_details"] is True
    assert captured["runner_options"]["run_configuration"]["runtime"] == {
        "request_timeout_seconds": 300.0,
        "max_cycles": 23,
        "log_level": "DEBUG",
        "stream_game_details": True,
        "guardrails": {
            "max_llm_iterations": 24,
                "max_tool_calls": 40,
                "max_research_tool_calls": 5,
            "max_research_operations": 6,
            "max_messages_per_activation": 2,
            "max_messages_per_phase": 30,
            "max_activations_per_opportunity": 12,
            "max_message_chars": 4000,
            "max_argument_action_chars": 2000,
            "max_desired_effect_chars": 2000,
            "max_pros": 5,
            "max_pro_chars": 1000,
            "max_note_chars": 4000,
            "max_tool_payload_bytes": 16384,
        },
    }
    assert captured["runner_options"]["cons_concurrency"] == 2
    assert captured["runner_options"]["diplomacy_concurrency"] == 3
    assert captured["runner_options"]["diplomacy_mode"] == "bounded_rounds"
    assert captured["runner_options"]["diplomacy_rounds_per_turn"] == 2
    assert captured["runner_options"]["run_configuration"]["resume"]["coordinator"][
        "diplomacy_mode"
    ] == "bounded_rounds"
    assert captured["runner_options"]["con_mode"] == "sampled_sealed"  # I-055
    assert captured["runner_options"]["con_challengers_per_argument"] == 1
    assert captured["runner_options"]["run_configuration"]["resume"]["coordinator"][
        "con_mode"
    ] == "sampled_sealed"
    assert captured["runner_options"]["run_configuration"]["resume"]["coordinator"][
        "con_challengers_per_argument"
    ] == 1
    assert captured["umpire_options"]["model"] == "provider/umpire"
    assert captured["umpire_options"]["temperature"] == 0.0
    assert captured["umpire_options"]["max_tokens"] == 16000
    assert captured["umpire_options"]["timeout"] == 300.0
    assert captured["umpire_options"]["reasoning_effort"] == "low"
    assert captured["runner_options"]["admin_token"]
    captured["runner_options"]["player_factory"](object(), "arkania", "Arkania")
    assert captured["player_config"].model == "provider/player"
    assert captured["player_config"].activation_mode == "tool_loop"
    assert captured["player_config"].research_mode == "batched-v1"
    assert captured["player_config"].research_history_mode == "compact-success-v1"
    assert captured["player_config"].tool_surface == "available_only"  # I-092
    assert captured["player_config"].prompt_version == "player-v1.10"
    assert captured["player_config"].temperature == 0.0
    assert captured["player_config"].max_tokens == 8000
    assert captured["player_config"].timeout == 300.0
    assert captured["player_config"].reasoning_effort == "low"
    assert captured["player_config"].guardrails.max_messages_per_activation == 2
    assert captured["player_config"].guardrails.max_llm_iterations == 24
    assert captured["player_config"].guardrails.max_tool_calls == 40
    assert captured["player_config"].guardrails.max_research_tool_calls == 5
    assert captured["player_config"].guardrails.max_messages_per_phase == 30
    assert captured["player_config"].guardrails.max_activations_per_opportunity == 12


def test_cli_run_accepts_bundled_scenario_name(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "named-scenario"
    scenario.mkdir()
    (scenario / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")

    with amaw_cli.conditions._resolve_scenario(str(scenario)) as resolved:
        assert resolved == scenario.resolve()


def test_cli_run_rejects_missing_scenario(tmp_path: Path) -> None:
    result = CliRunner().invoke(amaw_cli.app, ["run", str(tmp_path / "missing")])

    assert result.exit_code == 2
    assert "invalid value" in result.output.lower()


def test_cli_run_requires_openrouter_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _scenario_package(tmp_path)
    from app.config import Settings

    monkeypatch.setattr(
        amaw_cli.conditions,
        "_cli_settings_with_admin_token",
        lambda: (Settings(_env_file=None), "cli-test-token"),
    )

    result = CliRunner().invoke(amaw_cli.app, ["run", str(package)])

    assert result.exit_code == 2
    assert "OPENROUTER_API_KEY" in result.output


def test_i028_012_cli_concurrency_defaults_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    package = _scenario_package(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    monkeypatch.setenv("AMAW_CONS_CONCURRENCY", "3")
    monkeypatch.setenv("AMAW_DIPLOMACY_CONCURRENCY", "2")
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)
    captured: dict[str, object] = {}

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return cls()

    class FakeRunner:
        def __init__(self, client, **kwargs):  # noqa: ANN001
            captured["runner_options"] = kwargs
            self.last_game_id = None

        def run(self):
            return SimpleNamespace(game_id="completed-game", export={})

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
            captured["player_config"] = config
            return cls()

    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    monkeypatch.setenv("AMAW_PLAYER_ACTIVATION_MODE", "tool_loop")
    monkeypatch.setenv("AMAW_MAX_MESSAGES_PER_ACTIVATION", "1")

    result = CliRunner().invoke(amaw_cli.app, ["run", str(package)])

    assert result.exit_code == 0, result.output
    assert captured["runner_options"]["cons_concurrency"] == 3
    assert captured["runner_options"]["diplomacy_concurrency"] == 2
    captured["runner_options"]["player_factory"](object(), "arkania", "Arkania")
    assert captured["player_config"].activation_mode == "tool_loop"
    assert captured["player_config"].guardrails.max_messages_per_activation == 1
