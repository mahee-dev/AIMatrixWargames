"""I-083 — canonical execution-condition projections instead of drifting field lists.

One game's players share every field of :class:`PlayerConfig` but the model
(benchmark-methodology.md §5.1, agents.md §19); the runner-derived manifest sections cannot be
replaced by a caller (TRD §28); a supplied resume recipe must describe the players it is sealed
with; the server's resume validator reads the same ``autonomous-resume-v1`` vocabulary the
builder writes (``amaw_contracts``, TRD §4); and a sealed inference profile is validated
structurally before the reserved ``uniform`` name is honoured.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.guardrails import GuardrailConfig
from agents.lifecycle import LifecycleRunner
from agents.player import PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app.api import create_app
from app.config import Settings
from tests.agent_fakes import default_policy, model_from_policy
from tests.api_helpers import ACTORS

ADMIN_TOKEN = "admin-secret-token"


def _coordinator(engine, **kwargs) -> AutonomousGameRunner:  # noqa: ANN001
    client = TestClient(
        create_app(engine=engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        client, admin_token=ADMIN_TOKEN, scenario="tests/fixtures/deterministic-game",
        player_factory=lambda *_args: None, **kwargs,
    )


def _runner(actor_id: str, config: PlayerConfig) -> LifecycleRunner:
    tools = PlayerTools(SimpleNamespace(), game_id="game", token="token")
    player = ReferencePlayer(
        tools, actor_id=actor_id, actor_name=actor_id.title(),
        model=model_from_policy(default_policy), config=config,
    )
    return LifecycleRunner(player)


def _roster(*configs: PlayerConfig) -> dict[str, LifecycleRunner]:
    return {actor: _runner(actor, config) for actor, config in zip(ACTORS, configs, strict=True)}


# --- TEST-I083-001: roster uniformity over the complete non-model projection -----------------


def test_i083_001_shared_condition_is_every_field_but_the_model() -> None:
    config = PlayerConfig(model="fake/a", temperature=0.2)
    projection = config.shared_condition()
    assert set(projection) == {f.name for f in dataclasses.fields(PlayerConfig)} - {"model"}
    assert projection["temperature"] == 0.2 and projection["guardrails"] == config.guardrails
    # The model is the one thing a Model Benchmark varies; everything else must agree.
    assert projection == dataclasses.replace(config, model="fake/b").shared_condition()


@pytest.mark.parametrize(
    "field, other",
    [
        ("temperature", 0.7),
        ("timeout", 12.5),
        ("reasoning_effort", "high"),
        ("guardrails", GuardrailConfig(max_tool_calls=3)),
    ],
)
def test_i083_001_a_roster_differing_in_any_shared_field_is_refused(
    db_engine, field: str, other: object
) -> None:
    base = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    odd = dataclasses.replace(base, model="fake/b", **{field: other})
    coordinator = _coordinator(db_engine)

    with pytest.raises(AutonomousGameError, match=f"do not share one player condition.*{field}"):
        coordinator._manifest_configuration(_roster(base, odd, base))

    # Differing only in the model is the benchmark's normal case and still seals.
    configuration = coordinator._manifest_configuration(
        _roster(base, dataclasses.replace(base, model="fake/b"), base)
    )
    assert configuration["player"]["assignments"] == {
        ACTORS[0]: "fake/a", ACTORS[1]: "fake/b", ACTORS[2]: "fake/a"
    }
    assert configuration["resume"]["player"]["temperature"] == base.temperature


def test_i083_001_con_reply_condition_of_the_game_is_still_checked(db_engine) -> None:
    visible = PlayerConfig(model="fake/a", activation_mode="tool_loop", con_reply_mode="visible_reply")
    coordinator = _coordinator(db_engine)  # the game runs sealed_cons
    with pytest.raises(AutonomousGameError, match="con_reply_mode"):
        coordinator._manifest_configuration(_roster(visible, visible, visible))


# --- TEST-I083-002: the runner-derived manifest sections are reserved ------------------------


@pytest.mark.parametrize(
    "key", ["player", "code_version", "environment", "coordinator", "game_loop"]
)
def test_i083_002_a_caller_cannot_replace_a_runner_derived_section(db_engine, key: str) -> None:
    from agents.autonomous import expected_manifest_configuration

    config = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    claimed = {key: {"claimed": True} if key != "code_version" else "claimed"}

    coordinator = _coordinator(db_engine, run_configuration={"runtime": {"host": "x"}, **claimed})
    with pytest.raises(AutonomousGameError, match=f"reserved.*{key}"):
        coordinator._manifest_configuration(_roster(config, config, config))
    with pytest.raises(AutonomousGameError, match=f"reserved.*{key}"):
        expected_manifest_configuration(config, run_configuration=claimed)


def test_i083_002_runtime_passes_through_and_benchmark_is_merged_field_wise(db_engine) -> None:
    config = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    coordinator = _coordinator(
        db_engine,
        run_configuration={
            "runtime": {"host": "x"},
            "benchmark": {"mode": "model", "assignment_id": "a1", "repetition_index": 0},
        },
    )
    configuration = coordinator._manifest_configuration(_roster(config, config, config))
    assert configuration["runtime"] == {"host": "x"}
    assert configuration["benchmark"] == {
        "mode": "model", "assignment_id": "a1", "repetition_index": 0,
        "run_class": "playtest", "analysis_plan": None,
    }
    assert configuration["code_version"] and configuration["game_loop"]["con_mode"] == "open_cons"


# --- TEST-I083-003: a supplied resume recipe must describe the players it is sealed with -----


def _recipe(config: PlayerConfig, **coordinator) -> dict:
    from agents.autonomous import (
        DEFAULT_CONS_CONCURRENCY, DEFAULT_DIPLOMACY_CONCURRENCY, build_resume_recipe,
    )
    from app.game_loop import GameLoopCondition

    return build_resume_recipe(
        config, assignments={}, max_cycles=coordinator.pop("max_cycles", 1000),
        cons_concurrency=coordinator.pop("cons_concurrency", DEFAULT_CONS_CONCURRENCY),
        diplomacy_concurrency=coordinator.pop(
            "diplomacy_concurrency", DEFAULT_DIPLOMACY_CONCURRENCY
        ),
        condition=GameLoopCondition(**coordinator),
        umpire={"model": "fake/umpire", "temperature": None, "max_tokens": None,
                "timeout": None, "reasoning_effort": None},
    )


def test_i083_003_a_recipe_describing_other_players_or_another_loop_is_refused(db_engine) -> None:
    base = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    roster = _roster(base, base, base)

    other_players = _recipe(dataclasses.replace(base, temperature=0.9))
    with pytest.raises(AutonomousGameError, match="resume recipe.*temperature"):
        _coordinator(db_engine, run_configuration={"resume": other_players}) \
            ._manifest_configuration(roster)

    other_coordinator = _recipe(base, cons_concurrency=1)
    with pytest.raises(AutonomousGameError, match="resume recipe.*cons_concurrency"):
        _coordinator(db_engine, run_configuration={"resume": other_coordinator}) \
            ._manifest_configuration(roster)

    other_loop = _recipe(base, con_mode="sampled_sealed", con_challengers_per_argument=1)
    with pytest.raises(AutonomousGameError, match="resume recipe.*con_mode"):
        _coordinator(db_engine, run_configuration={"resume": other_loop}) \
            ._manifest_configuration(roster)

    with pytest.raises(AutonomousGameError, match="resume recipe"):
        _coordinator(db_engine, run_configuration={"resume": {"schema": "autonomous-resume-v1"}}) \
            ._manifest_configuration(roster)


def test_i083_003_a_legacy_recipe_resolving_to_the_players_condition_is_sealed_as_given(
    db_engine,
) -> None:
    # A pre-condition recipe resolves to the released defaults *of its time*: sequential/full
    # and, since I-112 moved the released default to `search-v2`, `search-v1` (D-088).
    base = PlayerConfig(model="fake/a", activation_mode="tool_loop", search_contract="search-v1")
    legacy = _recipe(base)
    for key in ("research_mode", "research_history_mode", "search_contract"):
        del legacy["player"][key]
    del legacy["player"]["guardrails"]["max_research_tool_calls"]
    del legacy["inference_profile"]
    # A pre-condition recipe carries no game-loop keys either (testing.md §26/§27/§29).
    legacy["coordinator"] = {
        key: legacy["coordinator"][key]
        for key in ("max_cycles", "cons_concurrency", "diplomacy_concurrency")
    }

    configuration = _coordinator(db_engine, run_configuration={"resume": legacy}) \
        ._manifest_configuration(_roster(base, base, base))
    sealed = configuration["resume"]
    assert sealed["player"]["assignments"] == {actor: "fake/a" for actor in ACTORS}
    assert {**sealed, "player": {**sealed["player"], "assignments": {}}} == legacy

    # The exact recipe the CLI seals for a fresh game passes untouched.
    current = _recipe(base)
    assert _coordinator(db_engine, run_configuration={"resume": current}) \
        ._manifest_configuration(_roster(base, base, base))["resume"]["player"]["temperature"] \
        == base.temperature


# --- TEST-I083-004: the server reads the recipe vocabulary the builder writes ----------------


def test_i083_004_the_contract_vocabulary_is_what_the_builder_writes() -> None:
    import amaw_contracts
    from app.game_loop import GAME_LOOP_FIELDS

    recipe = _recipe(PlayerConfig(model="fake/a"))
    assert recipe["schema"] == amaw_contracts.RESUME_RECIPE_SCHEMA
    assert set(recipe["player"]) == (
        set(amaw_contracts.RESUME_PLAYER_FIELDS) | set(amaw_contracts.RESUME_PLAYER_OPTIONAL_FIELDS)
    )
    assert set(recipe["coordinator"]) == (
        set(amaw_contracts.RESUME_COORDINATOR_FIELDS) | set(GAME_LOOP_FIELDS)
    )
    assert set(recipe["umpire"]) <= (
        set(amaw_contracts.RESUME_UMPIRE_FIELDS) | set(amaw_contracts.RESUME_UMPIRE_OPTIONAL_FIELDS)
    )
    assert {f.name for f in dataclasses.fields(GuardrailConfig)} == (
        set(amaw_contracts.PLAYER_GUARDRAIL_FIELDS)
        | set(amaw_contracts.PLAYER_GUARDRAIL_OPTIONAL_FIELDS)
    )
    # The optional keys are exactly the ones a pre-condition recipe lacks (I-057, I-069, I-092),
    # so a legacy recipe stays resumable while a current one is validated in full.
    assert set(amaw_contracts.RESUME_PLAYER_OPTIONAL_FIELDS) == {
        "research_mode", "research_history_mode", "search_contract", "tool_surface"
    }
    assert amaw_contracts.PLAYER_GUARDRAIL_OPTIONAL_FIELDS == (
        "max_research_tool_calls", "max_research_operations",
    )


def _persist_manifest(engine, game_id, recipe: dict) -> None:  # noqa: ANN001
    from app.database import session_scope
    from app.models import RunManifest

    manifest = {
        "game_id": str(game_id), "scenario": {"id": "deterministic-game"}, "seed": 1,
        "player": {}, "umpire": {}, "code_version": "test", "benchmark": {}, "resume": recipe,
    }
    with session_scope(engine) as session:
        session.add(RunManifest(game_id=game_id, manifest=manifest, manifest_sha256="a" * 64))


def _running_game(engine, **options):  # noqa: ANN001
    from app import game_service, scenarios
    from tests.api_helpers import FIXTURE_DIR

    creation = scenarios.create_game_from_scenario(
        engine, scenarios.load_scenario(FIXTURE_DIR), **options
    )
    game_service.start_game(engine, creation.game_id)
    return creation


def _server_recipe(config: PlayerConfig, **coordinator) -> dict:
    recipe = _recipe(config, **coordinator)
    recipe["player"]["assignments"] = {actor: config.model for actor in ACTORS}
    return recipe


@pytest.mark.parametrize(
    "corrupt, message",
    [
        (lambda r: r["player"].__setitem__("research_mode", 7), "incomplete"),
        (lambda r: r["player"].__setitem__("research_history_mode", ""), "incomplete"),
        (lambda r: r["player"]["guardrails"].__setitem__("max_research_tool_calls", -1), "incomplete"),
        (lambda r: r["player"]["guardrails"].__setitem__("max_research_tool_calls", True), "incomplete"),
        (lambda r: r["coordinator"].update(diplomacy_mode="bounded_rounds", diplomacy_rounds_per_turn=2), "Diplomacy"),
    ],
)
def test_i083_004_server_refuses_a_recipe_outside_the_versioned_vocabulary(
    db_engine, corrupt, message: str
) -> None:
    from app import resumption

    creation = _running_game(db_engine)
    recipe = _server_recipe(PlayerConfig(model="fake/a"))
    corrupt(recipe)
    _persist_manifest(db_engine, creation.game_id, recipe)

    with pytest.raises(resumption.ResumeError, match=message):
        resumption.describe(db_engine, creation.game_id)
    client = TestClient(
        create_app(engine=db_engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)),
        raise_server_exceptions=False,
    )
    response = client.get(
        f"/admin/games/{creation.game_id}/resume", headers={"Authorization": f"Bearer {ADMIN_TOKEN}"}
    )
    assert response.status_code == 409, response.text


def test_i083_004_server_accepts_a_current_and_a_legacy_recipe(db_engine) -> None:
    from app import resumption

    current = _running_game(db_engine)
    _persist_manifest(db_engine, current.game_id, _server_recipe(PlayerConfig(model="fake/a")))
    assert resumption.describe(db_engine, current.game_id).status.value == "running"

    legacy = _running_game(db_engine)
    recipe = _server_recipe(PlayerConfig(model="fake/a"))
    for key in ("research_mode", "research_history_mode"):
        del recipe["player"][key]
    del recipe["player"]["guardrails"]["max_research_tool_calls"]
    del recipe["inference_profile"]
    recipe["coordinator"] = {
        key: recipe["coordinator"][key]
        for key in ("max_cycles", "cons_concurrency", "diplomacy_concurrency")
    }
    _persist_manifest(db_engine, legacy.game_id, recipe)
    assert resumption.describe(db_engine, legacy.game_id).manifest["resume"] == recipe


# --- TEST-I083-005: profile structure is validated before the reserved name is honoured ------


_UNIFORM = {"schema": "inference-profile-v1", "name": "uniform", "phases": {}, "umpire": {}}


@pytest.mark.parametrize(
    "document",
    [
        {**_UNIFORM, "extra": 1},
        {**_UNIFORM, "phases": "garbage"},
        {**_UNIFORM, "phases": {"bogus": {"max_tokens": None}}},
        {**_UNIFORM, "phases": {"argument": {"unknown_key": None}}},
        {**_UNIFORM, "phases": {"argument": "flat"}},
        {**_UNIFORM, "umpire": {"reasoning_effort": "extreme"}},
    ],
    ids=["unknown-key", "phases-not-mapping", "unknown-phase", "unknown-entry-key",
         "entry-not-mapping", "bad-effort"],
)
def test_i083_005_a_malformed_uniform_document_fails_closed_on_both_sides(document: dict) -> None:
    from agents.inference_profile import InferenceProfile, InferenceProfileError
    from app import resumption

    with pytest.raises(InferenceProfileError):
        InferenceProfile.from_document(document, sealed=True)
    with pytest.raises(resumption.ResumeError):
        resumption._validate_sealed_inference_profile(document)


def test_i083_005_a_flat_sealed_uniform_document_still_loads() -> None:
    from agents.inference_profile import INFERENCE_PROFILE_UNIFORM, InferenceProfile
    from app import resumption

    flat = {**_UNIFORM, "phases": {"argument": {"reasoning_effort": None, "max_tokens": None}}}
    assert InferenceProfile.from_document(flat, sealed=True) is INFERENCE_PROFILE_UNIFORM
    assert InferenceProfile.from_document(_UNIFORM, sealed=True) is INFERENCE_PROFILE_UNIFORM
    resumption._validate_sealed_inference_profile(flat)
