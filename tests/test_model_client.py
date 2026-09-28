"""T-038 acceptance: the OpenRouter model client (`agents/model_client.py`, TRD §6, agents.md §19).

The minimal model gateway used by the reference player. It is transport-focused: per-call model
selection, a request timeout, **exactly one** retry on a transient provider/network condition
and none on a deterministic one (agents.md §18 "transiently fails → retry once"), and enough
provenance for T-053 later — requested vs returned model, provider, usage, latency
(agents.md §19). It lives outside ``app/`` (TRD §4) and the default suite runs with no key
(testing.md §3) by driving a fake ``httpx`` transport. The OpenRouter key never reaches a log
or an exception (testing.md §24).
"""

from __future__ import annotations

import io
import json
import logging

import httpx
import pytest

from agents.model_client import (
    ModelClientError,
    ModelConfig,
    ModelResult,
    OpenRouterClient,
    ProviderErrorCategory,
)

_MESSAGES = [{"role": "user", "content": "Say hello."}]


def _completion_body(model: str, content: str = "hello") -> dict:
    return {
        "id": "gen-123",
        "model": model,
        "provider": "TestProvider",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4},
    }


def _client(handler, *, api_key: str = "sk-test-key", timeout: float = 30.0) -> OpenRouterClient:
    return OpenRouterClient(
        api_key=api_key, transport=httpx.MockTransport(handler), timeout=timeout
    )


# --- requested model is propagated; provenance is captured -------------------------------


def test_requested_model_propagated_and_result_populated() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json=_completion_body("anthropic/claude-3.5-sonnet"))

    client = _client(handler)
    result = client.complete(_MESSAGES, config=ModelConfig(model="anthropic/claude-3.5-sonnet"))

    assert isinstance(result, ModelResult)
    assert seen["body"]["model"] == "anthropic/claude-3.5-sonnet"
    assert seen["body"]["messages"] == _MESSAGES
    assert seen["url"].endswith("/chat/completions")
    assert seen["auth"] == "Bearer sk-test-key"
    assert result.model_requested == "anthropic/claude-3.5-sonnet"
    assert result.completion == "hello"
    assert result.provider == "TestProvider"
    assert result.usage == {"prompt_tokens": 3, "completion_tokens": 1, "total_tokens": 4}
    assert result.latency_seconds >= 0.0
    assert result.request_count == 1


def test_different_players_can_request_different_models() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        requested.append(model)
        return httpx.Response(200, json=_completion_body(model))

    client = _client(handler)
    client.complete(_MESSAGES, config=ModelConfig(model="openai/gpt-4o-mini"))
    client.complete(_MESSAGES, config=ModelConfig(model="google/gemini-1.5-flash"))
    assert requested == ["openai/gpt-4o-mini", "google/gemini-1.5-flash"]


def test_returned_model_captured_separately_from_requested() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # The provider served a different concrete model than was requested.
        return httpx.Response(200, json=_completion_body("anthropic/claude-3.5-sonnet-20241022"))

    client = _client(handler)
    result = client.complete(_MESSAGES, config=ModelConfig(model="anthropic/claude-3.5-sonnet"))
    assert result.model_requested == "anthropic/claude-3.5-sonnet"
    assert result.model_returned == "anthropic/claude-3.5-sonnet-20241022"


# --- retry semantics: exactly one retry, transient only ----------------------------------


def test_one_transient_failure_causes_exactly_one_retry() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            raise httpx.ConnectError("boom", request=request)
        return httpx.Response(200, json=_completion_body("m"))

    result = _client(handler).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 2  # one failure + one retry that succeeded
    assert result.completion == "hello"
    assert result.request_count == 2


def test_second_transient_failure_is_surfaced() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectTimeout("still down", request=request)

    with pytest.raises(ModelClientError) as excinfo:
        _client(handler).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 2  # original + exactly one retry, then surfaced
    assert excinfo.value.category is ProviderErrorCategory.TRANSIENT
    assert excinfo.value.request_count == 2
    assert excinfo.value.latency_seconds >= 0


def test_transient_5xx_status_is_retried_once() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": {"message": "overloaded"}})
        return httpx.Response(200, json=_completion_body("m"))

    result = _client(handler).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 2
    assert result.request_count == 2


# --- non-transient failures are not retried ----------------------------------------------


def test_auth_failure_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(401, json={"error": {"message": "invalid key"}})

    with pytest.raises(ModelClientError) as excinfo:
        _client(handler).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 1  # deterministic failure — no retry
    assert excinfo.value.category is ProviderErrorCategory.AUTH
    assert excinfo.value.request_count == 1


def test_unsupported_model_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(404, json={"error": {"message": "no such model"}})

    with pytest.raises(ModelClientError) as excinfo:
        _client(handler).complete(_MESSAGES, config=ModelConfig(model="nope/nope"))
    assert calls["n"] == 1
    assert excinfo.value.category is ProviderErrorCategory.UNSUPPORTED_MODEL


def test_malformed_request_is_not_retried() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    with pytest.raises(ModelClientError) as excinfo:
        _client(handler).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 1
    assert excinfo.value.category is ProviderErrorCategory.INVALID_REQUEST


# --- timeout / config propagation --------------------------------------------------------


def test_per_call_timeout_is_propagated_to_the_request() -> None:
    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=_completion_body("m"))

    client = _client(handler, timeout=30.0)
    client.complete(_MESSAGES, config=ModelConfig(model="m", timeout=5.0))
    # httpx records the effective per-request timeout in the request extensions.
    assert seen["timeout"]["read"] == 5.0


def test_i071_001_complete_can_be_limited_to_a_single_attempt() -> None:
    """I-071: a caller that owns the retry bound (the Umpire, D-063) asks for one attempt."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(503, json={"error": "overloaded"})

    client = _client(handler)
    with pytest.raises(ModelClientError) as exc:
        client.complete(_MESSAGES, config=ModelConfig(model="m"), max_attempts=1)
    assert exc.value.category is ProviderErrorCategory.TRANSIENT
    assert calls["n"] == 1


def test_i072_001_deadline_aborts_a_trickling_body_as_transient() -> None:
    """I-072: a 200 whose body keeps trickling past the wall-clock deadline is abandoned as a
    transient failure — the per-read timeout alone never fires on a kept-alive response."""
    import time

    calls = {"n": 0}

    def trickle():
        # Keep-alive whitespace, as a gateway sends while waiting on a slow upstream.
        for _ in range(50):
            time.sleep(0.02)
            yield b" "
        yield json.dumps(_completion_body("m")).encode()

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=trickle(), headers={"content-type": "application/json"})

    client = _client(handler)
    started = time.perf_counter()
    with pytest.raises(ModelClientError) as exc:
        client.complete(
            _MESSAGES, config=ModelConfig(model="m", deadline=0.1), max_attempts=1
        )
    assert exc.value.category is ProviderErrorCategory.TRANSIENT
    assert "deadline" in str(exc.value)
    assert calls["n"] == 1
    assert time.perf_counter() - started < 0.8  # abandoned well before the full 1 s trickle


def test_i072_002_body_finishing_inside_the_deadline_is_returned() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_completion_body("m"))

    client = _client(handler)
    result = client.complete(_MESSAGES, config=ModelConfig(model="m", deadline=5.0))
    assert result.completion == "hello"


def test_from_settings_uses_central_config() -> None:
    """The client reads its key/timeout from the existing Settings — no new env loader."""
    from app.config import Settings

    settings = Settings(OPENROUTER_API_KEY="sk-from-settings", AMAW_REQUEST_TIMEOUT_SECONDS=12.0)

    seen: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["timeout"] = request.extensions.get("timeout")
        return httpx.Response(200, json=_completion_body("m"))

    client = OpenRouterClient.from_settings(settings, transport=httpx.MockTransport(handler))
    client.complete(_MESSAGES, config=ModelConfig(model="m"))
    assert seen["auth"] == "Bearer sk-from-settings"
    assert seen["timeout"]["read"] == 12.0


# --- the key never leaks into logs -------------------------------------------------------


def test_api_key_never_appears_in_logs() -> None:
    key = "sk-super-secret-key-1234567890"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": {"message": "boom"}})

    buffer = io.StringIO()
    handler_log = logging.StreamHandler(buffer)
    handler_log.setLevel(logging.DEBUG)
    root = logging.getLogger()
    prior = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler_log)
    try:
        with pytest.raises(ModelClientError):
            _client(handler, api_key=key).complete(_MESSAGES, config=ModelConfig(model="m"))
    finally:
        root.removeHandler(handler_log)
        root.setLevel(prior)

    assert key not in buffer.getvalue()


def test_missing_api_key_is_a_configuration_error_without_a_call() -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, json=_completion_body("m"))

    with pytest.raises(ModelClientError) as excinfo:
        _client(handler, api_key="").complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 0  # no provider call attempted without a key
    assert excinfo.value.category is ProviderErrorCategory.CONFIG


# --- optional real provider call (excluded from the default suite) ------------------------


@pytest.mark.llm
def test_real_openrouter_call() -> None:  # pragma: no cover - needs a key
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        pytest.skip("no OPENROUTER_API_KEY")
    client = OpenRouterClient(api_key=key)
    result = client.complete(
        [{"role": "user", "content": "Reply with the single word: pong."}],
        config=ModelConfig(model="openai/gpt-4o-mini", timeout=30.0),
    )
    assert result.completion
    assert result.model_requested == "openai/gpt-4o-mini"


# --- malformed and null-content completions -----------------------------------------------


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, content=b"not-json"),
        httpx.Response(200, json=[]),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json={"choices": [{"message": {"content": 123}}]}),
    ],
)
def test_i009_malformed_success_response_is_normalized(response: httpx.Response) -> None:
    """I-009: every parse failure is a ModelClientError. I-023: it is *transient* — OpenRouter's
    documented intermittent HTTP-200 envelope without a completion is retried exactly once, the
    same classification the PydanticAI player path already gives it (D-018). A completed choice
    whose ``content`` is ``null`` is not malformed — it is an empty completion (I-070)."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return response

    client_ = OpenRouterClient(api_key="key", transport=httpx.MockTransport(handler))
    with pytest.raises(ModelClientError) as exc:
        client_.complete([{"role": "user", "content": "x"}], config=ModelConfig(model="m"))
    assert exc.value.category is ProviderErrorCategory.TRANSIENT
    assert calls["n"] == 2  # first attempt + exactly one retry


def test_i070_003_null_content_is_a_completed_empty_completion() -> None:
    """I-070: ``content: null`` on a completed choice is the caller's unusable-output condition,
    not a transport transient — one request, empty completion, ``finish_reason`` preserved."""
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(
            200,
            json={
                "provider": "OpenInference",
                "choices": [{"message": {"content": None}, "finish_reason": "length"}],
            },
        )

    client_ = OpenRouterClient(api_key="key", transport=httpx.MockTransport(handler))
    result = client_.complete([{"role": "user", "content": "x"}], config=ModelConfig(model="m"))
    assert result.completion == ""
    assert result.finish_reason == "length"
    assert result.provider == "OpenInference"
    assert calls["n"] == 1
