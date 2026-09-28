"""Small framework-neutral values shared across AMAW's HTTP boundary."""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import (
    Any,
    Literal,
    NamedTuple,
    NotRequired,
    TypedDict,
    TypeGuard,
    get_origin,
    get_type_hints,
)


STRATEGIC_MEMORY_MAX_BYTES = 12_000

# D-084: versioned Resource-search ranking vocabulary shared across the HTTP core, reference
# player and resume validator without making ``app`` import the agent framework.
SEARCH_CONTRACT_V1 = "search-v1"
SEARCH_CONTRACT_V2 = "search-v2"
SEARCH_CONTRACTS = (SEARCH_CONTRACT_V1, SEARCH_CONTRACT_V2)
# I-112 / D-087 Decision A, D-088: the released default a *new* run resolves when nothing states
# a contract. Records written before I-112 are a different question — a legacy resume recipe with
# no ``search_contract`` still resolves to SEARCH_CONTRACT_V1, because it was sealed under that
# default and is never reinterpreted. Use this name only where the former is meant.
SEARCH_CONTRACT_DEFAULT = SEARCH_CONTRACT_V2

# Credential-shaped keys that must never be persisted into an exportable record (run manifest,
# AgentRun, analysis plan). Compared after ``_normalize_key`` (word boundaries → ``_``, lower-case).
SENSITIVE_KEYS = frozenset({
    "api_key", "authorization", "password", "secret", "token", "seat_token", "seat_tokens",
    "admin_token", "bearer_token",
})

# I-086: credential-shaped, not credential-spelled. A normalised key is refused when it is a
# ``SENSITIVE_KEYS`` member, ends with one of these suffixes or contains one of these fragments.
# ``key`` alone is deliberately not a fragment (``key_points``, ``keyword``); ``_key`` is a suffix.
# ``_tokens`` is deliberately not a suffix: token *counts* (``max_tokens``, ``input_tokens``) are
# ordinary manifest and AgentRun telemetry; the plural credential names are in ``SENSITIVE_KEYS``.
_SENSITIVE_KEY_SUFFIXES = (
    "_token", "_key", "_keys", "_secret", "_secrets", "_password", "_credential", "_credentials",
)
_SENSITIVE_KEY_FRAGMENTS = (
    "authorization", "password", "passwd", "secret", "credential", "bearer", "api_key", "apikey",
)


# I-095: a key's *words* are what is credential-shaped, however they are joined. camelCase is
# split at lower/digit→upper and at an acronym's last capital (``HTTPToken`` → ``HTTP_Token``),
# every non-alphanumeric run becomes one ``_``, then lower-case. ``maxTokens`` therefore becomes
# ``max_tokens`` and stays telemetry under the D-075 non-suffix rule for ``_tokens``.
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NON_ALNUM_RUN = re.compile(r"[^A-Za-z0-9]+")


def _normalize_key(name: Any) -> str:
    text = _CAMEL_BOUNDARY.sub("_", str(name))
    return _NON_ALNUM_RUN.sub("_", text).strip("_").lower()


def is_sensitive_key(name: Any) -> bool:
    """True when a field name is credential-shaped and must not reach an exportable record.

    Applied by the run-manifest and analysis-plan boundaries (testing.md §24 "Do not persist API
    keys or bearer tokens", TRD §36). Names are compared after ``_normalize_key`` — so ``token``,
    ``accessToken``, ``access-token`` and ``ACCESS_TOKEN`` are one shape (I-095). Values are not
    inspected here; ``app.config.redact_secrets`` scrubs values on the way out.
    """
    normalized = _normalize_key(name)
    return (
        normalized in SENSITIVE_KEYS
        or normalized.endswith(_SENSITIVE_KEY_SUFFIXES)
        or any(fragment in normalized for fragment in _SENSITIVE_KEY_FRAGMENTS)
    )


# agents.md §19: the ``inference-profile-v1`` vocabulary, shared by the agent-side loader and the
# server-side sealed-recipe validator (``app`` never imports ``agents``, TRD §4).
INFERENCE_PROFILE_SCHEMA = "inference-profile-v1"
PLAYER_INFERENCE_PHASES = ("diplomacy", "argument", "cons", "reply")
INFERENCE_SETTING_KEYS = ("reasoning_effort", "max_tokens")
# OpenRouter's documented effort levels (``OpenRouterReasoning.effort``); anything else fails closed.
ReasoningEffort = Literal["none", "minimal", "low", "medium", "high", "xhigh"]
REASONING_EFFORTS: tuple[ReasoningEffort, ...] = (
    "none", "minimal", "low", "medium", "high", "xhigh",
)


def is_reasoning_effort(value: object) -> TypeGuard[ReasoningEffort]:
    """Whether ``value`` is one of OpenRouter's effort levels."""
    return value in REASONING_EFFORTS

# The ``autonomous-resume-v1`` recipe (I-048, I-061, I-083): written by
# ``agents.autonomous.build_resume_recipe``, read back by ``PlayerConfig.from_resume_recipe`` and
# validated structurally by ``app.resumption``. The ``TypedDict``s below are the one definition of
# each section; they are plain ``dict``s at runtime, so the sealed bytes are whatever the builder
# writes (I-120). ``NotRequired`` keys are exactly those a pre-condition recipe lacks (I-057
# research modes, the I-092 tool surface, the Umpire prompt version, the game-loop condition), so
# legacy recipes stay resumable while a current one is validated in full. The ``RESUME_*_FIELDS``
# tuples are derived from these types; ``tests/test_provenance_condition_projections.py`` holds the
# builder and the validator to them.
RESUME_RECIPE_SCHEMA = "autonomous-resume-v1"


class ResumePlayerRecipe(TypedDict):
    """``resume.player``: the one player configuration every Seat shares, plus the assignments."""

    assignments: dict[str, str]
    temperature: float | None
    max_tokens: int | None
    timeout: float
    reasoning_effort: str | None
    activation_mode: str
    prompt_version: str
    guardrails: dict[str, int]
    research_mode: NotRequired[str]
    research_history_mode: NotRequired[str]
    search_contract: NotRequired[str]
    tool_surface: NotRequired[str]


class UmpireRecipe(TypedDict):
    """``resume.umpire``: the flat Umpire values the game's one Umpire is composed from."""

    model: str
    temperature: float | None
    max_tokens: int | None
    timeout: float | None
    reasoning_effort: str | None
    prompt_version: NotRequired[str]


class ConsequenceRecipe(TypedDict):
    """``resume.consequence``: the resolver recipe, present only under ``bounded_effects_v1``
    (T-074 / D-091)."""

    model: str
    temperature: float | None
    max_tokens: int | None
    timeout: float
    reasoning_effort: str | None
    prompt_version: str


class ResumeCoordinatorRecipe(TypedDict):
    """``resume.coordinator``: the runner's scheduling bounds and the game-loop condition.

    The condition keys are ``app.game_loop.GAME_LOOP_FIELDS`` plus ``consequence_mode``, which is
    written only when it is not ``none`` (D-091); a pre-condition recipe carries none of them.
    """

    max_cycles: int
    cons_concurrency: int
    diplomacy_concurrency: int
    diplomacy_mode: NotRequired[str]
    diplomacy_rounds_per_turn: NotRequired[int | None]
    con_mode: NotRequired[str]
    con_challengers_per_argument: NotRequired[int | None]
    con_reply_mode: NotRequired[str]
    reply_max_points: NotRequired[int | None]
    consequence_mode: NotRequired[str]


class ResumeRecipe(TypedDict):
    """The whole ``autonomous-resume-v1`` recipe, in the order the builder writes it."""

    schema: str
    player: ResumePlayerRecipe
    umpire: NotRequired[UmpireRecipe]
    consequence: NotRequired[ConsequenceRecipe]
    # The declared inference-profile document; validated by its own loader, so left open here.
    inference_profile: NotRequired[dict[str, Any]]
    coordinator: ResumeCoordinatorRecipe


class RunRuntime(TypedDict):
    """The manifest's ``runtime`` section: how the process ran, not the experimental condition."""

    request_timeout_seconds: float
    max_cycles: int
    log_level: str
    stream_game_details: bool
    guardrails: dict[str, int]


class RunConfiguration(TypedDict):
    """What the CLI hands ``AutonomousGameRunner`` to seal beside the sections it derives."""

    runtime: RunRuntime
    resume: ResumeRecipe


def _typed_keys(record: type, *, required: bool) -> tuple[str, ...]:
    """A ``TypedDict``'s required or ``NotRequired`` keys, in declaration order.

    Read through ``get_type_hints`` because this module postpones annotations, which hides
    ``NotRequired`` from ``__required_keys__``.
    """
    hints = get_type_hints(record, include_extras=True)
    return tuple(
        name for name, hint in hints.items() if (get_origin(hint) is not NotRequired) == required
    )


RESUME_PLAYER_FIELDS = _typed_keys(ResumePlayerRecipe, required=True)
RESUME_PLAYER_OPTIONAL_FIELDS = _typed_keys(ResumePlayerRecipe, required=False)
RESUME_UMPIRE_FIELDS = _typed_keys(UmpireRecipe, required=True)
RESUME_UMPIRE_OPTIONAL_FIELDS = _typed_keys(UmpireRecipe, required=False)
RESUME_CONSEQUENCE_FIELDS = _typed_keys(ConsequenceRecipe, required=True)
# The game-loop condition keys (``app.game_loop.GAME_LOOP_FIELDS``) ride beside these.
RESUME_COORDINATOR_FIELDS = _typed_keys(ResumeCoordinatorRecipe, required=True)
# agents.md §18: the client-side guardrail ceilings ``agents.guardrails.GuardrailConfig`` declares.
PLAYER_GUARDRAIL_FIELDS = (
    "max_llm_iterations", "max_tool_calls", "max_messages_per_activation",
    "max_messages_per_phase", "max_activations_per_opportunity", "max_message_chars",
    "max_argument_action_chars", "max_desired_effect_chars", "max_pros", "max_pro_chars",
    "max_note_chars", "max_tool_payload_bytes",
)
# I-069 added the research-call ceiling, I-098 the research-operation ceiling (D-078); a recipe
# that predates either resolves that ceiling to the released default.
PLAYER_GUARDRAIL_OPTIONAL_FIELDS = ("max_research_tool_calls", "max_research_operations")

INVALID_TOOL_CALL_SCHEMA_VERSION = "invalid-tool-calls-v1"
INVALID_TOOL_CALL_CATEGORIES = (
    "callable_tool_failure",
    "decision_validation_retry",
    "executed_action_rejection",
)
INVALID_TOOL_CALL_REASONS = (
    "server_rejection",
    "client_guardrail",
    "schema_validation",
    "output_validation",
    "execution_precondition",
)

REQUEST_TOPOLOGY_SCHEMA_VERSION = "request-topology-v1"
REQUEST_TOPOLOGY_COMPONENTS = (
    "system_prompts",
    "player_prompts",
    "tool_schemas",
    "output_schemas",
    "scenario_public_state",
    "actor_private_dossier",
    "effects",
    "incremental_messages",
    "strategic_memory",
    "current_argument_cons",
    "resource_results",
    "prior_tool_plumbing",
    "framework_envelope",
)


def canonical_json(value: Any) -> bytes:
    """AMAW's one canonical JSON encoding: sorted keys, minimal separators, UTF-8, non-ASCII kept.

    Every persisted content hash (run manifest, analysis plan, Umpire condition, idempotency
    payload, request topology, activation receipt) is taken over these bytes, so the encoding is
    part of the stored-data contract and must not change.

    Non-finite floats are refused (``ValueError``, I-086): ``NaN``/``Infinity`` are not JSON, and a
    hash over bytes no standard reader can parse is not a content identity. Bytes for every other
    value are unchanged.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


class NonJsonContent(NamedTuple):
    """One reason a document has no canonical JSON form, located by path (I-086, I-093).

    ``reason`` is ``"scalar"`` (a non-finite float or a type ``json.dumps`` does not know, ``value``
    is that scalar), ``"key"`` (a mapping key that is not ``str``, ``path`` is the mapping's path
    and ``value`` the offending key) or ``"cycle"`` (a container that is its own ancestor,
    ``path`` is where it recurs and ``value`` the container).
    """

    path: str
    value: Any
    reason: Literal["scalar", "key", "cycle"]


def first_non_json_scalar(
    value: Any, *, path: str = "", _ancestors: frozenset[int] = frozenset()
) -> NonJsonContent | None:
    """Locate the first reason :func:`canonical_json` cannot encode ``value`` as standard JSON.

    Returns a :class:`NonJsonContent` — the path in ``a.b[2].c`` form — or ``None`` when the whole
    document is plain JSON. Boundaries that canonicalise untrusted content (run manifest
    configuration, analysis plan) use it to raise their own error naming the key rather than the
    serializer's (I-086).

    Mapping keys are part of the contract (I-093): a non-``str`` key is reported, never coerced,
    because ``json.dumps`` would otherwise seal ``{1: …}`` and ``{"1": …}`` to one identity (or
    raise its own ``TypeError`` under ``sort_keys``). A circular structure — a recursive YAML alias
    ``&a [*a]`` — is reported as a ``cycle`` where the container recurs, not as a raw
    ``RecursionError``. Cycles are detected against the *ancestors of the current walk* only: a
    container reused in two places (a non-recursive YAML anchor) is an ordinary acyclic document.
    """
    if isinstance(value, (dict, list, tuple)):
        if id(value) in _ancestors:
            return NonJsonContent(path, value, "cycle")
        ancestors = _ancestors | {id(value)}
        if isinstance(value, dict):
            for key, nested in value.items():
                if not isinstance(key, str):
                    return NonJsonContent(path, key, "key")
                found = first_non_json_scalar(
                    nested, path=f"{path}.{key}" if path else key, _ancestors=ancestors
                )
                if found is not None:
                    return found
            return None
        for index, nested in enumerate(value):
            found = first_non_json_scalar(nested, path=f"{path}[{index}]", _ancestors=ancestors)
            if found is not None:
                return found
        return None
    if isinstance(value, float) and not math.isfinite(value):
        return NonJsonContent(path, value, "scalar")
    if value is None or isinstance(value, (bool, int, float, str)):
        return None
    return NonJsonContent(path, value, "scalar")


def canonical_sha256(value: Any) -> str:
    """SHA-256 hex digest of :func:`canonical_json`: content identity, not object identity."""
    return hashlib.sha256(canonical_json(value)).hexdigest()


def canonical_json_size(value: Any) -> int:
    """Return the UTF-8 byte size of AMAW's canonical JSON representation."""
    return len(canonical_json(value))
