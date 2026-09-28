"""I-056: privacy-safe per-request topology at the player model boundary."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    SystemPromptPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import Engine, inspect, text

from amaw_contracts import REQUEST_TOPOLOGY_COMPONENTS, REQUEST_TOPOLOGY_SCHEMA_VERSION
from agents.autonomous import AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import GuardrailConfig, PlayerConfig, ReferencePlayer
from agents.request_topology import RequestTopologyProfiler
from agents.tools import PlayerTools
from app import export as export_module, game_service, provenance
from app.api import create_app
from app.config import Settings
from app.database import engine_for_path, initialize_database
from tests.agent_fakes import decision_call
from tests.api_helpers import FIXTURE_DIR, seed


ADMIN_TOKEN = "i-056-admin"


def _memory(marker: str = "measure only") -> dict:
    return {
        "commitments_made": [],
        "commitments_or_offers_received": [],
        "outstanding_offers_and_negotiations": [],
        "red_lines_and_constraints": [],
        "uncertain_or_disputed_claims": [],
        "relationships_and_likely_coalitions": [],
        "strategic_assessment": marker,
        "intended_next_action": "",
    }


def _usage(index: int) -> RequestUsage:
    return RequestUsage(
        input_tokens=100 * index,
        cache_read_tokens=20 * index,
        cache_write_tokens=5 * index,
        output_tokens=10 * index,
        output_reasoning_tokens=3 * index,
    )


def _player(
    engine: Engine,
    creation,
    model: FunctionModel,
    *,
    max_iterations: int = 8,
    max_message_chars: int = 2_000,
) -> ReferencePlayer:
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
        config=PlayerConfig(
            model="fake/topology",
            activation_mode="single_shot",
            guardrails=GuardrailConfig(
                max_llm_iterations=max_iterations,
                max_message_chars=max_message_chars,
            ),
        ),
    )


def _coordinator(engine: Engine) -> AutonomousGameRunner:
    return AutonomousGameRunner(
        TestClient(
            create_app(
                engine=engine,
                settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
            ),
            raise_server_exceptions=False,
        ),
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=lambda *_args: None,
    )


def _research_activation(db_engine: Engine):  # noqa: ANN202
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    game_service.send_message(
        db_engine,
        creation.game_id,
        sender_actor_id="belvar",
        recipients=["arkania"],
        public=False,
        text="private-message-marker",
    )

    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages) + 1
        if index == 1:
            parts = [ToolCallPart(
                tool_name="search_resources", args={"query": "regional background"}
            )]
        elif index == 2:
            parts = [ToolCallPart(
                tool_name="read_resource", args={"resource_id": "background"}
            )]
        else:
            parts = [decision_call(info, {
                "messages": [],
                "strategic_memory": _memory("private-memory-marker"),
                "ready": True,
            })]
        return ModelResponse(parts=parts, usage=_usage(index), model_name="fake/topology-v1")

    runner = LifecycleRunner(_player(db_engine, creation, FunctionModel(policy)))
    outcome = runner.step()
    assert outcome.activation is not None
    return creation, runner, outcome


def test_i056_001_multi_request_components_growth_prefix_and_provider_cache_usage(
    db_engine: Engine,
) -> None:
    _, _, outcome = _research_activation(db_engine)
    topology = outcome.activation.request_topology

    assert topology["schema_version"] == REQUEST_TOPOLOGY_SCHEMA_VERSION
    assert topology["measurement"] == {
        "boundary": "pydantic-ai-model-wrapper-logical-request",
        "encoding": "canonical-json-utf8",
        "hash_scheme": "hmac-sha256-ephemeral-activation-key",
        "provider_wire_exact": False,
        "tokenizer": None,
    }
    requests = topology["requests"]
    assert [request["sequence"] for request in requests] == [1, 2, 3]
    assert set(requests[0]["component_bytes"]) == set(REQUEST_TOPOLOGY_COMPONENTS)
    for index, request in enumerate(requests):
        assert sum(request["component_bytes"].values()) == request["measured_bytes"]
        expected_growth = (
            None if index == 0
            else request["measured_bytes"] - requests[index - 1]["measured_bytes"]
        )
        assert request["growth_bytes"] == expected_growth
        assert request["stable_prefix_bytes"] <= request["measured_bytes"]
        if index == 0:
            assert request["stable_prefix_bytes"] == 0
            assert request["stable_prefix_digest"] is None
        else:
            assert request["stable_prefix_bytes"] > 0
            assert len(request["stable_prefix_digest"]) == 64
        assert request["provider_usage"] == {
            "available": True,
            "input_tokens": 100 * (index + 1),
            "cache_read_tokens": 20 * (index + 1),
            "cache_write_tokens": 5 * (index + 1),
        }

    first = requests[0]["component_bytes"]
    assert all(first[name] > 0 for name in (
        "system_prompts",
        "player_prompts",
        "tool_schemas",
        "output_schemas",
        "scenario_public_state",
        "actor_private_dossier",
        "effects",
        "incremental_messages",
        "strategic_memory",
        "framework_envelope",
    ))
    assert first["resource_results"] == 0
    assert requests[1]["component_bytes"]["resource_results"] > 0
    assert requests[2]["component_bytes"]["resource_results"] > requests[1][
        "component_bytes"
    ]["resource_results"]
    assert requests[1]["component_bytes"]["prior_tool_plumbing"] > 0


def test_i056_002_topology_is_content_free_and_prefix_digest_is_not_raw_sha256(
    db_engine: Engine,
) -> None:
    _, _, outcome = _research_activation(db_engine)
    topology = outcome.activation.request_topology
    encoded = json.dumps(topology, sort_keys=True)

    forbidden = (
        "Arkania",
        "private-memory-marker",
        "private-message-marker",
        "regional background",
        "background",
        "objectives",
        "private_brief",
        "resource_id",
        "tool_call_id",
        "reasoning",
        "Bearer",
    )
    assert all(value not in encoded for value in forbidden)
    assert "digest_key" not in encoded and "hmac_key" not in encoded
    second = topology["requests"][1]
    raw_guess = hashlib.sha256(b"regional background").hexdigest()
    assert second["stable_prefix_digest"] != raw_guess

    # The same bytes receive a different digest after the activation-local secret is discarded.
    messages = [ModelRequest(parts=[
        SystemPromptPart("private-system-marker"),
        UserPromptPart("private-user-marker"),
    ])]
    profiler = RequestTopologyProfiler()
    profiler.capture(messages, {}, ModelRequestParameters(), request_number=1)
    profiler.capture(messages, {}, ModelRequestParameters(), request_number=2)
    first_digest = profiler.snapshot()["requests"][1]["stable_prefix_digest"]
    profiler.reset()
    profiler.capture(messages, {}, ModelRequestParameters(), request_number=1)
    profiler.capture(messages, {}, ModelRequestParameters(), request_number=2)
    second_digest = profiler.snapshot()["requests"][1]["stable_prefix_digest"]
    assert first_digest != second_digest


def test_i056_003_request_shapes_survive_provider_failure_and_logical_retry(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    calls = 0

    def transient(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[ToolCallPart(
                    tool_name="search_resources", args={"query": "regional"}
                )],
                usage=_usage(1),
            )
        if calls == 2:
            raise ModelHTTPError(503, "fake/topology", {"private": "do-not-store"})
        return ModelResponse(parts=[decision_call(info, {
            "messages": [], "strategic_memory": _memory(), "ready": True,
        })], usage=_usage(3))

    activation = _player(db_engine, creation, FunctionModel(transient)).activate()
    requests = activation.request_topology["requests"]
    assert [request["sequence"] for request in requests] == [1, 2, 3]
    assert [request["provider_usage"]["available"] for request in requests] == [
        True, False, True
    ]
    assert requests[0]["provider_usage"]["input_tokens"] == 100
    assert requests[1]["provider_usage"]["input_tokens"] is None
    assert requests[2]["provider_usage"]["input_tokens"] == 300
    assert "do-not-store" not in json.dumps(activation.request_topology)


def test_i056_003_framework_rejection_retains_failed_activation_topology(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def reject(_messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        return ModelResponse(parts=[decision_call(info, {
            "messages": [{"recipients": [], "public": True, "text": "too long"}],
            "strategic_memory": _memory(),
            "ready": True,
        })], usage=_usage(1))

    runner = LifecycleRunner(_player(
        db_engine,
        creation,
        FunctionModel(reject),
        max_iterations=4,  # activation-wide (I-077): two rejections per attempt, two attempts
        max_message_chars=2,
    ))
    outcome = runner.step()
    assert outcome.failure is not None
    topology = outcome.failure.counts["request_topology"]
    assert [request["sequence"] for request in topology["requests"]] == [1, 2, 3, 4]
    assert all(request["provider_usage"]["available"] for request in topology["requests"])
    assert "too long" not in json.dumps(topology)

    _coordinator(db_engine)._record_failure(str(creation.game_id), runner, outcome)
    [record] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    assert record["completed"] is False
    assert record["request_topology"] == topology


def test_i056_004_persistence_export_v7_and_summary_reconcile(db_engine: Engine) -> None:
    creation, runner, outcome = _research_activation(db_engine)
    topology = outcome.activation.request_topology
    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, outcome)

    exported = export_module.build_game_export(db_engine, creation.game_id)
    assert exported["export_format_version"] == export_module.EXPORT_FORMAT_VERSION
    [record] = exported["agent_runs"]
    assert record["request_topology"] == topology
    summary = exported["run_summary"]["request_topology"]
    requests = topology["requests"]
    assert summary["player_model_calls"] == 3
    assert summary["measured_requests"] == 3
    assert summary["unmeasured_requests"] == 0
    assert summary["measured_bytes"] == sum(row["measured_bytes"] for row in requests)
    assert summary["component_bytes"] == {
        component: sum(row["component_bytes"][component] for row in requests)
        for component in REQUEST_TOPOLOGY_COMPONENTS
    }
    assert summary["provider_usage"] == {
        "reported_requests": 3,
        "input_tokens": 600,
        "cache_read_tokens": 120,
        "cache_write_tokens": 30,
    }


def _run_values(**overrides):  # noqa: ANN003, ANN202
    values = {
        "actor_id": "arkania",
        "role": "player",
        "turn": 1,
        "phase": "diplomacy",
        "model_requested": "fake/topology",
        "prompt_version": "player-test",
        "prompt_hash": "a" * 64,
        "agent_framework": "pydantic-ai",
        "tool_schema_version": "player-tools-v3",
        "model_calls": 1,
    }
    values.update(overrides)
    return values


def _valid_topology() -> dict:
    components = {component: 0 for component in REQUEST_TOPOLOGY_COMPONENTS}
    components["framework_envelope"] = 10
    return {
        "schema_version": REQUEST_TOPOLOGY_SCHEMA_VERSION,
        "measurement": {
            "boundary": "pydantic-ai-model-wrapper-logical-request",
            "encoding": "canonical-json-utf8",
            "hash_scheme": "hmac-sha256-ephemeral-activation-key",
            "provider_wire_exact": False,
            "tokenizer": None,
        },
        "requests": [{
            "sequence": 1,
            "measured_bytes": 10,
            "growth_bytes": None,
            "stable_prefix_bytes": 0,
            "stable_prefix_digest": None,
            "component_bytes": components,
            "provider_usage": {
                "available": False,
                "input_tokens": None,
                "cache_read_tokens": None,
                "cache_write_tokens": None,
            },
        }],
    }


def test_i056_005_rejects_malformed_or_nonreconciling_topology(db_engine: Engine) -> None:
    creation = seed(db_engine)
    topology = _valid_topology()
    topology["requests"][0]["measured_bytes"] = 11
    with pytest.raises(provenance.ProvenanceError, match="reconcile"):
        provenance.record_agent_run(
            db_engine,
            creation.game_id,
            **_run_values(request_topology=topology),
        )

    topology = _valid_topology()
    topology["requests"][0]["provider_usage"]["cache_read_tokens"] = 1
    with pytest.raises(provenance.ProvenanceError, match="provider_usage"):
        provenance.record_agent_run(
            db_engine,
            creation.game_id,
            **_run_values(request_topology=topology),
        )

    topology = _valid_topology()
    topology["requests"][0]["provider_usage"] = {
        "available": True,
        "input_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
    }
    with pytest.raises(provenance.ProvenanceError, match="AgentRun"):
        provenance.record_agent_run(
            db_engine,
            creation.game_id,
            **_run_values(request_topology=topology),
        )

    with pytest.raises(provenance.ProvenanceError, match="Umpire"):
        provenance.record_agent_run(
            db_engine,
            creation.game_id,
            **_run_values(
                actor_id=None,
                role="umpire",
                request_topology=_valid_topology(),
            ),
        )


def test_i056_005_additive_migration_marks_legacy_topology_unavailable(tmp_path: Path) -> None:
    engine = engine_for_path(tmp_path / "pre-i056.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE agentrun (id INTEGER PRIMARY KEY, game_id CHAR(32) NOT NULL, "
            "role VARCHAR NOT NULL, model_requested VARCHAR NOT NULL, latency_ms INTEGER)"
        )
        connection.exec_driver_sql(
            "INSERT INTO agentrun VALUES (1, '00000000000000000000000000000000', "
            "'player', 'legacy/model', 1234)"
        )

    initialize_database(engine)

    assert "request_topology" in {
        column["name"] for column in inspect(engine).get_columns("agentrun")
    }
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT model_requested, request_topology FROM agentrun WHERE id = 1"
        )).one()
    assert row == ("legacy/model", "{}")
