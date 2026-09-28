"""The framework-neutral Consequence resolver boundary (T-074, D-091, agents.md §28).

The resolver *proposes*; the server *validates and commits*. Like :mod:`app.umpire`, this module
carries only the contract the two share:

- the public-only context the server builds (:class:`ConsequenceContext`);
- the structured proposal the resolver returns (:class:`ConsequenceProposal`);
- the :class:`ConsequenceResolver` protocol and the :class:`ConsequenceResolverUnavailable`
  signal;
- separately, the optional provenance capability a provider-backed resolver adds
  (:class:`ConsequenceProvenanceSource`), which emits the same normalised attempt records as the
  Umpire.

It imports no agent framework and no provider client (AGENTS.md §6 / TRD §4). The concrete
resolvers — the deterministic :class:`agents.consequence.FakeConsequenceResolver` and the
OpenRouter-backed :class:`agents.consequence.AIConsequenceResolver` — live in ``agents/`` and are
injected by the caller.

A proposal carries **no** authority: the resolver never touches the database, never advances the
game and owns no randomness (state-machine §21, INV-CONSEQ-004, INV-EFFECT-001/002). Every
reference in it is revalidated by :func:`app.game_service.resolve_consequences` inside the write
transaction, and any violation rejects the whole proposal (INV-CONSEQ-002/003).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from app.umpire import UmpireAttemptRecord

# agents.md §28.2 / state-machine INV-CONSEQ-002: the bounds, shared by the provider-facing schema
# and the server's transactional validation so the schema never admits what the server rejects.
MAX_CONSEQUENCE_EFFECTS = 3
MAX_EVIDENCE_REFERENCES = 8
MAX_CONSEQUENCE_TEXT_CHARS = 4000
# INV-CONSEQ-003: the only citable Event type. Its payload is public (api.md §13.1) and names the
# Argument whose authoritative outcome it records.
CITABLE_EVENT_TYPE = "argument_adjudicated"
EVIDENCE_KIND_EVENT = "event"
EVIDENCE_KIND_EFFECT = "effect"
EVIDENCE_KINDS = (EVIDENCE_KIND_EVENT, EVIDENCE_KIND_EFFECT)


class ConsequenceResolverUnavailable(Exception):
    """The resolver could not produce a usable proposal (agents.md §28, D-091).

    Framework-neutral, like :class:`app.umpire.UmpireUnavailable`. A concrete resolver raises it
    when a model outage or unusable output survives its bounded attempts. The server never
    fabricates or skips the pass in response: :func:`app.game_service.resolve_consequences`
    pauses the game in ``consequences`` with nothing committed.
    """


@dataclass(frozen=True)
class ConsequenceContext:
    """The public-only context the server hands the resolver (agents.md §28.1).

    ``outcomes`` has one entry per current-turn ``argument_adjudicated`` Event, in event order:
    ``event_id``, ``actor_id``, ``action``, ``desired_effect``, ``mode``, ``success`` and
    ``created_effect_ids``. ``active_effects`` lists the active **public** Effects as ``id``,
    ``description`` and ``created_turn``. No Umpire rationale, probability, roll, Con, reply,
    private information, private Effect, Message or Resource content is ever included.
    ``scenario_rules`` is ``None`` for a rule-free scenario, so its key is absent when rendered.
    """

    scenario_summary: str
    turn: int
    turn_length: str | None
    outcomes: list[dict[str, Any]]
    active_effects: list[dict[str, Any]]
    scenario_rules: list[dict[str, str]] | None = None

    def as_payload(self) -> dict[str, Any]:
        """The §28.1 JSON object, exactly what a provider-backed resolver sends."""
        payload: dict[str, Any] = {
            "scenario_summary": self.scenario_summary,
            "turn": self.turn,
            "turn_length": self.turn_length,
            "outcomes": self.outcomes,
            "active_effects": self.active_effects,
        }
        if self.scenario_rules is not None:
            payload["scenario_rules"] = self.scenario_rules
        return payload


@dataclass(frozen=True)
class ProposedConsequence:
    """One proposed Effect: text plus the authoritative evidence it derives from (§28.2).

    ``evidence`` is kept as the raw ``{"kind", "id"}`` mappings the resolver produced. It is the
    server's job, not this type's, to prove each one resolves, so a malformed reference reaches
    validation and is rejected there instead of being silently repaired.
    """

    description: str
    rationale: str
    evidence: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class ConsequenceProposal:
    """The resolver's whole proposal: 0–3 Effects, accepted or rejected as one (§28.2)."""

    effects: list[ProposedConsequence] = field(default_factory=list)


@runtime_checkable
class ConsequenceResolver(Protocol):
    """The minimal consequence boundary: one synchronous, side-effect-free proposal call.

    An implementation receives the server-built :class:`ConsequenceContext` and returns a
    :class:`ConsequenceProposal`, or raises :class:`ConsequenceResolverUnavailable`. It must not
    touch the database, draw randomness, create Effects or advance the game.
    """

    def propose(self, context: ConsequenceContext) -> ConsequenceProposal: ...


@dataclass(frozen=True)
class ConsequenceResolution:
    """One resolver call's correlated, immutable result (the I-081 pattern, D-091).

    ``proposal`` is ``None`` when the call produced no usable proposal; ``failure`` then names why,
    in safe operational vocabulary. ``attempts`` are exactly the model attempts this call made, as
    :class:`~app.umpire.UmpireAttemptRecord` mappings: the Umpire's normalised record shape.
    """

    proposal: ConsequenceProposal | None
    attempts: tuple[UmpireAttemptRecord, ...]
    failure: str | None


@runtime_checkable
class ConsequenceProvenanceSource(Protocol):
    """The optional provenance capability of a resolver (the I-065/I-081 pattern, D-091).

    ``propose_recorded`` never raises for unavailability: the result says so. The server persists
    one ``AgentRun`` per attempt (``role = "consequence"``) and seals
    :meth:`provenance_configuration` into the run manifest (agents.md §27).
    """

    def provenance_configuration(self) -> dict[str, Any]: ...

    def propose_recorded(self, context: ConsequenceContext) -> ConsequenceResolution: ...


class RecordedConsequenceResolver:
    """One pass's :class:`ConsequenceResolver` over a provenance source (the I-081 pattern).

    The engine sees the plain contract, a proposal or :class:`ConsequenceResolverUnavailable`,
    while the correlated :class:`ConsequenceResolution` stays here for the caller that owns the
    pass (the advance route) to persist. It is single use: one instance, one pass.
    """

    def __init__(self, source: ConsequenceProvenanceSource) -> None:
        self._source = source
        self.result: ConsequenceResolution | None = None

    def propose(self, context: ConsequenceContext) -> ConsequenceProposal:
        if self.result is not None:
            raise RuntimeError("a RecordedConsequenceResolver proposes exactly once")
        self.result = self._source.propose_recorded(context)
        if self.result.proposal is None:
            raise ConsequenceResolverUnavailable(
                self.result.failure or "consequence resolver unavailable"
            )
        return self.result.proposal
