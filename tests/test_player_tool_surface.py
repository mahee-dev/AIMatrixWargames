"""I-092 — the ``tool_loop`` mutating tool surface is an explicit, sealed experimental condition.

Option (d), D-079: ``single_shot`` stays the normal/default player condition; ``tool_loop`` is
experimental and carries ``tool_surface ∈ {broad, available_only}``. ``broad`` (the default) is
today's behaviour — the model is offered the whole mutating vocabulary and an attempt outside
``available_actions`` is measured as invalid-tool behaviour (run 11's Cons-phase
``send_message``). ``available_only`` withdraws, per activation, every mutating tool the server
does not currently list. The server stays authoritative (api.md §8.1, API-INV-009); the filter
is ergonomic, never a legality check. The value is sealed wherever ``activation_mode`` is
(agents.md §5/§19; D-046), so the two surfaces are never mistaken for one condition.

Outside ``tool_loop`` the field is accepted and sealed as given: one invariant ("always a valid
sealed value; governs only the tool-loop mutating surface") instead of a mode×surface special
case. ``single_shot`` already derives its decision schema from ``available_actions`` (D-046).

Offline, no key: real :class:`pydantic_ai.Agent`s driven by deterministic policies on the
in-process server (`tests/api_helpers.py`).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine

from agents.autonomous import (
    AutonomousGameError,
    AutonomousGameRunner,
    build_resume_recipe,
    expected_manifest_configuration,
)
from agents.lifecycle import LifecycleRunner
from agents.player import PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from agents.umpire import FakeUmpire
from app import export as export_module
from app import game_service
from app.api import create_app
from app.config import Settings
from app.models import AdjudicationMode
from app.umpire import AdjudicationProposal
from tests.agent_fakes import (
    default_decision_policy,
    default_policy,
    extract_packet,
    model_from_policy,
)
from tests.api_helpers import ACTORS, client, seed, start_to_cons, start_to_diplomacy

ADMIN_TOKEN = "admin-secret-token"


def _coordinator(engine: Engine, **kwargs) -> AutonomousGameRunner:  # noqa: ANN003
    api_client = TestClient(
        create_app(engine=engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        api_client, admin_token=ADMIN_TOKEN, scenario="tests/fixtures/deterministic-game",
        player_factory=lambda *_args: None, **kwargs,
    )


def _detached_runner(actor_id: str, config: PlayerConfig) -> LifecycleRunner:
    tools = PlayerTools(SimpleNamespace(), game_id="game", token="token")
    player = ReferencePlayer(
        tools, actor_id=actor_id, actor_name=actor_id.title(),
        model=model_from_policy(default_policy), config=config,
    )
    return LifecycleRunner(player)


def _roster(config: PlayerConfig) -> dict[str, LifecycleRunner]:
    return {actor: _detached_runner(actor, config) for actor in ACTORS}


def _recipe(config: PlayerConfig) -> dict:
    from agents.autonomous import DEFAULT_CONS_CONCURRENCY, DEFAULT_DIPLOMACY_CONCURRENCY
    from app.game_loop import GameLoopCondition

    return build_resume_recipe(
        config, assignments={}, max_cycles=1000,
        cons_concurrency=DEFAULT_CONS_CONCURRENCY,
        diplomacy_concurrency=DEFAULT_DIPLOMACY_CONCURRENCY,
        condition=GameLoopCondition(),
        umpire={"model": "fake/umpire", "temperature": None, "max_tokens": None,
                "timeout": None, "reasoning_effort": None},
    )


def _player(
    engine: Engine,
    creation,  # noqa: ANN001
    policy,  # noqa: ANN001
    *,
    actor: str = "arkania",
    **config_kwargs,  # noqa: ANN003
) -> ReferencePlayer:
    tools = PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )
    config = PlayerConfig(model="fake/i092", **config_kwargs)
    return ReferencePlayer(
        tools, actor_id=actor, actor_name=actor.title(), model=model_from_policy(policy),
        config=config,
    )


# --- TEST-I092-001: the condition field ---------------------------------------------------------


def test_i092_001_player_config_accepts_both_surfaces_and_defaults_to_broad() -> None:
    assert PlayerConfig(model="fake/a").tool_surface == "broad"
    assert PlayerConfig(model="fake/a", tool_surface="broad").tool_surface == "broad"
    assert PlayerConfig(
        model="fake/a", activation_mode="tool_loop", tool_surface="available_only"
    ).tool_surface == "available_only"
    # Sealed as given outside tool_loop too — no mode×surface special case.
    assert PlayerConfig(
        model="fake/a", activation_mode="single_shot", tool_surface="available_only"
    ).tool_surface == "available_only"
    with pytest.raises(ValueError, match=r"tool_surface must be one of .*'hidden'"):
        PlayerConfig(model="fake/a", tool_surface="hidden")


def test_i092_001_a_settings_carry_the_surface_and_refuse_an_unknown_value() -> None:
    assert Settings(_env_file=None).player_tool_surface == "broad"
    assert Settings(
        AMAW_PLAYER_TOOL_SURFACE="available_only", _env_file=None
    ).player_tool_surface == "available_only"
    with pytest.raises(ValidationError):
        Settings(AMAW_PLAYER_TOOL_SURFACE="hidden", _env_file=None)


# --- TEST-I092-002: sealed in the recipe and the manifest; round-trips; legacy → broad ---------


def test_i092_002_recipe_and_manifest_seal_the_surface_and_round_trip(db_engine: Engine) -> None:
    import amaw_contracts

    config = PlayerConfig(
        model="fake/a", activation_mode="tool_loop", tool_surface="available_only"
    )
    recipe = _recipe(config)
    assert recipe["player"]["tool_surface"] == "available_only"
    assert "tool_surface" in amaw_contracts.RESUME_PLAYER_OPTIONAL_FIELDS
    # The recipe's inverse rebuilds the recipe byte-for-byte (I-061).
    rebuilt = PlayerConfig.from_resume_recipe(recipe, model="fake/a")
    assert rebuilt.tool_surface == "available_only"
    assert _recipe(rebuilt) == recipe

    # Expected (declared before any game) and actual (recorded by the runner) agree (I-078).
    expected = expected_manifest_configuration(config)
    actual = _coordinator(db_engine)._manifest_configuration(_roster(config))
    assert expected["player"]["tool_surface"] == "available_only"
    assert actual["player"]["tool_surface"] == "available_only"
    assert actual["resume"]["player"]["tool_surface"] == "available_only"
    assert {**expected["player"], "assignments": None} == {**actual["player"], "assignments": None}

    # A model benchmark holds it constant (methodology §5.1).
    from benchmark.controls import PLAYER_CONDITION_PATHS
    assert {"player.tool_surface", "resume.player.tool_surface"} <= set(PLAYER_CONDITION_PATHS)


def test_i092_002_a_legacy_recipe_without_the_key_resolves_to_broad_and_seals_as_given(
    db_engine: Engine,
) -> None:
    base = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    legacy = _recipe(base)
    del legacy["player"]["tool_surface"]
    assert PlayerConfig.from_resume_recipe(legacy, model="fake/a").tool_surface == "broad"
    # An existing tool_loop run is never silently switched: the legacy recipe describes the
    # players' (broad) condition and is sealed byte-for-byte, no key inserted.
    sealed = _coordinator(db_engine, run_configuration={"resume": legacy}) \
        ._manifest_configuration(_roster(base))["resume"]
    assert "tool_surface" not in sealed["player"]
    assert {**sealed, "player": {**sealed["player"], "assignments": {}}} == legacy


# --- TEST-I092-003: a broad game refuses resume under available_only, and the inverse ---------


@pytest.mark.parametrize(
    "sealed_surface, resumed_surface",
    [("broad", "available_only"), ("available_only", "broad")],
)
def test_i092_003_a_game_refuses_resume_under_the_other_surface(
    db_engine: Engine, sealed_surface: str, resumed_surface: str
) -> None:
    sealed = _recipe(PlayerConfig(
        model="fake/a", activation_mode="tool_loop", tool_surface=sealed_surface,
    ))
    resumed = PlayerConfig(model="fake/a", activation_mode="tool_loop", tool_surface=resumed_surface)
    with pytest.raises(AutonomousGameError, match=r"resume recipe.*differing: tool_surface"):
        _coordinator(db_engine, run_configuration={"resume": sealed}) \
            ._manifest_configuration(_roster(resumed))


# --- TEST-I092-009: every player AgentRun names the surface that generated its metrics --------


def test_i092_009_recorded_activation_provenance_carries_the_surface(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def ready_then_stop(packet, tool_names, idx):  # noqa: ANN001
        return [ToolCallPart(tool_name="ready", args={})] if idx == 0 else [TextPart("done")]

    runner = LifecycleRunner(_player(
        db_engine, creation, ready_then_stop,
        activation_mode="tool_loop", tool_surface="available_only",
    ))
    activation = runner.player.activate()
    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, SimpleNamespace(
        activation=activation, context={"turn": 1}, phase="diplomacy",
    ))
    export = export_module.build_game_export(db_engine, creation.game_id)
    [record] = export["agent_runs"]
    assert record["model_parameters"]["activation_mode"] == "tool_loop"
    assert record["model_parameters"]["tool_surface"] == "available_only"


# --- the model-facing surface -------------------------------------------------------------------

BROAD_LOOP_TOOLS = frozenset({
    "observe", "read_messages", "search_resources", "read_resource",
    "send_message", "make_argument", "make_con", "pass_con", "update_strategic_memory", "ready",
})
RESEARCH_TOOLS = frozenset({
    "read_messages", "search_resources", "read_resource",
    "search_resources_batch", "read_resources_batch",
})


def _recording_policy(offered: list[list[str]], steps):  # noqa: ANN001
    """Record the tools offered at every step, then play ``steps`` (one parts list per step)."""

    def policy(packet, tool_names, idx):  # noqa: ANN001
        offered.append(sorted(tool_names))
        return steps[idx] if idx < len(steps) else [TextPart("done")]

    return policy


def _rejections(activation, tool_name: str) -> list[dict]:  # noqa: ANN001
    return [
        event for event in activation.invalid_tool_call_breakdown.get("events", [])
        if event.get("action") == tool_name and event["reason"] == "server_rejection"
    ]


SEND = ToolCallPart(
    tool_name="send_message",
    args={"recipients": ["arkania"], "public": False, "text": "Stand with me."},
)


def _pass_con(creation, argument) -> ToolCallPart:  # noqa: ANN001
    return ToolCallPart(tool_name="pass_con", args={"argument_id": str(argument.id)})


# --- TEST-I092-004: broad keeps today's surface — the Cons-phase attempt stays measurable ------


def test_i092_004_broad_offers_the_whole_mutating_vocabulary_in_cons(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    offered: list[list[str]] = []
    player = _player(
        db_engine, creation, _recording_policy(offered, [[SEND], [_pass_con(creation, argument)]]),
        actor="belvar", activation_mode="tool_loop", tool_surface="broad",
    )
    assert "send_message" not in player.tools.observe().data["available_actions"]

    activation = player.activate()
    # Compatibility proof: the first step offers exactly today's tool-loop set (I-091 withdraws
    # only a declared-then-retracted tool; a never-declared one is left offered).
    assert set(offered[0]) == BROAD_LOOP_TOOLS
    [rejection] = _rejections(activation, "send_message")
    assert rejection["error_code"] == "wrong_phase"
    assert activation.tool_call_names[:2] == ["send_message", "pass_con"]


# --- TEST-I092-005: available_only withdraws what the server does not list ---------------------


def test_i092_005_available_only_withdraws_an_unlisted_mutating_tool(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    offered: list[list[str]] = []
    player = _player(
        db_engine, creation, _recording_policy(offered, [[_pass_con(creation, argument)]]),
        actor="belvar", activation_mode="tool_loop", tool_surface="available_only",
    )
    declared = player.tools.observe().data["available_actions"]
    assert "send_message" not in declared and {"make_con", "pass_con"} <= set(declared)

    activation = player.activate()
    assert "send_message" not in offered[0]
    assert "make_argument" not in offered[0] and "ready" not in offered[0]
    assert {"make_con", "pass_con"} <= set(offered[0])
    # Client-side tools and the research tools are not the server's to list; they stay.
    assert {
        "observe", "update_strategic_memory", "read_messages", "search_resources", "read_resource",
    } <= set(offered[0])
    assert activation.invalid_tool_calls == 0
    assert activation.tool_call_names == ["pass_con"]
    # Deterministic naming: the same names, in the same registration order, as broad's set.
    assert player.tool_names == frozenset(BROAD_LOOP_TOOLS)


# --- TEST-I092-006: the surface follows the context — the tool comes back when legal again ----


def test_i092_006_available_only_exposes_the_tool_again_when_the_context_lists_it(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    offered: list[list[str]] = []
    player = _player(
        db_engine, creation, _recording_policy(offered, [[_pass_con(creation, argument)]]),
        actor="belvar", activation_mode="tool_loop", tool_surface="available_only",
    )
    player.activate()  # Cons: send_message withdrawn
    assert "send_message" not in offered[0]

    # The server moves on: the third opponent passes, the Argument is adjudicated, and the
    # next Argument phase makes belvar the active Actor — messaging is legal again (§8.1).
    game_service.pass_con(db_engine, creation.game_id, actor_id="cestia",
                          argument_id=argument.id)
    game_service.resolve_adjudication(db_engine, creation.game_id, FakeUmpire(AdjudicationProposal(
        mode=AdjudicationMode.automatic_success, rationale="clear",
        success_effects=["Reserves deploy."], failure_effects=["Mobilisation stalls."],
    )))
    game_service.advance_after_resolution(db_engine, creation.game_id)
    context = player.tools.observe().data
    assert context["phase"] == "argument" and context["active_actor_id"] == "belvar"
    assert {"send_message", "make_argument"} <= set(context["available_actions"])

    offered.clear()
    player.activate()
    assert {"send_message", "make_argument"} <= set(offered[0])
    assert "pass_con" not in offered[0] and "make_con" not in offered[0]


# --- TEST-I092-007: the research surface is the same under both tool surfaces ------------------


def _offered_definitions(engine: Engine, creation, *, actor: str, **config_kwargs):  # noqa: ANN001, ANN003
    """The full tool definitions (name, description, schema) the model sees at its first step."""
    captured: list[tuple[str, str | None, dict]] = []

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not captured:
            captured.extend(
                (t.name, t.description, t.parameters_json_schema) for t in info.function_tools
            )
        packet = extract_packet(messages)
        if config_kwargs.get("activation_mode") == "single_shot":
            return ModelResponse(parts=default_decision_policy(packet, 0, info))
        return ModelResponse(parts=[TextPart("done")])

    tools = PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )
    player = ReferencePlayer(
        tools, actor_id=actor, actor_name=actor.title(), model=FunctionModel(run),
        config=PlayerConfig(model="fake/i092", **config_kwargs),
    )
    player.activate()
    return sorted(captured)


@pytest.mark.parametrize("research_mode", ["sequential-v1", "batched-v1"])
def test_i092_007_research_tools_are_identical_between_the_surfaces(
    db_engine: Engine, research_mode: str
) -> None:
    creation = seed(db_engine)
    start_to_cons(db_engine, creation)
    by_surface = {}
    for surface in ("broad", "available_only"):
        definitions = _offered_definitions(
            db_engine, creation, actor="belvar", activation_mode="tool_loop",
            tool_surface=surface, research_mode=research_mode,
        )
        by_surface[surface] = [d for d in definitions if d[0] in RESEARCH_TOOLS]
    assert by_surface["broad"] == by_surface["available_only"]
    expected = {"read_messages", "search_resources", "read_resource"}
    if research_mode == "batched-v1":
        expected |= {"search_resources_batch", "read_resources_batch"}
    assert {d[0] for d in by_surface["broad"]} == expected


# --- TEST-I092-008: single_shot is unchanged under either value ---------------------------------


def _single_shot_offer(engine: Engine, surface: str) -> tuple:
    """What a single-shot model sees on a fresh Cons game: function tools + the output tool."""
    creation = seed(engine)
    start_to_cons(engine, creation)
    captured: list = []

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        if not captured:
            captured.append(sorted(t.name for t in info.function_tools))
            captured.extend(
                (t.name, t.description, t.parameters_json_schema) for t in info.output_tools
            )
        return ModelResponse(parts=default_decision_policy(extract_packet(messages), 0, info))

    tools = PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens["belvar"]
    )
    player = ReferencePlayer(
        tools, actor_id="belvar", actor_name="Belvar", model=FunctionModel(run),
        config=PlayerConfig(model="fake/i092", activation_mode="single_shot", tool_surface=surface),
    )
    assert player.activate().invalid_tool_calls == 0
    return tuple(captured)


def test_i092_008_single_shot_surface_and_decision_schema_are_unchanged(db_engine: Engine) -> None:
    broad = _single_shot_offer(db_engine, "broad")
    # The single-shot model sees the three read tools plus the one ``decide`` output tool whose
    # schema is derived from ``available_actions`` (D-046) — the surface field has nothing to
    # filter, and the offered names and schemas are identical under either value.
    assert broad[0] == ["read_messages", "read_resource", "search_resources"]
    assert [name for name, _, _ in broad[1:]] == ["decide"]
    assert _single_shot_offer(db_engine, "available_only") == broad
