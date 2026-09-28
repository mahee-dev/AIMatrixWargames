"""I-033 acceptance: one retry-safe outgoing Message budget per Actor activation."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelMessage, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine

from agents.autonomous import AutonomousGameRunner
from agents.decisions import DiplomacyDecision, validate_decision
from agents.guardrails import GuardrailConfig
from agents.lifecycle import LifecycleRunner
from agents.player import PLAYER_PROMPT_VERSIONS, PlayerConfig, ReferencePlayer
from agents.scripted import deterministic_umpire
from agents.tools import PlayerTools
from app.activation_context import EMPTY_STRATEGIC_MEMORY
from app.api import create_app
from app.config import Settings
from tests.agent_fakes import (
    decision_call,
    default_decision_policy,
    extract_packet,
    model_from_decision_policy,
)
from tests.api_helpers import ACTORS, client, seed, start_to_diplomacy
from tests.benchmark_helpers import SHORT_TURNS


ADMIN_TOKEN = "admin-secret-token"


def _memory(text: str = "") -> dict:
    memory = json.loads(json.dumps(EMPTY_STRATEGIC_MEMORY))
    memory["strategic_assessment"] = text
    return memory


def _tools(engine: Engine, creation, actor: str) -> PlayerTools:
    return PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )


def _player(
    engine: Engine, creation, actor: str, model, *, mode: str = "tool_loop",
    activation_budget: int = 3, phase_budget: int = 10,
) -> ReferencePlayer:
    tools = _tools(engine, creation, actor)
    return ReferencePlayer(
        tools,
        actor_id=actor,
        actor_name=tools.observe().data["you"]["name"],
        model=model,
        config=PlayerConfig(
            model="fake/deterministic",
            activation_mode=mode,
            guardrails=GuardrailConfig(
                max_messages_per_activation=activation_budget,
                max_messages_per_phase=phase_budget,
                max_activations_per_opportunity=50,
            ),
        ),
    )


def _message(text: str) -> ToolCallPart:
    return ToolCallPart(
        tool_name="send_message",
        args={"recipients": [], "public": True, "text": text},
    )


def test_i033_002_packet_exposes_activation_and_phase_remaining(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen: list[tuple[int, int]] = []

    def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        packet = extract_packet(messages)
        idx = sum(isinstance(message, ModelResponse) for message in messages)
        if idx == 0:
            limits = packet["limits"]
            seen.append(
                (limits["messages_remaining_this_activation"], limits["messages_remaining_this_phase"])
            )
            return ModelResponse(parts=[_message("one")])
        return ModelResponse(parts=[TextPart(content="done")])

    runner = LifecycleRunner(
        _player(db_engine, creation, "arkania", FunctionModel(policy), activation_budget=3,
                phase_budget=4)
    )
    runner.step()
    runner.step()

    assert seen == [(3, 4), (3, 3)]


def test_i033_003_single_shot_exact_budget_is_valid_and_excess_is_retried_before_mutation(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    exact = DiplomacyDecision(
        messages=[
            {"recipients": [], "public": True, "text": "one"},
            {"recipients": [], "public": True, "text": "two"},
        ],
        strategic_memory=_memory(),
        ready=True,
    )
    assert validate_decision(
        exact, guardrails=GuardrailConfig(max_messages_per_activation=2),
        message_budget=2, con_allowed=False, con_slots_remaining=None,
    ) is None

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        count = 3 if idx == 0 else 2
        return [decision_call(info, {
            "messages": [
                {"recipients": [], "public": True, "text": f"accepted-{i}"}
                for i in range(count)
            ],
            "strategic_memory": _memory(),
            "ready": True,
        })]

    player = _player(
        db_engine, creation, "arkania", model_from_decision_policy(policy),
        mode="single_shot", activation_budget=2,
    )
    result = LifecycleRunner(player).step().activation

    assert result is not None
    assert result.invalid_tool_calls == 1
    assert result.messages_sent == 2
    assert [m["text"] for m in player.tools.read_messages().data["messages"]] == [
        "accepted-0", "accepted-1"
    ]


def test_i033_003_single_shot_validates_against_lower_remaining_phase_capacity(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen_remaining: list[int] = []

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        seen_remaining.append(packet["limits"]["messages_remaining_this_activation"])
        count = 2 if idx == 0 else 1
        return [decision_call(info, {
            "messages": [
                {"recipients": [], "public": True, "text": f"remaining-{i}"}
                for i in range(count)
            ],
            "strategic_memory": _memory(),
            "ready": True,
        })]

    player = _player(
        db_engine, creation, "arkania", model_from_decision_policy(policy),
        mode="single_shot", activation_budget=3, phase_budget=10,
    )
    # Simulate the authoritative lifecycle's remaining phase capacity after prior committed sends.
    player.set_message_budget(1)
    result = player.activate()

    assert seen_remaining == [1, 1]
    assert result.invalid_tool_calls == 1 and result.messages_sent == 1
    assert [m["text"] for m in player.tools.read_messages().data["messages"]] == ["remaining-0"]


def test_i033_004_zero_budget_blocks_messages_but_tool_loop_continues(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    observed_errors: list[str] = []

    def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        idx = sum(isinstance(message, ModelResponse) for message in messages)
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "send_message":
                    observed_errors.append(str(part.content))
        if idx == 0:
            return ModelResponse(parts=[_message("blocked")])
        if idx == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="update_strategic_memory", args=_memory("continued")
            )])
        if idx == 2:
            return ModelResponse(parts=[ToolCallPart(tool_name="ready", args={})])
        return ModelResponse(parts=[TextPart(content="done")])

    player = _player(
        db_engine, creation, "arkania", FunctionModel(policy), activation_budget=0
    )
    result = LifecycleRunner(player).step().activation

    assert result is not None and result.messages_sent == 0
    assert any("guardrail_message_activation_budget_exhausted" in error for error in observed_errors)
    assert result.tool_call_names == ["send_message", "update_strategic_memory", "ready"]
    assert player.tools.read_messages().data["messages"] == []
    assert "ready" not in player.tools.observe().data["available_actions"]


def test_i033_005_committed_messages_survive_model_retry_and_only_remaining_is_available(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = 0
    retry_returns: list[str] = []
    attempt_budgets: list[int] = []

    def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls in (1, 3):
            attempt_budgets.append(
                extract_packet(messages)["limits"]["messages_remaining_this_activation"]
            )
        for message in messages:
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart):
                    retry_returns.append(str(part.content))
        if calls == 1:
            return ModelResponse(parts=[_message("A"), _message("B")])
        if calls == 2:
            raise UnexpectedModelBehavior("retry after committed sends")
        if calls == 3:
            return ModelResponse(parts=[_message("C"), _message("D")])
        return ModelResponse(parts=[TextPart(content="done")])

    player = _player(
        db_engine, creation, "arkania", FunctionModel(policy), activation_budget=3
    )
    result = LifecycleRunner(player).step().activation

    assert result is not None and result.messages_sent == 3
    assert attempt_budgets == [3, 1]
    # PydanticAI runs the two send_message calls of one model response on separate worker
    # threads (I-021 serialises the budget check, not the commit order), so A/B may land in
    # either order and either of C/D may win the last slot (I-058). Only membership and the
    # count are contractual: both pre-retry sends survive, exactly one more fits.
    texts = [m["text"] for m in player.tools.read_messages().data["messages"]]
    assert sorted(texts[:2]) == ["A", "B"]
    assert len(texts) == 3 and texts[2] in ("C", "D")
    assert any("guardrail_message_activation_budget_exhausted" in item for item in retry_returns)


def test_i033_005_idempotent_replay_and_rejected_send_do_not_double_charge(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = 0

    def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        nonlocal calls
        calls += 1
        if calls == 1:
            return ModelResponse(parts=[_message("same")])
        if calls == 2:
            raise UnexpectedModelBehavior("retry")
        if calls == 3:
            # Same operation/payload/opportunity namespace: server replay, not a second spend.
            return ModelResponse(parts=[
                _message("same"),
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": ["nobody"], "public": False, "text": "rejected"},
                ),
                _message("new"),
            ])
        return ModelResponse(parts=[TextPart(content="done")])

    player = _player(
        db_engine, creation, "arkania", FunctionModel(policy), activation_budget=2
    )
    result = LifecycleRunner(player).step().activation

    assert result is not None and result.messages_sent == 2
    assert [m["text"] for m in player.tools.read_messages().data["messages"]] == ["same", "new"]


def test_i033_006_activation_and_phase_ceilings_both_apply(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        idx = sum(isinstance(message, ModelResponse) for message in messages)
        if idx == 0:
            return ModelResponse(parts=[_message("first"), _message("second")])
        return ModelResponse(parts=[TextPart(content="done")])

    runner = LifecycleRunner(
        _player(db_engine, creation, "arkania", FunctionModel(policy), activation_budget=1,
                phase_budget=2)
    )
    for _ in range(4):
        runner.step()

    mine = [
        m for m in runner.player.tools.read_messages().data["messages"]
        if m["sender_actor_id"] == "arkania"
    ]
    assert len(mine) == 2


def test_i033_007_concurrent_actors_have_independent_activation_budgets(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def make_runner(actor: str) -> LifecycleRunner:
        def policy(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
            idx = sum(isinstance(message, ModelResponse) for message in messages)
            if idx == 0:
                return ModelResponse(parts=[_message(f"{actor}-1"), _message(f"{actor}-2")])
            return ModelResponse(parts=[TextPart(content="done")])
        return LifecycleRunner(
            _player(db_engine, creation, actor, FunctionModel(policy), activation_budget=1)
        )

    runners = {actor: make_runner(actor) for actor in ACTORS}
    contexts = {actor: runner.observe() for actor, runner in runners.items()}
    with ThreadPoolExecutor(max_workers=3) as pool:
        outcomes = list(pool.map(lambda actor: runners[actor].step(contexts[actor]), ACTORS))

    assert all(outcome.activation and outcome.activation.messages_sent == 1 for outcome in outcomes)
    messages = runners["arkania"].player.tools.read_messages().data["messages"]
    assert {m["sender_actor_id"] for m in messages} == set(ACTORS)
    assert len(messages) == 3


def test_i033_008_manifest_agent_runs_and_new_prompts_record_budget(
    db_engine: Engine, short_game: Path
) -> None:
    def factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        return LifecycleRunner(ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_decision_policy(default_decision_policy),
            config=PlayerConfig(
                model="fake/deterministic",
                guardrails=GuardrailConfig(max_messages_per_activation=2),
            ),
        ))

    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    api = TestClient(
        create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire()),
        raise_server_exceptions=False,
    )
    result = AutonomousGameRunner(
        api, admin_token=ADMIN_TOKEN, scenario=short_game, player_factory=factory, seed=7,
    ).run()

    assert PLAYER_PROMPT_VERSIONS == {
        "tool_loop": "player-v1.10", "single_shot": "player-v1.11"
    }
    assert result.export["run_manifest"]["player"]["max_messages_per_activation"] == 2
    # The representative deterministic policy sends one Message per Actor/turn. It remains below
    # the new cap, so the run keeps its one-Message-per-Actor-per-turn shape.
    assert len(result.export["messages"]) == len(ACTORS) * SHORT_TURNS
    player_runs = [r for r in result.export["agent_runs"] if r["role"] == "player"]
    assert {r["model_parameters"]["guardrails"]["max_messages_per_activation"] for r in player_runs} == {2}
