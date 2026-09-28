"""I-043: single-shot Cons decisions respect the server's remaining Con capacity."""

from __future__ import annotations

from dataclasses import replace
import json

import pytest
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import Engine
from sqlmodel import select

from agents.decisions import ConsDecision, validate_decision
from agents.guardrails import GuardrailConfig
from agents.player import ActivationFailed, PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools, ToolResult
from app import game_service
from app.database import session_scope
from app.activation_context import EMPTY_STRATEGIC_MEMORY
from app.models import Con
from tests.agent_fakes import decision_call, model_from_decision_policy
from tests.api_helpers import client, seed, start_to_cons


def _memory() -> dict:
    return json.loads(json.dumps(EMPTY_STRATEGIC_MEMORY))


def test_i043_context_and_packet_expose_remaining_con_capacity(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)

    initial = client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers={"Authorization": f"Bearer {creation.seat_tokens['belvar']}"},
    ).json()
    assert initial["con_slots_remaining"] == 3
    owner = client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers={"Authorization": f"Bearer {creation.seat_tokens['arkania']}"},
    ).json()
    assert owner["con_slots_remaining"] is None

    game_service.make_con(
        db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar", reason="first"
    )
    game_service.make_con(
        db_engine, creation.game_id, argument_id=argument.id, actor_id="belvar", reason="second"
    )

    player = ReferencePlayer(
        # The model is not run; packet construction is the assertion boundary here.
        tools=PlayerTools(
            client(db_engine),
            game_id=str(creation.game_id),
            token=creation.seat_tokens["belvar"],
        ),
        actor_id="belvar",
        actor_name="Belvar",
        model=model_from_decision_policy(lambda packet, idx, info: []),
        config=PlayerConfig(model="fake/deterministic"),
    )
    packet = player.build_packet()

    assert packet["limits"]["con_slots_remaining"] == 1


def test_i043_oversized_cons_decision_is_rejected_before_execution() -> None:
    decision = ConsDecision(cons=["one", "two"], strategic_memory=_memory())

    error = validate_decision(
        decision,
        guardrails=GuardrailConfig(),
        message_budget=None,
        con_allowed=True,
        con_slots_remaining=1,
    )

    assert error is not None
    assert error["error"]["code"] == "guardrail_con_capacity_exceeded"
    assert error["error"]["details"] == {"limit": 1, "actual": 2}


def test_i043_missing_capacity_fails_closed_before_execution() -> None:
    decision = ConsDecision(cons=[], strategic_memory=_memory())

    error = validate_decision(
        decision,
        guardrails=GuardrailConfig(),
        message_budget=None,
        con_allowed=True,
        con_slots_remaining=None,
    )

    assert error == {
        "ok": False,
        "http_status": None,
        "error": {
            "code": "guardrail_con_capacity_missing",
            "message": "make_con is available but remaining Con capacity was not supplied",
            "details": None,
        },
    }


class _MissingCapacityTools(PlayerTools):
    """Simulate an older/custom activation context that drops the new capacity field."""

    def begin_activation(self) -> ToolResult:
        delivery = super().begin_activation()
        assert delivery.data is not None
        data = dict(delivery.data)
        data.pop("con_slots_remaining", None)
        return replace(delivery, data=data)


def test_i043_missing_capacity_activation_makes_no_con_request(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_cons(db_engine, creation)
    tools = _MissingCapacityTools(
        client(db_engine),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["belvar"],
    )

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        return [decision_call(info, {
            "cons": ["never sent"],
            "strategic_memory": packet["strategic_memory"],
        })]

    player = ReferencePlayer(
        tools=tools,
        actor_id="belvar",
        actor_name="Belvar",
        model=model_from_decision_policy(policy),
        config=PlayerConfig(
            model="fake/deterministic",
            activation_mode="single_shot",
            guardrails=GuardrailConfig(max_llm_iterations=2),
        ),
    )

    with pytest.raises(ActivationFailed):
        player.activate()

    with session_scope(db_engine) as session:
        assert [con.reason for con in session.exec(select(Con)).all()] == []


def test_i043_one_remaining_slot_executes_at_most_one_make_con(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    for reason in ("first", "second"):
        game_service.make_con(
            db_engine,
            creation.game_id,
            argument_id=argument.id,
            actor_id="belvar",
            reason=reason,
        )

    tools = PlayerTools(
        client(db_engine),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["belvar"],
    )

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        return [decision_call(info, {
            "cons": ["third", "never sent"] if idx == 0 else ["third"],
            "strategic_memory": packet["strategic_memory"],
        })]

    player = ReferencePlayer(
        tools=tools,
        actor_id="belvar",
        actor_name="Belvar",
        model=model_from_decision_policy(policy),
        config=PlayerConfig(model="fake/deterministic", activation_mode="single_shot"),
    )
    result = player.activate()

    assert result.invalid_tool_calls == 1
    assert result.tool_call_names == ["make_con"]
    with session_scope(db_engine) as session:
        reasons = [con.reason for con in session.exec(select(Con)).all()]
    assert reasons == ["first", "second", "third"]
