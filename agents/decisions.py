"""Structured single-shot phase decisions (I-031, agents.md §3/§11/§13/§16/§18).

In ``single_shot`` activation mode the model returns **one** decision per activation instead of
the tool loop's expected one mutating tool per provider round-trip (which the tool loop does not
enforce, D-082). This module owns the three decision schemas,
how the schema for an activation is chosen, how a decision is validated against the agents.md §18
guardrails, and how a validated decision is executed against the server through the ordinary
:class:`~agents.tools.PlayerTools` HTTP client.

Three rules keep the server authoritative and the harness neutral:

- **The schema follows ``available_actions``.** :func:`select_decision_type` reads only the
  server's legality declaration — never a phase literal — so the T-040 property (an agent plays
  every phase without being told which phase it is in) holds for this harness too.
- **Validation happens before any request, execution after the model run.** A guardrail breach
  is returned in exactly the :func:`~agents.guardrails.guardrail_error` shape the tool loop uses,
  so the model's retry message is the same in both modes. Server rejections during execution are
  *recorded*, not raised: an unfinished opportunity simply re-activates under the runner's stall
  bound (agents.md §18 step 3), and every rejection is provenance.
- **Execution order is fixed:** messages → completion, followed by the player's successful
  activation acknowledgement and memory replacement. Identity is never a field
  (INV-ACTOR-001): the Seat token behind ``PlayerTools`` is the Actor.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agents.guardrails import (
    GuardrailConfig,
    check_argument,
    check_message,
    check_payload,
    check_strategic_memory,
    guardrail_error,
)
from agents.tools import PlayerTools, ToolResult

# The single output tool name the model sees in every phase.
DECISION_TOOL_NAME = "decide"

_ACTION_MAKE_ARGUMENT = "make_argument"
_ACTION_MAKE_CON = "make_con"
_ACTION_PASS_CON = "pass_con"
_ACTION_REPLY_TO_CONS = "reply_to_cons"  # I-046, visible_reply only
_ACTION_READY = "ready"


class _Strict(BaseModel):
    """Base for every decision object: extra fields are refused.

    A decision object sent as a string containing a JSON object is decoded before validation,
    then validated as strictly as the object form. Some models double-encode nested objects in
    tool arguments and repeat it after the retry prompt; the content is what the schema asks for,
    and rejecting it would measure a model's JSON escaping, not its play (#31, D-124). Any other
    string still fails validation unchanged.
    """

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _decode_json_object_string(cls, data: Any) -> Any:
        if isinstance(data, str):
            try:
                decoded = json.loads(data)
            except ValueError:
                return data
            if isinstance(decoded, dict):
                return decoded
        return data


class OutgoingMessage(_Strict):
    """One Message to send (agents.md §8): public to all, or private to named recipients."""

    recipients: list[str] = Field(
        description="Actor ids to address privately. Empty with public=true for a public Message."
    )
    public: bool = Field(description="true = visible to every Actor; false = private to recipients.")
    text: str = Field(description="The Message text, in your Actor's own words.")


class ArgumentBody(_Strict):
    """A Matrix Argument (agents.md §11): action, desired Effect, Pros."""

    action: str = Field(description="What your Actor does.")
    desired_effect: str = Field(description="The consequential change you want established.")
    pros: list[str] = Field(description="Concise reasons the Effect should occur.")


class StrategicMemory(_Strict):
    """The complete replace-in-place private memory carried across activations (D-048)."""

    commitments_made: list[str]
    commitments_or_offers_received: list[str]
    outstanding_offers_and_negotiations: list[str]
    red_lines_and_constraints: list[str]
    uncertain_or_disputed_claims: list[str]
    relationships_and_likely_coalitions: list[str]
    strategic_assessment: str
    intended_next_action: str


class DiplomacyDecision(_Strict):
    """Diplomacy: optional Messages, replacement memory, and opportunity completion."""

    messages: list[OutgoingMessage] = Field(
        default_factory=list, description="Messages to send now, in order. May be empty."
    )
    strategic_memory: StrategicMemory
    ready: bool = Field(
        description=(
            "true = nothing further this diplomacy opportunity (calls ready). false = keep the "
            "opportunity open; you will be activated again and see any replies."
        )
    )


class ArgumentDecision(_Strict):
    """Argument: optional Messages, replacement memory, and the primary Matrix Argument."""

    messages: list[OutgoingMessage] = Field(
        default_factory=list, description="Messages to send before arguing. May be empty."
    )
    strategic_memory: StrategicMemory
    argument: ArgumentBody = Field(description="Your primary Matrix Argument for this turn.")


class ConsDecision(_Strict):
    """Cons: zero or more Cons against the current Argument; an empty list is a pass."""

    cons: list[str] = Field(
        default_factory=list,
        description=(
            "Substantive reasons the current Argument's desired Effect may fail or be limited, "
            "one per Con, up to the remaining Con capacity in limits.con_slots_remaining. "
            "Empty = pass without a Con."
        ),
    )
    strategic_memory: StrategicMemory


class ReplyPoint(_Strict):
    """One answer to one revealed Con (agents.md §13.1)."""

    con_id: str = Field(description="The con_id of the revealed Con this point answers.")
    text: str = Field(description="Your answer to that Con. It cannot change your Argument.")


class ReplyDecision(_Strict):
    """Reply (I-046, ``visible_reply`` only): the owner's single answer to the revealed Cons.

    ``points=[]`` is the explicit pass. At most ``limits.reply_max_points`` points, each naming a
    distinct ``con_id`` from ``current_argument.cons``; validated before any request is made.
    """

    points: list[ReplyPoint] = Field(
        default_factory=list,
        description=(
            "Answers to specific revealed Cons, at most limits.reply_max_points, each con_id "
            "at most once. Empty = pass without a reply."
        ),
    )
    strategic_memory: StrategicMemory


Decision = DiplomacyDecision | ArgumentDecision | ConsDecision | ReplyDecision


def select_decision_type(available_actions: Iterable[str]) -> type[Decision] | None:
    """The decision schema for this activation, from the server's ``available_actions`` alone.

    The completion action the server currently lists picks the schema; when none is listed the
    Actor has nothing to complete and no decision is asked for (the runner never activates such an
    Actor anyway — ``requires_input`` gates the model, agents.md §3).
    """
    actions = set(available_actions)
    if _ACTION_MAKE_ARGUMENT in actions:
        return ArgumentDecision
    if _ACTION_PASS_CON in actions:
        return ConsDecision
    if _ACTION_REPLY_TO_CONS in actions:
        return ReplyDecision
    if _ACTION_READY in actions:
        return DiplomacyDecision
    return None


def validate_decision(
    decision: Decision,
    *,
    guardrails: GuardrailConfig,
    message_budget: int | None,
    con_allowed: bool,
    con_slots_remaining: int | None,
    reply_max_points: int | None = None,
    reply_con_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Check one decision against the agents.md §18 guardrails; ``None`` when compliant.

    Reuses the tool loop's checks so both modes refuse the same things with the same error
    shape. ``message_budget`` is the runner's remaining per-phase Message allowance (``None`` =
    unbounded direct use, which claims no cap). ``con_allowed`` is whether ``make_con`` is in
    ``available_actions``: the server tracks the per-Actor Con limit, and once it is exhausted
    only a pass is legal. ``con_slots_remaining`` must be supplied whenever ``con_allowed`` is
    true; it is the server-computed unused capacity and bounds the whole decision before
    execution, so a single-shot decision cannot issue a predictable sequence of rejected Con
    requests. ``None`` is valid only when ``make_con`` is unavailable. ``reply_max_points`` and
    ``reply_con_ids`` (I-046) are the server-stated reply bound and the revealed ``con_id``s;
    both must be supplied for a :class:`ReplyDecision` so cardinality, references and
    duplicates are refused before any request.
    """
    g = guardrails
    if con_allowed and con_slots_remaining is None:
        return guardrail_error(
            "guardrail_con_capacity_missing",
            "make_con is available but remaining Con capacity was not supplied",
        )
    messages = getattr(decision, "messages", [])
    if message_budget is not None and len(messages) > message_budget:
        return guardrail_error(
            "guardrail_message_cap_reached",
            f"{len(messages)} Messages decided; only {message_budget} remain this phase",
            limit=message_budget,
            actual=len(messages),
        )
    for message in messages:
        args = message.model_dump()
        violation = check_message(message.text, g) or check_payload(args, g)
        if violation is not None:
            return violation
    memory_payload = decision.strategic_memory.model_dump()
    violation = check_strategic_memory(memory_payload, g)
    if violation is not None:
        return violation
    if isinstance(decision, ArgumentDecision):
        body = decision.argument
        violation = check_argument(
            body.action, body.desired_effect, list(body.pros), g
        ) or check_payload(body.model_dump(), g)
        if violation is not None:
            return violation
    if isinstance(decision, ConsDecision):
        if decision.cons and not con_allowed:
            return guardrail_error(
                "guardrail_con_not_available",
                "make_con is not currently available to you; decide cons=[] to pass",
                actual=len(decision.cons),
            )
        if (
            con_slots_remaining is not None
            and len(decision.cons) > con_slots_remaining
        ):
            return guardrail_error(
                "guardrail_con_capacity_exceeded",
                f"{len(decision.cons)} Cons decided; only {con_slots_remaining} remain",
                limit=con_slots_remaining,
                actual=len(decision.cons),
            )
        for reason in decision.cons:
            violation = check_message(reason, g) or check_payload({"reason": reason}, g)
            if violation is not None:
                return violation
    if isinstance(decision, ReplyDecision):
        if reply_max_points is None or reply_con_ids is None:
            return guardrail_error(
                "guardrail_reply_bounds_missing",
                "reply_to_cons is available but the reply bound or revealed Cons were not supplied",
            )
        if len(decision.points) > reply_max_points:
            return guardrail_error(
                "guardrail_reply_points_exceeded",
                f"{len(decision.points)} reply points decided; at most {reply_max_points} allowed",
                limit=reply_max_points,
                actual=len(decision.points),
            )
        seen: set[str] = set()
        for point in decision.points:
            if point.con_id not in reply_con_ids:
                return guardrail_error(
                    "guardrail_reply_unknown_con",
                    f"reply point names {point.con_id!r}, which is not a revealed Con",
                )
            if point.con_id in seen:
                return guardrail_error(
                    "guardrail_reply_duplicate_con",
                    f"reply point names Con {point.con_id!r} more than once",
                )
            seen.add(point.con_id)
            violation = check_message(point.text, g) or check_payload(point.model_dump(), g)
            if violation is not None:
                return violation
    return None


@dataclass
class ExecutionReport:
    """What executing one decision did against the server (provenance, agents.md §19).

    ``actions`` are the tool names invoked in execution order (successful or not); ``rejected``
    holds the server's structured error for every refused call; ``messages_sent`` counts Messages
    the server accepted (for the runner's per-phase cap accounting).
    """

    actions: list[str] = field(default_factory=list)
    rejected: list[dict[str, Any]] = field(default_factory=list)
    messages_sent: int = 0

    def record(self, name: str, result: ToolResult) -> bool:
        self.actions.append(name)
        if not result.ok:
            self.rejected.append(
                {
                    "action": name,
                    "http_status": result.http_status,
                    "code": result.error_code,
                    "message": result.error_message,
                    "details": result.error_details,
                }
            )
        return result.ok


KeyFn = Callable[[str, dict[str, Any]], str]


def execute_decision(
    decision: Decision,
    tools: PlayerTools,
    *,
    argument_id: str | None,
    key_fn: KeyFn,
) -> ExecutionReport:
    """Carry out a validated decision through the HTTP client: messages → note → completion.

    Completion: ``make_argument``; each Con, then ``pass_con`` only if the server still lists it
    (a final Con that exhausts the budget completes participation by itself — I-030/D-043);
    ``ready`` only when ``ready`` is true. ``key_fn(operation, payload)`` supplies the player's
    per-opportunity idempotency keys (api.md §4). Nothing here raises on a server rejection.
    """
    report = ExecutionReport()
    for message in getattr(decision, "messages", []):
        payload = message.model_dump()
        result = tools.send_message(
            recipients=list(message.recipients),
            public=message.public,
            text=message.text,
            idempotency_key=key_fn("send_message", payload),
        )
        if report.record("send_message", result):
            report.messages_sent += 1
    if isinstance(decision, ArgumentDecision):
        payload = decision.argument.model_dump()
        report.record(
            "make_argument",
            tools.make_argument(
                action=payload["action"],
                desired_effect=payload["desired_effect"],
                pros=list(payload["pros"]),
                idempotency_key=key_fn("make_argument", payload),
            ),
        )
    elif isinstance(decision, ConsDecision):
        _execute_cons(decision, tools, report, argument_id=argument_id, key_fn=key_fn)
    elif isinstance(decision, ReplyDecision):
        if argument_id is None:
            report.rejected.append(
                {"action": "reply_to_cons", "code": "no_current_argument", "message": "no Argument"}
            )
        else:
            points = [point.model_dump() for point in decision.points]
            payload = {"argument_id": argument_id, "points": points}
            report.record(
                "reply_to_cons",
                tools.reply_to_cons(
                    argument_id=argument_id, points=points,
                    idempotency_key=key_fn("reply_to_cons", payload),
                ),
            )
    elif isinstance(decision, DiplomacyDecision) and decision.ready:
        report.record("ready", tools.ready(idempotency_key=key_fn("ready", {})))
    return report


def _execute_cons(
    decision: ConsDecision,
    tools: PlayerTools,
    report: ExecutionReport,
    *,
    argument_id: str | None,
    key_fn: KeyFn,
) -> None:
    if argument_id is None:
        report.rejected.append(
            {"action": "make_con", "code": "no_current_argument", "message": "no Argument"}
        )
        return
    accepted = 0
    for reason in decision.cons:
        payload = {"argument_id": argument_id, "reason": reason}
        accepted += report.record(
            "make_con",
            tools.make_con(
                argument_id=argument_id, reason=reason,
                idempotency_key=key_fn("make_con", payload),
            ),
        )
    if decision.cons and not accepted:
        # I-079 / agents.md §18: the model decided to challenge and none of it executed. A pass
        # would be a materially different decision the harness never fabricates; the
        # opportunity stays open for the lifecycle's bounded reactivation and stall handling.
        return
    pass_required = True
    if decision.cons:
        # The final permitted Con completes participation on the server (I-030/D-043); pass only
        # if the server still lists it as legal. One authoritative read decides, not a guess.
        observed = tools.observe()
        actions = (observed.data or {}).get("available_actions", []) if observed.ok else []
        pass_required = _ACTION_PASS_CON in actions
    if pass_required:
        payload = {"argument_id": argument_id}
        report.record(
            "pass_con",
            tools.pass_con(argument_id=argument_id, idempotency_key=key_fn("pass_con", payload)),
        )
