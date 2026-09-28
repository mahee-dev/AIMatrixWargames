"""I-034: incremental inbound Message context and one rolling strategic memory."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine
from sqlmodel import select

from agents.lifecycle import LifecycleRunner
from agents.player import ActivationFailed, GuardrailConfig, PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app import game_service
from app.activation_context import CONTEXT_MODE, EMPTY_STRATEGIC_MEMORY, MEMORY_VERSION
from app.database import session_scope
from app.models import ActorStrategicState, Note
from tests.agent_fakes import (
    decision_call,
    model_from_decision_policy,
    model_from_policy,
)
from tests.api_helpers import client, seed


def _tools(engine: Engine, creation, actor: str) -> PlayerTools:
    return PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )


def _memory(label: str) -> dict:
    return {
        "commitments_made": [f"commitment {label}"],
        "commitments_or_offers_received": [f"received {label}"],
        "outstanding_offers_and_negotiations": [f"outstanding {label}"],
        "red_lines_and_constraints": [f"red line {label}"],
        "uncertain_or_disputed_claims": [f"uncertain {label}"],
        "relationships_and_likely_coalitions": [f"coalition {label}"],
        "strategic_assessment": f"assessment {label}",
        "intended_next_action": f"next {label}",
    }


def _send(engine: Engine, creation, sender: str, text: str, recipients=None) -> None:
    game_service.send_message(
        engine,
        creation.game_id,
        sender_actor_id=sender,
        text=text,
        public=recipients is None,
        recipients=[] if recipients is None else recipients,
    )


def test_i034_001_first_activation_receives_initial_authorised_context(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    _send(db_engine, creation, "belvar", "Public opening")
    _send(db_engine, creation, "cestia", "Private opening", ["arkania"])
    _send(db_engine, creation, "arkania", "Our own outgoing statement")

    delivery = _tools(db_engine, creation, "arkania").begin_activation().data

    assert delivery["context_mode"] == CONTEXT_MODE == "incremental-context-v1"
    assert delivery["memory_version"] == MEMORY_VERSION == "strategic-memory-v1"
    assert delivery["strategic_memory"] == EMPTY_STRATEGIC_MEMORY
    assert [m["text"] for m in delivery["messages_received"]] == [
        "Public opening", "Private opening"
    ]
    assert delivery["scenario"]["id"] == "deterministic-game"
    assert delivery["you"]["actor_id"] == "arkania"
    assert delivery["effects"]
    assert "available_actions" in delivery


def test_i034_002_acknowledged_activation_gets_only_newer_messages(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", "first")
    first = tools.begin_activation().data
    assert tools.acknowledge_activation(first["delivery_receipt"], _memory("one")).ok
    _send(db_engine, creation, "belvar", "second")

    second = tools.begin_activation().data
    assert [m["text"] for m in second["messages_received"]] == ["second"]
    assert second["strategic_memory"] == _memory("one")


def test_i034_003_read_messages_retains_complete_visible_history(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", "old")
    first = tools.begin_activation().data
    tools.acknowledge_activation(first["delivery_receipt"], _memory("one"))
    _send(db_engine, creation, "belvar", "new")
    assert [m["text"] for m in tools.read_messages().data["messages"]] == ["old", "new"]


def test_i034_004_private_message_and_memory_never_cross_actor(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    ark = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", "only Arkania", ["arkania"])
    delivery = ark.begin_activation().data
    foreign = _tools(db_engine, creation, "cestia").acknowledge_activation(
        delivery["delivery_receipt"], _memory("stolen")
    )
    assert not foreign.ok and foreign.http_status == 409
    ark.acknowledge_activation(delivery["delivery_receipt"], _memory("arkania-secret"))

    cestia = _tools(db_engine, creation, "cestia").begin_activation().data
    assert cestia["messages_received"] == []
    assert cestia["strategic_memory"] == EMPTY_STRATEGIC_MEMORY
    assert "arkania-secret" not in str(cestia)


def test_i034_005_failed_activation_does_not_acknowledge_messages(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    _send(db_engine, creation, "belvar", "must replay")
    tools = _tools(db_engine, creation, "arkania")

    def broken(messages, info):  # noqa: ANN001
        raise RuntimeError("model failed")

    player = ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=FunctionModel(broken),
        config=PlayerConfig(model="fake", activation_mode="tool_loop"),
    )
    with pytest.raises(ActivationFailed):
        player.activate()

    replay = _tools(db_engine, creation, "arkania").begin_activation().data
    assert [m["text"] for m in replay["messages_received"]] == ["must replay"]


def test_i034_005_stall_recovery_does_not_acknowledge_an_unseen_message(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    player = ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model_from_policy(
            lambda packet, tool_names, idx: [TextPart(content="no progress")]
        ),
        config=PlayerConfig(
            model="fake",
            activation_mode="tool_loop",
            guardrails=GuardrailConfig(max_activations_per_opportunity=1),
        ),
    )
    runner = LifecycleRunner(player)
    assert runner.step().acted  # successful, but deliberately leaves diplomacy open
    _send(db_engine, creation, "belvar", "arrived before stall recovery")
    stalled = runner.step()
    assert stalled.failure is not None and stalled.failure.category == "stall"

    delivery = tools.begin_activation().data
    assert [message["text"] for message in delivery["messages_received"]] == [
        "arrived before stall recovery"
    ]


def test_i034_006_fresh_player_resumes_persisted_boundary(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    first_tools = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", "acknowledged")
    first = first_tools.begin_activation().data
    first_tools.acknowledge_activation(first["delivery_receipt"], _memory("persisted"))
    _send(db_engine, creation, "belvar", "after restart")

    fresh_tools = _tools(db_engine, creation, "arkania")
    delivery = fresh_tools.begin_activation().data
    assert [m["text"] for m in delivery["messages_received"]] == ["after restart"]
    assert delivery["strategic_memory"] == _memory("persisted")


def test_i034_007_message_after_snapshot_waits_for_next_batch_without_duplication(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", "inside snapshot")
    pending = tools.begin_activation().data
    _send(db_engine, creation, "cestia", "after snapshot")
    replay = tools.begin_activation().data
    assert replay["delivery_receipt"] == pending["delivery_receipt"]
    assert [m["text"] for m in replay["messages_received"]] == ["inside snapshot"]
    tools.acknowledge_activation(pending["delivery_receipt"], _memory("one"))

    next_batch = tools.begin_activation().data
    assert [m["text"] for m in next_batch["messages_received"]] == ["after snapshot"]


def test_i034_008_memory_replaces_one_state_and_does_not_append_notes(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    first = tools.begin_activation().data
    tools.acknowledge_activation(first["delivery_receipt"], _memory("one"))
    second = tools.begin_activation().data
    tools.acknowledge_activation(second["delivery_receipt"], _memory("two"))

    with session_scope(db_engine) as session:
        states = list(session.exec(select(ActorStrategicState)).all())
        assert len(states) == 1
        assert states[0].strategic_memory == _memory("two")
        assert list(session.exec(select(Note)).all()) == []


def test_i034_009_memory_is_game_scoped_when_actor_ids_repeat(db_engine: Engine) -> None:
    game_a = seed(db_engine)
    game_b = seed(db_engine)
    game_service.start_game(db_engine, game_a.game_id)
    game_service.start_game(db_engine, game_b.game_id)
    a = _tools(db_engine, game_a, "arkania")
    pending = a.begin_activation().data
    a.acknowledge_activation(pending["delivery_receipt"], _memory("game-a"))

    assert _tools(db_engine, game_b, "arkania").begin_activation().data[
        "strategic_memory"
    ] == EMPTY_STRATEGIC_MEMORY


@pytest.mark.parametrize("mode", ["single_shot", "tool_loop"])
def test_i034_010_both_activation_modes_acknowledge_success(
    db_engine: Engine, mode: str
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", f"for {mode}")
    if mode == "single_shot":
        def decide(packet: dict, idx: int, info: AgentInfo):
            memory = _memory(mode)
            return [decision_call(info, {
                "messages": [], "strategic_memory": memory, "ready": True
            })]
        model = model_from_decision_policy(decide)
    else:
        def loop(packet: dict, tool_names: list[str], idx: int):
            if idx == 0:
                return [ToolCallPart(tool_name="update_strategic_memory", args=_memory(mode))]
            if idx == 1:
                return [ToolCallPart(tool_name="ready", args={})]
            return [TextPart(content="done")]
        model = model_from_policy(loop)
    player = ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(model="fake", activation_mode=mode),
    )
    player.activate()
    next_delivery = tools.begin_activation().data
    assert next_delivery["messages_received"] == []
    assert next_delivery["strategic_memory"] == _memory(mode)


def test_i034_ack_replay_is_idempotent_and_conflicting_replay_is_refused(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    pending = tools.begin_activation().data
    receipt = pending["delivery_receipt"]
    assert tools.acknowledge_activation(receipt, _memory("same")).ok
    assert tools.acknowledge_activation(receipt, _memory("same")).ok
    conflict = tools.acknowledge_activation(receipt, _memory("different"))
    assert not conflict.ok
    assert conflict.http_status == 409
    assert conflict.error_code == "idempotency_conflict"


def test_i034_invalid_memory_does_not_advance_pending_delivery(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    _send(db_engine, creation, "belvar", "still pending")
    pending = tools.begin_activation().data
    invalid = tools.acknowledge_activation(
        pending["delivery_receipt"], {"strategic_assessment": "incomplete"}
    )
    assert not invalid.ok and invalid.http_status == 422
    replay = tools.begin_activation().data
    assert replay["delivery_receipt"] == pending["delivery_receipt"]
    assert [message["text"] for message in replay["messages_received"]] == ["still pending"]


def test_i034_011_incremental_state_does_not_change_server_legality(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation, "arkania")
    before = deepcopy(tools.observe().data["available_actions"])
    pending = tools.begin_activation().data
    tools.acknowledge_activation(pending["delivery_receipt"], _memory("one"))
    assert tools.observe().data["available_actions"] == before
