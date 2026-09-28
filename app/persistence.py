"""GameService-facing persistence operations and the transactional guard (T-008).

This is the data layer GameService calls; it sits on ``app.database.session_scope`` (T-007)
and never re-implements the session lifecycle. Two responsibilities, both from WBS §2.2.2–2.2.3:

1. **Create / read / update** for the nine TRD §7.1–7.9 entities (Game, Actor, Seat, Message,
   Argument, Con, Effect, GameEvent, Note). There is deliberately **no delete** — Effects are
   append-only history and supersession *deactivates* rather than removes (INV-EFFECT-004,
   TRD §26); every other entity is likewise never destructively removed in the lean MVP.

2. **A transactional guard wrapper** (:func:`apply_guarded`) for state-changing game
   operations. Inside one transaction it re-reads the authoritative Game, runs the caller's
   phase/eligibility guard against that *fresh* state, and commits the mutation only if the
   guard holds — otherwise it raises and the transaction rolls back with zero partial writes.
   This is the mechanism INV-GAME-004 ("Every authoritative state transition must occur
   transactionally. A request must not leave a partially transitioned game.") and INV-GAME-005
   ("The same Argument, Actor cycle or turn must not advance more than once.") demand. The
   concrete per-phase legality predicates are supplied by the phase tasks that call this
   (T-013–T-021); request-level race guards under simultaneous requests are T-024.
   state-machine §25 requires the re-check to happen "inside the same transaction that writes
   the mutation" — this wrapper is that boundary.

``app/`` imports no agent framework (AGENTS.md §6): only SQLModel/SQLAlchemy and app-local
modules.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

from sqlalchemy import Engine
from sqlmodel import Session, SQLModel, select

from app.database import write_session_scope
from app.models import Game

T = TypeVar("T", bound=SQLModel)


class GuardFailed(Exception):
    """A guarded state-changing operation was refused against fresh authoritative state.

    Raised by :func:`apply_guarded` when the caller's guard does not hold (or the target Game
    is absent). Raising rolls the transaction back, so nothing the mutation began is written
    (INV-GAME-004); the second of two identical guarded advances raises this rather than
    advancing twice (INV-GAME-005).
    """


# --- Create / read / update -------------------------------------------------------------


def create(session: Session, entity: T) -> T:
    """Persist ``entity`` within the caller's session and return it, populated with defaults.

    ``flush`` sends the INSERT so server/default-generated columns (UUIDs, the monotonic
    GameEvent id, timestamps) are available immediately; ``refresh`` reloads them onto the
    instance. The caller owns the transaction (a ``session_scope`` block), so nothing is
    durable until that scope commits.
    """
    session.add(entity)
    session.flush()
    session.refresh(entity)
    return entity


def get(session: Session, model: type[T], pk: Any) -> T | None:
    """Return the row of ``model`` with primary key ``pk``, or ``None`` if absent.

    ``pk`` is a scalar for single-column keys and a mapping for composite keys (e.g.
    ``{"game_id": ..., "id": "A"}`` for :class:`~app.models.Actor`).
    """
    return session.get(model, pk)


def list_by_game(session: Session, model: type[T], game_id: Any) -> list[T]:
    """Return every ``model`` row scoped to ``game_id`` (models carrying a ``game_id`` column).

    Con has no ``game_id`` — it is scoped to its Argument; use :func:`list_by_argument`.
    """
    return list(session.exec(select(model).where(model.game_id == game_id)).all())


def list_by_argument(session: Session, model: type[T], argument_id: Any) -> list[T]:
    """Return every ``model`` row scoped to ``argument_id`` (e.g. Cons on one Argument)."""
    return list(session.exec(select(model).where(model.argument_id == argument_id)).all())


def update(session: Session, entity: T, **fields: Any) -> T:
    """Assign ``fields`` onto ``entity`` and flush the UPDATE; return the entity.

    Only the named mutable fields change — for a Game that is turn/phase/status/active Actor/
    RNG draw index/diplomacy-ready set (TRD §7.1), all authoritative transition state that
    must survive a restart. There is no delete counterpart by design (see module docstring).
    """
    for name, value in fields.items():
        setattr(entity, name, value)
    session.add(entity)
    session.flush()
    session.refresh(entity)
    return entity


# --- Transactional guard ----------------------------------------------------------------


def apply_guarded(
    engine: Engine,
    game_id: Any,
    guard: Callable[[Game, Session], bool],
    mutate: Callable[[Game, Session], None],
) -> None:
    """Run ``mutate`` on a game transactionally, gated by ``guard`` re-checked on fresh state.

    In one transaction (``session_scope`` commits on success, rolls back on any exception):

    1. re-read the authoritative :class:`~app.models.Game` from the database;
    2. evaluate ``guard(game, session)`` against that fresh state — the phase/eligibility
       precondition the caller supplies (per state-machine §25: phase, active Actor, current
       Argument, participation, budget, game status);
    3. only if it holds, apply ``mutate(game, session)`` and let the scope commit.

    A missing game or a false guard raises :class:`GuardFailed`; any error from ``mutate``
    propagates. In every failing case the transaction rolls back, leaving zero partial writes
    (INV-GAME-004). Because the guard reads committed state each call, a precondition that the
    first successful mutation invalidates makes the second call fail — no double advancement
    (INV-GAME-005).
    """
    with write_session_scope(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise GuardFailed(f"game {game_id!r} does not exist")
        if not guard(game, session):
            raise GuardFailed(f"guard rejected the operation on game {game_id!r}")
        mutate(game, session)
