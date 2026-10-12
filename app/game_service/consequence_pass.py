"""The state-machine §21 Option B consequence pass that closes a turn: bounded, public,
non-expiring Effects cited from the turn's evidence (D-091)."""

from __future__ import annotations

import uuid

from sqlalchemy import Engine
from sqlmodel import Session, select

from app import persistence
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
from app.game_errors import (
    ConsequencesAlreadyResolved,
    ConsequencesUnavailable,
    GameNotFound,
    InvalidConsequenceProposal,
    InvalidPhase,
)
from app.game_results import ConsequenceOutcome
from app.models import Argument, ConsequenceRecord, Effect, Game, GameEvent, GamePhase, GameStatus
from app.game_service._common import (
    _CONSEQUENCE_EFFECT_VISIBILITY,
    _CONSEQUENCE_MODE_BOUNDED,
    _EVENT_CONSEQUENCES_RESOLVED,
    _EVENT_EFFECT_CREATED,
    _EVENT_GAME_PAUSED,
    _PAUSE_REASON_CONSEQUENCES,
    _emit_event,
    _require_running_phase,
    _run_idempotent,
)


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
