"""The Actor-facing HTTP surface: one Seat's context, diplomacy, Arguments, Cons, Notes,
Resources and events (api.md §6–§13).

Identity comes only from the Seat bearer token (:func:`app.api.seat_identity`, INV-ACTOR-001),
and every read is filtered through :mod:`app.visibility`. This module never imports the
administrative export: private data stays on the admin surface in :mod:`app.api_admin`. The
routes are registered by :func:`app.api.create_app`.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Sequence
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Header, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import Engine

from amaw_contracts import canonical_json
from app import activation_context
from app import context as context_module
from app import game_service, resources, visibility
from app.api import (
    APIErrorForIdempotency,
    NotFound,
    SeatIdentity,
    ValidationFailed,
    WrongPhase,
    get_request_engine,
    idempotency_key,
    seat_identity,
)
from app.database import session_scope
from app.models import Game, Message, ResourceAccess


class _MessageIn(BaseModel):
    """Body of POST /messages (api.md §7.2). Any ``actor_id``/``sender`` field is ignored —
    identity comes only from the Seat token (INV-ACTOR-001)."""

    recipients: list[str] = []
    public: bool
    text: str


class _ArgumentIn(BaseModel):
    """Body of POST /arguments (api.md §8.1). Structural rules (non-empty, 1–max Pros) are
    enforced by the service so there is one source of truth; both paths render 422."""

    action: str
    desired_effect: str
    pros: list[str]


class _ConIn(BaseModel):
    """Body of POST /arguments/{id}/cons (api.md §9.1)."""

    reason: str


class _ReplyPointIn(BaseModel):
    """One reply point (api.md §9.3). Extra fields — an action, desired effect or pro — are
    rejected: a reply answers Cons, it never amends the Argument (INV-REPLY-002)."""

    model_config = ConfigDict(extra="forbid")

    con_id: str
    text: str


class _ReplyIn(BaseModel):
    """Body of POST /arguments/{id}/reply (api.md §9.3); ``points: []`` is the explicit pass."""

    model_config = ConfigDict(extra="forbid")

    points: list[_ReplyPointIn]


class _NoteIn(BaseModel):
    """Body of POST /notes (api.md §11.2). Any owner/actor field is ignored (INV-ACTOR-001)."""

    text: str


class _ActivationAckIn(BaseModel):
    """Complete replacement memory for one opaque activation delivery (api.md §6.2)."""

    strategic_memory: dict[str, Any]


def _serialize_message(message: Message) -> dict[str, Any]:
    """The documented Message projection (api.md §7.1); no internal schema fields."""
    return {
        "id": str(message.id),
        "turn": message.turn,
        "sender_actor_id": message.sender_actor_id,
        "recipients": list(message.recipients),
        "public": message.public,
        "text": message.text,
        "created_at": message.created_at.isoformat(),
    }


def _require_running_resource_access(session, game_id: uuid.UUID) -> Game:  # noqa: ANN001
    """Enforce D-036 before any Resource candidate lookup."""
    game = session.get(Game, game_id)
    if game is None:
        raise NotFound("Resource not found.")
    if game.status.value != "running":
        raise WrongPhase(
            "Resources are available only while the game is running.",
            {"status": game.status.value},
        )
    return game


def _log_resource_access(
    session,  # noqa: ANN001
    game: Game,
    actor_id: str,
    *,
    operation: str,
    query: str | None = None,
    resource_id: str | None = None,
    activation_id: str | None = None,
    results: Sequence[resources.ResourceSearchResult] | None = None,
) -> None:
    """Append the TRD §29 resource-access row for one *authorised* Resource operation.

    Written inside the endpoint's own ``session_scope`` (so it commits with the request) as the
    agents.md §10 side effect. It is append-only, observational provenance for
    benchmark-methodology §17 — never authoritative state — so it records only what the Actor was
    authorised to and did access: a denied read (``404``) and a rejected blank query never reach
    here (D-038). ``turn`` is the Game's turn at access time; ``actor_id`` is the Seat token's
    Actor (INV-ACTOR-001).

    I-099: a search row also records the identity of what it returned — ``results`` is the very
    list the response is built from, already visibility-filtered (api.md §10.1), so a hidden
    match can no more reach the log than the response. The three fields are set together or not
    at all; a read passes no ``results`` and records NULL.
    """
    row = ResourceAccess(
        game_id=game.id,
        actor_id=actor_id,
        turn=game.turn,
        operation=operation,
        query=query,
        resource_id=resource_id,
        activation_id=activation_id,
    )
    if results is not None:
        # Already bounded to ``resources.MAX_SEARCH_RESULTS`` by ``search_resources``.
        ids = [result.id for result in results]
        row.result_count = len(ids)
        row.result_resource_ids = ids
        row.result_set_sha256 = hashlib.sha256(canonical_json(ids)).hexdigest()
    session.add(row)


def register_player_routes(app: FastAPI) -> None:
    """Register the Actor-facing player endpoints (api.md §6–§13).

    Each authenticates via :func:`seat_identity` (identity from the token, never a body —
    INV-ACTOR-001); each mutating endpoint threads the ``Idempotency-Key`` header to the service
    (API-INV-007). No endpoint advances authoritative state (API-INV-008) or filters visibility
    itself — all Actor-facing filtering is owned by :mod:`app.visibility` (API-INV-002).
    """

    @app.get("/games/{game_id}/context")
    def _get_context(
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        # The read-model is server-computed and visibility-filtered (api.md §6.1, API-INV-009).
        return context_module.actor_context(engine, identity.game_id, identity.actor_id)

    @app.post("/games/{game_id}/activations")
    def _begin_activation(
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        delivery, current = activation_context.begin_activation_with_context(
            engine, identity.game_id, identity.actor_id
        )
        return {
            **current,
            "delivery_receipt": delivery.receipt,
            "context_mode": activation_context.CONTEXT_MODE,
            "memory_version": activation_context.MEMORY_VERSION,
            "messages_received": delivery.messages,
            "strategic_memory": delivery.strategic_memory,
        }

    @app.post("/games/{game_id}/activations/{receipt}/ack")
    def _acknowledge_activation(
        receipt: str,
        body: _ActivationAckIn,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        try:
            state = activation_context.acknowledge_activation(
                engine,
                identity.game_id,
                identity.actor_id,
                receipt=receipt,
                strategic_memory=body.strategic_memory,
            )
        except activation_context.InvalidStrategicMemory as exc:
            raise ValidationFailed(str(exc)) from exc
        except activation_context.ActivationReceiptConflict as exc:
            raise APIErrorForIdempotency(str(exc)) from exc
        return {
            "acknowledged": True,
            "context_mode": activation_context.CONTEXT_MODE,
            "memory_version": activation_context.MEMORY_VERSION,
            "strategic_memory": dict(state.strategic_memory),
        }

    @app.get("/games/{game_id}/messages")
    def _get_messages(
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        after: int | None = None,  # documented cursor; the authoritative cursor is /events (D-015)
    ) -> dict[str, Any]:
        with session_scope(engine) as session:
            messages = visibility.visible_messages(
                session, identity.game_id, identity.actor_id
            )
            return {"messages": [_serialize_message(m) for m in messages]}

    @app.get("/games/{game_id}/resources/search")
    def _search_resources(
        q: str,
        request: Request,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        activation_id: Annotated[uuid.UUID | None, Header(alias="X-AMAW-Activation-ID")] = None,
        contract: str = resources.SEARCH_CONTRACT_DEFAULT,
    ) -> dict[str, Any]:
        with session_scope(engine) as session:
            game = _require_running_resource_access(session, identity.game_id)
            try:
                query, folded_query = resources.normalize_search_query(q)
                resources.validate_search_contract(contract)
            except (resources.InvalidResourceQuery, resources.InvalidSearchContract) as exc:
                # Invalid search input is not a performed search and writes no access-log row.
                raise ValidationFailed(str(exc)) from exc

            def perform_authorised_search() -> resources.SearchReplayValue:
                candidates = visibility.visible_resources(
                    session, identity.game_id, identity.actor_id
                )
                # Always explicit: the resolved contract is what ranks, never this module's
                # own default (I-112 — the two are no longer the same value for legacy input).
                _, matched = resources.search_resources(candidates, q, contract=contract)
                return tuple(matched)

            if activation_id is None:
                results = perform_authorised_search()
            else:
                # The cached value was visibility-filtered for this exact Game and Actor before
                # matching. Only immutable result projections are retained; each caller still
                # writes its own ResourceAccess row below, preserving repeated-search telemetry.
                cache_key: resources.SearchReplayKey = (
                    str(identity.game_id),
                    identity.actor_id,
                    str(activation_id),
                    contract,
                    folded_query,
                )
                results = request.app.state.resource_search_replay_cache.get_or_compute(
                    cache_key, perform_authorised_search
                )
            # Log the raw query the Actor sent (faithful behavioural record, TRD §29); a
            # zero-result search still logs — the query is the datum (§17).
            _log_resource_access(
                session,
                game,
                identity.actor_id,
                operation="search",
                query=q,
                activation_id=str(activation_id) if activation_id is not None else None,
                results=results,
            )
            return {"query": query, "results": [result.as_dict() for result in results]}

    @app.get("/games/{game_id}/resources/{resource_id}")
    def _read_resource(
        resource_id: str,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        activation_id: Annotated[uuid.UUID | None, Header(alias="X-AMAW-Activation-ID")] = None,
    ) -> dict[str, Any]:
        with session_scope(engine) as session:
            game = _require_running_resource_access(session, identity.game_id)
            resource = visibility.visible_resource(
                session, identity.game_id, identity.actor_id, resource_id
            )
            if resource is None:
                # Unauthorised/unknown reads are an indistinguishable 404 and log nothing (D-038):
                # the log records only what the Actor was authorised to and did read.
                raise NotFound("Resource not found.")
            _log_resource_access(
                session,
                game,
                identity.actor_id,
                operation="read",
                resource_id=resource_id,
                activation_id=str(activation_id) if activation_id is not None else None,
            )
            projection = resources.resource_read_projection(resource)
            return {"resource": projection.as_dict()}

    @app.post("/games/{game_id}/messages")
    def _post_message(
        body: _MessageIn,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        result = game_service.send_message(
            engine,
            identity.game_id,
            sender_actor_id=identity.actor_id,  # identity from the token, never the body
            text=body.text,
            public=body.public,
            recipients=body.recipients,
            idempotency_key=key,
        )
        return {
            "message": {
                "id": str(result.id),
                "sender_actor_id": result.sender_actor_id,
                "recipients": list(result.recipients),
                "public": result.public,
                "text": body.text,
            }
        }

    @app.post("/games/{game_id}/arguments")
    def _post_argument(
        body: _ArgumentIn,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        result = game_service.make_argument(
            engine,
            identity.game_id,
            actor_id=identity.actor_id,  # only the active Actor is accepted (API-INV-003)
            action=body.action,
            desired_effect=body.desired_effect,
            pros=body.pros,
            idempotency_key=key,
        )
        return {
            "argument": {
                "id": str(result.id),
                "turn": result.turn,
                "actor_id": result.actor_id,
                "action": body.action,
                "desired_effect": body.desired_effect,
                "pros": list(body.pros),
                "status": result.status,
            }
        }

    @app.post("/games/{game_id}/arguments/{argument_id}/cons")
    def _post_con(
        argument_id: uuid.UUID,
        body: _ConIn,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        result = game_service.make_con(
            engine,
            identity.game_id,
            argument_id=argument_id,  # the target Argument, not identity
            actor_id=identity.actor_id,
            reason=body.reason,
            idempotency_key=key,
        )
        return {
            "con": {
                "id": str(result.id),
                "argument_id": str(result.argument_id),
                "actor_id": result.actor_id,
                "reason": result.reason,
            }
        }

    @app.post("/games/{game_id}/arguments/{argument_id}/pass")
    def _post_pass(
        argument_id: uuid.UUID,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        # A distinct endpoint from /ready — Con participation is never overloaded onto readiness.
        result = game_service.pass_con(
            engine,
            identity.game_id,
            argument_id=argument_id,
            actor_id=identity.actor_id,
            idempotency_key=key,
        )
        return {
            "passed": result.passed,
            "argument_id": str(result.argument_id),
            "actor_id": result.actor_id,
        }

    @app.post("/games/{game_id}/arguments/{argument_id}/reply")
    def _post_reply(
        argument_id: uuid.UUID,
        body: _ReplyIn,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        # I-046 / D-060: the owner's single reply to the revealed Cons; the service revalidates
        # mode, phase, ownership and exactly-once inside its transaction.
        result = game_service.submit_reply(
            engine,
            identity.game_id,
            argument_id=argument_id,
            actor_id=identity.actor_id,
            points=[point.model_dump() for point in body.points],
            idempotency_key=key,
        )
        return {
            "reply": {
                "argument_id": str(result.argument_id),
                "actor_id": result.actor_id,
                "points": list(result.points),
            }
        }

    @app.get("/games/{game_id}/notes")
    def _get_notes(
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        with session_scope(engine) as session:
            notes = visibility.visible_notes(
                session, identity.game_id, identity.actor_id
            )
            return {
                "notes": [
                    {
                        "id": str(note.id),
                        "text": note.text,
                        "created_at": note.created_at.isoformat(),
                    }
                    for note in notes
                ]
            }

    @app.post("/games/{game_id}/notes")
    def _post_note(
        body: _NoteIn,
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        result = game_service.save_note(
            engine,
            identity.game_id,
            actor_id=identity.actor_id,
            text=body.text,
            idempotency_key=key,
        )
        return {
            "note": {
                "id": str(result.id),
                "text": result.text,
                "created_at": result.created_at.isoformat(),
            }
        }

    @app.post("/games/{game_id}/ready")
    def _post_ready(
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        key: Annotated[str | None, Depends(idempotency_key)],
    ) -> dict[str, Any]:
        # A distinct endpoint from /pass. Closing diplomacy is the service's own transition,
        # not a client-driven advance (API-INV-008); this endpoint only signals readiness.
        result = game_service.ready(
            engine, identity.game_id, identity.actor_id, idempotency_key=key
        )
        return {"ready": result.ready, "turn": result.turn, "phase": result.phase.value}

    @app.get("/games/{game_id}/events")
    def _get_events(
        identity: Annotated[SeatIdentity, Depends(seat_identity)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        after: int | None = None,
    ) -> dict[str, Any]:
        with session_scope(engine) as session:
            events = visibility.visible_events(
                session, identity.game_id, identity.actor_id, after=after
            )
            serialized = [
                {
                    "id": event.id,
                    "turn": event.turn,
                    "type": event.type,
                    "actor_id": event.actor_id,
                    "payload": event.payload,
                    "created_at": event.created_at.isoformat(),
                }
                for event in events
            ]
        # The cursor advances over visible events only; when nothing new is visible it holds at
        # `after` (or 0), so a hidden newest event never forces its id to be disclosed.
        last_event_id = serialized[-1]["id"] if serialized else (after if after is not None else 0)
        return {"events": serialized, "last_event_id": last_event_id}
