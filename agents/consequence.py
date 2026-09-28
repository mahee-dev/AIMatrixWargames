"""The consequence resolvers (T-074, D-091, agents.md §28).

Both implement :class:`app.consequences.ConsequenceResolver`; the engine injects one and depends
only on ``app.consequences``.

:class:`FakeConsequenceResolver` is deterministic and offline. It has no provider, network or
agent framework, so engine tests and the deterministic runner resolve every pass with no
``OPENROUTER_API_KEY`` (testing.md §3).

:class:`AIConsequenceResolver` is the LLM resolver. Like :class:`agents.umpire.AIUmpire`, it uses
the httpx :class:`~agents.model_client.OpenRouterClient` and imports no agent framework (TRD §4).
It works in four steps:

1. render the server-built, public-only :class:`~app.consequences.ConsequenceContext` as data
   after the fixed ``consequence-v1.1`` system prompt;
2. request a strict structured output whose evidence ids are enumerated from that exact context;
3. retry within D-063's bound, at most :data:`CONSEQUENCE_MAX_ATTEMPTS` model calls per pass, on
   an outage or unusable or context-invalid output;
4. map the JSON to a :class:`~app.consequences.ConsequenceProposal`.

It proposes only: it draws no randomness, writes nothing and never advances the game. The
server's transactional validation stays authoritative (INV-CONSEQ-002/003). Provenance is
invocation-owned (the I-081 pattern): :meth:`AIConsequenceResolver.propose_recorded` returns
exactly the attempts that one call made.
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from dataclasses import field as dataclasses_field
from importlib import metadata as importlib_metadata
from typing import TYPE_CHECKING, Any

from agents.model_client import (
    ModelClientError,
    ModelConfig,
    ModelResult,
    OpenRouterClient,
    ProviderErrorCategory,
)
from agents.umpire import _normalize_inclusive_usage, _usage_cost, _usage_integer
from amaw_resources import read_prompt
from app.consequences import (
    EVIDENCE_KIND_EFFECT,
    EVIDENCE_KIND_EVENT,
    MAX_CONSEQUENCE_EFFECTS,
    MAX_EVIDENCE_REFERENCES,
    ConsequenceContext,
    ConsequenceProposal,
    ConsequenceResolution,
    ConsequenceResolverUnavailable,
    ProposedConsequence,
)
from app.umpire import UmpireAttemptRecord

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.config import Settings

logger = logging.getLogger("agents.consequence")

# The fixed, versioned resolver prompt (agents.md §28/§31), read from package data at runtime.
CONSEQUENCE_PROMPT_VERSION = "consequence-v1.1"
CONSEQUENCE_PROMPT_VERSIONS = (CONSEQUENCE_PROMPT_VERSION,)
# D-063 applied to the pass (D-091): at most this many model calls, each under a deadline.
CONSEQUENCE_MAX_ATTEMPTS = 4
CONSEQUENCE_CALL_DEADLINE_SECONDS = 600.0
CONSEQUENCE_AGENT_FRAMEWORK = "httpx"
CONSEQUENCE_TOOL_SCHEMA_VERSION = "consequence-output-v1"

# Provider-enforced structured output for the exact agents.md §28.2 shape. The per-call context
# narrows the evidence ids to an enum (:meth:`AIConsequenceResolver._config_for_context`).
_CONSEQUENCE_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "amaw_consequence_proposal",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "effects": {
                    "type": "array",
                    "maxItems": MAX_CONSEQUENCE_EFFECTS,
                    "items": {
                        "type": "object",
                        "properties": {
                            "description": {"type": "string"},
                            "rationale": {"type": "string"},
                            "evidence": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": MAX_EVIDENCE_REFERENCES,
                                "items": {
                                    "anyOf": [
                                        {
                                            "type": "object",
                                            "properties": {
                                                "kind": {"type": "string", "enum": [EVIDENCE_KIND_EVENT]},
                                                "id": {"type": "integer"},
                                            },
                                            "required": ["kind", "id"],
                                            "additionalProperties": False,
                                        },
                                        {
                                            "type": "object",
                                            "properties": {
                                                "kind": {"type": "string", "enum": [EVIDENCE_KIND_EFFECT]},
                                                "id": {"type": "string"},
                                            },
                                            "required": ["kind", "id"],
                                            "additionalProperties": False,
                                        },
                                    ]
                                },
                            },
                        },
                        "required": ["description", "rationale", "evidence"],
                        "additionalProperties": False,
                    },
                }
            },
            "required": ["effects"],
            "additionalProperties": False,
        },
    },
}


def consequence_schema_identity() -> dict[str, Any]:
    """Name and canonical hash of the static output schema (the fixed condition, agents.md §27)."""
    schema = _CONSEQUENCE_RESPONSE_FORMAT["json_schema"]
    canonical = json.dumps(schema["schema"], sort_keys=True, separators=(",", ":"))
    return {"name": schema["name"], "sha256": hashlib.sha256(canonical.encode()).hexdigest()}


def load_consequence_prompt(version: str = CONSEQUENCE_PROMPT_VERSION) -> str:
    """The fixed, versioned resolver system prompt, read from package data (agents.md §31)."""
    if version not in CONSEQUENCE_PROMPT_VERSIONS:
        raise ValueError(
            f"prompt_version must be one of {CONSEQUENCE_PROMPT_VERSIONS}, got {version!r}"
        )
    return read_prompt(version)


# --- the deterministic fake -------------------------------------------------------------


class FakeConsequenceResolver:
    """A deterministic, test-configurable resolver (the :class:`~agents.umpire.FakeUmpire` twin).

    Configure it with one fixed ``proposal`` or a ``responder`` mapping the context to a
    proposal. A responder may raise :class:`~app.consequences.ConsequenceResolverUnavailable`
    to simulate an outage. ``calls`` counts the passes it was asked for, so tests can prove that a
    resumed game never calls it twice for one turn.
    """

    def __init__(
        self,
        proposal: ConsequenceProposal | None = None,
        *,
        responder: Callable[[ConsequenceContext], ConsequenceProposal] | None = None,
    ) -> None:
        if (proposal is None) == (responder is None):
            raise ValueError("supply exactly one of `proposal` or `responder`")
        self._proposal = proposal
        self._responder = responder
        self.calls = 0
        self.contexts: list[ConsequenceContext] = []

    def propose(self, context: ConsequenceContext) -> ConsequenceProposal:
        self.calls += 1
        self.contexts.append(context)
        if self._responder is not None:
            return self._responder(context)
        assert self._proposal is not None  # guaranteed by __init__
        return self._proposal

    def provenance_configuration(self) -> dict[str, Any]:
        identity = "fake-consequence-v1"
        return {
            "model": identity,
            "provider": None,
            "prompt_version": identity,
            "prompt_hash": hashlib.sha256(identity.encode()).hexdigest(),
            "settings": {"deterministic": True},
            "consequence_schema": None,
        }

    def propose_recorded(self, context: ConsequenceContext) -> ConsequenceResolution:
        try:
            return ConsequenceResolution(proposal=self.propose(context), attempts=(), failure=None)
        except ConsequenceResolverUnavailable as exc:
            return ConsequenceResolution(proposal=None, attempts=(), failure=str(exc) or "unavailable")


# --- the LLM resolver -------------------------------------------------------------------


class _RetryableOutput(ConsequenceResolverUnavailable):
    """A parseable completion that breaks a context-checkable part of the §28.2 contract."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class _PassCall:
    """One in-flight pass: its attempt records and safe failure diagnosis (never shared)."""

    attempts: list[UmpireAttemptRecord] = dataclasses_field(default_factory=list)
    failure: str | None = None


class AIConsequenceResolver:
    """The OpenRouter-backed resolver behind :class:`~app.consequences.ConsequenceResolver`."""

    def __init__(
        self,
        client: OpenRouterClient,
        *,
        model: str,
        prompt_version: str = CONSEQUENCE_PROMPT_VERSION,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        deadline: float | None = CONSEQUENCE_CALL_DEADLINE_SECONDS,
    ) -> None:
        self._client = client
        self._prompt_version = prompt_version
        self._prompt = load_consequence_prompt(prompt_version)
        self._config = ModelConfig(
            model=model,
            timeout=timeout,
            deadline=deadline,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={
                "response_format": _CONSEQUENCE_RESPONSE_FORMAT,
                # As for the Umpire: route only to providers that honour the strict schema.
                "provider": {"require_parameters": True},
                **(
                    {"reasoning": {"effort": reasoning_effort}}
                    if reasoning_effort is not None
                    else {}
                ),
            },
        )

    @classmethod
    def for_openrouter(
        cls,
        settings: Settings,
        *,
        model: str,
        transport: Any | None = None,
        prompt_version: str = CONSEQUENCE_PROMPT_VERSION,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
    ) -> AIConsequenceResolver:
        client = OpenRouterClient.from_settings(settings, transport=transport)
        return cls(
            client,
            model=model,
            prompt_version=prompt_version,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
        )

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def model(self) -> str:
        return self._config.model

    def provenance_configuration(self) -> dict[str, Any]:
        """The exact resolver identity for the run manifest (agents.md §27, D-091)."""
        return {
            "model": self._config.model,
            "provider": "openrouter",
            "prompt_version": self._prompt_version,
            "prompt_hash": hashlib.sha256(self._prompt.encode()).hexdigest(),
            "settings": {
                "temperature": self._config.temperature,
                "max_tokens": self._config.max_tokens,
                "timeout": self._config.timeout,
                "deadline": self._config.deadline,
                # The client's fixed response-body ceiling, recorded beside the other bounds on
                # one call (I-122).
                "max_response_bytes": self._client.max_response_bytes,
                "attempts": CONSEQUENCE_MAX_ATTEMPTS,
                "reasoning": self._config.extra_body.get("reasoning"),
                "provider": self._config.extra_body.get("provider"),
            },
            "consequence_schema": consequence_schema_identity(),
        }

    # --- the protocol calls --------------------------------------------------------------

    def propose(self, context: ConsequenceContext) -> ConsequenceProposal:
        return self._propose(context, _PassCall())

    def propose_recorded(self, context: ConsequenceContext) -> ConsequenceResolution:
        """Never raises for unavailability; the result carries the failure and every attempt."""
        call = _PassCall()
        proposal: ConsequenceProposal | None
        try:
            proposal = self._propose(context, call)
        except ConsequenceResolverUnavailable:
            proposal = None
        return ConsequenceResolution(
            proposal=proposal, attempts=tuple(call.attempts), failure=call.failure
        )

    def _propose(self, context: ConsequenceContext, call: _PassCall) -> ConsequenceProposal:
        messages = [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": json.dumps(context.as_payload(), default=str)},
        ]
        config = self._config_for_context(context)
        last = CONSEQUENCE_MAX_ATTEMPTS - 1
        for attempt in range(CONSEQUENCE_MAX_ATTEMPTS):
            try:
                result = self._complete_or_raise(messages, config, call)
            except ConsequenceResolverUnavailable as exc:
                cause = exc.__cause__
                if (
                    attempt < last
                    and isinstance(cause, ModelClientError)
                    and cause.category is ProviderErrorCategory.TRANSIENT
                ):
                    continue
                raise
            try:
                proposal = self._parse_completion(result.completion)
                self._validate_against_context(context, proposal)
            except ConsequenceResolverUnavailable as exc:
                call.failure = f"output:{exc}:finish_reason={result.finish_reason or 'none'}"
                record = call.attempts[-1]
                record["completed"] = False
                record["failure_reason"] = call.failure
                if isinstance(exc, _RetryableOutput):
                    metadata = dict(record.get("provider_metadata") or {})
                    metadata["semantic_failure"] = {"code": exc.code}
                    record["provider_metadata"] = metadata
                if attempt < last:
                    config = self._config_ignoring_provider(config, result.provider)
                    logger.info(
                        "unusable consequence output (finish_reason=%s) — attempt %d of %d",
                        result.finish_reason or "none",
                        attempt + 2,
                        CONSEQUENCE_MAX_ATTEMPTS,
                    )
                    continue
                raise
            call.failure = None
            return proposal
        raise AssertionError("unreachable: the retry loop returns or raises")  # pragma: no cover

    @staticmethod
    def _allowed_ids(context: ConsequenceContext) -> tuple[list[int], list[str]]:
        events = [int(o["event_id"]) for o in context.outcomes]
        effects = [str(e["id"]) for e in context.active_effects]
        return events, effects

    def _config_for_context(self, context: ConsequenceContext) -> ModelConfig:
        """Enumerate the citable ids of this exact context in the schema (the D-064 pattern).

        A kind with nothing citable in this context is removed from the schema. With nothing
        citable at all the proposal must be empty. The server still revalidates every reference
        transactionally.
        """
        response_format = deepcopy(_CONSEQUENCE_RESPONSE_FORMAT)
        effects_schema = response_format["json_schema"]["schema"]["properties"]["effects"]
        evidence = effects_schema["items"]["properties"]["evidence"]
        event_ids, effect_ids = self._allowed_ids(context)
        branches = evidence["items"]["anyOf"]
        kept = []
        if event_ids:
            branches[0]["properties"]["id"]["enum"] = event_ids
            kept.append(branches[0])
        if effect_ids:
            branches[1]["properties"]["id"]["enum"] = effect_ids
            kept.append(branches[1])
        if kept:
            evidence["items"]["anyOf"] = kept
        else:
            effects_schema["maxItems"] = 0
        extra_body = dict(self._config.extra_body)
        extra_body["response_format"] = response_format
        return replace(self._config, extra_body=extra_body)

    def _validate_against_context(
        self, context: ConsequenceContext, proposal: ConsequenceProposal
    ) -> None:
        """Retry-classify output that escapes this context (never an authority transfer)."""
        if len(proposal.effects) > MAX_CONSEQUENCE_EFFECTS:
            raise _RetryableOutput("too many consequence Effects", code="too_many_effects")
        event_ids, effect_ids = self._allowed_ids(context)
        allowed = {(EVIDENCE_KIND_EVENT, i) for i in event_ids} | {
            (EVIDENCE_KIND_EFFECT, i) for i in effect_ids
        }
        for proposed in proposal.effects:
            if not proposed.evidence:
                raise _RetryableOutput("consequence without evidence", code="missing_evidence")
            keys = []
            for ref in proposed.evidence:
                key = (ref.get("kind"), ref.get("id"))
                if key not in allowed:
                    raise _RetryableOutput(
                        "context-invalid evidence reference", code="context_invalid_evidence"
                    )
                keys.append(key)
            if len(set(keys)) != len(keys):
                raise _RetryableOutput("duplicate evidence reference", code="duplicate_evidence")

    @staticmethod
    def _config_ignoring_provider(config: ModelConfig, provider: str | None) -> ModelConfig:
        """Exclude a downstream provider that returned unusable output (D-061/D-063)."""
        if not provider or provider.casefold() == "openrouter":
            return config
        extra_body = deepcopy(config.extra_body)
        routing = dict(extra_body.get("provider") or {})
        ignored = list(routing.get("ignore") or [])
        if provider in ignored:
            return config
        routing["ignore"] = [*ignored, provider]
        extra_body["provider"] = routing
        return replace(config, extra_body=extra_body)

    def _complete_or_raise(
        self, messages: list[dict[str, Any]], config: ModelConfig, call: _PassCall
    ) -> ModelResult:
        try:
            result = self._client.complete(messages, config=config, max_attempts=1)
        except ModelClientError as exc:
            call.failure = (
                f"provider:{exc.category.value}:status={exc.status_code or 'none'}:{exc}"
            )
            call.attempts.append(
                self._attempt_record(
                    config,
                    model_returned=None,
                    provider="openrouter",
                    usage={},
                    model_calls=exc.request_count,
                    latency_seconds=exc.latency_seconds,
                    completed=False,
                    failure_reason=call.failure,
                    provider_metadata={
                        "error_category": exc.category.value,
                        "status_code": exc.status_code,
                    },
                )
            )
            raise ConsequenceResolverUnavailable(
                f"consequence model call failed ({exc.category.value})"
            ) from exc
        usage, normalization = _normalize_inclusive_usage(result.usage)
        metadata: dict[str, Any] = {"finish_reason": result.finish_reason}
        generation_id = (result.raw or {}).get("id")
        if isinstance(generation_id, str):
            metadata["generation_id"] = generation_id
        if normalization:
            metadata["usage_normalization"] = normalization
        call.attempts.append(
            self._attempt_record(
                config,
                model_returned=result.model_returned,
                provider=result.provider,
                usage=usage,
                model_calls=result.request_count,
                latency_seconds=result.latency_seconds,
                completed=True,
                failure_reason=None,
                provider_metadata=metadata,
            )
        )
        return result

    def _attempt_record(
        self,
        config: ModelConfig,
        *,
        model_returned: str | None,
        provider: str | None,
        usage: dict[str, Any],
        model_calls: int,
        latency_seconds: float,
        completed: bool,
        failure_reason: str | None,
        provider_metadata: dict[str, Any],
    ) -> UmpireAttemptRecord:
        """One attempt in the Umpire's normalised record shape; no prompt, context or key."""
        cost_usd = _usage_cost(usage)
        latency_ms = round(latency_seconds * 1000)
        return {
            "model_requested": config.model,
            "model_returned": model_returned,
            "provider": provider,
            "prompt_version": self._prompt_version,
            "prompt_hash": hashlib.sha256(self._prompt.encode()).hexdigest(),
            "model_parameters": {
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "timeout": config.timeout,
                "deadline": config.deadline,
            },
            "reasoning_configuration": config.extra_body.get("reasoning"),
            "agent_framework": CONSEQUENCE_AGENT_FRAMEWORK,
            "agent_framework_version": importlib_metadata.version("httpx"),
            "tool_schema_version": CONSEQUENCE_TOOL_SCHEMA_VERSION,
            "input_tokens": _usage_integer(usage, "input_tokens"),
            "output_tokens": _usage_integer(usage, "output_tokens"),
            "reasoning_tokens": _usage_integer(usage, "reasoning_tokens"),
            "cache_read_tokens": _usage_integer(usage, "cache_read_tokens"),
            "cache_write_tokens": _usage_integer(usage, "cache_write_tokens"),
            "model_calls": model_calls,
            "costed_model_calls": 1 if cost_usd is not None else 0,
            "tool_calls": 0,
            "invalid_tool_calls": 0,
            "stalls": 0,
            "cost_usd": cost_usd,
            "latency_ms": latency_ms,
            "activation_wall_ms": latency_ms,
            "completed": completed,
            "failure_reason": failure_reason,
            "provider_metadata": provider_metadata,
        }

    # --- parsing (structure only; the server owns semantic validation) -------------------

    @staticmethod
    def _parse_completion(text: str) -> ConsequenceProposal:
        stripped = (text or "").strip()
        if stripped.startswith("```"):
            body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
            if body.rstrip().endswith("```"):
                body = body.rstrip()[: -len("```")]
            stripped = body.strip()
        try:
            data = json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            data = None
        if not isinstance(data, dict) or not isinstance(data.get("effects"), list):
            raise ConsequenceResolverUnavailable("resolver returned no usable JSON object")
        effects: list[ProposedConsequence] = []
        for item in data["effects"]:
            if not isinstance(item, dict) or not isinstance(item.get("evidence"), list):
                raise ConsequenceResolverUnavailable("resolver Effect was not a structured object")
            if not all(isinstance(ref, dict) for ref in item["evidence"]):
                raise ConsequenceResolverUnavailable("resolver evidence was not a list of objects")
            description = item.get("description")
            rationale = item.get("rationale")
            effects.append(
                ProposedConsequence(
                    description=description if isinstance(description, str) else "",
                    rationale=rationale if isinstance(rationale, str) else "",
                    evidence=[dict(ref) for ref in item["evidence"]],
                )
            )
        return ConsequenceProposal(effects=effects)
