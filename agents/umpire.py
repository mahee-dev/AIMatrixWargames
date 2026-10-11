"""The Umpire implementations (T-018 FakeUmpire, T-042 AIUmpire).

Both implement the :class:`app.umpire.Umpire` boundary; the engine injects one and depends only
on ``app.umpire``.

:class:`FakeUmpire` (T-018) has no model provider, no network and no agent framework — so the
engine can resolve a full game offline, in tests and in the deterministic fixture, and layers 1–3
run without an ``OPENROUTER_API_KEY`` (testing.md §3).

:class:`AIUmpire` (T-042) is the real LLM adjudicator. It is built on the T-038
:class:`~agents.model_client.OpenRouterClient` (``httpx``), **not** PydanticAI (D-020), so this
module — and therefore the ``app`` resolution path that never imports it — pulls in no agent
framework (TRD §4). The AI Umpire only *proposes*: it renders the server-built
:class:`~app.umpire.AdjudicationContext` into the fixed ``umpire-v1.3`` prompt, calls the model
under D-063's bounded attempt policy, and maps the structured JSON to an
:class:`~app.umpire.AdjudicationProposal`. It performs **no** RNG and returns **no** roll or
success: the server owns the draw and the outcome (agents.md §26, INV-RNG-001,
INV-EFFECT-002). Server-owned validation (``_validate_proposal``/``_validate_supersession``) stays
authoritative. D-064 additionally rejects an AI completion whose supersession ids are outside the
active set in its exact authorised context, so a provider schema violation spends a bounded retry
instead of reaching mutation. On an outage or unusable output that survives the bound, it raises
the framework-neutral :class:`~app.umpire.UmpireUnavailable`; the server then pauses the game and
never fabricates an adjudication (TRD §35).

Provenance is invocation-owned (I-081, D-072): :meth:`AIUmpire.adjudicate_recorded` returns one
immutable :class:`~app.umpire.UmpireAdjudication` carrying exactly the attempts that call made.
Nothing is queued on the instance, so concurrent adjudications on the one app-level Umpire cannot
interleave their records and no caller can drain another's.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from importlib import metadata as importlib_metadata
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, replace
from dataclasses import field as dataclasses_field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agents.model_client import (
    ModelClientError,
    ModelConfig,
    ModelResult,
    OpenRouterClient,
    ProviderErrorCategory,
)
from app.models import AdjudicationMode
from app.umpire import (
    MAX_EFFECTS_PER_BRANCH,
    AdjudicationContext,
    AdjudicationProposal,
    UmpireAdjudication,
    UmpireAttemptRecord,
    UmpireUnavailable,
)
from amaw_contracts import REASONING_EFFORTS, is_reasoning_effort
from amaw_resources import read_prompt

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime into app/
    from app.config import Settings

logger = logging.getLogger("agents.umpire")

# The fixed, versioned Umpire prompt (agents.md §27/§31). Loaded from the file at runtime; never
# duplicated into a second embedded copy, and never edited while keeping this version id.
# D-063 (TRD §35): the Umpire's whole outage budget per adjudication — at most this many model
# calls, each bounded by a wall-clock deadline. Fixed conditions, recorded in every manifest.
UMPIRE_MAX_ATTEMPTS = 4
UMPIRE_CALL_DEADLINE_SECONDS = 600.0

UMPIRE_PROMPT_VERSION = "umpire-v1.3"
# I-046 / D-060: the reply-aware prompt. ``visible_reply`` requires it; ``sealed_cons`` keeps
# v1.3 unless an experiment pins v1.4 explicitly so both treatments share one Umpire condition.
UMPIRE_PROMPT_VERSION_REPLY = "umpire-v1.4"
# T-073 / D-090: v1.4 plus the ``scenario_rules`` framing. A game whose scenario declares rules
# requires it; it may be pinned for a rule-free game to hold the Umpire condition constant.
UMPIRE_PROMPT_VERSION_RULES = "umpire-v1.5"
# T-082 / D-096: v1.5 plus Effect discipline — at most ``_DISCIPLINED_EFFECTS_PER_BRANCH`` Effects
# per branch, bounded in the output schema and retried at this boundary. The default for a
# scenario with rules; v1.5 stays selectable.
UMPIRE_PROMPT_VERSION_EFFECTS = "umpire-v1.6"
# v1.6 plus a branch audit: no Effect may rest on another Actor's new decision, and each branch
# has the shape its mode needs (D-112). It is the prompt every unpinned game runs, with or
# without rules and under either Con-reply treatment (D-114). ``UMPIRE_PROMPT_VERSION`` stays the
# released v1.3 identity, because legacy recipes and direct library callers resolve to it.
UMPIRE_PROMPT_VERSION_AUDIT = "umpire-v1.7"
UMPIRE_PROMPT_VERSION_DEFAULT = UMPIRE_PROMPT_VERSION_AUDIT
UMPIRE_PROMPT_VERSIONS = (
    UMPIRE_PROMPT_VERSION,
    UMPIRE_PROMPT_VERSION_REPLY,
    UMPIRE_PROMPT_VERSION_RULES,
    UMPIRE_PROMPT_VERSION_EFFECTS,
    UMPIRE_PROMPT_VERSION_AUDIT,
)
RULES_AWARE_UMPIRE_PROMPT_VERSIONS = frozenset(
    {UMPIRE_PROMPT_VERSION_RULES, UMPIRE_PROMPT_VERSION_EFFECTS, UMPIRE_PROMPT_VERSION_AUDIT}
)
_DISCIPLINED_EFFECTS_PER_BRANCH = 3
# The per-prompt Effect-list bound; a version absent here keeps the server's own cap.
_EFFECT_BOUND_BY_PROMPT = {
    UMPIRE_PROMPT_VERSION_EFFECTS: _DISCIPLINED_EFFECTS_PER_BRANCH,
    UMPIRE_PROMPT_VERSION_AUDIT: _DISCIPLINED_EFFECTS_PER_BRANCH,
}

# I-065: the Umpire's framework identity and output-schema version as persisted on every
# Umpire ``AgentRun`` (TRD §28). The Umpire talks to OpenRouter directly through httpx — there
# is no agent framework and no tool loop — and its "tool schema" is the structured proposal.
UMPIRE_AGENT_FRAMEWORK = "httpx"
UMPIRE_TOOL_SCHEMA_VERSION = "umpire-output-v1"
# D-096: the v1.6 schema differs (bounded Effect lists), so it is a distinct schema identity.
UMPIRE_TOOL_SCHEMA_VERSION_BOUNDED = "umpire-output-v2"


class _RetryableSemanticOutput(UmpireUnavailable):
    """A parseable completion that violates a context-checkable Umpire contract."""

    def __init__(self, message: str, *, code: str, offending_effect_ids: list[str]) -> None:
        super().__init__(message)
        self.code = code
        self.offending_effect_ids = offending_effect_ids


def _usage_integer(usage: dict[str, Any], *names: str) -> int:
    for name in names:
        value: Any = usage
        for part in name.split("."):
            value = value.get(part) if isinstance(value, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            return value
    return 0


def _usage_cost(usage: dict[str, Any]) -> float | None:
    """The provider-reported cost, or ``None`` when it was not reported (D-039: never fabricated)."""
    for name in ("cost_usd", "cost"):
        value = usage.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
            return float(value)
    return None


def _normalize_inclusive_usage(
    usage: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, int]]:
    """Add canonical inclusive buckets without discarding inconsistent provider details.

    Routed providers do not always agree on whether their top-level prompt/completion counts
    include the detailed cache/reasoning subsets. When a subset exceeds its parent, raising the
    parent to that subset is the only safe lower bound: summing could double-count an
    already-inclusive provider, while clamping the subset would discard observed usage.
    """
    normalized = dict(usage or {})
    reported_input = _usage_integer(normalized, "input_tokens", "prompt_tokens")
    reported_output = _usage_integer(normalized, "output_tokens", "completion_tokens")
    reasoning = _usage_integer(
        normalized,
        "reasoning_tokens",
        "output_reasoning_tokens",
        "completion_tokens_details.reasoning_tokens",
    )
    cache_read = _usage_integer(
        normalized,
        "cache_read_tokens",
        "cached_tokens",
        "prompt_tokens_details.cached_tokens",
    )
    cache_write = _usage_integer(
        normalized,
        "cache_write_tokens",
        "cache_creation_input_tokens",
        "prompt_tokens_details.cache_write_tokens",
    )
    input_tokens = max(reported_input, cache_read, cache_write)
    output_tokens = max(reported_output, reasoning)
    normalized.update({
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "reasoning_tokens": reasoning,
        "cache_read_tokens": cache_read,
        "cache_write_tokens": cache_write,
    })
    adjustment: dict[str, int] = {}
    if input_tokens != reported_input:
        adjustment.update({
            "input_tokens_reported": reported_input,
            "input_tokens_recorded": input_tokens,
            "cache_read_tokens_reported": cache_read,
            "cache_write_tokens_reported": cache_write,
        })
    if output_tokens != reported_output:
        adjustment.update({
            "output_tokens_reported": reported_output,
            "output_tokens_recorded": output_tokens,
            "reasoning_tokens_reported": reasoning,
        })
    return normalized, adjustment

# Provider-enforced structured output for the exact agents.md §22 proposal shape. Prompt-only
# JSON requests proved insufficient in the first real T-044 game: the same model produced valid
# JSON for one adjudication and unusable output for the next. OpenRouter's `response_format`
# contract makes syntax/shape deterministic; the server's `_validate_proposal` remains the
# authoritative semantic gate (probability/mode pairing, rationale, supersession eligibility).
_ADJUDICATION_RESPONSE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "json_schema": {
        "name": "amaw_adjudication_proposal",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": [mode.value for mode in AdjudicationMode],
                },
                "probability": {"type": ["number", "null"]},
                "rationale": {"type": "string"},
                "success_effects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_EFFECTS_PER_BRANCH,
                },
                "failure_effects": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": MAX_EFFECTS_PER_BRANCH,
                },
                "success_supersedes_effect_ids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": MAX_EFFECTS_PER_BRANCH,
                    "uniqueItems": True,
                },
                "failure_supersedes_effect_ids": {
                    "type": "array",
                    "items": {"type": "string", "format": "uuid"},
                    "maxItems": MAX_EFFECTS_PER_BRANCH,
                    "uniqueItems": True,
                },
            },
            "required": [
                "mode",
                "probability",
                "rationale",
                "success_effects",
                "failure_effects",
                "success_supersedes_effect_ids",
                "failure_supersedes_effect_ids",
            ],
            "additionalProperties": False,
        },
    },
}
class FakeUmpire:
    """A deterministic, test-configurable Umpire.

    Configure it either with a single fixed ``proposal`` (returned for every Argument) or with a
    ``responder`` callable that maps a context to a proposal (to drive different Arguments in one
    game deterministically). Exactly one of the two must be supplied.
    """

    def __init__(
        self,
        proposal: AdjudicationProposal | None = None,
        *,
        responder: Callable[[AdjudicationContext], AdjudicationProposal] | None = None,
    ) -> None:
        if (proposal is None) == (responder is None):
            raise ValueError("supply exactly one of `proposal` or `responder`")
        self._proposal = proposal
        self._responder = responder

    def adjudicate(self, context: AdjudicationContext) -> AdjudicationProposal:
        """Return the configured proposal — deterministically, with no RNG or side effects."""
        if self._responder is not None:
            return self._responder(context)
        assert self._proposal is not None  # guaranteed by __init__
        return self._proposal

    def provenance_configuration(self) -> dict[str, Any]:
        """Stable non-provider configuration for deterministic/offline run manifests."""
        identity = "fake-umpire-v1"
        return {
            "model": identity,
            "provider": None,
            "prompt_version": identity,
            "prompt_hash": hashlib.sha256(identity.encode()).hexdigest(),
            "settings": {"deterministic": True},
            "adjudication_schema": None,
        }

    def adjudicate_recorded(self, context: AdjudicationContext) -> UmpireAdjudication:
        """The provenance-carrying call (I-065/I-081): a FakeUmpire makes no provider call, so
        the result carries the proposal and no attempt records, and never a failure."""
        return UmpireAdjudication(proposal=self.adjudicate(context), attempts=(), failure=None)


# --- T-042: the AI Umpire ---------------------------------------------------------------


def adjudication_schema_identity() -> dict[str, Any]:
    """Name and canonical content hash of the provider-enforced proposal schema (agents.md §27)."""
    schema = _ADJUDICATION_RESPONSE_FORMAT["json_schema"]
    canonical = json.dumps(schema["schema"], sort_keys=True, separators=(",", ":"))
    return {"name": schema["name"], "sha256": hashlib.sha256(canonical.encode()).hexdigest()}



def load_umpire_prompt(
    *, path: Path | None = None, version: str = UMPIRE_PROMPT_VERSION
) -> str:
    """The fixed, versioned Umpire system prompt, read from package data.

    Loaded at runtime (agents.md §31) — the single source of the prompt text, so a prompt-version
    change is a deliberate file change, never a silent embedded edit. Only the versions this
    code knows how to drive are accepted.
    """
    if path is None:
        if version not in UMPIRE_PROMPT_VERSIONS:
            raise ValueError(
                f"prompt_version must be one of {UMPIRE_PROMPT_VERSIONS}, got {version!r}"
            )
        return read_prompt(version)
    return path.read_text(encoding="utf-8")


@dataclass(frozen=True)
class UmpireProvenance:
    """Provenance for one adjudication call (agents.md §19/§27), consumed by T-053.

    Surfaced on :attr:`AIUmpire.last_provenance` for inspection only; durable capture goes
    through the call's own :class:`~app.umpire.UmpireAdjudication` (I-081). ``model_requested``
    is always what we asked for; ``model_returned`` is the concrete model the provider reports.
    """

    model_requested: str
    model_returned: str | None
    provider: str | None
    usage: dict[str, Any] | None
    latency_seconds: float
    prompt_version: str


@dataclass
class _AdjudicationCall:
    """The state of one in-flight adjudication call (I-081): its attempt records, the safe
    failure diagnosis and the last provider provenance. Local to the call, never on the
    instance, so concurrent calls on one :class:`AIUmpire` cannot see or drain each other."""

    attempts: list[UmpireAttemptRecord] = dataclasses_field(default_factory=list)
    failure: str | None = None
    last_provenance: UmpireProvenance | None = None

    def mark_last_output_failure(self, failure_reason: str, error: UmpireUnavailable) -> None:
        """Mark the just-recorded provider call as unusable without exposing model content."""
        if not self.attempts:  # pragma: no cover - call/record invariant
            return
        record = self.attempts[-1]
        record["completed"] = False
        record["failure_reason"] = failure_reason
        if isinstance(error, _RetryableSemanticOutput):
            provider_metadata = dict(record.get("provider_metadata") or {})
            provider_metadata["semantic_failure"] = {
                "code": error.code,
                "offending_effect_ids": error.offending_effect_ids,
            }
            record["provider_metadata"] = provider_metadata


class AIUmpire:
    """A real LLM adjudicator behind the :class:`app.umpire.Umpire` protocol (agents.md §20–§27).

    Bound to one fixed Umpire model and the ``umpire-v1.3`` prompt. :meth:`adjudicate` renders the
    server-built context into the documented §21 user message, calls the model through the T-038
    :class:`~agents.model_client.OpenRouterClient` under the D-063 bound (at most
    :data:`UMPIRE_MAX_ATTEMPTS` model calls per adjudication, transient failures and unusable
    completions each consuming one, deterministic failures stopping at once), and maps the
    structured JSON to an :class:`~app.umpire.AdjudicationProposal`. It never rolls, decides a branch, or writes Effects
    (agents.md §26). Semantic validity is the server's to gate: parsing is lenient so that a
    representable-but-invalid proposal (out-of-range probability, empty rationale, …) reaches the
    server's ``_validate_proposal`` and is refused there. Only output that cannot be turned into a
    proposal at all — or a model failure that survives the bound — raises
    :class:`~app.umpire.UmpireUnavailable`.
    """

    def __init__(
        self,
        client: OpenRouterClient,
        *,
        model: str,
        prompt_version: str = UMPIRE_PROMPT_VERSION,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        prompt: str | None = None,
        deadline: float | None = UMPIRE_CALL_DEADLINE_SECONDS,
    ) -> None:
        # The Umpire requests only OpenRouter's effort levels, as a Player and an inference-profile
        # entry do; an unknown level fails here rather than at the provider (I-129, D-101).
        if reasoning_effort is not None and not is_reasoning_effort(reasoning_effort):
            raise ValueError(
                f"reasoning_effort must be one of {REASONING_EFFORTS}, got {reasoning_effort!r}"
            )
        self._client = client
        self._prompt_version = prompt_version
        self._prompt = (
            prompt if prompt is not None else load_umpire_prompt(version=prompt_version)
        )
        self._config = ModelConfig(
            model=model,
            timeout=timeout,
            deadline=deadline,
            temperature=temperature,
            max_tokens=max_tokens,
            extra_body={
                "response_format": _ADJUDICATION_RESPONSE_FORMAT,
                # OpenRouter may otherwise route to a downstream provider that silently ignores
                # structured-output parameters. Restrict routing to providers that support every
                # requested parameter so the strict schema is actually enforced.
                "provider": {"require_parameters": True},
                **(
                    {"reasoning": {"effort": reasoning_effort}}
                    if reasoning_effort is not None
                    else {}
                ),
            },
        )
        # Inspection only (tests, operators): provenance and the safe operational diagnosis of
        # the most recent call on this instance. Never stores response content, prompts, private
        # context, credentials, or provider error bodies. Durable capture never reads these —
        # each call owns its records (I-081) — so a concurrent call may overwrite them.
        self.last_provenance: UmpireProvenance | None = None
        self.last_failure: str | None = None

    @classmethod
    def for_openrouter(
        cls,
        settings: Settings,
        *,
        model: str,
        transport: Any | None = None,
        prompt_version: str = UMPIRE_PROMPT_VERSION,
        temperature: float | None = None,
        max_tokens: int | None = None,
        timeout: float | None = None,
        reasoning_effort: str | None = None,
        prompt: str | None = None,
    ) -> AIUmpire:
        """Build an OpenRouter-backed AI Umpire from the central settings (deployment use)."""
        client = OpenRouterClient.from_settings(settings, transport=transport)
        return cls(
            client,
            model=model,
            prompt_version=prompt_version,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            reasoning_effort=reasoning_effort,
            prompt=prompt,
        )

    @property
    def prompt_version(self) -> str:
        return self._prompt_version

    @property
    def prompt(self) -> str:
        """The fixed system prompt text this Umpire sends (inspection only)."""
        return self._prompt

    @property
    def model(self) -> str:
        return self._config.model

    def provenance_configuration(self) -> dict[str, Any]:
        """Exact immutable Umpire configuration for the per-game run manifest."""
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
                "attempts": UMPIRE_MAX_ATTEMPTS,
                "reasoning": self._config.extra_body.get("reasoning"),
                "provider": self._config.extra_body.get("provider"),
            },
            # agents.md §27: the adjudication schema is part of the fixed Umpire identity.
            "adjudication_schema": adjudication_schema_identity(),
        }

    # --- the protocol calls --------------------------------------------------------------

    def adjudicate(self, context: AdjudicationContext) -> AdjudicationProposal:
        """Obtain and structure one adjudication proposal (agents.md §21/§22).

        The plain :class:`~app.umpire.Umpire` contract: the proposal, or
        :class:`~app.umpire.UmpireUnavailable`. Its attempt records are discarded — a caller
        that must persist them uses :meth:`adjudicate_recorded`.
        """
        call = _AdjudicationCall()
        try:
            return self._adjudicate(context, call)
        finally:
            self.last_provenance = call.last_provenance
            self.last_failure = call.failure

    def adjudicate_recorded(self, context: AdjudicationContext) -> UmpireAdjudication:
        """The provenance-carrying call (I-081, ``app.umpire.UmpireProvenanceSource``).

        Never raises for unavailability: the result carries ``proposal=None`` and the failure
        instead, with every attempt already marked. The records belong to this result alone.
        """
        call = _AdjudicationCall()
        proposal: AdjudicationProposal | None
        try:
            proposal = self._adjudicate(context, call)
        except UmpireUnavailable:
            proposal = None
        finally:
            self.last_provenance = call.last_provenance
            self.last_failure = call.failure
        return UmpireAdjudication(
            proposal=proposal, attempts=tuple(call.attempts), failure=call.failure
        )

    def _adjudicate(
        self, context: AdjudicationContext, call: _AdjudicationCall
    ) -> AdjudicationProposal:
        if context.scenario_rules and self._prompt_version not in RULES_AWARE_UMPIRE_PROMPT_VERSIONS:
            # D-090: a rules-unaware prompt would adjudicate without the framing for the scenario's
            # binding rules. Refuse before any provider call; the server pauses with no draw.
            call.failure = f"condition:scenario_rules require {UMPIRE_PROMPT_VERSION_RULES}"
            raise UmpireUnavailable(
                f"scenario_rules require {UMPIRE_PROMPT_VERSION_RULES}; "
                f"this Umpire runs {self._prompt_version}"
            )
        messages = self._render_messages(context)
        config = self._config_for_context(context)
        # D-063 (TRD §35): at most UMPIRE_MAX_ATTEMPTS model calls per adjudication. An unusable
        # completion (most often a truncated one — reasoning consumed the budget and the JSON
        # never came, ``finish_reason=length``) or a transient provider failure consumes one
        # attempt; a deterministic failure stops at once. After an unusable completion the
        # downstream provider that produced it stays excluded for the rest of this adjudication
        # (D-061, cumulative). Exhausting the bound is an outage: the game pauses, nothing is
        # fabricated.
        last = UMPIRE_MAX_ATTEMPTS - 1
        for attempt in range(UMPIRE_MAX_ATTEMPTS):
            try:
                result = self._complete_or_raise(messages, config, call)
            except UmpireUnavailable as exc:
                cause = exc.__cause__
                if (
                    attempt < last
                    and isinstance(cause, ModelClientError)
                    and cause.category is ProviderErrorCategory.TRANSIENT
                ):
                    logger.info(
                        "transient Umpire provider failure (%s) — attempt %d of %d",
                        cause,
                        attempt + 2,
                        UMPIRE_MAX_ATTEMPTS,
                    )
                    continue
                raise
            try:
                proposal = self._parse_completion(result.completion)
                self._validate_context_supersession(context, proposal)
                self._validate_effect_bound(proposal)
                if attempt < last:
                    self._validate_failure_establishes_something(proposal)
            except UmpireUnavailable as exc:
                # `finish_reason` distinguishes a *truncated* completion (``length``) from
                # genuinely malformed output; it is a safe provider enum, never model content.
                call.failure = f"output:{exc}:finish_reason={result.finish_reason or 'none'}"
                call.mark_last_output_failure(call.failure, exc)
                if attempt < last:
                    retry_config = self._config_ignoring_provider(config, result.provider)
                    logger.info(
                        "unusable Umpire output (finish_reason=%s provider=%s) — attempt %d of %d%s",
                        result.finish_reason or "none",
                        result.provider or "unknown",
                        attempt + 2,
                        UMPIRE_MAX_ATTEMPTS,
                        (
                            " through a different provider"
                            if retry_config is not config
                            else ""
                        ),
                    )
                    config = retry_config
                    continue
                raise
            call.failure = None  # a recovered retry is not a failure of this adjudication
            return proposal
        raise AssertionError("unreachable: the retry loop returns or raises")  # pragma: no cover

    @staticmethod
    def _validate_context_supersession(
        context: AdjudicationContext,
        proposal: AdjudicationProposal,
    ) -> None:
        """Reject provider output that escapes the active-id enum supplied for this call.

        This is a retry classification at the AI boundary, not an authority transfer: the server
        still transactionally revalidates every target in ``_validate_supersession``. The context
        contains active Effects only, so an unknown, foreign or inactive id is absent from this
        set. Per-branch duplicates violate the same response schema and are separately labelled.
        """
        allowed = {
            uuid.UUID(str(effect["id"]))
            for effect in context.relevant_effects
            if isinstance(effect, dict) and effect.get("id") is not None
        }
        outside_context: list[uuid.UUID] = []
        duplicates: list[uuid.UUID] = []
        for branch in (
            proposal.success_supersedes_effect_ids,
            proposal.failure_supersedes_effect_ids,
        ):
            seen: set[uuid.UUID] = set()
            for effect_id in branch:
                if effect_id in seen and effect_id not in duplicates:
                    duplicates.append(effect_id)
                seen.add(effect_id)
                if effect_id not in allowed and effect_id not in outside_context:
                    outside_context.append(effect_id)

        if outside_context:
            raise _RetryableSemanticOutput(
                "context-invalid supersession reference",
                code="context_invalid_supersession",
                offending_effect_ids=[str(effect_id) for effect_id in outside_context],
            )
        if duplicates:
            raise _RetryableSemanticOutput(
                "duplicate supersession reference",
                code="duplicate_supersession",
                offending_effect_ids=[str(effect_id) for effect_id in duplicates],
            )

    def _validate_effect_bound(self, proposal: AdjudicationProposal) -> None:
        """D-096: under a disciplined prompt, more Effects than its bound is unusable output.

        Retried within the same D-063 attempt budget like any other unusable completion. The
        server's version-independent cap (``MAX_EFFECTS_PER_BRANCH``) is untouched.
        """
        bound = _EFFECT_BOUND_BY_PROMPT.get(self._prompt_version)
        if bound is None:
            return
        if max(len(proposal.success_effects), len(proposal.failure_effects)) > bound:
            raise _RetryableSemanticOutput(
                f"effect_bound_exceeded: more than {bound} Effects in a branch",
                code="effect_bound_exceeded",
                offending_effect_ids=[],
            )

    @staticmethod
    def _validate_failure_establishes_something(proposal: AdjudicationProposal) -> None:
        """An automatic failure that establishes nothing on either branch is retried elsewhere.

        That is how a provider answered when it ruled a complete Argument "not provided": a short
        completion with no Effects at all (#45). Every genuine automatic failure on record
        established at least one Effect, while an empty automatic success is a legitimate
        self-binding reaffirmation and is left alone. §25 does not forbid an empty failure, so
        the caller skips this check on the last attempt: it diversifies routing within the D-063
        budget and never pauses a game by itself. The server's validation is unchanged.
        """
        if (
            proposal.mode is AdjudicationMode.automatic_failure
            and not proposal.success_effects
            and not proposal.failure_effects
        ):
            raise _RetryableSemanticOutput(
                "empty_automatic_failure: an automatic failure that establishes nothing",
                code="empty_automatic_failure",
                offending_effect_ids=[],
            )

    @staticmethod
    def _config_ignoring_provider(
        config: ModelConfig, provider: str | None
    ) -> ModelConfig:
        """Keep the model condition fixed while diversifying one unusable-output retry.

        OpenRouter can report a nominally successful HTTP-200 response whose assistant content
        is empty or unusable. Its normal provider failover does not apply to that response because
        the gateway saw success. When the response names a concrete downstream provider, exclude
        it from every later attempt of this adjudication (D-061, made cumulative by D-063); never
        treat the gateway fallback label as a provider slug, and never add a model fallback.
        """
        if not provider or provider.casefold() == "openrouter":
            return config
        extra_body = deepcopy(config.extra_body)
        routing = dict(extra_body.get("provider") or {})
        ignored = list(routing.get("ignore") or [])
        if provider in ignored:
            return config
        ignored.append(provider)
        routing["ignore"] = ignored
        extra_body["provider"] = routing
        return replace(config, extra_body=extra_body)

    def _complete_or_raise(
        self, messages: list[dict[str, Any]], config: ModelConfig, call: _AdjudicationCall
    ) -> ModelResult:
        """One provider call recorded on ``call``; a provider failure is raised for the bound."""
        try:
            # ``max_attempts=1``: the D-063 bound in :meth:`adjudicate` owns every retry, so the
            # client's own transient retry must not multiply it.
            result = self._client.complete(messages, config=config, max_attempts=1)
        except ModelClientError as exc:
            # No detail from the model is trusted into an adjudication (TRD §35).
            # The client's message names the failure shape (HTTP status, transport error type,
            # or which part of a 2xx body was unusable) and never carries the key or the body.
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
            raise UmpireUnavailable(
                f"Umpire model call failed ({exc.category.value})"
            ) from exc

        # Record provenance for the actual call before parsing, so it is captured even when the
        # response turns out to be unusable.
        call.last_provenance = UmpireProvenance(
            model_requested=result.model_requested,
            model_returned=result.model_returned,
            provider=result.provider,
            usage=result.usage,
            latency_seconds=result.latency_seconds,
            prompt_version=self._prompt_version,
        )
        normalized_usage, usage_normalization = _normalize_inclusive_usage(result.usage)
        provider_metadata: dict[str, Any] = {"finish_reason": result.finish_reason}
        # I-073: OpenRouter's generation id lets any call be looked up after the fact
        # (``GET /api/v1/generation?id=``) — it is an opaque handle, never model content.
        generation_id = (result.raw or {}).get("id")
        if isinstance(generation_id, str):
            provider_metadata["generation_id"] = generation_id
        if usage_normalization:
            provider_metadata["usage_normalization"] = usage_normalization
        call.attempts.append(
            self._attempt_record(
                config,
                model_returned=result.model_returned,
                provider=result.provider,
                usage=normalized_usage,
                model_calls=result.request_count,
                latency_seconds=result.latency_seconds,
                completed=True,
                failure_reason=None,
                provider_metadata=provider_metadata,
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
        """The one place a provider attempt becomes an :class:`UmpireAttemptRecord` (I-065).

        ``usage`` is already canonical (:func:`_normalize_inclusive_usage`) or empty for an
        attempt that never returned a body. The Umpire execution is one direct provider
        operation with no local tool loop, so its activation wall is the measured provider
        latency, and a call is "costed" exactly when the provider reported a cost (D-039).
        """
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
            "agent_framework": UMPIRE_AGENT_FRAMEWORK,
            "agent_framework_version": importlib_metadata.version("httpx"),
            "tool_schema_version": (
                UMPIRE_TOOL_SCHEMA_VERSION_BOUNDED
                if self._prompt_version in _EFFECT_BOUND_BY_PROMPT
                else UMPIRE_TOOL_SCHEMA_VERSION
            ),
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

    def _config_for_context(self, context: AdjudicationContext) -> ModelConfig:
        """Constrain supersession output to the active Effect ids the server supplied.

        A static ``string`` schema still lets a model invent a UUID or return an Effect
        description. Both are unusable or unauthorised. The per-call enum makes the provider emit
        only an id from this exact server-built context; with no Effects, the array must be empty.
        Server-side supersession validation remains authoritative and transactionally revalidates
        every id before changing state.
        """
        response_format = deepcopy(_ADJUDICATION_RESPONSE_FORMAT)
        bound = _EFFECT_BOUND_BY_PROMPT.get(self._prompt_version)
        if bound is not None:
            properties = response_format["json_schema"]["schema"]["properties"]
            for field in ("success_effects", "failure_effects"):
                properties[field]["maxItems"] = bound
        allowed = [
            str(effect["id"])
            for effect in context.relevant_effects
            if isinstance(effect, dict) and effect.get("id") is not None
        ]
        for field in (
            "success_supersedes_effect_ids",
            "failure_supersedes_effect_ids",
        ):
            supersedes = response_format["json_schema"]["schema"]["properties"][field]
            if allowed:
                supersedes["items"] = {
                    "type": "string",
                    "format": "uuid",
                    "enum": allowed,
                }
            else:
                supersedes["maxItems"] = 0
        extra_body = dict(self._config.extra_body)
        extra_body["response_format"] = response_format
        return replace(self._config, extra_body=extra_body)

    # --- rendering -----------------------------------------------------------------------

    def _render_messages(self, context: AdjudicationContext) -> list[dict[str, Any]]:
        """The system prompt plus the §21 context as a single JSON user message.

        The server-built context is passed verbatim as *data*; scenario/Argument/Con text is
        never merged into the system prompt, and the prompt's own guard treats every field as
        untrusted content, never as instructions (umpire-v1.3).
        """
        payload = {
            "scenario_summary": context.scenario_summary,
            "turn": context.turn,
            "turn_length": context.turn_length,
            "argument": context.argument,
            "cons": context.cons,
            "relevant_effects": context.relevant_effects,
            "relevant_hidden_information": context.relevant_hidden_information,
        }
        if context.owner_reply is not None:
            # I-046: present only under visible_reply after a reply phase, so the sealed_cons
            # rendering stays byte-identical to the released one.
            payload["owner_reply"] = context.owner_reply
        if context.scenario_rules is not None:
            # D-090: its own field, verbatim; absent for a rule-free scenario (byte-identical).
            payload["scenario_rules"] = context.scenario_rules
        return [
            {"role": "system", "content": self._prompt},
            {"role": "user", "content": json.dumps(payload, default=str)},
        ]

    # --- parsing (lenient; the server owns semantic validation) --------------------------

    def _parse_completion(self, text: str) -> AdjudicationProposal:
        """Map the model's structured JSON to a proposal, or raise :class:`UmpireUnavailable`.

        "Unusable" (→ outage) means the output cannot be turned into a proposal object at all: not
        a JSON object, an unknown ``mode``, a non-numeric probability, a non-list Effect field, or
        a malformed superseded id. Everything that *can* be represented — including a
        representable-but-invalid value such as a probability of ``1.1`` or an empty rationale — is
        passed through so the server's ``_validate_proposal`` remains the authoritative gate.
        """
        data = self._extract_json(text)
        if not isinstance(data, dict):
            raise UmpireUnavailable("Umpire returned no usable JSON object")

        mode_raw = data.get("mode")
        try:
            mode = AdjudicationMode(mode_raw)
        except ValueError as exc:
            raise UmpireUnavailable(f"Umpire returned an unusable mode {mode_raw!r}") from exc

        probability = data.get("probability")
        if probability is not None:
            try:
                probability = float(probability)
            except (TypeError, ValueError) as exc:
                raise UmpireUnavailable(
                    f"Umpire returned a non-numeric probability {probability!r}"
                ) from exc

        rationale_raw = data.get("rationale")
        rationale = rationale_raw if isinstance(rationale_raw, str) else ""

        success_effects = self._effect_list(data.get("success_effects"))
        failure_effects = self._effect_list(data.get("failure_effects"))
        success_supersedes = self._supersede_ids(
            data.get("success_supersedes_effect_ids")
        )
        failure_supersedes = self._supersede_ids(
            data.get("failure_supersedes_effect_ids")
        )

        return AdjudicationProposal(
            mode=mode,
            rationale=rationale,
            probability=probability,
            success_effects=success_effects,
            failure_effects=failure_effects,
            success_supersedes_effect_ids=success_supersedes,
            failure_supersedes_effect_ids=failure_supersedes,
        )

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Parse the completion as JSON, tolerating a ```json … ``` code fence."""
        stripped = text.strip()
        if stripped.startswith("```"):
            # Drop the opening fence line (``` or ```json) and any trailing fence.
            body = stripped.split("\n", 1)[1] if "\n" in stripped else ""
            if body.rstrip().endswith("```"):
                body = body.rstrip()[: -len("```")]
            stripped = body.strip()
        try:
            return json.loads(stripped)
        except (json.JSONDecodeError, ValueError):
            return None

    @staticmethod
    def _effect_list(value: Any) -> list[str]:
        """Effect descriptions are a list of strings; a missing field is an empty branch.

        A non-list where descriptions are expected is unusable structure (→ outage). Element-level
        emptiness/typing is the server's to reject (``_validate_proposal``), so elements pass
        through unchanged.
        """
        if value is None:
            return []
        if not isinstance(value, list):
            raise UmpireUnavailable("Umpire Effect field was not a list")
        return list(value)

    @staticmethod
    def _supersede_ids(value: Any) -> list[uuid.UUID]:
        """Parse superseded Effect ids to UUIDs; a well-formed unknown id is the server's to reject.

        A malformed id (not a UUID string) is unusable structure (→ outage); an existence/
        eligibility check against the game belongs to ``_validate_supersession`` (INV-EFFECT rules,
        TEST-ADJ-006), so parseable ids pass through.
        """
        if value is None:
            return []
        if not isinstance(value, list):
            raise UmpireUnavailable("Umpire branch supersession field was not a list")
        ids: list[uuid.UUID] = []
        for item in value:
            try:
                ids.append(uuid.UUID(str(item)))
            except (ValueError, AttributeError, TypeError) as exc:
                raise UmpireUnavailable(
                    f"Umpire returned a malformed superseded Effect id {item!r}"
                ) from exc
        return ids
