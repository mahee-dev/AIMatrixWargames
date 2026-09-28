"""The game-domain errors the state machine raises, shared by mutation and read-model boundaries.

They carry no HTTP types: the API layer maps each one to a status and envelope code
(api.md §3.1). :mod:`app.game_service` re-exports every name, so ``game_service.<Error>`` stays
the calling convention.
"""


class GameServiceError(Exception):
    """Base for every game-service failure. Carries only caller-supplied identifiers."""


class GameNotFound(GameServiceError):
    """The target game does not exist — fail closed rather than operate blind."""


class IllegalStart(GameServiceError):
    """Game start refused: not in LOBBY, or scenario/Seat/turn-order setup is invalid."""


class InvalidDiplomacyRound(GameServiceError):
    """A coordinator reported a stale, skipped or malformed Diplomacy round."""


class InvalidPhase(GameServiceError):
    """The operation is not legal in the game's current phase/status (state-machine §16)."""


class ActorNotInGame(GameServiceError):
    """The acting Actor does not belong to this game (identity is game-local, TRD §7.2)."""


class InvalidRecipient(GameServiceError):
    """A Message recipient is missing, foreign to the game, or otherwise malformed."""


class InvalidMessage(GameServiceError):
    """A Message violates its required shape (empty text, or public/recipients mismatch)."""


class AlreadyReady(GameServiceError):
    """The Actor is already marked ready; readiness is not idempotent here (idempotency: T-023)."""


class NotActiveActor(GameServiceError):
    """Primary Argument refused because the caller is not the authoritative active Actor."""


class InvalidArgument(GameServiceError):
    """An Argument violates its required action/desired-effect/Pros structure."""


class ArgumentBudgetExhausted(GameServiceError):
    """The active Actor has used its persisted primary-Argument budget for this turn."""


class ArgumentNotCurrent(GameServiceError):
    """The supplied/current Argument is absent, stale, foreign or lifecycle-ambiguous."""


class InvalidCon(GameServiceError):
    """A Con violates its required shape (currently a non-empty string reason)."""


class SelfConForbidden(GameServiceError):
    """The owner of the current Argument may not oppose or pass against itself."""


class ConChallengerNotSelected(GameServiceError):
    """An opponent outside the sealed ``sampled_sealed`` challenger set (I-055, INV-CON-003)."""


class InvalidReply(GameServiceError):
    """An owner reply violates INV-REPLY-002 (cardinality, references or text)."""


class NotArgumentOwner(GameServiceError):
    """Only the owner of the current Argument may reply to its revealed Cons (INV-REPLY-001)."""


class ReplyAlreadySubmitted(GameServiceError):
    """The Argument already has its one reply; a fresh attempt is already participated."""


class ConLimitReached(GameServiceError):
    """An opponent has reached the Game snapshot's per-Argument Con limit."""


class ConParticipationComplete(GameServiceError):
    """The opponent already completed participation for this Argument."""


class InvalidAdjudication(GameServiceError):
    """The Umpire proposal violates its structured contract (mode/probability/rationale)."""


class InvalidSupersession(GameServiceError):
    """A proposed supersedes_effect_id is unknown, foreign, duplicated or ineligible."""


class AlreadyAdjudicated(GameServiceError):
    """The Argument already has one authoritative adjudication (INV-ADJ-003)."""


class AdjudicatorUnavailable(GameServiceError):
    """The Umpire was unavailable and the game was paused rather than adjudicated (TRD §35).

    Raised by :func:`resolve_adjudication` *after* it has committed the game to ``paused`` in
    response to :class:`~app.umpire.UmpireUnavailable`: no RNG draw, no
    :class:`~app.models.AdjudicationRecord` and no Effects were written (never a fabricated
    result). The API layer maps this to ``503 adjudicator_unavailable`` (api.md §3.1).
    """


class InvalidConsequenceProposal(GameServiceError):
    """A consequence proposal violates INV-CONSEQ-002/003 and is rejected as a whole (D-091).

    Internal to :func:`resolve_consequences`: the rejection commits nothing, pauses the game and
    surfaces as :class:`ConsequencesUnavailable`.
    """


class ConsequencesUnavailable(GameServiceError):
    """The consequence pass could not commit and the game was paused (D-091, state-machine §21).

    Raised by :func:`resolve_consequences` *after* it has committed the pause, when the resolver
    was unavailable or its proposal failed validation. No record, Effect or RNG draw was written:
    the pass is never fabricated or skipped. The API maps this to
    ``503 consequence_resolver_unavailable`` (api.md §3.1). Resuming the game re-runs the pass.
    """


class ConsequencesAlreadyResolved(GameServiceError):
    """This turn already holds its one consequence record (INV-CONSEQ-001)."""


class InvalidNote(GameServiceError):
    """A Note violates its required shape (currently a non-empty string text)."""


class IdempotencyConflict(GameServiceError):
    """An idempotency key was reused with a different semantic payload (INV-IDEM-002).

    The API layer maps this to ``409 idempotency_conflict`` (api.md §4.1); the service raises a
    domain error only — no HTTP type crosses this boundary (T-027 owns the mapping).
    """
