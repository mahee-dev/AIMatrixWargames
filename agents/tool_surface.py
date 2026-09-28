"""The reference player's tool surface: which tools the model sees, and how each call is admitted.

One :class:`ToolSurface` belongs to one :class:`~agents.player.ReferencePlayer`. It owns the two
policies that decide what a model call can do before a tool body runs, together with the
per-activation state behind them and the one lock that guards it:

- **Exposure.** A mutating tool follows the server's declaration: under ``available_only`` only
  the declared actions are offered, and under ``broad`` a declared tool the server has since
  retracted is withdrawn (D-077, D-079). Research tools are offered only while optional
  capacity remains, and their descriptions state that capacity (I-069, D-078). Nothing here
  infers legality; the server names the actions (api.md §8.1, API-INV-009).
- **Admission and accounting.** Every call is counted, even when the activation later fails.
  A research call is reserved before it runs, so two identical sibling calls execute at most
  once (I-077); a repeat of a successful call closes research (D-062); a call over the research
  budget is refused without dispatch. A Resource operation is charged before dispatch (D-078).

The tool bodies themselves stay in the player: PydanticAI derives each tool's name, description
and JSON schema from its function, so they are registered here unchanged through
:meth:`ToolSurface.tracked`. No tool reaches the game except through ``PlayerTools``.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Callable, Iterable
from dataclasses import replace
from functools import wraps
from typing import Any

from agents.guardrails import GuardrailConfig, guardrail_error

# The player-safe baseline tools (agents.md §5). The resource tools (`search_resources`/
# `read_resource`) are wired in as of T-049 so information-seeking is the agent's own decision;
# admin/export/umpire/engine controls are never a player's to hold (agents.md §2 "must not ...
# directly modify game phase"). In single-shot mode (I-031) only the *read* tools below are
# exposed to the model; every mutation is a field of the one structured decision.
SEQUENTIAL_RESEARCH_TOOL_NAMES = ("read_messages", "search_resources", "read_resource")
BATCH_RESEARCH_TOOL_NAMES = ("search_resources_batch", "read_resources_batch")
# I-098 / D-078: the tools whose calls dispatch Resource operations (``read_messages`` does not).
RESOURCE_OPERATION_TOOL_NAMES = frozenset(
    ("search_resources", "read_resource", *BATCH_RESEARCH_TOOL_NAMES)
)
COMPACTABLE_RESEARCH_TOOL_NAMES = frozenset(
    (*SEQUENTIAL_RESEARCH_TOOL_NAMES, *BATCH_RESEARCH_TOOL_NAMES)
)
TOOL_LOOP_ACTION_NAMES = (
    "observe",
    "send_message",
    "make_argument",
    "make_con",
    "pass_con",
    "update_strategic_memory",
    "ready",
)
REPLY_TOOL_NAME = "reply_to_cons"  # I-046: registered only under visible_reply
# I-091 / D-077: the server-mutating tools whose success can retract the activation's declared
# ``available_actions`` (api.md §8.1 and the phase-closing ``ready``/``pass_con``/``make_con``).
# ``update_strategic_memory`` stages client-side and is not one of them.
MUTATING_TOOL_NAMES = frozenset(
    {"send_message", "make_argument", "make_con", "pass_con", "reply_to_cons", "ready"}
)


class ToolSurface:
    """One player's tool exposure and per-activation call accounting, under one lock.

    Every counter, call-key set and declaration below is read and written only while holding
    ``_lock``. PydanticAI runs the sync tools of one model response concurrently, so that lock is
    what makes the budgets exact.
    """

    def __init__(
        self,
        guardrails: GuardrailConfig,
        *,
        single_shot: bool,
        available_only: bool,
        batched_research: bool,
        visible_reply: bool,
    ) -> None:
        self._guardrails = guardrails
        self._single_shot = single_shot
        # I-092 / D-079: only the tool-loop mutating surface is filtered; single_shot's decision
        # schema is already derived from ``available_actions`` (D-046).
        self._available_only = available_only and not single_shot
        self._batched_research = batched_research
        self._visible_reply = visible_reply
        self._lock = threading.Lock()
        self._tool_calls = 0
        self._research_tool_calls = 0
        # I-098 / D-078: Resource operations charged before dispatch (batched items included).
        self._research_operations = 0
        self._successful_research_calls: set[str] = set()
        # I-077: research calls reserved under the lock and still executing. A duplicate check
        # alone lets two identical sibling calls both pass and both reach the server; a
        # reservation makes at most one execute. An in-flight twin is rejected but does not close
        # the research surface — only a repeat of an already-*successful* call is D-062's
        # exact-repeat closure.
        self._inflight_research_calls: set[str] = set()
        self._research_exhausted = False
        # I-091 / D-077: the mutating actions the activation packet declared, and the server's
        # latest declaration after the Actor's own successful mutation. A declared tool the
        # server has since retracted is withdrawn for the rest of the activation; a tool the
        # packet never declared is left exactly as it was (I-092 stays measurable).
        self._declared_actions: frozenset[str] = frozenset()
        self._live_actions: frozenset[str] | None = None

    # --- the activation boundary -----------------------------------------------------------

    def exposed_tool_names(self) -> frozenset[str]:
        """The exact tool set registered for the model under this configuration."""
        research_tools = list(SEQUENTIAL_RESEARCH_TOOL_NAMES)
        if self._batched_research:
            research_tools.extend(BATCH_RESEARCH_TOOL_NAMES)
        loop_tools = list(TOOL_LOOP_ACTION_NAMES)
        if self._visible_reply:
            loop_tools.append(REPLY_TOOL_NAME)
        return frozenset(research_tools if self._single_shot else [*research_tools, *loop_tools])

    def reset(self) -> None:
        """Start a new logical activation: every count, reservation and live declaration clears.

        The transient model retry inside one activation does not call this, so its counts span
        both attempts (I-077).
        """
        with self._lock:
            self._tool_calls = 0
            self._research_tool_calls = 0
            self._research_operations = 0
            self._successful_research_calls = set()
            self._inflight_research_calls = set()
            self._research_exhausted = False
            self._live_actions = None

    def declare(self, actions: Iterable[str]) -> None:
        """Record the activation packet's ``available_actions``."""
        with self._lock:
            self._declared_actions = frozenset(actions)

    def record_live_actions(self, actions: Iterable[str]) -> None:
        """Record the server's refreshed declaration after the Actor's own mutation (D-077)."""
        with self._lock:
            self._live_actions = frozenset(actions)

    @property
    def tool_calls(self) -> int:
        with self._lock:
            return self._tool_calls

    def research_counts(self) -> dict[str, int]:
        """The I-098 / D-078 per-activation research counts, for results and failures alike."""
        with self._lock:
            return {
                "research_tool_calls": self._research_tool_calls,
                "research_operations": self._research_operations,
            }

    # --- Resource operations (D-078) -------------------------------------------------------

    def remaining_research_operations(self) -> int:
        with self._lock:
            return self._guardrails.max_research_operations - self._research_operations

    def charge_research_operation(self) -> dict[str, Any] | None:
        """Charge one Resource operation before dispatch (I-098 / D-078), or refuse.

        Decided and charged under the lock so two sibling calls in one model response cannot
        both pass on the same remaining unit. A charged operation stays charged whatever the
        request then returns; a refusal dispatches nothing and consumes no operation.
        """
        with self._lock:
            if self._research_operations >= self._guardrails.max_research_operations:
                return guardrail_error(
                    "guardrail_research_operations_exhausted",
                    "the Resource operation budget is exhausted; make the required decision "
                    "using the information already available",
                    limit=self._guardrails.max_research_operations,
                    spent=self._research_operations,
                )
            self._research_operations += 1
        return None

    # --- exposure ---------------------------------------------------------------------------

    def prepare_mutating_tool(self, ctx, tool_def):  # noqa: ANN001, ANN201
        """Withdraw a declared mutating tool the server has retracted (I-091 / D-077).

        Under ``available_only`` (I-092 / D-079) withdraw every mutating tool the server's
        current declaration — the packet's ``available_actions``, or the live refresh after
        the Actor's own mutation — does not list. The server names actions by these tool
        names (api.md §8.1), so nothing here infers legality; ``broad`` keeps the D-077 rule
        alone, so an attempt outside the declaration stays measurable.
        """
        with self._lock:
            declared = self._declared_actions
            live = self._live_actions
        current = declared if live is None else live
        if self._available_only:
            return tool_def if tool_def.name in current else None
        if live is not None and tool_def.name in declared and tool_def.name not in live:
            return None
        return tool_def

    def prepare_research_tool(self, ctx, tool_def):  # noqa: ANN001, ANN201
        """Expose research only while optional capacity remains (I-069)."""
        with self._lock:
            spent = self._research_tool_calls
            exhausted = self._research_exhausted
        request_limit = getattr(ctx.usage_limits, "request_limit", None)
        tool_limit = getattr(ctx.usage_limits, "tool_calls_limit", None)
        request_room = (
            request_limit is None or ctx.usage.requests < max(0, request_limit - 1)
        )
        tool_room = tool_limit is None or ctx.usage.tool_calls < max(0, tool_limit - 1)
        remaining = self._guardrails.max_research_tool_calls - spent
        if exhausted or remaining <= 0 or not request_room or not tool_room:
            return None
        # I-098 / D-078: a Resource tool is withdrawn once the operation budget is spent,
        # invocations or not; ``read_messages`` performs no Resource operation.
        operations_remaining = self.remaining_research_operations()
        if tool_def.name in RESOURCE_OPERATION_TOOL_NAMES and operations_remaining <= 0:
            return None
        description = tool_def.description or ""
        description = (
            f"{description} Research is optional; {remaining} research tool call"
            f"{'' if remaining == 1 else 's'} remain in this activation. Do not repeat "
            "a successful call; reserve capacity for the required decision."
        )
        if tool_def.name in BATCH_RESEARCH_TOOL_NAMES:
            # The batch tools alone disclose the operation budget: a sequential call is one
            # operation, so its definition gains nothing and stays byte-identical (v11/v13).
            description += (
                f" {operations_remaining} Resource operation"
                f"{'' if operations_remaining == 1 else 's'} remain; each query or id is "
                "one. A batch longer than the remaining operations executes in order up to "
                "that budget and returns the rest under not_executed. Every executed item "
                "returns an ordered success or error outcome; a recoverable error does not "
                "stop later items."
            )
        return replace(tool_def, description=description)

    # --- admission and accounting -----------------------------------------------------------

    def tracked(self, agent: Any, *, refresh_live_surface: Callable[[], dict[str, Any]]):  # noqa: ANN201
        """The decorator the player registers every tool with on ``agent``.

        ``@tracked_tool`` registers an action tool; ``@tracked_tool(research=True)`` a research
        tool. The decorated function is registered unchanged apart from the counting wrapper
        (``functools.wraps``), so its name, docstring and signature — the model-facing
        definition — are the player's own. ``refresh_live_surface`` re-reads the server's
        declaration after the Actor's own successful mutation and returns the context fragment
        attached to that result (D-077).
        """

        def tracked_tool(function=None, *, research: bool = False):  # noqa: ANN001, ANN202
            """Count tool invocations even when the enclosing activation later fails."""

            def register(func):  # noqa: ANN001, ANN202
                mutating = func.__name__ in MUTATING_TOOL_NAMES

                @wraps(func)
                def counted(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
                    call_key = ""
                    duplicate = False
                    over_research_budget = False
                    with self._lock:
                        self._tool_calls += 1
                        if research:
                            self._research_tool_calls += 1
                            over_research_budget = (
                                self._research_tool_calls
                                > self._guardrails.max_research_tool_calls
                            )
                            call_key = json.dumps(
                                [func.__name__, args, kwargs],
                                sort_keys=True,
                                separators=(",", ":"),
                                default=str,
                            )
                            # I-077: decide and reserve under the lock, *before* the tool runs,
                            # so a duplicate never reaches the server (no second ResourceAccess
                            # row) and two identical concurrent calls execute at most once.
                            if call_key in self._successful_research_calls:
                                duplicate = True
                                self._research_exhausted = True  # D-062 exact-repeat closure
                            elif call_key in self._inflight_research_calls:
                                duplicate = True
                            elif not over_research_budget:
                                self._inflight_research_calls.add(call_key)
                    if over_research_budget:
                        return guardrail_error(
                            "guardrail_research_budget_exhausted",
                            "the optional research budget is exhausted; make the required "
                            "decision using the information already available",
                            limit=self._guardrails.max_research_tool_calls,
                            spent=self._research_tool_calls,
                        )
                    if duplicate:
                        return guardrail_error(
                            "guardrail_research_duplicate",
                            "this successful research call was already completed in this "
                            "activation; use its retained result and make the required decision",
                        )

                    if not research:
                        result = func(*args, **kwargs)
                        if mutating and isinstance(result, dict) and result.get("ok") is True:
                            # I-091 / D-077: the Actor's own success may have moved the game;
                            # re-declare from the server and tell the model in the result.
                            result["context"] = refresh_live_surface()
                        return result
                    try:
                        result = func(*args, **kwargs)
                    finally:
                        with self._lock:
                            self._inflight_research_calls.discard(call_key)
                    # A failed call may legitimately be tried again; only success is remembered.
                    if isinstance(result, dict) and result.get("ok") is not False:
                        with self._lock:
                            self._successful_research_calls.add(call_key)
                    return result

                prepare = None
                if research:
                    prepare = self.prepare_research_tool
                elif mutating:
                    prepare = self.prepare_mutating_tool
                return agent.tool_plain(counted, prepare=prepare)

            return register if function is None else register(function)

        return tracked_tool
