"""I-117: player model calls record the provider-reported cost, or record it as unavailable.

PydanticAI's OpenRouter adapter surfaces OpenRouter's ``usage.cost`` on
``ModelResponse.provider_details["cost"]``. ``RequestUsage.cost`` is a local genai-prices
estimate and never a provider report, so it must not be recorded (D-039: cost is never
fabricated). ``None`` means unavailable; an explicit ``0.0`` means a known zero cost.
"""

from __future__ import annotations

import json
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.openrouter import OpenRouterModel
from pydantic_ai.providers.openrouter import OpenRouterProvider
from pydantic_ai.usage import RequestUsage
from sqlalchemy import Engine

from agents.autonomous import AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import (
    RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    GuardrailConfig,
    PlayerConfig,
    ReferencePlayer,
    _TimedModel,
)
from agents.tools import PlayerTools
from app import export as export_module, game_service, provenance
from app.api import create_app
from app.config import Settings
from app.summary import build_game_summary, format_game_summary
from tests.agent_fakes import decision_call
from tests.api_helpers import FIXTURE_DIR, seed


ADMIN_TOKEN = "i-117-admin"


def _memory() -> dict:
    return {
        "commitments_made": [],
        "commitments_or_offers_received": [],
        "outstanding_offers_and_negotiations": [],
        "red_lines_and_constraints": [],
        "uncertain_or_disputed_claims": [],
        "relationships_and_likely_coalitions": [],
        "strategic_assessment": "account for cost",
        "intended_next_action": "",
    }


def _usage(index: int) -> RequestUsage:
    return RequestUsage(input_tokens=100 * index, output_tokens=10 * index)


def _details(cost: float | None) -> dict | None:
    return {"downstream_provider": "fake", "cost": cost} if cost is not None else None


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
        api_client, game_id=str(creation.game_id), token=creation.seat_tokens["arkania"]
    )
    return ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(
            model="fake/cost",
            activation_mode=activation_mode,
            guardrails=GuardrailConfig(
                max_llm_iterations=max_iterations, max_message_chars=max_message_chars
            ),
        ),
    )


def _coordinator(engine: Engine) -> AutonomousGameRunner:
    api_client = TestClient(
        create_app(engine=engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        api_client,
        admin_token=ADMIN_TOKEN,
        scenario=FIXTURE_DIR,
        player_factory=lambda *_args: None,
    )


def _sequenced(costs: list[float | None], *, usage_cost: Decimal | None = None) -> FunctionModel:
    """One request per entry: tool calls until the last entry, which ends the run."""

    def policy(messages, _info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages) + 1
        parts = (
            [ToolCallPart(tool_name="update_strategic_memory", args=_memory())]
            if index < len(costs)
            else [TextPart(content="done")]
        )
        usage = _usage(index)
        usage.cost = usage_cost
        return ModelResponse(
            parts=parts,
            usage=usage,
            model_name="fake/cost-v1",
            provider_details=_details(costs[index - 1]),
        )

    return FunctionModel(policy)


def _record(db_engine: Engine, creation, model: FunctionModel) -> tuple[dict, dict]:
    runner = LifecycleRunner(_player(db_engine, creation, model))
    outcome = runner.step()
    assert outcome.activation is not None
    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, outcome)
    return outcome.activation.usage, export_module.build_game_export(db_engine, creation.game_id)


def test_i117_001_provider_reported_cost_is_recorded(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    usage, exported = _record(db_engine, creation, _sequenced([0.00125]))
    [record] = exported["agent_runs"]
    assert usage["request_usage"][0]["cost_usd"] == 0.00125
    assert record["cost_usd"] == 0.00125
    assert record["costed_model_calls"] == 1
    assert record["model_calls"] == 1
    assert (record["input_tokens"], record["output_tokens"]) == (100, 10)
    assert exported["run_summary"]["estimated_cost_usd"] == 0.00125
    assert exported["run_summary"]["cost_coverage"] == {
        "calls_with_cost": 1, "total_calls": 1, "complete": True,
    }


def test_i117_002_missing_provider_cost_is_null_not_zero(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    usage, exported = _record(db_engine, creation, _sequenced([None]))
    [record] = exported["agent_runs"]
    assert usage["cost_usd"] is None
    assert record["cost_usd"] is None
    assert record["costed_model_calls"] == 0
    assert (record["input_tokens"], record["output_tokens"]) == (100, 10)
    assert exported["run_summary"]["estimated_cost_usd"] is None


def test_i117_003_explicit_zero_is_a_known_cost(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    _usage_dict, exported = _record(db_engine, creation, _sequenced([0.0]))
    [record] = exported["agent_runs"]
    assert record["cost_usd"] == 0.0
    assert record["cost_usd"] is not None
    assert record["costed_model_calls"] == 1
    assert exported["run_summary"]["cost_coverage"]["complete"] is True


def test_i117_004_multiple_requests_sum_each_cost_once(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    usage, exported = _record(db_engine, creation, _sequenced([0.001, 0.002, 0.004]))
    [record] = exported["agent_runs"]
    assert [item["cost_usd"] for item in usage["request_usage"]] == [0.001, 0.002, 0.004]
    assert record["cost_usd"] == pytest.approx(0.007)
    assert record["costed_model_calls"] == 3
    assert record["model_calls"] == 3
    assert record["input_tokens"] == 600


def test_i117_005_partial_coverage_is_reported_as_partial(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    _usage_dict, exported = _record(db_engine, creation, _sequenced([0.001, None, 0.004]))
    [record] = exported["agent_runs"]
    assert record["cost_usd"] == pytest.approx(0.005)
    assert record["costed_model_calls"] == 2
    assert record["model_calls"] == 3
    summary = exported["run_summary"]
    assert summary["cost_coverage"] == {
        "calls_with_cost": 2, "total_calls": 3, "complete": False,
    }
    assert "(partial: 2/3 calls)" in format_game_summary(summary)


def test_i117_006_local_price_estimate_is_never_recorded_as_provider_cost(
    db_engine: Engine,
) -> None:
    # RequestUsage.cost is PydanticAI's genai-prices estimate, not an OpenRouter report.
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    _usage_dict, exported = _record(
        db_engine, creation, _sequenced([None], usage_cost=Decimal("0.5"))
    )
    [record] = exported["agent_runs"]
    assert record["cost_usd"] is None
    assert record["costed_model_calls"] == 0


def test_i117_007_cost_survives_outer_transient_retry(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    calls = 0

    def transient_after_cost(_messages, _info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(
                parts=[ToolCallPart(tool_name="update_strategic_memory", args=_memory())],
                usage=_usage(1),
                provider_details=_details(0.003),
            )
        if calls == 2:
            raise ModelHTTPError(503, "fake/cost", {"error": "retry"})
        return ModelResponse(
            parts=[TextPart(content="recovered")],
            usage=_usage(3),
            provider_details=_details(0.005),
        )

    runner = LifecycleRunner(_player(db_engine, creation, FunctionModel(transient_after_cost)))
    outcome = runner.step()
    assert outcome.activation is not None
    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, outcome)
    [record] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    # Three represented requests; the one that raised returned no usage and no cost.
    assert record["model_calls"] == 3
    assert record["cost_usd"] == pytest.approx(0.008)
    assert record["costed_model_calls"] == 2


def test_i117_008_failed_activation_keeps_incurred_cost(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def reject_forever(_messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        return ModelResponse(
            parts=[
                decision_call(
                    info,
                    {
                        "messages": [{"recipients": [], "public": True, "text": "far too long"}],
                        "strategic_memory": _memory(),
                        "ready": True,
                    },
                )
            ],
            usage=_usage(1),
            provider_details=_details(0.002),
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
    assert outcome.failure.counts["usage"]["cost_usd"] == pytest.approx(0.008)
    _coordinator(db_engine)._record_failure(str(creation.game_id), runner, outcome)
    exported = export_module.build_game_export(db_engine, creation.game_id)
    [record] = exported["agent_runs"]
    assert record["completed"] is False
    assert record["model_calls"] == 4
    assert record["cost_usd"] == pytest.approx(0.008)
    assert record["costed_model_calls"] == 4
    assert exported["run_summary"]["estimated_cost_usd"] == pytest.approx(0.008)


def test_i117_009_summary_combines_player_and_umpire_cost(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    _record(db_engine, creation, _sequenced([0.001, None]))
    provenance.record_agent_run(
        db_engine,
        creation.game_id,
        actor_id=None,
        role="umpire",
        turn=1,
        phase="adjudication",
        model_requested="model/umpire",
        model_returned="model/umpire",
        provider="fake",
        prompt_version="umpire-v1",
        prompt_hash="b" * 64,
        model_parameters={},
        reasoning_configuration=None,
        agent_framework="httpx",
        agent_framework_version=None,
        tool_schema_version="umpire-v1",
        input_tokens=50,
        output_tokens=5,
        model_calls=1,
        costed_model_calls=1,
        tool_calls=0,
        invalid_tool_calls=0,
        stalls=0,
        cost_usd=0.002,
        latency_ms=10,
        completed=True,
        failure_reason=None,
    )
    summary = build_game_summary(db_engine, creation.game_id)
    assert summary["estimated_cost_usd"] == pytest.approx(0.003)
    assert summary["cost_coverage"] == {
        "calls_with_cost": 2, "total_calls": 3, "complete": False,
    }


def _openrouter_completion(usage: dict) -> dict:
    return {
        "id": "gen-i117",
        "object": "chat.completion",
        "created": 1_790_000_000,
        "model": "deepseek/deepseek-v4-flash-0731",
        "provider": "DeepSeek",
        "choices": [
            {
                "index": 0,
                "finish_reason": "stop",
                "native_finish_reason": "stop",
                "message": {"role": "assistant", "content": "done"},
            }
        ],
        "usage": usage,
    }


def _run_through_openrouter_adapter(usage: dict) -> tuple[list[dict], list[dict]]:
    """Drive PydanticAI's real OpenRouter adapter over a mocked HTTP transport."""
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=_openrouter_completion(usage))

    client = AsyncOpenAI(
        api_key="test-key",
        base_url="https://openrouter.ai/api/v1",
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    model = _TimedModel(
        OpenRouterModel(
            "deepseek/deepseek-v4-flash-0731",
            provider=OpenRouterProvider(openai_client=client),
        ),
        research_history_mode=RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    )
    Agent(model).run_sync("hello")
    return model.metrics()[2], sent


def test_i117_010_openrouter_adapter_cost_reaches_request_usage() -> None:
    # Pins the PydanticAI boundary: an upgrade that moves OpenRouter's cost fails here loudly.
    request_usage, sent = _run_through_openrouter_adapter(
        {"prompt_tokens": 40, "completion_tokens": 6, "total_tokens": 46, "cost": 0.000321}
    )
    [item] = request_usage
    assert item["cost_usd"] == 0.000321
    assert (item["input_tokens"], item["output_tokens"]) == (40, 6)
    # OpenRouter reports cost without an opt-in, so the player request body is unchanged.
    assert "usage" not in sent[0]


def test_i117_011_openrouter_adapter_drops_explicit_zero_cost() -> None:
    # PydanticAI 2.42 copies usage.cost only when truthy, so a provider-reported 0 arrives as
    # unavailable. AMAW records that as null (unknown), never as an invented zero. If this
    # starts failing, the adapter now forwards zero and I-117's explicit-zero path applies.
    request_usage, _sent = _run_through_openrouter_adapter(
        {"prompt_tokens": 40, "completion_tokens": 6, "total_tokens": 46, "cost": 0}
    )
    assert request_usage[0]["cost_usd"] is None
