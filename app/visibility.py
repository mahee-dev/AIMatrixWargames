"""The one authoritative visibility service (T-012, WBS 2.5).

This module is the single Actor-facing security decision path answering "what may this Actor
see in this Game?". Visibility is a **security boundary**, not a presentation concern
(TRD §36). All later Actor-facing APIs and agent-context construction must call this service;
information-security rules must not be duplicated in routes, agent code, serializers or
templates. The intended dependency direction is::

    Actor-facing caller  →  visibility service  →  persisted authoritative state

Rules implemented here, quoting the contract:

- **INV-ACTOR-002** — only the owning Actor (and authorised administration, which is not this
  layer) may access another Actor's ``private_brief``, ``objectives``,
  ``private_information`` and ``Notes``. :func:`get_actor_view` returns the requester's own
  private state and other Actors' **public projection only**, so no private field of another
  Actor is reachable through the returned value.
- **INV-MSG-002/003** — a public Message is visible to every Actor; a private Message only to
  its sender and listed recipients (:func:`can_view_message`).
- **INV-EFFECT-005** — a private Effect is never returned to an unauthorised Actor. Effect
  visibility is decided from the authoritative ``visibility``/``visible_to`` fields **only** —
  never inferred from the source Argument, description, Actor role or creator
  (:func:`can_view_effect`).
- **INV-NOTE-001** — Notes are scoped to ``game_id + actor_id``; an Actor reads only its own.
- **INV-RES-002/003** — Resource candidates are game-scoped and filtered here before search,
  ranking, snippets or counts. Public Resources are visible to every game Actor; private
  Resources only to Actors in ``visible_to``. Collection and direct reads share
  :func:`can_view_resource`, so a hidden row is indistinguishable from an absent row.
- **api.md §13 / API-INV-010** — the event stream must not become a side channel. Actor-facing
  events are filtered by :func:`can_view_event`: public lifecycle events reach everyone, a
  ``message_sent`` event only its message's parties, and ``effect_created``/``effect_deactivated``
  only Actors who may see the referenced Effect (D-014). No hidden information leaks through an
  event payload, count or id.
- Actor IDs are **local to a game** (TRD §7.2): every operation is constrained by ``game_id``
  and an Actor is always resolved by the ``(game_id, actor_id)`` pair, never by bare Actor ID.

Fail-closed: every entry point first checks the requester belongs to the Game
(:func:`require_actor`) and raises :class:`UnknownActor` otherwise, so an invalid Actor/Game
pair can never return an empty-but-successful view that would hide an authorisation defect.

Scope boundaries (kept out on purpose): Seat token→Actor resolution (T-028), FastAPI route
serialization, Resource matching/ranking/snippets (T-047), Effect **expiry** evaluation and state
transitions (T-013/T-020 — this service may filter by the stored ``active`` flag but changes no
state and does not read ``expires_turn``). This is a small explicit service, not a generic
ACL/RBAC/policy engine. Event visibility (T-035) is here
because it is an Actor-facing security decision; it reads Effects/Messages to decide but, like
everything else here, mutates nothing.

``app/`` imports no agent framework (AGENTS.md §6): only SQLModel/SQLAlchemy and app-local
modules.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlmodel import Session, select

from app.models import Actor, ActorStrategicState, Effect, GameEvent, Message, Note, Resource


class UnknownActor(Exception):
    """The requesting Actor does not belong to the given Game — visibility is refused.

    Raised fail-closed by every entry point so an unknown or malformed ``(game_id, actor_id)``
    pair never yields an empty-but-successful view (which would silently hide an authorisation
    defect, TRD §36). Carries only the caller-supplied identifiers, never internal state.
    """


@dataclass(frozen=True)
class ActorPublicView:
    """Another Actor as the requester may see it: public fields only (INV-ACTOR-002)."""

    id: str
    name: str
    public_description: str


@dataclass(frozen=True)
class ActorView:
    """What one Actor may see about the Actors in its Game.

    Its own record carries the private fields (``private_brief``, ``objectives``,
    ``private_information``); every other Actor appears only as an :class:`ActorPublicView`.
    Private fields of other Actors are therefore not representable in this value at all —
    isolation by construction (INV-ACTOR-002).
    """

    actor_id: str
    name: str
    public_description: str
    private_brief: str
    objectives: list[str]
    private_information: list[str]
    others: list[ActorPublicView]


@dataclass(frozen=True)
class VisibleEvent:
    """An Actor-view event carrying a view-scoped cursor, never its database id."""

    id: int
    turn: int
    type: str
    actor_id: str | None
    payload: dict
    created_at: datetime


# --- Identity resolution ----------------------------------------------------------------


def require_actor(session: Session, game_id: uuid.UUID, actor_id: str) -> Actor:
    """Return the ``(game_id, actor_id)`` Actor, or raise :class:`UnknownActor`.

    Actor IDs are game-local (TRD §7.2), so identity is always the composite pair — never a
    bare Actor ID resolved across games. This is the fail-closed gate every visibility entry
    point runs first.
    """
    actor = session.get(Actor, {"game_id": game_id, "id": actor_id})
    if actor is None:
        raise UnknownActor(f"actor {actor_id!r} does not belong to game {game_id!r}")
    return actor


# --- Per-row authorisation predicates (the single decision, reused everywhere) -----------


def can_view_message(actor_id: str, message: Message) -> bool:
    """Whether ``actor_id`` may see ``message`` (INV-MSG-002/003).

    Public Messages are visible to every Actor; a private Message only to its sender and its
    listed recipients. Callers must still scope by ``game_id`` (see :func:`visible_messages`).
    """
    if message.public:
        return True
    return actor_id == message.sender_actor_id or actor_id in message.recipients


def can_view_effect(actor_id: str, effect: Effect) -> bool:
    """Whether ``actor_id`` may see ``effect`` (INV-EFFECT-005).

    Decided from the authoritative ``visibility``/``visible_to`` fields only: a ``public``
    Effect is visible to all; any other (private/restricted) Effect only to the Actors named
    in ``visible_to``. Visibility is never inferred from the source Argument, description,
    Actor role or creator.
    """
    if effect.visibility == "public":
        return True
    return actor_id in effect.visible_to


def can_view_resource(actor_id: str, resource: Resource) -> bool:
    """Whether ``actor_id`` may see one Resource (INV-RES-002/003).

    Public Resources are visible to all Actors in their game; every non-public Resource is
    visible only to Actors explicitly named in ``visible_to``. Callers provide game scoping.
    """
    if resource.visibility == "public":
        return True
    return actor_id in resource.visible_to


# Event types whose visibility follows the underlying object rather than being public.
_EVENT_MESSAGE_SENT = "message_sent"
_EFFECT_EVENT_TYPES = frozenset({"effect_created", "effect_deactivated"})
_PUBLIC_EVENT_TYPES = frozenset(
    {
        "game_started", "phase_started", "actor_ready", "argument_submitted",
        "cons_revealed", "argument_adjudicated", "turn_completed",
        "game_completed", "game_paused",
        # T-074 / D-091: names only public consequence Effects; never emitted under ``none``.
        "consequences_resolved",
        # T-080 / D-094: turn and round only; never emitted at budget 1.
        "argument_round_started",
    }
)
# I-080 / D-070 / INV-CON-007: per-Actor Con participation is sealed. ``con_submitted`` and
# ``con_passed`` carry the opponent's ``actor_id`` and ``created_at`` — who challenged, who
# passed and who answered first — which is not information the game may convey under either
# reply policy. They stay in the authoritative log (§24) and the admin export, never in the
# Actor-facing projection. ``cons_revealed`` is the one disclosure D-060 permits: the frozen
# ``con_ids`` in canonical ``(actor_id, con_id)`` order, emitted only under ``visible_reply``
# when participation closes.
_SEALED_PARTICIPATION_EVENT_TYPES = frozenset({"con_submitted", "con_passed"})


def can_view_event(session: Session, actor_id: str, event: GameEvent) -> bool:
    """Whether ``actor_id`` may see ``event`` (api.md §13, API-INV-010).

    Lifecycle/state events (``game_started``, ``phase_started``, ``actor_ready``,
    ``argument_submitted``, ``cons_revealed``, ``argument_adjudicated``, ``turn_completed``,
    ``game_completed``) carry only public game facts and are visible to every Actor. Per-Actor
    Con participation events are never Actor-visible (I-080, INV-CON-007). Two more types carry
    object-scoped information and inherit that object's visibility, so the event stream never
    leaks what a direct read would hide:

    - ``message_sent`` — a public Message's event is visible to all; a private Message's event
      only to its sender (``event.actor_id``) and listed recipients (``payload.recipients``).
    - ``effect_created`` / ``effect_deactivated`` — visible only to Actors who may see the
      referenced Effect (:func:`can_view_effect`). A missing/absent Effect id is treated as a
      bare lifecycle reference with no hidden content.
    """
    payload = event.payload or {}
    if event.type in _SEALED_PARTICIPATION_EVENT_TYPES:
        return False
    if event.type == _EVENT_MESSAGE_SENT:
        message_id = payload.get("message_id")
        try:
            message_id = uuid.UUID(str(message_id))
        except (TypeError, ValueError, AttributeError):
            return False
        message = session.get(Message, message_id)
        return message is not None and message.game_id == event.game_id and can_view_message(actor_id, message)
    if event.type in _EFFECT_EVENT_TYPES:
        effect_id = payload.get("effect_id")
        try:
            effect_id = uuid.UUID(str(effect_id))
        except (TypeError, ValueError, AttributeError):
            return False
        effect = session.get(Effect, effect_id)
        return effect is not None and effect.game_id == event.game_id and can_view_effect(actor_id, effect)
    return event.type in _PUBLIC_EVENT_TYPES


# --- Actor-facing views (game-scoped at the query boundary) ------------------------------


def get_actor_view(session: Session, game_id: uuid.UUID, actor_id: str) -> ActorView:
    """Return the requester's own private Actor state plus a public projection of the others.

    Fails closed if the requester is not in the Game. Other Actors are returned as
    :class:`ActorPublicView` (public fields only), so no private field of another Actor is
    reachable through the result (INV-ACTOR-002).
    """
    me = require_actor(session, game_id, actor_id)
    others = [
        ActorPublicView(id=a.id, name=a.name, public_description=a.public_description)
        for a in session.exec(select(Actor).where(Actor.game_id == game_id)).all()
        if a.id != actor_id
    ]
    others.sort(key=lambda a: a.id)
    return ActorView(
        actor_id=me.id,
        name=me.name,
        public_description=me.public_description,
        private_brief=me.private_brief,
        objectives=list(me.objectives),
        private_information=list(me.private_information),
        others=others,
    )


def visible_messages(session: Session, game_id: uuid.UUID, actor_id: str) -> list[Message]:
    """Messages in ``game_id`` that ``actor_id`` may read (INV-MSG-002/003).

    The ``game_id`` filter is applied at the query boundary — cross-game rows are never
    fetched (TRD §7.2) — and per-row authorisation uses the single :func:`can_view_message`
    predicate. Recipient membership is a JSON-list check evaluated in Python because SQLite
    JSON-array containment is not portable; the game scoping that matters for isolation is in
    SQL. Unauthorised Messages are absent from the result, so counts cannot leak them.
    """
    require_actor(session, game_id, actor_id)
    rows = session.exec(
        select(Message).where(Message.game_id == game_id).order_by(Message.created_at, Message.id)
    ).all()
    return [m for m in rows if can_view_message(actor_id, m)]


def visible_effects(
    session: Session, game_id: uuid.UUID, actor_id: str, *, active_only: bool = False
) -> list[Effect]:
    """Effects in ``game_id`` that ``actor_id`` may see (INV-EFFECT-005).

    ``active_only`` filters by the **stored** ``active`` flag only — a convenience for callers
    that want current Effects. It performs no expiry evaluation and no state transition
    (``expires_turn`` handling is T-013/T-020); this service never mutates authoritative state.
    """
    require_actor(session, game_id, actor_id)
    rows = session.exec(select(Effect).where(Effect.game_id == game_id)).all()
    return [
        e
        for e in rows
        if can_view_effect(actor_id, e) and (not active_only or e.active)
    ]


def visible_resources(
    session: Session, game_id: uuid.UUID, actor_id: str
) -> list[Resource]:
    """Return only authorised Resource candidates, deterministically ordered by ID.

    The SQL query is constrained to ``game_id`` before the shared row predicate runs. Search
    callers receive only this already-authorised list, structurally enforcing INV-RES-002.
    """
    require_actor(session, game_id, actor_id)
    rows = session.exec(
        select(Resource).where(Resource.game_id == game_id).order_by(Resource.id)
    ).all()
    return [resource for resource in rows if can_view_resource(actor_id, resource)]


def visible_resource(
    session: Session, game_id: uuid.UUID, actor_id: str, resource_id: str
) -> Resource | None:
    """Return one authorised game-scoped Resource, or ``None`` for hidden/missing.

    The composite primary-key lookup scopes by game before considering the Resource ID. Both
    hidden and absent rows collapse to ``None`` so callers cannot distinguish them.
    """
    require_actor(session, game_id, actor_id)
    resource = session.get(Resource, {"game_id": game_id, "id": resource_id})
    if resource is None or not can_view_resource(actor_id, resource):
        return None
    return resource


def visible_events(
    session: Session,
    game_id: uuid.UUID,
    actor_id: str,
    *,
    after: int | None = None,
) -> list[VisibleEvent]:
    """Events in ``game_id`` that ``actor_id`` may see, ordered by id (api.md §13.1).

    ``game_id`` scopes the query; ``after`` (an event id) returns only strictly-later events for
    polling; per-event authorisation uses :func:`can_view_event`. Unauthorised events are absent
    from the result — never returned redacted — so no payload, count or id reveals hidden
    information (API-INV-010).
    """
    require_actor(session, game_id, actor_id)
    rows = session.exec(
        select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
    ).all()
    visible = [event for event in rows if can_view_event(session, actor_id, event)]
    start = max(after or 0, 0)
    return [
        VisibleEvent(
            id=index,
            turn=event.turn,
            type=event.type,
            actor_id=event.actor_id,
            payload=event.payload,
            created_at=event.created_at,
        )
        for index, event in enumerate(visible, start=1)
        if index > start
    ]


def visible_notes(session: Session, game_id: uuid.UUID, actor_id: str) -> list[Note]:
    """The Notes ``actor_id`` may read — strictly its own (INV-NOTE-001).

    Both ``game_id`` and ``actor_id`` constrain the query, so another Actor's Notes (including
    those of a same-named Actor in a different game) are never fetched. No listing exposes
    another Actor's Note text, count, ids or existence.
    """
    require_actor(session, game_id, actor_id)
    return list(
        session.exec(
            select(Note).where(Note.game_id == game_id, Note.actor_id == actor_id).order_by(Note.created_at, Note.id)
        ).all()
    )


def visible_strategic_state(
    session: Session, game_id: uuid.UUID, actor_id: str
) -> ActorStrategicState | None:
    """Return only this authenticated Actor/game's one private strategic state (D-048)."""
    require_actor(session, game_id, actor_id)
    return session.get(ActorStrategicState, (game_id, actor_id))
