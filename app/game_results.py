"""The frozen result values the state machine returns from each operation.

Each is captured inside the operation's transaction and is safe to read after it closes. An
idempotent replay rebuilds the same type from its stored JSON form. :mod:`app.game_service`
re-exports every name.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from app.models import AdjudicationMode, GamePhase, GameStatus


@dataclass(frozen=True)
class StartResult:
    """The post-start authoritative game state (state-machine §15)."""

    turn: int
    phase: GamePhase
    status: GameStatus
    active_actor_id: str | None


@dataclass(frozen=True)
class MessageResult:
    """The persisted Message's identifying fields (its content stays in the store)."""

    id: uuid.UUID
    turn: int
    sender_actor_id: str
    recipients: list[str]
    public: bool


@dataclass(frozen=True)
class ReadyResult:
    """The outcome of a readiness signal, including whether it closed diplomacy (§16)."""

    actor_id: str
    ready: bool
    turn: int
    phase: GamePhase
    active_actor_id: str | None
    diplomacy_closed: bool


@dataclass(frozen=True)
class DiplomacyRoundResult:
    """Durable result of settling one complete I-028 Diplomacy round."""

    turn: int
    round_number: int
    rounds_completed: int
    phase: GamePhase
    closed: bool
    close_reason: str | None


@dataclass(frozen=True)
class ArgumentResult:
    """The accepted primary Argument and its authoritative budget slot."""

    id: uuid.UUID
    turn: int
    actor_id: str
    budget_slot: int
    status: str


@dataclass(frozen=True)
class ConResult:
    """A persisted Con bound to the authoritative current Argument."""

    id: uuid.UUID
    argument_id: uuid.UUID
    actor_id: str
    reason: str


@dataclass(frozen=True)
class ReplyResult:
    """The owner's single persisted reply to the revealed Cons (I-046, api.md §9.3)."""

    argument_id: uuid.UUID
    actor_id: str
    points: list[dict]


@dataclass(frozen=True)
class PassConResult:
    """Persisted explicit completion and whether this operation closed the Cons phase."""

    argument_id: uuid.UUID
    actor_id: str
    passed: bool
    phase: GamePhase
    cons_closed: bool


@dataclass(frozen=True)
class NoteResult:
    """A persisted private Note (api.md §11). ``created_at`` is restored on idempotent replay."""

    id: uuid.UUID
    actor_id: str
    text: str
    created_at: datetime


@dataclass(frozen=True)
class AdjudicationOutcome:
    """The authoritative result of resolving one Argument (T-019/T-020).

    ``roll`` is ``None`` for automatic modes and the server draw for probabilistic ones.
    ``created_effect_ids`` are the Effects materialised from the selected branch;
    ``superseded_effect_ids`` are the prior Effects this resolution deactivated. ``phase``
    remains ``adjudication`` — Actor advancement is the separate
    :func:`advance_after_resolution` (T-021).
    """

    argument_id: uuid.UUID
    mode: AdjudicationMode
    success: bool
    probability: float | None
    roll: float | None
    created_effect_ids: list[uuid.UUID]
    superseded_effect_ids: list[uuid.UUID]
    phase: GamePhase


@dataclass(frozen=True)
class ConsequenceOutcome:
    """The committed consequence pass of one turn (T-074, state-machine §21 Option B).

    ``created_effect_ids`` are the accepted Effects, in proposal order; empty for a valid
    zero-Effect pass. ``phase`` remains ``consequences``: turn completion is the separate
    :func:`advance_after_resolution`.
    """

    turn: int
    created_effect_ids: list[uuid.UUID]
    phase: GamePhase


@dataclass(frozen=True)
class AdvanceResult:
    """The authoritative post-advancement game state (state-machine §20/§21/§22, T-021).

    ``turn_completed`` is ``True`` when this advancement ended a turn (the last Actor had
    resolved); ``game_completed`` is ``True`` when it also completed the game at
    ``turn == max_turns``. For a mid-turn advancement both are ``False`` and ``active_actor_id``
    is the next Actor in ``turn_order``.
    """

    turn: int
    phase: GamePhase
    status: GameStatus
    active_actor_id: str | None
    turn_completed: bool
    game_completed: bool
