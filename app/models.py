"""AMAW domain models — core entities and minimal transition records.

One module by design: TRD §5 says do not split modules prematurely. The nine original game
entities are joined by two restart records (D-008) and the generic Resource snapshot (T-045).
This file defines *shape* only — the entity fields and their
required/optional structure from TRD §7.1–7.9. Procedural *legality* (correct actor,
correct phase, argument budget, no self-Con, message immutability) is enforced by the engine
phases (T-014–T-016) and the persistence layer (T-008), not here.

`app/` imports no agent framework (AGENTS.md §6) — this module depends only on SQLModel /
SQLAlchemy / the standard library.

JSON columns
------------
`list[str]` and `dict` fields use ``_JSON``. UUID lists use ``_UUIDListJSON`` so SQLite stores
portable JSON strings while the Python model consistently exposes ``UUID`` objects.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum

from sqlalchemy import (
    TEXT,
    ForeignKeyConstraint,
    Index,
    LargeBinary,
    TypeDecorator,
    UniqueConstraint,
)
from sqlmodel import Field, SQLModel


class _JSON(TypeDecorator):
    """JSON-column storage for lists and dictionaries (see module docstring).

    Backed by ``TEXT``; ``json.dumps(default=str)`` makes UUID-bearing lists serializable
    without a bespoke encoder per field. Values round-trip as plain JSON (UUIDs as strings).
    """

    impl = TEXT
    cache_ok = True

    def process_bind_param(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return json.dumps(value, default=str)

    def process_result_value(self, value, dialect):  # noqa: ANN001
        if value is None:
            return None
        return json.loads(value)


class _UUIDListJSON(_JSON):
    """JSON storage that restores the annotated ``list[UUID]`` representation on load."""

    cache_ok = True

    def process_result_value(self, value, dialect):  # noqa: ANN001
        loaded = super().process_result_value(value, dialect)
        if loaded is None:
            return None
        return [item if isinstance(item, uuid.UUID) else uuid.UUID(item) for item in loaded]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# --- Enums (TRD §7.1) -------------------------------------------------------------------


class GameStatus(str, Enum):
    lobby = "lobby"
    running = "running"
    paused = "paused"
    complete = "complete"


class GamePhase(str, Enum):
    diplomacy = "diplomacy"
    argument = "argument"
    cons = "cons"
    reply = "reply"  # I-046 / D-060: visible_reply only — the owner answers the revealed Cons
    adjudication = "adjudication"
    consequences = "consequences"
    complete = "complete"


class AdjudicationMode(str, Enum):
    automatic_success = "automatic_success"
    automatic_failure = "automatic_failure"
    probabilistic = "probabilistic"


# --- Entities ---------------------------------------------------------------------------


class Game(SQLModel, table=True):
    """TRD §7.1. Authoritative phase, scenario-rule snapshot and restartable RNG state."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    scenario_id: str
    scenario_version: str | None = None
    scenario_summary: str | None = None
    # Exact package provenance captured by the one trusted loader (T-045). Optional for
    # compatibility with databases created before this additive schema extension.
    scenario_source_bytes: bytes | None = Field(default=None, sa_type=LargeBinary)
    scenario_content_sha256: str | None = None
    resource_manifest: list[dict] = Field(default_factory=list, sa_type=_JSON)
    resource_manifest_sha256: str | None = None
    # D-090 (scenario-format §11.1): public, non-executable scenario rules as ``{"id", "text"}``
    # in declaration order, snapshotted at creation. Never interpreted by the engine.
    scenario_rules: list[dict] = Field(default_factory=list, sa_type=_JSON)
    # T-081 / D-095: scheduled injects, snapshotted at creation and never shown to an Actor before
    # their release (state-machine §15.5a). Empty for a scenario without injects.
    scenario_injects: list[dict] = Field(default_factory=list, sa_type=_JSON)

    turn: int = 0
    max_turns: int

    phase: GamePhase = GamePhase.diplomacy
    status: GameStatus = GameStatus.lobby

    turn_order: list[str] = Field(default_factory=list, sa_type=_JSON)
    active_actor_id: str | None = None

    seed: int
    rng_draw_index: int = 0

    primary_arguments_per_actor: int = 1
    max_cons_per_actor: int = 3
    in_world_turn_length: str | None = None
    diplomacy_timeout_seconds: int | None = None
    cons_timeout_seconds: int | None = None

    diplomacy_ready_actor_ids: list[str] = Field(default_factory=list, sa_type=_JSON)
    # None on migrated historical rows: exports must not invent a condition that was not recorded.
    diplomacy_policy_version: str | None = None
    diplomacy_mode: str | None = None
    diplomacy_rounds_per_turn: int | None = None
    diplomacy_rounds_completed: int = 0
    diplomacy_close_reason: str | None = None
    # I-055 / D-053: persisted Con opposition condition. None on migrated historical rows for the
    # same reason as the Diplomacy policy. The cursor is the durable position of the rotating
    # challenger schedule on ``turn_order`` and is meaningful only under ``sampled_sealed``.
    con_policy_version: str | None = None
    con_mode: str | None = None
    con_challengers_per_argument: int | None = None
    con_challenger_cursor: int | None = None
    # I-046 / D-060: persisted Con-reply condition. None on migrated historical rows, again so
    # exports never invent a condition. ``reply_max_points`` is meaningful only under
    # ``visible_reply``.
    con_reply_policy_version: str | None = None
    con_reply_mode: str | None = None
    reply_max_points: int | None = None
    # T-074 / D-091: persisted Consequence Management condition (state-machine §21). None on
    # migrated historical rows, which ran the released Option A, so ``None`` reads as ``none``.
    consequence_policy_version: str | None = None
    consequence_mode: str | None = None
    # I-078: a creation-time, immutable choice — ``start_game`` refuses a flagged Game with no
    # sealed RunManifest, so a benchmark's pre-committed condition is a server-enforced fact
    # rather than the runner's write order. False on legacy rows and for every harness that does
    # not opt in; the state-machine §14 start guard is unchanged for them.
    require_run_manifest: bool = False


class Actor(SQLModel, table=True):
    """TRD §7.2. A scenario role; AMAW infers no real-world behaviour from it."""

    game_id: uuid.UUID = Field(foreign_key="game.id", primary_key=True)
    id: str = Field(primary_key=True)
    name: str

    public_description: str
    private_brief: str

    objectives: list[str] = Field(default_factory=list, sa_type=_JSON)
    private_information: list[str] = Field(default_factory=list, sa_type=_JSON)

    # Optional scenario metadata; no core semantics in MVP (TRD §7.2).
    organisation_id: str | None = None
    role: str | None = None
    # `metadata` is reserved by the SQLAlchemy Declarative API — stored as `actor_metadata`
    # (D-007); scenario YAML `metadata` maps here at the loader boundary (T-009).
    actor_metadata: dict = Field(default_factory=dict, sa_type=_JSON)


class Resource(SQLModel, table=True):
    """Immutable, generic Resource bytes snapshotted for one game (INV-RES-001, D-030).

    ``type`` and ``resource_metadata`` are opaque scenario values. Runtime code must not infer
    domain behaviour from either. The composite primary key permits the same scenario Resource ID
    in different games while preventing duplicates inside one game.
    """

    game_id: uuid.UUID = Field(foreign_key="game.id", primary_key=True)
    id: str = Field(primary_key=True)
    name: str
    type: str
    visibility: str
    visible_to: list[str] = Field(default_factory=list, sa_type=_JSON)
    tags: list[str] = Field(default_factory=list, sa_type=_JSON)
    # `metadata` is reserved by SQLAlchemy; external scenario/export shape remains `metadata`.
    resource_metadata: dict = Field(default_factory=dict, sa_type=_JSON)
    source_kind: str
    source_bytes: bytes = Field(sa_type=LargeBinary)
    attachment_name: str | None = None
    content_sha256: str
    # T-046 format projection, derived once from ``source_bytes`` at package load. Defaults keep
    # additive-schema compatibility for pre-T-046 rows; current game creation always supplies all.
    representation_kind: str = "attachment"
    media_type: str = "application/octet-stream"
    text_content: str | None = Field(default=None, sa_type=TEXT)
    size_bytes: int = 0


class Seat(SQLModel, table=True):
    """TRD §7.3 / AGENTS.md §6 / INV-ACTOR-001.

    Stores only the bearer-token **hash** — never the raw token. Actor identity is derived
    from the token at request time, never asserted by a request body.
    """

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actor.game_id", "actor.id"], name="fk_seat_actor"
        ),
        UniqueConstraint("game_id", "actor_id", name="uq_seat_game_actor"),
        UniqueConstraint("token_hash", name="uq_seat_token_hash"),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID
    actor_id: str

    token_hash: str


class Message(SQLModel, table=True):
    """TRD §7.4 / state-machine §5.

    `public=True` → visible to all Actors; otherwise only sender + listed recipients.
    Messages are immutable after sending (INV-MSG-001) — enforcement is T-014; this is shape.
    """

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "sender_actor_id"],
            ["actor.game_id", "actor.id"],
            name="fk_message_sender",
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID
    turn: int

    sender_actor_id: str
    recipients: list[str] = Field(default_factory=list, sa_type=_JSON)

    text: str
    public: bool

    created_at: datetime = Field(default_factory=_now)


class Argument(SQLModel, table=True):
    """TRD §7.5 / state-machine §6.

    `action` and `desired_effect` are required (INV-ARG-004). The 1–5 Pro ceiling, budget,
    correct actor and correct phase are legality rules enforced in T-015, not here.
    """

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"],
            ["actor.game_id", "actor.id"],
            name="fk_argument_actor",
        ),
        UniqueConstraint(
            "game_id", "turn", "actor_id", "budget_slot", name="uq_argument_budget_slot"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID
    turn: int
    actor_id: str

    action: str
    desired_effect: str
    pros: list[str] = Field(default_factory=list, sa_type=_JSON)

    # Internal 1-based slot supports configurable budgets while atomically protecting each slot.
    budget_slot: int = 1
    status: str = "submitted"
    # I-055 / D-053: the sealed challenger set chosen by the server at acceptance under
    # ``sampled_sealed``. ``None`` means unrestricted opposition (``open_cons`` or a legacy row) —
    # it is derived, never fabricated.
    challenger_actor_ids: list[str] | None = Field(default=None, sa_type=_JSON)


class Con(SQLModel, table=True):
    """TRD §7.6 / state-machine §7. No-self-Con and limits are legality rules (T-016)."""

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    argument_id: uuid.UUID = Field(foreign_key="argument.id")
    actor_id: str
    reason: str


class ArgumentReply(SQLModel, table=True):
    """I-046 / D-060: the owner's single reply to the revealed Cons (state-machine §18.1).

    One row per Argument — the primary key *is* the exactly-once guarantee (INV-REPLY-001). An
    empty ``points`` list is the recorded pass. Each point is ``{"con_id", "text"}`` and names a
    distinct frozen Con (INV-REPLY-002).
    """

    argument_id: uuid.UUID = Field(foreign_key="argument.id", primary_key=True)
    actor_id: str
    points: list[dict] = Field(default_factory=list, sa_type=_JSON)
    created_at: datetime = Field(default_factory=_now)


class ConParticipation(SQLModel, table=True):
    """Persisted Con completion state (D-008/D-043, state-machine §18)."""

    argument_id: uuid.UUID = Field(foreign_key="argument.id", primary_key=True)
    actor_id: str = Field(primary_key=True)
    completed_at: datetime = Field(default_factory=_now)


class AdjudicationRecord(SQLModel, table=True):
    """One authoritative Umpire proposal and server resolution per Argument."""

    argument_id: uuid.UUID = Field(foreign_key="argument.id", primary_key=True)
    mode: AdjudicationMode
    probability: float | None = None
    rationale: str
    success_effects: list[str] = Field(default_factory=list, sa_type=_JSON)
    failure_effects: list[str] = Field(default_factory=list, sa_type=_JSON)
    supersedes_effect_ids: list[uuid.UUID] = Field(
        default_factory=list, sa_type=_UUIDListJSON
    )
    roll: float | None = None
    success: bool
    raw_output: dict = Field(default_factory=dict, sa_type=_JSON)
    created_at: datetime = Field(default_factory=_now)


class Effect(SQLModel, table=True):
    """TRD §7.7 / state-machine §8.

    `supersedes` names prior Effect ids. Supersession later *deactivates* the referenced
    Effects (INV-EFFECT-004) — it never deletes them; deactivated Effects stay in history.
    Intentionally not a truth-maintenance system (TRD §7.7).
    """

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID = Field(foreign_key="game.id")

    description: str

    visibility: str
    visible_to: list[str] = Field(default_factory=list, sa_type=_JSON)

    source_argument_id: uuid.UUID | None = Field(default=None, foreign_key="argument.id")
    # T-074 / D-091: the turn whose consequence pass created this Effect — its origin is the
    # ``ConsequenceRecord`` keyed ``(game_id, source_consequence_turn)``. Null for every other
    # Effect, so an Effect has at most one of the two origins.
    source_consequence_turn: int | None = None
    # T-081 / D-095: the scheduled inject this Effect released. Null for every other Effect.
    source_inject_id: str | None = None

    created_turn: int
    expires_turn: int | None = None

    active: bool = True

    supersedes: list[uuid.UUID] = Field(default_factory=list, sa_type=_UUIDListJSON)


class ConsequenceRecord(SQLModel, table=True):
    """The one accepted consequence pass of a turn (T-074, D-091, state-machine §21 Option B).

    The composite primary key ``(game_id, turn)`` is INV-CONSEQ-001: at most one pass per turn,
    and the record's existence is what turn completion under ``bounded_effects_v1`` requires, so a
    resumed game never runs the pass twice. ``effects`` keeps, in proposal order, each accepted
    Effect's ``effect_id``, ``description``, ``rationale`` and its validated ``evidence``
    references. Only an accepted proposal is recorded: a rejected or unavailable one commits
    nothing (fail closed).
    """

    game_id: uuid.UUID = Field(foreign_key="game.id", primary_key=True)
    turn: int = Field(primary_key=True)
    effects: list[dict] = Field(default_factory=list, sa_type=_JSON)
    created_at: datetime = Field(default_factory=_now)


class GameEvent(SQLModel, table=True):
    """TRD §7.8. Audit/history record with a monotonic integer id — not event sourcing."""

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"],
            ["actor.game_id", "actor.id"],
            name="fk_event_actor",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    game_id: uuid.UUID
    turn: int

    type: str
    actor_id: str | None = None

    payload: dict = Field(default_factory=dict, sa_type=_JSON)
    created_at: datetime = Field(default_factory=_now)


class IdempotencyRecord(SQLModel, table=True):
    """Replay-safe record of one Actor-facing mutation (T-023, api.md §4, INV-IDEM-001…003).

    The composite primary key ``(game_id, actor_id, operation, key)`` is exactly the key scope
    the contract requires — ``game_id + seat_id + operation family`` (``actor_id`` is the seat
    proxy until Seat→Actor auth, T-028) — so the same client key never collides across games,
    Actors or operation families. ``payload_hash`` is a canonical hash of the *semantic* inputs
    only (not server-derived turn/ids/timestamps) so a genuine replay is distinguished from a
    conflicting reuse (INV-IDEM-003). ``result`` is the JSON-safe original return value, written
    in the *same* transaction as the mutation, so a rolled-back mutation claims no key and a
    fresh-engine replay still returns the original result. Append-only: never updated or deleted.
    """

    game_id: uuid.UUID = Field(primary_key=True)
    actor_id: str = Field(primary_key=True)
    operation: str = Field(primary_key=True)
    key: str = Field(primary_key=True)

    payload_hash: str
    result: dict = Field(default_factory=dict, sa_type=_JSON)
    created_at: datetime = Field(default_factory=_now)


class ResourceAccess(SQLModel, table=True):
    """TRD §29. Append-only, observational log of one *authorised* Resource operation.

    Written as a server-side side effect of ``search_resources``/``read_resource`` (agents.md
    §10) — game, Actor, turn, operation, query, Resource read and timestamp — so a full game
    yields the queryable access log the benchmark-methodology §17 research metrics consume. This
    is behavioural provenance, **not authoritative game state**: it never drives a transition and
    is excluded from the deterministic golden comparison (its ``created_at`` is a real time).
    Append-only like history (INV-EFFECT-004 discipline): rows are never updated or deleted, so a
    repeated identical search is a distinct row (§17 "repeated searches"). The monotonic integer
    id fixes insertion order for "time/turn of first access" ordering and a deterministic export.
    The logged ``actor_id`` is always the Seat token's Actor (INV-ACTOR-001).
    """

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"],
            ["actor.game_id", "actor.id"],
            name="fk_resource_access_actor",
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    game_id: uuid.UUID
    actor_id: str
    turn: int

    operation: str  # Literal["search", "read"] (TRD §29)
    query: str | None = None
    resource_id: str | None = None
    activation_id: str | None = None
    # I-099: what a *search* returned, taken from the already visibility-filtered response the
    # Actor received (api.md §10.1 holds by construction) — the count, the Resource ids in
    # returned order, and ``sha256(canonical_json(ids))``. The digest is deliberately an
    # *ordered-result-list* identity, not a set identity: the same membership in a different
    # ranking is a different result. Content-light: no snippet, name, tag or text is copied.
    # NULL on read rows and on legacy rows (unavailable, never "zero results").
    result_count: int | None = None
    result_resource_ids: list[str] | None = Field(default=None, sa_type=_JSON)
    result_set_sha256: str | None = None

    created_at: datetime = Field(default_factory=_now)


class RunManifest(SQLModel, table=True):
    """One immutable, content-hashed run configuration for a Game (TRD §28).

    The manifest includes the Game id as well as the exact scenario/Resource, prompt, model,
    framework, tool-schema, code and benchmark-assignment configuration.  The service accepts an
    identical retry but never updates a different manifest in place.
    """

    game_id: uuid.UUID = Field(foreign_key="game.id", primary_key=True)
    manifest: dict = Field(default_factory=dict, sa_type=_JSON)
    manifest_sha256: str
    created_at: datetime = Field(default_factory=_now)


class AgentRun(SQLModel, table=True):
    """Append-only provenance for one player activation or Umpire provider call (TRD §28).

    ``model_calls`` is the number of actual provider requests represented by the row. A player
    activation may require several requests around tool calls, while an Umpire row normally
    represents one. ``activation_id`` is an opaque observational join to ResourceAccess, never an
    identity or game-state capability. Aggregates and the human post-game report derive from rows.
    """

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"],
            ["actor.game_id", "actor.id"],
            name="fk_agent_run_actor",
        ),
        # I-082: one provenance row per player activation, enforced by the database rather than
        # by the service's read-before-insert. Umpire rows have no activation (NULL) and SQLite
        # treats NULLs as distinct, so they stay unconstrained. A unique *index* so a legacy
        # table can gain it additively (``initialize_database``).
        Index("uq_agent_run_activation", "game_id", "activation_id", unique=True),
    )

    id: int | None = Field(default=None, primary_key=True)
    game_id: uuid.UUID
    actor_id: str | None = None
    activation_id: str | None = None
    role: str  # "player" or "umpire"
    turn: int | None = None
    phase: str | None = None

    model_requested: str
    model_returned: str | None = None
    provider: str | None = None
    prompt_version: str
    prompt_hash: str
    model_parameters: dict = Field(default_factory=dict, sa_type=_JSON)
    reasoning_configuration: dict | None = Field(default=None, sa_type=_JSON)
    agent_framework: str
    agent_framework_version: str | None = None
    tool_schema_version: str
    context_mode: str | None = None
    memory_version: str | None = None

    input_tokens: int = 0
    output_tokens: int = 0
    # Inclusive subsets retained for reconciliation; never add them again to the parent totals.
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_calls: int = 1
    costed_model_calls: int = 0
    tool_calls: int = 0
    # I-098 / D-078: research invocations charged and Resource operations charged in this
    # activation (agents.md §18/§19). NULL means a legacy row that never recorded them.
    research_tool_calls: int | None = None
    research_operations: int | None = None
    invalid_tool_calls: int = 0
    # Empty means a legacy/unclassified aggregate; populated rows use invalid-tool-calls-v1.
    invalid_tool_call_breakdown: dict = Field(default_factory=dict, sa_type=_JSON)
    # Empty means legacy/unavailable. Populated player rows use request-topology-v1 and retain
    # only byte counts plus activation-keyed prefix digests, never request content.
    request_topology: dict = Field(default_factory=dict, sa_type=_JSON)
    stalls: int = 0
    cost_usd: float | None = None
    # ``latency_ms`` is provider/model wait only. The enclosing activation also includes local
    # tool/API work and is retained separately so throughput reports do not conflate the two.
    latency_ms: int | None = None
    activation_wall_ms: int | None = None
    completed: bool = True
    failure_reason: str | None = None
    provider_metadata: dict = Field(default_factory=dict, sa_type=_JSON)
    created_at: datetime = Field(default_factory=_now)


class Note(SQLModel, table=True):
    """TRD §7.9. Private to one Actor in one game. No chain-of-thought is required."""

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actor.game_id", "actor.id"], name="fk_note_actor"
        ),
    )

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    game_id: uuid.UUID
    actor_id: str

    text: str
    created_at: datetime = Field(default_factory=_now)


class ActorStrategicState(SQLModel, table=True):
    """One private incremental-delivery and rolling-memory state per Actor/Game (D-048).

    Event ids are an internal ordering boundary only and are never returned to the Actor. The
    opaque pending receipt makes a delivery replayable after failure/restart and binds an
    acknowledgement to exactly the snapshot the model received.
    """

    __table_args__ = (
        ForeignKeyConstraint(
            ["game_id", "actor_id"], ["actor.game_id", "actor.id"],
            name="fk_actor_strategic_state_actor",
        ),
        UniqueConstraint("pending_receipt", name="uq_actor_strategic_state_pending_receipt"),
    )

    game_id: uuid.UUID = Field(primary_key=True)
    actor_id: str = Field(primary_key=True)
    strategic_memory: dict = Field(default_factory=dict, sa_type=_JSON)
    acknowledged_event_id: int = 0
    pending_event_id: int | None = None
    pending_receipt: str | None = None
    last_ack_receipt: str | None = None
    last_ack_memory_hash: str | None = None
    updated_at: datetime = Field(default_factory=_now)
