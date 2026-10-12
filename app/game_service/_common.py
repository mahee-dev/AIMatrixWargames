"""What every game operation shares: the constants, idempotent execution, event emission and the
phase guards.

Every state-changing operation runs through :func:`_run_idempotent`, which evaluates its guard inside
the transaction that writes the mutation (state-machine §25, INV-GAME-004/005), and emits its §24
events through :func:`_emit_event` in that same transaction.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import uuid
from datetime import datetime
from enum import Enum

from sqlalchemy import Engine
from sqlmodel import Session

from app import persistence
from app.game_errors import GameNotFound, IdempotencyConflict, InvalidPhase
from app.models import Game, GameEvent, GamePhase, GameStatus, IdempotencyRecord


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


def _require_running_message_phase(game: Game) -> None:
    """Messages are legal in diplomacy and Argument (TRD phase contract)."""
    if game.status is not GameStatus.running or game.phase not in {
        GamePhase.diplomacy,
        GamePhase.argument,
    }:
        raise InvalidPhase(
            f"messaging is not allowed in {game.status.value}/{game.phase.value}"
        )
