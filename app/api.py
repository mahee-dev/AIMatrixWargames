"""The HTTP application in front of the authoritative engine, and what every route shares.

:func:`create_app` builds an isolated application bound to one engine and registers the routes,
which live in two modules by audience: the Actor-facing routes in :mod:`app.api_player` and
the admin and export routes in :mod:`app.api_admin`. This module owns the error envelope, the
authentication dependencies, the exception handlers and request logging. Routes authenticate,
validate request shape and render results; they own **no** game logic — every state change
goes through :mod:`app.game_service`, every Actor-facing read through :mod:`app.visibility`,
the export through :mod:`app.export` and the summary through :mod:`app.summary`. Invariants
held here: one error envelope for every failure path, including framework 404s (api.md §3);
Seat identity comes only from the bearer token, never from a body (api.md §2.3,
INV-ACTOR-001); the admin token is separate and a Seat token never authorizes an admin route
(api.md §2.4, TRD §16); ``Idempotency-Key`` is honoured per api.md §4; request logging never
emits the ``Authorization`` header or a token. Administrative routes return private data with
ownership metadata; Actor-facing routes are always filtered. ``app/`` imports no agent
framework (AGENTS.md §6 / TRD §4): the Umpire is injected as a :class:`~app.umpire.Umpire`
protocol instance through :func:`create_app` and read back from ``app.state.umpire``.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from sqlalchemy import Engine
from sqlmodel import select
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import game_service, resources
from app.config import Settings, configure_logging
from app.database import get_engine, initialize_database, session_scope
from app.models import Game, Seat
from app.security import hash_token
from app.consequences import ConsequenceResolver
from app.umpire import Umpire

logger = logging.getLogger("app.api")


# --- error envelope (api.md §3) --------------------------------------------------------


def _envelope(code: str, message: str, details: dict | None = None) -> dict[str, Any]:
    """Build the single documented error shape (api.md §3); ``details`` is optional."""
    error: dict[str, Any] = {"code": code, "message": message}
    if details:
        error["details"] = details
    return {"error": error}


def _error_response(
    status_code: int, code: str, message: str, details: dict | None = None
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=_envelope(code, message, details))


class APIError(Exception):
    """An API-layer failure that renders directly as the error envelope.

    Distinct from :class:`app.game_service.GameServiceError` (domain failures): these are
    protocol/auth concerns the service layer never raises.
    """

    http_status: int = 500
    code: str = "internal_error"

    def __init__(self, message: str, details: dict | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details


class InvalidToken(APIError):
    """Missing, malformed or unknown Bearer token (api.md §3.1 → 401 invalid_token)."""

    http_status = 401
    code = "invalid_token"


class Forbidden(APIError):
    """Authenticated but lacking access to this resource (api.md §3.1 → 403 forbidden)."""

    http_status = 403
    code = "forbidden"


class InvalidRequest(APIError):
    """A logically malformed admin request (api.md §3.1 → 400 invalid_request)."""

    http_status = 400
    code = "invalid_request"


class ValidationFailed(APIError):
    """Schema/rule validation failed on an admin request (api.md §3.1 → 422 validation_error)."""

    http_status = 422
    code = "validation_error"


class NotFound(APIError):
    """A missing or intentionally hidden Actor-facing object (api.md §3.1)."""

    http_status = 404
    code = "not_found"


class WrongPhase(APIError):
    """An operation unavailable in the current game status/phase (api.md §3.1)."""

    http_status = 409
    code = "wrong_phase"


class StateConflict(APIError):
    """A write conflicts with immutable or already-claimed state (→ 409)."""

    http_status = 409
    code = "state_conflict"


class APIErrorForIdempotency(APIError):
    """An activation receipt replay conflicts with its original acknowledgement."""

    http_status = 409
    code = "idempotency_conflict"


class AdjudicatorUnavailable(APIError):
    """No Umpire is configured to resolve the pending adjudication (→ 503).

    The server never fabricates a result (TRD §35): when the admin ``…/advance`` reaches an
    adjudication boundary but no :class:`~app.umpire.Umpire` was injected into the app, it reports
    ``adjudicator_unavailable`` rather than a generic 500.
    """

    http_status = 503
    code = "adjudicator_unavailable"


class ConsequenceResolverMissing(APIError):
    """No consequence resolver is configured for a pending consequence pass (→ 503, D-091).

    Like :class:`AdjudicatorUnavailable`: the server never skips or fabricates the pass.
    """

    http_status = 503
    code = "consequence_resolver_unavailable"


# Domain error → (HTTP status, envelope code). Every code is verbatim from api.md §3.1. A
# GameServiceError with no entry here falls back to 500 internal_error (below), so a new
# domain error can never silently escape the envelope with a leaked message.
_DOMAIN_ERROR_MAP: dict[type[game_service.GameServiceError], tuple[int, str]] = {
    game_service.GameNotFound: (404, "not_found"),
    game_service.IllegalStart: (409, "state_conflict"),
    game_service.InvalidDiplomacyRound: (409, "state_conflict"),
    game_service.InvalidPhase: (409, "wrong_phase"),
    game_service.ActorNotInGame: (403, "forbidden"),
    game_service.InvalidRecipient: (400, "invalid_request"),
    game_service.InvalidMessage: (400, "invalid_request"),
    game_service.AlreadyReady: (409, "state_conflict"),
    game_service.NotActiveActor: (409, "not_active_actor"),
    game_service.InvalidArgument: (422, "validation_error"),
    game_service.ArgumentBudgetExhausted: (409, "argument_budget_exhausted"),
    game_service.ArgumentNotCurrent: (409, "state_conflict"),
    game_service.InvalidCon: (422, "validation_error"),
    game_service.InvalidNote: (422, "validation_error"),
    game_service.SelfConForbidden: (403, "forbidden"),
    game_service.ConChallengerNotSelected: (403, "forbidden"),  # I-055 sealed challenger set
    game_service.ConLimitReached: (409, "already_participated"),
    game_service.ConParticipationComplete: (409, "already_participated"),
    # I-046 owner reply (api.md §9.3)
    game_service.InvalidReply: (422, "validation_error"),
    game_service.NotArgumentOwner: (403, "forbidden"),
    game_service.ReplyAlreadySubmitted: (409, "already_participated"),
    game_service.InvalidAdjudication: (422, "validation_error"),
    game_service.InvalidSupersession: (422, "validation_error"),
    game_service.AlreadyAdjudicated: (409, "state_conflict"),
    game_service.AdjudicatorUnavailable: (503, "adjudicator_unavailable"),
    # T-074 / D-091 (api.md §3.1, §14)
    game_service.ConsequencesUnavailable: (503, "consequence_resolver_unavailable"),
    game_service.ConsequencesAlreadyResolved: (409, "state_conflict"),
    game_service.IdempotencyConflict: (409, "idempotency_conflict"),
}

# Framework HTTP statuses → envelope code, so even a Starlette 404/405/401/403 renders the
# documented shape rather than the default ``{"detail": ...}``.
_HTTP_STATUS_CODE: dict[int, str] = {
    400: "invalid_request",
    401: "invalid_token",
    403: "forbidden",
    404: "not_found",
    405: "invalid_request",
    409: "state_conflict",
    422: "validation_error",
    429: "rate_limited",
    503: "adjudicator_unavailable",
}


# --- common response fields (api.md §5) ------------------------------------------------


def common_response_fields(game: Game) -> dict[str, Any]:
    """The fields every game-scoped response should carry (api.md §5).

    Deliberately does not expose the internal schema — only the three documented fields.
    """
    return {"game_id": str(game.id), "turn": game.turn, "phase": game.phase.value}


# --- authenticated identity ------------------------------------------------------------


@dataclass(frozen=True)
class SeatIdentity:
    """The authoritative identity resolved from a Seat bearer token (never from a body)."""

    game_id: uuid.UUID
    actor_id: str


def _bearer_token(request: Request) -> str:
    """Extract the raw Bearer token, or raise :class:`InvalidToken` (api.md §2.3)."""
    header = request.headers.get("authorization")
    if not header:
        raise InvalidToken("missing bearer token")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise InvalidToken("malformed authorization header")
    return token.strip()


def get_request_engine(request: Request) -> Engine:
    """The engine dependency — reads the engine bound to the app.

    The seam tests use: ``create_app(engine=...)`` binds an isolated engine, and this is also
    overridable via ``app.dependency_overrides[get_request_engine]``.
    """
    return request.app.state.engine


def seat_identity(game_id: uuid.UUID, request: Request) -> SeatIdentity:
    """Resolve ``Bearer <token> → Seat → (game_id, actor_id)`` (api.md §2.3, INV-ACTOR-001).

    Identity is derived from the token alone. A missing/malformed/unknown token → 401
    invalid_token; a token whose Seat is for a different game than the path → 403 forbidden.
    """
    token = _bearer_token(request)
    engine: Engine = request.app.state.engine
    with session_scope(engine) as session:
        seat = session.exec(
            select(Seat).where(Seat.token_hash == hash_token(token))
        ).first()
        identity = (
            SeatIdentity(game_id=seat.game_id, actor_id=seat.actor_id)
            if seat is not None
            else None
        )
    if identity is None:
        # Do not reveal whether the token or the game is the problem (api.md §3.1).
        raise InvalidToken("unknown or invalid Seat token")
    if identity.game_id != game_id:
        raise Forbidden("token does not authorize this game")
    return identity


def require_admin(request: Request) -> None:
    """Authorise an administrative endpoint with the separate admin token (api.md §2.4).

    A missing/malformed bearer → 401 invalid_token; any present token that is not the admin
    token — a Seat token included — → 403 forbidden. Constant-time comparison; when no admin
    token is configured the surface is closed (every request → 403).
    """
    token = _bearer_token(request)
    settings: Settings = request.app.state.settings
    admin = settings.admin_token.get_secret_value() if settings.admin_token else None
    if admin is None or not secrets.compare_digest(token, admin):
        raise Forbidden("admin access required")


def idempotency_key(
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> str | None:
    """Extract the optional ``Idempotency-Key`` header (api.md §4).

    Extraction only — the idempotency *behaviour* already lives in the service (T-023). The
    mutating endpoints that consume it are T-030+.
    """
    if idempotency_key is not None and len(idempotency_key) > game_service.MAX_IDEMPOTENCY_KEY_CHARS:
        raise InvalidRequest(
            f"Idempotency-Key exceeds {game_service.MAX_IDEMPOTENCY_KEY_CHARS} characters"
        )
    return idempotency_key


# --- exception handlers ----------------------------------------------------------------


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(APIError)
    async def _handle_api_error(_: Request, exc: APIError) -> JSONResponse:
        return _error_response(exc.http_status, exc.code, exc.message, exc.details)

    @app.exception_handler(game_service.GameServiceError)
    async def _handle_domain_error(
        _: Request, exc: game_service.GameServiceError
    ) -> JSONResponse:
        mapping = _DOMAIN_ERROR_MAP.get(type(exc))
        if mapping is None:
            # Unmapped domain error: never leak its message — treat as an internal error.
            logger.exception("Unmapped domain error: %s", type(exc).__name__)
            return _error_response(500, "internal_error", "An internal error occurred.")
        status_code, code = mapping
        return _error_response(status_code, code, str(exc))

    @app.exception_handler(RequestValidationError)
    async def _handle_validation_error(
        _: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return _error_response(
            422,
            "validation_error",
            "Request failed schema validation.",
            {"errors": exc.errors()},
        )

    @app.exception_handler(StarletteHTTPException)
    async def _handle_http_exception(
        _: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        code = _HTTP_STATUS_CODE.get(
            exc.status_code, "invalid_request" if exc.status_code < 500 else "internal_error"
        )
        message = exc.detail if isinstance(exc.detail, str) else code
        return _error_response(exc.status_code, code, message)

    @app.exception_handler(Exception)
    async def _handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        # Log the detail server-side; return a generic message so nothing internal leaks.
        logger.exception("Unhandled error: %s", type(exc).__name__)
        return _error_response(500, "internal_error", "An internal error occurred.")


# --- request logging (never logs the Authorization header or a token) ------------------

# The largest request body the API accepts, checked against a declared ``Content-Length`` and
# again while the body streams (I-088).
MAX_REQUEST_BODY_BYTES = 65536
_BODY_TOO_LARGE = f"Request body exceeds {MAX_REQUEST_BODY_BYTES} bytes."


def _replay_consumed_body(request: Request, body: bytes) -> None:
    """Hand the already-consumed body back to Starlette so route parsing reads it.

    The middleware has drained ``request.stream()`` to enforce the limit, and a stream can be
    read only once. Starlette replays a cached body from the private ``request._body``, and this
    is the one place that relies on it. The dependency is pinned in ``uv.lock``, and every POST
    test runs through this path, so an upgrade that changes it fails loudly (I-119).
    """
    request._body = body  # noqa: SLF001 - Starlette's replay contract


def _register_request_logging(app: FastAPI) -> None:
    @app.middleware("http")
    async def _log_requests(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        started = time.perf_counter()
        content_length = request.headers.get("content-length")
        if content_length is not None:
            try:
                if int(content_length) > MAX_REQUEST_BODY_BYTES:
                    return _error_response(413, "invalid_request", _BODY_TOO_LARGE)
            except ValueError:
                return _error_response(400, "invalid_request", "Invalid Content-Length header.")
        if request.method in {"POST", "PUT", "PATCH"}:
            # Consume incrementally and retain at most the accepted bound. Starlette replays the
            # cached body to downstream parsing; an oversized chunk is never appended and no
            # later chunk is requested (I-088).
            chunks: list[bytes] = []
            received = 0
            async for chunk in request.stream():
                received += len(chunk)
                if received > MAX_REQUEST_BODY_BYTES:
                    return _error_response(413, "invalid_request", _BODY_TOO_LARGE)
                chunks.append(chunk)
            _replay_consumed_body(request, b"".join(chunks))
        response = await call_next(request)
        duration_ms = (time.perf_counter() - started) * 1000.0
        # Method, path, status and duration only — headers (incl. Authorization) are never
        # logged (testing.md §24). The SecretRedactingFilter is a second line of defense.
        logger.info(
            "%s %s -> %s (%.1fms)",
            request.method,
            request.url.path,
            response.status_code,
            duration_ms,
        )
        return response


# --- application factory ---------------------------------------------------------------


def create_app(
    engine: Engine | None = None,
    settings: Settings | None = None,
    umpire: Umpire | None = None,
    consequence_resolver: ConsequenceResolver | None = None,
) -> FastAPI:
    """Build a configured FastAPI application.

    ``engine`` binds the persistence engine (tests pass an isolated temp engine); when omitted
    the process default from :func:`app.database.get_engine` is used. ``settings`` defaults to
    the environment-driven :class:`~app.config.Settings`.

    ``umpire`` is the :class:`~app.umpire.Umpire` the admin ``…/advance`` route uses to resolve a
    pending adjudication (server-owned RNG). It is injected — never constructed here — so ``app/``
    stays free of any agent framework (TRD §4): the composition root (benchmark runner / CLI /
    tests) passes the concrete ``FakeUmpire`` or ``AIUmpire``. When ``None``, an ``…/advance`` that
    needs to resolve reports ``503 adjudicator_unavailable`` rather than fabricating a result.

    ``consequence_resolver`` is the injected :class:`~app.consequences.ConsequenceResolver` the
    same route uses for a ``bounded_effects_v1`` game's end-of-turn pass (T-074, D-091). When
    ``None``, a pending pass reports ``503 consequence_resolver_unavailable``.
    """
    settings = settings or Settings()
    configure_logging(settings)

    app = FastAPI(title="AMAW", version="0.2")
    app.state.engine = engine if engine is not None else get_engine()
    initialize_database(app.state.engine)
    app.state.settings = settings
    app.state.umpire = umpire
    app.state.consequence_resolver = consequence_resolver
    # I-054: immutable Resource results may be replayed only inside this application instance
    # and only when the endpoint supplies the complete Game/Actor/activation/query scope.
    app.state.resource_search_replay_cache = resources.ActivationSearchReplayCache()

    # The route modules import this one for errors and auth, so they are imported here.
    from app.api_admin import register_admin_routes
    from app.api_player import register_player_routes

    _register_exception_handlers(app)
    _register_request_logging(app)
    register_player_routes(app)
    register_admin_routes(app)
    return app
