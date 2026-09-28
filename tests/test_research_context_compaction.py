"""I-057: independently selectable batch research and lossless success compaction."""

from __future__ import annotations

import copy
import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine
from sqlmodel import select

from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import (
    RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    RESEARCH_HISTORY_FULL_V1,
    RESEARCH_MODE_BATCHED_V1,
    RESEARCH_MODE_SEQUENTIAL_V1,
    PlayerConfig,
    ReferencePlayer,
    compact_research_history,
)
from agents.tools import PlayerTools
from app import export as export_module, game_service
from app.api import create_app
from app.config import Settings
from app.database import session_scope
from app.models import ResourceAccess
from tests.agent_fakes import decision_call
from tests.api_helpers import seed
from tests.test_single_shot import _memory


ADMIN_TOKEN = "i-057-admin"


def _player(engine: Engine, creation, model, **config_overrides) -> ReferencePlayer:  # noqa: ANN001
    tools = PlayerTools(
        TestClient(create_app(engine=engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    return ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(model="fake/i057", **config_overrides),
    )


def _coordinator(engine: Engine) -> AutonomousGameRunner:
    client = TestClient(
        create_app(
            engine=engine,
            settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        ),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        client,
        admin_token=ADMIN_TOKEN,
        scenario="tests/fixtures/deterministic-game",
        player_factory=lambda *_args: None,
    )


def test_i057_001_defaults_validation_and_tool_schema_are_explicit(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    default = _player(db_engine, creation, FunctionModel(lambda _m, _i: ModelResponse(parts=[])))
    assert default.config.research_mode == RESEARCH_MODE_SEQUENTIAL_V1
    assert default.config.research_history_mode == RESEARCH_HISTORY_FULL_V1
    assert default.tool_schema_version == "player-tools-v11-live-bounded-research"
    assert "search_resources_batch" not in default.tool_names

    batched = _player(
        db_engine,
        creation,
        FunctionModel(lambda _m, _i: ModelResponse(parts=[])),
        research_mode=RESEARCH_MODE_BATCHED_V1,
    )
    assert {"search_resources_batch", "read_resources_batch"} <= batched.tool_names
    assert batched.tool_schema_version == (
        "player-tools-v17-batched-live-bounded-operations-item-outcomes"
    )

    with pytest.raises(ValueError, match="research_mode"):
        PlayerConfig(model="fake", research_mode="unknown")
    with pytest.raises(ValueError, match="research_history_mode"):
        PlayerConfig(model="fake", research_history_mode="unknown")


def test_i057_002_batch_tools_reduce_round_trips_and_preserve_access_attribution(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages)
        if index == 0:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="search_resources_batch",
                args={"queries": ["regional", "intelligence"]},
            )])
        if index == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="read_resources_batch",
                args={"resource_ids": ["background", "arkania-intel"]},
            )])
        return ModelResponse(parts=[decision_call(info, {
            "messages": [], "strategic_memory": _memory(), "ready": True,
        })])

    player = _player(
        db_engine,
        creation,
        FunctionModel(policy),
        research_mode=RESEARCH_MODE_BATCHED_V1,
    )
    result = player.activate()
    assert result.model_iterations == 3
    assert result.tool_call_names == [
        "search_resources_batch", "read_resources_batch", "ready"
    ]
    with session_scope(db_engine) as session:
        accesses = [
            (row.operation, row.query, row.resource_id, row.activation_id)
            for row in session.exec(
                select(ResourceAccess)
                .where(ResourceAccess.game_id == creation.game_id)
                .order_by(ResourceAccess.id)
            )
        ]
    assert [row[:3] for row in accesses] == [
        ("search", "regional", None),
        ("search", "intelligence", None),
        ("read", None, "background"),
        ("read", None, "arkania-intel"),
    ]
    assert {row[3] for row in accesses} == {result.activation_id}


def test_i057_003_compaction_is_lossless_non_mutating_and_keeps_failures() -> None:
    success_id = "success-1"
    failure_id = "failure-1"
    returned = {"results": [{"id": "background", "snippet": "substantive marker"}]}
    messages = [
        ModelRequest(parts=[UserPromptPart(content="activation")]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="search_resources", args={"query": "regional"},
            tool_call_id=success_id,
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="search_resources", content={"ok": True, "data": returned},
            tool_call_id=success_id,
        )]),
        ModelResponse(parts=[ToolCallPart(
            tool_name="read_resource", args={"resource_id": "missing"},
            tool_call_id=failure_id,
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="read_resource",
            content={"ok": False, "http_status": 404, "error": {"code": "not_found"}},
            tool_call_id=failure_id,
            outcome="failed",
        )]),
    ]
    original = copy.deepcopy(messages)

    compacted = compact_research_history(messages)

    assert messages == original
    assert isinstance(compacted[1].parts[0], TextPart)
    retained = json.loads(compacted[2].parts[0].content)
    assert retained == {
        "retained_research_results": [
            {"tool_name": "search_resources", "data": returned}
        ],
        "schema_version": "research-history-compact-success-v1",
    }
    assert isinstance(compacted[3].parts[0], ToolCallPart)
    assert isinstance(compacted[4].parts[0], ToolReturnPart)
    assert compacted[4].parts[0].content["error"]["code"] == "not_found"


def test_i057_004_provider_sees_compaction_but_local_telemetry_keeps_full_history(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    seen: list[list] = []

    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        seen.append(list(messages))
        index = sum(isinstance(message, ModelResponse) for message in messages)
        if index == 0:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="search_resources", args={"query": "regional"}
            )])
        return ModelResponse(parts=[decision_call(info, {
            "messages": [], "strategic_memory": _memory(), "ready": True,
        })])

    player = _player(
        db_engine,
        creation,
        FunctionModel(policy),
        research_history_mode=RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    )
    result = player.activate()

    assert any(
        isinstance(part, UserPromptPart) and "retained_research_results" in part.content
        for message in seen[1] for part in message.parts
    )
    assert not any(
        isinstance(part, (ToolCallPart, ToolReturnPart))
        and getattr(part, "tool_name", None) == "search_resources"
        for message in seen[1] for part in message.parts
    )
    assert any(
        isinstance(part, ToolReturnPart) and part.tool_name == "search_resources"
        for message in result.messages for part in message.parts
    )
    assert result.invalid_tool_call_breakdown["events"] == []
    assert result.request_topology["requests"][1]["component_bytes"]["resource_results"] > 0


def test_i057_005_manifest_resume_and_agent_run_provenance_pin_both_conditions(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(_messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        return ModelResponse(parts=[decision_call(info, {
            "messages": [], "strategic_memory": _memory(), "ready": True,
        })])

    player = _player(
        db_engine,
        creation,
        FunctionModel(policy),
        research_mode=RESEARCH_MODE_BATCHED_V1,
        research_history_mode=RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    )
    runner = LifecycleRunner(player)
    coordinator = _coordinator(db_engine)
    configuration = coordinator._manifest_configuration({"arkania": runner})
    assert configuration["player"]["research_mode"] == RESEARCH_MODE_BATCHED_V1
    assert configuration["player"]["research_history_mode"] == RESEARCH_HISTORY_COMPACT_SUCCESS_V1
    assert configuration["player"]["tool_schema_version"] == player.tool_schema_version
    assert configuration["resume"]["player"]["research_mode"] == RESEARCH_MODE_BATCHED_V1
    assert configuration["resume"]["player"]["research_history_mode"] == (
        RESEARCH_HISTORY_COMPACT_SUCCESS_V1
    )

    baseline = _player(
        db_engine,
        creation,
        FunctionModel(policy),
        research_mode=RESEARCH_MODE_SEQUENTIAL_V1,
        research_history_mode=RESEARCH_HISTORY_FULL_V1,
    )
    # I-083: refused through the one shared-condition projection, which names the fields.
    with pytest.raises(AutonomousGameError, match="research_mode"):
        coordinator._manifest_configuration({
            "arkania": runner,
            "belvar": LifecycleRunner(baseline),
        })

    activation = player.activate()
    outcome = SimpleNamespace(
        activation=activation,
        context={"turn": 1},
        phase="diplomacy",
    )
    coordinator._record_activation(str(creation.game_id), runner, outcome)
    [record] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    assert record["model_parameters"]["research_mode"] == RESEARCH_MODE_BATCHED_V1
    assert record["model_parameters"]["research_history_mode"] == (
        RESEARCH_HISTORY_COMPACT_SUCCESS_V1
    )
    assert record["tool_schema_version"] == player.tool_schema_version
