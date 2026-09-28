"""Offline fake PydanticAI models for the T-039/T-040 reference-player tests.

The default test suite runs with no OpenRouter key (testing.md §3). These helpers drive a real
:class:`pydantic_ai.Agent` with a deterministic in-process model, so the reference player and
lifecycle are exercised end-to-end without a provider.

A fake model is a :class:`pydantic_ai.models.function.FunctionModel` whose policy reads the
**decision packet** the player renders into the activation prompt and branches purely on the
server-provided ``available_actions`` — never on a phase literal handed to it externally. That is
exactly the property T-040 must prove: the agent participates correctly across phases without
being told which phase it is in.
"""

from __future__ import annotations

import json
from typing import Any, Callable

from pydantic_ai.messages import (
    ModelMessage,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserPromptPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel

from agents.player import ACTIVATION_PACKET_MARKER

Policy = Callable[[dict, list[str], int], list[Any]]


def extract_packet(messages: list[ModelMessage]) -> dict:
    """Parse the JSON decision packet the player embedded in the latest user prompt."""
    for message in reversed(messages):
        for part in getattr(message, "parts", []):
            if isinstance(part, UserPromptPart) and isinstance(part.content, str):
                if ACTIVATION_PACKET_MARKER in part.content:
                    payload = part.content.split(ACTIVATION_PACKET_MARKER, 1)[1].strip()
                    return json.loads(payload)
    return {}


def _response_index(messages: list[ModelMessage]) -> int:
    """How many model responses already exist in *this* run — the per-activation call index.

    Computed from the message list, so it resets naturally for every fresh activation (nothing
    is carried across ``run_sync`` calls) — the ephemeral-history property under test.
    """
    return sum(1 for m in messages if isinstance(m, ModelResponse))


def model_from_policy(policy: Policy) -> FunctionModel:
    """Wrap a ``(packet, tool_names, call_index) -> parts`` policy as a FunctionModel."""

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        packet = extract_packet(messages)
        tool_names = [t.name for t in info.function_tools]
        parts = policy(packet, tool_names, _response_index(messages))
        return ModelResponse(parts=list(parts))

    return FunctionModel(run)


def default_policy(packet: dict, tool_names: list[str], idx: int) -> list[Any]:
    """A neutral, phase-agnostic policy driven only by ``available_actions``.

    It never inspects ``packet["phase"]``: whatever the server currently permits, it does the
    minimal legal thing and then finishes. This is the fake that proves an agent can play every
    phase without an external "now do X" instruction.
    """
    actions = packet.get("available_actions", [])
    you = packet.get("you", {})
    current = packet.get("current_argument") or {}

    # ARGUMENT: submit one structurally valid primary Argument grounded in an objective.
    if "make_argument" in actions and idx == 0:
        objective = (you.get("objectives") or ["advance my position"])[0]
        return [
            ToolCallPart(
                tool_name="make_argument",
                args={
                    "action": f"{you.get('name', 'We')} act to {objective}",
                    "desired_effect": f"Establish progress toward: {objective}",
                    "pros": ["It follows directly from my objectives."],
                },
            )
        ]

    # CONS: one substantive Con, then explicitly pass (never rely on inactivity).
    if "make_con" in actions and idx == 0:
        return [
            ToolCallPart(
                tool_name="make_con",
                args={
                    "argument_id": current.get("id"),
                    "reason": "The desired effect is not assured under current conditions.",
                },
            )
        ]
    if "pass_con" in actions and idx <= 1:
        return [ToolCallPart(tool_name="pass_con", args={"argument_id": current.get("id")})]

    # DIPLOMACY: one public statement, then ready.
    if "ready" in actions:
        if idx == 0:
            return [
                ToolCallPart(
                    tool_name="send_message",
                    args={"recipients": [], "public": True, "text": "Opening statement."},
                )
            ]
        if idx == 1:
            return [ToolCallPart(tool_name="ready", args={})]

    return [TextPart(content="Nothing further this activation.")]


def pass_only_policy(packet: dict, tool_names: list[str], idx: int) -> list[Any]:
    """A CONS policy that passes without inventing a Con (agents.md §13)."""
    actions = packet.get("available_actions", [])
    current = packet.get("current_argument") or {}
    if "pass_con" in actions and idx == 0:
        return [ToolCallPart(tool_name="pass_con", args={"argument_id": current.get("id")})]
    return [TextPart(content="done")]


# --- single-shot structured decisions (I-031) -------------------------------------------------

DecisionPolicy = Callable[[dict, int, AgentInfo], list[Any]]


def decision_call(info: AgentInfo, decision: dict) -> ToolCallPart:
    """The one output-tool call a single-shot activation ends with (``agents.decisions``)."""
    return ToolCallPart(tool_name=info.output_tools[0].name, args=decision)


def current_memory(packet: dict) -> dict:
    """Carry the server-backed rolling memory through a minimal fake decision."""
    return dict(packet.get("strategic_memory") or {
        "commitments_made": [],
        "commitments_or_offers_received": [],
        "outstanding_offers_and_negotiations": [],
        "red_lines_and_constraints": [],
        "uncertain_or_disputed_claims": [],
        "relationships_and_likely_coalitions": [],
        "strategic_assessment": "",
        "intended_next_action": "",
    })


def model_from_decision_policy(policy: DecisionPolicy) -> FunctionModel:
    """Wrap a ``(packet, call_index, info) -> parts`` policy as a FunctionModel.

    Unlike :func:`model_from_policy` the policy receives ``info`` so it can name the output tool
    PydanticAI registered for this run's decision schema (and see which research tools exist).
    """

    def run(messages: list[ModelMessage], info: AgentInfo) -> ModelResponse:
        packet = extract_packet(messages)
        return ModelResponse(parts=list(policy(packet, _response_index(messages), info)))

    return FunctionModel(run)


def default_decision_policy(packet: dict, idx: int, info: AgentInfo) -> list[Any]:
    """The single-shot twin of :func:`default_policy`: one decision, chosen from ``available_actions``.

    Argument → one primary Argument grounded in an objective; Cons → one substantive Con;
    diplomacy → one public statement and ``ready``. Never inspects ``packet["phase"]``.
    """
    actions = packet.get("available_actions", [])
    you = packet.get("you", {})
    if "make_argument" in actions:
        objective = (you.get("objectives") or ["advance my position"])[0]
        return [decision_call(info, {
            "messages": [],
            "strategic_memory": current_memory(packet),
            "argument": {
                "action": f"{you.get('name', 'We')} act to {objective}",
                "desired_effect": f"Establish progress toward: {objective}",
                "pros": ["It follows directly from my objectives."],
            },
        })]
    if "pass_con" in actions:
        cons = (
            ["The desired effect is not assured under current conditions."]
            if "make_con" in actions else []
        )
        return [decision_call(info, {
            "cons": cons, "strategic_memory": current_memory(packet)
        })]
    if "ready" in actions:
        return [decision_call(info, {
            "messages": [{"recipients": [], "public": True, "text": "Opening statement."}],
            "strategic_memory": current_memory(packet),
            "ready": True,
        })]
    return [TextPart(content="Nothing to decide.")]
