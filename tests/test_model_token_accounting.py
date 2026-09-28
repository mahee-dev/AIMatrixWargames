"""I-050: every returned provider usage record survives the logical activation."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import Engine, inspect

from agents.autonomous import AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import GuardrailConfig, PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app import export as export_module, game_service
from app.api import create_app
from app.config import Settings
from app.database import engine_for_path, initialize_database
from tests.agent_fakes import decision_call
from tests.api_helpers import FIXTURE_DIR, seed


ADMIN_TOKEN = "i-050-admin"


def _memory() -> dict:
    return {
        "commitments_made": [],
        "commitments_or_offers_received": [],
        "outstanding_offers_and_negotiations": [],
        "red_lines_and_constraints": [],
        "uncertain_or_disputed_claims": [],
        "relationships_and_likely_coalitions": [],
        "strategic_assessment": "reconcile usage",
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


def _inconsistent_usage(index: int) -> RequestUsage:
    """Provider shape observed in T-060 run 12: detailed subsets exceed their parents."""
    return RequestUsage(
        input_tokens=10 * index,
        cache_read_tokens=20 * index,
        cache_write_tokens=15 * index,
        output_tokens=10 * index,
        output_reasoning_tokens=12 * index,
    )


def _player(
    engine: Engine,
    creation,
    model: FunctionModel,
    *,
    max_iterations: int = 8,
    activation_mode: str = "tool_loop",
    max_message_chars: int = 2_000,
) -> ReferencePlayer:
    api_client = TestClient(create_app(engine=engine), raise_server_exceptions=False)
    tools = PlayerTools(
        api_client,
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    return ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(
            model="fake/accounting",
            activation_mode=activation_mode,
            guardrails=GuardrailConfig(
                max_llm_iterations=max_iterations,
                max_message_chars=max_message_chars,
            ),
        ),
    )


def _coordinator(engine: Engine) -> AutonomousGameRunner:
    api_client = TestClient(
        create_app(
            engine=engine,
            settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None),
        ),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        api_client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=lambda *_args: None,  # unused by these focused persistence checks
    )


def test_i050_three_requests_reconcile_activation_agentrun_export_and_summary(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(messages, _info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages) + 1
        parts = (
            [ToolCallPart(tool_name="update_strategic_memory", args=_memory())]
            if index < 3
            else [TextPart(content="done")]
        )
        return ModelResponse(parts=parts, usage=_usage(index), model_name="fake/accounting-v1")

    runner = LifecycleRunner(_player(db_engine, creation, FunctionModel(policy)))
    outcome = runner.step()
    activation = outcome.activation
    assert activation is not None
    assert activation.usage == {
        "input_tokens": 600,
        "output_tokens": 60,
        "reasoning_tokens": 18,
        "cache_read_tokens": 120,
        "cache_write_tokens": 30,
        "requests": 3,
        "usage_reported_requests": 3,
        "billed_requests": 0,
        "tool_calls": 2,
        "cost_usd": None,
        "details": {"output_reasoning_tokens": 18},
        "request_usage": [
            {
                "request_number": index,
                "input_tokens": 100 * index,
                "output_tokens": 10 * index,
                "reasoning_tokens": 3 * index,
                "cache_read_tokens": 20 * index,
                "cache_write_tokens": 5 * index,
                "cost_usd": None,
                "details": {"output_reasoning_tokens": 3 * index},
            }
            for index in (1, 2, 3)
        ],
    }

    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, outcome)
    exported = export_module.build_game_export(db_engine, creation.game_id)
    [record] = exported["agent_runs"]
    assert {
        name: record[name]
        for name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        )
    } == {
        "input_tokens": 600,
        "output_tokens": 60,
        "reasoning_tokens": 18,
        "cache_read_tokens": 120,
        "cache_write_tokens": 30,
    }
    assert record["provider_metadata"]["usage"]["request_usage"] == activation.usage[
        "request_usage"
    ]
    assert exported["run_summary"]["input_tokens"] == 600
    assert exported["run_summary"]["reasoning_tokens"] == 18
    assert exported["run_summary"]["cache_read_tokens"] == 120
    assert exported["run_summary"]["cache_write_tokens"] == 30


def test_i050_usage_survives_framework_rejection_and_failed_agentrun(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def reject_forever(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages) + 1
        return ModelResponse(
            parts=[
                decision_call(
                    info,
                    {
                        "messages": [
                            {"recipients": [], "public": True, "text": "far too long"}
                        ],
                        "strategic_memory": _memory(),
                        "ready": True,
                    },
                )
            ],
            usage=_usage(index),
        )

    runner = LifecycleRunner(
        _player(
            db_engine,
            creation,
            FunctionModel(reject_forever),
            # Four requests across both logical attempts: the ceiling is activation-wide (I-077),
            # so it must admit the retry's two requests as well as the first attempt's.
            max_iterations=4,
            activation_mode="single_shot",
            max_message_chars=4,
        )
    )
    outcome = runner.step()
    assert outcome.failure is not None
    # PydanticAI rejected two outputs in each of AMAW's two logical attempts. All four returned
    # usage records survive even though no run produced an acceptable decision.
    assert outcome.failure.counts["usage"]["input_tokens"] == 600
    assert outcome.failure.counts["usage"]["request_usage"][1]["request_number"] == 2

    _coordinator(db_engine)._record_failure(str(creation.game_id), runner, outcome)
    [record] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    assert record["completed"] is False
    assert record["input_tokens"] == 600
    assert record["output_tokens"] == 60
    assert record["reasoning_tokens"] == 18
    assert record["cache_read_tokens"] == 120
    assert record["cache_write_tokens"] == 30
    assert len(record["provider_metadata"]["usage"]["request_usage"]) == 4


def test_i050_usage_survives_outer_logical_retry(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    calls = 0

    def transient_after_usage(_messages, _info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="update_strategic_memory", args=_memory())],
                usage=_usage(1),
            )
        if calls == 2:
            raise ModelHTTPError(503, "fake/accounting", {"error": "retry"})
        return ModelResponse(parts=[TextPart(content="recovered")], usage=_usage(3))

    activation = _player(db_engine, creation, FunctionModel(transient_after_usage)).activate()
    assert activation.model_iterations == 3
    assert activation.usage["input_tokens"] == 400
    assert activation.usage["output_tokens"] == 40
    assert [item["request_number"] for item in activation.usage["request_usage"]] == [1, 3]


def test_i106_001_inconsistent_usage_is_normalized_before_agentrun_persistence(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(_messages, _info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        return ModelResponse(parts=[TextPart(content="done")], usage=_inconsistent_usage(1))

    runner = LifecycleRunner(_player(db_engine, creation, FunctionModel(policy)))
    outcome = runner.step()
    activation = outcome.activation
    assert activation is not None
    assert activation.usage["input_tokens"] == 20
    assert activation.usage["output_tokens"] == 12
    assert activation.usage["reasoning_tokens"] == 12
    [request] = activation.usage["request_usage"]
    assert request["usage_normalization"] == {
        "input_tokens_reported": 10,
        "input_tokens_recorded": 20,
        "cache_read_tokens_reported": 20,
        "cache_write_tokens_reported": 15,
        "output_tokens_reported": 10,
        "output_tokens_recorded": 12,
        "reasoning_tokens_reported": 12,
    }

    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, outcome)
    exported = export_module.build_game_export(db_engine, creation.game_id)
    [record] = exported["agent_runs"]
    assert record["input_tokens"] == 20
    assert record["output_tokens"] == record["reasoning_tokens"] == 12
    assert record["provider_metadata"]["usage"]["request_usage"] == [request]
    assert exported["run_summary"]["input_tokens"] == 20
    assert exported["run_summary"]["output_tokens"] == 12
    assert exported["run_summary"]["reasoning_tokens"] == 12


def test_i106_002_inconsistent_usage_survives_framework_rejection_and_retry(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def reject_forever(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages) + 1
        return ModelResponse(
            parts=[
                decision_call(
                    info,
                    {
                        "messages": [
                            {"recipients": [], "public": True, "text": "far too long"}
                        ],
                        "strategic_memory": _memory(),
                        "ready": True,
                    },
                )
            ],
            usage=_inconsistent_usage(index),
        )

    runner = LifecycleRunner(
        _player(
            db_engine,
            creation,
            FunctionModel(reject_forever),
            max_iterations=4,
            activation_mode="single_shot",
            max_message_chars=4,
        )
    )
    outcome = runner.step()
    assert outcome.failure is not None
    usage = outcome.failure.counts["usage"]
    assert usage["input_tokens"] == 120
    assert usage["output_tokens"] == usage["reasoning_tokens"] == 72
    assert len(usage["request_usage"]) == 4
    assert all(
        request["input_tokens"] >= request["cache_read_tokens"]
        and request["input_tokens"] >= request["cache_write_tokens"]
        and request["output_tokens"] >= request["reasoning_tokens"]
        and request["usage_normalization"]["output_tokens_reported"]
        < request["usage_normalization"]["output_tokens_recorded"]
        for request in usage["request_usage"]
    )

    _coordinator(db_engine)._record_failure(str(creation.game_id), runner, outcome)
    [record] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    assert record["completed"] is False
    assert record["input_tokens"] == 120
    assert record["output_tokens"] == record["reasoning_tokens"] == 72
    assert len(record["provider_metadata"]["usage"]["request_usage"]) == 4


def test_i050_additive_migration_adds_token_breakdown_columns(tmp_path: Path) -> None:
    engine = engine_for_path(tmp_path / "legacy-token-accounting.db")
    initialize_database(engine)
    existing = {column["name"] for column in inspect(engine).get_columns("agentrun")}
    with engine.begin() as connection:
        for name in (
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
        ):
            if name in existing:
                connection.exec_driver_sql(f"ALTER TABLE agentrun DROP COLUMN {name}")

    initialize_database(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("agentrun")}
    assert {"reasoning_tokens", "cache_read_tokens", "cache_write_tokens"} <= columns
