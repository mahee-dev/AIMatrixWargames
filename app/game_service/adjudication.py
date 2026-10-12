"""Adjudication of the current Argument: the Umpire proposes ``mode`` + ``probability``, the server
validates the proposal, draws the roll and applies the chosen branch's Effects (INV-ADJ-003)."""

from __future__ import annotations

import math
import uuid

from sqlalchemy import Engine
from sqlmodel import Session, select

from app import game_queries, persistence, rng
from app.database import session_scope
from app.game_errors import (
    AdjudicatorUnavailable,
    AlreadyAdjudicated,
    ArgumentNotCurrent,
    GameNotFound,
    InvalidAdjudication,
    InvalidSupersession,
)
from app.game_results import AdjudicationOutcome
from app.game_queries import revealed_cons
from app.models import (
    Actor,
    AdjudicationMode,
    AdjudicationRecord,
    Argument,
    ArgumentReply,
    Effect,
    Game,
    GamePhase,
    GameStatus,
)
from app.umpire import (
    MAX_EFFECTS_PER_BRANCH,
    AdjudicationContext,
    AdjudicationProposal,
    Umpire,
    UmpireUnavailable,
)
from app.game_service._common import (
    CON_REPLY_MODE_VISIBLE,
    MAX_TEXT_CHARS,
    _ADJUDICATION_EFFECT_VISIBILITY,
    _ARGUMENT_ADJUDICATING,
    _ARGUMENT_RESOLVED,
    _EFFECT_DEACTIVATED_SUPERSEDED,
    _EVENT_ARGUMENT_ADJUDICATED,
    _EVENT_EFFECT_CREATED,
    _EVENT_EFFECT_DEACTIVATED,
    _EVENT_GAME_PAUSED,
    _emit_event,
    _require_running_phase,
    _run_idempotent,
)


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
