"""The OpenRouter model client (T-038, TRD §6, agents.md §18–§19).

The minimal model-gateway abstraction the reference player (T-039) needs to invoke a model
through OpenRouter. It is deliberately narrow — model transport and configuration only:

- per-call/per-player model selection (:class:`ModelConfig`);
- a request timeout, and a fixed ceiling on the response body (I-122);
- **exactly one** retry on a transient provider/network condition, and **no** retry on a
  deterministic one (agents.md §18 "transiently fails → retry once");
- provenance consumed by T-053's durable recorder: requested vs
  returned model, provider, usage, latency (agents.md §19).

It is built directly on :mod:`httpx` against OpenRouter's OpenAI-compatible
``/chat/completions`` endpoint. That keeps this layer outside ``app/`` (TRD §4), lets the whole
default test suite run offline against a fake transport with no key (testing.md §3), and gives
precise control over the one-retry and provenance the contract requires. PydanticAI is the
reference *player*'s dependency (T-039), not this transport's; nothing here imports it. Key and
timeout come from the existing :class:`app.config.Settings` — no second env loader. The
OpenRouter key is never written to a log or an exception (testing.md §24).

Generalized fallback models remain outside this transport boundary.
"""

from __future__ import annotations

import enum
import json
import logging
import time
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import httpx

if TYPE_CHECKING:
    from app.config import Settings

logger = logging.getLogger("agents.model_client")

# The OpenRouter OpenAI-compatible base URL — the single provider boundary. The T-039 reference
# player points PydanticAI's OpenAI-compatible provider at this same URL (D-016).
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
_OPENROUTER_BASE_URL = OPENROUTER_BASE_URL  # backwards-compatible private alias
_COMPLETIONS_PATH = "/chat/completions"
_DEFAULT_TIMEOUT = 60.0
# The largest response body the client will read, counted as the body streams. A structured
# Umpire or consequence completion is a few KiB and even a long reasoning trace stays far below
# this, so the limit never shapes a normal run; it stops a misbehaving or hostile gateway from
# growing memory until the deadline. Crossing it fails closed and is never retried (I-122).
MAX_RESPONSE_BYTES = 2 * 1024 * 1024


class ProviderErrorCategory(enum.Enum):
    """Why a model call failed — the classification that drives the retry decision.

    Only :data:`TRANSIENT` is retried (once). The rest are deterministic: retrying cannot help,
    so the call fails immediately (agents.md §18).
    """

    TRANSIENT = "transient"  # timeout, connection drop, 429/5xx — retry once
    AUTH = "auth"  # invalid/again-invalid key (401/403) — never retry
    INVALID_REQUEST = "invalid_request"  # malformed request (400/422) — never retry
    UNSUPPORTED_MODEL = "unsupported_model"  # unknown model (404) — never retry
    CONFIG = "config"  # local misconfiguration (e.g. no key) — no call made
    RESPONSE_TOO_LARGE = "response_too_large"  # body over MAX_RESPONSE_BYTES — never retry
    OTHER = "other"  # anything else — not retried


# HTTP status → category. Statuses not listed here are treated as OTHER (not retried).
_STATUS_CATEGORY: dict[int, ProviderErrorCategory] = {
    400: ProviderErrorCategory.INVALID_REQUEST,
    401: ProviderErrorCategory.AUTH,
    403: ProviderErrorCategory.AUTH,
    404: ProviderErrorCategory.UNSUPPORTED_MODEL,
    408: ProviderErrorCategory.TRANSIENT,
    422: ProviderErrorCategory.INVALID_REQUEST,
    429: ProviderErrorCategory.TRANSIENT,
    500: ProviderErrorCategory.TRANSIENT,
    502: ProviderErrorCategory.TRANSIENT,
    503: ProviderErrorCategory.TRANSIENT,
    504: ProviderErrorCategory.TRANSIENT,
}


class ModelClientError(RuntimeError):
    """A model call failed. :attr:`category` classifies it (and drives retry).

    The message never contains the API key — only the model, provider and category, which are
    safe to surface and log.
    """

    def __init__(
        self,
        message: str,
        *,
        category: ProviderErrorCategory,
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.category = category
        self.status_code = status_code
        # Filled by ``complete`` at the retry boundary so T-053 can persist failed provider
        # attempts as well as successful responses. A local config error leaves both at zero.
        self.request_count = 0
        self.latency_seconds = 0.0


@dataclass(frozen=True)
class ModelConfig:
    """Per-player model configuration (benchmark role/model assignment depends on this, §19).

    ``model`` is the OpenRouter model identifier (e.g. ``"anthropic/claude-3.5-sonnet"``).
    ``timeout`` overrides the client default for this call; ``temperature``/``max_tokens`` and
    ``extra_body`` pass provider parameters through without this layer interpreting them.
    ``deadline`` is a wall-clock bound in seconds on one model call, enforced while the body is
    read (D-063): ``timeout`` is httpx's per-operation timeout and never fires on a response the
    gateway keeps alive with keepalive bytes, so a runaway completion is otherwise unbounded.
    """

    model: str
    timeout: float | None = None
    deadline: float | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    extra_body: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelResult:
    """A completed model call plus the provenance T-053 persists (agents.md §19).

    ``model_requested`` is always what we asked for; ``model_returned`` is what the provider
    reports serving (often a pinned snapshot), captured separately. ``raw`` keeps the parsed
    provider body so nothing useful is wrapped away.
    """

    completion: str
    model_requested: str
    model_returned: str | None
    provider: str | None
    usage: dict[str, Any] | None
    latency_seconds: float
    finish_reason: str | None = None
    raw: dict[str, Any] | None = None
    request_count: int = 1


class OpenRouterClient:
    """A minimal OpenRouter chat-completions client (transport + one-retry + provenance).

    Construct with an ``api_key`` (held privately, never logged) and an optional ``transport``
    (tests inject an :class:`httpx.MockTransport`) and ``timeout``. Use :meth:`from_settings` to
    take the key and timeout from the central :class:`app.config.Settings`.
    ``max_response_bytes`` is the released :data:`MAX_RESPONSE_BYTES`; it is a parameter only so
    tests can reach the limit with a small body, and no run configuration sets it.
    """

    def __init__(
        self,
        *,
        api_key: str,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _OPENROUTER_BASE_URL,
        timeout: float = _DEFAULT_TIMEOUT,
        provider_label: str = "openrouter",
        max_response_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self._api_key = api_key  # never logged, never placed in an exception message
        self._timeout = timeout
        self._max_response_bytes = max_response_bytes
        self._provider_label = provider_label
        self._client = httpx.Client(base_url=base_url, transport=transport, timeout=timeout)

    @classmethod
    def from_settings(
        cls,
        settings: Settings,
        *,
        transport: httpx.BaseTransport | None = None,
        base_url: str = _OPENROUTER_BASE_URL,
    ) -> OpenRouterClient:
        """Build a client from the existing central settings — no new env loader (TRD §36)."""
        key = (
            settings.openrouter_api_key.get_secret_value()
            if settings.openrouter_api_key is not None
            else ""
        )
        return cls(
            api_key=key,
            transport=transport,
            base_url=base_url,
            timeout=settings.request_timeout_seconds,
        )

    @property
    def max_response_bytes(self) -> int:
        """The response-body ceiling this client enforces; recorded in Umpire/resolver provenance."""
        return self._max_response_bytes

    def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        config: ModelConfig,
        max_attempts: int = 2,
    ) -> ModelResult:
        """Invoke ``config.model`` on ``messages``; return the completion and its provenance.

        Retries **exactly once** by default, and only for a
        :data:`ProviderErrorCategory.TRANSIENT` failure. A deterministic failure (auth,
        unsupported model, malformed request, missing key) is raised immediately with no retry.
        A caller that owns a larger bound of its own (the Umpire, D-063) passes
        ``max_attempts=1`` so the two policies never multiply.
        """
        if not self._api_key:
            # A local configuration error — do not even attempt a provider call.
            raise ModelClientError(
                "no OpenRouter API key configured", category=ProviderErrorCategory.CONFIG
            )

        payload = self._build_payload(messages, config)
        timeout = config.timeout if config.timeout is not None else self._timeout

        # One transient retry: the first attempt plus at most one more.
        last_error: ModelClientError | None = None
        call_started = time.perf_counter()
        for attempt in range(max_attempts):
            try:
                result = self._attempt(
                    payload,
                    timeout=timeout,
                    deadline=config.deadline,
                    model_requested=config.model,
                )
                return replace(
                    result,
                    latency_seconds=time.perf_counter() - call_started,
                    request_count=attempt + 1,
                )
            except ModelClientError as exc:
                last_error = exc
                if (
                    exc.category is not ProviderErrorCategory.TRANSIENT
                    or attempt == max_attempts - 1
                ):
                    exc.request_count = attempt + 1
                    exc.latency_seconds = time.perf_counter() - call_started
                    # Non-transient, or the retry has already been used — surface it.
                    logger.warning(
                        "model call failed model=%s category=%s status=%s",
                        config.model,
                        exc.category.value,
                        exc.status_code,
                    )
                    raise
                logger.info(
                    "transient model failure model=%s category=%s — retrying once",
                    config.model,
                    exc.category.value,
                )
        assert last_error is not None  # loop always raises or returns
        raise last_error

    # --- internals -----------------------------------------------------------------------

    def _build_payload(
        self, messages: list[dict[str, Any]], config: ModelConfig
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {"model": config.model, "messages": messages}
        if config.temperature is not None:
            payload["temperature"] = config.temperature
        if config.max_tokens is not None:
            payload["max_tokens"] = config.max_tokens
        payload.update(config.extra_body)
        return payload

    def _attempt(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        deadline: float | None,
        model_requested: str,
    ) -> ModelResult:
        started = time.perf_counter()
        try:
            # The body is streamed so the wall-clock deadline can be checked between chunks: a
            # gateway that keeps a slow response alive never trips the per-read timeout, so a
            # runaway completion would otherwise run for as long as the provider lets it
            # (I-072). One blocked read can still overrun the deadline by at most ``timeout``.
            with self._client.stream(
                "POST",
                _COMPLETIONS_PATH,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=timeout,
            ) as response:
                if not response.is_success:
                    category = _STATUS_CATEGORY.get(
                        response.status_code, ProviderErrorCategory.OTHER
                    )
                    raise ModelClientError(
                        f"provider returned {response.status_code} for model {model_requested}",
                        category=category,
                        status_code=response.status_code,
                    )
                chunks: list[bytes] = []
                received = 0
                for chunk in response.iter_bytes():
                    # Checked before the chunk is kept, and no further chunk is requested: an
                    # oversized body is never held past the limit (I-122). Deterministic, so the
                    # caller's retry bound does not repeat it.
                    received += len(chunk)
                    if received > self._max_response_bytes:
                        raise ModelClientError(
                            f"provider response exceeded {self._max_response_bytes} bytes "
                            f"for model {model_requested}",
                            category=ProviderErrorCategory.RESPONSE_TOO_LARGE,
                        )
                    chunks.append(chunk)
                    if deadline is not None and time.perf_counter() - started > deadline:
                        raise ModelClientError(
                            f"wall-clock deadline of {deadline:g}s exceeded",
                            category=ProviderErrorCategory.TRANSIENT,
                        )
        except (httpx.TimeoutException, httpx.TransportError) as exc:
            # Network-level failure — transient, eligible for the single retry. The message
            # carries the exception type only, never the key.
            raise ModelClientError(
                f"transport failure: {type(exc).__name__}",
                category=ProviderErrorCategory.TRANSIENT,
            ) from exc

        return self._parse(b"".join(chunks), model_requested, time.perf_counter() - started)

    def _parse(self, raw_body: bytes, model_requested: str, latency: float) -> ModelResult:
        # A 2xx body that cannot be read as a completion is OpenRouter's documented intermittent
        # envelope (an upstream error or empty choice returned with HTTP 200), not a fault in the
        # request: the same condition is a transient `ModelAPIError` on the PydanticAI player path
        # (D-018 — one shared classification), so it gets the single retry here too (I-023).
        try:
            body = json.loads(raw_body)
        except ValueError as exc:
            raise ModelClientError(
                "provider returned invalid JSON",
                category=ProviderErrorCategory.TRANSIENT,
            ) from exc
        if not isinstance(body, dict):
            raise ModelClientError(
                "provider returned a non-object response",
                category=ProviderErrorCategory.TRANSIENT,
            )
        choices = body.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise ModelClientError(
                "provider response omitted a valid choice",
                category=ProviderErrorCategory.TRANSIENT,
            )
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ModelClientError(
                "provider response omitted completion content",
                category=ProviderErrorCategory.TRANSIENT,
            )
        content = message.get("content")
        if content is None:
            # A completed choice whose ``content`` is ``null`` is the provider's way of saying the
            # model produced no answer (typically ``finish_reason=length`` after reasoning consumed
            # the budget). That is unusable *output* — the same condition as an empty string — and
            # belongs to the caller's output retry with its provider exclusion (D-061), not to the
            # transport-level transient retry here, which would repeat unchanged routing (I-070).
            content = ""
        if not isinstance(content, str):
            raise ModelClientError(
                "provider response omitted completion content",
                category=ProviderErrorCategory.TRANSIENT,
            )
        model_returned = body.get("model")
        provider = body.get("provider", self._provider_label)
        usage = body.get("usage")
        return ModelResult(
            completion=content,
            model_requested=model_requested,
            model_returned=model_returned if isinstance(model_returned, str) else None,
            provider=provider if isinstance(provider, str) else self._provider_label,
            usage=usage if isinstance(usage, dict) else None,
            latency_seconds=latency,
            finish_reason=(
                choices[0].get("finish_reason")
                if isinstance(choices[0].get("finish_reason"), str)
                else None
            ),
            raw=body,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> OpenRouterClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
