"""I-054: behaviour-neutral replay of identical intra-activation Resource searches."""

from __future__ import annotations

import threading
import uuid
from concurrent.futures import ThreadPoolExecutor

from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import Engine
from sqlmodel import select

from agents.player import PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app import provenance, resources, summary
from app.api import create_app
from app.database import session_scope
from app.models import AgentRun, ResourceAccess
from tests.api_helpers import bearer, seed, start_to_diplomacy
from tests.test_single_shot import _memory, decision_call


def _search(client, game_id, token, query, activation_id=None):  # noqa: ANN001, ANN201
    headers = bearer(token)
    if activation_id is not None:
        headers["X-AMAW-Activation-ID"] = activation_id
    return client.get(
        f"/games/{game_id}/resources/search",
        params={"q": query},
        headers=headers,
    )


def test_i054_001_normalized_repeat_executes_search_once_and_preserves_echo(
    db_engine: Engine, monkeypatch
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    app = create_app(engine=db_engine)
    client = TestClient(app, raise_server_exceptions=False)
    activation_id = str(uuid.uuid4())
    calls = 0
    original = resources.search_resources

    def counted(candidates, query, **kwargs):  # noqa: ANN001, ANN003, ANN202
        nonlocal calls
        calls += 1
        return original(candidates, query, **kwargs)

    monkeypatch.setattr(resources, "search_resources", counted)
    first = _search(
        client, creation.game_id, creation.seat_tokens["arkania"],
        "  REGIONAL ", activation_id,
    )
    second = _search(
        client, creation.game_id, creation.seat_tokens["arkania"],
        "regional", activation_id,
    )

    assert first.status_code == second.status_code == 200
    assert first.json()["query"] == "REGIONAL"
    assert second.json()["query"] == "regional"
    assert first.json()["results"] == second.json()["results"]
    assert calls == 1


def test_i054_002_replays_remain_ordered_accesses_and_reported_repeats(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    client = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    activation_id = str(uuid.uuid4())
    token = creation.seat_tokens["arkania"]

    assert _search(client, creation.game_id, token, "  REGIONAL ", activation_id).status_code == 200
    assert _search(client, creation.game_id, token, "regional", activation_id).status_code == 200
    provenance.record_agent_run(
        db_engine,
        creation.game_id,
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
        model_calls=1,
        completed=True,
    )

    with session_scope(db_engine) as session:
        rows = [
            (row.query, row.activation_id)
            for row in session.exec(
                select(ResourceAccess)
                .where(ResourceAccess.game_id == creation.game_id)
                .order_by(ResourceAccess.id)
            )
        ]
    assert rows == [
        ("  REGIONAL ", activation_id),
        ("regional", activation_id),
    ]
    report = summary.build_resource_research_report(db_engine, creation.game_id)
    item = report["activations"][0]
    assert item["normalized_queries"] == ["regional", "regional"]
    assert item["repeated_queries"] == [{"query": "regional", "count": 2}]
    assert item["searches"] == 2


def test_i054_003_cache_never_crosses_visibility_or_activation_context(
    db_engine: Engine, monkeypatch
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    app = create_app(engine=db_engine)
    client = TestClient(app, raise_server_exceptions=False)
    calls = 0
    original = resources.search_resources

    def counted(candidates, query, **kwargs):  # noqa: ANN001, ANN003, ANN202
        nonlocal calls
        calls += 1
        return original(candidates, query, **kwargs)

    monkeypatch.setattr(resources, "search_resources", counted)
    activation_a = str(uuid.uuid4())
    activation_b = str(uuid.uuid4())

    arkania = _search(
        client, creation.game_id, creation.seat_tokens["arkania"],
        "intelligence", activation_a,
    )
    belvar = _search(
        client, creation.game_id, creation.seat_tokens["belvar"],
        "intelligence", activation_a,
    )
    assert [row["id"] for row in arkania.json()["results"]] == ["arkania-intel"]
    assert belvar.json()["results"] == []

    _search(client, creation.game_id, creation.seat_tokens["arkania"], "intelligence", activation_b)
    _search(client, creation.game_id, creation.seat_tokens["arkania"], "intelligence")
    _search(client, creation.game_id, creation.seat_tokens["arkania"], "intelligence")
    assert calls == 5

    # A new application instance has no inherited replay state even over the same database.
    other = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    _search(
        other, creation.game_id, creation.seat_tokens["arkania"],
        "intelligence", activation_a,
    )
    assert calls == 6


def test_i054_004_cache_is_single_flight_and_bounded() -> None:
    cache = resources.ActivationSearchReplayCache(max_entries=2)
    result = (resources.ResourceSearchResult(
        id="r", name="Resource", type="background", tags=(), snippet="result"
    ),)
    calls = 0
    barrier = threading.Barrier(8)

    def compute():  # noqa: ANN202
        nonlocal calls
        calls += 1
        return result

    def lookup():  # noqa: ANN202
        barrier.wait()
        return cache.get_or_compute(("game", "actor", "activation", "query"), compute)

    with ThreadPoolExecutor(max_workers=8) as pool:
        returned = list(pool.map(lambda _: lookup(), range(8)))
    assert returned == [result] * 8
    assert calls == 1

    cache.get_or_compute(("game", "actor", "activation", "second"), lambda: result)
    cache.get_or_compute(("game", "actor", "activation", "third"), lambda: result)
    cache.get_or_compute(("game", "actor", "activation", "query"), compute)
    assert calls == 2  # the oldest entry was evicted

    # Different keys remain concurrent; the single-flight wait applies only to duplicates.
    independent = resources.ActivationSearchReplayCache(max_entries=2)
    rendezvous = threading.Barrier(2)

    def independent_compute(value):  # noqa: ANN001, ANN202
        rendezvous.wait(timeout=1)
        return value

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(
            independent.get_or_compute,
            ("game", "actor-a", "activation", "query"),
            lambda: independent_compute(result),
        )
        second = pool.submit(
            independent.get_or_compute,
            ("game", "actor-b", "activation", "query"),
            lambda: independent_compute(result),
        )
        assert first.result(timeout=2) == second.result(timeout=2) == result


def test_i054_005_player_sequence_and_invalid_call_classification_are_unchanged(
    db_engine: Engine, monkeypatch
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    calls = 0
    original = resources.search_resources

    def counted(candidates, query, **kwargs):  # noqa: ANN001, ANN003, ANN202
        nonlocal calls
        calls += 1
        return original(candidates, query, **kwargs)

    monkeypatch.setattr(resources, "search_resources", counted)

    def policy(messages, info):  # noqa: ANN001, ANN202
        responses = sum(isinstance(message, ModelResponse) for message in messages)
        if responses == 0:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="search_resources", args={"query": "regional"}),
                ToolCallPart(tool_name="search_resources", args={"query": " REGIONAL "}),
            ])
        return ModelResponse(parts=[decision_call(info, {
            "messages": [],
            "strategic_memory": _memory(),
            "ready": True,
        })])

    tools = PlayerTools(
        TestClient(create_app(engine=db_engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    player = ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=FunctionModel(policy),
        config=PlayerConfig(model="fake/researcher"),
    )
    result = player.activate()

    assert result.tool_call_names == ["search_resources", "search_resources", "ready"]
    assert result.invalid_tool_calls == 0
    assert result.invalid_tool_call_breakdown["events"] == []
    assert calls == 1
    with session_scope(db_engine) as session:
        accesses = list(session.exec(
            select(ResourceAccess).where(ResourceAccess.activation_id == result.activation_id)
        ))
        run_count = len(list(session.exec(select(AgentRun))))
    assert len(accesses) == 2
    assert run_count == 0  # the player layer still does not persist provenance itself
