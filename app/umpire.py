"""The framework-neutral Umpire boundary (T-018, agents.md §21/§22, TRD §22).

The Umpire *proposes*; the server *resolves*. This module carries only the contract shared
between the two: the adjudication context the server builds, the structured proposal the Umpire
returns, the :class:`Umpire` protocol, and — separately — the optional provenance capability a
provider-backed Umpire adds (:class:`UmpireProvenanceSource`, emitting
:class:`UmpireAttemptRecord`; I-065). It is deliberately part of ``app/`` so the game
engine (``app.game_service``) depends only on app-local, framework-neutral code — it imports no
agent framework (AGENTS.md §6 / TRD §4). The concrete implementations live behind this
protocol: the deterministic :class:`agents.umpire.FakeUmpire` (offline, no provider) and, later,
a PydanticAI/OpenRouter AI Umpire (T-042). The engine never imports either directly — the caller
injects an :class:`Umpire` instance.

A provider-backed Umpire's provenance is *invocation-owned* (I-081, D-072): one call returns one
immutable :class:`UmpireAdjudication` carrying the proposal (or the failure that replaced it) and
exactly the attempts that produced it, so nothing is queued on the shared Umpire instance for a
later caller to drain and two concurrent adjudications can never interleave their records.

The proposal maps 1:1 onto :class:`app.models.AdjudicationRecord`. Crucially it carries **no
roll and no authoritative result** — the Umpire "does not perform or fabricate the roll"
(agents.md §26); the server draws and decides (INV-RNG-001, INV-EFFECT-002).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol, TypedDict, runtime_checkable

from app.models import AdjudicationMode

# One authoritative bound shared by the provider-facing proposal schema and the server's
# transactional validation. Keeping it at this framework-neutral boundary prevents the schema
# from accepting a proposal that the engine must reject solely because of branch size.
MAX_EFFECTS_PER_BRANCH = 10


class UmpireUnavailable(Exception):
    """The Umpire could not produce a usable adjudication and the game cannot continue.

    Framework-neutral on purpose: it is declared here in ``app/`` so the engine can catch it
    without importing any agent framework (TRD §4). A concrete :class:`Umpire` raises it when a
    model outage survives its bounded attempts, or when the model returns output that cannot be
    turned into a proposal that is usable against its authorised context. The server never
    fabricates an adjudication in response (TRD §35 "Do not invent an adjudication"):
    :func:`app.game_service.resolve_adjudication`
    catches this, pauses the game with **no** RNG draw, record or Effect, and surfaces it at the
    API as ``503 adjudicator_unavailable`` (api.md §3.1). It is deliberately *not* a
    ``GameServiceError`` — it is a signal from the Umpire boundary, mapped to the authoritative
    domain error by the service.
    """


@dataclass(frozen=True)
class AdjudicationContext:
    """The authorised information the server hands the Umpire (agents.md §21).

    Built entirely by the server; a player model never chooses what the Umpire sees
    (agents.md §21). Per D-029 the server fills ``relevant_hidden_information`` with every
    Actor's private facts and every active private Effect, each as
    ``{"kind", "actor_id", "text"}`` tagged by owner. Adjudicated Effects are public, so the
    Umpire prompt forbids restating any of this in its Effects or rationale.
    """

    scenario_summary: str
    turn: int
    turn_length: str | None
    argument: dict[str, Any]
    cons: list[dict[str, Any]]
    relevant_effects: list[dict[str, Any]]
    relevant_hidden_information: list[dict[str, Any]] = field(default_factory=list)
    # I-046 / D-060: the owner's recorded reply under ``visible_reply`` — a list of
    # ``{"con_id", "con_actor_id", "text"}`` (empty for a recorded pass). ``None`` under
    # ``sealed_cons`` or when the reply phase was skipped, so the released rendering is
    # byte-identical.
    owner_reply: list[dict[str, Any]] | None = None
    # T-073 / D-090: the Game's snapshotted scenario rules as ``{"id", "text"}`` in declaration
    # order, identical on every adjudication. ``None`` when the scenario declares none, so the
    # rule-free rendering stays byte-identical to the released one.
    scenario_rules: list[dict[str, str]] | None = None


@dataclass(frozen=True)
class AdjudicationProposal:
    """The Umpire's structured proposal (agents.md §22, TRD §22).

    ``mode`` is one of the three :class:`~app.models.AdjudicationMode` values (INV-ADJ-001).
    ``probability`` is required for ``probabilistic`` and ``None`` for the automatic modes
    (INV-ADJ-002) — the server validates this. ``success_effects``/``failure_effects`` are plain
    description strings (the simplest structured shape the spec uses); the server materialises
    only the selected branch into authoritative Effects. The two supersession lists name prior
    Effects to deactivate only when their corresponding branch is selected (D-044). There is no
    roll or success field: the Umpire cannot decide the outcome (agents.md §26,
    INV-EFFECT-002).
    """

    mode: AdjudicationMode
    rationale: str
    probability: float | None = None
    success_effects: list[str] = field(default_factory=list)
    failure_effects: list[str] = field(default_factory=list)
    success_supersedes_effect_ids: list[uuid.UUID] = field(default_factory=list)
    failure_supersedes_effect_ids: list[uuid.UUID] = field(default_factory=list)


@runtime_checkable
class Umpire(Protocol):
    """The minimal adjudication boundary: one synchronous, side-effect-free proposal call.

    An implementation receives the already-authorised :class:`AdjudicationContext` and returns
    an :class:`AdjudicationProposal`. It must not touch the database, perform RNG, create
    Effects or advance the game — those are the server's authoritative responsibilities
    (state-machine §19, INV-EFFECT-001/002, INV-RNG-001). Kept synchronous because the in-process
    FakeUmpire needs no async; a networked AI Umpire (T-042) can adapt behind the same boundary.
    """

    def adjudicate(self, context: AdjudicationContext) -> AdjudicationProposal: ...


class UmpireAttemptRecord(TypedDict):
    """One normalised Umpire model attempt, as the Umpire boundary emits it (I-065, D-065).

    Provider-shaped usage becomes canonical *before* it crosses this seam: the record already
    carries the five inclusive token buckets, the call and billing counts, and the framework
    identity that only the Umpire knows. It deliberately carries **no** server-owned context —
    ``actor_id``, ``role``, ``turn`` and ``phase`` are the server's to add — and no
    authoritative outcome: the server may still overlay ``completed``/``failure_reason`` when
    its own resolution fails after the provider call succeeded. A plain ``dict`` at runtime,
    so a queued record can be marked unusable in place (D-064) before it is drained.
    """

    model_requested: str
    model_returned: str | None
    provider: str | None
    prompt_version: str
    prompt_hash: str
    model_parameters: dict[str, Any]
    reasoning_configuration: dict[str, Any] | None
    agent_framework: str
    agent_framework_version: str | None
    tool_schema_version: str
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    model_calls: int
    costed_model_calls: int
    tool_calls: int
    invalid_tool_calls: int
    stalls: int
    cost_usd: float | None
    latency_ms: int
    activation_wall_ms: int
    completed: bool
    failure_reason: str | None
    provider_metadata: dict[str, Any]


@dataclass(frozen=True)
class UmpireAdjudication:
    """One adjudication call's correlated, immutable result (I-081, D-072).

    ``proposal`` is the structured proposal, or ``None`` when the call could not produce a usable
    one — then ``failure`` names why, in the Umpire's safe operational vocabulary (never model
    content), and every attempt already carries ``completed=False`` with that reason. ``attempts``
    are exactly the model attempts this call made, in order, each an
    :class:`UmpireAttemptRecord`; a recovered retry leaves its failed predecessor in place. The
    server persists them with the context only it knows and overlays its *own* resolution
    failure when one follows a usable proposal.
    """

    proposal: AdjudicationProposal | None
    attempts: tuple[UmpireAttemptRecord, ...]
    failure: str | None


@runtime_checkable
class UmpireProvenanceSource(Protocol):
    """The optional, atomic provenance capability of an Umpire (I-065, D-065; I-081, D-072).

    Separate from :class:`Umpire` on purpose: adjudication is the contract every Umpire must
    honour; provenance is what a *provider-backed* Umpire additionally reports so the server can
    persist one append-only ``AgentRun`` per model attempt (D-039) and seal the Umpire identity
    into the run manifest (agents.md §27). ``adjudicate_recorded`` is the provenance-carrying
    form of :meth:`Umpire.adjudicate`: it never raises for unavailability — the result says so —
    and it owns its records, so the capability has no shared queue and no "last" state. The
    server checks for the capability with one ``isinstance`` and records nothing when it is
    absent. An implementation provides both members or none — a partial duck type is not a
    provenance source.
    """

    def provenance_configuration(self) -> dict[str, Any]: ...

    def adjudicate_recorded(self, context: AdjudicationContext) -> UmpireAdjudication: ...


class RecordedUmpire:
    """One adjudication's :class:`Umpire` over a provenance source (I-081, D-072).

    The engine's :func:`~app.game_service.resolve_adjudication` sees the plain contract — a
    proposal or :class:`UmpireUnavailable` — while the correlated :class:`UmpireAdjudication`
    stays on this object for the caller that owns the adjudication (the advance route) to
    persist. Single use by construction: one instance, one adjudication, one result — never a
    queue on the shared Umpire that a concurrent adjudication could drain.
    """

    def __init__(self, source: UmpireProvenanceSource) -> None:
        self._source = source
        self.result: UmpireAdjudication | None = None

    def adjudicate(self, context: AdjudicationContext) -> AdjudicationProposal:
        if self.result is not None:
            raise RuntimeError("a RecordedUmpire adjudicates exactly once")
        self.result = self._source.adjudicate_recorded(context)
        if self.result.proposal is None:
            raise UmpireUnavailable(self.result.failure or "Umpire unavailable")
        return self.result.proposal
