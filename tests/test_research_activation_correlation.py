"""I-045: mechanically attribute Resource research to player activations."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine, inspect, text
from sqlmodel import select

from agents.player import ActivationFailed, PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app import provenance, summary
from app.api import create_app
from app.database import engine_for_path, initialize_database, session_scope
from app.models import AgentRun, Resource, ResourceAccess
from tests.api_helpers import client, seed, start_to_diplomacy


def _record_player_run(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    activation_id: str,
    model_calls: int,
    completed: bool = True,
) -> AgentRun:
    return provenance.record_agent_run(
        engine,
        game_id,
        activation_id=activation_id,
        actor_id="arkania",
        role="player",
        turn=1,
        phase="diplomacy",
        model_requested="fake/researcher",
        prompt_version="player-test",
        prompt_hash="a" * 64,
        agent_framework="pydantic-ai",
        tool_schema_version="player-tools-v3",
        model_calls=model_calls,
        completed=completed,
        failure_reason=None if completed else "synthetic failure",
    )


def _player(engine: Engine, game_id: uuid.UUID, token: str, model: FunctionModel) -> ReferencePlayer:
    tools = PlayerTools(
        TestClient(create_app(engine=engine), raise_server_exceptions=False),
        game_id=str(game_id),
        token=token,
    )
    return ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=model,
        config=PlayerConfig(model="fake/researcher", activation_mode="tool_loop"),
    )


def test_i045_001_successful_activation_correlates_resource_access_and_clears_scope(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        index = sum(isinstance(message, ModelResponse) for message in messages)
        if index == 0:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="search_resources", args={"query": "regional"}
            )])
        if index == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="read_resource", args={"resource_id": "background"}
            )])
        return ModelResponse(parts=[TextPart(content="done")])

    player = _player(
        db_engine,
        creation.game_id,
        creation.seat_tokens["arkania"],
        FunctionModel(policy),
    )
    result = player.activate()
    uuid.UUID(result.activation_id)
    run = _record_player_run(
        db_engine,
        creation.game_id,
        activation_id=result.activation_id,
        model_calls=result.model_iterations,
    )

    # A later direct tool call is outside the completed activation and must remain unattributed.
    assert player.tools.read_resource(resource_id="background").ok
    with session_scope(db_engine) as session:
        access_activation_ids = [row.activation_id for row in session.exec(
            select(ResourceAccess).where(ResourceAccess.game_id == creation.game_id)
            .order_by(ResourceAccess.id)
        )]
    assert access_activation_ids == [
        result.activation_id,
        result.activation_id,
        None,
    ]
    assert run.activation_id == result.activation_id
    report = summary.build_resource_research_report(db_engine, creation.game_id)
    assert report["totals"]["unattributed_accesses"] == 1
    assert report["unattributed_access"][0]["resource_access_id"] == 3


def test_i045_002_failed_activation_retains_resource_correlation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        if not any(isinstance(message, ModelResponse) for message in messages):
            return ModelResponse(parts=[ToolCallPart(
                tool_name="search_resources", args={"query": "regional"}
            )])
        raise ValueError("synthetic deterministic failure")

    player = _player(
        db_engine,
        creation.game_id,
        creation.seat_tokens["arkania"],
        FunctionModel(policy),
    )
    with pytest.raises(ActivationFailed) as raised:
        player.activate()
    activation_id = raised.value.activation_id
    uuid.UUID(activation_id)
    _record_player_run(
        db_engine,
        creation.game_id,
        activation_id=activation_id,
        model_calls=raised.value.counts["model_calls"],
        completed=False,
    )

    report = summary.build_resource_research_report(db_engine, creation.game_id)
    item = next(row for row in report["activations"] if row["activation_id"] == activation_id)
    assert item["completed"] is False
    assert item["searches"] == 1


def test_i045_003_report_classifies_repeats_conversion_versions_and_continuation(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    first = str(uuid.uuid4())
    second = str(uuid.uuid4())

    def get(path: str, activation_id: str, **kwargs):  # noqa: ANN003
        headers = {
            "Authorization": f"Bearer {token}",
            "X-AMAW-Activation-ID": activation_id,
        }
        return c.get(f"/games/{creation.game_id}{path}", headers=headers, **kwargs)

    # Search segments: converted, unconverted, converted. Both queries and reads repeat exactly
    # after the server's documented trim+casefold normalization.
    assert get("/resources/search", first, params={"q": "  REGIONAL "}).status_code == 200
    assert get("/resources/background", first).status_code == 200
    assert get("/resources/search", first, params={"q": "regional"}).status_code == 200
    assert get("/resources/search", first, params={"q": "trade"}).status_code == 200
    assert get("/resources/background", first).status_code == 200
    _record_player_run(db_engine, creation.game_id, activation_id=first, model_calls=4)
    _record_player_run(db_engine, creation.game_id, activation_id=second, model_calls=1)

    report = summary.build_resource_research_report(db_engine, creation.game_id)
    item = next(row for row in report["activations"] if row["activation_id"] == first)
    with session_scope(db_engine) as session:
        version = session.get(Resource, (creation.game_id, "background")).content_sha256

    assert item["searches"] == 3
    assert item["reads"] == 2
    assert item["repeated_queries"] == [{"query": "regional", "count": 2}]
    assert item["unique_resources"] == [{
        "resource_id": "background", "content_sha256": version, "read_count": 2
    }]
    assert item["repeated_resources"] == item["unique_resources"]
    assert item["search_to_read_conversions"] == 2
    assert item["searches_followed_by_no_read"] == 1
    assert item["provider_calls"] == 4
    assert item["continued_after_research"] is True
    assert report["totals"] == {
        "player_activations": 2,
        "research_activations": 1,
        "searches": 3,
        "reads": 2,
        "search_to_read_conversions": 2,
        "searches_followed_by_no_read": 1,
        # I-099 result-identity totals (every search here carries metadata).
        "zero_result_searches": 0,
        "reformulations_after_identical_set": 0,
        "search_returned_read_conversions": 2,
        "searches_without_result_metadata": 0,
        "activations_continued_after_research": 1,
        "unattributed_accesses": 0,
    }


def test_i045_004_additive_migration_preserves_legacy_uncorrelated_rows(tmp_path) -> None:
    engine = engine_for_path(tmp_path / "pre-i045.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE resourceaccess (id INTEGER PRIMARY KEY, game_id CHAR(32) NOT NULL, "
            "actor_id VARCHAR NOT NULL, turn INTEGER NOT NULL, operation VARCHAR NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE agentrun (id INTEGER PRIMARY KEY, game_id CHAR(32) NOT NULL, "
            "role VARCHAR NOT NULL, model_requested VARCHAR NOT NULL, latency_ms INTEGER)"
        )
        connection.exec_driver_sql(
            "INSERT INTO resourceaccess VALUES (1, '00000000000000000000000000000000', "
            "'arkania', 1, 'search')"
        )
        connection.exec_driver_sql(
            "INSERT INTO agentrun VALUES (1, '00000000000000000000000000000000', "
            "'player', 'legacy/model', 1234)"
        )

    initialize_database(engine)

    assert "activation_id" in {
        column["name"] for column in inspect(engine).get_columns("resourceaccess")
    }
    assert "activation_id" in {
        column["name"] for column in inspect(engine).get_columns("agentrun")
    }
    with engine.connect() as connection:
        access = connection.execute(text(
            "SELECT operation, activation_id FROM resourceaccess WHERE id = 1"
        )).one()
        run = connection.execute(text(
            "SELECT model_requested, activation_id FROM agentrun WHERE id = 1"
        )).one()
    assert access == ("search", None)
    assert run == ("legacy/model", None)
