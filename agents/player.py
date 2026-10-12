"""The PydanticAI reference player (T-039, agents.md §2/§4/§17, TRD §4/§6).

One neutral, benchmark-grade player that controls exactly one AMAW Actor. It is bound to a
single Seat (one :class:`~agents.tools.PlayerTools`), a single configured model, and the
versioned neutral prompt for its activation mode (``docs/prompts/player-v1.10.md`` /
``player-v1.11.md``). It exposes only the player-safe
T-037 tools to the model, and it talks to the game **exclusively over HTTP** through those tools
— never the engine, persistence, visibility service or database (AGENTS.md §6 / TRD §4). The
HTTP visibility service therefore stays the one security boundary: the player only ever receives
what its Seat token authorises.

**Central architectural rule (this batch): fresh, bounded context per activation.** Persistent
Actor identity is *not* a persistent LLM conversation. Each activation builds a compact
*decision packet* from authoritative Actor context, incremental inbound Messages and the Actor's
one server-backed structured strategic memory, runs the model with **no** prior message history,
takes its action(s), and discards temporary reasoning. Successful completion atomically
acknowledges the delivered Message snapshot and replaces memory; failed/stalled activations do
neither. Cross-activation memory is not a growing chat transcript or Note archive.

**Two activation modes (I-031).** ``tool_loop`` exposes the legal action/research tools plus one
replace-memory staging tool and expects the model to call one mutating tool per provider
round-trip — expected, not enforced: the tool calls of one response are dispatched concurrently
and two sibling mutations race (D-082). ``single_shot`` (the default) exposes only
the three read tools and asks for **one structured decision** per activation — see
:mod:`agents.decisions` — which this player validates inside the model run and then executes
against the server in a fixed order. Each mode has its own immutable prompt version and is
recorded in provenance; a model benchmark holds it constant (agents.md §19).

Guardrail ceilings, stall recovery and auto-ready/auto-pass on failure are **T-041** and live in
the lifecycle runner; the per-activation bounds are applied inside ``activate``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic_ai import Agent
from pydantic_ai.exceptions import ModelRetry, UsageLimitExceeded
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.usage import UsageLimits

from amaw_contracts import (  # noqa: F401 - the profile vocabulary is re-exported from here
    INFERENCE_PROFILE_SCHEMA,
    INFERENCE_SETTING_KEYS,
    INVALID_TOOL_CALL_CATEGORIES,
    INVALID_TOOL_CALL_SCHEMA_VERSION,
    PLAYER_INFERENCE_PHASES,
    REASONING_EFFORTS,
    SEARCH_CONTRACTS,
    SEARCH_CONTRACT_DEFAULT,
    SEARCH_CONTRACT_V1,
    STRATEGIC_MEMORY_MAX_BYTES,
    is_reasoning_effort,
)
from agents.guardrails import (  # noqa: F401 - re-exported: the historical import path
    GuardrailConfig,
    check_argument,
    check_message,
    check_note,
    check_payload,
    check_strategic_memory,
    guardrail_error as _guardrail_error,
)
from agents.tool_surface import ToolSurface
from agents.tools import PlayerTools, ToolResult
from agents.player_errors import (  # noqa: F401 - re-exported: the historical import path
    FAIL_DETERMINISTIC,
    FAIL_GUARDRAIL,
    FAIL_TRANSIENT,
    ActivationFailed,
    PlayerConfigError,
    PlayerError,
    _cause_summary,
    _classify_activation_error,
    _failure_cause,
    classify_invalid_tool_calls,
)
from agents.player_config import (  # noqa: F401 - re-exported: the historical import path
    ACTIVATION_MODES,
    ACTIVATION_MODE_SINGLE_SHOT,
    ACTIVATION_MODE_TOOL_LOOP,
    BASELINE_TOOL_SCHEMA_VERSION,
    BATCHED_REPLY_TOOL_SCHEMA_VERSION,
    BATCHED_TOOL_SCHEMA_VERSION,
    CON_REPLY_MODES,
    CON_REPLY_MODE_SEALED,
    CON_REPLY_MODE_VISIBLE,
    PLAYER_CONTEXT_MODE,
    PLAYER_MEMORY_VERSION,
    PLAYER_PROMPT_VERSION,
    PLAYER_PROMPT_VERSIONS,
    REPLY_PLAYER_PROMPT_VERSIONS,
    REPLY_TOOL_SCHEMA_VERSION,
    RESEARCH_BATCH_MAX_ITEMS,
    RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
    RESEARCH_HISTORY_FULL_V1,
    RESEARCH_HISTORY_MODES,
    RESEARCH_MODES,
    RESEARCH_MODE_BATCHED_V1,
    RESEARCH_MODE_SEQUENTIAL_V1,
    TOOL_SURFACES,
    TOOL_SURFACE_AVAILABLE_ONLY,
    TOOL_SURFACE_BROAD,
    _CONTEXT_FRAGMENT_KEYS,
    PlayerConfig,
    load_player_prompt,
    prompt_hash_for,
    tool_schema_version_for,
)
from agents.player_model import (  # noqa: F401 - re-exported: the historical import path
    _TimedModel,
    _accumulated_usage_dict,
    build_openrouter_model,
    compact_research_history,
    phase_model_settings,
    player_model_settings,
)

logger = logging.getLogger("agents.player")

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime into app/
    from app.config import Settings


# The activation prompt embeds the decision packet as JSON after this marker, so the packet is
# both human-legible for a real model and machine-parseable for the offline fake models in tests.
ACTIVATION_PACKET_MARKER = "=== DECISION CONTEXT (JSON) ==="

# The tool-name vocabularies and the exposure policy live in :mod:`agents.tool_surface`.


@dataclass(frozen=True)
class ActivationResult:
    """The outcome of one activation, with provenance for later benchmarking (agents.md §19).

    Exposes requested/returned model, usage, latency and the tools called — enough for T-053 to
    build on — **without** building the benchmark system here. ``messages`` is the *temporary*
    per-activation history, returned for inspection only; the player never retains it.
    """

    activation_id: str
    output: str
    tool_call_names: list[str]
    model_requested: str
    model_returned: str | None
    usage: dict[str, Any] | None
    latency_seconds: float
    activation_wall_seconds: float
    provider_wait_seconds: float
    model_iterations: int
    provider: str | None = None
    invalid_tool_calls: int = 0
    invalid_tool_call_breakdown: dict[str, Any] = field(default_factory=dict)
    request_topology: dict[str, Any] = field(default_factory=dict)
    messages: list[Any] = field(default_factory=list)
    messages_sent: int = 0  # Messages actually sent this activation (per-phase cap accounting)
    # I-098 / D-078: research *invocations* charged (every research tool call, refused or not)
    # and Resource *operations* charged (every search/read dispatched toward the server).
    research_tool_calls: int = 0
    research_operations: int = 0
    # Single-shot mode only (I-031): the validated decision the model returned, as plain data.
    decision: dict[str, Any] | None = None


def _payload(result: ToolResult) -> dict[str, Any]:
    """Render a :class:`ToolResult` as a JSON-able dict the model can read.

    A recoverable API rejection (wrong phase, not the active Actor, budget exhausted, …) is
    surfaced as structured data so the model can *correct itself within the same activation*
    (agents.md §18). Infrastructure failures still raise inside the tool layer.
    """
    if result.ok:
        return {"ok": True, "data": result.data}
    return {
        "ok": False,
        "http_status": result.http_status,
        "error": {
            "code": result.error_code,
            "message": result.error_message,
            "details": result.error_details,
        },
    }


class ReferencePlayer:
    """A neutral reference player bound to one Seat/game/Actor (agents.md §2)."""

    def __init__(
        self,
        tools: PlayerTools,
        *,
        actor_id: str,
        actor_name: str,
        model: Any,
        config: PlayerConfig,
        prompt: str | None = None,
        provider: str | None = None,
    ) -> None:
        self._tools = tools
        self._actor_id = actor_id
        self._actor_name = actor_name
        self._config = config
        self._provider = provider
        self._guardrails = config.guardrails
        self._model = _TimedModel(
            model, research_history_mode=config.research_history_mode
        )
        self._prompt = (
            prompt if prompt is not None
            else load_player_prompt(actor_name, version=config.prompt_version)
        )
        # The runner supplies remaining phase capacity. I-033's normal activation limit is always
        # active, including direct player use; an internal model retry shares the same counters.
        self._message_budget: int | None = None
        self._messages_sent_this_activation = 0
        self._opportunity_namespace = "unscoped"
        self._activation_sequence = 0
        self._successful_message_keys: set[str] = set()
        # PydanticAI runs the tool calls of one model response concurrently (sync tools in an
        # executor). The per-phase Message cap is check→send→count, so without serialisation two
        # send_message calls in one response both pass a cap of 1 (I-021). One lock around the
        # whole send keeps the count exact; Messages are rare, so the serialisation is free.
        self._send_lock = threading.Lock()
        self._single_shot = config.activation_mode == ACTIVATION_MODE_SINGLE_SHOT
        self._visible_reply = config.con_reply_mode == CON_REPLY_MODE_VISIBLE
        # Which tools the model sees and how each call is admitted and counted (I-124).
        self._surface = ToolSurface(
            self._guardrails,
            single_shot=self._single_shot,
            available_only=config.tool_surface == TOOL_SURFACE_AVAILABLE_ONLY,
            batched_research=config.research_mode == RESEARCH_MODE_BATCHED_V1,
            visible_reply=self._visible_reply,
        )
        self._delivery_receipt: str | None = None
        self._staged_strategic_memory: dict[str, Any] | None = None
        self._agent = self._build_agent()
        # The exact tool set exposed to the model — asserted against in tests.
        self.tool_names = self._surface.exposed_tool_names()

    @classmethod
    def for_openrouter(
        cls,
        tools: PlayerTools,
        *,
        actor_id: str,
        actor_name: str,
        config: PlayerConfig,
        settings: Settings,
        prompt: str | None = None,
    ) -> ReferencePlayer:
        """Build a player whose model is OpenRouter-backed via PydanticAI (deployment use)."""
        model = build_openrouter_model(config, settings)
        return cls(
            tools,
            actor_id=actor_id,
            actor_name=actor_name,
            model=model,
            config=config,
            prompt=prompt,
            provider="openrouter",
        )

    # --- identity / configuration --------------------------------------------------------

    @property
    def actor_id(self) -> str:
        return self._actor_id

    @property
    def actor_name(self) -> str:
        return self._actor_name

    @property
    def game_id(self) -> str:
        return self._tools._game_id  # the Seat's game — set once at construction

    @property
    def tools(self) -> PlayerTools:
        return self._tools

    @property
    def config(self) -> PlayerConfig:
        return self._config

    @property
    def prompt(self) -> str:
        return self._prompt

    @property
    def prompt_version(self) -> str:
        # ``PlayerConfig.__post_init__`` resolves a missing version from the activation mode, so
        # a constructed config always carries one.
        version = self._config.prompt_version
        if version is None:
            raise PlayerConfigError("PlayerConfig.prompt_version was not resolved")
        return version

    @property
    def prompt_hash(self) -> str:
        """SHA-256 of the configured prompt version's template, before Actor-name substitution."""
        return prompt_hash_for(self._config)

    @property
    def activation_mode(self) -> str:
        return self._config.activation_mode

    @property
    def research_mode(self) -> str:
        return self._config.research_mode

    @property
    def research_history_mode(self) -> str:
        return self._config.research_history_mode

    @property
    def con_reply_mode(self) -> str:
        return self._config.con_reply_mode

    @property
    def tool_schema_version(self) -> str:
        return tool_schema_version_for(self._config)

    async def aclose(self) -> None:
        """Close the owned OpenRouter client on the Actor's model event loop (I-042)."""
        if self._provider != "openrouter":
            return
        client = getattr(self._model.wrapped, "client", None)
        if client is not None:
            await client.close()

    # --- the model agent and its player-safe tools ---------------------------------------

    def _build_agent(self) -> Agent:
        """One PydanticAI Agent exposing only the baseline player tools (agents.md §5).

        Tools are thin closures over the bound :class:`PlayerTools`: each performs the documented
        HTTP call and returns structured data. The model may call several in one activation
        (agents.md §3). Identity is never a tool argument — it is the Seat token's alone
        (INV-ACTOR-001), so no tool accepts ``actor_id``.
        """
        agent = Agent(self._model, system_prompt=self._prompt)

        # Exposure and per-call accounting belong to the tool surface (I-124); the bodies below
        # are registered unchanged, so each model-facing definition is this closure's own.
        tracked_tool = self._surface.tracked(
            agent, refresh_live_surface=self._refresh_live_surface
        )

        mutation_key = self._mutation_key

        @tracked_tool(research=True)
        def read_messages(after_event_id: int | None = None) -> dict[str, Any]:
            """Read the Messages currently visible to you."""
            return _payload(self._tools.read_messages(after_event_id=after_event_id))

        @tracked_tool(research=True)
        def search_resources(query: str) -> dict[str, Any]:
            """Search scenario Resources available to you for information relevant to your goals."""
            refusal = self._surface.charge_research_operation()
            if refusal is not None:
                return refusal
            return _payload(
                self._tools.search_resources(
                    query=query, search_contract=self._config.search_contract
                )
            )

        @tracked_tool(research=True)
        def read_resource(resource_id: str) -> dict[str, Any]:
            """Read one scenario Resource you are authorised to see (use an id from search_resources)."""
            refusal = self._surface.charge_research_operation()
            if refusal is not None:
                return refusal
            return _payload(self._tools.read_resource(resource_id=resource_id))

        if self._config.research_mode == RESEARCH_MODE_BATCHED_V1:
            @tracked_tool(research=True)
            def search_resources_batch(queries: list[str]) -> dict[str, Any]:
                """Search up to eight queries; return every ordered success/error outcome."""
                violation = self._validate_research_batch("queries", queries)
                if violation is not None:
                    return violation
                return self._run_research_batch(
                    "query",
                    queries,
                    lambda query: self._tools.search_resources(
                        query=query, search_contract=self._config.search_contract
                    ),
                )

            @tracked_tool(research=True)
            def read_resources_batch(resource_ids: list[str]) -> dict[str, Any]:
                """Read up to eight ids; return every ordered success/error outcome."""
                violation = self._validate_research_batch("resource_ids", resource_ids)
                if violation is not None:
                    return violation
                return self._run_research_batch(
                    "resource_id",
                    resource_ids,
                    lambda resource_id: self._tools.read_resource(resource_id=resource_id),
                )

        if self._single_shot:
            # Every mutation is a field of the structured decision (agents/decisions.py); the
            # decision itself is validated and executed by ``activate``.
            return agent

        @tracked_tool
        def observe() -> dict[str, Any]:
            """Refresh your authorised game context, including the legal available_actions."""
            return _payload(self._tools.observe())

        @tracked_tool
        def send_message(recipients: list[str], public: bool, text: str) -> dict[str, Any]:
            """Send a public (recipients=[]) or private Message to named recipients."""
            args = {"recipients": recipients, "public": public, "text": text}
            violation = check_message(text, self._guardrails) or check_payload(
                args, self._guardrails
            )
            if violation is not None:
                return violation
            with self._send_lock:
                # I-033 normal per-activation budget. The count is not reset by the one internal
                # model retry, and the entire check→send→count path stays locked (I-021).
                if (
                    self._messages_sent_this_activation
                    >= self._guardrails.max_messages_per_activation
                ):
                    return _guardrail_error(
                        "guardrail_message_activation_budget_exhausted",
                        "the per-activation Message budget is exhausted; continue with other "
                        "legal actions",
                        limit=self._guardrails.max_messages_per_activation,
                        spent=self._messages_sent_this_activation,
                    )
                # The existing phase-wide emergency ceiling remains a separate check.
                if self._message_budget is not None and (
                    self._messages_sent_this_activation >= self._message_budget
                ):
                    return _guardrail_error(
                        "guardrail_message_cap_reached",
                        "the per-phase message cap has been reached; do not send more this phase",
                        limit=self._guardrails.max_messages_per_phase,
                    )
                key = mutation_key("send_message", args)
                result = self._tools.send_message(
                    recipients=recipients, public=public, text=text, idempotency_key=key
                )
                if result.ok and key not in self._successful_message_keys:
                    self._successful_message_keys.add(key)
                    self._messages_sent_this_activation += 1
                return _payload(result)

        @tracked_tool
        def make_argument(action: str, desired_effect: str, pros: list[str]) -> dict[str, Any]:
            """Submit your primary Matrix Argument: an action, a desired Effect, and Pros."""
            args = {"action": action, "desired_effect": desired_effect, "pros": pros}
            violation = check_argument(
                action, desired_effect, pros, self._guardrails
            ) or check_payload(args, self._guardrails)
            if violation is not None:
                return violation
            return _payload(
                self._tools.make_argument(
                    action=action, desired_effect=desired_effect, pros=pros,
                    idempotency_key=mutation_key("make_argument", args),
                )
            )

        @tracked_tool
        def make_con(argument_id: str, reason: str) -> dict[str, Any]:
            """Challenge the current Argument with a substantive Con."""
            args = {"argument_id": argument_id, "reason": reason}
            violation = check_message(reason, self._guardrails) or check_payload(
                args, self._guardrails
            )
            if violation is not None:
                return violation
            return _payload(self._tools.make_con(
                argument_id=argument_id, reason=reason,
                idempotency_key=mutation_key("make_con", args),
            ))

        @tracked_tool
        def pass_con(argument_id: str) -> dict[str, Any]:
            """Close your participation in the current Argument's Cons without a further Con."""
            payload = {"argument_id": argument_id}
            return _payload(self._tools.pass_con(
                argument_id=argument_id,
                idempotency_key=mutation_key("pass_con", payload),
            ))

        if self._visible_reply:

            @tracked_tool
            def reply_to_cons(argument_id: str, points: list[dict[str, str]]) -> dict[str, Any]:
                """Answer the revealed Cons against your own Argument, once; points=[] passes."""
                args = {"argument_id": argument_id, "points": points}
                violation = self._check_reply_points(points) or check_payload(
                    args, self._guardrails
                )
                if violation is not None:
                    return violation
                return _payload(self._tools.reply_to_cons(
                    argument_id=argument_id, points=points,
                    idempotency_key=mutation_key("reply_to_cons", args),
                ))

        @tracked_tool
        def update_strategic_memory(
            commitments_made: list[str],
            commitments_or_offers_received: list[str],
            outstanding_offers_and_negotiations: list[str],
            red_lines_and_constraints: list[str],
            uncertain_or_disputed_claims: list[str],
            relationships_and_likely_coalitions: list[str],
            strategic_assessment: str,
            intended_next_action: str,
        ) -> dict[str, Any]:
            """Stage the complete private rolling memory to commit when this activation succeeds."""
            memory = {
                "commitments_made": commitments_made,
                "commitments_or_offers_received": commitments_or_offers_received,
                "outstanding_offers_and_negotiations": outstanding_offers_and_negotiations,
                "red_lines_and_constraints": red_lines_and_constraints,
                "uncertain_or_disputed_claims": uncertain_or_disputed_claims,
                "relationships_and_likely_coalitions": relationships_and_likely_coalitions,
                "strategic_assessment": strategic_assessment,
                "intended_next_action": intended_next_action,
            }
            violation = check_strategic_memory(memory, self._guardrails)
            if violation is not None:
                return violation
            self._staged_strategic_memory = memory
            return {"ok": True, "data": {"staged": True}}

        @tracked_tool
        def ready() -> dict[str, Any]:
            """Signal you have nothing further to do this diplomacy opportunity."""
            return _payload(self._tools.ready(
                idempotency_key=mutation_key("ready", {})
            ))

        return agent

    def _check_reply_points(self, points: Any) -> dict[str, Any] | None:
        """Tool-loop twin of the single-shot reply validation (I-046): shape and text only —
        the server owns cardinality, references and exactly-once inside its transaction."""
        if not isinstance(points, list) or any(
            not isinstance(point, dict)
            or set(point) != {"con_id", "text"}
            or not isinstance(point.get("con_id"), str)
            or not isinstance(point.get("text"), str)
            for point in points
        ):
            return _guardrail_error(
                "guardrail_reply_points_invalid",
                "points must be a list of {con_id, text} objects",
            )
        for point in points:
            violation = check_message(point["text"], self._guardrails)
            if violation is not None:
                return violation
        return None

    def _validate_research_batch(
        self, field_name: str, values: list[str]
    ) -> dict[str, Any] | None:
        if (
            not isinstance(values, list)
            or not 1 <= len(values) <= RESEARCH_BATCH_MAX_ITEMS
            or any(not isinstance(value, str) or not value.strip() for value in values)
        ):
            return _guardrail_error(
                "guardrail_research_batch_invalid",
                f"{field_name} must contain 1–{RESEARCH_BATCH_MAX_ITEMS} non-empty strings",
                limit=RESEARCH_BATCH_MAX_ITEMS,
            )
        return check_payload({field_name: values}, self._guardrails)

    def _run_research_batch(
        self,
        field_name: str,
        values: list[str],
        dispatch: Callable[[str], ToolResult],
    ) -> dict[str, Any]:
        """Execute a validated batch into ordered item outcomes, truncated by the budget.

        Each item is charged immediately before its own dispatch (D-078). Items beyond the
        remaining budget are never attempted and are listed under ``not_executed`` in input
        order. A recoverable failure is an item outcome and does not abandon later work (D-085).
        The call is ``ok`` when at least one item executed; a refusal before the first dispatch
        keeps D-078's whole-call guardrail shape.
        """
        items: list[dict[str, Any]] = []
        not_executed: list[dict[str, Any]] = []
        refusal: dict[str, Any] | None = None
        for index, value in enumerate(values):
            refusal = self._surface.charge_research_operation()
            if refusal is not None:
                not_executed = [
                    {field_name: rest, "error": {"code": refusal["error"]["code"]}}
                    for rest in values[index:]
                ]
                break
            result = dispatch(value)
            if result.ok:
                items.append({field_name: value, "ok": True, "data": result.data})
            else:
                items.append({
                    field_name: value,
                    "ok": False,
                    "error": {
                        "http_status": result.http_status,
                        "code": result.error_code,
                        "message": result.error_message,
                        "details": result.error_details,
                    },
                })
        if not items and refusal is not None:
            return refusal  # nothing executed: the whole batch is the refusal
        data: dict[str, Any] = {"items": items}
        if not_executed:
            data["not_executed"] = not_executed
        return {"ok": True, "data": data}

    def _mutation_key(self, operation: str, payload: dict[str, Any]) -> str:
        """Idempotency key for one mutation within the current opportunity (api.md §4)."""
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        digest = hashlib.sha256(
            f"{self._opportunity_namespace}|{operation}|{canonical}".encode()
        ).hexdigest()
        return f"amaw_{digest}"

    def _decision_output(self, packet: dict[str, Any]) -> Any:
        """The per-activation ``ToolOutput`` for single-shot mode, or ``None`` if nothing to decide.

        The schema follows the server's ``available_actions`` (agents/decisions.py); the output
        function validates the decision against the guardrails and raises ``ModelRetry`` with the
        same structured error the tool loop would have returned, so a breach costs one corrected
        model call, never a request.
        """
        from pydantic_ai import ModelRetry, ToolOutput

        from agents import decisions

        actions = list(packet.get("available_actions", []))
        decision_type = decisions.select_decision_type(actions)
        if decision_type is None:
            return None
        budget = self._remaining_message_budget()
        con_allowed = "make_con" in actions
        limits = packet.get("limits") or {}
        con_slots_remaining = limits.get("con_slots_remaining")
        # I-046: the reply bound and the revealed con_ids come from the server packet, so the
        # decision is bounded before any request (agents.md §5).
        reply_max_points = limits.get("reply_max_points")
        reply_con_ids = [
            con["con_id"] for con in (packet.get("current_argument") or {}).get("cons", [])
        ]
        guardrails = self._guardrails

        def decide(decision):  # noqa: ANN001, ANN202 - annotated below, per activation
            violation = decisions.validate_decision(
                decision,
                guardrails=guardrails,
                message_budget=budget,
                con_allowed=con_allowed,
                con_slots_remaining=con_slots_remaining,
                reply_max_points=reply_max_points,
                reply_con_ids=reply_con_ids,
            )
            if violation is not None:
                raise ModelRetry(json.dumps(violation, default=str))
            return decision

        # The schema is this activation's decision type. Set as real types (not the module's
        # postponed string annotations) so PydanticAI can build the output tool from them.
        decide.__annotations__ = {"decision": decision_type, "return": decision_type}
        return ToolOutput(decide, name=decisions.DECISION_TOOL_NAME)

    @property
    def guardrails(self) -> GuardrailConfig:
        return self._guardrails

    def set_message_budget(self, remaining: int | None) -> None:
        """Set remaining phase capacity; I-033's activation budget remains independently active.

        Called by the :class:`LifecycleRunner`, which tracks the count across activations for the
        current ``(turn, phase)``. ``None`` removes only the phase bound (e.g. direct use in a
        test). The per-activation bound is always enforced inside ``send_message``.
        """
        self._message_budget = remaining

    def _remaining_message_budget(self) -> int:
        """Additional successful sends allowed now by both independent Message ceilings."""
        activation_remaining = max(
            0,
            self._guardrails.max_messages_per_activation
            - self._messages_sent_this_activation,
        )
        if self._message_budget is None:
            return activation_remaining
        phase_remaining = max(0, self._message_budget - self._messages_sent_this_activation)
        return min(activation_remaining, phase_remaining)

    # --- activation ----------------------------------------------------------------------

    def _refresh_live_surface(self) -> dict[str, Any]:
        """Re-read the server's declaration after the Actor's own successful mutation (D-077).

        Returns the agents.md §6 ``observe`` fragment for the tool result and records the
        server's current ``available_actions`` so the tool surface can withdraw a
        declared tool the server no longer lists. The server computes the list (API-INV-009);
        nothing here infers a phase.
        """
        context = self._observe_context()
        self._surface.record_live_actions(context.get("available_actions", []))
        return {key: context.get(key) for key in _CONTEXT_FRAGMENT_KEYS}

    def _observe_context(self) -> dict[str, Any]:
        result = self._tools.observe()
        if not result.ok or result.data is None:
            raise PlayerError(
                f"could not read context for {self._actor_id!r}: "
                f"{result.error_code or 'no data'}"
            )
        return result.data

    def _packet_limits(
        self, *, con_slots_remaining: int | None = None, reply_max_points: int | None = None
    ) -> dict[str, Any]:
        """The bounds the model must plan within (agents.md §18), stated as data in the packet.

        A ceiling the model cannot see is a ceiling it can only hit. The normal activation budget
        and the runner's shrinking per-phase capacity are restated every activation. The numbers
        are start-of-activation snapshots; ``send_message`` reports exhaustion dynamically.
        ``messages_remaining_this_phase`` is omitted when no runner supplied phase capacity, but
        I-033's activation budget still applies to direct use. The server-computed remaining Con
        capacity is present only during an eligible Cons opportunity.
        """
        g = self._guardrails
        limits: dict[str, Any] = {
            "max_llm_iterations": g.max_llm_iterations,
            "max_tool_calls": g.max_tool_calls,
            "max_research_tool_calls": g.max_research_tool_calls,
            "max_research_operations": g.max_research_operations,
            "max_messages_per_activation": g.max_messages_per_activation,
            "messages_remaining_this_activation": self._remaining_message_budget(),
            "max_messages_per_phase": g.max_messages_per_phase,
            "max_message_chars": g.max_message_chars,
            "max_argument_action_chars": g.max_argument_action_chars,
            "max_desired_effect_chars": g.max_desired_effect_chars,
            "max_pros": g.max_pros,
            "max_pro_chars": g.max_pro_chars,
            "max_note_chars": g.max_note_chars,
            "max_strategic_memory_bytes": STRATEGIC_MEMORY_MAX_BYTES,
        }
        if self._message_budget is not None:
            limits["messages_remaining_this_phase"] = self._message_budget
        if con_slots_remaining is not None:
            limits["con_slots_remaining"] = con_slots_remaining
        if reply_max_points is not None:
            limits["reply_max_points"] = reply_max_points
        return limits

    def build_packet(self, context: dict[str, Any] | None = None) -> dict[str, Any]:
        """Assemble the bounded, authorised decision packet for one activation (agents.md §4).

        Everything here is already visibility-scoped to this Seat (it comes from ``observe`` /
        the Actor's own Notes and Messages); this method adds no visibility rule of its own and
        includes no engine/SQL internals. Kept deliberately small and explicit — no
        summarisation, persistent chat transcript or RAG.
        """
        delivery = self._tools.begin_activation()
        if not delivery.ok or delivery.data is None:
            raise PlayerError(
                f"could not begin activation for {self._actor_id!r}: "
                f"{delivery.error_code or 'no data'}"
            )
        delivered = delivery.data
        self._delivery_receipt = delivered.get("delivery_receipt")
        self._staged_strategic_memory = dict(delivered.get("strategic_memory") or {})
        packet = {
            "scenario": delivered.get("scenario"),
            "turn": delivered.get("turn"),
            "max_turns": delivered.get("max_turns"),
            "phase": delivered.get("phase"),
            "status": delivered.get("status"),
            "active_actor_id": delivered.get("active_actor_id"),
            "you": delivered.get("you"),
            "effects": delivered.get("effects", []),
            "current_argument": delivered.get("current_argument"),
            "requires_input": delivered.get("requires_input"),
            "available_actions": delivered.get("available_actions", []),
            "messages_received": delivered.get("messages_received", []),
            "strategic_memory": delivered.get("strategic_memory"),
            "context_mode": delivered.get("context_mode"),
            "memory_version": delivered.get("memory_version"),
            "limits": self._packet_limits(
                con_slots_remaining=delivered.get("con_slots_remaining"),
                reply_max_points=delivered.get("reply_max_points"),
            ),
        }
        if "revealed_exchanges" in delivered:
            # I-046: present only under visible_reply — the sealed packet is byte-identical.
            packet["revealed_exchanges"] = delivered["revealed_exchanges"]
        if "argument_budget" in delivered:
            # D-094 (agents.md §11): present only under round-robin Argument rounds, so the
            # model can plan one decision per Argument instead of bundling several.
            packet["argument_budget"] = delivered["argument_budget"]
        return packet

    def build_activation_prompt(self, packet: dict[str, Any]) -> str:
        """Render the activation prompt: a neutral framing plus the packet as JSON.

        The prompt carries the phase only as *data* inside the packet — never an imperative
        "you are now in phase X, do Y" instruction (that would defeat the T-040 requirement that
        the agent choose its action from ``available_actions`` alone).
        """
        framing = (
            "Consider your Actor's situation from the authorised context below and take the "
            "action(s) you judge best, then stop. Only the actions listed in "
            "`available_actions` are currently permitted; respect them."
        )
        # The server, not this client, declares legality. Identify the procedural completion
        # action from that declaration so optional Messages/Notes cannot consume a whole bounded
        # activation while leaving the participation opportunity unfinished. This says nothing
        # about tactics and does not infer or hard-code a phase.
        actions = packet.get("available_actions", [])
        completion_action = next(
            (
                action
                for action in ("make_argument", "pass_con", "reply_to_cons", "ready")
                if action in actions
            ),
            None,
        )
        if self._single_shot:
            framing += (
                " Return exactly one decision with the `decide` tool; its fields follow from "
                "`available_actions`. If you need information you do not have, call a research "
                "tool first, then decide."
            )
        elif packet.get("requires_input") and completion_action is not None:
            framing += (
                " Before ending, complete the required participation opportunity with "
                f"`{completion_action}`; optional communication or notes do not complete it."
            )
        limits = packet.get("limits") or {}
        activation_remaining = limits.get("messages_remaining_this_activation")
        phase_remaining = limits.get("messages_remaining_this_phase")
        remaining = activation_remaining
        if phase_remaining is not None:
            remaining = min(activation_remaining, phase_remaining)
        if remaining == 0:
            framing += " You cannot send any Messages in this activation."
        elif remaining is not None:
            framing += (
                f" You may send at most {remaining} Message"
                f"{'' if remaining == 1 else 's'} in this activation; `limits` states the "
                "separate activation and phase capacities and "
                "every other bound."
            )
        body = json.dumps(packet, indent=2, default=str, sort_keys=False)
        return f"{framing}\n\n{ACTIVATION_PACKET_MARKER}\n{body}"

    def activate(self, context: dict[str, Any] | None = None) -> ActivationResult:
        """Run one activation inside a behaviour-neutral Resource-correlation scope."""
        activation_id = str(uuid.uuid4())
        self._tools.set_activation_correlation(activation_id)
        try:
            return self._activate(context, activation_id=activation_id)
        except ActivationFailed as exc:
            exc.activation_id = activation_id
            exc.counts.setdefault("activation_id", activation_id)
            raise
        finally:
            self._tools.set_activation_correlation(None)

    def _ceiling_counts(self, activation_started: float) -> dict[str, Any]:
        """The counts recorded on a guardrail-ceiling failure (activation-wide totals)."""
        model_iterations, provider_wait, request_usage, request_topology = self._model.metrics()
        return {
            "max_llm_iterations": self._guardrails.max_llm_iterations,
            "max_tool_calls": self._guardrails.max_tool_calls,
            "model_calls": model_iterations,
            "provider_wait_ms": round(provider_wait * 1000),
            "activation_wall_ms": round((time.perf_counter() - activation_started) * 1000),
            "tool_calls": self._surface.tool_calls,
            **self._surface.research_counts(),
            "messages_sent": self._messages_sent_this_activation,
            "usage": _accumulated_usage_dict(
                model_iterations,
                request_usage,
                tool_calls=self._surface.tool_calls,
            ),
            "request_topology": request_topology,
        }

    def _activate(
        self, context: dict[str, Any] | None = None, *, activation_id: str
    ) -> ActivationResult:
        """Run one fresh, bounded activation and return its result.

        A new run every time, with **no** ``message_history`` from any previous activation — the
        temporary reasoning is discarded when this method returns (nothing is stored on the
        player). Persistent memory is the Actor's Notes and authoritative server state, not a
        chat transcript.
        """
        if context is None:
            context = self._observe_context()
        # One logical activation owns this counter across both attempts of the model retry loop.
        # Reset before packet construction so the model sees the full new activation allowance.
        self._messages_sent_this_activation = 0
        self._successful_message_keys = set()
        self._surface.reset()
        packet = self.build_packet(context)
        self._surface.declare(packet.get("available_actions", []))
        self._activation_sequence += 1
        self._opportunity_namespace = "|".join(
            [
                *(str(context.get(name)) for name in ("turn", "phase", "active_actor_id")),
                str(self._activation_sequence),
            ]
        )
        prompt = self.build_activation_prompt(packet)

        # Per-activation ceilings enforced by PydanticAI's own usage-limit mechanism (agents.md
        # §18): a runaway single activation is stopped by the framework and surfaced as a
        # structured ActivationFailed, never an unbounded loop. PydanticAI enforces the limits
        # per run, so the transient retry below is handed only what the first attempt left
        # (I-077) — the ceilings bound the logical activation, not each ``run_sync``.
        limits = UsageLimits(
            request_limit=self._guardrails.max_llm_iterations,
            tool_calls_limit=self._guardrails.max_tool_calls,
        )

        # Exactly one retry, and only for a clearly transient failure (agents.md §18 step 2 /
        # TRD §35). The reference player runs through PydanticAI's own model loop, so "retry once"
        # is applied here by re-running the activation; the classification is shared with T-038's
        # OpenRouter client (D-018). A deterministic or guardrail failure is never retried.
        self._model.reset_metrics()
        run_kwargs: dict[str, Any] = {}
        # I-032: the phase-resolved inference budget, merged over the model's base settings for
        # this run only. Empty under the uniform profile.
        phase_settings = phase_model_settings(self._config, context.get("phase"))
        if phase_settings:
            run_kwargs["model_settings"] = phase_settings
        if self._single_shot:
            output = self._decision_output(packet)
            if output is not None:
                run_kwargs["output_type"] = output
        activation_started = time.perf_counter()
        causes: list[dict[str, Any]] = []
        for attempt in range(2):
            try:
                # Deliberately no message_history — ephemeral, bounded context every time.
                run = self._agent.run_sync(prompt, usage_limits=limits, **run_kwargs)
            except UsageLimitExceeded as exc:
                raise ActivationFailed(
                    f"activation ceiling hit for {self._actor_id!r}: {exc}",
                    category=FAIL_GUARDRAIL,
                    counts=self._ceiling_counts(activation_started),
                ) from exc
            except Exception as exc:  # a model/network failure raised by the run
                category = _classify_activation_error(exc)
                causes.append(_failure_cause(exc))
                if category == FAIL_TRANSIENT and attempt == 0:
                    logger.info(
                        "transient activation failure for %s (%s) — retrying once",
                        self._actor_id,
                        _cause_summary(causes[-1]),
                    )
                    # The retry is still the same logical activation. Re-read the same durable
                    # pending delivery so the packet exposes capacity remaining after any sends
                    # committed by the failed attempt; do not reset counters or the idempotency
                    # namespace. Single-shot has not executed mutations yet, but rebuilding keeps
                    # both modes on the same retry contract.
                    # I-077: the retry shares the activation-wide request/tool ceilings. Both
                    # counters already span the two attempts (``_TimedModel`` counts at the
                    # wrapped-request boundary; tool calls are counted per activation), so the
                    # remainder is exact. A run needs at least one request and one tool call;
                    # with either spent, this is the same ceiling failure the framework would
                    # raise — surfaced here, without a second run, as the player's own rule.
                    requests_used = self._model.metrics()[0]
                    tools_used = self._surface.tool_calls
                    requests_left = self._guardrails.max_llm_iterations - requests_used
                    tools_left = self._guardrails.max_tool_calls - tools_used
                    if requests_left < 1 or tools_left < 1:
                        raise ActivationFailed(
                            f"activation ceiling hit for {self._actor_id!r}: the transient "
                            "retry has no remaining request/tool allowance",
                            category=FAIL_GUARDRAIL,
                            counts=self._ceiling_counts(activation_started),
                        ) from exc
                    limits = UsageLimits(
                        request_limit=requests_left, tool_calls_limit=tools_left
                    )
                    packet = self.build_packet(context)
                    prompt = self.build_activation_prompt(packet)
                    if self._single_shot:
                        retry_output = self._decision_output(packet)
                        if retry_output is not None:
                            run_kwargs["output_type"] = retry_output
                    continue
                model_iterations, provider_wait, request_usage, request_topology = (
                    self._model.metrics()
                )
                raise ActivationFailed(
                    f"activation failed for {self._actor_id!r}: {_cause_summary(causes[-1])}",
                    category=category,
                    counts={
                        "attempts": attempt + 1,
                        "causes": causes,
                        "model_calls": model_iterations,
                        "provider_wait_ms": round(provider_wait * 1000),
                        "activation_wall_ms": round(
                            (time.perf_counter() - activation_started) * 1000
                        ),
                        "tool_calls": self._surface.tool_calls,
                        **self._surface.research_counts(),
                        "messages_sent": self._messages_sent_this_activation,
                        "usage": _accumulated_usage_dict(
                            model_iterations,
                            request_usage,
                            tool_calls=self._surface.tool_calls,
                        ),
                        "request_topology": request_topology,
                    },
                ) from exc
            activation_wall = time.perf_counter() - activation_started
            break

        model_iterations, provider_wait, request_usage, request_topology = self._model.metrics()
        accumulated_usage = _accumulated_usage_dict(
            model_iterations,
            request_usage,
            tool_calls=self._surface.tool_calls,
        )

        messages = list(run.all_messages())
        tool_call_names = [
            part.tool_name
            for message in messages
            for part in getattr(message, "parts", [])
            if isinstance(part, ToolCallPart)
        ]
        invalid_tool_call_breakdown = classify_invalid_tool_calls(messages)
        decision_data: dict[str, Any] | None = None
        if self._single_shot and "output_type" in run_kwargs:
            from agents import decisions

            decision = run.output
            # The output-tool call itself is not a game action; the executed actions are.
            tool_call_names = [n for n in tool_call_names if n != decisions.DECISION_TOOL_NAME]
            current = packet.get("current_argument") or {}
            report = decisions.execute_decision(
                decision,
                self._tools,
                argument_id=str(current["id"]) if current.get("id") is not None else None,
                key_fn=self._mutation_key,
            )
            tool_call_names.extend(report.actions)
            invalid_tool_call_breakdown = classify_invalid_tool_calls(
                messages,
                executed_rejections=report.rejected,
                decision_tool_name=decisions.DECISION_TOOL_NAME,
            )
            self._messages_sent_this_activation += report.messages_sent
            decision_data = decision.model_dump()
            self._staged_strategic_memory = decision.strategic_memory.model_dump()
        if self._delivery_receipt is None or self._staged_strategic_memory is None:
            raise ActivationFailed(
                f"activation delivery state missing for {self._actor_id!r}",
                category=FAIL_DETERMINISTIC,
                counts={
                    "model_calls": model_iterations,
                    "provider_wait_ms": round(provider_wait * 1000),
                    "tool_calls": self._surface.tool_calls,
                    **self._surface.research_counts(),
                    "messages_sent": self._messages_sent_this_activation,
                    "usage": accumulated_usage,
                    "request_topology": request_topology,
                },
            )
        acknowledged = self._tools.acknowledge_activation(
            self._delivery_receipt, self._staged_strategic_memory
        )
        if not acknowledged.ok:
            acknowledgement_reason = acknowledged.error_code or "unknown"
            if acknowledged.error_message:
                acknowledgement_reason += f": {acknowledged.error_message}"
            raise ActivationFailed(
                f"activation acknowledgement failed for {self._actor_id!r}: "
                f"{acknowledgement_reason}",
                category=FAIL_DETERMINISTIC,
                counts={
                    "model_calls": model_iterations,
                    "tool_calls": self._surface.tool_calls,
                    **self._surface.research_counts(),
                    "messages_sent": self._messages_sent_this_activation,
                    "provider_wait_ms": round(provider_wait * 1000),
                    "usage": accumulated_usage,
                    "request_topology": request_topology,
                },
            )
        return ActivationResult(
            activation_id=activation_id,
            output=run.output if isinstance(run.output, str) else str(run.output),
            tool_call_names=tool_call_names,
            model_requested=self._config.model,
            model_returned=_model_returned(messages),
            usage=accumulated_usage,
            latency_seconds=activation_wall,
            activation_wall_seconds=activation_wall,
            provider_wait_seconds=provider_wait,
            model_iterations=model_iterations,
            provider=self._provider,
            invalid_tool_calls=invalid_tool_call_breakdown["total"],
            invalid_tool_call_breakdown=invalid_tool_call_breakdown,
            request_topology=request_topology,
            messages=messages,
            messages_sent=self._messages_sent_this_activation,
            **self._surface.research_counts(),
            decision=decision_data,
        )


def _model_returned(messages: list[Any]) -> str | None:
    """The concrete model name a provider reported, if any (provenance for T-053)."""
    for message in reversed(messages):
        if isinstance(message, ModelResponse):
            name = getattr(message, "model_name", None)
            if name:
                return str(name)
    return None
