"""I-118 — a scenario declares its recommended Diplomacy and Con conditions (D-099).

``diplomacy_mode``/``diplomacy_rounds_per_turn`` and ``con_mode``/``con_challengers_per_argument``
join ``consequence_mode`` as optional ``game.*`` scenario defaults. Every entry point resolves the
condition with the one resolver in ``app.game_loop``: an explicit creation-time value, then the
scenario's default, then the released default. The resolved ``GameLoopCondition`` is what is
persisted, sealed and compared on resume. See I-118 in ``dev/BACKLOG_RESOLVED.md``.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from typer.testing import CliRunner

import amaw_cli
from amaw_contracts import canonical_json
from app import resumption, scenarios
from app.api import create_app
from app.config import Settings
from app.database import session_scope
from app.game_loop import (
    GameLoopCondition,
    GameLoopConditionLayer,
    InvalidGameLoopCondition,
    resolve_game_loop_condition,
)
from app.models import Game, GameStatus, RunManifest
from tests.api_helpers import FIXTURE_DIR

REPO = Path(__file__).resolve().parents[1]
ADMIN = "i118-admin"
RELEASED = GameLoopCondition()
EMPTY = GameLoopConditionLayer()

# The cheaper condition a large scenario would recommend (skaro-crisis README, T-083).
SCENARIO_LINES = (
    "  diplomacy_mode: bounded_rounds\n"
    "  diplomacy_rounds_per_turn: 3\n"
    "  con_mode: sampled_sealed\n"
    "  con_challengers_per_argument: 1\n"
)
SCENARIO_LAYER = GameLoopConditionLayer(
    diplomacy_mode="bounded_rounds", diplomacy_rounds_per_turn=3,
    con_mode="sampled_sealed", con_challengers_per_argument=1,
)


def _declared(tmp_path: Path, lines: str = SCENARIO_LINES, name: str = "declared") -> Path:
    package = tmp_path / name
    shutil.copytree(FIXTURE_DIR, package)
    manifest = package / "scenario.yaml"
    text = manifest.read_text(encoding="utf-8")
    anchor = "  max_cons_per_actor: 3\n"
    assert anchor in text
    manifest.write_text(text.replace(anchor, anchor + lines), encoding="utf-8")
    return package


# --- TEST-I118-001: the precedence matrix, per field ---------------------------------------


@pytest.mark.parametrize(
    ("explicit", "scenario", "expected"),
    [
        # A. released default only
        (EMPTY, EMPTY, RELEASED),
        # B. scenario default
        (EMPTY, SCENARIO_LAYER, GameLoopCondition(
            diplomacy_mode="bounded_rounds", diplomacy_rounds_per_turn=3,
            con_mode="sampled_sealed", con_challengers_per_argument=1,
        )),
        # C. explicit override of every field
        (
            GameLoopConditionLayer(
                diplomacy_mode="bounded_rounds", diplomacy_rounds_per_turn=5,
                con_mode="sampled_sealed", con_challengers_per_argument=2,
            ),
            SCENARIO_LAYER,
            GameLoopCondition(
                diplomacy_mode="bounded_rounds", diplomacy_rounds_per_turn=5,
                con_mode="sampled_sealed", con_challengers_per_argument=2,
            ),
        ),
        # E. mixed: explicit Diplomacy mode, scenario Con mode, released Con count is replaced
        # by the scenario's because the resolved mode is the scenario's.
        (
            GameLoopConditionLayer(diplomacy_mode="open_diplomacy"),
            SCENARIO_LAYER,
            GameLoopCondition(con_mode="sampled_sealed", con_challengers_per_argument=1),
        ),
    ],
    ids=["A-released", "B-scenario", "C-explicit", "E-mixed"],
)
def test_i118_001_precedence_matrix(
    explicit: GameLoopConditionLayer, scenario: GameLoopConditionLayer, expected: GameLoopCondition
) -> None:
    resolved = resolve_game_loop_condition(explicit, scenario)
    assert resolved == expected
    resolved.validate()
    # Deterministic and side-effect free: the same inputs give the same value, inputs untouched.
    assert resolve_game_loop_condition(explicit, scenario) == resolved
    assert scenario == SCENARIO_LAYER or scenario == EMPTY


@pytest.mark.parametrize(
    ("field", "explicit_value", "scenario_value", "released_value"),
    [
        ("consequence_mode", "none", "bounded_effects_v1", "none"),
        ("diplomacy_mode", "open_diplomacy", "bounded_rounds", "open_diplomacy"),
        ("con_mode", "open_cons", "sampled_sealed", "open_cons"),
    ],
)
def test_i118_001_each_mode_field_resolves_explicit_then_scenario_then_released(
    field: str, explicit_value: str, scenario_value: str, released_value: str
) -> None:
    counts = {
        "diplomacy_mode": {"diplomacy_rounds_per_turn": 2},
        "con_mode": {"con_challengers_per_argument": 1},
    }.get(field, {})
    scenario = GameLoopConditionLayer(**{field: scenario_value, **counts})
    explicit = GameLoopConditionLayer(**{field: explicit_value})
    assert getattr(resolve_game_loop_condition(EMPTY, EMPTY), field) == released_value
    assert getattr(resolve_game_loop_condition(EMPTY, scenario), field) == scenario_value
    assert getattr(resolve_game_loop_condition(explicit, scenario), field) == explicit_value
    # An explicit value equal to the released default still wins: no truthiness shortcut.
    assert getattr(resolve_game_loop_condition(explicit, scenario), field) != scenario_value


def test_i118_002_a_count_follows_its_mode() -> None:
    """A count that is not explicit is inherited only from a layer whose mode was resolved."""
    # Explicit open over a bounded scenario: open, and no scenario bound leaks in.
    reverted = resolve_game_loop_condition(
        GameLoopConditionLayer(diplomacy_mode="open_diplomacy", con_mode="open_cons"),
        SCENARIO_LAYER,
    )
    assert reverted == RELEASED
    reverted.validate()
    # Explicit count only: the scenario's mode, the caller's count.
    counted = resolve_game_loop_condition(
        GameLoopConditionLayer(diplomacy_rounds_per_turn=6, con_challengers_per_argument=2),
        SCENARIO_LAYER,
    )
    assert (counted.diplomacy_mode, counted.diplomacy_rounds_per_turn) == ("bounded_rounds", 6)
    assert (counted.con_mode, counted.con_challengers_per_argument) == ("sampled_sealed", 2)
    # Explicit mode equal to the scenario's: the scenario's count is inherited.
    same = resolve_game_loop_condition(
        GameLoopConditionLayer(diplomacy_mode="bounded_rounds"), SCENARIO_LAYER
    )
    assert same.diplomacy_rounds_per_turn == 3
    # Without a scenario the pairing rules refuse exactly as before.
    with pytest.raises(InvalidGameLoopCondition):
        resolve_game_loop_condition(GameLoopConditionLayer(diplomacy_rounds_per_turn=2)).validate()
    with pytest.raises(InvalidGameLoopCondition):
        resolve_game_loop_condition(GameLoopConditionLayer(con_mode="sampled_sealed")).validate()
    # Zero is an explicit value, refused by the validator, never read as "omitted".
    zero = resolve_game_loop_condition(
        GameLoopConditionLayer(diplomacy_rounds_per_turn=0), SCENARIO_LAYER
    )
    assert zero.diplomacy_rounds_per_turn == 0
    with pytest.raises(InvalidGameLoopCondition):
        zero.validate()


def test_i118_003_layers_and_resolved_condition_are_frozen_values() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        SCENARIO_LAYER.con_mode = "open_cons"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        RELEASED.con_mode = "sampled_sealed"  # type: ignore[misc]
    assert {f.name for f in dataclasses.fields(GameLoopConditionLayer)} == {
        f.name for f in dataclasses.fields(GameLoopCondition)
    }


# --- TEST-I118-004: the scenario schema ----------------------------------------------------


def test_i118_004_scenario_declares_the_four_fields(tmp_path: Path) -> None:
    loaded = scenarios.load_scenario(_declared(tmp_path))
    # ``consequence_mode`` has always had a schema default of ``none``, the released value.
    assert loaded.game.condition_defaults() == dataclasses.replace(
        SCENARIO_LAYER, consequence_mode="none"
    )
    # D. a scenario without the fields declares nothing beyond the released consequence mode.
    legacy = scenarios.load_scenario(FIXTURE_DIR).game.condition_defaults()
    assert resolve_game_loop_condition(EMPTY, legacy) == RELEASED


@pytest.mark.parametrize(
    ("lines", "message"),
    [
        ("  diplomacy_mode: unbounded\n", "diplomacy_mode"),
        ("  diplomacy_mode: bounded_rounds\n", "diplomacy_rounds_per_turn"),
        ("  diplomacy_rounds_per_turn: 2\n", "diplomacy_rounds_per_turn"),
        ("  diplomacy_mode: bounded_rounds\n  diplomacy_rounds_per_turn: 0\n",
         "diplomacy_rounds_per_turn"),
        ("  con_mode: sampled_sealed\n", "con_challengers_per_argument"),
        ("  con_challengers_per_argument: 1\n", "con_challengers_per_argument"),
        # The fixture has three Actors, so two opponents per Argument.
        ("  con_mode: sampled_sealed\n  con_challengers_per_argument: 3\n", "opponents"),
    ],
)
def test_i118_004_scenario_rejects_an_illegal_declaration(
    tmp_path: Path, lines: str, message: str
) -> None:
    with pytest.raises(scenarios.ScenarioValidationError, match=message):
        scenarios.load_scenario(_declared(tmp_path, lines))


# --- TEST-I118-005: game creation and the admin API (G) ------------------------------------


def _game(engine: Engine, game_id: Any) -> Game:
    with session_scope(engine) as session:
        game = session.get(Game, uuid.UUID(str(game_id)))
        assert game is not None
        session.expunge(game)
        return game


def _condition(game: Game) -> tuple[Any, ...]:
    return (game.diplomacy_mode, game.diplomacy_rounds_per_turn,
            game.con_mode, game.con_challengers_per_argument)


def test_i118_005_creation_resolves_and_persists_the_effective_condition(
    db_engine: Engine, tmp_path: Path
) -> None:
    loaded = scenarios.load_scenario(_declared(tmp_path))
    declared = scenarios.create_game_from_scenario(db_engine, loaded)
    assert _condition(_game(db_engine, declared.game_id)) == ("bounded_rounds", 3, "sampled_sealed", 1)
    overridden = scenarios.create_game_from_scenario(
        db_engine, loaded, diplomacy_mode="open_diplomacy", con_challengers_per_argument=2,
    )
    assert _condition(_game(db_engine, overridden.game_id)) == ("open_diplomacy", None, "sampled_sealed", 2)
    legacy = scenarios.create_game_from_scenario(db_engine, scenarios.load_scenario(FIXTURE_DIR))
    assert _condition(_game(db_engine, legacy.game_id)) == ("open_diplomacy", None, "open_cons", None)


def test_i118_005_admin_api_omitted_fields_defer_to_the_scenario(
    db_engine: Engine, tmp_path: Path
) -> None:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN, _env_file=None)
    api = TestClient(create_app(engine=db_engine, settings=settings), raise_server_exceptions=False)
    admin = {"Authorization": f"Bearer {ADMIN}"}
    package = str(_declared(tmp_path))

    omitted = api.post("/admin/games", json={"scenario": package}, headers=admin)
    assert omitted.status_code == 201, omitted.text
    assert _condition(_game(db_engine, omitted.json()["game_id"])) == (
        "bounded_rounds", 3, "sampled_sealed", 1
    )
    # Explicit null is the same as omitted.
    nulls = api.post("/admin/games", json={
        "scenario": package, "diplomacy_mode": None, "con_mode": None,
        "diplomacy_rounds_per_turn": None, "con_challengers_per_argument": None,
    }, headers=admin)
    assert nulls.status_code == 201, nulls.text
    assert _condition(_game(db_engine, nulls.json()["game_id"])) == (
        "bounded_rounds", 3, "sampled_sealed", 1
    )
    explicit = api.post("/admin/games", json={
        "scenario": package, "diplomacy_mode": "open_diplomacy", "con_mode": "open_cons",
    }, headers=admin)
    assert explicit.status_code == 201, explicit.text
    assert _condition(_game(db_engine, explicit.json()["game_id"])) == (
        "open_diplomacy", None, "open_cons", None
    )
    # A legacy scenario keeps the released behaviour for a client that sends nothing new.
    legacy = api.post("/admin/games", json={"scenario": str(FIXTURE_DIR)}, headers=admin)
    assert legacy.status_code == 201, legacy.text
    assert _condition(_game(db_engine, legacy.json()["game_id"])) == (
        "open_diplomacy", None, "open_cons", None
    )
    # An explicit illegal value is still refused.
    bad = api.post("/admin/games", json={"scenario": package, "con_challengers_per_argument": 0},
                   headers=admin)
    assert bad.status_code in (400, 422)


# --- TEST-I118-006: amaw run and the sealed manifest (F, H) --------------------------------


def _environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    for name in list(os.environ):
        if name.startswith("AMAW_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))


def _run(monkeypatch: pytest.MonkeyPatch, argv: list[str], **describe: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    class FakeRunner:
        last_game_id = None

        def __init__(self, client, **kwargs):  # noqa: ANN001, ANN003
            captured["runner"] = kwargs

        def run(self):  # noqa: ANN202
            return SimpleNamespace(game_id="g", export={})

        def resume(self, game_id):  # noqa: ANN001, ANN202
            return SimpleNamespace(game_id=game_id, export={})

    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", FakeRunner)
    monkeypatch.setattr(
        "agents.umpire.AIUmpire.for_openrouter",
        classmethod(lambda cls, settings, **kwargs: SimpleNamespace()),
    )
    if describe:
        monkeypatch.setattr("app.resumption.describe", lambda engine, parsed: SimpleNamespace(
            manifest=describe["manifest"]
        ))
    result = CliRunner().invoke(amaw_cli.app, argv)
    assert result.exit_code == 0, result.output
    return captured


def _runner_condition(captured: dict[str, Any]) -> tuple[Any, ...]:
    runner = captured["runner"]
    coordinator = runner["run_configuration"]["resume"]["coordinator"]
    declared = tuple(runner.get(name) for name in (
        "diplomacy_mode", "diplomacy_rounds_per_turn", "con_mode", "con_challengers_per_argument",
    ))
    sealed = tuple(coordinator.get(name) for name in (
        "diplomacy_mode", "diplomacy_rounds_per_turn", "con_mode", "con_challengers_per_argument",
    ))
    assert declared == sealed  # the runner declares exactly what the manifest seals
    return declared


def test_i118_006_cli_run_omitted_flags_take_the_scenario_default(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    package = str(_declared(tmp_path))
    base = ["run", package, "--player-model", "p/p", "--umpire-model", "u/u", "--seed", "1"]

    captured = _run(monkeypatch, base)
    assert _runner_condition(captured) == ("bounded_rounds", 3, "sampled_sealed", 1)

    captured = _run(monkeypatch, [*base, "--diplomacy-mode", "open_diplomacy",
                                  "--con-challengers-per-argument", "2"])
    assert _runner_condition(captured) == ("open_diplomacy", None, "sampled_sealed", 2)

    # An AMAW_* value that is actually set is an explicit operator value, like a flag.
    monkeypatch.setenv("AMAW_CON_MODE", "open_cons")
    captured = _run(monkeypatch, base)
    assert _runner_condition(captured) == ("bounded_rounds", 3, "open_cons", None)
    monkeypatch.delenv("AMAW_CON_MODE")

    # D. a legacy scenario: the released condition, exactly as before.
    captured = _run(monkeypatch, ["run", str(FIXTURE_DIR), *base[2:]])
    assert _runner_condition(captured) == ("open_diplomacy", None, "open_cons", None)


def test_i118_006_manifest_configuration_carries_the_effective_values() -> None:
    from agents.autonomous import expected_manifest_configuration
    from agents.player import PlayerConfig

    condition = resolve_game_loop_condition(EMPTY, SCENARIO_LAYER)
    configuration = expected_manifest_configuration(
        PlayerConfig(model="p/p"), condition=condition
    )
    assert configuration["game_loop"] == condition.provenance()
    assert configuration["game_loop"]["diplomacy_rounds_per_turn"] == 3
    assert configuration["game_loop"]["con_challengers_per_argument"] == 1


# --- TEST-I118-007: resume compares the effective condition (I) ----------------------------


def _recipe(**coordinator: Any) -> dict[str, Any]:
    from agents.guardrails import GuardrailConfig

    return {
        "schema": "autonomous-resume-v1",
        "player": {
            "assignments": {"arkania": "m/p", "belvar": "m/p", "cestia": "m/p"},
            "temperature": 0, "max_tokens": 1000, "timeout": 30, "reasoning_effort": None,
            "activation_mode": "single_shot", "prompt_version": "player-v1.6",
            "guardrails": dataclasses.asdict(GuardrailConfig()),
        },
        "umpire": {"model": "m/u", "temperature": 0, "max_tokens": 1000, "timeout": 30,
                   "reasoning_effort": None},
        "coordinator": {"max_cycles": 1000, "cons_concurrency": 2, "diplomacy_concurrency": 2,
                        **coordinator},
    }


def _running(engine: Engine, creation: scenarios.GameCreation, recipe: dict[str, Any]) -> uuid.UUID:
    game_id = uuid.UUID(str(creation.game_id))
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        assert game is not None
        game.status = GameStatus.running
        game.turn = 1
        session.add(game)
        session.add(RunManifest(
            game_id=game_id, manifest={"game_id": str(game_id), "resume": recipe},
            manifest_sha256="a" * 64,
        ))
    return game_id


def test_i118_007_resume_compares_the_effective_condition(
    db_engine: Engine, tmp_path: Path
) -> None:
    loaded = scenarios.load_scenario(_declared(tmp_path))
    effective = resolve_game_loop_condition(EMPTY, loaded.game.condition_defaults())
    sealed = _recipe(**effective.as_dict())

    # Unchanged: a game whose condition came from the scenario resumes under the same values.
    same = _running(db_engine, scenarios.create_game_from_scenario(db_engine, loaded), sealed)
    assert resumption.describe(db_engine, same).manifest["resume"] is not None

    # Drift: a recipe restating the released condition is refused for the scenario-default game.
    drifted = _running(
        db_engine, scenarios.create_game_from_scenario(db_engine, loaded), _recipe()
    )
    with pytest.raises(resumption.ResumeError, match="condition"):
        resumption.describe(db_engine, drifted)

    # Legacy: a game created under the released condition, whose recipe carries no condition
    # key, is never reinterpreted under the defaults its scenario declares today.
    legacy = _running(
        db_engine,
        scenarios.create_game_from_scenario(
            db_engine, loaded, diplomacy_mode="open_diplomacy", con_mode="open_cons",
        ),
        _recipe(),
    )
    assert resumption.describe(db_engine, legacy).game_id == legacy


def test_i118_007_cli_resume_restores_the_sealed_condition_not_the_scenario(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    legacy = _recipe()  # a pre-I-118 recipe: no condition keys means the released condition
    runtime = {"request_timeout_seconds": 30.0, "max_cycles": 1000, "log_level": "INFO",
               "stream_game_details": False, "guardrails": legacy["player"]["guardrails"]}
    captured = _run(
        monkeypatch, ["run", "--resume-game", str(uuid.uuid4())],
        manifest={"resume": legacy, "runtime": runtime},
    )
    runner = captured["runner"]
    assert runner["run_configuration"]["resume"] is legacy
    assert (runner["diplomacy_mode"], runner["con_mode"]) == ("open_diplomacy", "open_cons")


# --- TEST-I118-008: benchmark sealing (J) --------------------------------------------------


def test_i118_008_benchmark_seals_the_scenario_derived_condition(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _environment(monkeypatch, tmp_path)
    captured: dict[str, Any] = {}

    def executor(client, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["executor"] = kwargs
        return "executor"

    def run_batch(schedule, **kwargs):  # noqa: ANN001, ANN003, ANN202
        captured["controls"] = kwargs["controls"]
        return SimpleNamespace(counts=lambda: {
            "total_games": 0, "completed_games": 0, "failed_games": 0, "not_run_games": 0,
        })

    monkeypatch.setattr("benchmark.runner.autonomous_executor", executor)
    monkeypatch.setattr("benchmark.runner.run_batch", run_batch)

    def invoke(package: Path, *extra: str) -> dict[str, Any]:
        captured.clear()
        result = CliRunner().invoke(amaw_cli.app, [
            "benchmark", str(package), "--models", "a/a", "--benchmark-mode", "agent",
            "--output-dir", str(tmp_path / f"batch-{uuid.uuid4().hex}"), "--experiment-id", "exp",
            "--base-seed", "1", "--scheduling-seed", "2", *extra,
        ])
        assert result.exit_code == 0, result.output
        return captured

    declared = _declared(tmp_path)
    scenario_run = invoke(declared)
    names = ("diplomacy_mode", "diplomacy_rounds_per_turn", "con_mode",
             "con_challengers_per_argument")
    kwargs = tuple(scenario_run["executor"][name] for name in names)
    assert kwargs == ("bounded_rounds", 3, "sampled_sealed", 1)
    expected_scenario = scenario_run["controls"].expected_condition
    coordinator = scenario_run["executor"]["run_configuration"]["resume"]["coordinator"]
    assert coordinator["diplomacy_rounds_per_turn"] == 3

    # The same effective condition spelled explicitly seals the same way.
    explicit_run = invoke(
        declared, "--diplomacy-mode", "bounded_rounds", "--diplomacy-rounds-per-turn", "3",
        "--con-mode", "sampled_sealed", "--con-challengers-per-argument", "1",
    )
    assert tuple(explicit_run["executor"][name] for name in names) == kwargs
    assert canonical_json(explicit_run["controls"].expected_condition.record()) == canonical_json(
        expected_scenario.record()
    )

    # A different effective condition never serialises as the same one.
    reverted = invoke(declared, "--diplomacy-mode", "open_diplomacy", "--con-mode", "open_cons")
    assert canonical_json(reverted["controls"].expected_condition.record()) != canonical_json(
        expected_scenario.record()
    )


# --- TEST-I118-009: serialisation identity for an unchanged effective condition (K) --------


def test_i118_009_released_condition_serialises_exactly_as_before() -> None:
    legacy = scenarios.load_scenario(FIXTURE_DIR).game.condition_defaults()
    resolved = resolve_game_loop_condition(EMPTY, legacy)
    assert canonical_json(resolved.as_dict()) == (
        b'{"con_challengers_per_argument":null,"con_mode":"open_cons",'
        b'"con_reply_mode":"sealed_cons","diplomacy_mode":"open_diplomacy",'
        b'"diplomacy_rounds_per_turn":null,"reply_max_points":null}'
    )
    assert canonical_json(resolved.provenance()) == canonical_json(RELEASED.provenance())
    # The explicit spelling of a scenario-declared condition is the identical value.
    explicit = resolve_game_loop_condition(SCENARIO_LAYER)
    assert resolve_game_loop_condition(EMPTY, SCENARIO_LAYER) == explicit
    assert canonical_json(explicit.provenance()) == canonical_json(
        resolve_game_loop_condition(EMPTY, SCENARIO_LAYER).provenance()
    )


# --- TEST-I118-010: the typed hand-off is checked -------------------------------------------


def test_i118_010_type_checker_rejects_a_malformed_condition_hand_off(tmp_path: Path) -> None:
    import subprocess
    import sys

    probe = tmp_path / "probe.py"
    probe.write_text(
        "from app.game_loop import GameLoopConditionLayer, resolve_game_loop_condition\n"
        "layer = GameLoopConditionLayer(diplomacy_rounds=3)\n"  # misspelled field
        "typed = GameLoopConditionLayer(con_challengers_per_argument='two')\n"  # wrong type
        "resolved = resolve_game_loop_condition({'con_mode': 'open_cons'})\n"  # a mapping
        "resolved.diplomacy_round_limit\n",  # misspelled attribute
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--follow-imports=silent", str(probe)],
        cwd=REPO, capture_output=True, text=True, check=False,
    )
    output = result.stdout
    assert result.returncode == 1, output
    assert 'Unexpected keyword argument "diplomacy_rounds"' in output
    assert 'incompatible type "str"; expected "int | None"' in output
    assert '"resolve_game_loop_condition" has incompatible type "dict[str, str]"' in output
    assert '"GameLoopCondition" has no attribute "diplomacy_round_limit"' in output
