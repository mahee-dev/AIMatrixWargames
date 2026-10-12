"""The reference player's sealed configuration: the activation, research, Con-reply and tool-surface
vocabularies, :class:`PlayerConfig`, and the prompt and tool-schema versions a configuration
resolves to (agents.md §17, §19, §31)."""

from __future__ import annotations

import dataclasses
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amaw_contracts import (
    REASONING_EFFORTS,
    SEARCH_CONTRACTS,
    SEARCH_CONTRACT_DEFAULT,
    SEARCH_CONTRACT_V1,
    is_reasoning_effort,
)
from agents.guardrails import GuardrailConfig
from agents.inference_profile import INFERENCE_PROFILE_UNIFORM, InferenceProfile
from amaw_resources import read_prompt


# The versioned neutral prompts (I-001 settled their authoritative source; D-003). Read from
# wheel package data at runtime, never embedded in code (agents.md §17). Each activation mode has
# its own immutable prompt version (agents.md §31). I-034 issues new versions for the incremental
# context + rolling-memory benchmark condition; all earlier versions remain immutable.
ACTIVATION_MODE_TOOL_LOOP = "tool_loop"
ACTIVATION_MODE_SINGLE_SHOT = "single_shot"
ACTIVATION_MODES = (ACTIVATION_MODE_TOOL_LOOP, ACTIVATION_MODE_SINGLE_SHOT)
PLAYER_CONTEXT_MODE = "incremental-context-v1"
PLAYER_MEMORY_VERSION = "strategic-memory-v1"
RESEARCH_MODE_SEQUENTIAL_V1 = "sequential-v1"
RESEARCH_MODE_BATCHED_V1 = "batched-v1"
RESEARCH_MODES = (RESEARCH_MODE_SEQUENTIAL_V1, RESEARCH_MODE_BATCHED_V1)
# I-092 / D-079: the mutating tools a ``tool_loop`` model is offered. ``broad`` is the whole
# vocabulary (attempts outside ``available_actions`` stay measurable); ``available_only`` follows
# the server's current declaration. Sealed like ``activation_mode``; inert outside ``tool_loop``.
TOOL_SURFACE_BROAD = "broad"
TOOL_SURFACE_AVAILABLE_ONLY = "available_only"
TOOL_SURFACES = (TOOL_SURFACE_BROAD, TOOL_SURFACE_AVAILABLE_ONLY)
RESEARCH_HISTORY_FULL_V1 = "full-v1"
RESEARCH_HISTORY_COMPACT_SUCCESS_V1 = "compact-success-v1"
RESEARCH_HISTORY_MODES = (
    RESEARCH_HISTORY_FULL_V1,
    RESEARCH_HISTORY_COMPACT_SUCCESS_V1,
)
RESEARCH_BATCH_MAX_ITEMS = 8
BASELINE_TOOL_SCHEMA_VERSION = "player-tools-v11-live-bounded-research"
# I-101 / D-085 (v17/v18, "item-outcomes"): the batch tools continue after recoverable item
# failures and return one ordered outcome per executed item. The sequential surfaces (v11/v13)
# are byte-identical and stand.
BATCHED_TOOL_SCHEMA_VERSION = (
    "player-tools-v17-batched-live-bounded-operations-item-outcomes"
)
# I-046 / D-060: the visible_reply schemas add ``reply_to_cons``. I-069 versions both surfaces
# because bounded dynamic research availability changes the model-facing tool definitions.
# I-091 / D-077 (v11–v14, "live"): after the Actor's own successful mutation the tool-loop
# surface follows the server's refreshed declaration and results carry a §6 context fragment.
REPLY_TOOL_SCHEMA_VERSION = "player-tools-v13-reply-live-bounded-research"
BATCHED_REPLY_TOOL_SCHEMA_VERSION = (
    "player-tools-v18-batched-reply-live-bounded-operations-item-outcomes"
)
# The agents.md §6 ``observe`` shape, attached to a successful mutation's result as ``context``.
_CONTEXT_FRAGMENT_KEYS = ("turn", "phase", "active_actor_id", "requires_input", "available_actions")
CON_REPLY_MODE_SEALED = "sealed_cons"
CON_REPLY_MODE_VISIBLE = "visible_reply"
CON_REPLY_MODES = (CON_REPLY_MODE_SEALED, CON_REPLY_MODE_VISIBLE)
PLAYER_PROMPT_VERSIONS = {
    ACTIVATION_MODE_TOOL_LOOP: "player-v1.10",
    ACTIVATION_MODE_SINGLE_SHOT: "player-v1.11",
}
# The visible_reply prompts state the Con visibility before any Con is submitted (agents.md
# §12); a sealed-Con prompt is never reused unchanged for that mode (D-060).
REPLY_PLAYER_PROMPT_VERSIONS = {
    ACTIVATION_MODE_TOOL_LOOP: "player-v1.12",
    ACTIVATION_MODE_SINGLE_SHOT: "player-v1.13",
}
PLAYER_PROMPT_VERSION = PLAYER_PROMPT_VERSIONS[ACTIVATION_MODE_TOOL_LOOP]  # historical name


@dataclass(frozen=True)
class PlayerConfig:
    """Per-player model configuration (agents.md §19). Model selection is per Actor/player."""

    model: str
    temperature: float | None = None
    max_tokens: int | None = None
    timeout: float | None = None
    reasoning_effort: str | None = None
    # How one activation is driven (I-031): ``tool_loop`` — the model is expected to call one
    # mutating tool per provider round-trip (siblings in one response race, D-082);
    # ``single_shot`` — one structured decision, research tools still callable.
    # A benchmark condition, recorded in the run manifest and every AgentRun row.
    activation_mode: str = ACTIVATION_MODE_SINGLE_SHOT
    # I-057: independently selectable model-facing research surface and provider-visible
    # intra-activation history policy. Released behavior remains the default.
    research_mode: str = RESEARCH_MODE_SEQUENTIAL_V1
    # D-084: server-side Resource matching/ranking, independent of research request topology.
    # I-112/D-088: the evaluated token-coverage contract is the released default; v1 is explicit.
    search_contract: str = SEARCH_CONTRACT_DEFAULT
    research_history_mode: str = RESEARCH_HISTORY_FULL_V1
    # I-092 / D-079: which mutating tools a ``tool_loop`` model is offered — ``broad`` (the
    # whole vocabulary, today's behaviour) or ``available_only`` (only what the server's current
    # ``available_actions`` lists). Always a valid sealed value; it governs only the tool-loop
    # mutating surface, so under ``single_shot`` it is recorded as given and has no effect.
    tool_surface: str = TOOL_SURFACE_BROAD
    # I-032: per-phase overrides of ``reasoning_effort``/``max_tokens``; ``uniform`` = none.
    inference_profile: InferenceProfile = INFERENCE_PROFILE_UNIFORM
    # I-046 / D-060: the game's Con-reply condition as the player must know it — it selects the
    # prompt (visibility disclosure) and the tool schema. Released ``sealed_cons`` is the default.
    con_reply_mode: str = CON_REPLY_MODE_SEALED
    # ``None`` resolves to the mode's own immutable prompt version (agents.md §31).
    prompt_version: str | None = None
    guardrails: GuardrailConfig = field(default_factory=GuardrailConfig)

    def shared_condition(self) -> dict[str, Any]:
        """Every field but ``model``: the player condition one game's Seats must share (I-083).

        The canonical projection behind the runner's roster check — derived from the dataclass
        fields so a newly added setting joins the held-constant set by construction
        (benchmark-methodology.md §5.1, agents.md §19: "vary only the underlying model").
        """
        return {
            f.name: getattr(self, f.name) for f in dataclasses.fields(self) if f.name != "model"
        }

    def resolve_inference(self, phase: str | None) -> dict[str, Any]:
        """The ``reasoning_effort``/``max_tokens`` actually requested in ``phase`` (I-032)."""
        return self.inference_profile.resolve_phase(
            phase or "", reasoning_effort=self.reasoning_effort, max_tokens=self.max_tokens
        )

    @classmethod
    def from_resume_recipe(cls, recipe: dict[str, Any], *, model: str) -> PlayerConfig:
        """The shared player configuration a sealed ``autonomous-resume-v1`` recipe describes.

        The inverse of :func:`agents.autonomous.build_resume_recipe` (I-061): a recipe parsed
        here and rebuilt there is byte-identical, which is what ``resume`` relies on. A
        pre-condition recipe carries no research keys, no inference profile, no Con-reply
        condition and legacy guardrails; each resolves to the released default it ran under.
        """
        from app.game_loop import GameLoopCondition

        player = recipe["player"]
        sealed_profile = recipe.get("inference_profile")
        return cls(
            model=model,
            temperature=player["temperature"],
            max_tokens=player["max_tokens"],
            timeout=player["timeout"],
            reasoning_effort=player["reasoning_effort"],
            activation_mode=player["activation_mode"],
            research_mode=player.get("research_mode", RESEARCH_MODE_SEQUENTIAL_V1),
            # A pre-I-112 recipe was sealed under the v1 default and is never reinterpreted
            # as v2 (D-084, D-088); only a recipe that states the contract carries it.
            search_contract=player.get("search_contract", SEARCH_CONTRACT_V1),
            research_history_mode=player.get(
                "research_history_mode", RESEARCH_HISTORY_FULL_V1
            ),
            # A pre-I-092 recipe ran the broad surface; it is never silently switched (D-079).
            tool_surface=player.get("tool_surface", TOOL_SURFACE_BROAD),
            inference_profile=(
                InferenceProfile.from_document(sealed_profile, sealed=True)
                if sealed_profile is not None
                else INFERENCE_PROFILE_UNIFORM
            ),
            con_reply_mode=GameLoopCondition.from_mapping(recipe["coordinator"]).con_reply_mode,
            prompt_version=player["prompt_version"],
            guardrails=GuardrailConfig(**player["guardrails"]),
        )

    def __post_init__(self) -> None:
        if not isinstance(self.inference_profile, InferenceProfile):
            raise ValueError("inference_profile must be an InferenceProfile")
        if self.activation_mode not in ACTIVATION_MODES:
            raise ValueError(
                f"activation_mode must be one of {ACTIVATION_MODES}, got {self.activation_mode!r}"
            )
        if self.research_mode not in RESEARCH_MODES:
            raise ValueError(
                f"research_mode must be one of {RESEARCH_MODES}, got {self.research_mode!r}"
            )
        if self.search_contract not in SEARCH_CONTRACTS:
            raise ValueError(
                f"search_contract must be one of {SEARCH_CONTRACTS}, got {self.search_contract!r}"
            )
        if self.research_history_mode not in RESEARCH_HISTORY_MODES:
            raise ValueError(
                "research_history_mode must be one of "
                f"{RESEARCH_HISTORY_MODES}, got {self.research_history_mode!r}"
            )
        if self.con_reply_mode not in CON_REPLY_MODES:
            raise ValueError(
                f"con_reply_mode must be one of {CON_REPLY_MODES}, got {self.con_reply_mode!r}"
            )
        if self.tool_surface not in TOOL_SURFACES:
            raise ValueError(
                f"tool_surface must be one of {TOOL_SURFACES}, got {self.tool_surface!r}"
            )
        # The top-level effort fails closed exactly as an inference-profile entry does, rather
        # than reaching the provider as an unknown level (I-127, D-101).
        if self.reasoning_effort is not None and not is_reasoning_effort(self.reasoning_effort):
            raise ValueError(
                f"reasoning_effort must be one of {REASONING_EFFORTS}, "
                f"got {self.reasoning_effort!r}"
            )
        if self.prompt_version is None:
            versions = (
                REPLY_PLAYER_PROMPT_VERSIONS
                if self.con_reply_mode == CON_REPLY_MODE_VISIBLE
                else PLAYER_PROMPT_VERSIONS
            )
            object.__setattr__(self, "prompt_version", versions[self.activation_mode])


def prompt_hash_for(config: PlayerConfig) -> str:
    """SHA-256 of ``config``'s prompt template before Actor-name substitution.

    I-078: the same derivation serves the player's recorded provenance and a batch's expected
    condition, so what a game records and what the batch declared cannot compute differently.
    """
    template = load_player_prompt("{actor_name}", version=config.prompt_version)
    return hashlib.sha256(template.encode()).hexdigest()


def tool_schema_version_for(config: PlayerConfig) -> str:
    """The tool schema identity ``config`` runs under (research mode × Con-reply condition)."""
    batched = config.research_mode == RESEARCH_MODE_BATCHED_V1
    if config.con_reply_mode == CON_REPLY_MODE_VISIBLE:
        return BATCHED_REPLY_TOOL_SCHEMA_VERSION if batched else REPLY_TOOL_SCHEMA_VERSION
    return BATCHED_TOOL_SCHEMA_VERSION if batched else BASELINE_TOOL_SCHEMA_VERSION


def load_player_prompt(
    actor_name: str, *, path: Path | None = None, version: str | None = None
) -> str:
    """The packaged versioned prompt with the Actor name substituted (agents.md §17)."""
    text = (
        path.read_text(encoding="utf-8")
        if path is not None
        else read_prompt(version or PLAYER_PROMPT_VERSION)
    )
    return text.replace("{actor_name}", actor_name)
