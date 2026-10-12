"""Diplomacy: private and public Messages, readiness and the close of a diplomacy round."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlmodel import Session

from app import game_queries, persistence
from app.game_errors import (
    ActorNotInGame,
    AlreadyReady,
    InvalidDiplomacyRound,
    InvalidMessage,
    InvalidRecipient,
)
from app.game_results import DiplomacyRoundResult, MessageResult, ReadyResult
from app.models import Game, GamePhase, GameStatus, Message
from app.game_service._common import (
    MAX_TEXT_CHARS,
    _EVENT_ACTOR_READY,
    _EVENT_DIPLOMACY_CLOSED,
    _EVENT_DIPLOMACY_ROUND_COMPLETED,
    _EVENT_MESSAGE_SENT,
    _EVENT_PHASE_STARTED,
    _OP_MESSAGE,
    _OP_READY,
    _emit_event,
    _require_running_message_phase,
    _require_running_phase,
    _run_idempotent,
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
