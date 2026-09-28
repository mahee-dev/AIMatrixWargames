"""Server-backed incremental activation delivery and rolling strategic memory (I-034).

This is Actor-owned agent state, not authoritative game state: acknowledging it never changes
phase, readiness, legality or Effects. Message visibility remains exclusively in
``app.visibility``; this module only selects from already-authorised rows.
"""

from __future__ import annotations

import json
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import Engine, func
from sqlmodel import Session, select

from amaw_contracts import (
    STRATEGIC_MEMORY_MAX_BYTES,
    canonical_json,
    canonical_json_size,
    canonical_sha256,
)
from app import context, visibility
from app.database import write_session_scope
from app.models import ActorStrategicState, Game, GameEvent, Message

CONTEXT_MODE = "incremental-context-v1"
MEMORY_VERSION = "strategic-memory-v1"
MEMORY_FIELDS = (
    "commitments_made",
    "commitments_or_offers_received",
    "outstanding_offers_and_negotiations",
    "red_lines_and_constraints",
    "uncertain_or_disputed_claims",
    "relationships_and_likely_coalitions",
    "strategic_assessment",
    "intended_next_action",
)
_LIST_FIELDS = MEMORY_FIELDS[:6]
_TEXT_FIELDS = MEMORY_FIELDS[6:]

EMPTY_STRATEGIC_MEMORY: dict[str, Any] = {
    **{field: [] for field in _LIST_FIELDS},
    **{field: "" for field in _TEXT_FIELDS},
}


class ActivationContextError(ValueError):
    """Invalid memory or delivery acknowledgement."""


class InvalidStrategicMemory(ActivationContextError):
    """The replacement memory is not the exact bounded v1 structure."""


class ActivationReceiptConflict(ActivationContextError):
    """The receipt is foreign, stale, or replayed with different memory."""


@dataclass(frozen=True)
class ActivationDelivery:
    receipt: str
    messages: list[dict[str, Any]]
    strategic_memory: dict[str, Any]


def normalize_strategic_memory(value: dict[str, Any]) -> dict[str, Any]:
    """Validate and copy the complete ``strategic-memory-v1`` state."""
    if not isinstance(value, dict) or set(value) != set(MEMORY_FIELDS):
        raise InvalidStrategicMemory("strategic_memory must contain exactly the v1 fields")
    normalized: dict[str, Any] = {}
    for field in _LIST_FIELDS:
        items = value[field]
        if not isinstance(items, list) or any(not isinstance(item, str) for item in items):
            raise InvalidStrategicMemory(f"{field} must be a list of strings")
        normalized[field] = [item.strip() for item in items if item.strip()]
    for field in _TEXT_FIELDS:
        text = value[field]
        if not isinstance(text, str):
            raise InvalidStrategicMemory(f"{field} must be a string")
        normalized[field] = text.strip()
    size = canonical_json_size(normalized)
    if size > STRATEGIC_MEMORY_MAX_BYTES:
        raise InvalidStrategicMemory(
            "strategic_memory exceeds "
            f"{STRATEGIC_MEMORY_MAX_BYTES} serialized bytes (actual {size})"
        )
    return normalized


def _empty_memory() -> dict[str, Any]:
    return json.loads(canonical_json(EMPTY_STRATEGIC_MEMORY))


def _message_projection(message: Message) -> dict[str, Any]:
    return {
        "id": str(message.id),
        "turn": message.turn,
        "sender_actor_id": message.sender_actor_id,
        "recipients": list(message.recipients),
        "public": message.public,
        "text": message.text,
        "created_at": message.created_at.isoformat(),
    }


def _delivered_messages(
    session: Session, state: ActorStrategicState
) -> list[dict[str, Any]]:
    pending = state.pending_event_id
    if pending is None:
        return []
    events = session.exec(
        select(GameEvent).where(
            GameEvent.game_id == state.game_id,
            GameEvent.type == "message_sent",
            GameEvent.id > state.acknowledged_event_id,
            GameEvent.id <= pending,
        ).order_by(GameEvent.id)
    ).all()
    delivered: list[dict[str, Any]] = []
    for event in events:
        message_id = (event.payload or {}).get("message_id")
        try:
            message = session.get(Message, uuid.UUID(str(message_id)))
        except (TypeError, ValueError, AttributeError):
            message = None
        if (
            message is not None
            and message.game_id == state.game_id
            and message.sender_actor_id != state.actor_id
            and visibility.can_view_message(state.actor_id, message)
        ):
            delivered.append(_message_projection(message))
    return delivered


def begin_activation(
    engine: Engine, game_id: uuid.UUID, actor_id: str
) -> ActivationDelivery:
    """Create or replay the Actor's one pending bounded delivery snapshot."""
    with write_session_scope(engine) as session:
        return begin_activation_in_session(session, game_id, actor_id)


def begin_activation_with_context(
    engine: Engine, game_id: uuid.UUID, actor_id: str
) -> tuple[ActivationDelivery, dict[str, Any]]:
    """The delivery snapshot and the Actor's context as one transactional projection.

    I-082 / INV-GAME-004: read in the same ``BEGIN IMMEDIATE`` transaction that fixes the
    delivery boundary, so the context cannot describe a later phase than the Messages it is
    delivered with.
    """
    with write_session_scope(engine) as session:
        delivery = begin_activation_in_session(session, game_id, actor_id)
        return delivery, context.actor_context_in_session(session, game_id, actor_id)


def begin_activation_in_session(
    session: Session, game_id: uuid.UUID, actor_id: str
) -> ActivationDelivery:
    """:func:`begin_activation` inside a caller's write transaction."""
    if session.get(Game, game_id) is None:
        raise ActivationContextError(f"game {game_id} does not exist")
    visibility.require_actor(session, game_id, actor_id)
    state = visibility.visible_strategic_state(session, game_id, actor_id)
    if state is None:
        state = ActorStrategicState(
            game_id=game_id,
            actor_id=actor_id,
            strategic_memory=_empty_memory(),
        )
        session.add(state)
        session.flush()
    if state.pending_receipt is None:
        boundary = session.exec(
            select(func.max(GameEvent.id)).where(GameEvent.game_id == game_id)
        ).one()
        state.pending_event_id = int(boundary or 0)
        state.pending_receipt = secrets.token_urlsafe(24)
        state.updated_at = datetime.now(timezone.utc)
        session.add(state)
        session.flush()
    return ActivationDelivery(
        receipt=state.pending_receipt,
        messages=_delivered_messages(session, state),
        strategic_memory=normalize_strategic_memory(state.strategic_memory),
    )


def acknowledge_activation(
    engine: Engine,
    game_id: uuid.UUID,
    actor_id: str,
    *,
    receipt: str,
    strategic_memory: dict[str, Any],
) -> ActorStrategicState:
    """Atomically replace memory and advance exactly the receipt's pending boundary."""
    memory = normalize_strategic_memory(strategic_memory)
    memory_hash = canonical_sha256(memory)
    with write_session_scope(engine) as session:
        visibility.require_actor(session, game_id, actor_id)
        state = visibility.visible_strategic_state(session, game_id, actor_id)
        if state is None:
            raise ActivationReceiptConflict("no activation delivery is pending")
        if receipt == state.last_ack_receipt:
            if memory_hash != state.last_ack_memory_hash:
                raise ActivationReceiptConflict(
                    "delivery receipt was already acknowledged with different memory"
                )
            return ActorStrategicState.model_validate(state.model_dump())
        if receipt != state.pending_receipt or state.pending_event_id is None:
            raise ActivationReceiptConflict("delivery receipt is not current for this Actor/game")
        state.strategic_memory = memory
        state.acknowledged_event_id = state.pending_event_id
        state.pending_event_id = None
        state.pending_receipt = None
        state.last_ack_receipt = receipt
        state.last_ack_memory_hash = memory_hash
        state.updated_at = datetime.now(timezone.utc)
        session.add(state)
        session.flush()
        return ActorStrategicState.model_validate(state.model_dump())
