"""Offline harness for ``amaw run`` composition tests: fake the provider boundary, capture options.

``capture_run_cli`` isolates the CLI from the environment and replaces the AI Umpire, the
Consequence resolver and the autonomous runner (and, on request, the reference player) with fakes
that record the options the CLI composed. Nothing reaches a provider or plays a game.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

import amaw_cli

# Prefixes of the variables ``Settings`` reads; a developer shell or ``.env`` may set any of them.
_SETTINGS_ENV_PREFIXES = ("AMAW_", "OPENROUTER_")


def isolate_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make ``Settings()`` resolve to the released defaults for the rest of the test.

    Stops ``Settings`` reading the repository ``.env`` and removes every ``AMAW_*`` and
    ``OPENROUTER_*`` variable, so a developer's key, model overrides or conditions cannot change
    what the CLI composes. Tests set what they need afterwards.
    """
    from app.config import Settings

    monkeypatch.setitem(Settings.model_config, "env_file", None)
    for name in list(os.environ):
        if name.startswith(_SETTINGS_ENV_PREFIXES):
            monkeypatch.delenv(name)


def capture_run_cli(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, fake_player: bool = False
) -> dict:
    """Fake the boundary for one ``amaw run``; the returned dict fills in as the CLI composes.

    Keys: ``umpire_options``, ``resolver_options``, ``runner_options`` and, with
    ``fake_player``, ``player_config``.
    """
    isolate_settings(monkeypatch)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    captured: dict = {}

    class FakeAIUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            captured["umpire_options"] = kwargs
            return cls()

    class FakeResolver:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            captured["resolver_options"] = kwargs
            return cls()

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001
            captured["runner_options"] = kwargs

        def run(self):
            return SimpleNamespace(game_id="g", export={"game": {"id": "g"}})

    class FakePlayer:
        @classmethod
        def for_openrouter(cls, tools, *, actor_id, actor_name, config, settings, prompt=None):  # noqa: ANN001
            captured["player_config"] = config
            return cls()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeAIUmpire)
    monkeypatch.setattr("agents.consequence.AIConsequenceResolver", FakeResolver)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    if fake_player:
        monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    return captured


def invoke_run(package: Path, *extra: str):
    """``amaw run <package>`` with fixed placeholder models and seed, plus ``extra`` flags."""
    return CliRunner().invoke(
        amaw_cli.app,
        ["run", str(package), "--player-model", "p/m", "--umpire-model", "u/m", "--seed", "1",
         *extra],
    )
