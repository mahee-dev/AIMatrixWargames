"""I-091 — an Actor's own phase-advancing action must not leave it with a stale context.

`_available_actions` (`app/context.py`) correctly lists `send_message` + `make_argument` for the
active Actor in the Argument phase. A successful `make_argument` moves the game to Cons *inside
the same activation* (api.md §8.1), but the tool result the model receives carries no `phase`
and no `available_actions` — so the only legality signal the agent has (agents.md §4) is now
stale by its own legal action, and its follow-ups are rejected `409 wrong_phase` / `403 forbidden`
and counted against it in the I-052 ledger (run 11: 13/19 `send_message` 409s, 5/6 `ready` 409s
and 5/3 `pass_con` 403s per game sat on exactly this path).

The agent did nothing the server had declared illegal; the harness let the declaration go stale.
The fix lives in `agents/` (D-077), never in `app/` (the server's declaration is correct) and never
in the prompt's tactical framing (T-040 neutrality).

Offline, no key: real :class:`pydantic_ai.Agent`s driven by deterministic :class:`FunctionModel`s
on the in-process server (`tests/api_helpers.py`).
"""

from __future__ import annotations

from typing import Any

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from sqlalchemy import Engine
from sqlmodel import select

from agents.lifecycle import LifecycleRunner
from agents.player import GuardrailConfig, PlayerConfig, ReferencePlayer
from agents.tools import PlayerTools
from app.database import session_scope
from app.models import Argument
from tests.agent_fakes import _response_index, extract_packet
from tests.api_helpers import client, seed, start_to_argument, start_to_cons, start_to_diplomacy

STALE_REJECTIONS = {"wrong_phase", "forbidden"}


def _runner(engine, creation, actor, *, model, guardrails=None) -> LifecycleRunner:
    tools = PlayerTools(
        client(engine), game_id=str(creation.game_id), token=creation.seat_tokens[actor]
    )
    name = tools.observe().data["you"]["name"]
    config = PlayerConfig(
        model="fake/deterministic", activation_mode="tool_loop",
        guardrails=guardrails or GuardrailConfig(),
    )
    player = ReferencePlayer(tools, actor_id=actor, actor_name=name, model=model, config=config)
    return LifecycleRunner(player, sleep=lambda: None)


def _tool_returns(messages: list[ModelMessage], tool_name: str) -> list[ToolReturnPart]:
    return [
        part
        for message in messages
        for part in getattr(message, "parts", [])
        if isinstance(part, ToolReturnPart) and part.tool_name == tool_name
    ]


def _stale_rejections(activation) -> list[dict[str, Any]]:
    """Server rejections the agent could not have foreseen from the context it was given."""
    return [
        event
        for event in activation.invalid_tool_call_breakdown.get("events", [])
        if event["reason"] == "server_rejection" and event["error_code"] in STALE_REJECTIONS
    ]


def _offered(info: AgentInfo, *calls: ToolCallPart) -> list[ToolCallPart]:
    """Only the calls whose tool is actually offered on this step — as a real model sees them."""
    names = {tool.name for tool in info.function_tools}
    return [call for call in calls if call.tool_name in names]


def _latest_declaration(messages: list[ModelMessage], packet: dict) -> list[str] | None:
    """The last `available_actions` the model was given: a tool result's `context`, else the packet."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, ToolReturnPart) and isinstance(part.content, dict):
                context = part.content.get("context")
                if isinstance(context, dict) and "available_actions" in context:
                    return list(context["available_actions"])
    return packet.get("available_actions")


def _make_argument_call(packet: dict) -> ToolCallPart:
    objective = (packet.get("you", {}).get("objectives") or ["hold position"])[0]
    return ToolCallPart(
        tool_name="make_argument",
        args={
            "action": f"Arkania acts to {objective}",
            "desired_effect": f"Progress toward {objective}",
            "pros": ["It follows from my objectives."],
        },
    )


def _follow_ups(argument_id: str) -> list[ToolCallPart]:
    """What run 11's models did after their own `make_argument`, in one response."""
    return [
        ToolCallPart(
            tool_name="send_message",
            args={"recipients": ["belvar"], "public": False, "text": "Stand with me."},
        ),
        ToolCallPart(tool_name="ready", args={}),
        ToolCallPart(tool_name="pass_con", args={"argument_id": argument_id}),
    ]


def _argument_then_keep_going(*, reads_results: bool) -> FunctionModel:
    """Submit the Argument, then keep using the surface — as run 11's models did.

    Call 1: `make_argument`. Call 2: a private Message (listed in the packet's
    `available_actions`), `ready`, and `pass_con` on its own Argument. With ``reads_results``
    the model respects the latest declaration it was *given* — the packet, or a tool result's
    `context` — and calls only what that declares; without it, it ignores its tool results and
    calls whatever the harness still offers. Either way it can only call offered tools, like a
    real model. Call 3: stop.
    """

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        packet = extract_packet(messages)
        idx = _response_index(messages)
        if idx == 0:
            return ModelResponse(parts=[_make_argument_call(packet)])
        if idx == 1:
            [submitted] = _tool_returns(messages, "make_argument")
            calls = _follow_ups(submitted.content["data"]["argument"]["id"])
            if reads_results:
                declared = _latest_declaration(messages, packet) or []
                calls = [c for c in calls if c.tool_name in declared]
            calls = _offered(info, *calls)
            if calls:
                return ModelResponse(parts=calls)
        return ModelResponse(parts=[TextPart(content="done")])

    return FunctionModel(run)


def test_i091_001_follow_ups_after_own_argument_are_not_stale_context_rejections(
    db_engine: Engine,
) -> None:
    """TEST-I091-001 — the run 11 regression on the Argument-phase path.

    Before D-077 this model was charged three server rejections it could not have foreseen:
    `send_message` (declared legal, silently retracted) and `ready`/`pass_con` (inferred from
    a result that said only `open_for_cons`). Now the result says what is legal and the
    retracted declaration is gone.
    """
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)  # arkania active, `send_message` + `make_argument` legal
    runner = _runner(
        db_engine, creation, "arkania", model=_argument_then_keep_going(reads_results=True)
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    assert outcome.activation is not None
    # The Argument was committed exactly once and the game moved on — the agent's action stood.
    with session_scope(db_engine) as session:
        arguers = session.exec(
            select(Argument.actor_id).where(Argument.game_id == creation.game_id)
        ).all()
    assert arguers == ["arkania"]
    assert runner.observe()["phase"] == "cons"
    # … and none of its follow-ups was charged as a server rejection it could not have foreseen.
    assert _stale_rejections(outcome.activation) == []
    assert outcome.activation.tool_call_names == ["make_argument"]


def test_i091_002_retracted_declared_tool_is_withdrawn_even_from_a_model_ignoring_results(
    db_engine: Engine,
) -> None:
    """TEST-I091-002 — the mechanical half of D-077.

    `send_message` was in the packet's `available_actions`; the Actor's own `make_argument`
    retracted it. A model that never reads its tool results cannot call it any more — it is
    withdrawn from the offered surface, not merely reported. The never-declared `ready` and
    `pass_con` are *not* hidden (TEST-I091-006 pins that boundary).
    """
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(
        db_engine, creation, "arkania", model=_argument_then_keep_going(reads_results=False)
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    assert outcome.activation is not None
    assert "send_message" not in outcome.activation.tool_call_names
    assert not [
        e for e in _stale_rejections(outcome.activation) if e["action"] == "send_message"
    ]


def _memory(label: str) -> dict:
    return {
        "commitments_made": [f"commitment {label}"],
        "commitments_or_offers_received": [],
        "outstanding_offers_and_negotiations": [],
        "red_lines_and_constraints": [],
        "uncertain_or_disputed_claims": [],
        "relationships_and_likely_coalitions": [],
        "strategic_assessment": f"assessment {label}",
        "intended_next_action": f"next {label}",
    }


def _scripted(*steps: list[ToolCallPart]) -> FunctionModel:
    """One response per step, each filtered to the tools actually offered; then stop."""

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        idx = _response_index(messages)
        if idx < len(steps):
            calls = _offered(info, *steps[idx])
            if calls:
                return ModelResponse(parts=calls)
        return ModelResponse(parts=[TextPart(content="done")])

    return FunctionModel(run)


def test_i091_003_diplomacy_ready_withdraws_only_ready_and_keeps_legal_messaging(
    db_engine: Engine,
) -> None:
    """TEST-I091-003 — the diplomacy twin (the I-025 shape), and the "only what was retracted" rule.

    After the Actor's own `ready` the server no longer lists `ready` but still lists
    `send_message` (legal in diplomacy). The harness withdraws exactly `ready`; the Message
    goes through.
    """
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)  # nobody ready: arkania's `ready` closes nothing
    message = ToolCallPart(
        tool_name="send_message",
        args={"recipients": ["belvar"], "public": False, "text": "After my ready."},
    )
    runner = _runner(
        db_engine, creation, "arkania",
        model=_scripted(
            [ToolCallPart(tool_name="ready", args={})],
            [ToolCallPart(tool_name="ready", args={}), message],
        ),
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    assert outcome.activation is not None
    assert outcome.activation.tool_call_names == ["ready", "send_message"]
    assert _stale_rejections(outcome.activation) == []
    assert outcome.activation.messages_sent == 1
    assert runner.observe()["phase"] == "diplomacy"


def test_i091_004_cons_pass_withdraws_the_retracted_con_tools(db_engine: Engine) -> None:
    """TEST-I091-004 — the Cons twin: `pass_con` retracts `make_con` and `pass_con` alike."""
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)  # arkania's Argument open; belvar eligible
    argument_id = str(argument.id)
    runner = _runner(
        db_engine, creation, "belvar",
        model=_scripted(
            [ToolCallPart(tool_name="pass_con", args={"argument_id": argument_id})],
            [
                ToolCallPart(
                    tool_name="make_con", args={"argument_id": argument_id, "reason": "Too late."}
                ),
                ToolCallPart(tool_name="pass_con", args={"argument_id": argument_id}),
            ],
        ),
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    assert outcome.activation is not None
    assert outcome.activation.tool_call_names == ["pass_con"]
    assert _stale_rejections(outcome.activation) == []
    context = runner.observe()
    assert context["requires_input"] is False
    assert "make_con" not in context["available_actions"]


def test_i091_005_memory_staged_after_the_completion_action_still_commits(
    db_engine: Engine,
) -> None:
    """TEST-I091-005 — withdrawing retracted tools must not cost the model its memory (I-034)."""
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(
        db_engine, creation, "arkania",
        model=_scripted(
            [ToolCallPart(tool_name="make_argument", args=_make_argument_call({}).args)],
            [ToolCallPart(tool_name="update_strategic_memory", args=_memory("after"))],
        ),
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    assert outcome.activation.tool_call_names == ["make_argument", "update_strategic_memory"]
    delivery = runner.player._tools.begin_activation().data
    assert delivery["strategic_memory"] == _memory("after")


def test_i091_006_never_declared_tools_are_not_hidden_so_i092_stays_measurable(
    db_engine: Engine,
) -> None:
    """TEST-I091-006 — the boundary D-077 draws.

    A tool the packet never declared is left exactly as it was, before *and* after the Actor's
    own mutation: a Cons-phase `send_message` is still offered, still tried by a model that
    ignores declared legality, and still a `409 wrong_phase` in the ledger. That is the I-092
    measurement; this task does not touch it.
    """
    creation = seed(db_engine)
    argument = start_to_cons(db_engine, creation)
    argument_id = str(argument.id)
    message = ToolCallPart(
        tool_name="send_message",
        args={"recipients": ["arkania"], "public": False, "text": "Not legal in Cons."},
    )
    runner = _runner(
        db_engine, creation, "belvar",
        model=_scripted(
            [message],
            [ToolCallPart(tool_name="pass_con", args={"argument_id": argument_id})],
            [message],
        ),
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    assert outcome.activation.tool_call_names == ["send_message", "pass_con", "send_message"]
    rejections = [
        (e["action"], e["error_code"], e["http_status"])
        for e in outcome.activation.invalid_tool_call_breakdown["events"]
    ]
    assert rejections == [
        ("send_message", "wrong_phase", 409),
        ("send_message", "wrong_phase", 409),
    ]


def test_i091_007_successful_mutation_result_carries_the_observe_fragment(
    db_engine: Engine,
) -> None:
    """TEST-I091-007 — the informational half of D-077: the result says what is legal now.

    The fragment is the agents.md §6 `observe` shape and is the *server's* declaration
    (API-INV-009) — it equals what `observe` returns immediately afterwards.
    """
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    runner = _runner(
        db_engine, creation, "arkania",
        model=_scripted([ToolCallPart(tool_name="make_argument", args=_make_argument_call({}).args)]),
    )

    outcome = runner.step()

    assert outcome.failure is None, outcome.failure
    [submitted] = _tool_returns(outcome.activation.messages, "make_argument")
    fragment = submitted.content["context"]
    assert set(fragment) == {
        "turn", "phase", "active_actor_id", "requires_input", "available_actions"
    }
    observed = runner.observe()
    assert fragment == {key: observed[key] for key in fragment}
    assert fragment["phase"] == "cons"
    assert fragment["requires_input"] is False
    assert "send_message" not in fragment["available_actions"]
    # The pre-existing result shape is untouched (agents.md §11).
    assert submitted.content["ok"] is True
    assert submitted.content["data"]["argument"]["status"] == "open_for_cons"


def test_i091_008_the_player_condition_moved_and_single_shot_is_untouched(
    db_engine: Engine,
) -> None:
    """TEST-I091-008 — a model-facing surface change is a new sealed condition (§5.1, D-077)."""
    from agents.player import (
        CON_REPLY_MODE_VISIBLE,
        RESEARCH_MODE_BATCHED_V1,
        tool_schema_version_for,
    )

    versions = {
        tool_schema_version_for(PlayerConfig(model="m")),
        tool_schema_version_for(PlayerConfig(model="m", research_mode=RESEARCH_MODE_BATCHED_V1)),
        tool_schema_version_for(PlayerConfig(model="m", con_reply_mode=CON_REPLY_MODE_VISIBLE)),
        tool_schema_version_for(PlayerConfig(
            model="m", research_mode=RESEARCH_MODE_BATCHED_V1, con_reply_mode=CON_REPLY_MODE_VISIBLE
        )),
    }
    assert len(versions) == 4
    assert all("-live-" in v for v in versions)
    assert not any("player-tools-v7-" in v or "player-tools-v10-" in v for v in versions)

    # single_shot registers no mutating tools: its surface has no prepare hook to change.
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    tools = PlayerTools(
        client(db_engine), game_id=str(creation.game_id), token=creation.seat_tokens["arkania"]
    )
    shot = ReferencePlayer(
        tools, actor_id="arkania", actor_name="Arkania",
        model=_scripted(),
        config=PlayerConfig(model="fake", activation_mode="single_shot"),
    )
    assert not (shot.tool_names & {"send_message", "make_argument", "ready", "pass_con"})
