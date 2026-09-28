"""I-100: versioned deterministic token-coverage Resource search candidate."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

import amaw_cli
from agents.autonomous import (
    AutonomousGameRunner,
    build_resume_recipe,
    expected_manifest_configuration,
)
from agents.player import (
    RESEARCH_MODE_BATCHED_V1,
    PlayerConfig,
    ReferencePlayer,
    tool_schema_version_for,
)
from agents.tools import PlayerTools, ToolResult
from app import game_service, scenarios
from app.api import create_app
from app.config import Settings
from app.game_loop import GameLoopCondition
from app.models import Resource
from app.resources import MAX_SNIPPET_CHARS, search_resources
from benchmark.controls import PLAYER_CONDITION_PATHS


KNOWLEDGE_DIR = Path(__file__).parent / "fixtures" / "knowledge-scenario"


def _resource(
    resource_id: str,
    name: str,
    *,
    tags: list[str] | None = None,
    text: str = "",
) -> Resource:
    raw = text.encode()
    return Resource(
        game_id=uuid.uuid4(),
        id=resource_id,
        name=name,
        type="background",
        visibility="public",
        visible_to=[],
        tags=tags or [],
        resource_metadata={},
        source_kind="inline",
        source_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        representation_kind="text",
        media_type="text/plain; charset=utf-8",
        text_content=text,
        size_bytes=len(raw),
    )


def _ids(resources: list[Resource], query: str, *, contract: str) -> list[str]:
    return [item.id for item in search_resources(resources, query, contract=contract)[1]]


def test_i100_001_search_v1_remains_whole_phrase_based() -> None:
    # v1's own semantics are frozen and still selectable; which contract is the *default* is
    # TEST-I112-001's subject since I-112 moved it to v2.
    resources = [_resource("naval", "Belvar naval posture", text="A foothold is disputed.")]

    assert _ids(resources, "Belvar naval", contract="search-v1") == ["naval"]
    assert _ids(resources, "Belvar foothold", contract="search-v1") == []


def test_i100_002_search_v2_has_frozen_weighted_ranking_and_id_tie_break() -> None:
    resources = [
        _resource("z-name", "Belvar naval mandate"),
        _resource("phrase-tag", "Other", tags=["Belvar naval mandate"]),
        _resource("tag", "Other", tags=["belvar", "naval", "mandate"]),
        _resource("text", "Other", text="Belvar naval mandate."),
        _resource("a-partial", "Belvar outlook"),
        _resource("b-partial", "Belvar outlook"),
        _resource("none", "Unrelated", text="No matching concepts."),
    ]

    assert _ids(
        resources,
        "  BELVAR naval naval mandate  ",
        contract="search-v2",
    ) == ["z-name", "phrase-tag", "tag", "a-partial", "b-partial", "text"]
    assert _ids(resources, "Belvar naval mandate", contract="search-v2")[:3] == [
        "z-name",
        "phrase-tag",
        "text",
    ]


def test_i100_003_search_v2_snippet_centres_on_first_best_matching_token() -> None:
    prefix = "x" * 220
    resource = _resource(
        "intel",
        "Distant report",
        text=f"{prefix} Belvar task group has a foothold near the strait.",
    )

    [result] = search_resources(
        [resource], "mandate Belvar foothold", contract="search-v2"
    )[1]
    assert len(result.snippet) <= MAX_SNIPPET_CHARS
    assert "Belvar" in result.snippet
    assert result.snippet.startswith("…")


def test_i100_004_unknown_search_contract_is_rejected() -> None:
    with pytest.raises(ValueError, match="search contract"):
        search_resources([_resource("r", "Resource")], "resource", contract="search-v3")


def _running_knowledge_game(engine):  # noqa: ANN001, ANN202
    loaded = scenarios.load_scenario(KNOWLEDGE_DIR)
    creation = scenarios.create_game_from_scenario(engine, loaded)
    game_service.start_game(engine, creation.game_id)
    return creation, TestClient(create_app(engine=engine), raise_server_exceptions=False)


def _api_search(client, creation, actor: str, query: str, **params):  # noqa: ANN001, ANN202
    return client.get(
        f"/games/{creation.game_id}/resources/search",
        params={"q": query, **params},
        headers={"Authorization": f"Bearer {creation.seat_tokens[actor]}"},
    )


def test_i100_004_api_v2_preserves_shape_and_visibility_first(db_engine) -> None:  # noqa: ANN001
    creation, client = _running_knowledge_game(db_engine)
    query = "GREYPETREL unrelated"

    owner = _api_search(client, creation, "arkania", query, contract="search-v2")
    outsider = _api_search(client, creation, "belvar", query, contract="search-v2")
    invalid = _api_search(client, creation, "arkania", query, contract="search-v3")

    assert owner.status_code == 200
    assert set(owner.json()) == {"query", "results"}
    assert [item["id"] for item in owner.json()["results"]] == ["arkania-intel"]
    assert outsider.status_code == 200 and outsider.json() == {"query": query, "results": []}
    assert "GREYPETREL" not in json.dumps(outsider.json()["results"])
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "validation_error"


def test_i100_005_activation_replay_cache_isolated_by_search_contract(db_engine) -> None:  # noqa: ANN001
    creation, client = _running_knowledge_game(db_engine)
    query = "GREYPETREL unrelated"
    activation = {"X-AMAW-Activation-ID": str(uuid.uuid4())}

    v1 = client.get(
        f"/games/{creation.game_id}/resources/search",
        params={"q": query, "contract": "search-v1"},
        headers={
            "Authorization": f"Bearer {creation.seat_tokens['arkania']}",
            **activation,
        },
    )
    v2 = client.get(
        f"/games/{creation.game_id}/resources/search",
        params={"q": query, "contract": "search-v2"},
        headers={
            "Authorization": f"Bearer {creation.seat_tokens['arkania']}",
            **activation,
        },
    )

    assert v1.json()["results"] == []
    assert [item["id"] for item in v2.json()["results"]] == ["arkania-intel"]


def test_i100_006_search_contract_is_validated_and_sealed_in_all_player_records() -> None:
    with pytest.raises(ValueError, match="search_contract"):
        PlayerConfig(model="fake/a", search_contract="search-v3")

    config = PlayerConfig(model="fake/a", search_contract="search-v2")
    assert tool_schema_version_for(config) == tool_schema_version_for(
        PlayerConfig(model="fake/a", search_contract="search-v1")
    )
    recipe = build_resume_recipe(
        config,
        assignments={"arkania": "fake/a"},
        max_cycles=10,
        cons_concurrency=1,
        diplomacy_concurrency=1,
        condition=GameLoopCondition(),
    )
    assert recipe["player"]["search_contract"] == "search-v2"
    assert (
        PlayerConfig.from_resume_recipe(recipe, model="fake/b").search_contract == "search-v2"
    )
    del recipe["player"]["search_contract"]
    assert (
        PlayerConfig.from_resume_recipe(recipe, model="fake/b").search_contract == "search-v1"
    )

    manifest = expected_manifest_configuration(config)
    assert manifest["player"]["search_contract"] == "search-v2"
    assert "player.search_contract" in PLAYER_CONDITION_PATHS
    assert "resume.player.search_contract" in PLAYER_CONDITION_PATHS

    coordinator = object.__new__(AutonomousGameRunner)
    coordinator._condition = GameLoopCondition()  # type: ignore[attr-defined]
    fake_player = SimpleNamespace(
        config=config,
        actor_id="arkania",
        prompt_version="player-v1.11",
        prompt_hash="a" * 64,
        tool_schema_version="player-tools-test",
    )
    body = coordinator._agent_run_body(
        SimpleNamespace(player=fake_player), phase="argument", usage={}
    )
    assert body["model_parameters"]["search_contract"] == "search-v2"


def test_i100_007_settings_and_both_cli_commands_expose_search_contract() -> None:
    settings = Settings(AMAW_PLAYER_SEARCH_CONTRACT="search-v2", _env_file=None)
    assert settings.player_search_contract == "search-v2"
    assert "search-contract" in amaw_cli.RESUME_OVERRIDE_FLAGS
    assert "search_contract" in __import__("inspect").signature(amaw_cli.run).parameters
    assert "search_contract" in __import__("inspect").signature(amaw_cli.benchmark).parameters


def test_i108_001_sequential_and_every_batched_search_use_sealed_non_default_contract(
    db_engine,
) -> None:  # noqa: ANN001
    creation, client = _running_knowledge_game(db_engine)
    tools = PlayerTools(
        client,
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    calls: list[tuple[str, str]] = []

    def recording_search(
        *, query: str, search_contract: str = "search-v1"
    ) -> ToolResult:
        calls.append((query, search_contract))
        return ToolResult(ok=True, http_status=200, data={"query": query, "results": []})

    tools.search_resources = recording_search  # type: ignore[method-assign]

    def policy(messages, _info) -> ModelResponse:  # noqa: ANN001
        iteration = sum(isinstance(message, ModelResponse) for message in messages)
        if iteration == 0:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="search_resources", args={"query": "sequential"})
            ])
        if iteration == 1:
            return ModelResponse(parts=[ToolCallPart(
                tool_name="search_resources_batch",
                args={"queries": ["alpha", "beta", "gamma"]},
            )])
        return ModelResponse(parts=[TextPart(content="done")])

    player = ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name="Arkania",
        model=FunctionModel(policy),
        config=PlayerConfig(
            model="fake/i108",
            activation_mode="tool_loop",
            research_mode=RESEARCH_MODE_BATCHED_V1,
            search_contract="search-v2",
        ),
    )

    player.activate()

    assert calls == [
        ("sequential", "search-v2"),
        ("alpha", "search-v2"),
        ("beta", "search-v2"),
        ("gamma", "search-v2"),
    ]
