"""Client-side player guardrails (T-041, agents.md §18).

The bounds that keep one autonomous player safe to run unattended, and the checks that enforce
the "also enforce" list (message length, Argument text length, Pros count, Note length, tool
payload size) *before* an HTTP call is made. A violation is returned as a structured error the
model can correct within the same activation — shaped exactly like a failed tool payload so it
flows on the same channel as a server rejection — never raised.

Shared by both activation modes of :mod:`agents.player` (the tool loop checks each tool call;
the single-shot harness checks the one structured decision — I-031), which is why they live in
their own module rather than inside the player.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from amaw_contracts import STRATEGIC_MEMORY_MAX_BYTES, canonical_json_size

@dataclass(frozen=True)
class GuardrailConfig:
    """Bounds that keep one autonomous player safe to run unattended (agents.md §18).

    The four headline ceilings carry the agents.md §18 recommended defaults. The remaining
    text/size limits are the "also enforce" list (message length, Argument text length, Pros
    count, Note length, tool payload size); they are validated *before* the HTTP call and a
    violation is returned to the model as a structured tool error it can correct within the same
    activation — not raised. ``max_activations_per_opportunity`` is the no-progress stall bound
    the runner uses (the converted T-040 ``max_activations`` seam).
    """

    max_llm_iterations: int = 12  # agents.md §18 MAX_LLM_ITERATIONS
    max_tool_calls: int = 20  # agents.md §18 MAX_TOOL_CALLS
    # I-069: optional research is a strict subset of the total tool budget. Four calls allow two
    # normal search→read chains (or a Message read plus a chain and one spare) while keeping
    # provider round-trips bounded and reserving capacity for the required decision.
    max_research_tool_calls: int = 4
    # I-098 / D-078: the Resource *operations* an activation may cause, whatever tool calls carry
    # them — every dispatched search/read, batched or not, is one. The same released default as
    # the invocation ceiling (the number 4, not a reference to it), so sequential research is
    # effectively unchanged while batched research loses its per-call multiplier.
    max_research_operations: int = 4
    # I-033 normal behavioural bound. Unlike the phase ceiling below, this resets for each
    # Actor activation (but not for an internal model retry of that activation).
    max_messages_per_activation: int = 3
    max_messages_per_phase: int = 10  # agents.md §18 MAX_MESSAGES_PER_PHASE
    max_activations_per_opportunity: int = 6  # no-progress stall bound (runner)
    max_message_chars: int = 4000
    max_argument_action_chars: int = 2000
    max_desired_effect_chars: int = 2000
    max_pros: int = 5
    max_pro_chars: int = 1000
    max_note_chars: int = 4000
    max_tool_payload_bytes: int = 16384

    def __post_init__(self) -> None:
        # The same bounds `app.config.Settings` applies (I-077): a ceiling of zero would only
        # fail later, loudly, as an immediate UsageLimitExceeded or a stall.
        for name in ("max_llm_iterations", "max_tool_calls", "max_activations_per_opportunity"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1")
        for name in (
            "max_messages_per_activation", "max_research_tool_calls", "max_research_operations",
            "max_messages_per_phase",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be at least 0")


def guardrail_error(code: str, message: str, **details: Any) -> dict[str, Any]:
    """A structured tool error for a client-side guardrail violation (agents.md §18).

    Shaped exactly like a failed tool payload so it flows to the model on the *same* channel
    as a server rejection — the model can shorten its Note, drop a Pro, and retry within the same
    activation. ``http_status`` is ``None`` because no request was made.
    """
    return {
        "ok": False,
        "http_status": None,
        "error": {"code": code, "message": message, "details": details or None},
    }


def check_message(text: str, g: GuardrailConfig) -> dict[str, Any] | None:
    """Reject an over-long Message before it is sent (agents.md §18 "message length")."""
    if len(text) > g.max_message_chars:
        return guardrail_error(
            "guardrail_message_too_long",
            f"message is {len(text)} chars; limit is {g.max_message_chars}",
            field="text",
            limit=g.max_message_chars,
            actual=len(text),
        )
    return None


def check_argument(
    action: str, desired_effect: str, pros: list[str], g: GuardrailConfig
) -> dict[str, Any] | None:
    """Reject an over-long/over-wide Argument before it is submitted (agents.md §18)."""
    if len(action) > g.max_argument_action_chars:
        return guardrail_error(
            "guardrail_argument_action_too_long",
            f"action is {len(action)} chars; limit is {g.max_argument_action_chars}",
            field="action",
            limit=g.max_argument_action_chars,
            actual=len(action),
        )
    if len(desired_effect) > g.max_desired_effect_chars:
        return guardrail_error(
            "guardrail_desired_effect_too_long",
            f"desired_effect is {len(desired_effect)} chars; limit is {g.max_desired_effect_chars}",
            field="desired_effect",
            limit=g.max_desired_effect_chars,
            actual=len(desired_effect),
        )
    if len(pros) > g.max_pros:
        return guardrail_error(
            "guardrail_too_many_pros",
            f"{len(pros)} Pros supplied; limit is {g.max_pros}",
            field="pros",
            limit=g.max_pros,
            actual=len(pros),
        )
    for i, pro in enumerate(pros):
        if len(pro) > g.max_pro_chars:
            return guardrail_error(
                "guardrail_pro_too_long",
                f"Pro #{i} is {len(pro)} chars; limit is {g.max_pro_chars}",
                field="pros",
                index=i,
                limit=g.max_pro_chars,
                actual=len(pro),
            )
    return None


def check_note(text: str, g: GuardrailConfig) -> dict[str, Any] | None:
    """Reject an over-long Note before it is saved (agents.md §18 "Note length")."""
    if len(text) > g.max_note_chars:
        return guardrail_error(
            "guardrail_note_too_long",
            f"note is {len(text)} chars; limit is {g.max_note_chars}",
            field="text",
            limit=g.max_note_chars,
            actual=len(text),
        )
    return None


def check_strategic_memory(
    memory: dict[str, Any], g: GuardrailConfig
) -> dict[str, Any] | None:
    """Apply per-entry and shared whole-memory bounds before accepting replacement state."""
    texts = [
        item
        for value in memory.values()
        for item in (value if isinstance(value, list) else [value])
    ]
    violation = next(
        (problem for text in texts if (problem := check_note(text, g)) is not None),
        None,
    )
    if violation is not None:
        return violation
    size = canonical_json_size(memory)
    limit = min(g.max_tool_payload_bytes, STRATEGIC_MEMORY_MAX_BYTES)
    if size > limit:
        return guardrail_error(
            "guardrail_strategic_memory_too_large",
            f"strategic_memory is {size} serialized bytes; limit is {limit}",
            field="strategic_memory",
            limit=limit,
            actual=size,
        )
    return None


def check_payload(args: dict[str, Any], g: GuardrailConfig) -> dict[str, Any] | None:
    """Reject an over-large tool payload before it is sent (agents.md §18 "tool payload size")."""
    size = len(json.dumps(args, default=str).encode("utf-8"))
    if size > g.max_tool_payload_bytes:
        return guardrail_error(
            "guardrail_payload_too_large",
            f"tool payload is {size} bytes; limit is {g.max_tool_payload_bytes}",
            limit=g.max_tool_payload_bytes,
            actual=size,
        )
    return None
