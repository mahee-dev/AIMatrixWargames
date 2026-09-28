"""The agent HTTP tool layer (T-037, agents.md §5–§16).

A thin, authenticated boundary between a player agent and the AMAW HTTP API. Every player
game action an agent can take goes through this client; each method:

1. constructs the documented HTTP request (api.md §6–§13);
2. authenticates with the raw Seat bearer token — identity is the token's alone, never a
   request body (INV-ACTOR-001 / API-INV-001);
3. parses the response; and
4. converts an expected/recoverable API rejection (the api.md §3 error envelope) into a
   structured :class:`ToolResult` rather than letting an HTTP failure escape as an opaque
   exception into the eventual LLM loop (agents.md §18 "malformed tool call → tool error
   returned").

What this layer deliberately does **not** do (the server stays authoritative): it infers no
game rules, reproduces no phase logic, filters no visibility, never recomputes
``available_actions`` (API-INV-009 — ``observe`` returns the server's list verbatim), never
advances state except by calling the documented endpoint (API-INV-008), and never retries an
invalid player action. Infrastructure/programming failures (a broken connection, a non-JSON
body, a response with no error envelope) still raise — only game-rule/API rejections are
returned as structured tool errors.

Boundary (AGENTS.md §6 / TRD §4): this module imports only the standard library and
``amaw_contracts``; the HTTP client is supplied by the caller.
It never imports :mod:`app` internals (game service, persistence, visibility, models) or an
agent framework — it talks to the game exclusively over HTTP.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from amaw_contracts import SEARCH_CONTRACT_DEFAULT

logger = logging.getLogger("agents.tools")


class _RequestURL(Protocol):
    @property
    def path(self) -> str: ...


class _SentRequest(Protocol):
    @property
    def url(self) -> _RequestURL: ...


class HTTPResponse(Protocol):
    """The parts of a response the agent layer reads.

    Structural, because the in-process ``TestClient`` returns Starlette's own client's response
    type (``httpx2`` when installed) rather than ``httpx.Response``.
    """

    @property
    def status_code(self) -> int: ...

    @property
    def is_success(self) -> bool: ...

    @property
    def request(self) -> _SentRequest: ...

    def json(self) -> object: ...


class HTTPClient(Protocol):
    """The synchronous HTTP surface the agent layer drives the server through.

    ``httpx.Client`` satisfies it for a deployment and Starlette's ``TestClient`` in-process.
    Only the keywords the player tools and the coordinator actually pass are declared, so the
    agent layer depends on this surface rather than on one concrete client.
    """

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = ...,
        json: object = ...,
        params: Mapping[str, str | int | float | bool | None] | None = ...,
        timeout: float | None = ...,
    ) -> HTTPResponse: ...

# I-034: decision packets use incremental delivery and one rolling strategic memory;
# ``tool_loop`` stages replacement memory with ``update_strategic_memory``, while
# ``single_shot`` carries it in the structured decision. Legacy Note HTTP methods remain for
# older clients but are not exposed to the v1.4/v1.5 model condition. The player-visible
# tool-schema identity that run manifests and AgentRun rows pin is versioned in
# ``agents/player.py`` (``*_TOOL_SCHEMA_VERSION``), not here.


@dataclass(frozen=True)
class ToolResult:
    """The stable, agent-facing outcome of a tool call.

    ``ok`` is the single success flag. On success ``data`` carries the parsed response body;
    on a recoverable failure ``error_code`` / ``error_message`` / ``error_details`` carry the
    API error envelope (api.md §3), so a player model gets structured guidance it can react to
    (wrong phase, not active, budget exhausted, …) instead of an exception. ``http_status`` is
    always the response status.
    """

    ok: bool
    http_status: int
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: dict[str, Any] | None = None


class ToolTransportError(RuntimeError):
    """An infrastructure/programming failure, not a recoverable game-rule rejection.

    Raised when the API did not answer with a usable JSON error envelope (a non-JSON body, or a
    non-2xx response that carries no ``{"error": {...}}``). Transport-level failures from
    :mod:`httpx` (timeouts, connection errors) propagate directly. These are for the caller's
    failure-recovery policy (agents.md §18), not for the LLM to "fix".
    """


@dataclass
class _DiplomacyRound:
    """Round-local read snapshot and deferred completion state (I-028)."""

    context: ToolResult
    activation: ToolResult
    messages: ToolResult
    ready_deferred: bool = False
    deferred_ready_key: str | None = None
    private_recipients: set[str] = field(default_factory=set)


class PlayerTools:
    """The baseline player HTTP surface bound to one Seat in one game.

    Construct with an :class:`HTTPClient` (a ``TestClient`` in-process, or an ``httpx.Client``
    for a deployment), the ``game_id``, and the raw Seat ``token``. The token is held privately and is
    never logged (testing.md §24).
    """

    def __init__(
        self,
        client: HTTPClient,
        *,
        game_id: str,
        token: str,
        timeout: float | None = None,
    ) -> None:
        self._client = client
        self._game_id = game_id
        self._token = token  # raw Seat token — never logged, never put in an exception
        self._timeout = timeout
        self._diplomacy_round: _DiplomacyRound | None = None
        # Observational correlation only (I-045). ReferencePlayer scopes this opaque id to one
        # model activation; the server never uses it for identity, legality or state changes.
        self._activation_id: str | None = None

    def set_activation_correlation(self, activation_id: str | None) -> None:
        """Attach/clear the opaque id used to correlate Resource research with AgentRun."""
        self._activation_id = activation_id

    def prepare_diplomacy_round(self, context: dict[str, Any]) -> None:
        """Freeze this Actor's mutable reads before any worker in the round may write.

        ``POST /activations`` is a read-snapshot operation despite using POST: calling it here
        pins incremental Message delivery to the round-start event boundary.  The model's later
        call receives this exact response.  ``ready`` is deferred until every worker settles so
        a same-round private Message cannot be overtaken by a stale readiness write.
        """
        if self._diplomacy_round is not None:
            raise RuntimeError("a diplomacy round is already active for these tools")
        activation = self._request("POST", "/activations", json_body={})
        messages = self._request("GET", "/messages")
        self._diplomacy_round = _DiplomacyRound(
            context=ToolResult(ok=True, http_status=200, data=dict(context)),
            activation=activation,
            messages=messages,
        )

    def diplomacy_round_state(self) -> tuple[bool, set[str]]:
        """Return deferred readiness and accepted private recipients for coordinator settlement."""
        if self._diplomacy_round is None:
            return False, set()
        return (
            self._diplomacy_round.ready_deferred,
            set(self._diplomacy_round.private_recipients),
        )

    def settle_diplomacy_round(self, *, commit_ready: bool) -> ToolResult | None:
        """Commit or suppress this Actor's deferred readiness, then leave round mode."""
        round_state = self._diplomacy_round
        if round_state is None:
            return None
        try:
            if not round_state.ready_deferred or not commit_ready:
                return None
            return self._request(
                "POST", "/ready", json_body={},
                idempotency_key=round_state.deferred_ready_key,
            )
        finally:
            self._diplomacy_round = None

    def abort_diplomacy_round(self) -> None:
        """Drop only coordinator-local round state after every worker has stopped."""
        self._diplomacy_round = None

    # --- HTTP plumbing (identity, envelope parsing) --------------------------------------

    def _path(self, suffix: str) -> str:
        return f"/games/{self._game_id}{suffix}"

    def _headers(self, idempotency_key: str | None) -> dict[str, str]:
        # Identity travels only in the Authorization header (INV-ACTOR-001). Never logged.
        headers = {"Authorization": f"Bearer {self._token}"}
        if idempotency_key is not None:
            headers["Idempotency-Key"] = idempotency_key
        if self._activation_id is not None:
            headers["X-AMAW-Activation-ID"] = self._activation_id
        return headers

    def _request(
        self,
        method: str,
        suffix: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        # httpx transport errors (timeout/connection) propagate — they are infra failures for
        # the caller's recovery policy, not recoverable game-rule rejections. Only forward an
        # explicit per-call timeout (an in-process TestClient rejects the kwarg otherwise).
        extra: dict[str, Any] = {"timeout": self._timeout} if self._timeout is not None else {}
        response = self._client.request(
            method,
            self._path(suffix),
            headers=self._headers(idempotency_key),
            json=json_body,
            params=params,
            **extra,
        )
        # Log method/path/status only — never the token or the Authorization header.
        logger.debug("tool %s %s -> %s", method, self._path(suffix), response.status_code)
        return self._to_result(response)

    @staticmethod
    def _to_result(response: HTTPResponse) -> ToolResult:
        try:
            body = response.json()
        except ValueError as exc:  # non-JSON body — not a documented outcome
            raise ToolTransportError(
                f"non-JSON response ({response.status_code}) from {response.request.url.path}"
            ) from exc

        if response.is_success:
            data = body if isinstance(body, dict) else {"result": body}
            return ToolResult(ok=True, http_status=response.status_code, data=data)

        error = body.get("error") if isinstance(body, dict) else None
        if not isinstance(error, dict) or "code" not in error:
            # A non-2xx with no api.md §3 envelope is a server/programming fault, not a
            # recoverable player mistake.
            raise ToolTransportError(
                f"unenveloped error response ({response.status_code}) from "
                f"{response.request.url.path}"
            )
        return ToolResult(
            ok=False,
            http_status=response.status_code,
            error_code=error.get("code"),
            error_message=error.get("message"),
            error_details=error.get("details"),
        )

    # --- the nine baseline tools (agents.md §5) ------------------------------------------

    def observe(self) -> ToolResult:
        """Refresh Actor-specific context, incl. server-computed ``available_actions``.

        agents.md §6 / api.md §6.1. Side effects: none. ``available_actions`` is returned
        verbatim — the tool never recomputes legality (API-INV-009).
        """
        if self._diplomacy_round is not None:
            return self._diplomacy_round.context
        return self._request("GET", "/context")

    def begin_activation(self) -> ToolResult:
        """Create or resume this Seat's server-backed incremental context delivery."""
        if self._diplomacy_round is not None:
            return self._diplomacy_round.activation
        return self._request("POST", "/activations", json_body={})

    def acknowledge_activation(
        self, delivery_receipt: str, strategic_memory: dict[str, Any]
    ) -> ToolResult:
        """Acknowledge one successful activation and atomically replace rolling memory."""
        return self._request(
            "POST",
            f"/activations/{delivery_receipt}/ack",
            json_body={"strategic_memory": strategic_memory},
        )

    def read_messages(self, *, after_event_id: int | None = None) -> ToolResult:
        """Read Messages visible to the Actor (agents.md §7 / api.md §7.1).

        ``after_event_id`` is the optional event cursor (mapped to the ``after`` query param).
        Visibility is the API's — the tool adds none.
        """
        if self._diplomacy_round is not None:
            return self._diplomacy_round.messages
        params = {"after": after_event_id} if after_event_id is not None else None
        return self._request("GET", "/messages", params=params)

    def send_message(
        self,
        *,
        recipients: list[str],
        public: bool,
        text: str,
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """Send a public or private Message (agents.md §8 / api.md §7.2).

        No ``sender``/``actor_id`` is accepted — the sender is the Seat token (INV-ACTOR-001).
        """
        body = {"recipients": recipients, "public": public, "text": text}
        result = self._request(
            "POST", "/messages", json_body=body, idempotency_key=idempotency_key
        )
        if result.ok and not public and self._diplomacy_round is not None:
            self._diplomacy_round.private_recipients.update(recipients)
        return result

    def search_resources(
        self, *, query: str, search_contract: str = SEARCH_CONTRACT_DEFAULT
    ) -> ToolResult:
        """Search scenario Resources visible to the Actor (agents.md §9 / api.md §10.1).

        A read: identity is the Seat token's, no idempotency key. Results are visibility-filtered
        by the server before matching (INV-RES-002/003) — the tool adds no filtering. ``query`` is
        passed as the ``q`` parameter; the server echoes it back with the results. The internal
        ``search_contract`` is fixed by the player's sealed condition, not exposed to the model.

        The contract is always sent, never left to the server's own default (I-112): the sealed
        condition is the authority, and an omitted parameter would silently follow a default
        change — exactly what a `search-v1` reproducibility run must not do.
        """
        return self._request(
            "GET", "/resources/search", params={"q": query, "contract": search_contract}
        )

    def read_resource(self, *, resource_id: str) -> ToolResult:
        """Read one authorised scenario Resource (agents.md §10 / api.md §10.2).

        An unauthorised, hidden or unknown Resource comes back as the same ``404 not_found``
        envelope — a structured :class:`ToolResult`, not a raise — so a hidden Resource is
        indistinguishable from a nonexistent one (INV-RES-004).
        """
        return self._request("GET", f"/resources/{resource_id}")

    def make_argument(
        self,
        *,
        action: str,
        desired_effect: str,
        pros: list[str],
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """Submit the Actor's primary Matrix Argument (agents.md §11 / api.md §8.1).

        Legal only when ``make_argument`` is in ``available_actions``; the server enforces that
        (budget, active Actor, phase) — the tool does not.
        """
        body = {"action": action, "desired_effect": desired_effect, "pros": pros}
        return self._request(
            "POST", "/arguments", json_body=body, idempotency_key=idempotency_key
        )

    def make_con(
        self, *, argument_id: str, reason: str, idempotency_key: str | None = None
    ) -> ToolResult:
        """Challenge another Actor's current Argument (agents.md §12 / api.md §9.1)."""
        body = {"reason": reason}
        return self._request(
            "POST",
            f"/arguments/{argument_id}/cons",
            json_body=body,
            idempotency_key=idempotency_key,
        )

    def pass_con(self, *, argument_id: str, idempotency_key: str | None = None) -> ToolResult:
        """Close Con participation without another Con (agents.md §13 / api.md §9.2).

        A dedicated tool — never overloaded onto :meth:`ready` (agents.md §16).
        """
        return self._request(
            "POST",
            f"/arguments/{argument_id}/pass",
            json_body={},
            idempotency_key=idempotency_key,
        )

    def reply_to_cons(
        self, *, argument_id: str, points: list[dict[str, str]],
        idempotency_key: str | None = None,
    ) -> ToolResult:
        """Answer the revealed Cons against your own Argument, once (agents.md §13.1 /
        api.md §9.3). ``points=[]`` is the explicit pass. ``visible_reply`` only."""
        return self._request(
            "POST",
            f"/arguments/{argument_id}/reply",
            json_body={"points": list(points)},
            idempotency_key=idempotency_key,
        )

    def get_notes(self) -> ToolResult:
        """Read the Actor's own private Notes (agents.md §14 / api.md §11.1)."""
        return self._request("GET", "/notes")

    def save_note(self, *, text: str, idempotency_key: str | None = None) -> ToolResult:
        """Save an explicit strategic Note (agents.md §15 / api.md §11.2)."""
        return self._request(
            "POST", "/notes", json_body={"text": text}, idempotency_key=idempotency_key
        )

    def ready(self, *, idempotency_key: str | None = None) -> ToolResult:
        """Signal no further useful action this participation opportunity (agents.md §16).

        Use only where ``ready`` is in ``available_actions`` (primarily diplomacy); closing the
        phase is the server's own transition (API-INV-008), not a client-driven advance.
        """
        if self._diplomacy_round is not None:
            if not self._diplomacy_round.ready_deferred:
                self._diplomacy_round.ready_deferred = True
                self._diplomacy_round.deferred_ready_key = idempotency_key
            return ToolResult(ok=True, http_status=200, data={"ready": True, "deferred": True})
        return self._request("POST", "/ready", json_body={}, idempotency_key=idempotency_key)
