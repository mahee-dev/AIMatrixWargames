"""Shared benchmark test support: the short-game fixture, one offline batch harness, table readers."""

from __future__ import annotations

import csv
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

# The five raw tables benchmark-methodology §25 names, spelled out independently of
# ``benchmark.outputs`` so a table the writer drops fails the read.
TABLES = ("games", "agents", "arguments", "messages", "resource_access")


def read_tables(directory: Path) -> dict[str, tuple[list[str], list[dict[str, str]]]]:
    """Each raw table as ``(header, rows)``, keyed by table name."""
    tables = {}
    for name in TABLES:
        with (directory / f"{name}.csv").open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            tables[name] = (list(reader.fieldnames or []), list(reader))
    return tables


# --- The short game: a turn-limited copy of the canonical fixture --------------------------

# Bookkeeping tests (manifests, ``batch.json``, per-activation records) need a completed game,
# not six turns of one. Two turns keep a turn boundary and cost a third of the canonical game.
SHORT_TURNS = 2


def derive_short_fixture(destination: Path, *, turns: int = SHORT_TURNS) -> Path:
    """Copy the canonical fixture to ``destination`` with ``max_turns`` lowered to ``turns``.

    The canonical fixture itself is never edited (testing.md §4); §18/§19 keep playing it.
    """
    from tests.api_helpers import FIXTURE_DIR

    shutil.copytree(FIXTURE_DIR, destination)
    manifest = destination / "scenario.yaml"
    text = manifest.read_text(encoding="utf-8")
    assert "  max_turns: 6\n" in text, "the canonical fixture no longer declares six turns"
    manifest.write_text(text.replace("  max_turns: 6\n", f"  max_turns: {turns}\n"), encoding="utf-8")
    return destination


# --- One offline batch harness: a real ``run_batch``, only the models faked ------------------

ADMIN = "admin-secret-token"


def offline_batch(
    db_engine: Any,
    tmp_path: Path,
    *,
    scenario: Path,
    models: Sequence[str],
    rotate: bool = False,
    rotation: str | None = None,
    repetitions: int = 1,
    base_seed: int = 500000,
    scheduling_seed: int = 11,
    experiment_id: str = "controls-1",
    experiment: dict | None = None,
    benchmark_mode: str | None = None,
    analysis_plan: Any = None,
    umpire: Any = None,
    consequence_resolver: Any = None,
    controls: Any = None,
    run_configuration: dict | None = None,
    executor_options: dict | None = None,
    umpire_for_attempt: Callable | None = None,
    resolver_for_attempt: Callable | None = None,
    config_for_attempt: Callable | None = None,
    attempt_for_execution: Callable | None = None,
    export_for_attempt: Callable | None = None,
    fail_position: int | None = None,
) -> tuple[Any, Any, BaseException | None]:
    """Run a real batch through ``run_batch`` and return ``(schedule, result, outcome)``.

    Players are ``tool_loop`` ``ReferencePlayer``s on ``FunctionModel``s behind a ``TestClient``;
    the Umpire is deterministic unless ``umpire`` is given. The batch writes to
    ``tmp_path / "batch"``. ``outcome`` is the exception ``run_batch`` raised (an integrity
    failure), or ``None``; ``result`` is then the partial result it carried.

    Hooks, each applied per attempt:

    - ``controls`` are the batch controls, or ``controls(schedule, prototype, run_configuration)``
      declaring them once the schedule exists (I-078: the expected condition precedes game 1).
    - ``umpire_for_attempt(attempt)`` / ``resolver_for_attempt(attempt)`` may swap the
      application's Umpire or consequence resolver: the injected drift the controls must catch.
    - ``config_for_attempt(attempt, config)`` may change a player's configuration.
    - ``attempt_for_execution(attempt)`` may hand the executor a different attempt than the one
      scheduled, and ``export_for_attempt(attempt, export)`` may alter what it returns.
    - ``fail_position`` raises before game creation at that schedule position.
    """
    from fastapi.testclient import TestClient

    from agents.lifecycle import LifecycleRunner
    from agents.player import PlayerConfig, ReferencePlayer
    from agents.scripted import deterministic_umpire
    from app.api import create_app
    from app.config import Settings
    from app.scenarios import load_scenario
    from benchmark.runner import autonomous_executor, build_schedule, run_batch
    from tests.agent_fakes import default_policy, model_from_policy

    current: dict = {"attempt": None}

    def player_factory(tools: Any, actor_id: str, actor_name: str, model: str) -> Any:
        config = PlayerConfig(model=model, activation_mode="tool_loop")
        if config_for_attempt is not None:
            config = config_for_attempt(current["attempt"], config)
        player = ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_policy(default_policy), config=config,
        )
        return LifecycleRunner(player)

    schedule = build_schedule(
        actor_ids=list(load_scenario(scenario).game.turn_order), models=list(models),
        rotate=rotate, rotation=rotation, repetitions=repetitions, base_seed=base_seed,
        scheduling_seed=scheduling_seed, experiment_id=experiment_id,
    )
    if callable(controls):
        prototype = PlayerConfig(model=list(models)[0], activation_mode="tool_loop")
        controls = controls(schedule, prototype, run_configuration)
    application = create_app(
        engine=db_engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None),
        umpire=umpire or deterministic_umpire(), consequence_resolver=consequence_resolver,
    )
    outcome: BaseException | None = None
    with TestClient(application, raise_server_exceptions=False) as api:
        real = autonomous_executor(
            api, admin_token=ADMIN, scenario=scenario, player_factory=player_factory,
            benchmark_mode=benchmark_mode, run_configuration=run_configuration,
            analysis_plan=analysis_plan, **(executor_options or {}),
        )

        def execute(attempt: Any) -> Any:
            current["attempt"] = attempt
            if fail_position is not None and attempt.position == fail_position:
                raise RuntimeError("injected before game creation")
            if umpire_for_attempt is not None and (swap := umpire_for_attempt(attempt)) is not None:
                application.state.umpire = swap
            if resolver_for_attempt is not None and (swap := resolver_for_attempt(attempt)) is not None:
                application.state.consequence_resolver = swap
            executed = attempt_for_execution(attempt) if attempt_for_execution else attempt
            export = real(executed)
            return export_for_attempt(attempt, export) if export_for_attempt else export

        try:
            result = run_batch(
                schedule, execute=execute, output_dir=tmp_path / "batch",
                experiment=experiment or {"name": "controls", "scenario_id": "fixture"},
                controls=controls,
            )
        except Exception as exc:  # noqa: BLE001 - the caller inspects integrity failures
            outcome = exc
            result = getattr(exc, "result", None)
    return schedule, result, outcome


# --- ``amaw benchmark`` with the providers and ``run_batch`` faked ---------------------------


def invoke_benchmark(
    monkeypatch: Any,
    tmp_path: Path,
    scenario: Path,
    args: Sequence[str],
    *,
    output_dir: Path | None = None,
    env: dict[str, str] | None = None,
    integrity_failure: bool = False,
) -> tuple[Any, dict]:
    """Run ``amaw benchmark scenario --output-dir … *args`` and capture what it composed.

    The Umpire, the consequence resolver and the players are faked at their OpenRouter
    constructors, and ``run_batch`` records its inputs instead of playing. ``captured`` holds
    ``umpire_options``, ``resolver_options``, ``player_configs``, ``app_options`` and the
    ``run_batch`` inputs (``schedule``, ``execute``, ``output_dir``, ``experiment``,
    ``secret_values``, ``controls``). ``integrity_failure`` makes ``run_batch`` raise
    ``BatchIntegrityError``.
    """
    from typer.testing import CliRunner

    import amaw_cli
    from app.api import create_app as real_create_app

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    for name in ("AMAW_ADMIN_TOKEN", "AMAW_CONSEQUENCE_MODE", "AMAW_CONSEQUENCE_MODEL"):
        monkeypatch.delenv(name, raising=False)
    for name, value in (env or {}).items():
        monkeypatch.setenv(name, value)
    captured: dict = {}

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings: Any, **kwargs: Any) -> FakeUmpire:
            captured["umpire_options"] = kwargs
            return cls()

        def provenance_configuration(self) -> dict:
            options = captured["umpire_options"]
            return {
                "model": options["model"], "provider": "openrouter",
                "prompt_version": "umpire-test", "prompt_hash": "h" * 64,
                "settings": {
                    "temperature": options.get("temperature"),
                    "max_tokens": options.get("max_tokens"),
                    "timeout": options.get("timeout"),
                    "reasoning": (
                        {"effort": options["reasoning_effort"]}
                        if options.get("reasoning_effort") else None
                    ),
                    "provider": {"require_parameters": True},
                },
                "adjudication_schema": {"name": "amaw_adjudication_proposal", "sha256": "s" * 64},
            }

    class FakeResolver:
        @classmethod
        def for_openrouter(cls, settings: Any, **kwargs: Any) -> FakeResolver:
            captured["resolver_options"] = kwargs
            return cls()

        def provenance_configuration(self) -> dict:
            options = captured["resolver_options"]
            return {"model": options["model"], "provider": "openrouter",
                    "prompt_version": options["prompt_version"], "prompt_hash": "c" * 64,
                    "settings": {"timeout": options["timeout"]}, "consequence_schema": None}

    class FakePlayer:
        @classmethod
        def for_openrouter(
            cls, tools: Any, *, actor_id: str, actor_name: str, config: Any, settings: Any,
            prompt: Any = None,
        ) -> FakePlayer:
            captured.setdefault("player_configs", []).append((actor_id, config))
            return cls()

    def fake_run_batch(
        schedule: Any, *, execute: Any, output_dir: Path, experiment: dict,
        secret_values: Sequence[str] = (), controls: Any = None,
    ) -> Any:
        from benchmark.runner import BatchIntegrityError, BatchResult

        captured.update(
            schedule=schedule, execute=execute, output_dir=output_dir, experiment=experiment,
            secret_values=list(secret_values), controls=controls,
        )
        result = BatchResult(output_dir=output_dir, schedule=schedule, attempts=())
        if integrity_failure:
            raise BatchIntegrityError("experiment integrity failure: umpire_condition drifted", result)
        return result

    def fake_create_app(**kwargs: Any) -> Any:
        captured["app_options"] = kwargs
        return real_create_app(**kwargs)

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.consequence.AIConsequenceResolver", FakeResolver)
    monkeypatch.setattr("agents.player.ReferencePlayer", FakePlayer)
    monkeypatch.setattr("benchmark.runner.run_batch", fake_run_batch)
    monkeypatch.setattr("app.api.create_app", fake_create_app)
    result = CliRunner().invoke(amaw_cli.app, [
        "benchmark", str(scenario), "--output-dir", str(output_dir or tmp_path / "out"), *args,
    ])
    return result, captured
