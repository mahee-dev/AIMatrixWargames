"""The authoritative game state-machine service — the **one** owner of every phase transition.

Callers (the API routes, the runner, tests) drive a game exclusively through here; no
transition logic lives in endpoints, agents, model methods, the visibility service or the CLI.
Every state-changing operation runs through :func:`_run_idempotent` →
:func:`app.persistence.apply_guarded`, which re-reads the authoritative
:class:`~app.models.Game` and evaluates the guard **inside the transaction** that writes the
mutation (state-machine §25, INV-GAME-004/005), so a mutation and the transition it triggers
commit together or not at all. Randomness is server-owned and restart-safe (:mod:`app.rng`,
INV-RNG-001…004); an Umpire only *proposes* ``mode`` + ``probability`` (INV-ADJ-003). Effects
are append-only history — expiry and supersession deactivate, never delete (INV-EFFECT-004,
D-010). Every mutation emits its §24 events via :func:`_emit_event` in the same transaction;
events are audit only and never drive state. Visibility is owned solely by
:mod:`app.visibility`. ``app/`` imports no agent framework (AGENTS.md §6).
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Engine
from sqlmodel import Session, select

from app import game_queries, persistence, rng
from app.consequences import (
    CITABLE_EVENT_TYPE,
    EVIDENCE_KIND_EFFECT,
    EVIDENCE_KIND_EVENT,
    MAX_CONSEQUENCE_EFFECTS,
    MAX_CONSEQUENCE_TEXT_CHARS,
    MAX_EVIDENCE_REFERENCES,
    ConsequenceContext,
    ConsequenceProposal,
    ConsequenceResolver,
    ConsequenceResolverUnavailable,
    ProposedConsequence,
)
from app.database import session_scope
# The errors and result values live in their own modules; every name is re-exported here.
from app.game_errors import (  # noqa: F401 — re-exported
    ActorNotInGame,
    AdjudicatorUnavailable,
    AlreadyAdjudicated,
    AlreadyReady,
    ArgumentBudgetExhausted,
    ArgumentNotCurrent,
    ConChallengerNotSelected,
    ConLimitReached,
    ConParticipationComplete,
    ConsequencesAlreadyResolved,
    ConsequencesUnavailable,
    GameNotFound,
    GameServiceError,
    IdempotencyConflict,
    IllegalStart,
    InvalidAdjudication,
    InvalidArgument,
    InvalidCon,
    InvalidConsequenceProposal,
    InvalidDiplomacyRound,
    InvalidMessage,
    InvalidNote,
    InvalidPhase,
    InvalidRecipient,
    InvalidReply,
    InvalidSupersession,
    NotActiveActor,
    NotArgumentOwner,
    ReplyAlreadySubmitted,
    SelfConForbidden,
)
from app.game_results import (  # noqa: F401 — re-exported
    AdjudicationOutcome,
    AdvanceResult,
    ArgumentResult,
    ConResult,
    ConsequenceOutcome,
    DiplomacyRoundResult,
    MessageResult,
    NoteResult,
    PassConResult,
    ReadyResult,
    ReplyResult,
    StartResult,
)
from app.game_queries import eligible_con_actor_ids, revealed_cons
from app.models import (
    Actor,
    AdjudicationMode,
    AdjudicationRecord,
    Argument,
    ArgumentReply,
    Con,
    ConParticipation,
    ConsequenceRecord,
    Effect,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    IdempotencyRecord,
    Message,
    Note,
    RunManifest,
    Seat,
)
from app.umpire import (
    MAX_EFFECTS_PER_BRANCH,
    AdjudicationContext,
    AdjudicationProposal,
    Umpire,
    UmpireUnavailable,
)

_ARGUMENT_OPEN_FOR_CONS = "open_for_cons"
_ARGUMENT_AWAITING_REPLY = "awaiting_reply"  # I-046: Cons frozen, owner reply pending (§18.1)
_ARGUMENT_ADJUDICATING = "adjudicating"
_ARGUMENT_RESOLVED = "resolved"
_UNRESOLVED_ARGUMENT_STATUSES = (
    "submitted",  # model-era pre-Cons value; fail closed if one survives in authoritative state
    _ARGUMENT_OPEN_FOR_CONS,
    _ARGUMENT_AWAITING_REPLY,
    _ARGUMENT_ADJUDICATING,
)
CON_REPLY_MODE_SEALED = "sealed_cons"
CON_REPLY_MODE_VISIBLE = "visible_reply"

MAX_TEXT_CHARS = 4000
MAX_ARGUMENT_CHARS = 2000
MAX_PRO_CHARS = 1000
MAX_IDEMPOTENCY_KEY_CHARS = 128
_MAX_PROS = 5
# Effects created by adjudication default to public visibility: the outcome of a resolved
# primary Argument is public knowledge, and the contract forbids inventing hidden Effects
# implicitly (the Umpire proposal carries description text only, not a visibility field).
_ADJUDICATION_EFFECT_VISIBILITY = "public"

# Authoritative event types (TRD §7.8, state-machine §24). Only the ones for engine/player
# operations implemented through T-021 are emitted; `agent_error` waits for the agent layer.
_EVENT_GAME_STARTED = "game_started"
_EVENT_PHASE_STARTED = "phase_started"
_EVENT_MESSAGE_SENT = "message_sent"
_EVENT_ARGUMENT_SUBMITTED = "argument_submitted"
_EVENT_CON_SUBMITTED = "con_submitted"
_EVENT_CON_PASSED = "con_passed"  # §24 "con_submitted / con_passed"
# I-055 / D-053: the sealed challenger selection, emitted between ``argument_submitted`` and
# ``phase_started(cons)`` (state-machine §17 step 3, "initialize Con participation state").
_EVENT_CON_CHALLENGERS_SELECTED = "con_challengers_selected"
# I-046 / D-060 (state-machine §18/§18.1): the simultaneous reveal and the owner's single reply.
_EVENT_CONS_REVEALED = "cons_revealed"
_EVENT_OWNER_REPLY_SUBMITTED = "owner_reply_submitted"
_CON_MODE_SAMPLED_SEALED = "sampled_sealed"
_EVENT_ARGUMENT_ADJUDICATED = "argument_adjudicated"
_EVENT_EFFECT_CREATED = "effect_created"
_EVENT_EFFECT_DEACTIVATED = "effect_deactivated"
_EVENT_ACTOR_READY = "actor_ready"
_EVENT_DIPLOMACY_ROUND_COMPLETED = "diplomacy_round_completed"
_EVENT_DIPLOMACY_CLOSED = "diplomacy_closed"
_EVENT_TURN_COMPLETED = "turn_completed"
_EVENT_ARGUMENT_ROUND_STARTED = "argument_round_started"  # D-094: budget > 1 only
_EVENT_GAME_COMPLETED = "game_completed"
# Emitted when an Umpire outage pauses the game instead of fabricating an adjudication (T-042,
# TRD §35). Not part of the §24 *minimum* event set — that list is a floor, not a ceiling — but
# recorded so admin export/history shows why a game is paused (append-only audit, never state-
# driving). The stored phase is retained (state-machine §14 "phase may remain stored separately").
_EVENT_GAME_PAUSED = "game_paused"
# T-074 / D-091 (state-machine §21 Option B, §24): the committed consequence pass of one turn,
# emitted after its ``effect_created`` events. Never emitted under ``consequence_mode: none``.
_EVENT_CONSEQUENCES_RESOLVED = "consequences_resolved"
_PAUSE_REASON_CONSEQUENCES = "consequence_resolver_unavailable"
_CONSEQUENCE_MODE_BOUNDED = "bounded_effects_v1"
# D-091: consequence Effects are public, non-expiring and non-superseding in v1.
_CONSEQUENCE_EFFECT_VISIBILITY = "public"
# Reasons recorded on `effect_deactivated` so history shows *why* an Effect became inactive.
_EFFECT_DEACTIVATED_SUPERSEDED = "superseded"
_EFFECT_DEACTIVATED_EXPIRED = "expired"

# Idempotency operation families (T-023, api.md §4). The key scope is
# ``game_id + actor_id + operation`` (actor_id is the Seat's Actor, resolved by auth in T-028).
# ``save_note`` is the Notes op (T-033), idempotent through the same machinery.
_OP_MESSAGE = "send_message"
_OP_READY = "ready"
_OP_ARGUMENT = "make_argument"
_OP_CON = "make_con"
_OP_PASS_CON = "pass_con"
_OP_REPLY = "reply_to_cons"  # I-046
_OP_SAVE_NOTE = "save_note"


# --- Internal helpers -------------------------------------------------------------------


def _semantic_hash(payload: dict) -> str:
    """A canonical, order-stable hash of the *semantic* inputs only (INV-IDEM-003).

    Dict keys are sorted; list element order is preserved (it is part of the semantic payload —
    e.g. an Argument's ordered Pros). Server-derived fields (turn, generated ids, timestamps)
    are never placed in ``payload``, so a genuine retry hashes identically while a changed action
    hashes differently and is therefore a conflict rather than a false replay.

    Deliberately not :func:`amaw_contracts.canonical_json`: a payload may carry a ``UUID``
    (``make_con``'s ``argument_id``) and ``default=str`` is part of the stored-hash contract.
    """
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _json_safe(value):  # noqa: ANN001, ANN201 — recursive JSON coercion of stored results
    """Convert a result value to a JSON-storable form (UUID→str, Enum→value, datetime→ISO)."""
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


# Result-dataclass fields that need coercion back from their JSON-safe stored form on replay.
_RESULT_UUID_FIELDS = frozenset({"id", "argument_id"})
_RESULT_ENUM_FIELDS = {"phase": GamePhase}
_RESULT_DATETIME_FIELDS = frozenset({"created_at"})


def _rebuild_result(result_type, data: dict):  # noqa: ANN001, ANN201
    """Rebuild a frozen result dataclass from its JSON-safe stored dict, coercing typed fields.

    Both the live and replay paths return the same dataclass type; only the replay path passes
    through here, restoring ``UUID``/``GamePhase`` fields that :func:`_json_safe` flattened.
    """
    kwargs: dict = {}
    for field in dataclasses.fields(result_type):
        stored = data[field.name]
        if stored is not None and field.name in _RESULT_UUID_FIELDS and not isinstance(stored, uuid.UUID):
            stored = uuid.UUID(stored)
        elif (
            stored is not None
            and field.name in _RESULT_ENUM_FIELDS
            and not isinstance(stored, Enum)
        ):
            stored = _RESULT_ENUM_FIELDS[field.name](stored)
        elif (
            stored is not None
            and field.name in _RESULT_DATETIME_FIELDS
            and not isinstance(stored, datetime)
        ):
            stored = datetime.fromisoformat(stored)
        kwargs[field.name] = stored
    return result_type(**kwargs)


def _run_idempotent(
    engine: Engine,
    game_id: uuid.UUID,
    mutate,  # noqa: ANN001 — Callable[[Game, Session], None]
    *,
    operation: str = "",
    actor_id: str = "",
    key: str | None = None,
    payload: dict | None = None,
    result_type=None,  # noqa: ANN001 — a frozen result dataclass type
    captured: dict | None = None,
):
    """Run ``mutate`` under the T-008 guard, adding replay-safe idempotency when ``key`` is set.

    With ``key is None`` this is exactly :func:`_run`. With a key, the idempotency lookup, the
    mutation and the record write share the **one** ``apply_guarded`` transaction (state-machine
    §25), so they commit or roll back together (INV-GAME-004):

    - a matching record with the *same* semantic payload replays the stored original result and
      runs no mutation (INV-IDEM-001) — even if the phase has since moved on;
    - a matching record with a *different* payload raises :class:`IdempotencyConflict`
      (INV-IDEM-002);
    - a first-time key runs the mutation and records the JSON-safe result, so a rolled-back
      mutation claims no key and a fresh-engine replay still returns the original result.

    Returns the rebuilt result on replay, else ``None`` (the caller then builds it from
    ``captured``).
    """
    if key is not None and (not isinstance(key, str) or not key or len(key) > MAX_IDEMPOTENCY_KEY_CHARS):
        raise IdempotencyConflict(
            f"idempotency key must contain 1–{MAX_IDEMPOTENCY_KEY_CHARS} characters"
        )
    replayed: dict = {}

    def guarded_mutate(game: Game, session: Session) -> None:
        if key is None:
            mutate(game, session)
            return
        assert payload is not None and result_type is not None and captured is not None
        payload_hash = _semantic_hash(payload)
        existing = session.get(
            IdempotencyRecord,
            {"game_id": game.id, "actor_id": actor_id, "operation": operation, "key": key},
        )
        if existing is not None:
            if existing.payload_hash != payload_hash:
                raise IdempotencyConflict(
                    f"idempotency key {key!r} reused with a different payload for "
                    f"{operation!r} by actor {actor_id!r}"
                )
            replayed["result"] = _rebuild_result(result_type, existing.result)
            return
        mutate(game, session)
        persistence.create(
            session,
            IdempotencyRecord(
                game_id=game.id,
                actor_id=actor_id,
                operation=operation,
                key=key,
                payload_hash=payload_hash,
                result=_json_safe(dict(captured)),
            ),
        )

    try:
        persistence.apply_guarded(
            engine, game_id, guard=lambda g, s: True, mutate=guarded_mutate
        )
    except persistence.GuardFailed as exc:
        raise GameNotFound(f"game {game_id!r} does not exist") from exc
    return replayed.get("result")


def _emit_event(
    session: Session,
    game: Game,
    event_type: str,
    *,
    actor_id: str | None = None,
    payload: dict | None = None,
) -> None:
    """Append one authoritative :class:`~app.models.GameEvent` (T-022, TRD §7.8, §24).

    The event is created inside the caller's transaction, so it commits or rolls back with the
    state mutation it records — never one without the other. ``GameEvent.id`` is a monotonic
    integer assigned on flush, giving events a deterministic causal order independent of the
    wall-clock ``created_at``. ``turn`` is taken from the game's current state at emission time.
    The event log is append-only audit history; it never drives authoritative state.
    """
    persistence.create(
        session,
        GameEvent(
            game_id=game.id,
            turn=game.turn,
            type=event_type,
            actor_id=actor_id,
            payload=payload or {},
        ),
    )


def _require_running_phase(game: Game, phase: GamePhase) -> None:
    """Require a running game in exactly ``phase`` using freshly loaded state."""
    if game.status is not GameStatus.running or game.phase is not phase:
        raise InvalidPhase(
            f"operation requires a running {phase.value} phase; game is "
            f"{game.status.value}/{game.phase.value}"
        )


def _get_current_argument(session: Session, game: Game) -> Argument:
    """Return the one persisted Argument open for Cons in this Actor cycle.

    Currentness is deliberately derived from durable lifecycle state: same game, turn and
    authoritative active Actor, with status ``open_for_cons``. Zero or multiple matches are
    equally unsafe and fail closed (INV-ARG-005 / INV-CON-006).
    """
    arguments = game_queries.arguments_by_status(
        session, game, (_ARGUMENT_OPEN_FOR_CONS,)
    )
    if len(arguments) != 1:
        raise ArgumentNotCurrent(
            f"game {game.id!r} does not have exactly one current Argument"
        )
    return arguments[0]


def _challenger_ring(session: Session, game: Game) -> list[str]:
    """The rotating-schedule ring: authoritative ``turn_order``, then any non-arguing Actors."""
    actors = game_queries.actor_ids(session, game.id)
    ring = [actor_id for actor_id in game.turn_order if actor_id in actors]
    ring.extend(sorted(actors - set(ring)))
    return ring


def _select_challengers(session: Session, game: Game, owner_id: str) -> list[str]:
    """Seal the next ``K`` challengers for ``owner_id``'s Argument (I-055 / D-053).

    Walks the ring from the durable cursor, skips the owner, takes the first ``K`` distinct
    Actors and moves the cursor to just past the last one taken. Exposure therefore balances
    across a game (every opponent within ±1 of every other) and the seeded start rotates it
    across repetitions. The walk is pure bookkeeping: no adjudication draw is consumed, so an
    open and a sampled run with the same seed still share every server roll.
    """
    ring = _challenger_ring(session, game)
    count = game.con_challengers_per_argument or 0
    if game.con_challenger_cursor is None or count < 1 or not ring:
        raise GameServiceError(
            f"game {game.id!r} is in sampled_sealed mode without a usable challenger schedule"
        )
    cursor = game.con_challenger_cursor % len(ring)
    selected: list[str] = []
    for step in range(len(ring)):
        position = (cursor + step) % len(ring)
        candidate = ring[position]
        if candidate == owner_id:
            continue
        selected.append(candidate)
        if len(selected) == count:
            game.con_challenger_cursor = (position + 1) % len(ring)
            break
    if len(selected) != count:
        raise GameServiceError(
            f"game {game.id!r} cannot seat {count} challengers against {owner_id!r}"
        )
    session.add(game)
    return selected


def _require_open_con_participation(
    session: Session,
    game: Game,
    argument: Argument,
    actor_id: str,
) -> None:
    """Validate game-local opponent eligibility and absence of persisted completion."""
    actors = game_queries.actor_ids(session, game.id)
    if actor_id not in actors:
        raise ActorNotInGame(
            f"actor {actor_id!r} does not belong to game {game.id!r}"
        )
    if actor_id == argument.actor_id:
        raise SelfConForbidden(
            f"actor {actor_id!r} owns Argument {argument.id!r}"
        )
    if actor_id not in eligible_con_actor_ids(session, game, argument):
        raise ConChallengerNotSelected(
            f"actor {actor_id!r} is not a sealed challenger of Argument {argument.id!r}"
        )
    completed = session.get(
        ConParticipation,
        {"argument_id": argument.id, "actor_id": actor_id},
    )
    if completed is not None:
        raise ConParticipationComplete(
            f"actor {actor_id!r} already completed Cons for Argument {argument.id!r}"
        )


def _close_cons_if_complete(session: Session, game: Game, argument: Argument) -> bool:
    """Atomically close Cons iff every eligible opponent has durably completed (§18).

    Under ``visible_reply`` with at least one Con the close reveals the frozen set and opens the
    ``reply`` phase for the owner instead of adjudicating (§18.1, D-060); zero Cons never enter
    ``reply``.
    """
    eligible = eligible_con_actor_ids(session, game, argument)
    completed = set(
        session.exec(
            select(ConParticipation.actor_id).where(
                ConParticipation.argument_id == argument.id
            )
        ).all()
    )
    if not eligible.issubset(completed):
        return False
    if game.con_reply_mode == CON_REPLY_MODE_VISIBLE:
        cons = revealed_cons(session, argument.id)
        if cons:
            argument.status = _ARGUMENT_AWAITING_REPLY
            game.phase = GamePhase.reply
            session.add(argument)
            session.add(game)
            _emit_event(
                session,
                game,
                _EVENT_CONS_REVEALED,
                payload={
                    "argument_id": str(argument.id),
                    "con_ids": [str(con.id) for con in cons],
                },
            )
            _emit_event(
                session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.reply.value}
            )
            return True
    argument.status = _ARGUMENT_ADJUDICATING
    game.phase = GamePhase.adjudication
    session.add(argument)
    session.add(game)
    _emit_event(
        session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.adjudication.value}
    )
    return True


def _expire_effects(session: Session, game: Game) -> None:
    """Deactivate active Effects whose expiry boundary has been reached (§15.5, D-010).

    Evaluated against ``game.turn`` (the turn now starting). Deactivation is an update, never a
    delete (INV-EFFECT-004); Effects that are already inactive, never expire, or expire on a
    later turn are left untouched.
    """
    active = session.exec(
        select(Effect).where(Effect.game_id == game.id, Effect.active == True)  # noqa: E712
    ).all()
    for effect in active:
        if effect.expires_turn is not None and effect.expires_turn <= game.turn:
            effect.active = False
            session.add(effect)
            _emit_event(
                session,
                game,
                _EVENT_EFFECT_DEACTIVATED,
                payload={
                    "effect_id": str(effect.id),
                    "reason": _EFFECT_DEACTIVATED_EXPIRED,
                },
            )


def _start_turn(session: Session, game: Game, *, turn: int) -> None:
    """Establish the deterministic turn-start state for ``turn`` (state-machine §15).

    Sets the turn, enters diplomacy with no active Actor, clears persisted readiness, runs
    Effect expiry for the new turn and emits ``phase_started(diplomacy)`` (§15.6). This is the
    **single** turn-start implementation: :func:`start_game` uses it for turn 1 and
    :func:`advance_after_resolution` reuses it for every later turn (T-021), so expiry,
    readiness reset and phase setup are never reimplemented.
    """
    game.turn = turn
    game.phase = GamePhase.diplomacy
    game.active_actor_id = None
    game.diplomacy_ready_actor_ids = []
    game.diplomacy_rounds_completed = 0
    game.diplomacy_close_reason = None
    # §15 order: expire Effects (step 5) before announcing the phase (step 6). Expiry emits its
    # own effect_deactivated events, which therefore precede this phase_started(diplomacy).
    _expire_effects(session, game)
    _release_injects(session, game)  # §15.5a (D-095): after expiry, before the phase opens
    _emit_event(
        session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.diplomacy.value}
    )


def _release_injects(session: Session, game: Game) -> None:
    """Release the scenario injects scheduled for ``game.turn`` as Effects (§15.5a, D-095).

    Declaration order, no RNG, no condition: the server never reads the text. Runs inside the
    turn-start transaction (INV-GAME-004). An inject whose Effect already exists is skipped, so
    no path can release it twice.
    """
    for inject in game.scenario_injects:
        if inject["turn"] != game.turn:
            continue
        released = session.exec(
            select(Effect.id).where(
                Effect.game_id == game.id, Effect.source_inject_id == inject["id"]
            )
        ).first()
        if released is not None:
            continue
        effect = persistence.create(
            session,
            Effect(
                game_id=game.id,
                description=inject["description"],
                visibility=inject["visibility"],
                visible_to=list(inject.get("visible_to") or []),
                source_inject_id=inject["id"],
                created_turn=game.turn,
                expires_turn=inject.get("expires_turn"),
                active=True,
                supersedes=[],
            ),
        )
        _emit_event(
            session,
            game,
            _EVENT_EFFECT_CREATED,
            payload={"effect_id": str(effect.id), "source_inject_id": inject["id"]},
        )


def _close_diplomacy_if_ready(session: Session, game: Game) -> bool:
    """Close diplomacy to ARGUMENT iff every Actor is currently ready (§16 → §17).

    Uses the *current* persisted readiness set (so a readiness reset earlier in the same
    transaction is already reflected). On close the phase becomes ``argument`` and the active
    Actor becomes the first entry in ``turn_order``; the turn is preserved and the game keeps
    running. Returns whether the close happened.
    """
    required = game_queries.actor_ids(session, game.id)
    ready = set(game.diplomacy_ready_actor_ids)
    if not required or not required.issubset(ready):
        return False
    game.phase = GamePhase.argument
    game.diplomacy_close_reason = "all_ready"
    # First Actor in the authoritative turn order becomes active (state-machine §17).
    game.active_actor_id = game.turn_order[0] if game.turn_order else None
    # Readiness is reset at the next turn start (§15.4); it is left as-is on close.
    _emit_event(
        session,
        game,
        _EVENT_DIPLOMACY_CLOSED,
        payload={"reason": "all_ready"},
    )
    _emit_event(
        session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.argument.value}
    )
    return True


def complete_diplomacy_round(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    turn: int,
    round_number: int,
) -> DiplomacyRoundResult:
    """Checkpoint one fully settled I-028 round and apply the persisted Diplomacy policy.

    ``turn + round_number`` is the semantic idempotency key. The coordinator calls this only
    after every eligible activation has settled and deferred readiness has been applied, so a
    failed activation consumes no completed-round slot. On the final bounded round, committed
    Messages and readiness changes remain durable, then this transaction closes to Argument;
    final-round private-message recipients receive no extra Diplomacy activation.
    """
    if not isinstance(turn, int) or isinstance(turn, bool) or turn < 1:
        raise InvalidDiplomacyRound("turn must be a positive integer")
    if not isinstance(round_number, int) or isinstance(round_number, bool) or round_number < 1:
        raise InvalidDiplomacyRound("round_number must be a positive integer")
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        if game.turn != turn:
            raise InvalidDiplomacyRound(
                f"round belongs to turn {turn}, but game is on turn {game.turn}"
            )
        completed = game.diplomacy_rounds_completed
        if round_number <= completed:
            captured.update(
                turn=turn,
                round_number=round_number,
                rounds_completed=completed,
                phase=game.phase,
                closed=game.phase is not GamePhase.diplomacy,
                close_reason=game.diplomacy_close_reason,
            )
            return
        if game.status is not GameStatus.running:
            raise InvalidDiplomacyRound("round completion requires a running game")
        if round_number != completed + 1:
            raise InvalidDiplomacyRound(
                f"round {round_number} cannot follow completed round {completed}"
            )
        # All-ready settlement may already have moved to Argument in the immediately preceding
        # readiness transaction. No other phase is a legal round-completion boundary.
        if game.phase not in {GamePhase.diplomacy, GamePhase.argument}:
            raise InvalidDiplomacyRound(
                f"cannot complete a Diplomacy round from phase {game.phase.value!r}"
            )

        game.diplomacy_rounds_completed = round_number
        session.add(game)
        _emit_event(
            session,
            game,
            _EVENT_DIPLOMACY_ROUND_COMPLETED,
            payload={
                "round": round_number,
                "mode": game.diplomacy_mode,
                "round_limit": game.diplomacy_rounds_per_turn,
            },
        )

        closed = game.phase is GamePhase.argument
        reason = game.diplomacy_close_reason if closed else None
        if (
            not closed
            and game.diplomacy_mode == "bounded_rounds"
            and game.diplomacy_rounds_per_turn is not None
            and round_number >= game.diplomacy_rounds_per_turn
        ):
            game.phase = GamePhase.argument
            game.active_actor_id = game.turn_order[0] if game.turn_order else None
            game.diplomacy_close_reason = "round_bound_reached"
            closed = True
            reason = game.diplomacy_close_reason
            _emit_event(
                session,
                game,
                _EVENT_DIPLOMACY_CLOSED,
                payload={"reason": reason, "rounds_completed": round_number},
            )
            _emit_event(
                session,
                game,
                _EVENT_PHASE_STARTED,
                payload={"phase": GamePhase.argument.value},
            )

        captured.update(
            turn=turn,
            round_number=round_number,
            rounds_completed=game.diplomacy_rounds_completed,
            phase=game.phase,
            closed=closed,
            close_reason=reason,
        )

    _run_idempotent(engine, game_id, mutate)
    return DiplomacyRoundResult(**captured)


# --- T-013: game start ------------------------------------------------------------------


def start_game(engine: Engine, game_id: uuid.UUID) -> StartResult:
    """Transition a LOBBY game to the first turn's DIPLOMACY phase (state-machine §14/§15).

    Guards (evaluated on fresh state, inside the transaction): the game is in ``LOBBY``; its
    ``turn_order`` is non-empty and every id in it is a real Actor of the game; and every Actor
    has a configured Seat (§14 guard "scenario valid, Seats configured"). A failing guard raises
    :class:`IllegalStart` and the transaction rolls back, leaving the lobby state untouched. On
    success ``turn = 1``, ``phase = diplomacy``, ``active_actor_id = None``, readiness is reset,
    Effects are expired for turn 1, and ``status`` becomes ``running``.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        if game.status is not GameStatus.lobby:
            raise IllegalStart(
                f"game {game_id!r} cannot start from status {game.status.value!r}"
            )
        if not game.turn_order:
            raise IllegalStart(f"game {game_id!r} has an empty turn_order")
        actors = game_queries.actor_ids(session, game.id)
        missing = [a for a in game.turn_order if a not in actors]
        if missing:
            raise IllegalStart(f"turn_order names unknown Actor(s) {missing!r}")
        seated = set(
            session.exec(select(Seat.actor_id).where(Seat.game_id == game.id)).all()
        )
        unseated = [a for a in game.turn_order if a not in seated]
        if unseated:
            raise IllegalStart(f"Actor(s) without a configured Seat: {unseated!r}")
        # I-078: a Game that opted in at creation cannot start without its sealed manifest —
        # revalidated here, inside the transaction (INV-GAME-004). Games that did not opt in
        # keep the unchanged §14 guard.
        if game.require_run_manifest and session.get(RunManifest, game.id) is None:
            raise IllegalStart(
                f"game {game_id!r} requires a sealed run manifest before it can start"
            )

        game.status = GameStatus.running
        # game_started precedes the turn-1 phase_started(diplomacy) that _start_turn emits (§24).
        game.turn = 1  # so the game_started event carries the correct turn before _start_turn
        _emit_event(
            session,
            game,
            _EVENT_GAME_STARTED,
            payload={"max_turns": game.max_turns, "turn_order": list(game.turn_order)},
        )
        _start_turn(session, game, turn=1)
        captured.update(
            turn=game.turn,
            phase=game.phase,
            status=game.status,
            active_actor_id=game.active_actor_id,
        )

    _run_idempotent(engine, game_id, mutate)
    return StartResult(**captured)


# --- T-014: diplomacy Messages ----------------------------------------------------------


def send_message(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    sender_actor_id: str,
    text: str,
    public: bool,
    recipients: list[str] | None = None,
    idempotency_key: str | None = None,
) -> MessageResult:
    """Create a Message in an allowed phase and apply diplomacy's readiness-reset rule.

    The ``sender_actor_id`` is the authoritative identity supplied by the caller/service
    boundary (Seat→Actor resolution is T-028); it is validated to belong to the game. Validation
    (all inside the transaction, on fresh state): the game is running and in ``diplomacy`` or
    ``argument`` (TRD phase contract); the sender is an Actor of the game; ``text`` is non-empty;
    a public Message carries an empty recipient list (INV-MSG-002) while a private Message
    carries at least one, every recipient a distinct Actor of the game (INV-MSG-003/004). The
    Message turn is server-derived.

    Ordering for the readiness race (§16): validate → persist Message → clear the readiness of
    every ready **private** recipient → re-evaluate the close condition → commit. A public
    Message never clears readiness. Because a reset can only *remove* readiness, this call never
    closes diplomacy; the re-evaluation is retained for correctness and to match the contract's
    prescribed sequence.
    """
    recipient_list = list(recipients or [])
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        _require_running_message_phase(game)

        actors = game_queries.actor_ids(session, game.id)
        if sender_actor_id not in actors:
            raise ActorNotInGame(
                f"actor {sender_actor_id!r} does not belong to game {game_id!r}"
            )
        if not isinstance(text, str) or not text.strip():
            raise InvalidMessage("message text must be non-empty")
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidMessage(f"message text exceeds {MAX_TEXT_CHARS} characters")

        if public:
            if recipient_list:
                raise InvalidMessage("a public Message must have an empty recipient list")
            stored_recipients: list[str] = []
        else:
            if not recipient_list:
                raise InvalidRecipient("a private Message requires at least one recipient")
            if len(set(recipient_list)) != len(recipient_list):
                raise InvalidRecipient("recipient list contains duplicates")
            foreign = [r for r in recipient_list if r not in actors]
            if foreign:
                raise InvalidRecipient(f"recipient(s) not in game {game_id!r}: {foreign!r}")
            stored_recipients = recipient_list

        message = persistence.create(
            session,
            Message(
                game_id=game.id,
                turn=game.turn,  # server-derived
                sender_actor_id=sender_actor_id,
                recipients=stored_recipients,
                text=text,
                public=public,
            ),
        )
        # Raw admin audit record (T-022). Recipients are retained here because T-022 stores the
        # complete authoritative stream; Actor-facing visibility filtering of events is T-035.
        _emit_event(
            session,
            game,
            _EVENT_MESSAGE_SENT,
            actor_id=sender_actor_id,
            payload={
                "message_id": str(message.id),
                "public": message.public,
                "recipients": list(message.recipients),
                "phase": game.phase.value,
            },
        )

        # §16: a new private Message to an already-ready recipient clears that recipient's
        # readiness in this same transaction, before the close condition is evaluated.
        if game.phase is GamePhase.diplomacy and not public:
            ready = [a for a in game.diplomacy_ready_actor_ids if a not in stored_recipients]
            if ready != game.diplomacy_ready_actor_ids:
                game.diplomacy_ready_actor_ids = ready  # reassign so the JSON column is dirtied
        if game.phase is GamePhase.diplomacy:
            _close_diplomacy_if_ready(session, game)

        captured.update(
            id=message.id,
            turn=message.turn,
            sender_actor_id=message.sender_actor_id,
            recipients=list(message.recipients),
            public=message.public,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_MESSAGE,
        actor_id=sender_actor_id,
        key=idempotency_key,
        payload={"text": text, "public": public, "recipients": recipient_list},
        result_type=MessageResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return MessageResult(**captured)


# --- T-014: readiness -------------------------------------------------------------------


def ready(
    engine: Engine,
    game_id: uuid.UUID,
    actor_id: str,
    idempotency_key: str | None = None,
) -> ReadyResult:
    """Mark ``actor_id`` ready in diplomacy and close the phase if all Actors are now ready.

    Validation (inside the transaction, on fresh state): the game is running and in
    ``diplomacy`` (else :class:`InvalidPhase`); the Actor belongs to the game (else
    :class:`ActorNotInGame`); the Actor is not already ready (else :class:`AlreadyReady` —
    readiness is deliberately non-idempotent here, idempotency is T-023). The Actor is added to
    the persisted readiness set, then :func:`_close_diplomacy_if_ready` evaluates the close
    condition against that fresh set. A second close attempt is naturally refused because the
    phase is no longer ``diplomacy`` (INV-GAME-005).
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        _require_running_phase(game, GamePhase.diplomacy)
        if actor_id not in game_queries.actor_ids(session, game.id):
            raise ActorNotInGame(
                f"actor {actor_id!r} does not belong to game {game_id!r}"
            )
        if actor_id in game.diplomacy_ready_actor_ids:
            raise AlreadyReady(f"actor {actor_id!r} is already ready")

        game.diplomacy_ready_actor_ids = [*game.diplomacy_ready_actor_ids, actor_id]
        _emit_event(session, game, _EVENT_ACTOR_READY, actor_id=actor_id)
        closed = _close_diplomacy_if_ready(session, game)

        captured.update(
            actor_id=actor_id,
            ready=True,
            turn=game.turn,
            phase=game.phase,
            active_actor_id=game.active_actor_id,
            diplomacy_closed=closed,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_READY,
        actor_id=actor_id,
        key=idempotency_key,
        payload={},  # readiness carries no semantic body; identity is the scope (§4)
        result_type=ReadyResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return ReadyResult(**captured)


# --- T-015: primary Argument ------------------------------------------------------------


def make_argument(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    actor_id: str,
    action: str,
    desired_effect: str,
    pros: list[str],
    idempotency_key: str | None = None,
) -> ArgumentResult:
    """Accept the active Actor's primary Argument and atomically open the Cons phase.

    Actor identity enters from the trusted service boundary (Seat resolution is T-028); Actor
    and turn stored on the row come from the freshly revalidated game state, never request
    payload fields. The persisted Game snapshot supplies the per-game/turn/Actor budget. The
    selected 1-based ``budget_slot`` both supports configured values greater than one and uses
    the model's uniqueness constraint as a database backstop.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        _require_running_phase(game, GamePhase.argument)
        actors = game_queries.actor_ids(session, game.id)
        if actor_id not in actors:
            raise ActorNotInGame(
                f"actor {actor_id!r} does not belong to game {game.id!r}"
            )
        if game.active_actor_id is None or actor_id != game.active_actor_id:
            raise NotActiveActor(
                f"actor {actor_id!r} is not active in game {game.id!r}"
            )

        if not isinstance(action, str) or not action.strip():
            raise InvalidArgument("Argument action must be a non-empty string")
        if len(action) > MAX_ARGUMENT_CHARS:
            raise InvalidArgument(f"Argument action exceeds {MAX_ARGUMENT_CHARS} characters")
        if not isinstance(desired_effect, str) or not desired_effect.strip():
            raise InvalidArgument("Argument desired_effect must be a non-empty string")
        if len(desired_effect) > MAX_ARGUMENT_CHARS:
            raise InvalidArgument(
                f"Argument desired_effect exceeds {MAX_ARGUMENT_CHARS} characters"
            )
        if not isinstance(pros, list) or not 1 <= len(pros) <= _MAX_PROS:
            raise InvalidArgument(f"Argument requires 1–{_MAX_PROS} Pros")
        if any(not isinstance(pro, str) or not pro.strip() for pro in pros):
            raise InvalidArgument("every Pro must be a non-empty string")
        if any(len(pro) > MAX_PRO_CHARS for pro in pros):
            raise InvalidArgument(f"each Pro is limited to {MAX_PRO_CHARS} characters")

        unresolved = list(
            session.exec(
                select(Argument).where(
                    Argument.game_id == game.id,
                    Argument.status.in_(_UNRESOLVED_ARGUMENT_STATUSES),
                )
            ).all()
        )
        if unresolved:
            raise ArgumentNotCurrent(
                f"game {game.id!r} already has an unresolved current Argument"
            )

        accepted = game_queries.arguments_used(session, game, actor_id)
        if len(accepted) >= game.primary_arguments_per_actor:
            raise ArgumentBudgetExhausted(
                f"actor {actor_id!r} exhausted its turn-{game.turn} Argument budget"
            )
        used_slots = {argument.budget_slot for argument in accepted}
        budget_slot = next(
            slot
            for slot in range(1, game.primary_arguments_per_actor + 1)
            if slot not in used_slots
        )

        argument = persistence.create(
            session,
            Argument(
                game_id=game.id,
                turn=game.turn,
                actor_id=game.active_actor_id,
                action=action,
                desired_effect=desired_effect,
                pros=list(pros),
                budget_slot=budget_slot,
                status=_ARGUMENT_OPEN_FOR_CONS,
            ),
        )
        game.phase = GamePhase.cons
        session.add(game)
        # §17: persist Argument → argument_submitted → initialize Con participation state →
        # open Cons → phase_started(cons).
        _emit_event(
            session,
            game,
            _EVENT_ARGUMENT_SUBMITTED,
            actor_id=argument.actor_id,
            payload={"argument_id": str(argument.id)},
        )
        if game.con_mode == _CON_MODE_SAMPLED_SEALED:
            # I-055 / D-053: the challenger set is sealed here, before any opponent can be
            # activated, so neither latency nor response order can influence who opposes.
            cursor_before = game.con_challenger_cursor
            argument.challenger_actor_ids = _select_challengers(
                session, game, argument.actor_id
            )
            session.add(argument)
            _emit_event(
                session,
                game,
                _EVENT_CON_CHALLENGERS_SELECTED,
                payload={
                    "argument_id": str(argument.id),
                    "mode": game.con_mode,
                    "challengers_per_argument": game.con_challengers_per_argument,
                    "challenger_actor_ids": list(argument.challenger_actor_ids),
                    "cursor_before": cursor_before,
                    "cursor_after": game.con_challenger_cursor,
                },
            )
        _emit_event(
            session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.cons.value}
        )
        # I-079: §18 closes "when all eligible Actors have completed" — vacuously true when
        # nobody is eligible, and no later call would ever close it (only a Con or a pass does).
        # The scenario loader refuses a one-Actor game (D-069); the engine still never strands one.
        _close_cons_if_complete(session, game, argument)
        captured.update(
            id=argument.id,
            turn=argument.turn,
            actor_id=argument.actor_id,
            budget_slot=argument.budget_slot,
            status=argument.status,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_ARGUMENT,
        actor_id=actor_id,
        key=idempotency_key,
        payload={"action": action, "desired_effect": desired_effect, "pros": pros},
        result_type=ArgumentResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return ArgumentResult(**captured)


# --- T-016: Cons and explicit completion ------------------------------------------------


def make_con(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    argument_id: uuid.UUID,
    actor_id: str,
    reason: str,
    idempotency_key: str | None = None,
) -> ConResult:
    """Persist one eligible opponent's Con against the authoritative current Argument."""
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        _require_running_phase(game, GamePhase.cons)
        argument = _get_current_argument(session, game)
        if argument.id != argument_id:
            raise ArgumentNotCurrent(
                f"Argument {argument_id!r} is not current in game {game.id!r}"
            )
        _require_open_con_participation(session, game, argument, actor_id)
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidCon("Con reason must be a non-empty string")
        if len(reason) > MAX_TEXT_CHARS:
            raise InvalidCon(f"Con reason exceeds {MAX_TEXT_CHARS} characters")

        submitted = game_queries.cons_submitted(session, argument.id, actor_id)
        if len(submitted) >= game.max_cons_per_actor:
            raise ConLimitReached(
                f"actor {actor_id!r} reached the Con limit for Argument {argument.id!r}"
            )

        con = persistence.create(
            session,
            Con(argument_id=argument.id, actor_id=actor_id, reason=reason),
        )
        _emit_event(
            session,
            game,
            _EVENT_CON_SUBMITTED,
            actor_id=actor_id,
            payload={"argument_id": str(argument.id), "con_id": str(con.id)},
        )
        if len(submitted) + 1 == game.max_cons_per_actor:
            # I-030 / D-043: after the final allowed Con this Actor has no remaining strategic
            # choice except completion. Persist the same durable completion record used by an
            # explicit pass, but do not emit ``con_passed`` — the Actor submitted a Con, it did
            # not choose to pass. The final opponent closes the phase in this transaction.
            persistence.create(
                session,
                ConParticipation(argument_id=argument.id, actor_id=actor_id),
            )
            _close_cons_if_complete(session, game, argument)
        captured.update(
            id=con.id,
            argument_id=con.argument_id,
            actor_id=con.actor_id,
            reason=con.reason,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_CON,
        actor_id=actor_id,
        key=idempotency_key,
        payload={"argument_id": argument_id, "reason": reason},
        result_type=ConResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return ConResult(**captured)


def pass_con(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    argument_id: uuid.UUID,
    actor_id: str,
    idempotency_key: str | None = None,
) -> PassConResult:
    """Persist an eligible opponent's explicit pass and close Cons when all have passed.

    A pass is a completion record, never an empty Con or in-memory flag. The final record,
    Argument status change and ``CONS → ADJUDICATION`` transition share this transaction.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        _require_running_phase(game, GamePhase.cons)
        argument = _get_current_argument(session, game)
        if argument.id != argument_id:
            raise ArgumentNotCurrent(
                f"Argument {argument_id!r} is not current in game {game.id!r}"
            )
        _require_open_con_participation(session, game, argument, actor_id)

        persistence.create(
            session,
            ConParticipation(argument_id=argument.id, actor_id=actor_id),
        )
        _emit_event(
            session,
            game,
            _EVENT_CON_PASSED,
            actor_id=actor_id,
            payload={"argument_id": str(argument.id)},
        )
        closed = _close_cons_if_complete(session, game, argument)
        captured.update(
            argument_id=argument.id,
            actor_id=actor_id,
            passed=True,
            phase=game.phase,
            cons_closed=closed,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_PASS_CON,
        actor_id=actor_id,
        key=idempotency_key,
        payload={"argument_id": argument_id},
        result_type=PassConResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return PassConResult(**captured)


# --- I-046: owner reply to revealed Cons (state-machine §18.1, D-060) -------------------


def _get_awaiting_reply_argument(session: Session, game: Game) -> Argument:
    """The one Argument whose Cons are revealed and whose owner reply is pending, else fail."""
    arguments = game_queries.arguments_by_status(
        session, game, (_ARGUMENT_AWAITING_REPLY,)
    )
    if len(arguments) != 1:
        raise ArgumentNotCurrent(
            f"game {game.id!r} does not have exactly one Argument awaiting reply"
        )
    return arguments[0]


def _validated_reply_points(
    game: Game, cons: list[Con], points: object
) -> list[dict[str, str]]:
    """INV-REPLY-002: bounded cardinality, distinct frozen ``con_id`` references, bounded text."""
    if not isinstance(points, list):
        raise InvalidReply("reply points must be a list")
    limit = game.reply_max_points or 0
    if len(points) > limit:
        raise InvalidReply(f"reply has {len(points)} points; at most {limit} are allowed")
    frozen = {str(con.id): con for con in cons}
    seen: set[str] = set()
    cleaned: list[dict[str, str]] = []
    for point in points:
        if not isinstance(point, dict) or set(point) != {"con_id", "text"}:
            raise InvalidReply("each reply point is exactly {con_id, text}")
        con_id = str(point["con_id"])
        text = point["text"]
        if con_id not in frozen:
            raise InvalidReply(f"reply point references unknown Con {con_id!r}")
        if con_id in seen:
            raise InvalidReply(f"reply point references Con {con_id!r} more than once")
        if not isinstance(text, str) or not text.strip():
            raise InvalidReply("reply point text must be a non-empty string")
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidReply(f"reply point text exceeds {MAX_TEXT_CHARS} characters")
        seen.add(con_id)
        cleaned.append({"con_id": con_id, "text": text})
    return cleaned


def submit_reply(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    argument_id: uuid.UUID,
    actor_id: str,
    points: list[dict],
    idempotency_key: str | None = None,
) -> ReplyResult:
    """Persist the Argument owner's single reply to the revealed Cons and close to adjudication.

    Revalidates mode, phase, current Argument, owner identity and the absence of a prior reply
    inside the transaction (INV-REPLY-001, INV-GAME-004/005); validates the points against the
    frozen Con set (INV-REPLY-002). The ``ArgumentReply`` row, ``owner_reply_submitted``, the
    ``awaiting_reply → adjudicating`` status change and ``REPLY → ADJUDICATION`` commit
    together. A same-key same-payload replay returns the record; any other attempt once the
    reply exists is :class:`ReplyAlreadySubmitted`.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        # Acceptance closes the phase at once, so a fresh attempt after the reply exists would
        # otherwise only ever see "wrong phase"; the contract (api.md §9.3) promises
        # already-participated for the owner's second attempt, so that check comes first.
        existing = session.get(ArgumentReply, argument_id)
        if existing is not None and existing.actor_id == actor_id:
            raise ReplyAlreadySubmitted(
                f"Argument {argument_id!r} already has its reply"
            )
        _require_running_phase(game, GamePhase.reply)
        argument = _get_awaiting_reply_argument(session, game)
        if argument.id != argument_id:
            raise ArgumentNotCurrent(
                f"Argument {argument_id!r} is not current in game {game.id!r}"
            )
        if actor_id != argument.actor_id:
            raise NotArgumentOwner(
                f"actor {actor_id!r} does not own Argument {argument.id!r}"
            )
        cleaned = _validated_reply_points(game, revealed_cons(session, argument.id), points)

        persistence.create(
            session,
            ArgumentReply(argument_id=argument.id, actor_id=actor_id, points=cleaned),
        )
        _emit_event(
            session,
            game,
            _EVENT_OWNER_REPLY_SUBMITTED,
            actor_id=actor_id,
            payload={
                "argument_id": str(argument.id),
                "point_count": len(cleaned),
                "con_ids": [point["con_id"] for point in cleaned],
            },
        )
        argument.status = _ARGUMENT_ADJUDICATING
        game.phase = GamePhase.adjudication
        session.add(argument)
        session.add(game)
        _emit_event(
            session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.adjudication.value}
        )
        captured.update(argument_id=argument.id, actor_id=actor_id, points=cleaned)

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_REPLY,
        actor_id=actor_id,
        key=idempotency_key,
        payload={"argument_id": argument_id, "points": points},
        result_type=ReplyResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return ReplyResult(**captured)


# --- T-033: private Notes ---------------------------------------------------------------


def save_note(
    engine: Engine,
    game_id: uuid.UUID,
    *,
    actor_id: str,
    text: str,
    idempotency_key: str | None = None,
) -> NoteResult:
    """Create one private, game-/Actor-scoped Note (api.md §11, INV-NOTE-001/002).

    The ``actor_id`` is the authoritative identity supplied at the service boundary (from the
    Seat token — T-028); it is validated to belong to the game. ``text`` must be non-empty. No
    phase guard applies — Notes are private scratch space, legal in any phase. Reads are the
    visibility service's concern (:func:`app.visibility.visible_notes`); nothing here is ever
    surfaced to another Actor. Creation is idempotent through the reserved ``save_note`` family
    (T-023), so a retried save with the same key + text replays the original Note rather than
    duplicating it. No :class:`~app.models.GameEvent` is emitted — a Note is private and must
    never reach the (visibility-filtered but shared) event stream.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        if actor_id not in game_queries.actor_ids(session, game.id):
            raise ActorNotInGame(
                f"actor {actor_id!r} does not belong to game {game.id!r}"
            )
        if not isinstance(text, str) or not text.strip():
            raise InvalidNote("note text must be a non-empty string")
        if len(text) > MAX_TEXT_CHARS:
            raise InvalidNote(f"note text exceeds {MAX_TEXT_CHARS} characters")

        note = persistence.create(
            session, Note(game_id=game.id, actor_id=actor_id, text=text)
        )
        captured.update(
            id=note.id,
            actor_id=note.actor_id,
            text=note.text,
            created_at=note.created_at,
        )

    replay = _run_idempotent(
        engine,
        game_id,
        operation=_OP_SAVE_NOTE,
        actor_id=actor_id,
        key=idempotency_key,
        payload={"text": text},
        result_type=NoteResult,
        captured=captured,
        mutate=mutate,
    )
    if replay is not None:
        return replay
    return NoteResult(**captured)


# --- T-019 + T-020: adjudication and Effect application ---------------------------------


def _current_adjudicating_argument(session: Session, game: Game) -> Argument:
    """Return the single Argument being adjudicated this cycle, or fail closed.

    Currentness is derived from durable lifecycle state: same game/turn/active Actor. If that
    Argument already carries an authoritative :class:`AdjudicationRecord` (or is ``resolved``),
    the caller is attempting a second resolution — :class:`AlreadyAdjudicated` (INV-ADJ-003).
    Zero or multiple in-flight Arguments are equally unsafe and fail closed (§25).
    """
    candidates = game_queries.arguments_by_status(
        session, game, (_ARGUMENT_ADJUDICATING, _ARGUMENT_RESOLVED)
    )
    if len(candidates) != 1:
        raise ArgumentNotCurrent(
            f"game {game.id!r} does not have exactly one Argument to adjudicate"
        )
    argument = candidates[0]
    already = session.get(AdjudicationRecord, argument.id) is not None
    if already or argument.status == _ARGUMENT_RESOLVED:
        raise AlreadyAdjudicated(
            f"Argument {argument.id!r} is already authoritatively adjudicated"
        )
    return argument


def _umpire_argument(game: Game, argument: Argument) -> dict:
    """The Umpire's ``argument`` input (agents.md §21).

    D-094: under round-robin Argument rounds it also names the round, so a scenario rule can type
    a slot. At budget 1 the object is the released shape exactly.
    """
    payload: dict = {
        "actor_id": argument.actor_id,
        "action": argument.action,
        "desired_effect": argument.desired_effect,
        "pros": list(argument.pros),
    }
    if game.primary_arguments_per_actor > 1:
        payload["budget_slot"] = argument.budget_slot
        payload["primary_arguments_per_actor"] = game.primary_arguments_per_actor
    return payload


def _build_umpire_context(
    session: Session, game: Game, argument: Argument
) -> AdjudicationContext:
    """Assemble the authorised adjudication context (agents.md §21), built by the server.

    Contains the Argument, its Cons, the game's currently-active Effects and, per D-029, every
    Actor's private facts plus every active private Effect, each tagged with its owner. The
    player model never chooses what the Umpire sees.
    """
    # I-029 / D-045: Con activations may run concurrently, so commit order is latency-dependent.
    # The Umpire sees Cons in Actor-id order — deterministic in every scheduling mode.
    cons = revealed_cons(session, argument.id)
    # I-046 / D-060: under visible_reply each Con carries its id and the owner's reply travels
    # with it (agents.md §21); under sealed_cons the context is the released shape exactly.
    visible_reply = game.con_reply_mode == CON_REPLY_MODE_VISIBLE
    reply = session.get(ArgumentReply, argument.id) if visible_reply else None
    owner_reply: list[dict[str, str]] | None = None
    if reply is not None:
        author = {str(c.id): c.actor_id for c in cons}
        owner_reply = [
            {
                "con_id": point["con_id"],
                "con_actor_id": author[point["con_id"]],
                "text": point["text"],
            }
            for point in reply.points
        ]
    active_effects = session.exec(
        select(Effect)
        .where(Effect.game_id == game.id, Effect.active == True)  # noqa: E712
        .order_by(Effect.created_turn, Effect.id)
    ).all()
    # D-029 (agents.md §21): the server — never a player model — chooses what the Umpire sees.
    # Every Actor's private facts and every active private Effect go in, tagged by owner, so an
    # Actor's claims are bounded by its own facts whether it is arguing or conning. The Umpire
    # prompt (umpire-v1.3) forbids restating any of this in the public Effects it writes.
    actors = session.exec(
        select(Actor).where(Actor.game_id == game.id).order_by(Actor.id)
    ).all()
    hidden: list[dict[str, str]] = [
        {"kind": "private_information", "actor_id": actor.id, "text": fact}
        for actor in actors
        for fact in actor.private_information
    ]
    hidden.extend(
        {"kind": "private_effect", "actor_id": viewer, "text": effect.description}
        for effect in active_effects
        if effect.visibility == "private"
        for viewer in (effect.visible_to or [])
    )
    return AdjudicationContext(
        scenario_summary=game.scenario_summary or game.scenario_id,
        turn=game.turn,
        turn_length=game.in_world_turn_length,
        argument=_umpire_argument(game, argument),
        cons=[
            {"con_id": str(c.id), "actor_id": c.actor_id, "reason": c.reason}
            if visible_reply
            else {"actor_id": c.actor_id, "reason": c.reason}
            for c in cons
        ],
        owner_reply=owner_reply,
        # Private Effects appear twice on purpose: tagged here so the model can see that a
        # supersession target is private, and owner-tagged in the hidden list (D-029).
        relevant_effects=[
            {
                "id": str(e.id),
                "description": e.description,
                "visibility": e.visibility,
                "visible_to": list(e.visible_to or []),
            }
            for e in active_effects
        ],
        relevant_hidden_information=hidden,
        # D-090 (agents.md §21): the creation-time snapshot, never the package on disk, and never
        # chosen per Argument. Absent — not an empty list — for a rule-free scenario.
        scenario_rules=[dict(rule) for rule in game.scenario_rules] or None,
    )


def _validate_proposal(proposal: AdjudicationProposal) -> None:
    """Validate the Umpire proposal's shape before it can affect state (agents.md §22).

    Enforces INV-ADJ-001/002: a known mode, non-empty rationale, and the probability
    constraints — present and in ``[0.0, 1.0]`` for ``probabilistic``, absent for the automatic
    modes. Malformed proposals are rejected, never silently repaired.
    """
    if not isinstance(proposal.rationale, str) or not proposal.rationale.strip():
        raise InvalidAdjudication("Umpire rationale must be a non-empty string")
    if len(proposal.rationale) > MAX_TEXT_CHARS:
        raise InvalidAdjudication(f"Umpire rationale exceeds {MAX_TEXT_CHARS} characters")
    if not isinstance(proposal.mode, AdjudicationMode):
        raise InvalidAdjudication("Umpire mode is invalid")
    if proposal.mode is AdjudicationMode.probabilistic:
        if proposal.probability is None:
            raise InvalidAdjudication("probabilistic mode requires a probability")
        if not isinstance(proposal.probability, (int, float)) or not math.isfinite(
            proposal.probability
        ) or not 0.0 <= proposal.probability <= 1.0:
            raise InvalidAdjudication(
                f"probability must be within [0.0, 1.0], got {proposal.probability!r}"
            )
    else:
        if proposal.probability is not None:
            raise InvalidAdjudication(
                f"{proposal.mode.value} must not carry a probability"
            )
    for branch in (proposal.success_effects, proposal.failure_effects):
        if not isinstance(branch, list) or len(branch) > MAX_EFFECTS_PER_BRANCH:
            raise InvalidAdjudication(
                f"each Effect branch is limited to {MAX_EFFECTS_PER_BRANCH} items"
            )
        if any(not isinstance(text, str) or not text.strip() for text in branch):
            raise InvalidAdjudication("every proposed Effect description must be non-empty")
        if any(len(text) > MAX_TEXT_CHARS for text in branch):
            raise InvalidAdjudication(
                f"each Effect description is limited to {MAX_TEXT_CHARS} characters"
            )
    for branch_supersedes in (
        proposal.success_supersedes_effect_ids,
        proposal.failure_supersedes_effect_ids,
    ):
        if not isinstance(branch_supersedes, list) or len(
            branch_supersedes
        ) > MAX_EFFECTS_PER_BRANCH:
            raise InvalidAdjudication(
                f"each supersession branch is limited to {MAX_EFFECTS_PER_BRANCH} items"
            )


def _validate_supersession(
    session: Session, game: Game, supersedes_effect_ids: list[uuid.UUID]
) -> list[Effect]:
    """Validate every proposed supersession target and return the Effects to deactivate.

    Each id must be distinct, name an existing Effect **of this game** (no cross-game
    deactivation — INV-EFFECT rules / TEST-ADJ-006), and still be active. Superseding an
    already-inactive Effect is rejected as ineligible (D-011). UUID identity is preserved.
    """
    if len(set(supersedes_effect_ids)) != len(supersedes_effect_ids):
        raise InvalidSupersession("supersedes_effect_ids contains duplicates")
    targets: list[Effect] = []
    for effect_id in supersedes_effect_ids:
        effect = session.get(Effect, effect_id)
        if effect is None or effect.game_id != game.id:
            raise InvalidSupersession(
                f"superseded Effect {effect_id!r} is not part of game {game.id!r}"
            )
        if not effect.active:
            raise InvalidSupersession(
                f"superseded Effect {effect_id!r} is already inactive"
            )
        targets.append(effect)
    return targets


def _apply_effect_branch(
    session: Session,
    game: Game,
    argument: Argument,
    descriptions: list[str],
    supersedes_effect_ids: list[uuid.UUID],
) -> list[uuid.UUID]:
    """Materialise the selected branch's descriptions into authoritative Effects (§19.3).

    Each Effect records full provenance (source Argument, creation turn) and defaults to public
    visibility. The supersession relationship is recorded on the first created Effect; every
    referenced Effect is deactivated by :func:`_supersede_effects` regardless of branch size.
    """
    created: list[uuid.UUID] = []
    for index, description in enumerate(descriptions):
        effect = persistence.create(
            session,
            Effect(
                game_id=game.id,
                description=description,
                visibility=_ADJUDICATION_EFFECT_VISIBILITY,
                visible_to=[],
                source_argument_id=argument.id,
                created_turn=game.turn,
                expires_turn=None,
                active=True,
                # Record supersession on the first (primary) created Effect for history.
                supersedes=list(supersedes_effect_ids) if index == 0 else [],
            ),
        )
        created.append(effect.id)
    return created


def _supersede_effects(session: Session, targets: list[Effect]) -> list[uuid.UUID]:
    """Deactivate each already-validated superseded Effect (§19.4, INV-EFFECT-004).

    Supersession **deactivates**; it never deletes — the rows stay in history.
    """
    superseded: list[uuid.UUID] = []
    for effect in targets:
        effect.active = False
        session.add(effect)
        superseded.append(effect.id)
    return superseded


def resolve_adjudication(
    engine: Engine, game_id: uuid.UUID, umpire: Umpire
) -> AdjudicationOutcome:
    """Authoritatively resolve the current Argument in the ADJUDICATION phase (§19).

    Server-owned: player mutation is already frozen by the phase guards. Inside one transaction
    on freshly re-read state (§25), the flow is: confirm the phase and the single unresolved
    current Argument (rejecting a second resolution — INV-ADJ-003); build the authorised Umpire
    context; obtain and validate the structured proposal (INV-ADJ-001/002); validate any
    supersession targets; decide the outcome — ``automatic_success``/``automatic_failure`` select
    a branch with **no** RNG draw, ``probabilistic`` draws exactly one server roll and applies
    ``success = roll <= probability`` (agents.md §26); persist exactly one
    :class:`AdjudicationRecord` (both proposed branches retained for audit); create only the
    selected branch's Effects; deactivate superseded Effects; and mark the Argument ``resolved``.

    The RNG draw and the increment of ``Game.rng_draw_index`` commit in this same transaction as
    the record (INV-RNG-003), so a failure rolls back the index with everything else and a
    restart continues from the next draw. The Umpire never draws, writes Effects or advances the
    game (INV-EFFECT-001/002); Actor advancement (§20) is deferred to T-021, so the phase stays
    ``adjudication``.
    """
    captured: dict = {}

    # Provider latency must never hold SQLite's process-wide write reservation. Build an
    # authorised snapshot in a read transaction, call the Umpire outside any write transaction,
    # then re-enter the guarded write boundary and prove the same Argument is still current.
    with session_scope(engine) as session:
        snapshot_game = session.get(Game, game_id)
        if snapshot_game is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        _require_running_phase(snapshot_game, GamePhase.adjudication)
        snapshot_argument = _current_adjudicating_argument(session, snapshot_game)
        expected_argument_id = snapshot_argument.id
        context = _build_umpire_context(session, snapshot_game, snapshot_argument)

    try:
        proposal = umpire.adjudicate(context)
    except UmpireUnavailable as exc:
        def pause(game: Game, session: Session) -> None:
            _require_running_phase(game, GamePhase.adjudication)
            argument = _current_adjudicating_argument(session, game)
            if argument.id != expected_argument_id:
                raise ArgumentNotCurrent("adjudication boundary changed during Umpire call")
            game.status = GameStatus.paused
            session.add(game)
            _emit_event(
                session,
                game,
                _EVENT_GAME_PAUSED,
                actor_id=argument.actor_id,
                payload={"reason": "adjudicator_unavailable", "argument_id": str(argument.id)},
            )

        _run_idempotent(engine, game_id, pause)
        raise AdjudicatorUnavailable(
            "Umpire unavailable and game cannot continue; game paused"
        ) from exc

    def mutate(game: Game, session: Session) -> None:
        _require_running_phase(game, GamePhase.adjudication)
        argument = _current_adjudicating_argument(session, game)
        if argument.id != expected_argument_id:
            raise ArgumentNotCurrent("adjudication boundary changed during Umpire call")
        _validate_proposal(proposal)
        success_targets = _validate_supersession(
            session, game, proposal.success_supersedes_effect_ids
        )
        failure_targets = _validate_supersession(
            session, game, proposal.failure_supersedes_effect_ids
        )

        # Decide the authoritative outcome. RNG is drawn only for probabilistic mode, only
        # after validation, and the index increment commits with the record (INV-RNG-003).
        if proposal.mode is AdjudicationMode.automatic_success:
            success, roll = True, None
        elif proposal.mode is AdjudicationMode.automatic_failure:
            success, roll = False, None
        else:
            roll = rng.draw(game.seed, game.rng_draw_index)
            game.rng_draw_index += 1
            success = roll <= proposal.probability  # §26: roll <= probability

        selected_supersedes = (
            proposal.success_supersedes_effect_ids
            if success
            else proposal.failure_supersedes_effect_ids
        )
        selected_targets = success_targets if success else failure_targets

        persistence.create(
            session,
            AdjudicationRecord(
                argument_id=argument.id,
                mode=proposal.mode,
                probability=proposal.probability,
                rationale=proposal.rationale,
                success_effects=list(proposal.success_effects),
                failure_effects=list(proposal.failure_effects),
                # The authoritative record stores what the selected branch actually superseded;
                # both proposals remain in raw_output for audit (D-044).
                supersedes_effect_ids=list(selected_supersedes),
                roll=roll,
                success=success,
                raw_output={
                    "mode": proposal.mode.value,
                    "probability": proposal.probability,
                    "rationale": proposal.rationale,
                    "success_effects": list(proposal.success_effects),
                    "failure_effects": list(proposal.failure_effects),
                    "success_supersedes_effect_ids": [
                        str(x) for x in proposal.success_supersedes_effect_ids
                    ],
                    "failure_supersedes_effect_ids": [
                        str(x) for x in proposal.failure_supersedes_effect_ids
                    ],
                },
            ),
        )

        # §19/§24 event order: argument_adjudicated → effect_created[…] → effect_deactivated[…].
        _emit_event(
            session,
            game,
            _EVENT_ARGUMENT_ADJUDICATED,
            actor_id=argument.actor_id,
            payload={
                "argument_id": str(argument.id),
                "adjudication_id": str(argument.id),  # the record's PK is the Argument id
                "mode": proposal.mode.value,
                "probability": proposal.probability,
                "roll": roll,
                "success": success,
            },
        )

        selected = proposal.success_effects if success else proposal.failure_effects
        created = _apply_effect_branch(
            session, game, argument, selected, selected_supersedes
        )
        for effect_id in created:
            _emit_event(
                session,
                game,
                _EVENT_EFFECT_CREATED,
                payload={
                    "effect_id": str(effect_id),
                    "source_argument_id": str(argument.id),
                },
            )
        superseded = _supersede_effects(session, selected_targets)
        superseded_by = str(created[0]) if created else None
        for effect_id in superseded:
            _emit_event(
                session,
                game,
                _EVENT_EFFECT_DEACTIVATED,
                payload={
                    "effect_id": str(effect_id),
                    "reason": _EFFECT_DEACTIVATED_SUPERSEDED,
                    "superseded_by": superseded_by,
                },
            )

        argument.status = _ARGUMENT_RESOLVED
        session.add(argument)
        session.add(game)

        captured.update(
            argument_id=argument.id,
            mode=proposal.mode,
            success=success,
            probability=proposal.probability,
            roll=roll,
            created_effect_ids=created,
            superseded_effect_ids=superseded,
            phase=game.phase,
        )

    _run_idempotent(engine, game_id, mutate)
    return AdjudicationOutcome(**captured)


# --- T-021: Actor advancement, turn sequencing and game completion ----------------------


def _resolved_current_argument(session: Session, game: Game) -> Argument:
    """Return the just-resolved current Argument, or fail closed (§20 precondition).

    Advancement is legal only *after* resolution: every Argument of this game/turn/active Actor
    must be ``resolved`` — one per Argument round so far, slots ``1..r`` (D-094; exactly one at
    budget 1) — and the current round's Argument must have a persisted
    :class:`~app.models.AdjudicationRecord`. A still-``adjudicating`` Argument (resolve not yet
    called) is refused — advancement never runs because a caller says resolution is finished;
    it revalidates authoritative state (INV-GAME-004/005).
    """
    used = game_queries.arguments_used(session, game, game.active_actor_id)
    slots = sorted(argument.budget_slot for argument in used)
    if (
        not used
        or any(argument.status != _ARGUMENT_RESOLVED for argument in used)
        or slots != list(range(1, len(slots) + 1))
    ):
        raise ArgumentNotCurrent(
            f"game {game.id!r} has no single resolved current Argument to advance from"
        )
    argument = max(used, key=lambda candidate: candidate.budget_slot)
    if session.get(AdjudicationRecord, argument.id) is None:
        raise ArgumentNotCurrent(
            f"Argument {argument.id!r} is marked resolved but has no adjudication record"
        )
    return argument


def _advance_sequencing(session: Session, game: Game) -> tuple[bool, bool]:
    """Run the one authoritative §20→§21→§22 transition; return (turn_completed, completed).

    The current Actor is located by its **position** in the persisted ``turn_order`` (never by
    row insertion order), which is not mutated. If a later Actor remains, the phase returns to
    ``argument`` on that Actor (§20). If the round just finished is below the Argument budget
    (``round_robin_v1``, D-094), the next round starts on the first Actor. Otherwise the last
    Actor of the last round has resolved. Under
    ``consequence_mode: none`` (§21 Option A, the released loop) the turn completes immediately
    through :func:`_complete_turn` (§22). Under ``bounded_effects_v1`` (Option B, D-091) the
    game enters ``consequences`` with no active Actor, and a later advancement completes the turn
    through the same :func:`_complete_turn` once the turn's consequence record exists.
    """
    order = game.turn_order
    if game.active_actor_id is None or game.active_actor_id not in order:
        raise NotActiveActor(
            f"game {game.id!r} active Actor {game.active_actor_id!r} is not in turn_order"
        )
    index = order.index(game.active_actor_id)

    if index + 1 < len(order):
        # §20: another Actor remains this round — hand the ARGUMENT phase to the next Actor.
        game.active_actor_id = order[index + 1]
        game.phase = GamePhase.argument
        _emit_event(
            session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.argument.value}
        )
        return False, False

    finished_round = game_queries.current_argument_round(session, game) or 1
    if finished_round < game.primary_arguments_per_actor:
        # §20 / D-094 round_robin_v1: the ring restarts at the first Actor for the next round,
        # so no Actor argues twice in a row. Never reached at budget 1 (the released loop).
        game.active_actor_id = order[0]
        game.phase = GamePhase.argument
        _emit_event(
            session,
            game,
            _EVENT_ARGUMENT_ROUND_STARTED,
            payload={"turn": game.turn, "round": finished_round + 1},
        )
        _emit_event(
            session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.argument.value}
        )
        return False, False

    if game.consequence_mode == _CONSEQUENCE_MODE_BOUNDED:
        # §20/§21 Option B (D-091): the last Actor has resolved; hold the turn open for its one
        # consequence pass. No Actor is active, so none is offered a decision (INV-CONSEQ-004).
        game.phase = GamePhase.consequences
        game.active_actor_id = None
        _emit_event(
            session, game, _EVENT_PHASE_STARTED, payload={"phase": GamePhase.consequences.value}
        )
        return False, False

    # §21 Option A (``none``, the released loop) → §22: the turn completes at once.
    return _complete_turn(session, game)


def _complete_turn(session: Session, game: Game) -> tuple[bool, bool]:
    """§22 turn completion, shared by both §21 options; return (turn_completed, completed).

    ``turn += 1`` into a fresh DIPLOMACY via the shared :func:`_start_turn` when
    ``turn < max_turns``, else the game completes with a consistent terminal state
    (``status``/``phase`` = complete, ``active_actor_id = None``).
    """
    _emit_event(session, game, _EVENT_TURN_COMPLETED, payload={"turn": game.turn})
    if game.turn < game.max_turns:
        _start_turn(session, game, turn=game.turn + 1)  # shared turn-start; emits phase_started
        return True, False

    # turn == max_turns: complete the game (§22). Terminal state must be internally consistent.
    game.status = GameStatus.complete
    game.phase = GamePhase.complete
    game.active_actor_id = None
    _emit_event(session, game, _EVENT_GAME_COMPLETED, payload={"turn": game.turn})
    return True, True


def adjudication_pending(engine: Engine, game_id: uuid.UUID) -> bool:
    """Read-only: is the game at the adjudication boundary with an *unresolved* Argument?

    A pure read (mutates nothing, API-INV-008) used by the admin ``…/advance`` wrapper to decide
    whether it must first resolve the current Argument (server-owned RNG via the injected Umpire)
    before advancing — without reimplementing any transition. ``True`` iff the game is running in
    the ADJUDICATION phase with exactly one adjudicating Argument that has no
    :class:`~app.models.AdjudicationRecord` yet. An absent game raises :class:`GameNotFound`.
    """
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        if game.status is not GameStatus.running or game.phase is not GamePhase.adjudication:
            return False
        candidates = game_queries.arguments_by_status(
            session, game, (_ARGUMENT_ADJUDICATING,)
        )
        if len(candidates) != 1:
            return False
        return session.get(AdjudicationRecord, candidates[0].id) is None


def advance_after_resolution(engine: Engine, game_id: uuid.UUID) -> AdvanceResult:
    """Advance the game after the current Argument has been resolved (state-machine §20/§22).

    The single authoritative advancement path — the server, never a caller, agent or Umpire,
    decides the next Actor, next turn and completion. Inside one transaction on freshly re-read
    state: require a running ADJUDICATION phase (a completed or otherwise non-adjudication game
    is refused — INV-GAME-005); confirm the current Argument is genuinely resolved (an
    authoritative :class:`~app.models.AdjudicationRecord` exists); then run §20→§21→§22. This is
    kept distinct from :func:`resolve_adjudication` so the T-020 boundary holds: resolution
    applies Effects and leaves the phase ``adjudication``; advancement alone moves the game on.

    Under ``bounded_effects_v1`` (D-091) the advancement past the last Actor stops in
    ``consequences``; advancing from ``consequences`` requires the turn's committed
    :class:`~app.models.ConsequenceRecord` and then completes the turn (§22).

    Advancement and, at a turn boundary, the next turn's start (expiry, readiness reset, phase
    setup) commit together (INV-GAME-004); the required ``turn_completed``/``game_completed``/
    ``phase_started`` events are emitted in the same transaction.
    """
    captured: dict = {}

    def mutate(game: Game, session: Session) -> None:
        if game.status is GameStatus.running and game.phase is GamePhase.consequences:
            # §21 Option B → §22 (D-091): legal only once this turn's pass has committed, so a
            # resumed game advances without re-running it (INV-CONSEQ-001, INV-GAME-005).
            if session.get(ConsequenceRecord, (game.id, game.turn)) is None:
                raise InvalidPhase(
                    f"turn {game.turn} has no committed consequence pass to advance from"
                )
            turn_completed, game_completed = _complete_turn(session, game)
        else:
            _require_running_phase(game, GamePhase.adjudication)
            _resolved_current_argument(session, game)  # revalidate the post-resolution boundary
            turn_completed, game_completed = _advance_sequencing(session, game)
        session.add(game)
        captured.update(
            turn=game.turn,
            phase=game.phase,
            status=game.status,
            active_actor_id=game.active_actor_id,
            turn_completed=turn_completed,
            game_completed=game_completed,
        )

    _run_idempotent(engine, game_id, mutate)
    return AdvanceResult(**captured)


# --- T-074: the §21 Option B consequence pass (D-091) --------------------------------------


def consequence_pending(engine: Engine, game_id: uuid.UUID) -> bool:
    """Read-only: is the game in ``consequences`` without this turn's committed pass?

    The admin ``…/advance`` wrapper uses it, like :func:`adjudication_pending`, to decide whether
    it must first run :func:`resolve_consequences` before advancing. ``False`` once the record
    exists, so a game resumed after the commit is only advanced (INV-CONSEQ-001).
    """
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        if game.status is not GameStatus.running or game.phase is not GamePhase.consequences:
            return False
        return session.get(ConsequenceRecord, (game.id, game.turn)) is None


def _require_open_consequence_pass(session: Session, game: Game) -> None:
    """The pass boundary, revalidated in every transaction that touches it (§25)."""
    _require_running_phase(game, GamePhase.consequences)
    if game.consequence_mode != _CONSEQUENCE_MODE_BOUNDED:
        raise InvalidPhase(
            f"game {game.id!r} runs consequence_mode {game.consequence_mode!r}; no pass exists"
        )
    if session.get(ConsequenceRecord, (game.id, game.turn)) is not None:
        raise ConsequencesAlreadyResolved(
            f"turn {game.turn} of game {game.id!r} already holds its consequence pass"
        )


def _build_consequence_context(session: Session, game: Game) -> ConsequenceContext:
    """The public-only resolver context (agents.md §28.1), built by the server.

    One outcome per current-turn ``argument_adjudicated`` Event, in event order, carrying only
    what that public Event and the public Argument disclose, plus the Effect ids its adjudication
    created. The active Effects are the **public** ones only. Nothing hidden is ever read into
    it: no private information, private Effect, Message, Note, Resource, Con, reply, Umpire
    rationale, probability or roll (D-091).
    """
    events = session.exec(
        select(GameEvent)
        .where(
            GameEvent.game_id == game.id,
            GameEvent.turn == game.turn,
            GameEvent.type.in_((CITABLE_EVENT_TYPE, _EVENT_EFFECT_CREATED)),
        )
        .order_by(GameEvent.id)
    ).all()
    created_by_argument: dict[str, list[str]] = {}
    for event in events:
        if event.type == _EVENT_EFFECT_CREATED and event.payload.get("source_argument_id"):
            created_by_argument.setdefault(event.payload["source_argument_id"], []).append(
                event.payload["effect_id"]
            )
    outcomes: list[dict] = []
    for event in events:
        if event.type != CITABLE_EVENT_TYPE:
            continue
        argument = session.get(Argument, uuid.UUID(event.payload["argument_id"]))
        outcomes.append(
            {
                "event_id": event.id,
                "actor_id": argument.actor_id,
                "action": argument.action,
                "desired_effect": argument.desired_effect,
                "mode": event.payload.get("mode"),
                "success": event.payload.get("success"),
                "created_effect_ids": created_by_argument.get(str(argument.id), []),
            }
        )
    active_public = session.exec(
        select(Effect)
        .where(
            Effect.game_id == game.id,
            Effect.active == True,  # noqa: E712
            Effect.visibility == _CONSEQUENCE_EFFECT_VISIBILITY,
        )
        .order_by(Effect.created_turn, Effect.id)
    ).all()
    return ConsequenceContext(
        scenario_summary=game.scenario_summary or game.scenario_id,
        turn=game.turn,
        turn_length=game.in_world_turn_length,
        outcomes=outcomes,
        active_effects=[
            {"id": str(e.id), "description": e.description, "created_turn": e.created_turn}
            for e in active_public
        ],
        # D-090/D-091: the same creation-time rules the Umpire sees; absent when none declared.
        scenario_rules=[dict(rule) for rule in game.scenario_rules] or None,
    )


def _bounded_text(value: object, what: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidConsequenceProposal(f"every consequence {what} must be a non-empty string")
    if len(value) > MAX_CONSEQUENCE_TEXT_CHARS:
        raise InvalidConsequenceProposal(
            f"each consequence {what} is limited to {MAX_CONSEQUENCE_TEXT_CHARS} characters"
        )
    return value


def _resolve_evidence(session: Session, game: Game, reference: object) -> dict:
    """Prove one evidence reference resolves (INV-CONSEQ-003); return its canonical form.

    An Event must be this game's ``argument_adjudicated`` Event of the current turn. An Effect
    must be this game's, active and public now. The check runs inside the write transaction, so a
    model that guesses an id, cites another game's or another turn's row, or names a private or
    deactivated Effect is rejected no matter what its context contained.
    """
    if not isinstance(reference, dict) or set(reference) != {"kind", "id"}:
        raise InvalidConsequenceProposal("an evidence reference must be exactly {kind, id}")
    kind, raw_id = reference["kind"], reference["id"]
    if kind == EVIDENCE_KIND_EVENT:
        if not isinstance(raw_id, int) or isinstance(raw_id, bool):
            raise InvalidConsequenceProposal("an event reference id must be an integer")
        event = session.get(GameEvent, raw_id)
        if event is None or event.game_id != game.id:
            raise InvalidConsequenceProposal(f"evidence event {raw_id} is not part of this game")
        if event.turn != game.turn:
            raise InvalidConsequenceProposal(
                f"evidence event {raw_id} is from turn {event.turn}, not the current turn"
            )
        if event.type != CITABLE_EVENT_TYPE:
            raise InvalidConsequenceProposal(
                f"evidence event {raw_id} is a {event.type!r} event, which is not citable"
            )
        return {"kind": EVIDENCE_KIND_EVENT, "id": raw_id}
    if kind == EVIDENCE_KIND_EFFECT:
        try:
            effect_id = uuid.UUID(str(raw_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise InvalidConsequenceProposal("an effect reference id must be a UUID") from exc
        effect = session.get(Effect, effect_id)
        if effect is None or effect.game_id != game.id:
            raise InvalidConsequenceProposal(f"evidence Effect {effect_id} is not part of this game")
        if not effect.active:
            raise InvalidConsequenceProposal(f"evidence Effect {effect_id} is not active")
        if effect.visibility != _CONSEQUENCE_EFFECT_VISIBILITY:
            # D-091: a public consequence may never rest on, or reveal, restricted material.
            raise InvalidConsequenceProposal(f"evidence Effect {effect_id} is not public")
        return {"kind": EVIDENCE_KIND_EFFECT, "id": str(effect_id)}
    raise InvalidConsequenceProposal(f"unknown evidence kind {kind!r}")


def _validate_consequence_proposal(
    session: Session, game: Game, proposal: object
) -> list[tuple[ProposedConsequence, list[dict]]]:
    """Validate the whole proposal (INV-CONSEQ-002/003); any violation rejects all of it."""
    if not isinstance(proposal, ConsequenceProposal) or not isinstance(proposal.effects, list):
        raise InvalidConsequenceProposal("the resolver returned no consequence proposal")
    if len(proposal.effects) > MAX_CONSEQUENCE_EFFECTS:
        raise InvalidConsequenceProposal(
            f"a consequence pass proposes at most {MAX_CONSEQUENCE_EFFECTS} Effects, "
            f"got {len(proposal.effects)}"
        )
    accepted: list[tuple[ProposedConsequence, list[dict]]] = []
    for proposed in proposal.effects:
        if not isinstance(proposed, ProposedConsequence):
            raise InvalidConsequenceProposal("every proposed consequence must be structured")
        _bounded_text(proposed.description, "description")
        _bounded_text(proposed.rationale, "rationale")
        if not isinstance(proposed.evidence, list) or not (
            1 <= len(proposed.evidence) <= MAX_EVIDENCE_REFERENCES
        ):
            raise InvalidConsequenceProposal(
                f"every consequence must cite 1–{MAX_EVIDENCE_REFERENCES} evidence references"
            )
        references = [_resolve_evidence(session, game, ref) for ref in proposed.evidence]
        keys = {(ref["kind"], ref["id"]) for ref in references}
        if len(keys) != len(references):
            raise InvalidConsequenceProposal("a consequence cites the same evidence twice")
        accepted.append((proposed, references))
    return accepted


def _pause_consequences(
    engine: Engine, game_id: uuid.UUID, expected_turn: int, failure: str
) -> None:
    """Pause the game in ``consequences`` with nothing else committed (D-091, TRD §35)."""

    def pause(game: Game, session: Session) -> None:
        _require_open_consequence_pass(session, game)
        if game.turn != expected_turn:
            raise InvalidPhase("consequence boundary changed during the resolver call")
        game.status = GameStatus.paused
        session.add(game)
        _emit_event(
            session,
            game,
            _EVENT_GAME_PAUSED,
            payload={
                "reason": _PAUSE_REASON_CONSEQUENCES,
                "turn": game.turn,
                "failure": failure,
            },
        )

    _run_idempotent(engine, game_id, pause)


def resolve_consequences(
    engine: Engine, game_id: uuid.UUID, resolver: ConsequenceResolver
) -> ConsequenceOutcome:
    """Run the turn's one consequence pass (state-machine §21 Option B, agents.md §28, D-091).

    Server-owned, following the :func:`resolve_adjudication` template:

    1. build the public-only context in a **read** transaction;
    2. call the resolver **outside** any write transaction;
    3. re-enter the guarded write, re-prove the boundary (running ``consequences``, same turn,
       no record yet), validate the whole proposal (INV-CONSEQ-002/003), then commit the record,
       every accepted Effect and their events together, or nothing (INV-GAME-004).

    An unavailable resolver or a rejected proposal commits nothing. It pauses the game instead
    and raises :class:`ConsequencesUnavailable`, so the pass is never skipped or fabricated. The
    pass draws no randomness: ``rng_draw_index`` is untouched (INV-CONSEQ-004). The phase stays
    ``consequences``; :func:`advance_after_resolution` completes the turn.
    """
    captured: dict = {}

    with session_scope(engine) as session:
        snapshot_game = session.get(Game, game_id)
        if snapshot_game is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        _require_open_consequence_pass(session, snapshot_game)
        expected_turn = snapshot_game.turn
        context = _build_consequence_context(session, snapshot_game)

    try:
        proposal = resolver.propose(context)
    except ConsequenceResolverUnavailable as exc:
        _pause_consequences(engine, game_id, expected_turn, "unavailable")
        raise ConsequencesUnavailable(
            "consequence resolver unavailable; game paused in consequences"
        ) from exc

    def mutate(game: Game, session: Session) -> None:
        _require_open_consequence_pass(session, game)
        if game.turn != expected_turn:
            raise InvalidPhase("consequence boundary changed during the resolver call")
        accepted = _validate_consequence_proposal(session, game, proposal)

        created: list[uuid.UUID] = []
        entries: list[dict] = []
        for proposed, references in accepted:
            effect = persistence.create(
                session,
                Effect(
                    game_id=game.id,
                    description=proposed.description,
                    visibility=_CONSEQUENCE_EFFECT_VISIBILITY,
                    visible_to=[],
                    source_argument_id=None,
                    source_consequence_turn=game.turn,
                    created_turn=game.turn,
                    expires_turn=None,
                    active=True,
                    supersedes=[],
                ),
            )
            created.append(effect.id)
            entries.append(
                {
                    "effect_id": str(effect.id),
                    "description": proposed.description,
                    "rationale": proposed.rationale,
                    "evidence": references,
                }
            )
        persistence.create(
            session, ConsequenceRecord(game_id=game.id, turn=game.turn, effects=entries)
        )
        for effect_id in created:
            _emit_event(
                session,
                game,
                _EVENT_EFFECT_CREATED,
                payload={"effect_id": str(effect_id), "source_consequence_turn": game.turn},
            )
        _emit_event(
            session,
            game,
            _EVENT_CONSEQUENCES_RESOLVED,
            payload={"turn": game.turn, "effect_ids": [str(x) for x in created]},
        )
        captured.update(turn=game.turn, created_effect_ids=created, phase=game.phase)

    try:
        _run_idempotent(engine, game_id, mutate)
    except InvalidConsequenceProposal as exc:
        _pause_consequences(engine, game_id, expected_turn, "invalid_proposal")
        raise ConsequencesUnavailable(
            f"consequence proposal rejected ({exc}); game paused in consequences"
        ) from exc
    return ConsequenceOutcome(**captured)


def _require_running_message_phase(game: Game) -> None:
    """Messages are legal in diplomacy and Argument (TRD phase contract)."""
    if game.status is not GameStatus.running or game.phase not in {
        GamePhase.diplomacy,
        GamePhase.argument,
    }:
        raise InvalidPhase(
            f"messaging is not allowed in {game.status.value}/{game.phase.value}"
        )
