"""Neutral, read-only queries shared by the game service and Actor context.

This module owns no transitions and raises no game-service errors. Callers decide whether zero,
one or multiple rows are acceptable for their boundary: the mutation service fails closed while
the Actor read model tolerates ambiguous current state by advertising no action.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable

from sqlmodel import Session, select

from app.models import Actor, Argument, Con, Game, GamePhase


def arguments_by_status(
    session: Session, game: Game, statuses: Iterable[str]
) -> list[Argument]:
    """The active Actor's latest-round Argument this turn, if its status is in ``statuses``.

    D-094: under round-robin Argument rounds the active Actor holds one Argument per finished
    round; only the latest ``budget_slot`` is the current cycle's. Earlier rounds' resolved
    Arguments are history, never "current". At budget 1 there is one slot, so this is exactly
    the released game/turn/active-Actor/status filter.
    """
    if game.active_actor_id is None:
        return []
    used = arguments_used(session, game, game.active_actor_id)
    if not used:
        return []
    latest = max(argument.budget_slot for argument in used)
    wanted = set(statuses)
    return [a for a in used if a.budget_slot == latest and a.status in wanted]


def arguments_used(
    session: Session, game: Game, actor_id: str
) -> list[Argument]:
    """Accepted primary Arguments for one game + turn + Actor budget scope."""
    return list(
        session.exec(
            select(Argument).where(
                Argument.game_id == game.id,
                Argument.turn == game.turn,
                Argument.actor_id == actor_id,
            )
        ).all()
    )


def current_argument_round(session: Session, game: Game) -> int | None:
    """The current Argument round, derived from persisted Arguments (state-machine §17, D-094).

    In ``argument`` it is the active Actor's accepted Arguments this turn plus one; from ``cons``
    through ``adjudication`` it is the active Actor's latest ``budget_slot`` (the current
    Argument's round). ``None`` outside the Argument stage. Never stored separately, so a restart
    derives the same round from the same rows.
    """
    if game.active_actor_id is None:
        return None
    used = arguments_used(session, game, game.active_actor_id)
    if game.phase is GamePhase.argument:
        return len(used) + 1
    if game.phase in (GamePhase.cons, GamePhase.reply, GamePhase.adjudication) and used:
        return max(argument.budget_slot for argument in used)
    return None


def cons_submitted(
    session: Session, argument_id: uuid.UUID, actor_id: str
) -> list[Con]:
    """Cons submitted by one Actor against one Argument."""
    return list(
        session.exec(
            select(Con).where(
                Con.argument_id == argument_id,
                Con.actor_id == actor_id,
            )
        ).all()
    )


def actor_ids(session: Session, game_id: uuid.UUID) -> set[str]:
    """The game-local Actor ids."""
    return set(session.exec(select(Actor.id).where(Actor.game_id == game_id)).all())


def eligible_con_actor_ids(
    session: Session, game: Game, argument: Argument
) -> set[str]:
    """Game-local opponents allowed by the Argument's sealed challenger set, if any."""
    opponents = actor_ids(session, game.id) - {argument.actor_id}
    if argument.challenger_actor_ids is None:
        return opponents
    return opponents & set(argument.challenger_actor_ids)


def revealed_cons(session: Session, argument_id: uuid.UUID) -> list[Con]:
    """Cons in canonical ``(actor_id, con_id)`` order (INV-CON-007, D-045)."""
    return list(
        session.exec(
            select(Con)
            .where(Con.argument_id == argument_id)
            .order_by(Con.actor_id, Con.id)
        ).all()
    )
