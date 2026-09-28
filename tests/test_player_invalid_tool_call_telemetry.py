from __future__ import annotations

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest, RetryPromptPart, ToolReturnPart
from pydantic_ai.tools import RunContext
from sqlalchemy import Engine, inspect

from agents.guardrails import GuardrailConfig
from agents.player import classify_invalid_tool_calls
from app import export as export_module, game_service, provenance, summary
from app.database import engine_for_path, initialize_database
from tests.api_helpers import seed
from tests.test_single_shot import _memory, _player, decision_call, model_from_decision_policy


def test_i052_001_disjoint_categories_reconcile_in_event_order() -> None:
    messages = [
        ModelRequest(
            parts=[
                ToolReturnPart(
                    tool_name="send_message",
                    content={
                        "ok": False,
                        "http_status": 422,
                        "error": {"code": "invalid_request", "message": "redacted"},
                    },
                ),
                RetryPromptPart(
                    tool_name="decide",
                    content=[
                        {
                            "type": "missing",
                            "loc": ("argument",),
                            "msg": "Field required",
                            "input": {},
                        }
                    ],
                ),
            ]
        )
    ]

    breakdown = classify_invalid_tool_calls(
        messages,
        executed_rejections=[
            {"action": "ready", "http_status": 409, "code": "wrong_phase", "message": "redacted"}
        ],
        decision_tool_name="decide",
    )

    assert breakdown["schema_version"] == "invalid-tool-calls-v1"
    assert breakdown["counts_by_category"] == {
        "callable_tool_failure": 1,
        "decision_validation_retry": 1,
        "executed_action_rejection": 1,
    }
    assert [event["sequence"] for event in breakdown["events"]] == [1, 2, 3]
    assert [event["category"] for event in breakdown["events"]] == [
        "callable_tool_failure",
        "decision_validation_retry",
        "executed_action_rejection",
    ]
    assert breakdown["events"][0] == {
        "sequence": 1,
        "category": "callable_tool_failure",
        "reason": "server_rejection",
        "action": "send_message",
        "error_code": "invalid_request",
        "http_status": 422,
        "ensuing_model_retry": True,
    }
    assert breakdown["events"][1]["reason"] == "schema_validation"
    assert breakdown["events"][1]["http_status"] is None
    assert breakdown["events"][1]["ensuing_model_retry"] is True
    assert breakdown["events"][2] == {
        "sequence": 3,
        "category": "executed_action_rejection",
        "reason": "server_rejection",
        "action": "ready",
        "error_code": "wrong_phase",
        "http_status": 409,
        "ensuing_model_retry": False,
    }
    assert breakdown["total"] == len(breakdown["events"]) == 3
    assert "redacted" not in str(breakdown)
    assert all("details" not in event for event in breakdown["events"])


def test_i052_002_decision_validation_retry_is_not_an_http_failure(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(packet: dict, idx: int, info: RunContext) -> list:
        text = "x" * 50 if idx == 0 else "short"
        return [
            decision_call(
                info,
                {
                    "messages": [{"recipients": [], "public": True, "text": text}],
                    "strategic_memory": _memory(),
                    "ready": True,
                },
            )
        ]

    player = _player(
        db_engine,
        creation,
        "arkania",
        model_from_decision_policy(policy),
        guardrails=GuardrailConfig(max_message_chars=20),
    )
    result = player.activate()

    assert result.invalid_tool_calls == 1
    assert result.invalid_tool_call_breakdown["events"] == [
        {
            "sequence": 1,
            "category": "decision_validation_retry",
            "reason": "client_guardrail",
            "action": "decide",
            "error_code": "guardrail_message_too_long",
            "http_status": None,
            "ensuing_model_retry": True,
        }
    ]


def test_i052_003_executed_rejection_has_server_code_without_model_retry(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(packet: dict, idx: int, info: RunContext) -> list:
        return [
            decision_call(
                info,
                {
                    "messages": [
                        {"recipients": ["nobody"], "public": False, "text": "hello?"}
                    ],
                    "strategic_memory": _memory(),
                    "ready": False,
                },
            )
        ]

    player = _player(db_engine, creation, "arkania", model_from_decision_policy(policy))
    result = player.activate()

    assert result.invalid_tool_calls == 1
    [event] = result.invalid_tool_call_breakdown["events"]
    assert event["category"] == "executed_action_rejection"
    assert event["reason"] == "server_rejection"
    assert event["action"] == "send_message"
    assert event["error_code"] == "invalid_request"
    assert event["http_status"] == 400
    assert event["ensuing_model_retry"] is False


def _record(engine: Engine, game_id, *, invalid_tool_calls: int, breakdown: dict) -> None:
    provenance.record_agent_run(
        engine,
        game_id,
        actor_id="arkania",
        role="player",
        model_requested="fake/model",
        prompt_version="player-v1",
        prompt_hash="a" * 64,
        agent_framework="pydantic-ai",
        tool_schema_version="player-tools-v3",
        invalid_tool_calls=invalid_tool_calls,
        invalid_tool_call_breakdown=breakdown,
    )


def test_i052_004_persistence_export_and_summary_keep_breakdown(db_engine: Engine) -> None:
    creation = seed(db_engine)
    breakdown = {
        "schema_version": "invalid-tool-calls-v1",
        "total": 2,
        "counts_by_category": {
            "decision_validation_retry": 1,
            "executed_action_rejection": 1,
        },
        "counts_by_reason": {"schema_validation": 1, "server_rejection": 1},
        "events": [
            {
                "sequence": 1,
                "category": "decision_validation_retry",
                "reason": "schema_validation",
                "action": "decide",
                "error_code": None,
                "http_status": None,
                "ensuing_model_retry": True,
            },
            {
                "sequence": 2,
                "category": "executed_action_rejection",
                "reason": "server_rejection",
                "action": "ready",
                "error_code": "wrong_phase",
                "http_status": 409,
                "ensuing_model_retry": False,
            },
        ],
    }
    _record(db_engine, creation.game_id, invalid_tool_calls=2, breakdown=breakdown)

    [stored] = provenance.list_agent_runs(db_engine, creation.game_id)
    assert stored.invalid_tool_call_breakdown == breakdown
    exported = export_module.build_game_export(db_engine, creation.game_id)
    assert exported["export_format_version"] == export_module.EXPORT_FORMAT_VERSION
    assert exported["agent_runs"][0]["invalid_tool_calls"] == 2
    assert exported["agent_runs"][0]["invalid_tool_call_breakdown"] == breakdown
    assert exported["run_summary"]["invalid_tool_calls"] == {
        "total": 2,
        "classified": 2,
        "unclassified": 0,
        "counts_by_category": breakdown["counts_by_category"],
        "counts_by_reason": breakdown["counts_by_reason"],
    }
    assert (
        "Invalid tool-call aggregate 2 (2 classified, 0 unclassified; "
        "decision_validation_retry=1, executed_action_rejection=1)"
        in summary.format_game_summary(exported["run_summary"])
    )


def test_i052_005_rejects_non_reconciling_breakdown(db_engine: Engine) -> None:
    creation = seed(db_engine)
    with pytest.raises(provenance.ProvenanceError, match="must equal invalid_tool_calls"):
        _record(
            db_engine,
            creation.game_id,
            invalid_tool_calls=2,
            breakdown={
                "schema_version": "invalid-tool-calls-v1",
                "total": 1,
                "counts_by_category": {"callable_tool_failure": 1},
                "counts_by_reason": {"server_rejection": 1},
                "events": [],
            },
        )


def test_i052_005_additive_migration_marks_legacy_breakdown_unavailable(tmp_path: Path) -> None:
    engine = engine_for_path(tmp_path / "legacy.sqlite")
    initialize_database(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "ALTER TABLE agentrun DROP COLUMN invalid_tool_call_breakdown"
        )

    initialize_database(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("agentrun")}
    assert "invalid_tool_call_breakdown" in columns
    creation = seed(engine)
    _record(engine, creation.game_id, invalid_tool_calls=1, breakdown={})
    [stored] = provenance.list_agent_runs(engine, creation.game_id)
    assert stored.invalid_tool_calls == 1
    assert stored.invalid_tool_call_breakdown == {}
