"""I-051: player and server share one pre-mutation strategic-memory limit."""

from __future__ import annotations

from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo
from sqlalchemy import Engine

from agents.player import PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app import game_service
from app.api import create_app
from tests.agent_fakes import decision_call, model_from_decision_policy, model_from_policy
from tests.api_helpers import seed


def _memory(text: str = "") -> dict:
    return {
        "commitments_made": [text] if text else [],
        "commitments_or_offers_received": [text] if text else [],
        "outstanding_offers_and_negotiations": [text] if text else [],
        "red_lines_and_constraints": [text] if text else [],
        "uncertain_or_disputed_claims": [text] if text else [],
        "relationships_and_likely_coalitions": [text] if text else [],
        "strategic_assessment": "",
        "intended_next_action": "",
    }


def _oversized_memory() -> dict:
    # Each entry is below max_note_chars and the default JSON tool limit remains below 16,384,
    # but the complete memory exceeds the server's 12,000-byte bounded-state contract.
    return _memory("x" * 2_000)


def _tools(engine: Engine, creation) -> PlayerTools:
    return PlayerTools(
        TestClient(create_app(engine=engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )


def _player(engine: Engine, creation, model, *, mode: str) -> ReferencePlayer:  # noqa: ANN001
    return ReferencePlayer(
        _tools(engine, creation),
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(model="fake/memory", activation_mode=mode),
    )


def test_i051_single_shot_rejects_memory_before_bundled_mutations(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def decide(_packet: dict, index: int, info: AgentInfo) -> list:
        return [
            decision_call(
                info,
                {
                    "messages": [
                        {"recipients": [], "public": True, "text": "execute exactly once"}
                    ],
                    "strategic_memory": _oversized_memory() if index == 0 else _memory("fixed"),
                    "ready": True,
                },
            )
        ]

    result = _player(
        db_engine, creation, model_from_decision_policy(decide), mode="single_shot"
    ).activate()
    assert result.model_iterations == 2
    assert result.invalid_tool_calls == 1
    messages = _tools(db_engine, creation).read_messages().data["messages"]
    assert [message["text"] for message in messages] == ["execute exactly once"]


def test_i051_tool_loop_rejects_oversized_staging_then_accepts_correction(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(_packet: dict, _tools: list[str], index: int) -> list:
        if index == 0:
            return [ToolCallPart(tool_name="update_strategic_memory", args=_oversized_memory())]
        if index == 1:
            return [ToolCallPart(tool_name="update_strategic_memory", args=_memory("fixed"))]
        if index == 2:
            return [ToolCallPart(tool_name="ready", args={})]
        return [TextPart(content="done")]

    player = _player(db_engine, creation, model_from_policy(policy), mode="tool_loop")
    result = player.activate()
    assert result.invalid_tool_calls == 1
    assert player.tools.begin_activation().data["strategic_memory"] == _memory("fixed")


def test_i051_server_names_size_and_receipt_conflicts_separately(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation)
    pending = tools.begin_activation().data

    too_large = tools.acknowledge_activation(
        pending["delivery_receipt"], _oversized_memory()
    )
    assert too_large.http_status == 422
    assert "12000 serialized bytes" in too_large.error_message

    conflict = tools.acknowledge_activation("not-current", _memory("valid"))
    assert conflict.http_status == 409
    assert "not current" in conflict.error_message


def test_i051_valid_ack_survives_concurrent_phase_closure(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    tools = _tools(db_engine, creation)
    pending = tools.begin_activation().data
    for actor_id in ("arkania", "belvar", "cestia"):
        game_service.ready(db_engine, creation.game_id, actor_id)

    acknowledged = tools.acknowledge_activation(pending["delivery_receipt"], _memory("valid"))
    assert acknowledged.ok
    assert acknowledged.data["strategic_memory"] == _memory("valid")
