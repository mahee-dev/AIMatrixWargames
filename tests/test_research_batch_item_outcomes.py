"""I-101: batched research continues through recoverable per-item failures."""

from __future__ import annotations

import copy
import json

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from sqlalchemy import Engine
from sqlmodel import select

from agents.guardrails import GuardrailConfig
from agents.player import (
    RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    RESEARCH_MODE_BATCHED_V1,
    PlayerConfig,
    ReferencePlayer,
    compact_research_history,
    tool_schema_version_for,
)
from agents.tools import PlayerTools
from app.database import session_scope
from app.models import ResourceAccess
from tests.agent_fakes import model_from_policy
from tests.api_helpers import client, seed, start_to_diplomacy


def _player(engine: Engine, creation, policy, *, max_operations: int = 4) -> ReferencePlayer:  # noqa: ANN001
    return ReferencePlayer(
        PlayerTools(
            client(engine),
            game_id=str(creation.game_id),
            token=creation.seat_tokens["arkania"],
        ),
        actor_id="arkania",
        actor_name="Arkania",
        model=model_from_policy(policy),
        config=PlayerConfig(
            model="fake/i101",
            activation_mode="tool_loop",
            research_mode=RESEARCH_MODE_BATCHED_V1,
            guardrails=GuardrailConfig(
                max_research_tool_calls=4,
                max_research_operations=max_operations,
            ),
        ),
    )


def _returns(result, tool_name: str) -> list[ToolReturnPart]:  # noqa: ANN001
    return [
        part
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def _read_ids(engine: Engine, creation) -> list[str]:  # noqa: ANN001
    with session_scope(engine) as session:
        return [
            row.resource_id
            for row in session.exec(
                select(ResourceAccess)
                .where(
                    ResourceAccess.game_id == creation.game_id,
                    ResourceAccess.operation == "read",
                )
                .order_by(ResourceAccess.id)
            )
        ]


def _mixed_policy(*, repeat: bool = False):
    def policy(packet, tool_names, idx):  # noqa: ANN001
        if idx == 0 or (repeat and idx == 1):
            return [ToolCallPart(
                tool_name="read_resources_batch",
                args={"resource_ids": ["background", "missing", "trade"]},
            )]
        return [TextPart(content="done")]

    return policy


def test_i101_001_success_failure_success_returns_ordered_item_outcomes(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    result = _player(db_engine, creation, _mixed_policy()).activate()

    [returned] = _returns(result, "read_resources_batch")
    payload = returned.content
    assert payload["ok"] is True
    items = payload["data"]["items"]
    assert [(item["resource_id"], item["ok"]) for item in items] == [
        ("background", True),
        ("missing", False),
        ("trade", True),
    ]
    assert set(items[0]) == {"resource_id", "ok", "data"}
    assert set(items[1]) == {"resource_id", "ok", "error"}
    assert items[1]["error"]["http_status"] == 404
    assert items[1]["error"]["code"] == "not_found"
    assert _read_ids(db_engine, creation) == ["background", "trade"]
    assert result.research_operations == 3


def test_i101_002_retry_of_completed_mixed_batch_repeats_no_operations(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    result = _player(db_engine, creation, _mixed_policy(repeat=True)).activate()

    first, retried = _returns(result, "read_resources_batch")
    assert first.content["ok"] is True
    assert retried.content["ok"] is False
    assert retried.content["error"]["code"] == "guardrail_research_duplicate"
    assert _read_ids(db_engine, creation) == ["background", "trade"]
    assert result.research_operations == 3


def test_i101_003_compaction_losslessly_retains_mixed_item_outcomes() -> None:
    batch_id = "batch-1"
    data = {
        "items": [
            {"resource_id": "background", "ok": True, "data": {"id": "background"}},
            {
                "resource_id": "missing",
                "ok": False,
                "error": {
                    "http_status": 404,
                    "code": "not_found",
                    "message": "Resource not found.",
                    "details": None,
                },
            },
        ]
    }
    messages = [
        ModelResponse(parts=[ToolCallPart(
            tool_name="read_resources_batch",
            args={"resource_ids": ["background", "missing"]},
            tool_call_id=batch_id,
        )]),
        ModelRequest(parts=[ToolReturnPart(
            tool_name="read_resources_batch",
            content={"ok": True, "data": data},
            tool_call_id=batch_id,
        )]),
    ]
    original = copy.deepcopy(messages)

    compacted = compact_research_history(messages)

    assert messages == original
    assert isinstance(compacted[0].parts[0], TextPart)
    assert isinstance(compacted[1].parts[0], UserPromptPart)
    retained = json.loads(compacted[1].parts[0].content)
    assert retained["retained_research_results"] == [
        {"tool_name": "read_resources_batch", "data": data}
    ]


def test_i101_004_mixed_prefix_keeps_budget_truncation_and_zero_capacity_refusal(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    result = _player(
        db_engine, creation, _mixed_policy(), max_operations=2
    ).activate()

    [returned] = _returns(result, "read_resources_batch")
    assert [(item["resource_id"], item["ok"]) for item in returned.content["data"]["items"]] == [
        ("background", True),
        ("missing", False),
    ]
    assert returned.content["data"]["not_executed"] == [{
        "resource_id": "trade",
        "error": {"code": "guardrail_research_operations_exhausted"},
    }]
    assert result.research_operations == 2

    creation_zero = seed(db_engine)
    start_to_diplomacy(db_engine, creation_zero)
    zero_player = _player(
        db_engine, creation_zero, _mixed_policy(), max_operations=0
    )
    dispatched: list[str] = []
    zero_payload = zero_player._run_research_batch(  # noqa: SLF001
        "resource_id",
        ["background"],
        lambda resource_id: dispatched.append(resource_id),  # type: ignore[arg-type,func-returns-value]
    )
    assert zero_payload["ok"] is False
    assert zero_payload["error"]["code"] == (
        "guardrail_research_operations_exhausted"
    )
    assert dispatched == []
    assert _read_ids(db_engine, creation_zero) == []


def test_i101_005_schema_versions_change_only_for_batched_surfaces() -> None:
    baseline = PlayerConfig(model="fake")
    reply = PlayerConfig(model="fake", con_reply_mode="visible_reply")
    batched = PlayerConfig(model="fake", research_mode=RESEARCH_MODE_BATCHED_V1)
    batched_reply = PlayerConfig(
        model="fake",
        research_mode=RESEARCH_MODE_BATCHED_V1,
        con_reply_mode="visible_reply",
    )

    assert tool_schema_version_for(baseline) == "player-tools-v11-live-bounded-research"
    assert tool_schema_version_for(reply) == "player-tools-v13-reply-live-bounded-research"
    assert tool_schema_version_for(batched) == (
        "player-tools-v17-batched-live-bounded-operations-item-outcomes"
    )
    assert tool_schema_version_for(batched_reply) == (
        "player-tools-v18-batched-reply-live-bounded-operations-item-outcomes"
    )


def test_i101_005_malformed_batch_still_fails_before_dispatch(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(packet, tool_names, idx):  # noqa: ANN001
        if idx == 0:
            return [ToolCallPart(
                tool_name="read_resources_batch", args={"resource_ids": []},
            )]
        return [TextPart(content="done")]

    result = _player(db_engine, creation, policy).activate()
    [returned] = _returns(result, "read_resources_batch")
    assert returned.content["ok"] is False
    assert returned.content["error"]["code"] == "guardrail_research_batch_invalid"
    assert result.research_operations == 0
    assert _read_ids(db_engine, creation) == []
