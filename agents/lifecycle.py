"""The agent lifecycle loop (T-040, agents.md §3).

The orchestration around a :class:`~agents.player.ReferencePlayer`:

    observe → (Python decides whether input is required) → invoke the model only if so →
    allow bounded tool use during that activation → re-observe → repeat.

Two disciplines make this correct and cheap:

- **Python gates the model.** The server already publishes ``requires_input`` and
  ``available_actions`` (API-INV-009), so a deterministic check — not a wasted model call —
  decides whether the Actor needs to act. A waiting Actor (another Actor's turn, or
  adjudication) polls; it never spends a model call to discover it has nothing to do.
- **The server stays authoritative.** The loop re-observes after every activation and reacts to
  whatever state it finds, so it follows phase changes without reimplementing the state machine,
  and a stale/recoverable tool rejection simply flows back into the next observation rather than
  corrupting any local state (the previous context is never assumed still current).

**Guardrails and stall recovery (T-041, agents.md §18 / TRD §35).** A player that fails, stalls,
hits a ceiling or crashes must never hang the game. This runner:

- bounds each activation through the player's own :class:`GuardrailConfig` (iteration/tool-call
  ceilings via PydanticAI usage limits, applied inside ``activate``);
- tracks Messages sent per ``(turn, phase)`` and hands the player shrinking phase capacity; the
  player combines it with I-033's normal per-activation Message budget;
- detects a **no-progress stall** — the converted ``max_activations`` seam: repeated activations
  that never clear the current participation opportunity;
- on any of failure / stall / ceiling / crash, **records a structured** :class:`AgentFailure`
  (persisted by T-053's coordinator integration) and takes the *legal minimal completion* for the
  current state — ``ready`` in diplomacy, ``pass_con`` in Cons — chosen from the server's current
  ``available_actions``, never assumed. A guardrail/transient failure on a required Argument or
  Reply gets fresh activations only while the existing opportunity bound has room. Deterministic
  or exhausted failures yield; the runner **never fabricates an Argument or Reply** (agents.md §2).

The server stays authoritative throughout: recovery advances state only by calling a documented
player tool the server currently lists as legal (API-INV-008/009).

Boundary (AGENTS.md §6 / TRD §4): this module drives the game only through the player's HTTP
tools. It imports no ``app`` internals and no engine/persistence/visibility code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

from agents.player import (
    FAIL_GUARDRAIL,
    FAIL_TRANSIENT,
    ActivationFailed,
    ActivationResult,
    PlayerError,
    ReferencePlayer,
)

_STATUS_COMPLETE = "complete"

# An absolute backstop so a pathological driver loop still terminates. Real bounding is the
# per-opportunity stall detector plus the player's own per-activation ceilings (agents.md §18).
_DEFAULT_MAX_ACTIVATIONS = 500

# Recovery categories added by the runner on top of the player's FAIL_* activation categories.
FAIL_STALL = "stall"  # repeated activations never cleared the opportunity (no progress)

_ACTION_READY = "ready"
_ACTION_PASS_CON = "pass_con"
_RETRYABLE_REQUIRED_ACTIONS = {"make_argument", "reply_to_cons"}


class ActivationLimitExceeded(RuntimeError):
    """The absolute activation backstop was hit — recovery is the normal path; this is last resort."""


@dataclass(frozen=True)
class AgentFailure:
    """A recorded player failure (agents.md §18 step 3 / TRD §35 / testing.md §20).

    Held briefly on the runner; T-053 persists and exports it through the admin provenance API.
    ``category`` is a player ``FAIL_*`` value or :data:`FAIL_STALL`; ``recovery_action`` is the
    legal tool the runner took to keep the game moving (``ready``/``pass_con``) or ``None`` when no
    legal completion existed and the runner yielded (never a fabricated Argument).
    """

    category: str
    turn: int | None
    phase: str | None
    actor_id: str
    detail: str
    activations: int
    recovery_action: str | None
    counts: dict[str, Any] = field(default_factory=dict)
    # The Argument a recovery ``pass_con`` closed, so the pass can be told apart from a chosen
    # one in the run summary (I-150, D-105); ``None`` for ``ready`` and for no recovery.
    recovery_argument_id: str | None = None


@dataclass(frozen=True)
class CycleOutcome:
    """What one :meth:`LifecycleRunner.step` did.

    ``acted`` is ``True`` only when the model was actually invoked *and succeeded*. ``activation``
    carries the :class:`ActivationResult` in that case, else ``None``. ``failure`` is set when the
    activation failed/stalled and the runner recorded it (and possibly recovered). ``context`` is
    the authoritative context the decision was made from.
    """

    acted: bool
    status: str | None
    phase: str | None
    requires_input: bool
    activation: ActivationResult | None
    context: dict[str, Any]
    failure: AgentFailure | None = None
    # True only when the runner recorded an unrecoverable/exhausted failure for the *current*
    # opportunity and found no legal completion for it: the game is waiting on this Actor and the
    # runner will not fabricate the decision. A bounded required-decision retry, or a failure whose
    # opportunity has already moved on, is recorded but never ``stuck`` (I-025/I-069).
    stuck: bool = False


def opportunity_key(context: dict[str, Any]) -> tuple[Any, ...]:
    """The participation opportunity a context describes: (turn, phase, active arguer[, round]).

    D-094: under round-robin Argument rounds the same Actor is active in ``argument`` once per
    round of a turn, so the server-reported round is part of the key — a round-2 Argument is a new
    opportunity, never a continuation of round 1's stall count. At budget 1 the context carries no
    ``argument_budget`` and the key is the released 3-tuple.
    """
    key: tuple[Any, ...] = (
        context.get("turn"),
        context.get("phase"),
        context.get("active_actor_id"),
    )
    budget = context.get("argument_budget")
    if isinstance(budget, dict):
        key += (budget.get("round"),)
    return key


class LifecycleRunner:
    """Drives one :class:`ReferencePlayer` in reaction to authoritative server state."""

    def __init__(
        self,
        player: ReferencePlayer,
        *,
        max_activations: int = _DEFAULT_MAX_ACTIVATIONS,
        sleep: Callable[[], None] | None = None,
    ) -> None:
        self._player = player
        self._max_activations = max_activations
        self._sleep = sleep
        self._activations = 0
        self._failures: list[AgentFailure] = []
        # Messages sent by this Actor within a given (turn, phase), tracked across activations so
        # the per-phase cap holds even though each activation starts fresh (agents.md §18).
        self._messages_per_phase: dict[tuple[Any, Any], int] = {}
        # No-progress stall detection: how many activations the current participation opportunity
        # has consumed, and which opportunity that is. Reset when the opportunity changes.
        self._opportunity_key: tuple[Any, ...] | None = None
        self._opportunity_activations = 0
        # An opportunity the runner could not legally complete after bounded retries: recorded so
        # the single-Actor run() loop terminates instead of re-observing it forever.
        self._unrecoverable_key: tuple[Any, ...] | None = None

    @property
    def player(self) -> ReferencePlayer:
        return self._player

    @property
    def activations(self) -> int:
        """How many model activations this runner has performed."""
        return self._activations

    @property
    def failures(self) -> list[AgentFailure]:
        """Structured failures recorded so far — provenance for T-053 (agents.md §18/§19)."""
        return list(self._failures)

    def observe(self) -> dict[str, Any]:
        """Read this Actor's authoritative context over HTTP (raises on an unrecoverable read)."""
        result = self._player.tools.observe()
        if not result.ok or result.data is None:
            raise PlayerError(
                f"lifecycle could not observe context: {result.error_code or 'no data'}"
            )
        return result.data

    def step(self, context: dict[str, Any] | None = None) -> CycleOutcome:
        """One observe → (maybe) activate → (maybe) recover cycle.

        Python decides from ``requires_input`` whether to invoke the model: the game is complete,
        or the Actor is waiting → no model call. Otherwise one fresh, bounded activation, guarded
        by the independent activation/phase Message caps and the no-progress stall detector, with
        failure recorded and legally recovered so a misbehaving player cannot hang the game
        (agents.md §18 / TRD §35).

        ``context`` lets a coordinator that has just observed this Actor (to decide *how* to
        schedule the step — I-029) hand that authoritative read over instead of paying a second one.
        """
        if context is None:
            context = self.observe()
        status = context.get("status")
        phase = context.get("phase")
        turn = context.get("turn")
        requires_input = bool(context.get("requires_input"))

        if status == _STATUS_COMPLETE or not requires_input:
            if not requires_input and status != _STATUS_COMPLETE:
                self._wait()  # nothing for this Actor now — poll, do not spend a model call
            # The opportunity cleared (or the game ended): the next one starts fresh.
            self._opportunity_key = None
            self._opportunity_activations = 0
            return CycleOutcome(
                acted=False,
                status=status,
                phase=phase,
                requires_input=False,
                activation=None,
                context=context,
            )

        # A participation opportunity is keyed by (turn, phase, active arguer) so a stall is
        # detected per opportunity, not across the whole game.
        opportunity = opportunity_key(context)
        if opportunity != self._opportunity_key:
            self._opportunity_key = opportunity
            self._opportunity_activations = 0

        g = self._player.guardrails

        # No-progress stall: the converted max_activations seam. Repeated activations never cleared
        # this opportunity → record a stall and take the legal minimal completion.
        if self._opportunity_activations >= g.max_activations_per_opportunity:
            return self._recover(
                context,
                category=FAIL_STALL,
                detail=(
                    f"no progress after {self._opportunity_activations} activations in "
                    f"{phase} (turn {turn})"
                ),
                counts={"activations": self._opportunity_activations},
            )

        # Per-phase message cap (agents.md §18): tell the player how many Messages remain this
        # (turn, phase), tracked across activations. The player enforces it in send_message.
        sent = self._messages_per_phase.get((turn, phase), 0)
        self._player.set_message_budget(max(0, g.max_messages_per_phase - sent))

        self._activations += 1
        self._opportunity_activations += 1
        if self._activations > self._max_activations:  # absolute backstop, normally unreachable
            raise ActivationLimitExceeded(
                f"absolute activation backstop {self._max_activations} hit for "
                f"{self._player.actor_id!r}"
            )

        try:
            activation = self._player.activate(context)
        except ActivationFailed as exc:
            # I-022: tools can commit before the model later hits a ceiling. Charge those
            # Messages to the phase in which this activation began before recovery observes a
            # possibly newer phase/opportunity.
            failed_messages = exc.counts.get("messages_sent", 0)
            if isinstance(failed_messages, int) and failed_messages > 0:
                self._messages_per_phase[(turn, phase)] = sent + failed_messages
            # A model failure/crash after its one retry, or a per-activation ceiling breach.
            # Recover from the server's *current* state, never the pre-activation snapshot: the
            # failed activation may already have completed the opportunity (it called `ready`,
            # the phase closed, and the model kept going until it hit a ceiling) — a stale
            # snapshot would re-run a completion that is now illegal and call it a stall (I-025).
            return self._recover(
                self.observe(),
                category=exc.category,
                detail=exc.detail,
                counts=exc.counts,
            )
        finally:
            self._player.set_message_budget(None)  # never leak a budget into a later direct call

        self._messages_per_phase[(turn, phase)] = sent + activation.messages_sent
        return CycleOutcome(
            acted=True,
            status=status,
            phase=phase,
            requires_input=True,
            activation=activation,
            context=context,
        )

    def _recover(
        self,
        context: dict[str, Any],
        *,
        category: str,
        detail: str,
        counts: dict[str, Any],
    ) -> CycleOutcome:
        """Record the failure and take the legal minimal completion (agents.md §18 / TRD §35).

        The recovery action is chosen from the server's *current* ``available_actions`` — never
        assumed: ``ready`` in diplomacy, ``pass_con`` in Cons. If neither is legal (a failing
        active arguer, whose only advancing action would be a fabricated Argument — forbidden), the
        runner may schedule a fresh bounded activation for a required Argument/Reply; otherwise it
        records and yields. Recovery advances state only through a documented tool the server
        lists as legal (API-INV-008/009), and a retry never fabricates the decision.
        """
        actions = context.get("available_actions", [])
        turn = context.get("turn")
        phase = context.get("phase")
        requires_input = bool(context.get("requires_input"))
        current = opportunity_key(context)
        recovery_action: str | None = None
        recovery_argument_id: str | None = None

        if not requires_input or current != self._opportunity_key:
            # The failed opportunity is already cleared — the activation completed it before
            # failing, or the phase moved on. Record the failure, take nothing, and hand back the
            # current state: if it is a *new* opportunity it gets fresh activations of its own.
            failure = AgentFailure(
                category=category,
                turn=turn,
                phase=phase,
                actor_id=self._player.actor_id,
                detail=detail,
                activations=self._opportunity_activations,
                recovery_action=None,
                counts=dict(counts),
            )
            self._failures.append(failure)
            self._opportunity_key = None
            self._opportunity_activations = 0
            return CycleOutcome(
                acted=False,
                status=context.get("status"),
                phase=phase,
                requires_input=requires_input,
                activation=None,
                context=context,
                failure=failure,
            )

        if _ACTION_READY in actions:
            result = self._player.tools.ready(
                idempotency_key=self._recovery_key(context, _ACTION_READY, {})
            )
            if result.ok:
                recovery_action = _ACTION_READY
        elif _ACTION_PASS_CON in actions:
            argument = context.get("current_argument") or {}
            argument_id = argument.get("id")
            if argument_id is not None:
                payload = {"argument_id": str(argument_id)}
                result = self._player.tools.pass_con(
                    argument_id=str(argument_id),
                    idempotency_key=self._recovery_key(context, _ACTION_PASS_CON, payload),
                )
                if result.ok:
                    recovery_action = _ACTION_PASS_CON
                    recovery_argument_id = str(argument_id)

        failure = AgentFailure(
            category=category,
            turn=turn,
            phase=phase,
            actor_id=self._player.actor_id,
            detail=detail,
            activations=self._opportunity_activations,
            recovery_action=recovery_action,
            counts=dict(counts),
            recovery_argument_id=recovery_argument_id,
        )
        self._failures.append(failure)

        if recovery_action is not None:
            # The opportunity has been legally completed — the next observe starts a fresh one.
            self._opportunity_key = None
            self._opportunity_activations = 0
        retry_required_decision = (
            recovery_action is None
            and category in {FAIL_GUARDRAIL, FAIL_TRANSIENT}
            and bool(_RETRYABLE_REQUIRED_ACTIONS.intersection(actions))
            and self._opportunity_activations
            < self._player.guardrails.max_activations_per_opportunity
        )
        if recovery_action is None and not retry_required_decision:
            # No legal completion and no bounded fresh attempt remains. Never fabricate the
            # required decision; mark the opportunity so the coordinator stops with provenance.
            self._unrecoverable_key = opportunity_key(context)

        return CycleOutcome(
            acted=False,
            status=context.get("status"),
            phase=phase,
            requires_input=True,
            activation=None,
            context=context,
            failure=failure,
            stuck=recovery_action is None and not retry_required_decision,
        )

    def _recovery_key(
        self, context: dict[str, Any], operation: str, payload: dict[str, Any]
    ) -> str:
        """Stable key for replaying one legal recovery in one opportunity."""
        import hashlib
        import json

        namespace = [context.get("turn"), context.get("phase"), context.get("active_actor_id")]
        raw = json.dumps([namespace, operation, payload], sort_keys=True, separators=(",", ":"))
        return f"amaw_recovery_{hashlib.sha256(raw.encode()).hexdigest()}"

    def run(self, *, max_cycles: int = 1000) -> list[CycleOutcome]:
        """Step until the game completes or ``max_cycles`` is reached (single-Actor convenience).

        Returns every :class:`CycleOutcome`. Multi-Actor orchestration in a real deployment is
        simply several runners stepping independently; this helper is for the single-Actor case.
        """
        outcomes: list[CycleOutcome] = []
        for _ in range(max_cycles):
            outcome = self.step()
            outcomes.append(outcome)
            if outcome.status == _STATUS_COMPLETE:
                break
            # A deterministic or repeatedly failing required decision has no legal automatic
            # completion; stop re-observing the same exhausted opportunity forever. Bounded fresh
            # retries have already been spent and every failure was recorded.
            if outcome.stuck:
                break
        return outcomes

    def _wait(self) -> None:
        if self._sleep is not None:
            self._sleep()
