"""T-051 — Scenario Knowledge Acceptance Test (testing.md §21).

A dedicated scenario package carrying all six §21 content elements, and the release-blocking
proof that different agents independently retrieve their **own** authorised material, no private
Resource leaks (TEST-RES-002/003/004 semantics), and at least one AI Actor searches, reads and
then *uses* information that is absent from its brief.

The security assertions and the offline AI-behaviour proof run in the default (keyless) suite;
``test_scenario_knowledge_live`` repeats the AI-behaviour proof against real OpenRouter models
and is skipped without a key (testing.md §3).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import Engine

from app import game_service, scenarios
from app.api import create_app
from fastapi.testclient import TestClient
from tests.api_helpers import bearer

KNOWLEDGE_DIR = Path(__file__).parent / "fixtures" / "knowledge-scenario"
ACTORS = ("arkania", "belvar", "cestia")

# The decisive facts placed only inside a private Resource, one per authorised Actor. Each carries
# a unique codeword so a search for it can be shown to reveal nothing to an unauthorised Actor.
ARKANIA_SECRET_CODEWORD = "GREYPETREL"
BELVAR_SECRET_CODEWORD = "TIDEHOLD"


def _running_game(engine: Engine) -> tuple[TestClient, object, dict[str, str]]:
    """Create a game from the knowledge scenario and start it into a running phase."""
    loaded = scenarios.load_scenario(KNOWLEDGE_DIR)
    creation = scenarios.create_game_from_scenario(engine, loaded)
    game_service.start_game(engine, creation.game_id)  # LOBBY → DIPLOMACY (running)
    client = TestClient(create_app(engine=engine), raise_server_exceptions=False)
    return client, creation.game_id, creation.seat_tokens


def _search(client: TestClient, game_id, token: str, query: str):
    return client.get(
        f"/games/{game_id}/resources/search", params={"q": query}, headers=bearer(token)
    )


def _read(client: TestClient, game_id, token: str, resource_id: str):
    return client.get(f"/games/{game_id}/resources/{resource_id}", headers=bearer(token))


# The Resources each Actor is authorised to see: everyone sees the three public Resources; each
# larger power also sees its own private intelligence estimate.
PUBLIC_IDS = {"background", "trade", "strait-map"}
AUTHORISED: dict[str, set[str]] = {
    "arkania": PUBLIC_IDS | {"arkania-intel"},
    "belvar": PUBLIC_IDS | {"belvar-intel"},
    "cestia": set(PUBLIC_IDS),
}
PRIVATE_OF = {"arkania": "arkania-intel", "belvar": "belvar-intel"}
SECRET_OF = {"arkania": ARKANIA_SECRET_CODEWORD, "belvar": BELVAR_SECRET_CODEWORD}


def _brief_text(actor: scenarios.ActorDef) -> str:
    """Everything an Actor is told at the start: brief, objectives and private information."""
    return "\n".join(
        [actor.private_brief, *actor.objectives, *actor.private_information]
    ).casefold()


def test_knowledge_scenario_has_the_six_required_elements() -> None:
    """testing.md §21: the scenario contains all six required content elements."""
    loaded = scenarios.load_scenario(KNOWLEDGE_DIR)
    by_id = {r.declaration.id: r for r in loaded.resources}

    # public background
    background = by_id["background"]
    assert background.visibility == "public"
    assert background.representation_kind == "text"

    # private Resource for A and a distinct private Resource for B, each to a different Actor
    arkania_intel = by_id["arkania-intel"]
    belvar_intel = by_id["belvar-intel"]
    assert arkania_intel.visibility == "private" and arkania_intel.visible_to == ["arkania"]
    assert belvar_intel.visibility == "private" and belvar_intel.visible_to == ["belvar"]

    # CSV Resource
    trade = by_id["trade"]
    assert trade.media_type == "text/csv; charset=utf-8"
    assert trade.representation_kind == "text"

    # map/image attachment
    strait_map = by_id["strait-map"]
    assert strait_map.representation_kind == "attachment"
    assert strait_map.media_type == "image/png"
    assert strait_map.attachment_name == "strait-map.png"

    # critical information absent from initial brief: each private intel carries a decisive fact
    # (its codeword) that appears in that Actor's Resource text but in no Actor's brief.
    assert ARKANIA_SECRET_CODEWORD in (arkania_intel.text_content or "")
    assert BELVAR_SECRET_CODEWORD in (belvar_intel.text_content or "")
    all_briefs = "\n".join(_brief_text(a) for a in loaded.definition.actors.values())
    assert ARKANIA_SECRET_CODEWORD.casefold() not in all_briefs
    assert BELVAR_SECRET_CODEWORD.casefold() not in all_briefs


def test_each_public_resource_is_searchable_and_readable_by_every_actor(
    db_engine: Engine,
) -> None:
    """TEST-RES-001 over this scenario: the public background/CSV/map reach every Actor."""
    client, game_id, tokens = _running_game(db_engine)
    for actor in ACTORS:
        found = {
            item["id"]
            for query in ("northern strait", "trade", "map")
            for item in _search(client, game_id, tokens[actor], query).json()["results"]
        }
        assert PUBLIC_IDS <= found, (actor, found)
        for rid in PUBLIC_IDS:
            assert _read(client, game_id, tokens[actor], rid).status_code == 200, (actor, rid)


def test_res_002_private_intel_reaches_only_its_authorised_actor(db_engine: Engine) -> None:
    """TEST-RES-002: A's private Resource is searchable/readable by A and not_found-safe for others."""
    client, game_id, tokens = _running_game(db_engine)
    for owner, resource_id in PRIVATE_OF.items():
        # The owner can discover it (by its codeword) and read it.
        owner_hits = _search(client, game_id, tokens[owner], SECRET_OF[owner]).json()["results"]
        assert [item["id"] for item in owner_hits] == [resource_id]
        owner_read = _read(client, game_id, tokens[owner], resource_id)
        assert owner_read.status_code == 200
        assert SECRET_OF[owner] in owner_read.json()["resource"]["content"]
        # No other Actor can reveal or read it.
        for other in set(ACTORS) - {owner}:
            assert _search(client, game_id, tokens[other], SECRET_OF[owner]).json()["results"] == []
            denied = _read(client, game_id, tokens[other], resource_id)
            assert denied.status_code == 404
            assert denied.json() == {
                "error": {"code": "not_found", "message": "Resource not found."}
            }


def test_res_003_hidden_intel_term_returns_zero_results_for_unauthorised_actors(
    db_engine: Engine,
) -> None:
    """TEST-RES-003: a term matching only another Actor's hidden Resource yields count == 0."""
    client, game_id, tokens = _running_game(db_engine)
    for owner, codeword in SECRET_OF.items():
        for other in set(ACTORS) - {owner}:
            body = _search(client, game_id, tokens[other], codeword).json()
            assert body == {"query": codeword, "results": []}, (owner, other)


def test_res_004_no_hidden_metadata_leaks_through_any_actor_facing_surface(
    db_engine: Engine,
) -> None:
    """TEST-RES-004: no hidden id/name/tags/path/snippet/metadata reaches an unauthorised Actor.

    Checked across every unauthorised Actor's search, context and event surfaces — the private
    intelligence Resource's id, name, codeword and owner-only tag must appear in none of them.
    """
    client, game_id, tokens = _running_game(db_engine)
    for owner, resource_id in PRIVATE_OF.items():
        loaded_name = {
            "arkania-intel": "Arkanian Intelligence Estimate",
            "belvar-intel": "Belvar Intelligence Estimate",
        }[resource_id]
        needles = [resource_id, loaded_name, SECRET_OF[owner], "resources/"]
        for other in set(ACTORS) - {owner}:
            auth = bearer(tokens[other])
            # A neutral search (the shared "intelligence" tag), context and events are the surfaces
            # a leak would reach. A search *for* the codeword is excluded: its response echoes the
            # query the searcher themselves supplied (TEST-RES-003 asserts it returns no results).
            blobs = [
                _search(client, game_id, tokens[other], "intelligence").text,
                client.get(f"/games/{game_id}/context", headers=auth).text,
                client.get(f"/games/{game_id}/events", headers=auth).text,
            ]
            for needle in needles:
                assert all(needle not in blob for blob in blobs), (owner, other, needle)


def test_done_when_each_agent_retrieves_only_its_own_authorised_material(
    db_engine: Engine,
) -> None:
    """WBS done-when: different agents independently retrieve their authorised material, no leak.

    For every ordered Actor pair, the material one Actor can search and read is exactly its
    authorised set, and none of the other Actor's private-only material appears in it.
    """
    client, game_id, tokens = _running_game(db_engine)
    # A broad search that would match every Resource by tag or content if visibility were ignored.
    broad = "strait"
    for actor in ACTORS:
        # Union of what this Actor can reach: a broad search plus every private codeword.
        reachable = {
            item["id"]
            for query in (broad, "intelligence", "trade", "map", "background", *SECRET_OF.values())
            for item in _search(client, game_id, tokens[actor], query).json()["results"]
        }
        assert reachable == AUTHORISED[actor], (actor, reachable)
        # Direct reads confirm the same boundary: authorised → 200, everything else → 404.
        for rid in ("background", "trade", "strait-map", "arkania-intel", "belvar-intel"):
            expected = 200 if rid in AUTHORISED[actor] else 404
            assert _read(client, game_id, tokens[actor], rid).status_code == expected, (actor, rid)


def _advance_to_argument(engine: Engine):
    """Create + start the knowledge game and ready all Actors → ARGUMENT, arkania active."""
    loaded = scenarios.load_scenario(KNOWLEDGE_DIR)
    creation = scenarios.create_game_from_scenario(engine, loaded)
    game_service.start_game(engine, creation.game_id)
    for actor in ACTORS:
        game_service.ready(engine, creation.game_id, actor)
    return creation


def test_offline_ai_actor_seeks_reads_and_uses_brief_absent_information(
    db_engine: Engine,
) -> None:
    """testing.md §21: an AI Actor searches, reads, and *uses* brief-absent information.

    A deterministic FunctionModel drives arkania through its own decision: search for its
    intelligence, read the estimate, then build an Argument whose supporting reason is derived
    from the returned text — the GREYPETREL fuel-window fact that appears in no brief. This is
    the always-run, keyless proof of the §21 behaviour (the live proof is marked ``llm``).
    """
    from pydantic_ai.messages import (
        ModelResponse,
        TextPart,
        ToolCallPart,
        ToolReturnPart,
    )
    from pydantic_ai.models.function import AgentInfo, FunctionModel

    from agents.player import PlayerConfig, ReferencePlayer
    from agents.tools import PlayerTools

    creation = _advance_to_argument(db_engine)

    def _fact_from_read(messages) -> str:
        """The sentence carrying the codeword, taken from what read_resource returned."""
        for message in reversed(messages):
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_resource":
                    text = part.content.get("data", {}).get("resource", {}).get("content", "")
                    for sentence in text.replace("\n", " ").split("."):
                        if ARKANIA_SECRET_CODEWORD in sentence:
                            return sentence.strip()
        return ""

    def run(messages, info: AgentInfo) -> ModelResponse:
        idx = sum(1 for m in messages if isinstance(m, ModelResponse))
        if idx == 0:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="search_resources", args={"query": "intelligence"})
            ])
        if idx == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="read_resource", args={"resource_id": "arkania-intel"})
            ])
        if idx == 2:
            return ModelResponse(parts=[
                ToolCallPart(
                    tool_name="make_argument",
                    args={
                        "action": "Press Belvar to settle transit terms before its fuel window closes.",
                        "desired_effect": "Belvar accepts an Arkanian-led transit regime.",
                        "pros": [f"Intelligence I retrieved: {_fact_from_read(messages)}"],
                    },
                )
            ])
        return ModelResponse(parts=[TextPart(content="done")])

    tools = PlayerTools(
        TestClient(create_app(engine=db_engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    name = tools.observe().data["you"]["name"]
    player = ReferencePlayer(
        tools,
        actor_id="arkania",
        actor_name=name,
        model=FunctionModel(run),
        config=PlayerConfig(model="fake/deterministic", activation_mode="tool_loop"),
    )
    assert {"search_resources", "read_resource", "make_argument"} <= player.tool_names

    result = player.activate()

    # The three steps happened in order — research is the agent's own decision, before it acts.
    names = result.tool_call_names
    assert names.index("search_resources") < names.index("read_resource") < names.index(
        "make_argument"
    )

    returns = {
        part.tool_name: part.content
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    }
    # Each research call was a genuine server success, not an unknown-tool bounce.
    assert returns["search_resources"]["ok"] is True
    assert [r["id"] for r in returns["search_resources"]["data"]["results"]] == ["arkania-intel"]
    assert returns["read_resource"]["ok"] is True
    assert ARKANIA_SECRET_CODEWORD in returns["read_resource"]["data"]["resource"]["content"]
    assert returns["make_argument"]["ok"] is True

    # The Argument landed on the server and carries the retrieved, brief-absent fact.
    current = tools.observe().data["current_argument"]
    assert current is not None
    assert ARKANIA_SECRET_CODEWORD in " ".join(current["pros"])


@pytest.mark.llm
def test_scenario_knowledge_live(db_engine: Engine) -> None:
    """testing.md §21 with a real model: an AI Actor autonomously seeks, reads and argues.

    Bounded to one live activation of the active Actor in the ARGUMENT phase (a handful of model
    calls, not a full game): arkania must discover its authorised private intelligence, read a
    Resource, and submit an Argument — and belvar's private codeword must never appear in
    arkania's activation. Skipped without ``OPENROUTER_API_KEY`` (testing.md §3).
    """
    from pydantic_ai.messages import ToolReturnPart

    from agents.player import GuardrailConfig, PlayerConfig, ReferencePlayer
    from agents.tools import PlayerTools
    from app.config import Settings

    settings = Settings()
    if settings.openrouter_api_key is None:
        pytest.skip("no OPENROUTER_API_KEY")

    creation = _advance_to_argument(db_engine)  # ARGUMENT phase, arkania active — no model used
    tools = PlayerTools(
        TestClient(create_app(engine=db_engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    name = tools.observe().data["you"]["name"]
    player = ReferencePlayer.for_openrouter(
        tools,
        actor_id="arkania",
        actor_name=name,
        config=PlayerConfig(
            model=settings.player_model,
            temperature=0.0,
            reasoning_effort="low",
            max_tokens=4000,
            timeout=600.0,
            guardrails=GuardrailConfig(max_llm_iterations=8, max_tool_calls=12),
        ),
        settings=settings,
    )

    result = player.activate()

    # The agent chose to research, and both research tools executed against the server.
    assert {"search_resources", "read_resource"} <= set(result.tool_call_names), result.tool_call_names
    returns = [
        part.content
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
    ]
    searched = [c for c in returns if isinstance(c, dict) and "results" in c.get("data", {})]
    read = [c for c in returns if isinstance(c, dict) and "resource" in c.get("data", {})]
    assert any(c.get("ok") for c in searched)
    assert any(c.get("ok") for c in read)
    # arkania discovered its own authorised private intelligence.
    discovered = {
        item["id"] for c in searched for item in c.get("data", {}).get("results", [])
    }
    assert "arkania-intel" in discovered, discovered

    # It submitted an Argument — the retrieved knowledge is put to use in later behaviour.
    assert tools.observe().data["current_argument"] is not None

    # No leak: belvar's private codeword never surfaces anywhere in arkania's activation.
    activation_blob = repr(result.messages)
    assert BELVAR_SECRET_CODEWORD not in activation_blob
