"""The observer feed: a game's event log joined to the rows each event references.

An outside observer follows a game, live or after it ends, through two reads:

* :func:`feed` returns the events after a cursor, in order. Each item carries the event and the
  rows it references (the Message, Argument, Cons, adjudication, Effects), in the admin export's
  shapes, so the feed and ``amaw export`` never disagree. Event payloads hold only ids; the
  observer needs the text, the Umpire's probability and the server's roll.
* :func:`snapshot` returns where the game stands: turn, phase, the Actors, whom the game is
  waiting on, the active Effects, the running model-call totals and each Actor's failed
  activations.

The view is administrative and omniscient: private Messages and every Actor's material are
included, exactly as in the admin export, and nothing here goes through :mod:`app.visibility`
(TRD §41.1, D-122). It must never be served through an Actor-facing API.

Every read opens the database read-only and holds its read transaction only for the duration of
one call, so an observer can neither change game state nor keep the running game from
committing (#12, #23).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine
from sqlmodel import Session, select

from app import game_queries, persistence
from app.export import (
    _adjudication,
    _argument,
    _argument_reply,
    _con,
    _effect,
    _message,
)
from app.game_service import GameNotFound
from app.models import (
    Actor,
    AdjudicationRecord,
    AgentRun,
    Argument,
    ArgumentReply,
    Con,
    ConParticipation,
    Effect,
    Game,
    GameEvent,
    GamePhase,
    GameStatus,
    Message,
    RunManifest,
)


def read_only_engine(database_path: Path | str) -> Engine:
    """An engine whose every connection opens ``database_path`` read-only.

    SQLite refuses any write on such a connection, so no observer code path can change a game,
    and a missing file is an error rather than a new empty database.
    """
    uri = f"file:{Path(database_path).resolve()}?mode=ro"

    def connect() -> sqlite3.Connection:
        return sqlite3.connect(uri, uri=True, timeout=30, check_same_thread=False)

    return create_engine("sqlite://", creator=connect)


@contextmanager
def _read(engine: Engine) -> Iterator[Session]:
    """One short, consistent read transaction, always ended before returning."""
    session = Session(engine)
    try:
        session.connection().exec_driver_sql("BEGIN")
        yield session
    finally:
        session.rollback()
        session.close()


def _game(session: Session, game_id: uuid.UUID) -> Game:
    game = session.get(Game, game_id)
    if game is None:
        raise GameNotFound(f"game {game_id!r} does not exist")
    return game


def _get(session: Session, model: type, raw_id: Any) -> Any:
    """The row ``raw_id`` names, or ``None`` if the id is malformed or the row is missing."""
    try:
        key = uuid.UUID(str(raw_id))
    except ValueError:
        return None
    return session.get(model, key)


def _many(
    session: Session, model: type, raw_ids: Any, serialize: Callable[[Any], dict]
) -> list[dict] | None:
    if not isinstance(raw_ids, list):
        return None
    rows = [_get(session, model, raw_id) for raw_id in raw_ids]
    if any(row is None for row in rows):
        return None
    return [serialize(row) for row in rows]


def _refs(session: Session, event: GameEvent) -> dict[str, Any]:
    """The rows ``event`` references, keyed by kind; a reference that does not resolve is left out.

    Resolution is driven by the payload's id fields rather than by event type, so an event type
    added later still gets its rows joined, and an unknown type passes through with no refs.
    """
    payload = event.payload if isinstance(event.payload, dict) else {}
    refs: dict[str, Any] = {}

    if (message := _get(session, Message, payload.get("message_id"))) is not None:
        refs["message"] = _message(message)
    if (argument := _get(session, Argument, payload.get("argument_id"))) is not None:
        refs["argument"] = _argument(argument)
        if event.type == "argument_adjudicated":
            record = session.get(AdjudicationRecord, argument.id)
            if record is not None:
                refs["adjudication"] = _adjudication(record)
        if event.type == "owner_reply_submitted":
            reply = session.get(ArgumentReply, argument.id)
            if reply is not None:
                refs["reply"] = _argument_reply(reply)
    if (con := _get(session, Con, payload.get("con_id"))) is not None:
        refs["con"] = _con(con)
    if (cons := _many(session, Con, payload.get("con_ids"), _con)) is not None:
        refs["cons"] = cons
    if (effect := _get(session, Effect, payload.get("effect_id"))) is not None:
        refs["effect"] = _effect(effect)
    if (effects := _many(session, Effect, payload.get("effect_ids"), _effect)) is not None:
        refs["effects"] = effects
    return refs


def _item(session: Session, event: GameEvent) -> dict[str, Any]:
    return {
        "seq": event.id,
        "turn": event.turn,
        "type": event.type,
        "actor_id": event.actor_id,
        "created_at": event.created_at.isoformat(),
        "payload": dict(event.payload) if isinstance(event.payload, dict) else {},
        "refs": _refs(session, event),
    }


def feed(
    engine: Engine, game_id: uuid.UUID, *, after: int = 0, limit: int | None = None
) -> dict[str, Any]:
    """The events of ``game_id`` after cursor ``after``, oldest first, each joined to its rows.

    The cursor is the authoritative ``GameEvent`` id, which is monotonic and assigned in the same
    transaction as the state it records, so an event is never visible before its rows. Pass the
    returned ``cursor`` back as ``after`` to resume; an empty page holds the cursor where it was.
    """
    with _read(engine) as session:
        _game(session, game_id)
        query = (
            select(GameEvent)
            .where(GameEvent.game_id == game_id, GameEvent.id > after)  # type: ignore[operator]
            .order_by(GameEvent.id)  # type: ignore[arg-type]
        )
        if limit is not None:
            query = query.limit(limit)
        events = list(session.exec(query).all())
        items = [_item(session, event) for event in events]
    cursor = items[-1]["seq"] if items else after
    return {"items": items, "cursor": cursor}


def _waiting_on(session: Session, game: Game, actor_ids: list[str]) -> list[str]:
    """Who the game is waiting on, derived from phase and persisted state.

    Nothing records when a model call starts, so this names the Actors whose input the current
    phase still needs, not who is "thinking".
    """
    if game.status is not GameStatus.running:
        return []
    if game.phase is GamePhase.diplomacy:
        ready = set(game.diplomacy_ready_actor_ids)
        return [actor_id for actor_id in actor_ids if actor_id not in ready]
    if game.phase in (GamePhase.argument, GamePhase.reply):
        return [game.active_actor_id] if game.active_actor_id else []
    if game.phase is GamePhase.cons:
        current = game_queries.arguments_by_status(session, game, ("open_for_cons",))
        if len(current) != 1:
            return []
        argument = current[0]
        done = {
            p.actor_id for p in persistence.list_by_argument(session, ConParticipation, argument.id)
        }
        eligible = game_queries.eligible_con_actor_ids(session, game, argument)
        return [actor_id for actor_id in actor_ids if actor_id in eligible - done]
    if game.phase in (GamePhase.adjudication, GamePhase.consequences):
        return ["umpire"]
    return []


def _failure(run: AgentRun) -> dict[str, Any]:
    """A failed player activation as the snapshot reports it: when, where and why.

    Failed activations are not events, so they never appear as feed items; the snapshot carries
    them beside the model-call totals that come from the same rows, as the admin export carries
    them in ``agent_errors`` (#34). ``reason`` is the recorded cause, never model output (D-128).
    """
    return {
        "at": run.created_at.isoformat(),
        "turn": run.turn,
        "phase": run.phase,
        "reason": run.failure_reason,
    }


def snapshot(engine: Engine, game_id: uuid.UUID) -> dict[str, Any]:
    """Where ``game_id`` stands now, read in one transaction with the feed's cursor."""
    with _read(engine) as session:
        game = _game(session, game_id)
        actors = sorted(persistence.list_by_game(session, Actor, game_id), key=lambda a: a.id)
        actor_ids = [actor.id for actor in actors]
        manifest = session.get(RunManifest, game_id)
        assignments = (
            dict(manifest.manifest.get("player", {}).get("assignments", {}))
            if manifest is not None
            else {}
        )
        runs = list(persistence.list_by_game(session, AgentRun, game_id))
        effects = sorted(
            (e for e in persistence.list_by_game(session, Effect, game_id) if e.active),
            key=lambda e: (e.created_turn, str(e.id)),
        )
        last_event = session.exec(
            select(GameEvent.id)
            .where(GameEvent.game_id == game_id)
            .order_by(GameEvent.id.desc())  # type: ignore[union-attr]
            .limit(1)
        ).first()

        actor_rows = []
        for actor in actors:
            own_runs = [r for r in runs if r.actor_id == actor.id and r.role == "player"]
            failed = sorted((r for r in own_runs if not r.completed),
                            key=lambda r: (r.created_at, r.id or 0))
            actor_rows.append(
                {
                    "id": actor.id,
                    "name": actor.name,
                    "model": assignments.get(actor.id),
                    "diplomacy_ready": actor.id in game.diplomacy_ready_actor_ids,
                    "arguments_used": len(game_queries.arguments_used(session, game, actor.id)),
                    "cost_usd": sum(r.cost_usd or 0 for r in own_runs),
                    "input_tokens": sum(r.input_tokens for r in own_runs),
                    "output_tokens": sum(r.output_tokens for r in own_runs),
                    "failed_activations": len(failed),
                    "last_failure": _failure(failed[-1]) if failed else None,
                }
            )

        return {
            "game": {
                "id": str(game.id),
                "scenario_id": game.scenario_id,
                "scenario_version": game.scenario_version,
                "status": game.status.value,
                "phase": game.phase.value,
                "turn": game.turn,
                "max_turns": game.max_turns,
                "active_actor_id": game.active_actor_id,
                "primary_arguments_per_actor": game.primary_arguments_per_actor,
            },
            "actors": actor_rows,
            "waiting_on": _waiting_on(session, game, actor_ids),
            "effects": [_effect(effect) for effect in effects],
            "totals": {
                "cost_usd": sum(r.cost_usd or 0 for r in runs),
                "input_tokens": sum(r.input_tokens for r in runs),
                "output_tokens": sum(r.output_tokens for r in runs),
                "model_calls": sum(r.model_calls for r in runs),
            },
            "cursor": last_event or 0,
        }
