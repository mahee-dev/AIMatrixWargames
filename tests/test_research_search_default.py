"""I-112 — `search-v2` becomes the released default (D-087 Decision A, D-088).

Three things must hold together, and the first two pull against each other:

* every place a *new* run resolves an unstated Resource-search contract now yields `search-v2`;
* an explicit `search-v1` still reaches the matcher as `search-v1`, so the reproducibility
  condition I-100 compared against is still runnable; and
* a record written before this change — a legacy resume recipe with no `search_contract` — still
  resolves to `search-v1`, because it was sealed under the old default and is never reinterpreted.

`TEST-I100-002/003` own `search-v2`'s frozen ranking; nothing here re-tests it. The discriminating
query below is the one `TEST-I100-005` already uses: whole-query v1 finds nothing, token-coverage
v2 finds the Actor's own intel Resource.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from agents.autonomous import build_resume_recipe, expected_manifest_configuration
from agents.player import PlayerConfig
from agents.tools import PlayerTools
from amaw_contracts import SEARCH_CONTRACT_DEFAULT, SEARCH_CONTRACT_V1, SEARCH_CONTRACT_V2
from app import game_service, resources, scenarios
from app.api import create_app
from app.config import Settings
from app.game_loop import GameLoopCondition
from app.models import Resource

KNOWLEDGE_DIR = Path(__file__).parent / "fixtures" / "knowledge-scenario"

# Only v2's token coverage reaches this Resource; v1's whole-query substring cannot.
SPLIT_QUERY = "GREYPETREL unrelated"
SPLIT_RESULT = ["arkania-intel"]


def _resource(resource_id: str, name: str, *, text: str = "") -> Resource:
    raw = text.encode("utf-8")
    return Resource(
        id=resource_id,
        game_id=uuid.uuid4(),
        name=name,
        type="background",
        visibility="public",
        visible_to=[],
        tags=[],
        resource_metadata={},
        source_kind="inline",
        source_bytes=raw,
        content_sha256=hashlib.sha256(raw).hexdigest(),
        representation_kind="text",
        media_type="text/plain; charset=utf-8",
        text_content=text,
        size_bytes=len(raw),
    )


def _running_knowledge_game(engine):  # noqa: ANN001, ANN202
    creation = scenarios.create_game_from_scenario(engine, scenarios.load_scenario(KNOWLEDGE_DIR))
    game_service.start_game(engine, creation.game_id)
    return creation, TestClient(create_app(engine=engine), raise_server_exceptions=False)


def _resume_recipe(config: PlayerConfig) -> dict:
    return build_resume_recipe(
        config,
        assignments={"arkania": config.model},
        max_cycles=10,
        cons_concurrency=1,
        diplomacy_concurrency=1,
        condition=GameLoopCondition(),
    )


# --- TEST-I112-001: the released default is search-v2 -----------------------------------------


def test_i112_001_every_unstated_search_contract_resolves_to_v2() -> None:
    assert SEARCH_CONTRACT_DEFAULT == SEARCH_CONTRACT_V2

    # The two condition surfaces a new run is composed from.
    assert Settings(_env_file=None).player_search_contract == SEARCH_CONTRACT_V2
    assert PlayerConfig(model="fake/a").search_contract == SEARCH_CONTRACT_V2
    assert _resume_recipe(PlayerConfig(model="fake/a"))["player"]["search_contract"] == (
        SEARCH_CONTRACT_V2
    )
    assert expected_manifest_configuration(PlayerConfig(model="fake/a"))["player"][
        "search_contract"
    ] == SEARCH_CONTRACT_V2

    # And the matcher itself: "foothold" appears only in the body, which whole-query v1 cannot
    # reach and token-coverage v2 can.
    library = [_resource("naval", "Belvar naval posture", text="A foothold is disputed.")]
    assert [r.id for r in resources.search_resources(library, "Belvar foothold")[1]] == ["naval"]
    assert resources.search_resources(library, "Belvar foothold", contract=SEARCH_CONTRACT_V1)[
        1
    ] == []


def test_i112_001_http_search_without_a_contract_parameter_uses_v2(db_engine) -> None:  # noqa: ANN001
    creation, client = _running_knowledge_game(db_engine)

    def search(**params: str) -> list[str]:
        response = client.get(
            f"/games/{creation.game_id}/resources/search",
            params={"q": SPLIT_QUERY, **params},
            headers={"Authorization": f"Bearer {creation.seat_tokens['arkania']}"},
        )
        assert response.status_code == 200, response.text
        return [item["id"] for item in response.json()["results"]]

    assert search() == SPLIT_RESULT
    assert search(contract=SEARCH_CONTRACT_V2) == SPLIT_RESULT
    assert search(contract=SEARCH_CONTRACT_V1) == []


# --- TEST-I112-002: explicit search-v1 survives the wire --------------------------------------


def test_i112_002_the_player_tool_carries_an_explicit_contract_to_the_server(db_engine) -> None:  # noqa: ANN001
    """The reproducibility condition may not ride on the server's default.

    Before I-112 the tool omitted ``contract`` whenever the sealed condition was v1 and let the
    server fall back to it. With the default flipped, that omission would silently upgrade a
    sealed ``search-v1`` player to v2, so the tool must now always state the contract.
    """
    creation, client = _running_knowledge_game(db_engine)
    tools = PlayerTools(
        client, game_id=str(creation.game_id), token=creation.seat_tokens["arkania"]
    )

    def ids(**kwargs: str) -> list[str]:
        result = tools.search_resources(query=SPLIT_QUERY, **kwargs)
        assert result.ok, result.error
        return [item["id"] for item in result.data["results"]]

    assert ids(search_contract=SEARCH_CONTRACT_V1) == []
    assert ids(search_contract=SEARCH_CONTRACT_V2) == SPLIT_RESULT
    assert ids() == SPLIT_RESULT


def test_i112_002_an_explicit_v1_player_condition_is_still_sealed_and_valid() -> None:
    config = PlayerConfig(model="fake/a", search_contract=SEARCH_CONTRACT_V1)
    assert config.search_contract == SEARCH_CONTRACT_V1
    recipe = _resume_recipe(config)
    assert recipe["player"]["search_contract"] == SEARCH_CONTRACT_V1
    assert PlayerConfig.from_resume_recipe(recipe, model="fake/b").search_contract == (
        SEARCH_CONTRACT_V1
    )
    assert expected_manifest_configuration(config)["player"]["search_contract"] == (
        SEARCH_CONTRACT_V1
    )
    assert Settings(AMAW_PLAYER_SEARCH_CONTRACT="search-v1", _env_file=None).player_search_contract\
        == SEARCH_CONTRACT_V1


# --- TEST-I112-003: pre-I-112 records are not reinterpreted -----------------------------------


def test_i112_003_a_legacy_recipe_without_a_contract_still_resolves_to_v1() -> None:
    recipe = _resume_recipe(PlayerConfig(model="fake/a"))
    del recipe["player"]["search_contract"]
    assert PlayerConfig.from_resume_recipe(recipe, model="fake/b").search_contract == (
        SEARCH_CONTRACT_V1
    )


def test_i112_003_an_unknown_contract_is_still_refused_everywhere() -> None:
    with pytest.raises(ValueError, match="search_contract"):
        PlayerConfig(model="fake/a", search_contract="search-v3")
    with pytest.raises(ValueError, match="search contract"):
        resources.search_resources([_resource("r", "Resource")], "resource", contract="search-v3")
