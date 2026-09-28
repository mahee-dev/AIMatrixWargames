"""I-098 / I-099 — research operations budget and search-result identity.

I-098: `batched-v1` granted up to ``RESEARCH_BATCH_MAX_ITEMS`` Resource operations per research
*invocation* while sealing the same ``max_research_tool_calls`` as `sequential-v1`. A second
guardrail, ``max_research_operations``, charges every dispatched Resource search/read — batched
or not — immediately before dispatch (D-078; agents.md §18). The two research modes then differ
only in request topology, and both counts are observable per activation.

I-099: a search row records what the authorised response returned (``result_count``, ordered
``result_resource_ids``, ``result_set_sha256``) so retrieval failure can be told apart from
continued model research (TRD §29, methodology §17, api.md §10.1).

Offline, no key: real :class:`pydantic_ai.Agent`s driven by :class:`FunctionModel` policies
against the real server.
"""

from __future__ import annotations

import hashlib
import json
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from sqlalchemy import Engine, inspect, text
from sqlmodel import select

import amaw_cli
import amaw_contracts
from agents.autonomous import AutonomousGameRunner, build_resume_recipe
from agents.guardrails import GuardrailConfig
from agents.lifecycle import LifecycleRunner
from agents.player import (
    RESEARCH_MODE_BATCHED_V1,
    RESEARCH_MODE_SEQUENTIAL_V1,
    PlayerConfig,
    ReferencePlayer,
)
from agents.tools import PlayerTools
from app import export as export_module, game_service, provenance, summary
from benchmark import outputs
from app.api import create_app
from app.config import Settings
from app.database import engine_for_path, initialize_database, session_scope
from app.game_loop import GameLoopCondition
from app.models import ResourceAccess
from app.provenance import ProvenanceError
from tests.agent_fakes import model_from_policy
from tests.api_helpers import ACTORS, bearer, client, seed, start_to_diplomacy

ADMIN_TOKEN = "i-098-admin"

# The sequential research tools' model-facing descriptions as they were before I-098 (captured
# 2026-09-21 from `player-tools-v11-live-bounded-research` at the default ceiling of 4). The
# operation ceiling adds nothing true of a sequential tool — one invocation is one operation —
# so these bytes must not move (TEST-I098-005).
_PINNED_SEQUENTIAL_DESCRIPTIONS = {
    "read_messages": (
        "Read the Messages currently visible to you. Research is optional; 4 research tool "
        "calls remain in this activation. Do not repeat a successful call; reserve capacity "
        "for the required decision."
    ),
    "search_resources": (
        "Search scenario Resources available to you for information relevant to your goals. "
        "Research is optional; 4 research tool calls remain in this activation. Do not repeat "
        "a successful call; reserve capacity for the required decision."
    ),
    "read_resource": (
        "Read one scenario Resource you are authorised to see (use an id from "
        "search_resources). Research is optional; 4 research tool calls remain in this "
        "activation. Do not repeat a successful call; reserve capacity for the required decision."
    ),
}


# --- helpers -------------------------------------------------------------------------------


def _player(
    engine: Engine,
    creation,  # noqa: ANN001
    policy,  # noqa: ANN001
    *,
    actor: str = "arkania",
    research_mode: str = RESEARCH_MODE_SEQUENTIAL_V1,
    guardrails: GuardrailConfig | None = None,
    con_reply_mode: str = "sealed_cons",
) -> ReferencePlayer:
    tools = PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )
    config = PlayerConfig(
        model="fake/i098",
        activation_mode="tool_loop",
        research_mode=research_mode,
        con_reply_mode=con_reply_mode,
        guardrails=guardrails or GuardrailConfig(),
    )
    return ReferencePlayer(
        tools, actor_id=actor, actor_name=actor.title(), model=model_from_policy(policy),
        config=config,
    )


def _coordinator(engine: Engine) -> AutonomousGameRunner:
    api_client = TestClient(
        create_app(engine=engine, settings=Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)),
        raise_server_exceptions=False,
    )
    return AutonomousGameRunner(
        api_client, admin_token=ADMIN_TOKEN, scenario="tests/fixtures/deterministic-game",
        player_factory=lambda *_args: None,
    )


def _access_rows(engine: Engine, creation) -> list[tuple[str, str | None, str | None]]:  # noqa: ANN001
    with session_scope(engine) as session:
        return [
            (row.operation, row.query, row.resource_id)
            for row in session.exec(
                select(ResourceAccess)
                .where(ResourceAccess.game_id == creation.game_id)
                .order_by(ResourceAccess.id)
            )
        ]


def _tool_returns(result, tool_name: str) -> list:  # noqa: ANN001
    return [
        part
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def _recipe(config: PlayerConfig, **assignments: str) -> dict:
    """A CLI-shaped recipe under the coordinator's own defaults (so a coordinator seals it)."""
    from agents.autonomous import DEFAULT_CONS_CONCURRENCY, DEFAULT_DIPLOMACY_CONCURRENCY

    return build_resume_recipe(
        config,
        assignments=assignments or {actor: config.model for actor in ACTORS},
        max_cycles=1000,
        cons_concurrency=DEFAULT_CONS_CONCURRENCY,
        diplomacy_concurrency=DEFAULT_DIPLOMACY_CONCURRENCY,
        condition=GameLoopCondition(),
    )


# =============================================================================================
# I-098
# =============================================================================================

# --- TEST-I098-001: the operation ceiling is a sealed condition ------------------------------


def test_i098_001_guardrail_default_is_four_and_non_negative() -> None:
    assert GuardrailConfig().max_research_operations == 4
    assert GuardrailConfig(max_research_operations=0).max_research_operations == 0
    with pytest.raises(ValueError, match="max_research_operations"):
        GuardrailConfig(max_research_operations=-1)


def test_i098_001_settings_and_cli_carry_the_ceiling(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AMAW_MAX_RESEARCH_OPERATIONS", raising=False)
    assert Settings(_env_file=None).max_research_operations == 4
    monkeypatch.setenv("AMAW_MAX_RESEARCH_OPERATIONS", "7")
    assert Settings(_env_file=None).max_research_operations == 7
    with pytest.raises(ValueError):
        Settings(AMAW_MAX_RESEARCH_OPERATIONS=-1, _env_file=None)
    # A condition flag of ``amaw run``, refused on resume like every other (I-048/I-061).
    assert "max-research-operations" in amaw_cli.RESUME_OVERRIDE_FLAGS


def test_i098_001_recipe_round_trips_and_a_legacy_recipe_resolves_to_the_default() -> None:
    assert amaw_contracts.PLAYER_GUARDRAIL_OPTIONAL_FIELDS == (
        "max_research_tool_calls", "max_research_operations",
    )
    config = PlayerConfig(
        model="fake/a", research_mode=RESEARCH_MODE_BATCHED_V1,
        guardrails=GuardrailConfig(max_research_operations=2),
    )
    recipe = _recipe(config)
    assert recipe["player"]["guardrails"]["max_research_operations"] == 2
    parsed = PlayerConfig.from_resume_recipe(recipe, model="fake/a")
    assert parsed.guardrails.max_research_operations == 2
    assert _recipe(parsed) == recipe

    legacy = _recipe(PlayerConfig(model="fake/a", research_mode=RESEARCH_MODE_BATCHED_V1))
    del legacy["player"]["guardrails"]["max_research_operations"]
    resolved = PlayerConfig.from_resume_recipe(legacy, model="fake/a")
    assert resolved.guardrails.max_research_operations == 4


def test_i098_001_a_legacy_recipe_is_sealed_as_given_and_runs_seal_the_ceiling(
    db_engine: Engine,
) -> None:
    from tests.test_provenance_condition_projections import _roster

    base = PlayerConfig(
        model="fake/a", activation_mode="tool_loop",
        guardrails=GuardrailConfig(max_research_operations=2),
    )
    legacy = _recipe(base)
    del legacy["player"]["guardrails"]["max_research_operations"]
    legacy_players = PlayerConfig.from_resume_recipe(legacy, model="fake/a")
    coordinator = _coordinator(db_engine)
    coordinator._run_configuration = {"resume": legacy}
    sealed = coordinator._manifest_configuration(
        _roster(legacy_players, legacy_players, legacy_players)
    )["resume"]
    # Semantically the default; sealed byte-for-byte as supplied — no key is inserted.
    assert "max_research_operations" not in sealed["player"]["guardrails"]
    assert {**sealed, "player": {**sealed["player"], "assignments": {}}} == {
        **legacy, "player": {**legacy["player"], "assignments": {}},
    }

    # A fresh run seals it in the manifest and in every player AgentRun's model_parameters.
    current = _coordinator(db_engine)
    manifest = current._manifest_configuration(_roster(base, base, base))
    assert manifest["resume"]["player"]["guardrails"]["max_research_operations"] == 2
    runner = _roster(base, base, base)["arkania"]
    body = current._agent_run_body(runner, phase="diplomacy", usage={})
    assert body["model_parameters"]["guardrails"]["max_research_operations"] == 2


# --- TEST-I098-002: equivalence of the operation maximum across research modes ---------------

_FOUR_QUERIES = ["regional", "trade", "history", "politics"]


def _plan_sequential(packet, tool_names, idx):  # noqa: ANN001
    if idx < 4:
        return [ToolCallPart(tool_name="search_resources", args={"query": _FOUR_QUERIES[idx]})]
    if idx == 4:
        # A fifth operation: refused (the tool may already be withdrawn; if offered, it must
        # refuse without dispatching).
        return [ToolCallPart(tool_name="search_resources", args={"query": "fifth"})]
    return [TextPart(content="done")]


def _plan_one_batch(packet, tool_names, idx):  # noqa: ANN001
    if idx == 0:
        return [ToolCallPart(tool_name="search_resources_batch", args={"queries": _FOUR_QUERIES})]
    return [TextPart(content="done")]


def _plan_two_batches(packet, tool_names, idx):  # noqa: ANN001
    if idx == 0:
        return [ToolCallPart(
            tool_name="search_resources_batch", args={"queries": _FOUR_QUERIES[:2]},
        )]
    if idx == 1:
        return [ToolCallPart(
            tool_name="search_resources_batch", args={"queries": _FOUR_QUERIES[2:]},
        )]
    return [TextPart(content="done")]


@pytest.mark.parametrize(
    "research_mode, plan",
    [
        (RESEARCH_MODE_SEQUENTIAL_V1, _plan_sequential),
        (RESEARCH_MODE_BATCHED_V1, _plan_one_batch),
        (RESEARCH_MODE_BATCHED_V1, _plan_two_batches),
    ],
    ids=["sequential-4", "batched-1x4", "batched-2x2"],
)
def test_i098_002_equivalent_plans_reach_the_same_operation_maximum(
    db_engine: Engine, research_mode: str, plan,  # noqa: ANN001
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen_tools: list[list[str]] = []

    def observed(packet, tool_names, idx):  # noqa: ANN001
        seen_tools.append(list(tool_names))
        return plan(packet, tool_names, idx)

    player = _player(
        db_engine, creation, observed, research_mode=research_mode,
        # Invocations are not the binding constraint here; operations are.
        guardrails=GuardrailConfig(max_research_tool_calls=6, max_research_operations=4),
    )
    result = player.activate()

    assert _access_rows(db_engine, creation) == [("search", q, None) for q in _FOUR_QUERIES]
    assert result.research_operations == 4
    # Once the operation budget is spent every Resource tool is withdrawn, invocations or not.
    assert not {"search_resources", "read_resource", "search_resources_batch",
                "read_resources_batch"} & set(seen_tools[-1])
    assert "read_messages" in seen_tools[-1]


def test_i098_002_a_batch_crossing_the_remaining_budget_is_truncated_never_granted(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    eight = [f"query-{index}" for index in range(8)]

    def policy(packet, tool_names, idx):  # noqa: ANN001
        if idx == 0:
            return [ToolCallPart(tool_name="search_resources_batch", args={"queries": eight})]
        return [TextPart(content="done")]

    player = _player(
        db_engine, creation, policy, research_mode=RESEARCH_MODE_BATCHED_V1,
        guardrails=GuardrailConfig(max_research_tool_calls=6, max_research_operations=4),
    )
    result = player.activate()

    [batch_return] = _tool_returns(result, "search_resources_batch")
    payload = batch_return.content
    assert payload["ok"] is True
    assert [item["query"] for item in payload["data"]["items"]] == eight[:4]
    assert payload["data"]["not_executed"] == [
        {
            "query": query,
            "error": {"code": "guardrail_research_operations_exhausted"},
        }
        for query in eight[4:]
    ]
    assert _access_rows(db_engine, creation) == [("search", q, None) for q in eight[:4]]
    assert result.research_tool_calls == 1
    assert result.research_operations == 4


def test_i098_002_a_read_batch_is_truncated_at_the_remaining_budget_too(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(packet, tool_names, idx):  # noqa: ANN001
        if idx == 0:
            return [ToolCallPart(tool_name="search_resources", args={"query": "regional"})]
        if idx == 1:
            return [ToolCallPart(
                tool_name="read_resources_batch",
                args={"resource_ids": ["background", "trade", "arkania-intel"]},
            )]
        return [TextPart(content="done")]

    player = _player(
        db_engine, creation, policy, research_mode=RESEARCH_MODE_BATCHED_V1,
        guardrails=GuardrailConfig(max_research_tool_calls=6, max_research_operations=3),
    )
    result = player.activate()

    [batch_return] = _tool_returns(result, "read_resources_batch")
    payload = batch_return.content
    assert payload["ok"] is True
    assert [item["resource_id"] for item in payload["data"]["items"]] == ["background", "trade"]
    assert payload["data"]["not_executed"] == [
        {"resource_id": "arkania-intel",
         "error": {"code": "guardrail_research_operations_exhausted"}},
    ]
    assert _access_rows(db_engine, creation) == [
        ("search", "regional", None), ("read", None, "background"), ("read", None, "trade"),
    ]
    assert result.research_operations == 3


# --- TEST-I098-003: what is charged ----------------------------------------------------------


def test_i098_003_read_messages_charges_an_invocation_but_no_operation(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen_tools: list[list[str]] = []

    def policy(packet, tool_names, idx):  # noqa: ANN001
        seen_tools.append(list(tool_names))
        if idx == 0:
            return [ToolCallPart(tool_name="read_messages", args={})]
        if idx == 1:
            return [ToolCallPart(tool_name="search_resources", args={"query": "regional"})]
        return [TextPart(content="done")]

    player = _player(
        db_engine, creation, policy,
        guardrails=GuardrailConfig(max_research_tool_calls=3, max_research_operations=1),
    )
    result = player.activate()

    assert result.tool_call_names == ["read_messages", "search_resources"]
    assert result.research_tool_calls == 2
    assert result.research_operations == 1
    assert _access_rows(db_engine, creation) == [("search", "regional", None)]
    # One invocation remains, no operation does: Resource tools withdrawn, Messages still offered.
    assert "read_messages" in seen_tools[2]
    assert "search_resources" not in seen_tools[2]
    assert "read_resource" not in seen_tools[2]


def test_i098_003_a_dispatched_read_that_fails_still_consumes_an_operation(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seen_tools: list[list[str]] = []

    def policy(packet, tool_names, idx):  # noqa: ANN001
        seen_tools.append(list(tool_names))
        if idx == 0:
            return [ToolCallPart(tool_name="read_resource", args={"resource_id": "missing"})]
        if idx == 1:
            return [ToolCallPart(tool_name="read_resource", args={"resource_id": "background"})]
        return [TextPart(content="done")]

    player = _player(
        db_engine, creation, policy,
        guardrails=GuardrailConfig(max_research_tool_calls=6, max_research_operations=2),
    )
    result = player.activate()

    first, second = _tool_returns(result, "read_resource")
    assert first.content["ok"] is False and first.content["http_status"] == 404
    assert second.content["ok"] is True
    # The 404 wrote no row (D-038) but was dispatched, so it was charged: two operations, one row.
    assert _access_rows(db_engine, creation) == [("read", None, "background")]
    assert result.research_operations == 2
    assert "read_resource" not in seen_tools[2]
    assert "search_resources" not in seen_tools[2]


def test_i098_003_a_client_side_exact_repeat_consumes_no_operation(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(packet, tool_names, idx):  # noqa: ANN001
        if idx < 2:
            return [ToolCallPart(tool_name="search_resources", args={"query": "regional"})]
        return [TextPart(content="done")]

    player = _player(
        db_engine, creation, policy,
        guardrails=GuardrailConfig(max_research_tool_calls=6, max_research_operations=4),
    )
    result = player.activate()

    returns = _tool_returns(result, "search_resources")
    assert returns[1].content["error"]["code"] == "guardrail_research_duplicate"
    assert result.research_tool_calls == 2
    assert result.research_operations == 1
    assert _access_rows(db_engine, creation) == [("search", "regional", None)]


def test_i098_003_a_sibling_call_beyond_the_budget_is_refused_without_dispatch(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    def policy(packet, tool_names, idx):  # noqa: ANN001
        if idx == 0:
            # Two sibling operations in one response with one operation remaining: the budget
            # is decided under the lock before either dispatches, so exactly one runs.
            return [
                ToolCallPart(tool_name="search_resources", args={"query": "regional"}),
                ToolCallPart(tool_name="search_resources", args={"query": "trade"}),
            ]
        return [TextPart(content="done")]

    player = _player(
        db_engine, creation, policy,
        guardrails=GuardrailConfig(max_research_tool_calls=6, max_research_operations=1),
    )
    result = player.activate()

    codes = sorted(
        part.content.get("error", {}).get("code", "ok")
        for part in _tool_returns(result, "search_resources")
    )
    assert codes == ["guardrail_research_operations_exhausted", "ok"]
    refused = next(
        part.content for part in _tool_returns(result, "search_resources")
        if part.content["ok"] is False
    )
    assert refused["http_status"] is None  # no request was made
    assert refused["error"]["details"] == {"limit": 1, "spent": 1}
    assert len(_access_rows(db_engine, creation)) == 1
    assert result.research_tool_calls == 2
    assert result.research_operations == 1
    events = result.invalid_tool_call_breakdown["events"]
    assert [(e["reason"], e["error_code"]) for e in events] == [
        ("client_guardrail", "guardrail_research_operations_exhausted")
    ]


# --- TEST-I098-004: both counts are observable per activation --------------------------------


def test_i098_004_completed_and_failed_activations_record_both_counts(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def research_then_ready(packet, tool_names, idx):  # noqa: ANN001
        if idx == 0:
            return [ToolCallPart(tool_name="read_messages", args={})]
        if idx == 1:
            return [ToolCallPart(
                tool_name="search_resources_batch", args={"queries": ["regional", "trade"]},
            )]
        if idx == 2:
            return [ToolCallPart(tool_name="ready", args={})]
        return [TextPart(content="done")]

    runner = LifecycleRunner(_player(
        db_engine, creation, research_then_ready, research_mode=RESEARCH_MODE_BATCHED_V1,
    ))
    outcome = runner.step()
    assert outcome.activation is not None
    assert outcome.activation.research_tool_calls == 2
    assert outcome.activation.research_operations == 2
    _coordinator(db_engine)._record_activation(str(creation.game_id), runner, outcome)

    def research_forever(packet, tool_names, idx):  # noqa: ANN001
        return [ToolCallPart(tool_name="search_resources", args={"query": f"q{idx}"})]

    failing = LifecycleRunner(_player(
        db_engine, creation, research_forever, actor="belvar",
        # Two research requests are charged; the third finds research withdrawn (one request is
        # reserved for the decision, I-069) and the unknown-tool retry exhausts the ceiling.
        guardrails=GuardrailConfig(
            max_llm_iterations=3, max_research_tool_calls=6, max_research_operations=6,
        ),
    ))
    failed = failing.step()
    assert failed.failure is not None
    assert failed.failure.counts["research_tool_calls"] == 2
    assert failed.failure.counts["research_operations"] == 2
    _coordinator(db_engine)._record_failure(str(creation.game_id), failing, failed)

    exported = export_module.build_game_export(db_engine, creation.game_id)
    by_actor = {record["actor_id"]: record for record in exported["agent_runs"]}
    assert by_actor["arkania"]["completed"] is True
    assert by_actor["arkania"]["tool_calls"] == 3
    assert by_actor["arkania"]["research_tool_calls"] == 2
    assert by_actor["arkania"]["research_operations"] == 2
    assert by_actor["belvar"]["completed"] is False
    assert by_actor["belvar"]["research_tool_calls"] == 2
    assert by_actor["belvar"]["research_operations"] == 2


def test_i098_004_packet_states_the_operation_ceiling(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    packets: list[dict] = []

    def policy(packet, tool_names, idx):  # noqa: ANN001
        packets.append(packet)
        return [TextPart(content="done")]

    _player(
        db_engine, creation, policy, guardrails=GuardrailConfig(max_research_operations=3),
    ).activate()
    assert packets[0]["limits"]["max_research_tool_calls"] == 4
    assert packets[0]["limits"]["max_research_operations"] == 3


def test_i098_004_legacy_agent_runs_migrate_additively_and_export_null(
    tmp_path, db_engine: Engine,  # noqa: ANN001
) -> None:
    engine = engine_for_path(tmp_path / "pre-i098.db")
    initialize_database(engine)
    with engine.begin() as connection:
        for name in ("research_tool_calls", "research_operations"):
            connection.exec_driver_sql(f"ALTER TABLE agentrun DROP COLUMN {name}")
    initialize_database(engine)
    columns = {column["name"]: column for column in inspect(engine).get_columns("agentrun")}
    assert columns["research_tool_calls"]["nullable"] is True
    assert columns["research_operations"]["nullable"] is True

    # A row recorded without the counts (a pre-I-098 client) exports them as unavailable.
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    provenance.record_agent_run(
        db_engine, creation.game_id, actor_id="arkania", role="player", turn=1,
        phase="diplomacy", model_requested="legacy/model", prompt_version="player-test",
        prompt_hash="a" * 64, agent_framework="pydantic-ai",
        tool_schema_version="player-tools-v3", model_calls=1, tool_calls=2, completed=True,
    )
    [record] = export_module.build_game_export(db_engine, creation.game_id)["agent_runs"]
    assert record["tool_calls"] == 2
    assert record["research_tool_calls"] is None
    assert record["research_operations"] is None


@pytest.mark.parametrize(
    "field, value",
    [
        ("research_tool_calls", -1),
        ("research_operations", -1),
        ("research_tool_calls", "2"),
        ("research_operations", 1.5),
        ("research_operations", True),
    ],
)
def test_i098_004_provenance_rejects_an_invalid_count(
    db_engine: Engine, field: str, value: object,
) -> None:
    creation = seed(db_engine)
    with pytest.raises(ProvenanceError, match=field):
        provenance.record_agent_run(
            db_engine, creation.game_id, actor_id="arkania", role="player",
            model_requested="fake", prompt_version="p", prompt_hash="a" * 64,
            agent_framework="pydantic-ai", tool_schema_version="player-tools-v3",
            **{field: value},
        )


# --- TEST-I098-005: schema identity -----------------------------------------------------------


def test_i098_005_sequential_surfaces_are_unchanged_and_batched_surfaces_are_versioned(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    descriptions: dict[str, str] = {}

    def capture(packet, tool_names, idx):  # noqa: ANN001
        return [TextPart(content="done")]

    # Pull the prepared tool definitions the model actually sees.
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models.function import FunctionModel

    def run(messages, info):  # noqa: ANN001
        descriptions.update({t.name: t.description for t in info.function_tools})
        return ModelResponse(parts=[TextPart(content="done")])

    sequential = ReferencePlayer(
        PlayerTools(
            client(db_engine), game_id=str(creation.game_id),
            token=creation.seat_tokens["arkania"],
        ),
        actor_id="arkania", actor_name="Arkania", model=FunctionModel(run),
        config=PlayerConfig(model="fake/i098", activation_mode="tool_loop"),
    )
    sequential.activate()
    assert sequential.tool_schema_version == "player-tools-v11-live-bounded-research"
    for name, pinned in _PINNED_SEQUENTIAL_DESCRIPTIONS.items():
        assert descriptions[name] == pinned, name

    reply = _player(db_engine, creation, capture, con_reply_mode="visible_reply")
    assert reply.tool_schema_version == "player-tools-v13-reply-live-bounded-research"

    batched = _player(db_engine, creation, capture, research_mode=RESEARCH_MODE_BATCHED_V1)
    assert batched.tool_schema_version == (
        "player-tools-v17-batched-live-bounded-operations-item-outcomes"
    )
    batched_reply = _player(
        db_engine, creation, capture, research_mode=RESEARCH_MODE_BATCHED_V1,
        con_reply_mode="visible_reply",
    )
    assert (
        batched_reply.tool_schema_version
        == "player-tools-v18-batched-reply-live-bounded-operations-item-outcomes"
    )


def test_i098_005_batched_tools_disclose_the_remaining_operations(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.models.function import FunctionModel

    descriptions: dict[str, str] = {}

    def run(messages, info):  # noqa: ANN001
        descriptions.update({t.name: t.description for t in info.function_tools})
        return ModelResponse(parts=[TextPart(content="done")])

    tools = PlayerTools(
        client(db_engine), game_id=str(creation.game_id), token=creation.seat_tokens["arkania"],
    )
    ReferencePlayer(
        tools, actor_id="arkania", actor_name="Arkania", model=FunctionModel(run),
        config=PlayerConfig(
            model="fake/i098", activation_mode="tool_loop",
            research_mode=RESEARCH_MODE_BATCHED_V1,
            guardrails=GuardrailConfig(max_research_operations=3),
        ),
    ).activate()
    # The sequential tools keep their pinned text even beside the batch tools …
    assert descriptions["search_resources"] == _PINNED_SEQUENTIAL_DESCRIPTIONS["search_resources"]
    # … and the batch tools state the operation budget and the truncation rule.
    for name in ("search_resources_batch", "read_resources_batch"):
        assert "3 Resource operations remain" in descriptions[name], name
        assert "not_executed" in descriptions[name], name
        assert "ordered success or error outcome" in descriptions[name], name


# =============================================================================================
# I-099
# =============================================================================================


def _search(c, game_id, token, query: str, activation_id: str | None = None):  # noqa: ANN001
    headers = bearer(token)
    if activation_id is not None:
        headers["X-AMAW-Activation-ID"] = activation_id
    return c.get(f"/games/{game_id}/resources/search", params={"q": query}, headers=headers)


def _read(c, game_id, token, resource_id: str, activation_id: str | None = None):  # noqa: ANN001
    headers = bearer(token)
    if activation_id is not None:
        headers["X-AMAW-Activation-ID"] = activation_id
    return c.get(f"/games/{game_id}/resources/{resource_id}", headers=headers)


def _result_rows(engine: Engine, creation) -> list[tuple]:  # noqa: ANN001
    with session_scope(engine) as session:
        return [
            (row.operation, row.query, row.result_count, row.result_resource_ids,
             row.result_set_sha256)
            for row in session.exec(
                select(ResourceAccess)
                .where(ResourceAccess.game_id == creation.game_id)
                .order_by(ResourceAccess.id)
            )
        ]


def _digest(ids: list[str]) -> str:
    return hashlib.sha256(amaw_contracts.canonical_json(ids)).hexdigest()


def _record_player_run(engine: Engine, creation, *, activation_id: str, actor_id="arkania"):  # noqa: ANN001
    return provenance.record_agent_run(
        engine, creation.game_id, activation_id=activation_id, actor_id=actor_id,
        role="player", turn=1, phase="diplomacy", model_requested="fake/researcher",
        prompt_version="player-test", prompt_hash="a" * 64, agent_framework="pydantic-ai",
        tool_schema_version="player-tools-v3", model_calls=2, completed=True,
    )


# --- TEST-I099-001: a search row records what the authorised response returned ----------------


def test_i099_001_search_rows_record_ordered_result_identity(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    activation_id = str(uuid.uuid4())

    response = _search(c, creation.game_id, token, "regional", activation_id)
    assert response.status_code == 200
    returned = [item["id"] for item in response.json()["results"]]
    assert returned  # the fixture's public Resources both match "Regional …"
    assert _search(c, creation.game_id, token, "no-such-term-anywhere").status_code == 200
    assert _read(c, creation.game_id, token, "background").status_code == 200
    # A same-activation same-folded-query replay (I-054) is a dispatched request: its own row,
    # carrying the identical ordered ids from the replay value.
    assert _search(c, creation.game_id, token, "  REGIONAL ", activation_id).status_code == 200

    rows = _result_rows(db_engine, creation)
    assert rows == [
        ("search", "regional", len(returned), returned, _digest(returned)),
        ("search", "no-such-term-anywhere", 0, [], _digest([])),
        ("read", None, None, None, None),
        ("search", "  REGIONAL ", len(returned), returned, _digest(returned)),
    ]
    # The digest is an ordered-result-list identity, not a set identity.
    assert _digest(["background", "trade"]) != _digest(["trade", "background"])
    # The Actor-facing response shape is unchanged.
    assert set(response.json()) == {"query", "results"}
    assert set(response.json()["results"][0]) == {"id", "name", "type", "tags", "snippet"}


# --- TEST-I099-002: no leak through the log ---------------------------------------------------


def test_i099_002_result_identity_is_computed_over_the_visible_list_only(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    # "belvar" matches the private arkania-intel Resource by tag (and public text); Belvar is
    # not authorised to see it (TEST-RES-002/003/004).
    arkania = _search(c, creation.game_id, creation.seat_tokens["arkania"], "belvar")
    belvar = _search(c, creation.game_id, creation.seat_tokens["belvar"], "belvar")
    assert arkania.status_code == belvar.status_code == 200
    arkania_ids = [item["id"] for item in arkania.json()["results"]]
    belvar_ids = [item["id"] for item in belvar.json()["results"]]
    assert "arkania-intel" in arkania_ids
    assert "arkania-intel" not in belvar_ids

    with session_scope(db_engine) as session:
        by_actor = {
            row.actor_id: (row.result_count, row.result_resource_ids, row.result_set_sha256)
            for row in session.exec(
                select(ResourceAccess).where(ResourceAccess.game_id == creation.game_id)
            )
        }
    assert by_actor["belvar"] == (len(belvar_ids), belvar_ids, _digest(belvar_ids))
    assert by_actor["arkania"] == (len(arkania_ids), arkania_ids, _digest(arkania_ids))
    assert by_actor["belvar"][2] != by_actor["arkania"][2]

    # Downstream rank is over the visible list too: Belvar reading a public Resource it was
    # returned ranks it within *its* list, never within Arkania's.
    activation_id = str(uuid.uuid4())
    token = creation.seat_tokens["belvar"]
    assert _search(c, creation.game_id, token, "belvar", activation_id).status_code == 200
    public_id = belvar_ids[0]
    assert _read(c, creation.game_id, token, public_id, activation_id).status_code == 200
    _record_player_run(db_engine, creation, activation_id=activation_id, actor_id="belvar")
    report = summary.build_resource_research_report(db_engine, creation.game_id)
    [item] = report["activations"]
    assert item["search_returned_read_conversions"] == 1
    assert [(read["resource_id"], read["rank"]) for read in item["read_ranks"]] == [
        (public_id, belvar_ids.index(public_id) + 1)
    ]


# --- TEST-I099-003: legacy rows, export version, CSV columns ----------------------------------


def test_i099_003_legacy_rows_migrate_additively_and_export_null(tmp_path) -> None:  # noqa: ANN001
    engine = engine_for_path(tmp_path / "pre-i099.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE resourceaccess (id INTEGER PRIMARY KEY, game_id CHAR(32) NOT NULL, "
            "actor_id VARCHAR NOT NULL, turn INTEGER NOT NULL, operation VARCHAR NOT NULL, "
            "query VARCHAR, activation_id VARCHAR)"
        )
        connection.exec_driver_sql(
            "INSERT INTO resourceaccess VALUES (1, '00000000000000000000000000000000', "
            "'arkania', 1, 'search', 'regional', NULL)"
        )
    initialize_database(engine)
    columns = {c["name"]: c for c in inspect(engine).get_columns("resourceaccess")}
    for name in ("result_count", "result_resource_ids", "result_set_sha256"):
        assert columns[name]["nullable"] is True, name
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT query, result_count, result_resource_ids, result_set_sha256 "
            "FROM resourceaccess WHERE id = 1"
        )).one()
    assert row == ("regional", None, None, None)


def test_i099_003_export_and_csv_carry_the_result_identity(db_engine: Engine) -> None:
    # The result identity arrived in format 13; later additive bumps (T-073: 14) keep it.
    assert int(export_module.EXPORT_FORMAT_VERSION) >= 13
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    assert _search(c, creation.game_id, token, "regional").status_code == 200
    assert _read(c, creation.game_id, token, "background").status_code == 200
    export = export_module.build_game_export(db_engine, creation.game_id)
    assert export["export_format_version"] == export_module.EXPORT_FORMAT_VERSION
    search_row, read_row = export["resource_access"]
    assert search_row["result_count"] == len(search_row["result_resource_ids"]) > 0
    assert search_row["result_set_sha256"] == _digest(search_row["result_resource_ids"])
    assert (read_row["result_count"], read_row["result_resource_ids"],
            read_row["result_set_sha256"]) == (None, None, None)

    # A legacy export row (pre-I-099, no keys) projects the fields as unavailable.
    legacy_export = {**export, "resource_access": [
        {k: v for k, v in search_row.items()
         if k not in ("result_count", "result_resource_ids", "result_set_sha256")},
    ]}
    columns = outputs._TABLE_COLUMNS["resource_access"]
    assert columns[-3:] == ["result_count", "result_resource_ids", "result_set_sha256"]
    current, _ = outputs._resource_access_rows(export)
    [legacy] = outputs._resource_access_rows(legacy_export)
    assert outputs._cell(current["result_resource_ids"]) == json.dumps(
        search_row["result_resource_ids"], sort_keys=True, allow_nan=False
    )
    assert outputs._cell(current["result_count"]) == search_row["result_count"]
    assert [outputs._cell(legacy[column]) for column in columns[-3:]] == ["", "", ""]


# --- TEST-I099-004: the metrics, against the definitions in the brief -------------------------


def _hand_rows(engine: Engine, creation, activation_id: str, sequence: list[tuple]) -> None:  # noqa: ANN001
    """Insert search/read rows directly: ``("search", query, ids | None)`` or ``("read", rid)``."""
    with session_scope(engine) as session:
        for entry in sequence:
            if entry[0] == "search":
                _, query, ids = entry
                session.add(ResourceAccess(
                    game_id=creation.game_id, actor_id="arkania", turn=1, operation="search",
                    query=query, activation_id=activation_id,
                    result_count=None if ids is None else len(ids),
                    result_resource_ids=ids,
                    result_set_sha256=None if ids is None else _digest(ids),
                ))
            else:
                _, resource_id = entry
                session.add(ResourceAccess(
                    game_id=creation.game_id, actor_id="arkania", turn=1, operation="read",
                    resource_id=resource_id, activation_id=activation_id,
                ))
        session.commit()


def test_i099_004_metrics_follow_the_definitions(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    activation_id = str(uuid.uuid4())
    _hand_rows(db_engine, creation, activation_id, [
        ("search", "A", ["r1", "r2"]),
        ("search", "B", ["r1", "r2"]),   # reformulation after an identical ordered set
        ("search", "C", []),             # zero-result
        ("read", "r2"),                  # returned by B (most recent list containing it): rank 2
        ("read", "r2"),                  # every qualifying read is one conversion observation
        ("search", "A", ["r1", "r2"]),   # exact repeat of an earlier query: not a reformulation
        ("search", "legacy", None),      # a pre-I-099 row: no metadata
    ])
    _record_player_run(db_engine, creation, activation_id=activation_id)

    report = summary.build_resource_research_report(db_engine, creation.game_id)
    [item] = report["activations"]
    assert item["searches"] == 5
    assert item["zero_result_searches"] == 1
    assert item["distinct_result_sets"] == 2
    assert item["reformulations_after_identical_set"] == 1
    assert item["search_returned_read_conversions"] == 2
    assert [(r["resource_id"], r["rank"]) for r in item["read_ranks"]] == [("r2", 2), ("r2", 2)]
    assert item["read_ranks"][0]["search_resource_access_id"] == item["read_ranks"][1]["search_resource_access_id"]
    assert item["searches_without_result_metadata"] == 1
    # The sequence-only I-045 metric is kept beside the new one, unchanged.
    assert item["search_to_read_conversions"] == 1
    totals = report["totals"]
    assert totals["zero_result_searches"] == 1
    assert totals["reformulations_after_identical_set"] == 1
    assert totals["search_returned_read_conversions"] == 2
    assert totals["searches_without_result_metadata"] == 1


def test_i099_004_a_read_not_returned_by_any_search_is_not_a_conversion(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    activation_id = str(uuid.uuid4())
    _hand_rows(db_engine, creation, activation_id, [
        ("search", "A", ["r1"]),
        ("read", "r9"),
        ("search", "A", ["r1"]),   # exact repeat, and same set as the preceding search
    ])
    _record_player_run(db_engine, creation, activation_id=activation_id)
    [item] = summary.build_resource_research_report(db_engine, creation.game_id)["activations"]
    assert item["search_returned_read_conversions"] == 0
    assert item["read_ranks"] == []
    assert item["reformulations_after_identical_set"] == 0
    assert item["search_to_read_conversions"] == 1  # sequence-only: a read followed A
