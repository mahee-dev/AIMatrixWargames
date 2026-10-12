"""The primary Argument, sealed challenger selection, Cons and the owner's reply to revealed Cons
(state-machine §17–§18.1)."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlmodel import Session, select

from app import game_queries, persistence
from app.game_errors import (
    ActorNotInGame,
    ArgumentBudgetExhausted,
    ArgumentNotCurrent,
    ConChallengerNotSelected,
    ConLimitReached,
    ConParticipationComplete,
    GameServiceError,
    InvalidArgument,
    InvalidCon,
    InvalidReply,
    NotActiveActor,
    NotArgumentOwner,
    ReplyAlreadySubmitted,
    SelfConForbidden,
)
from app.game_results import ArgumentResult, ConResult, PassConResult, ReplyResult
from app.game_queries import eligible_con_actor_ids, revealed_cons
from app.models import Argument, ArgumentReply, Con, ConParticipation, Game, GamePhase
from app.game_service._common import (
    CON_REPLY_MODE_VISIBLE,
    MAX_ARGUMENT_CHARS,
    MAX_PRO_CHARS,
    MAX_TEXT_CHARS,
    _ARGUMENT_ADJUDICATING,
    _ARGUMENT_AWAITING_REPLY,
    _ARGUMENT_OPEN_FOR_CONS,
    _CON_MODE_SAMPLED_SEALED,
    _EVENT_ARGUMENT_SUBMITTED,
    _EVENT_CONS_REVEALED,
    _EVENT_CON_CHALLENGERS_SELECTED,
    _EVENT_CON_PASSED,
    _EVENT_CON_SUBMITTED,
    _EVENT_OWNER_REPLY_SUBMITTED,
    _EVENT_PHASE_STARTED,
    _MAX_PROS,
    _OP_ARGUMENT,
    _OP_CON,
    _OP_PASS_CON,
    _OP_REPLY,
    _UNRESOLVED_ARGUMENT_STATUSES,
    _emit_event,
    _require_running_phase,
    _run_idempotent,
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
