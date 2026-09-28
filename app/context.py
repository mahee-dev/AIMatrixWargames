"""Actor context read-model (T-029, api.md §6.1, agents.md §4).

The server-computed view of one Actor's authorised game context. This is a **read** model: it
never mutates state. Two rules shape it:

- **All private-information filtering goes through :mod:`app.visibility`** (API-INV-002,
  AGENTS.md §6.4). ``you`` and ``effects`` come straight from the visibility service; this
  module never re-derives a visibility rule. The current Argument is *public* game state (every
  opponent must see it to Con it — there is no per-Argument visibility field), so it is not a
  visibility decision.
- **``available_actions`` is server-computed** (API-INV-009): the legal actions for *this* Actor
  in the current phase, derived here from authoritative state, never trusted from the client.
  Only actions the server can actually fulfil are advertised (api.md §6.1 "expose only legal
  available_actions"). ``search_resources``/``read_resource`` are advertised whenever the game is
  running (T-049); their endpoints are running-only and not phase-gated (D-036), so they sit in
  the same always-available set as the Notes actions.
- **``con_slots_remaining`` is server-computed**: when the Actor is eligible to participate in
  the current Cons opportunity, it reports the unused per-Actor Con capacity from the current
  Game snapshot; otherwise it is ``None`` and is omitted from the player packet's limits.

``app/`` imports no agent framework (AGENTS.md §6): only SQLModel/SQLAlchemy and app-local
modules.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from app import game_queries, visibility
from app.database import session_scope
from app.game_errors import GameNotFound
from app.game_loop import CON_REPLY_MODE_VISIBLE
from app.models import (
    Argument,
    ArgumentReply,
    ConParticipation,
    Game,
    GamePhase,
    GameStatus,
)

# Baseline player tool names (agents.md §5 / api.md §6.1). ``search_resources``/``read_resource``
# are advertised whenever the game is running (T-049); the endpoints are running-only (D-036).
_ACTION_SEND_MESSAGE = "send_message"
_ACTION_SEARCH_RESOURCES = "search_resources"
_ACTION_READ_RESOURCE = "read_resource"
_ACTION_MAKE_ARGUMENT = "make_argument"
_ACTION_MAKE_CON = "make_con"
_ACTION_PASS_CON = "pass_con"
_ACTION_REPLY_TO_CONS = "reply_to_cons"  # I-046: visible_reply only, the owner in REPLY
_ACTION_READY = "ready"
_ACTION_GET_NOTES = "get_notes"
_ACTION_SAVE_NOTE = "save_note"

# A canonical order so the serialized list is deterministic (membership, not order, is the
# contract — but a stable order keeps responses and tests legible). Matches the agents.md §5 /
# api.md §6.1 tool order.
_ACTION_ORDER = [
    _ACTION_SEND_MESSAGE,
    _ACTION_SEARCH_RESOURCES,
    _ACTION_READ_RESOURCE,
    _ACTION_MAKE_ARGUMENT,
    _ACTION_MAKE_CON,
    _ACTION_PASS_CON,
    _ACTION_REPLY_TO_CONS,
    _ACTION_READY,
    _ACTION_GET_NOTES,
    _ACTION_SAVE_NOTE,
]

_OPEN_FOR_CONS = "open_for_cons"
_AWAITING_REPLY = "awaiting_reply"
_CURRENT_STATUSES = (_OPEN_FOR_CONS, _AWAITING_REPLY)


def actor_context(
    engine: Engine, game_id: uuid.UUID, actor_id: str
) -> dict[str, Any]:
    """Assemble the Actor-specific context for ``(game_id, actor_id)`` (api.md §6.1).

    Raises :class:`app.game_errors.GameNotFound` if the game is absent and
    :class:`app.visibility.UnknownActor` if the Actor is not in the game (fail-closed — the
    caller's Seat token has already established both, so these are defensive).
    """
    with session_scope(engine) as session:
        return actor_context_in_session(session, game_id, actor_id)


def actor_context_in_session(
    session: Session, game_id: uuid.UUID, actor_id: str
) -> dict[str, Any]:
    """:func:`actor_context` read inside a caller's transaction.

    I-082: the activation endpoint delivers its bounded Message snapshot and this context as one
    read, so the two cannot straddle a phase transition (INV-GAME-004).
    """
    game = session.get(Game, game_id)
    if game is None:
        raise GameNotFound(f"game {game_id!r} does not exist")

    view = visibility.get_actor_view(session, game_id, actor_id)
    effects = visibility.visible_effects(
        session, game_id, actor_id, active_only=True
    )
    current = _current_argument(session, game)
    actions, requires_input, con_slots_remaining = _available_actions(
        session, game, actor_id
    )
    # I-046 / D-060: under the released sealed_cons policy the packet is byte-identical to
    # the released shape — no Actor ever reads a Con. Under visible_reply the frozen set,
    # the owner reply and this turn's earlier exchanges are projected to every Actor.
    visible_reply = game.con_reply_mode == CON_REPLY_MODE_VISIBLE

    packet = {
        "game_id": str(game.id),
        "scenario": {
            "id": game.scenario_id,
            "version": game.scenario_version,
            "summary": game.scenario_summary,
        },
        "turn": game.turn,
        "max_turns": game.max_turns,
        "phase": game.phase.value,
        "status": game.status.value,
        "active_actor_id": game.active_actor_id,
        "diplomacy": {
            "policy_version": game.diplomacy_policy_version,
            "mode": game.diplomacy_mode,
            "rounds_per_turn": game.diplomacy_rounds_per_turn,
            "rounds_completed": game.diplomacy_rounds_completed,
        },
        "you": {
            "actor_id": view.actor_id,
            "name": view.name,
            "public_description": view.public_description,
            "private_brief": view.private_brief,
            "objectives": list(view.objectives),
            "private_information": list(view.private_information),
        },
        "effects": [
            {
                "id": str(effect.id),
                "description": effect.description,
                "visibility": effect.visibility,
                "created_turn": effect.created_turn,
                "expires_turn": effect.expires_turn,
                "active": effect.active,
            }
            for effect in effects
        ],
        "current_argument": _serialize_argument(
            current, session=session, revealed=visible_reply
        )
        if current is not None
        else None,
        "con_slots_remaining": con_slots_remaining,
        "requires_input": requires_input,
        "available_actions": actions,
    }
    if visible_reply:
        packet["reply_max_points"] = game.reply_max_points
        packet["revealed_exchanges"] = _revealed_exchanges(session, game)
    if game.primary_arguments_per_actor > 1:
        # D-094 (api.md §6.1): round-robin Argument rounds. Absent at budget 1, keeping the
        # released packet byte-identical.
        packet["argument_budget"] = {
            "primary_arguments_per_actor": game.primary_arguments_per_actor,
            "round": (
                game_queries.current_argument_round(session, game)
                if game.status is GameStatus.running
                else None
            ),
            "used": len(game_queries.arguments_used(session, game, actor_id)),
        }
        if packet["current_argument"] is not None and current is not None:
            packet["current_argument"]["budget_slot"] = current.budget_slot
    if game.scenario_rules:
        # D-090 (agents.md §4, scenario-format §11.1): rules are scenario-wide and public, so every
        # Actor reads exactly what the Umpire applies. Absent for a rule-free scenario, keeping the
        # released packet byte-identical.
        packet["scenario"]["rules"] = [dict(rule) for rule in game.scenario_rules]
    return packet


def _serialize_argument(
    argument: Argument, *, session: Session | None = None, revealed: bool = False
) -> dict[str, Any]:
    """The public projection of the current Argument (api.md §8.1 response shape)."""
    packet: dict[str, Any] = {
        "id": str(argument.id),
        "turn": argument.turn,
        "actor_id": argument.actor_id,
        "action": argument.action,
        "desired_effect": argument.desired_effect,
        "pros": list(argument.pros),
        "status": argument.status,
    }
    if revealed and argument.status != _OPEN_FOR_CONS:
        # I-046 / INV-CON-007: the frozen Cons and the owner reply appear only once Con
        # participation has closed — never while the Argument is still open_for_cons.
        assert session is not None
        packet["cons"] = _serialize_cons(session, argument)
        packet["owner_reply"] = _serialize_reply(session, argument)
    return packet


def _serialize_cons(session: Session, argument: Argument) -> list[dict[str, Any]]:
    """The frozen set in canonical ``(actor_id, con_id)`` order — the Umpire's order, never
    commit order (INV-CON-007)."""
    return [
        {"con_id": str(con.id), "actor_id": con.actor_id, "reason": con.reason}
        for con in game_queries.revealed_cons(session, argument.id)
    ]


def _serialize_reply(session: Session, argument: Argument) -> dict[str, Any] | None:
    reply = session.get(ArgumentReply, argument.id)
    return None if reply is None else {"points": list(reply.points)}


def _revealed_exchanges(session: Session, game: Game) -> list[dict[str, Any]]:
    """This turn's earlier Arguments that went through ``reply``, oldest first (api.md §6.1).

    Bounded to the current turn on purpose: what was made public stays available to the Actors
    who argue later this turn, without an indefinitely growing transcript.
    """
    replied = session.exec(
        select(Argument, ArgumentReply)
        .join(ArgumentReply, ArgumentReply.argument_id == Argument.id)
        .where(Argument.game_id == game.id, Argument.turn == game.turn)
        .order_by(ArgumentReply.created_at, Argument.id)
    ).all()
    return [
        {
            "argument_id": str(argument.id),
            "actor_id": argument.actor_id,
            "action": argument.action,
            "desired_effect": argument.desired_effect,
            "cons": _serialize_cons(session, argument),
            "owner_reply": {"points": list(reply.points)},
            "resolved": argument.status == "resolved",
        }
        for argument, reply in replied
    ]


def _current_argument(session: Session, game: Game) -> Argument | None:
    """The single ``open_for_cons`` Argument for this game/turn/active Actor, else ``None``.

    Tolerant read (a read-model never fails closed the way a mutation guard does): zero or an
    ambiguous count yields ``None`` rather than raising.
    """
    if game.active_actor_id is None:
        return None
    rows = game_queries.arguments_by_status(session, game, _CURRENT_STATUSES)
    return rows[0] if len(rows) == 1 else None


def _available_actions(
    session: Session, game: Game, actor_id: str
) -> tuple[list[str], bool, int | None]:
    """The legal actions for ``actor_id`` right now, and whether the server awaits its input.

    Mirrors the service's own phase/eligibility guards so the two never diverge — an action is
    listed only if the corresponding :mod:`app.game_service` call would currently be accepted
    for this Actor. ``requires_input`` is ``True`` when the game is waiting on this Actor to
    advance the phase (submit an Argument, participate in Cons, or signal ready).
    """
    if game.status is not GameStatus.running:
        return [], False, None

    # Notes are private per-Actor scratch space with no phase guard, and Resource search/read are
    # running-only but not phase-gated (D-036) — all four are available while the game runs (api.md
    # §11, §10). None of them makes the server *wait* on this Actor (they are optional).
    actions = {
        _ACTION_GET_NOTES,
        _ACTION_SAVE_NOTE,
        _ACTION_SEARCH_RESOURCES,
        _ACTION_READ_RESOURCE,
    }
    requires_input = False
    con_slots_remaining: int | None = None

    if game.phase is GamePhase.diplomacy:
        actions.add(_ACTION_SEND_MESSAGE)  # messaging is legal in diplomacy
        if actor_id not in game.diplomacy_ready_actor_ids:
            actions.add(_ACTION_READY)
            requires_input = True
    elif game.phase is GamePhase.argument:
        actions.add(_ACTION_SEND_MESSAGE)  # messaging is legal in Argument too
        if actor_id == game.active_actor_id and _has_argument_budget(
            session, game, actor_id
        ):
            actions.add(_ACTION_MAKE_ARGUMENT)
            requires_input = True
    elif game.phase is GamePhase.reply:
        # I-046 / §18.1: only the owner acts, once; Cons are frozen and messaging is not legal.
        current = _current_argument(session, game)
        if (
            current is not None
            and current.status == _AWAITING_REPLY
            and current.actor_id == actor_id
            and session.get(ArgumentReply, current.id) is None
        ):
            actions.add(_ACTION_REPLY_TO_CONS)
            requires_input = True
    elif game.phase is GamePhase.cons:
        # Messaging is not legal in Cons; only the current Argument's eligible opponents act.
        current = _current_argument(session, game)
        if current is not None and _con_eligible(session, game, current, actor_id):
            actions.add(_ACTION_PASS_CON)  # a pass is always available until participation ends
            con_count = _con_count(session, current.id, actor_id)
            con_slots_remaining = max(
                game.max_cons_per_actor - con_count, 0
            )
            if con_slots_remaining > 0:
                actions.add(_ACTION_MAKE_CON)
            requires_input = True

    ordered = [action for action in _ACTION_ORDER if action in actions]
    return ordered, requires_input, con_slots_remaining


def _has_argument_budget(session: Session, game: Game, actor_id: str) -> bool:
    """Whether the active Actor still has an unused primary-Argument slot this turn (§8.1)."""
    used = len(game_queries.arguments_used(session, game, actor_id))
    return used < game.primary_arguments_per_actor


def _con_eligible(
    session: Session, game: Game, argument: Argument, actor_id: str
) -> bool:
    """An eligible challenger that has not yet completed Con participation (§9).

    Eligibility is the service's own rule — unrestricted opponents, or the sealed I-055
    challenger set — so ``available_actions`` can never disagree with ``/cons`` and ``/pass``.
    """
    if actor_id not in game_queries.eligible_con_actor_ids(session, game, argument):
        return False
    passed = session.get(
        ConParticipation, {"argument_id": argument.id, "actor_id": actor_id}
    )
    return passed is None


def _con_count(session: Session, argument_id: uuid.UUID, actor_id: str) -> int:
    return len(game_queries.cons_submitted(session, argument_id, actor_id))
