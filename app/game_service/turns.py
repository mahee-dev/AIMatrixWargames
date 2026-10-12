"""Game start, turn start and Effect expiry, and the sequencing that advances Actors, turns and
the game after an adjudication (state-machine §13, §20, §22)."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlmodel import Session, select

from app import game_queries, persistence
from app.game_errors import ArgumentNotCurrent, IllegalStart, InvalidPhase, NotActiveActor
from app.game_results import AdvanceResult, StartResult
from app.models import (
    AdjudicationRecord,
    Argument,
    ConsequenceRecord,
    Effect,
    Game,
    GamePhase,
    GameStatus,
    RunManifest,
    Seat,
)
from app.game_service._common import (
    _ARGUMENT_RESOLVED,
    _CONSEQUENCE_MODE_BOUNDED,
    _EFFECT_DEACTIVATED_EXPIRED,
    _EVENT_ARGUMENT_ROUND_STARTED,
    _EVENT_EFFECT_CREATED,
    _EVENT_EFFECT_DEACTIVATED,
    _EVENT_GAME_COMPLETED,
    _EVENT_GAME_STARTED,
    _EVENT_PHASE_STARTED,
    _EVENT_TURN_COMPLETED,
    _emit_event,
    _require_running_phase,
    _run_idempotent,
)


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
