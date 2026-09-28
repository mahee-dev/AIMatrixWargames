"""I-122: the direct provider client refuses a response body above a fixed byte ceiling.

The Umpire and the consequence resolver call OpenRouter through ``agents.model_client``, which
streams the completion body into memory. The body is bounded by ``MAX_RESPONSE_BYTES``, counted
while streaming. Crossing it stops the read at once and fails closed with a non-transient
``ModelClientError``, so neither the client's own transient retry nor the Umpire's or resolver's
attempt bound (D-063) repeats the call. A body at or under the limit parses exactly as before.
The limit is recorded beside ``timeout``/``deadline`` in the Umpire and resolver provenance.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from agents.consequence import AIConsequenceResolver
from agents.model_client import (
    MAX_RESPONSE_BYTES,
    ModelClientError,
    ModelConfig,
    OpenRouterClient,
    ProviderErrorCategory,
)
from agents.umpire import AIUmpire
from app.consequences import ConsequenceContext, ConsequenceResolverUnavailable
from app.umpire import AdjudicationContext, UmpireUnavailable

_MESSAGES = [{"role": "user", "content": "Say hello."}]


def _body(content: str = "hello") -> bytes:
    return json.dumps({
        "id": "gen-1",
        "model": "m",
        "provider": "P",
        "choices": [{"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }).encode()


def _client(handler, *, limit: int | None = None) -> OpenRouterClient:
    kwargs = {} if limit is None else {"max_response_bytes": limit}
    return OpenRouterClient(api_key="sk-test", transport=httpx.MockTransport(handler), **kwargs)


def _streaming(chunks, pulled: list[int]):
    """A body generator that records how many chunks the client actually pulled."""

    def stream():
        for chunk in chunks:
            pulled.append(len(chunk))
            yield chunk

    return stream()


def test_i122_001_limit_is_a_fixed_generous_constant() -> None:
    assert MAX_RESPONSE_BYTES == 2 * 1024 * 1024
    assert OpenRouterClient(api_key="k").max_response_bytes == MAX_RESPONSE_BYTES


def test_i122_002_body_below_the_limit_is_parsed_unchanged() -> None:
    body = _body()

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=body)

    result = _client(handler, limit=len(body) + 1).complete(
        _MESSAGES, config=ModelConfig(model="m")
    )
    assert result.completion == "hello"
    assert result.raw == json.loads(body)
    assert result.request_count == 1


def test_i122_003_body_exactly_at_the_limit_is_accepted() -> None:
    body = _body()
    padded = body + b" " * 64  # trailing JSON whitespace: still one valid document
    pulled: list[int] = []

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_streaming([padded[:40], padded[40:]], pulled))

    result = _client(handler, limit=len(padded)).complete(
        _MESSAGES, config=ModelConfig(model="m")
    )
    assert result.completion == "hello"
    assert sum(pulled) == len(padded)


def test_i122_004_streamed_body_over_the_limit_stops_reading_at_once() -> None:
    pulled: list[int] = []
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        # Far more than the limit if read to the end; the client must stop at chunk two.
        return httpx.Response(200, content=_streaming([b"x" * 600] * 1000, pulled))

    with pytest.raises(ModelClientError) as exc:
        _client(handler, limit=1000).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert pulled == [600, 600]
    assert exc.value.category is ProviderErrorCategory.RESPONSE_TOO_LARGE
    assert "1000" in str(exc.value)
    assert "sk-test" not in str(exc.value)


def test_i122_005_oversize_is_non_transient_and_never_retried() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=b"y" * 2000)

    with pytest.raises(ModelClientError) as exc:
        # The default ``max_attempts=2`` would retry a transient failure once.
        _client(handler, limit=1000).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert exc.value.category is not ProviderErrorCategory.TRANSIENT
    assert calls["n"] == 1
    assert exc.value.request_count == 1


def test_i122_006_transient_retry_is_unchanged_under_the_limit() -> None:
    calls = {"n": 0}
    body = _body()

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={"error": "overloaded"})
        return httpx.Response(200, content=body)

    result = _client(handler, limit=len(body)).complete(_MESSAGES, config=ModelConfig(model="m"))
    assert calls["n"] == 2
    assert result.request_count == 2
    assert result.completion == "hello"


_UMPIRE_CONTEXT = AdjudicationContext(
    scenario_summary="A regional standoff.",
    turn=1,
    turn_length="2 weeks",
    argument={"actor_id": "a", "action": "act", "desired_effect": "effect", "pros": ["p"]},
    cons=[],
    relevant_effects=[],
    relevant_hidden_information=[],
)


def test_i122_007_umpire_attempt_bound_does_not_retry_an_oversize_body() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=b"z" * 5000)

    umpire = AIUmpire(_client(handler, limit=1000), model="test/umpire")
    with pytest.raises(UmpireUnavailable):
        umpire.adjudicate(_UMPIRE_CONTEXT)
    assert calls["n"] == 1  # D-063 retries transient failures only
    assert umpire.last_failure is not None
    assert umpire.last_failure.startswith("provider:response_too_large:status=none:")


def test_i122_008_consequence_attempt_bound_does_not_retry_an_oversize_body() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, content=b"z" * 5000)

    resolver = AIConsequenceResolver(_client(handler, limit=1000), model="test/resolver")
    context = ConsequenceContext(
        scenario_summary="s", turn=1, turn_length=None,
        outcomes=[{"event_id": 1, "actor_id": "a", "action": "act", "desired_effect": "e",
                   "mode": "automatic_success", "success": True,
                   "created_effect_ids": [str(uuid.uuid4())]}],
        active_effects=[],
    )
    with pytest.raises(ConsequenceResolverUnavailable):
        resolver.propose(context)
    assert calls["n"] == 1
    resolution = resolver.propose_recorded(context)
    assert resolution.proposal is None
    assert resolution.failure is not None
    assert resolution.failure.startswith("provider:response_too_large:")
    assert calls["n"] == 2  # one more call for the second pass, still no retry


def test_i122_009_limit_is_recorded_beside_timeout_in_provenance() -> None:
    client = OpenRouterClient(api_key="sk-test")
    umpire_settings = AIUmpire(client, model="u").provenance_configuration()["settings"]
    resolver_settings = AIConsequenceResolver(client, model="c").provenance_configuration()[
        "settings"
    ]
    assert umpire_settings["max_response_bytes"] == MAX_RESPONSE_BYTES
    assert resolver_settings["max_response_bytes"] == MAX_RESPONSE_BYTES
    # Only the limit was added: the resume recipe derivation reads the same five keys.
    from app.provenance import resume_umpire_from_provenance

    assert set(resume_umpire_from_provenance(
        AIUmpire(client, model="u").provenance_configuration()
    )) == {"model", "temperature", "max_tokens", "timeout", "reasoning_effort"}
