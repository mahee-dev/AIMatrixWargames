"""The model an activation runs through: OpenRouter model construction and per-phase settings, and
the :class:`_TimedModel` wrapper that times each request, accounts its usage and cost, and
compacts completed research history before it reaches the provider."""

from __future__ import annotations

import copy
import dataclasses
import json
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.wrapper import WrapperModel

from amaw_contracts import is_reasoning_effort
from agents.request_topology import RequestTopologyProfiler
from agents.tool_surface import COMPACTABLE_RESEARCH_TOOL_NAMES
from agents.player_errors import PlayerConfigError
from agents.player_config import PlayerConfig, RESEARCH_HISTORY_COMPACT_SUCCESS_V1

# The player logs under one name, whichever of its modules emits the record.
logger = logging.getLogger("agents.player")

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime into app/
    from pydantic_ai.models.openrouter import OpenRouterModelSettings

    from app.config import Settings


def compact_research_history(messages: list[Any]) -> list[Any]:
    """Remove completed success plumbing while retaining exact research results.

    PydanticAI's authoritative graph history is never mutated. A provider-visible copy replaces
    only a complete research-only ``ToolCallPart`` → successful ``ToolReturnPart`` exchange with
    ordinary response/request messages. Returned ``data`` is copied verbatim into canonical JSON;
    failures, mixed exchanges and incomplete calls remain untouched so correction semantics and
    I-052 classification are unchanged.
    """
    compacted: list[Any] = []
    index = 0
    while index < len(messages):
        response = messages[index]
        request = messages[index + 1] if index + 1 < len(messages) else None
        response_parts = list(getattr(response, "parts", []))
        request_parts = list(getattr(request, "parts", []))
        if (
            isinstance(response, ModelResponse)
            and isinstance(request, ModelRequest)
            and response_parts
            and request_parts
            and all(
                isinstance(part, ToolCallPart)
                and part.tool_name in COMPACTABLE_RESEARCH_TOOL_NAMES
                for part in response_parts
            )
            and all(isinstance(part, ToolReturnPart) for part in request_parts)
        ):
            calls = {part.tool_call_id: part for part in response_parts}
            returns = {part.tool_call_id: part for part in request_parts}
            if set(calls) == set(returns) and all(
                isinstance(part.content, dict)
                and part.content.get("ok") is True
                and "data" in part.content
                for part in request_parts
            ):
                retained = {
                    "retained_research_results": [
                        {
                            "tool_name": part.tool_name,
                            "data": part.content["data"],
                        }
                        for part in request_parts
                    ],
                    "schema_version": "research-history-compact-success-v1",
                }
                compacted.append(dataclasses.replace(
                    response,
                    parts=[TextPart(
                        content="Completed research calls; exact returned data follows."
                    )],
                ))
                compacted.append(dataclasses.replace(
                    request,
                    parts=[UserPromptPart(content=json.dumps(
                        retained,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                        default=str,
                    ))],
                ))
                index += 2
                continue
        compacted.append(copy.deepcopy(response))
        index += 1
    return compacted


class _TimedModel(WrapperModel):
    """Measure waits and retain every returned request's usage at the model boundary.

    PydanticAI's model loop calls ``request`` once per model iteration. Wrapping that exact
    boundary gives I-027 the iteration count/provider wait and I-050 usage that survives output
    rejection or a new ``run_sync`` attempt. Timing/usage from only the final outer run loses
    already-consumed provider work.
    """

    def __init__(self, wrapped: Any, *, research_history_mode: str) -> None:
        super().__init__(wrapped)
        self._research_history_mode = research_history_mode
        self._metrics_lock = threading.Lock()
        self._request_count = 0
        self._wait_seconds = 0.0
        self._request_usage: list[dict[str, Any]] = []
        self._excluded_providers: list[str] = []
        self._topology = RequestTopologyProfiler()

    def reset_metrics(self) -> None:
        with self._metrics_lock:
            self._request_count = 0
            self._wait_seconds = 0.0
            self._request_usage = []
            self._excluded_providers = []
            self._topology.reset()

    def metrics(self) -> tuple[int, float, list[dict[str, Any]], dict[str, Any]]:
        with self._metrics_lock:
            return (
                self._request_count,
                self._wait_seconds,
                [dict(item) for item in self._request_usage],
                self._topology.snapshot(),
            )

    async def request(self, messages, model_settings, model_request_parameters):  # noqa: ANN001
        provider_messages = (
            compact_research_history(list(messages))
            if self._research_history_mode == RESEARCH_HISTORY_COMPACT_SUCCESS_V1
            else messages
        )
        with self._metrics_lock:
            self._request_count += 1
            request_number = self._request_count
            excluded = list(self._excluded_providers)
            if excluded:
                model_settings = _settings_ignoring_providers(model_settings, excluded)
            topology_record = self._topology.capture(
                provider_messages,
                model_settings,
                model_request_parameters,
                request_number=request_number,
            )
        started = time.perf_counter()
        response = None
        try:
            response = await self.wrapped.request(
                provider_messages, model_settings, model_request_parameters
            )
            return response
        finally:
            elapsed = time.perf_counter() - started
            with self._metrics_lock:
                self._wait_seconds += elapsed
                if response is not None:
                    usage = _request_usage_dict(
                        response.usage,
                        request_number=request_number,
                        provider_details=response.provider_details,
                    )
                    if excluded:
                        usage["excluded_providers"] = excluded
                    self._request_usage.append(usage)
                    self._exclude_if_unusable(response)
                    self._topology.attach_provider_usage(topology_record, usage)


    def _exclude_if_unusable(self, response: Any) -> None:
        """Route the rest of this activation away from a provider that returned nothing usable.

        A response with neither text nor a tool call (reasoning only, or empty) is a provider
        failure, not a decision: some providers end the turn after the model's reasoning. Later
        requests of the same activation, including the output retry and the activation retry,
        exclude that downstream provider. The model's conversation and retry budget are
        unchanged, so the run condition is too; only routing differs, as for the Umpire (#32,
        D-061). The gateway's own label is never excluded. Called with the metrics lock held.
        """
        if _has_usable_output(response):
            return
        provider = (response.provider_details or {}).get("downstream_provider")
        if not isinstance(provider, str) or not provider or provider.casefold() == "openrouter":
            return
        if provider not in self._excluded_providers:
            self._excluded_providers.append(provider)
            logger.info("excluding provider %s after a response with no text or tool call", provider)


def _has_usable_output(response: Any) -> bool:
    for part in response.parts:
        if isinstance(part, ToolCallPart):
            return True
        if isinstance(part, TextPart) and part.content.strip():
            return True
    return False


def _settings_ignoring_providers(model_settings: Any, providers: list[str]) -> dict[str, Any]:
    """A copy of ``model_settings`` whose OpenRouter routing also ignores ``providers``."""
    settings = dict(model_settings or {})
    routing = dict(settings.get("openrouter_provider") or {})
    ignored = list(routing.get("ignore") or [])
    routing["ignore"] = ignored + [p for p in providers if p not in ignored]
    settings["openrouter_provider"] = routing
    return settings


def _usage_integer(usage: Any, *names: str) -> int:
    details = dict(getattr(usage, "details", {}) or {})
    for name in names:
        value = getattr(usage, name, None)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
        value = details.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _provider_reported_cost(provider_details: dict[str, Any] | None) -> float | None:
    """The cost the provider reported for one request, or ``None`` when it reported none.

    PydanticAI's OpenRouter adapter copies OpenRouter's ``usage.cost`` into
    ``ModelResponse.provider_details["cost"]``. ``RequestUsage.cost`` is not read: PydanticAI
    fills it with a local genai-prices estimate, and cost is never fabricated (D-039, I-117).
    An explicit ``0`` is a known zero cost, not a missing one.
    """
    value = (provider_details or {}).get("cost")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
        return float(value)
    return None


def _request_usage_dict(
    usage: Any, *, request_number: int, provider_details: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Normalize one PydanticAI RequestUsage without double-counting inclusive buckets.

    Routed providers can report a cache/reasoning subset larger than its nominal parent. As on
    the Umpire path (I-066), raise the parent to the subset as the conservative lower bound:
    adding could double-count an already-inclusive provider and clamping would discard observed
    usage. Preserve every adjustment on the per-request reconciliation record.
    """
    details = dict(getattr(usage, "details", {}) or {})
    reasoning = _usage_integer(usage, "reasoning_tokens", "output_reasoning_tokens")
    if reasoning and not ({"reasoning_tokens", "output_reasoning_tokens"} & details.keys()):
        details["output_reasoning_tokens"] = reasoning
    reported_input = _usage_integer(usage, "input_tokens")
    cache_read = _usage_integer(usage, "cache_read_tokens")
    cache_write = _usage_integer(usage, "cache_write_tokens")
    input_tokens = max(reported_input, cache_read, cache_write)
    reported_output = _usage_integer(usage, "output_tokens")
    output_tokens = max(reported_output, reasoning)
    normalization: dict[str, int] = {}
    if input_tokens != reported_input:
        normalization.update({
            "input_tokens_reported": reported_input,
            "input_tokens_recorded": input_tokens,
            "cache_read_tokens_reported": cache_read,
            "cache_write_tokens_reported": cache_write,
        })
    if output_tokens != reported_output:
        normalization.update({
            "output_tokens_reported": reported_output,
            "output_tokens_recorded": output_tokens,
            "reasoning_tokens_reported": reasoning,
        })
    cost = _provider_reported_cost(provider_details)
    record = {
        "request_number": request_number,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
        "cost_usd": cost,
        "details": details,
    }
    if normalization:
        record["usage_normalization"] = normalization
    return record


def _accumulated_usage_dict(
    request_count: int,
    request_usage: list[dict[str, Any]],
    *,
    tool_calls: int,
) -> dict[str, Any]:
    details: dict[str, int] = {}
    for item in request_usage:
        for name, value in item["details"].items():
            if isinstance(value, int) and not isinstance(value, bool):
                details[name] = details.get(name, 0) + value
    known_costs = [item["cost_usd"] for item in request_usage if item["cost_usd"] is not None]
    return {
        "input_tokens": sum(item["input_tokens"] for item in request_usage),
        "output_tokens": sum(item["output_tokens"] for item in request_usage),
        "reasoning_tokens": sum(item["reasoning_tokens"] for item in request_usage),
        "cache_read_tokens": sum(item["cache_read_tokens"] for item in request_usage),
        "cache_write_tokens": sum(item["cache_write_tokens"] for item in request_usage),
        "requests": request_count,
        "usage_reported_requests": len(request_usage),
        "billed_requests": sum(item["cost_usd"] is not None for item in request_usage),
        "tool_calls": tool_calls,
        "cost_usd": sum(known_costs) if known_costs else None,
        "details": details,
        "request_usage": [dict(item) for item in request_usage],
    }


def build_openrouter_model(config: PlayerConfig, settings: Settings) -> Any:
    """A PydanticAI model pointed at OpenRouter (D-016 — the same provider boundary as T-038).

    Key and base URL come from central :class:`app.config.Settings`; the key is never logged.
    Imported lazily so importing this module does not require the ``agents`` extra unless a real
    model is actually built.
    """
    from pydantic_ai.models.openrouter import OpenRouterModel
    from pydantic_ai.providers.openrouter import OpenRouterProvider
    from openai import AsyncOpenAI

    secret = settings.openrouter_api_key
    key = secret.get_secret_value() if secret is not None else ""
    if not key:
        # Deterministic configuration failure — no network call is attempted, no key is exposed.
        raise PlayerConfigError("no OpenRouter API key configured (set OPENROUTER_API_KEY)")

    # Use PydanticAI's native OpenRouter adapter. OpenRouter extends the OpenAI response schema
    # (provider metadata, `error` finish reasons, and occasional null completion envelopes); the
    # generic OpenAI adapter can reject those valid HTTP-200 responses as unexpected behavior.
    provider = OpenRouterProvider(
        openai_client=AsyncOpenAI(
            api_key=key,
            base_url="https://openrouter.ai/api/v1",
            max_retries=0,
        )
    )
    overrides = player_model_settings(config)
    return OpenRouterModel(config.model, provider=provider, settings=overrides or None)


def player_model_settings(config: PlayerConfig) -> OpenRouterModelSettings:
    """The flat base request settings every phase starts from (OpenRouter setting names)."""
    overrides: OpenRouterModelSettings = {}
    if config.temperature is not None:
        overrides["temperature"] = config.temperature
    if config.max_tokens is not None:
        overrides["max_tokens"] = config.max_tokens
    if config.timeout is not None:
        overrides["timeout"] = config.timeout
    effort = config.reasoning_effort
    if effort is not None:
        # ``PlayerConfig`` accepts only OpenRouter's levels, so this never refuses a built config.
        if not is_reasoning_effort(effort):
            raise PlayerConfigError(f"reasoning_effort {effort!r} is not an OpenRouter level")
        overrides["openrouter_reasoning"] = {"effort": effort}
    return overrides


def phase_model_settings(config: PlayerConfig, phase: str | None) -> dict[str, Any]:
    """Per-run settings for one activation: only what the profile changes for ``phase`` (I-032).

    Returned empty under ``uniform`` so the request is byte-identical to the released behaviour;
    otherwise PydanticAI merges these over the model's base settings for this run only.
    """
    if config.inference_profile.is_uniform:
        return {}
    resolved = config.resolve_inference(phase)
    settings: dict[str, Any] = {}
    if resolved["max_tokens"] is not None:
        settings["max_tokens"] = resolved["max_tokens"]
    if resolved["reasoning_effort"] is not None:
        settings["openrouter_reasoning"] = {"effort": resolved["reasoning_effort"]}
    return settings
