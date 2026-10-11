"""I-031: single-shot structured phase decisions with a research-tool fallback.

In ``single_shot`` mode the reference player asks the model for **one structured decision per
activation** (messages + note + ready; action/desired_effect/pros; con/pass) instead of paying a
provider round-trip per mutating tool call. The read tools (``read_messages``,
``search_resources``, ``read_resource``) stay callable so the model can research first when it
needs information it does not have. The decision is validated against the agents.md §18 guardrails
inside the model run (a violation is a structured retry) and then executed by Python against the
server in a fixed order — messages → note → completion — so the server, not the model, still
decides legality (agents.md §3/§11/§13/§16).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.decisions import (
    ArgumentDecision,
    ConsDecision,
    DiplomacyDecision,
    select_decision_type,
    validate_decision,
)
from app.activation_context import EMPTY_STRATEGIC_MEMORY
from app.export import EXPORT_FORMAT_VERSION
from agents.guardrails import GuardrailConfig, guardrail_error

# --- decision selection ---------------------------------------------------------------------


def test_decision_type_follows_available_actions() -> None:
    """The schema offered to the model is derived from the server's legality declaration alone."""
    common = ["get_notes", "save_note", "search_resources", "read_resource"]
    assert select_decision_type([*common, "send_message", "ready"]) is DiplomacyDecision
    assert select_decision_type([*common, "send_message", "make_argument"]) is ArgumentDecision
    assert select_decision_type([*common, "pass_con", "make_con"]) is ConsDecision
    assert select_decision_type([*common, "pass_con"]) is ConsDecision  # Con budget exhausted
    # Nothing to complete (a waiting Actor; a diplomacy Actor already ready): no decision type.
    assert select_decision_type([*common, "send_message"]) is None
    assert select_decision_type(common) is None
    assert select_decision_type([]) is None


# --- double-encoded object fields (#31) ------------------------------------------------------


def test_decision_accepts_a_double_encoded_strategic_memory() -> None:
    """A nested object sent as a JSON string validates to the object form (#31).

    deepseek-v4.1-flash sends ``strategic_memory`` as ``"{\\"commitments_made\\": [...]}"`` and
    repeats it after the retry prompt; the content is exactly what the schema asks for.
    """
    memory = _memory(commitments_made=["Endorse the charter if the text is circulated."])
    as_object = DiplomacyDecision.model_validate(
        {"messages": [_msg()], "strategic_memory": memory, "ready": True}
    )
    as_string = DiplomacyDecision.model_validate(
        {"messages": [_msg()], "strategic_memory": json.dumps(memory), "ready": True}
    )
    assert as_string == as_object


def test_decision_accepts_double_encoded_nested_objects_at_every_depth() -> None:
    """The decode applies to every nested decision object, not only ``strategic_memory`` (#31)."""
    argument = {"action": "Act.", "desired_effect": "Effect.", "pros": ["p"]}
    as_object = ArgumentDecision.model_validate(
        {"messages": [_msg()], "strategic_memory": _memory(), "argument": argument}
    )
    as_string = ArgumentDecision.model_validate({
        "messages": [json.dumps(_msg())],
        "strategic_memory": json.dumps(_memory()),
        "argument": json.dumps(argument),
    })
    assert as_string == as_object


@pytest.mark.parametrize(
    "value",
    ["not json at all", json.dumps(["a", "list"]), json.dumps("a string"), "{broken json"],
)
def test_decision_still_rejects_strings_that_are_not_an_encoded_object(value: str) -> None:
    """Only a string that parses to a JSON object is decoded; anything else fails as before (#31)."""
    with pytest.raises(ValidationError) as excinfo:
        DiplomacyDecision.model_validate({"messages": [], "strategic_memory": value, "ready": True})
    assert excinfo.value.errors()[0]["loc"] == ("strategic_memory",)


def test_decision_still_rejects_an_encoded_object_with_wrong_fields() -> None:
    """Decoding does not relax the schema: the decoded object is validated strictly (#31)."""
    with pytest.raises(ValidationError):
        DiplomacyDecision.model_validate({
            "messages": [], "ready": True,
            "strategic_memory": json.dumps({**_memory(), "extra_field": "x"}),
        })


# --- validation reuses the guardrails --------------------------------------------------------

G = GuardrailConfig(max_message_chars=20, max_note_chars=10, max_pros=2, max_pro_chars=8)


def _msg(text: str = "hi") -> dict:
    return {"recipients": [], "public": True, "text": text}


def _memory(**changes) -> dict:
    value = {key: list(item) if isinstance(item, list) else item
             for key, item in EMPTY_STRATEGIC_MEMORY.items()}
    value.update(changes)
    return value


def test_validate_decision_accepts_a_compliant_decision() -> None:
    decision = DiplomacyDecision(messages=[_msg()], strategic_memory=_memory(), ready=True)
    assert validate_decision(
        decision, guardrails=G, message_budget=3, con_allowed=False, con_slots_remaining=None
    ) is None


def test_validate_decision_rejects_over_long_message_with_guardrail_shape() -> None:
    decision = DiplomacyDecision(messages=[_msg("x" * 21)], strategic_memory=_memory(), ready=False)
    error = validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=False, con_slots_remaining=None
    )
    assert error == guardrail_error(
        "guardrail_message_too_long",
        "message is 21 chars; limit is 20",
        field="text",
        limit=20,
        actual=21,
    )


def test_validate_decision_enforces_the_runners_message_budget() -> None:
    decision = DiplomacyDecision(messages=[_msg(), _msg("two")], strategic_memory=_memory(), ready=True)
    error = validate_decision(
        decision, guardrails=G, message_budget=1, con_allowed=False, con_slots_remaining=None
    )
    assert error is not None
    assert error["ok"] is False
    assert error["error"]["code"] == "guardrail_message_cap_reached"
    assert error["error"]["details"] == {"limit": 1, "actual": 2}
    # An unbounded direct use (no runner budget) does not invent a cap.
    assert validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=False, con_slots_remaining=None
    ) is None


def test_validate_decision_rejects_over_long_strategic_memory_entry() -> None:
    decision = ConsDecision(
        cons=[], strategic_memory=_memory(strategic_assessment="x" * 11)
    )
    error = validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=True, con_slots_remaining=0
    )
    assert error is not None and error["error"]["code"] == "guardrail_note_too_long"


def test_validate_decision_rejects_argument_over_pros_limit() -> None:
    decision = ArgumentDecision(
        messages=[],
        strategic_memory=_memory(),
        argument={"action": "act", "desired_effect": "effect", "pros": ["a", "b", "c"]},
    )
    error = validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=False, con_slots_remaining=None
    )
    assert error is not None and error["error"]["code"] == "guardrail_too_many_pros"


def test_validate_decision_rejects_a_con_when_make_con_is_not_available() -> None:
    """The Con budget is exhausted: only a pass is legal, so a Con is refused before any request."""
    decision = ConsDecision(cons=["it will fail"], strategic_memory=_memory())
    error = validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=False, con_slots_remaining=None
    )
    assert error is not None and error["error"]["code"] == "guardrail_con_not_available"
    assert validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=True, con_slots_remaining=1
    ) is None


def test_validate_decision_rejects_over_long_con_reason() -> None:
    decision = ConsDecision(cons=["x" * 21], strategic_memory=_memory())
    error = validate_decision(
        decision, guardrails=G, message_budget=None, con_allowed=True, con_slots_remaining=1
    )
    assert error is not None and error["error"]["code"] == "guardrail_message_too_long"


# --- execution against a live server -----------------------------------------------------------

import json

from sqlalchemy import Engine
from sqlmodel import select

from agents.decisions import execute_decision
from agents.tools import PlayerTools
from app import game_service
from app.database import session_scope
from app.models import Con, ConParticipation, Game, GamePhase, Message
from tests.api_helpers import FIXTURE_DIR, REPO_ROOT, client as api_client, seed, start_to_argument, start_to_cons
from tests.benchmark_helpers import SHORT_TURNS


def _key(operation: str, payload: dict) -> str:
    return f"k-{operation}-{json.dumps(payload, sort_keys=True)}"


def _tools(engine: Engine, creation, actor: str) -> PlayerTools:
    return PlayerTools(api_client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor])


def test_execute_diplomacy_sends_messages_then_ready(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    decision = DiplomacyDecision(
        messages=[
            {"recipients": [], "public": True, "text": "Opening statement."},
            {"recipients": ["belvar"], "public": False, "text": "Quietly: shall we align?"},
        ],
        strategic_memory=_memory(strategic_assessment="Belvar approached."),
        ready=True,
    )
    report = execute_decision(
        decision, _tools(db_engine, creation, "arkania"), argument_id=None, key_fn=_key
    )

    assert report.actions == ["send_message", "send_message", "ready"]
    assert report.rejected == []
    assert report.messages_sent == 2
    with session_scope(db_engine) as session:
        texts = [m.text for m in session.exec(select(Message)).all()]
        assert texts == ["Opening statement.", "Quietly: shall we align?"]
        game = session.get(Game, creation.game_id)
        assert "arkania" in game.diplomacy_ready_actor_ids


def test_execute_diplomacy_ready_false_leaves_the_opportunity_open(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    report = execute_decision(
        DiplomacyDecision(messages=[], strategic_memory=_memory(), ready=False),
        _tools(db_engine, creation, "arkania"), argument_id=None, key_fn=_key,
    )
    assert report.actions == []
    with session_scope(db_engine) as session:
        assert session.get(Game, creation.game_id).diplomacy_ready_actor_ids == []


def test_execute_argument_submits_the_primary_argument_after_messages(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania active
    decision = ArgumentDecision(
        messages=[{"recipients": [], "public": True, "text": "We act now."}],
        strategic_memory=_memory(),
        argument={"action": "Mobilise", "desired_effect": "Reserves deploy", "pros": ["Plans exist"]},
    )
    report = execute_decision(
        decision, _tools(db_engine, creation, "arkania"), argument_id=None, key_fn=_key
    )
    assert report.actions == ["send_message", "make_argument"]
    assert report.rejected == []
    with session_scope(db_engine) as session:
        assert session.get(Game, creation.game_id).phase is GamePhase.cons


def test_execute_cons_empty_list_passes(db_engine: Engine) -> None:
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    report = execute_decision(
        ConsDecision(
            cons=[], strategic_memory=_memory(strategic_assessment="Nothing to object to.")
        ),
        _tools(db_engine, creation, "belvar"), argument_id=str(argument.id), key_fn=_key,
    )
    assert report.actions == ["pass_con"]
    assert report.rejected == []
    with session_scope(db_engine) as session:
        assert session.exec(select(Con)).all() == []
        assert [p.actor_id for p in session.exec(select(ConParticipation)).all()] == ["belvar"]


def test_execute_cons_submits_each_con_then_passes_while_budget_remains(db_engine: Engine) -> None:
    creation = seed(db_engine)  # fixture: max_cons_per_actor = 3
    argument = start_to_cons(db_engine, creation)
    report = execute_decision(
        ConsDecision(
            cons=["Rail is disrupted.", "Reserves are untrained."], strategic_memory=_memory()
        ),
        _tools(db_engine, creation, "belvar"), argument_id=str(argument.id), key_fn=_key,
    )
    assert report.actions == ["make_con", "make_con", "pass_con"]
    assert report.rejected == []
    with session_scope(db_engine) as session:
        assert [c.reason for c in session.exec(select(Con)).all()] == [
            "Rail is disrupted.", "Reserves are untrained."
        ]
        assert [p.actor_id for p in session.exec(select(ConParticipation)).all()] == ["belvar"]


def test_execute_cons_does_not_pass_after_the_budget_exhausting_con(db_engine: Engine) -> None:
    """I-030/D-043: the final permitted Con completes participation; no redundant pass_con."""
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    report = execute_decision(
        ConsDecision(cons=["one", "two", "three"], strategic_memory=_memory()),
        _tools(db_engine, creation, "belvar"), argument_id=str(argument.id), key_fn=_key,
    )
    assert report.actions == ["make_con", "make_con", "make_con"]
    assert report.rejected == []


def test_execute_records_server_rejections_instead_of_raising(db_engine: Engine) -> None:
    """A wrong-phase completion is refused by the server; execution records it and returns."""
    creation = seed(db_engine)
    start_to_cons(db_engine, creation)  # Cons: send_message and ready are illegal
    report = execute_decision(
        DiplomacyDecision(
            messages=[{"recipients": [], "public": True, "text": "late"}],
            strategic_memory=_memory(), ready=True
        ),
        _tools(db_engine, creation, "cestia"), argument_id=None, key_fn=_key,
    )
    assert report.actions == ["send_message", "ready"]
    assert report.messages_sent == 0
    assert [r["action"] for r in report.rejected] == ["send_message", "ready"]
    assert all(r["http_status"] == 409 for r in report.rejected)
    assert all(isinstance(r["code"], str) for r in report.rejected)


def test_execute_uses_the_supplied_idempotency_keys(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    decision = DiplomacyDecision(
        messages=[{"recipients": [], "public": True, "text": "once"}],
        strategic_memory=_memory(), ready=False
    )
    tools = _tools(db_engine, creation, "arkania")
    first = execute_decision(decision, tools, argument_id=None, key_fn=_key)
    second = execute_decision(decision, tools, argument_id=None, key_fn=_key)
    assert first.messages_sent == 1
    assert second.rejected == []  # an exact replay is accepted idempotently (api.md §4)
    with session_scope(db_engine) as session:
        assert len(session.exec(select(Message)).all()) == 1


# --- the single-shot reference player ----------------------------------------------------------

import hashlib

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo

from agents.autonomous import AutonomousGameError, AutonomousGameRunner
from agents.lifecycle import LifecycleRunner
from agents.player import (
    PLAYER_PROMPT_VERSION,
    PLAYER_PROMPT_VERSIONS,
    PlayerConfig,
    ReferencePlayer,
    load_player_prompt,
)
from agents.scripted import deterministic_umpire
from app.api import create_app
from app.config import Settings
from tests.agent_fakes import (
    decision_call,
    default_decision_policy,
    default_policy,
    model_from_decision_policy,
    model_from_policy,
)

ADMIN_TOKEN = "admin-secret-token"
ACTORS = ("arkania", "belvar", "cestia")
RESEARCH_TOOLS = {"read_messages", "search_resources", "read_resource"}
ALL_TOOLS = RESEARCH_TOOLS | {
    "observe", "send_message", "make_argument", "make_con", "pass_con",
    "update_strategic_memory", "ready",
}


def test_player_config_defaults_to_single_shot_and_its_prompt_version() -> None:
    config = PlayerConfig(model="fake/deterministic")
    assert config.activation_mode == "single_shot"
    assert config.prompt_version == PLAYER_PROMPT_VERSIONS["single_shot"] == "player-v1.11"
    legacy = PlayerConfig(model="fake/deterministic", activation_mode="tool_loop")
    assert legacy.prompt_version == PLAYER_PROMPT_VERSION == "player-v1.10"
    with pytest.raises(ValueError):
        PlayerConfig(model="fake/deterministic", activation_mode="oneshot")


def _player(engine: Engine, creation, actor: str, model, **config) -> ReferencePlayer:
    tools = _tools(engine, creation, actor)
    return ReferencePlayer(
        tools,
        actor_id=actor,
        actor_name=tools.observe().data["you"]["name"],
        model=model,
        config=PlayerConfig(model="fake/deterministic", **config),
    )


def test_single_shot_player_offers_only_research_tools_and_hashes_its_own_prompt(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    single = _player(db_engine, creation, "arkania", model_from_decision_policy(default_decision_policy))
    legacy = _player(
        db_engine, creation, "belvar", model_from_policy(default_policy), activation_mode="tool_loop"
    )
    assert single.tool_names == frozenset(RESEARCH_TOOLS)
    assert legacy.tool_names == frozenset(ALL_TOOLS)
    v111 = (REPO_ROOT / "docs/prompts/player-v1.11.md").read_text(encoding="utf-8")
    assert single.prompt_hash == hashlib.sha256(v111.encode()).hexdigest()
    assert single.prompt_hash != legacy.prompt_hash
    assert "{actor_name}" not in single.prompt and single.actor_name in single.prompt
    assert "structured `decide`" in v111
    assert "update_strategic_memory" in load_player_prompt("X", version="player-v1.10")


def test_single_shot_activation_is_one_model_call_and_executes_the_decision(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    player = _player(db_engine, creation, "arkania", model_from_decision_policy(default_decision_policy))

    result = player.activate()

    assert result.model_iterations == 1
    assert result.tool_call_names == ["send_message", "ready"]
    assert result.messages_sent == 1
    assert result.invalid_tool_calls == 0
    assert result.decision == {
        "messages": [{"recipients": [], "public": True, "text": "Opening statement."}],
        "strategic_memory": _memory(),
        "ready": True,
    }
    context = player.tools.observe().data
    assert "ready" not in context["available_actions"]
    assert [m["text"] for m in player.tools.read_messages().data["messages"]] == ["Opening statement."]


def test_single_shot_prompt_framing_asks_for_one_decision_without_a_phase_imperative(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    player = _player(db_engine, creation, "arkania", model_from_decision_policy(default_decision_policy))
    prompt = player.build_activation_prompt(player.build_packet())
    framing = prompt.split("=== DECISION CONTEXT (JSON) ===")[0]
    assert "`decide`" in framing
    assert "research" in framing.lower()
    assert "with `ready`" not in framing  # the tool-loop completion sentence is not used here
    assert "diplomacy" not in framing.lower()  # phase enters only as data in the packet


def test_i069_001_activation_packet_discloses_all_model_and_research_ceilings(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    guardrails = GuardrailConfig(
        max_llm_iterations=9,
        max_tool_calls=13,
        max_research_tool_calls=4,
    )
    player = _player(
        db_engine,
        creation,
        "arkania",
        model_from_decision_policy(default_decision_policy),
        guardrails=guardrails,
    )

    limits = player.build_packet()["limits"]

    assert limits["max_llm_iterations"] == 9
    assert limits["max_tool_calls"] == 13
    assert limits["max_research_tool_calls"] == 4


def test_i069_002_research_tools_withdraw_before_reserved_decision_capacity(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    seen_tools: list[set[str]] = []

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        tools = {tool.name for tool in info.function_tools}
        seen_tools.append(tools)
        if "search_resources" in tools:
            return [
                ToolCallPart(
                    tool_name="search_resources",
                    args={"query": f"useful topic {idx}"},
                )
            ]
        return [decision_call(info, {
            "messages": [],
            "strategic_memory": _memory(),
            "ready": True,
        })]

    player = _player(
        db_engine,
        creation,
        "arkania",
        model_from_decision_policy(policy),
        guardrails=GuardrailConfig(
            max_llm_iterations=8,
            max_tool_calls=10,
            max_research_tool_calls=2,
        ),
    )

    result = player.activate()

    assert result.model_iterations == 3
    assert result.tool_call_names == ["search_resources", "search_resources", "ready"]
    assert "search_resources" in seen_tools[0]
    assert "search_resources" in seen_tools[1]
    assert not (RESEARCH_TOOLS & seen_tools[2])


def test_i069_003_exact_research_repeat_closes_research_surface(
    db_engine: Engine,
) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    seen_tools: list[set[str]] = []

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        tools = {tool.name for tool in info.function_tools}
        seen_tools.append(tools)
        if "search_resources" in tools:
            return [ToolCallPart(tool_name="search_resources", args={"query": "same topic"})]
        return [decision_call(info, {
            "messages": [],
            "strategic_memory": _memory(),
            "ready": True,
        })]

    player = _player(
        db_engine,
        creation,
        "arkania",
        model_from_decision_policy(policy),
        guardrails=GuardrailConfig(
            max_llm_iterations=8,
            max_tool_calls=10,
            max_research_tool_calls=6,
        ),
    )

    result = player.activate()

    assert result.model_iterations == 3
    assert result.tool_call_names == ["search_resources", "search_resources", "ready"]
    assert not (RESEARCH_TOOLS & seen_tools[2])
    duplicate_returns = [
        part
        for message in result.messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart)
        and part.tool_name == "search_resources"
        and "guardrail_research_duplicate" in str(part.content)
    ]
    assert len(duplicate_returns) == 1


def test_single_shot_guardrail_breach_is_one_structured_retry(db_engine: Engine) -> None:
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        text = "x" * 50 if idx == 0 else "short"
        return [decision_call(info, {
            "messages": [{"recipients": [], "public": True, "text": text}],
            "strategic_memory": _memory(), "ready": True,
        })]

    player = _player(
        db_engine, creation, "arkania", model_from_decision_policy(policy),
        guardrails=GuardrailConfig(max_message_chars=20),
    )
    result = player.activate()

    assert result.model_iterations == 2
    assert result.invalid_tool_calls == 1
    retries = [
        part for message in result.messages for part in getattr(message, "parts", [])
        if isinstance(part, RetryPromptPart)
    ]
    assert len(retries) == 1
    assert "guardrail_message_too_long" in str(retries[0].content)
    assert result.tool_call_names == ["send_message", "ready"]
    assert [m["text"] for m in player.tools.read_messages().data["messages"]] == ["short"]


def test_single_shot_double_encoded_memory_is_one_call_without_a_retry(db_engine: Engine) -> None:
    """The captured v4.1-flash shape completes in one model call, with no invalid call (#31)."""
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    memory = _memory(strategic_assessment="Hold.")

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        return [decision_call(info, {
            "messages": [{"recipients": [], "public": True, "text": "Opening statement."}],
            "strategic_memory": json.dumps(memory), "ready": True,
        })]

    player = _player(db_engine, creation, "arkania", model_from_decision_policy(policy))
    result = player.activate()

    assert result.model_iterations == 1
    assert result.invalid_tool_calls == 0
    assert result.tool_call_names == ["send_message", "ready"]
    assert result.decision["strategic_memory"] == memory


def test_single_shot_research_then_decide_uses_retrieved_fact(db_engine: Engine) -> None:
    """The hybrid: read tools first when information is needed, then the one decision."""
    from tests.test_scenario_knowledge import ARKANIA_SECRET_CODEWORD, _advance_to_argument

    creation = _advance_to_argument(db_engine)

    def fact_from_read(messages) -> str:
        for message in reversed(messages):
            for part in getattr(message, "parts", []):
                if isinstance(part, ToolReturnPart) and part.tool_name == "read_resource":
                    text = part.content.get("data", {}).get("resource", {}).get("content", "")
                    for sentence in text.replace("\n", " ").split("."):
                        if ARKANIA_SECRET_CODEWORD in sentence:
                            return sentence.strip()
        return ""

    seen: dict[str, list] = {}

    def run(messages, info: AgentInfo) -> ModelResponse:
        idx = sum(1 for m in messages if isinstance(m, ModelResponse))
        seen["tools"] = sorted(t.name for t in info.function_tools)
        if idx == 0:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="search_resources", args={"query": "intelligence"})
            ])
        if idx == 1:
            return ModelResponse(parts=[
                ToolCallPart(tool_name="read_resource", args={"resource_id": "arkania-intel"})
            ])
        return ModelResponse(parts=[decision_call(info, {
            "messages": [],
            "strategic_memory": _memory(
                strategic_assessment=f"Fuel window: {fact_from_read(messages)}"
            ),
            "argument": {
                "action": "Press Belvar to settle transit terms before its fuel window closes.",
                "desired_effect": "Belvar accepts an Arkanian-led transit regime.",
                "pros": [f"Intelligence I retrieved: {fact_from_read(messages)}"],
            },
        })])

    from pydantic_ai.models.function import FunctionModel

    tools = PlayerTools(
        TestClient(create_app(engine=db_engine), raise_server_exceptions=False),
        game_id=str(creation.game_id),
        token=creation.seat_tokens["arkania"],
    )
    player = ReferencePlayer(
        tools, actor_id="arkania", actor_name=tools.observe().data["you"]["name"],
        model=FunctionModel(run), config=PlayerConfig(model="fake/deterministic"),
    )
    result = player.activate()

    assert seen["tools"] == sorted(RESEARCH_TOOLS)
    assert result.model_iterations == 3
    assert result.tool_call_names == ["search_resources", "read_resource", "make_argument"]
    current = tools.observe().data["current_argument"]
    assert ARKANIA_SECRET_CODEWORD in " ".join(current["pros"])
    assert ARKANIA_SECRET_CODEWORD in tools.begin_activation().data[
        "strategic_memory"
    ]["strategic_assessment"]


def test_single_shot_records_server_rejections_as_invalid_calls(db_engine: Engine) -> None:
    """A decision the server refuses is recorded, never raised; the opportunity stays open."""
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        return [decision_call(info, {
            "messages": [{"recipients": ["nobody"], "public": False, "text": "hello?"}],
            "strategic_memory": _memory(),
            "ready": False,
        })]

    player = _player(db_engine, creation, "arkania", model_from_decision_policy(policy))
    result = player.activate()
    assert result.model_iterations == 1
    assert result.tool_call_names == ["send_message"]
    assert result.invalid_tool_calls == 1
    assert result.messages_sent == 0
    assert "ready" in player.tools.observe().data["available_actions"]


def _single_shot_factory(policy=default_decision_policy):
    def factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        return LifecycleRunner(ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name,
            model=model_from_decision_policy(policy),
            config=PlayerConfig(model="fake/deterministic"),
        ))
    return factory


def _autonomous_client(db_engine: Engine) -> TestClient:
    settings = Settings(AMAW_ADMIN_TOKEN=ADMIN_TOKEN, _env_file=None)
    return TestClient(
        create_app(engine=db_engine, settings=settings, umpire=deterministic_umpire()),
        raise_server_exceptions=False,
    )


def test_single_shot_game_completes_with_one_model_call_per_activation(
    db_engine: Engine, short_game: Path
) -> None:
    runner = AutonomousGameRunner(
        _autonomous_client(db_engine), admin_token=ADMIN_TOKEN, scenario=short_game,
        player_factory=_single_shot_factory(), seed=7,
    )
    result = runner.run()

    assert result.export["game"]["status"] == "complete"
    assert result.failures == []
    arguments = 3 * SHORT_TURNS
    assert len(result.export["arguments"]) == arguments
    assert all(trace.model_iterations == 1 for trace in result.activations)
    # One activation per opportunity: every Con opportunity is exactly one activation.
    cons_traces = [t for t in result.activations if t.phase == "cons"]
    assert len(cons_traces) == arguments * 2
    assert all(t.tool_call_names in (["make_con", "pass_con"], ["make_con"]) for t in cons_traces)
    assert result.summary["player_model_calls"] == len(result.activations)
    manifest = result.export["run_manifest"]
    assert manifest["player"]["activation_mode"] == "single_shot"
    assert manifest["player"]["prompt_version"] == "player-v1.11"
    assert manifest["player"]["tool_schema_version"] == "player-tools-v11-live-bounded-research"
    assert manifest["player"]["context_mode"] == "incremental-context-v1"
    assert manifest["player"]["memory_version"] == "strategic-memory-v1"
    assert result.export["export_format_version"] == EXPORT_FORMAT_VERSION
    assert {"messages", "notes", "strategic_memories", "run_manifest"} <= set(result.export)
    assert len(result.export["strategic_memories"]) == 3
    assert {run["context_mode"] for run in result.export["agent_runs"] if run["role"] == "player"} == {
        "incremental-context-v1"
    }
    assert {run["memory_version"] for run in result.export["agent_runs"] if run["role"] == "player"} == {
        "strategic-memory-v1"
    }
    assert {run["model_parameters"]["activation_mode"] for run in result.export["agent_runs"]} == {
        "single_shot"
    }


def test_single_shot_ready_false_keeps_diplomacy_open_until_a_later_activation(
    db_engine: Engine, short_game: Path,
) -> None:
    calls: dict[tuple, int] = {}

    def policy(packet: dict, idx: int, info: AgentInfo) -> list:
        actions = packet.get("available_actions", [])
        if "ready" in actions:
            me = (packet["you"]["actor_id"], packet["turn"])
            calls[me] = calls.get(me, 0) + 1
            return [decision_call(info, {
                "messages": [], "strategic_memory": _memory(), "ready": calls[me] >= 2
            })]
        return default_decision_policy(packet, idx, info)

    runner = AutonomousGameRunner(
        _autonomous_client(db_engine), admin_token=ADMIN_TOKEN, scenario=short_game,
        player_factory=_single_shot_factory(policy), seed=7,
    )
    result = runner.run()
    assert result.export["game"]["status"] == "complete"
    assert result.failures == []
    diplomacy = [t for t in result.activations if t.phase == "diplomacy"]
    # Per turn, three Actors × two activations each (the first said ready=false).
    assert len(diplomacy) == SHORT_TURNS * 3 * 2


def test_single_shot_failure_recovery_still_takes_the_legal_completion(db_engine: Engine) -> None:
    def broken(packet: dict, idx: int, info: AgentInfo) -> list:
        raise RuntimeError("model down")

    runner = AutonomousGameRunner(
        _autonomous_client(db_engine), admin_token=ADMIN_TOKEN, scenario=FIXTURE_DIR,
        player_factory=_single_shot_factory(broken), seed=7, max_cycles=50,
    )
    with pytest.raises(AutonomousGameError):
        runner.run()  # the arguer can never be recovered by fabrication (agents.md §2)
    export = runner._admin_request("GET", f"/admin/games/{runner.last_game_id}/export")
    recoveries = {(r["phase"], r["failure_reason"] is not None) for r in export["agent_runs"]}
    assert ("diplomacy", True) in recoveries  # diplomacy failures were recorded and auto-readied
    assert export["game"]["phase"] == "argument"  # and the game reached the unrecoverable arguer


def test_mixed_activation_modes_in_one_game_are_refused(db_engine: Engine) -> None:
    modes = iter(["single_shot", "tool_loop", "single_shot"])

    def factory(tools: PlayerTools, actor_id: str, actor_name: str) -> LifecycleRunner:
        mode = next(modes)
        model = (
            model_from_decision_policy(default_decision_policy) if mode == "single_shot"
            else model_from_policy(default_policy)
        )
        return LifecycleRunner(ReferencePlayer(
            tools, actor_id=actor_id, actor_name=actor_name, model=model,
            config=PlayerConfig(model="fake/deterministic", activation_mode=mode),
        ))

    runner = AutonomousGameRunner(
        _autonomous_client(db_engine), admin_token=ADMIN_TOKEN, scenario=FIXTURE_DIR,
        player_factory=factory,
    )
    # I-083: refused through the one shared-condition projection, which names the fields.
    with pytest.raises(AutonomousGameError, match="activation_mode.*prompt_version"):
        runner.run()


# --- empty responses exclude their downstream provider (#32) ---------------------------------


def _routing_model(script: list) -> tuple:
    """A FunctionModel that plays ``script`` in order and records each request's routing.

    A script entry is either the string provider name of an empty, reasoning-only response or a
    callable ``info -> parts`` for a usable one.
    """
    from pydantic_ai.messages import ThinkingPart
    from pydantic_ai.models.function import FunctionModel

    seen: list[list[str]] = []
    calls = iter(script)

    def run(messages, info: AgentInfo) -> ModelResponse:  # noqa: ANN001
        provider = (info.model_settings or {}).get("openrouter_provider") or {}
        seen.append(list(provider.get("ignore") or []))
        step = next(calls)
        if isinstance(step, str):
            return ModelResponse(
                parts=[ThinkingPart(content="Let me make the call.")],
                provider_details={"downstream_provider": step, "finish_reason": "stop"},
            )
        return ModelResponse(parts=step(info), provider_details={"downstream_provider": "GoodCo"})

    return FunctionModel(run), seen


def _decide(info: AgentInfo) -> list:
    return [decision_call(info, {
        "messages": [{"recipients": [], "public": True, "text": "Opening statement."}],
        "strategic_memory": _memory(), "ready": True,
    })]


def test_an_empty_response_excludes_its_provider_from_the_next_request(db_engine: Engine) -> None:
    """A reasoning-only response is a provider failure: the output retry is routed elsewhere (#32).

    deepseek-v4.1-flash served by one provider ended its turn with reasoning and no tool call;
    the model sees the same retry prompt and budget, only the routing changes (as D-061 does for
    the Umpire).
    """
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    model, seen = _routing_model(["BadCo", _decide])
    player = _player(db_engine, creation, "arkania", model)

    result = player.activate()

    assert result.tool_call_names == ["send_message", "ready"]
    assert seen == [[], ["BadCo"]]
    requests = result.usage["request_usage"]
    assert "excluded_providers" not in requests[0]
    assert requests[1]["excluded_providers"] == ["BadCo"]


def test_provider_exclusion_spans_the_activation_retry_and_accumulates(db_engine: Engine) -> None:
    """Two empty answers exhaust the output retry; the activation retry keeps both exclusions."""
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    model, seen = _routing_model(["BadCo", "OtherCo", _decide])
    player = _player(db_engine, creation, "arkania", model)

    result = player.activate()

    assert result.tool_call_names == ["send_message", "ready"]
    assert seen == [[], ["BadCo"], ["BadCo", "OtherCo"]]


def test_a_new_activation_starts_with_no_exclusions(db_engine: Engine) -> None:
    """Exclusions last for one activation; a later activation routes normally again."""
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    def stay_open(info: AgentInfo) -> list:
        return [decision_call(info, {"messages": [], "strategic_memory": _memory(), "ready": False})]

    model, seen = _routing_model(["BadCo", stay_open, _decide])
    player = _player(db_engine, creation, "arkania", model)

    player.activate()
    seen.clear()
    player.activate()

    assert seen == [[]]


def test_a_usable_response_or_an_unnamed_provider_excludes_nothing(db_engine: Engine) -> None:
    """Only a named downstream provider is excluded, never the gateway's own label."""
    creation = seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    model, seen = _routing_model(["openrouter", _decide])
    player = _player(db_engine, creation, "arkania", model)

    player.activate()

    assert seen == [[], []]
