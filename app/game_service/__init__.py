"""The authoritative game state-machine service — the **one** owner of every phase transition.

Callers (the API routes, the runner, tests) drive a game exclusively through here; no
transition logic lives in endpoints, agents, model methods, the visibility service or the CLI.
Every state-changing operation runs through :func:`_run_idempotent` →
:func:`app.persistence.apply_guarded`, which re-reads the authoritative
:class:`~app.models.Game` and evaluates the guard **inside the transaction** that writes the
mutation (state-machine §25, INV-GAME-004/005), so a mutation and the transition it triggers
commit together or not at all. Randomness is server-owned and restart-safe (:mod:`app.rng`,
INV-RNG-001…004); an Umpire only *proposes* ``mode`` + ``probability`` (INV-ADJ-003). Effects
are append-only history — expiry and supersession deactivate, never delete (INV-EFFECT-004,
D-010). Every mutation emits its §24 events via :func:`_emit_event` in the same transaction;
events are audit only and never drive state. Visibility is owned solely by
:mod:`app.visibility`. ``app/`` imports no agent framework (AGENTS.md §6).
"""

from __future__ import annotations

# The public API is re-exported here, including the errors and result values that live in their
# own modules, so callers keep using ``app.game_service.X``. Private helpers are not: a caller or
# test that needs one names the module that defines it, which is also where a monkeypatch must go.
from app.game_errors import (  # noqa: F401 — re-exported
    ActorNotInGame,
    AdjudicatorUnavailable,
    AlreadyAdjudicated,
    AlreadyReady,
    ArgumentBudgetExhausted,
    ArgumentNotCurrent,
    ConChallengerNotSelected,
    ConLimitReached,
    ConParticipationComplete,
    ConsequencesAlreadyResolved,
    ConsequencesUnavailable,
    GameNotFound,
    GameServiceError,
    IdempotencyConflict,
    IllegalStart,
    InvalidAdjudication,
    InvalidArgument,
    InvalidCon,
    InvalidConsequenceProposal,
    InvalidDiplomacyRound,
    InvalidMessage,
    InvalidNote,
    InvalidPhase,
    InvalidRecipient,
    InvalidReply,
    InvalidSupersession,
    NotActiveActor,
    NotArgumentOwner,
    ReplyAlreadySubmitted,
    SelfConForbidden,
)
from app.game_results import (  # noqa: F401 — re-exported
    AdjudicationOutcome,
    AdvanceResult,
    ArgumentResult,
    ConResult,
    ConsequenceOutcome,
    DiplomacyRoundResult,
    MessageResult,
    NoteResult,
    PassConResult,
    ReadyResult,
    ReplyResult,
    StartResult,
)
from app.game_service._common import (  # noqa: F401 — re-exported
    CON_REPLY_MODE_SEALED,
    CON_REPLY_MODE_VISIBLE,
    MAX_ARGUMENT_CHARS,
    MAX_IDEMPOTENCY_KEY_CHARS,
    MAX_PRO_CHARS,
    MAX_TEXT_CHARS,
)
from app.game_service.turns import advance_after_resolution, start_game  # noqa: F401 — re-exported
from app.game_service.diplomacy import (  # noqa: F401 — re-exported
    complete_diplomacy_round,
    ready,
    send_message,
)
from app.game_service.arguments import (  # noqa: F401 — re-exported
    make_argument,
    make_con,
    pass_con,
    submit_reply,
)
from app.game_service.notes import save_note  # noqa: F401 — re-exported
from app.game_service.adjudication import (  # noqa: F401 — re-exported
    adjudication_pending,
    resolve_adjudication,
)
from app.game_service.consequence_pass import (  # noqa: F401 — re-exported
    consequence_pending,
    resolve_consequences,
)
