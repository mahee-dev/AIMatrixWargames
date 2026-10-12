"""Scenario, settings and condition resolution, and the composition shared by ``run`` and
``benchmark``: one path from parsed flags to a composed Umpire, players and runtime (I-061).
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any
from pathlib import Path

import typer

from amaw_contracts import (
    REASONING_EFFORTS,
    ConsequenceRecipe,
    ResumeRecipe,
    RunRuntime,
    UmpireRecipe,
    is_reasoning_effort,
)

if TYPE_CHECKING:  # typing only: the agent stack is imported lazily inside the commands
    from agents.inference_profile import InferenceProfile
    from agents.player import PlayerConfig
    from app.config import Settings
    from app.game_loop import GameLoopCondition, GameLoopConditionLayer


@contextmanager
def _resolve_scenario(value: str):
    """Resolve a scenario package path or the name of a bundled scenario.

    Scenario packages are directories containing ``scenario.yaml``.  The API and runner still
    receive the directory path, so this helper is only a small CLI convenience rather than a
    scenario registry.
    """
    from amaw_resources import RuntimeResourceError, resolve_scenario

    try:
        with resolve_scenario(value) as scenario_path:
            yield scenario_path
    except RuntimeResourceError as exc:
        raise typer.BadParameter(str(exc)) from exc


def _cli_settings_with_admin_token():
    """Load settings and supply an in-process admin token for the local runner.

    The CLI composes the API and coordinator in one process, so a configured admin token is not
    required for this local-only composition.  If one is configured it is honoured; otherwise a
    fresh token is kept only in memory and passed to the in-process API.
    """
    from pydantic import SecretStr

    from app.config import Settings

    settings = Settings()
    configured = (
        settings.admin_token.get_secret_value() if settings.admin_token is not None else None
    )
    if configured:
        return settings, configured

    token = f"cli_{secrets.token_urlsafe(32)}"
    return settings.model_copy(update={"admin_token": SecretStr(token)}), token


def _game_loop_condition(
    settings: Settings,
    *,
    diplomacy_mode: str | None,
    diplomacy_rounds_per_turn: int | None,
    con_mode: str | None,
    con_challengers_per_argument: int | None,
    con_reply_mode: str | None = None,
    reply_max_points: int | None = None,
    consequence_mode: str | None = None,
    scenario: GameLoopConditionLayer | None = None,
) -> GameLoopCondition:
    """Resolve the Diplomacy (I-053), Con (I-055), Con-reply (I-046) and Consequence (D-091)
    conditions for a new game.

    The explicit value is the flag, else an ``AMAW_*`` setting the operator actually set; a
    Settings default is not explicit, so it never masks the scenario's declaration. The one
    resolver then falls back to the scenario's ``game.*`` defaults and the released defaults
    (D-099), and the pairing rules are the one ``app.game_loop`` validator (I-060). K against the
    Actor count is the server's check at game creation.
    """
    from app.game_loop import (
        GameLoopConditionLayer,
        InvalidGameLoopCondition,
        resolve_game_loop_condition,
    )

    def explicit(flag: Any, name: str) -> Any:
        if flag is not None:
            return flag
        return getattr(settings, name) if name in settings.model_fields_set else None

    condition = resolve_game_loop_condition(
        GameLoopConditionLayer(
            diplomacy_mode=explicit(diplomacy_mode, "diplomacy_mode"),
            diplomacy_rounds_per_turn=explicit(
                diplomacy_rounds_per_turn, "diplomacy_rounds_per_turn"
            ),
            con_mode=explicit(con_mode, "con_mode"),
            con_challengers_per_argument=explicit(
                con_challengers_per_argument, "con_challengers_per_argument"
            ),
            con_reply_mode=explicit(con_reply_mode, "con_reply_mode"),
            reply_max_points=explicit(reply_max_points, "reply_max_points"),
            consequence_mode=explicit(consequence_mode, "consequence_mode"),
        ),
        scenario or GameLoopConditionLayer(),
    )
    try:
        condition.validate()
    except InvalidGameLoopCondition as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    return condition


def _umpire_prompt_version(
    settings, *, explicit: str | None, con_reply_mode: str, scenario_declares_rules: bool = False
) -> str:
    """Resolve the Umpire prompt condition (agents.md §19/§21).

    Explicit flag, else ``AMAW_UMPIRE_PROMPT_VERSION``, else ``umpire-v1.7`` for every game, with
    or without rules and under either Con-reply treatment (D-114). A pin is still checked: a
    ``visible_reply`` game under v1.3 would adjudicate blind to the reply, and a rules scenario
    under v1.3/v1.4 without the rules framing, so both are refused.
    """
    from agents.umpire import (
        UMPIRE_PROMPT_VERSION,
        UMPIRE_PROMPT_VERSION_DEFAULT,
        UMPIRE_PROMPT_VERSION_REPLY,
        UMPIRE_PROMPT_VERSIONS,
        RULES_AWARE_UMPIRE_PROMPT_VERSIONS,
    )

    version = explicit or settings.umpire_prompt_version or UMPIRE_PROMPT_VERSION_DEFAULT
    if version not in UMPIRE_PROMPT_VERSIONS:
        typer.echo(
            "error: --umpire-prompt-version must be one of " + ", ".join(UMPIRE_PROMPT_VERSIONS),
            err=True,
        )
        raise typer.Exit(code=2)
    if con_reply_mode == "visible_reply" and version == UMPIRE_PROMPT_VERSION:
        typer.echo(
            f"error: visible_reply requires a reply-aware Umpire prompt "
            f"({UMPIRE_PROMPT_VERSION_REPLY} or a rules-aware one); "
            f"{version} cannot weigh an owner reply",
            err=True,
        )
        raise typer.Exit(code=2)
    if scenario_declares_rules and version not in RULES_AWARE_UMPIRE_PROMPT_VERSIONS:
        typer.echo(
            f"error: the scenario declares rules, which require a rules-aware Umpire prompt "
            f"({', '.join(sorted(RULES_AWARE_UMPIRE_PROMPT_VERSIONS))}); "
            f"{version} has no scenario_rules framing",
            err=True,
        )
        raise typer.Exit(code=2)
    return version


def _scenario_declares_rules(scenario_path) -> bool:  # noqa: ANN001
    """Whether the scenario package declares ``rules`` (D-090), read with the one trusted loader.

    An unloadable package reports ``False`` here: game creation rejects it with the real
    validation error, so the prompt resolution never masks it.
    """
    from app.scenarios import ScenarioError, load_scenario

    try:
        return bool(load_scenario(scenario_path).rules)
    except ScenarioError:
        return False


def _scenario_condition_defaults(scenario_path) -> GameLoopConditionLayer:  # noqa: ANN001
    """The scenario's declared ``game.*`` condition defaults (D-091, D-099), read with the
    trusted loader.

    An unloadable package declares nothing here; game creation rejects it with the real error.
    """
    from app.game_loop import GameLoopConditionLayer
    from app.scenarios import ScenarioError, load_scenario

    try:
        return load_scenario(scenario_path).game.condition_defaults()
    except ScenarioError:
        return GameLoopConditionLayer()


def _consequence_recipe(
    settings: Settings, *, mode: str, model: str | None, umpire_model: str
) -> ConsequenceRecipe | None:
    """The consequence-resolver section of the resume recipe, or ``None`` under ``none``.

    Under ``bounded_effects_v1`` an unset resolver model defaults to the Umpire model, so a
    scenario that declares the mode runs without extra flags; the resolved model is sealed
    either way (D-115).
    A resolver model under ``none`` is refused, as a condition that would not run (D-091).
    """
    from agents.consequence import CONSEQUENCE_PROMPT_VERSION

    model = model or settings.consequence_model
    if mode == "none":
        if model is not None:
            typer.echo(
                "error: --consequence-model/AMAW_CONSEQUENCE_MODEL requires "
                "consequence_mode bounded_effects_v1",
                err=True,
            )
            raise typer.Exit(code=2)
        return None
    return {
        "model": model or umpire_model,
        "temperature": None,
        "max_tokens": None,
        "timeout": settings.request_timeout_seconds,
        "reasoning_effort": None,
        "prompt_version": CONSEQUENCE_PROMPT_VERSION,
    }


def _compose_consequence_resolver(settings: Settings, recipe: ConsequenceRecipe | None) -> Any:
    """The game's one consequence resolver from its sealed recipe section, or ``None``."""
    if recipe is None:
        return None
    from agents.consequence import AIConsequenceResolver

    return AIConsequenceResolver.for_openrouter(
        settings,
        model=recipe["model"],
        prompt_version=recipe["prompt_version"],
        temperature=recipe["temperature"],
        max_tokens=recipe["max_tokens"],
        timeout=recipe["timeout"],
        reasoning_effort=recipe["reasoning_effort"],
    )


def _player_config(
    settings,
    *,
    player_model: str,
    player_temperature: float | None,
    player_max_tokens: int | None,
    player_reasoning_effort: str | None,
    profile,
    activation_mode: str | None,
    research_mode: str | None,
    search_contract: str | None,
    research_history_mode: str | None,
    tool_surface: str | None,
    max_messages_per_activation: int | None,
    max_llm_iterations: int | None,
    max_tool_calls: int | None,
    max_research_tool_calls: int | None,
    max_research_operations: int | None,
    max_messages_per_phase: int | None,
    max_activations_per_opportunity: int | None,
    con_reply_mode: str,
):
    """Build the one ``PlayerConfig`` every Actor of a *fresh* game shares (only the model differs
    per Seat). A resumed game parses its sealed recipe instead (``PlayerConfig.from_resume_recipe``)."""
    from agents.guardrails import GuardrailConfig
    from agents.player import PlayerConfig

    try:
        return PlayerConfig(
            model=player_model,
            temperature=player_temperature,
            max_tokens=player_max_tokens,
            timeout=settings.request_timeout_seconds,
            reasoning_effort=player_reasoning_effort,
            inference_profile=profile,
            activation_mode=activation_mode or settings.player_activation_mode,
            research_mode=research_mode or settings.player_research_mode,
            search_contract=search_contract or settings.player_search_contract,
            research_history_mode=(
                research_history_mode or settings.player_research_history_mode
            ),
            tool_surface=tool_surface or settings.player_tool_surface,
            con_reply_mode=con_reply_mode,
            guardrails=GuardrailConfig(
                max_messages_per_activation=(
                    settings.max_messages_per_activation
                    if max_messages_per_activation is None
                    else max_messages_per_activation
                ),
                max_llm_iterations=(
                    settings.max_llm_iterations
                    if max_llm_iterations is None
                    else max_llm_iterations
                ),
                max_tool_calls=(
                    settings.max_tool_calls if max_tool_calls is None else max_tool_calls
                ),
                max_research_tool_calls=(
                    settings.max_research_tool_calls
                    if max_research_tool_calls is None
                    else max_research_tool_calls
                ),
                max_research_operations=(
                    settings.max_research_operations
                    if max_research_operations is None
                    else max_research_operations
                ),
                max_messages_per_phase=(
                    settings.max_messages_per_phase
                    if max_messages_per_phase is None
                    else max_messages_per_phase
                ),
                max_activations_per_opportunity=(
                    settings.max_activations_per_opportunity
                    if max_activations_per_opportunity is None
                    else max_activations_per_opportunity
                ),
            ),
        )
    except ValueError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


# --- one composition path for `run` and `benchmark` (I-061, review F1) -----------------------
#
# Both commands read: parse → validate the plan → settings → agent stack → resolve the condition
# → compose (Umpire, players, app) → drive → write outputs. The helpers below are the shared
# steps; each command keeps only what genuinely differs (resume vs. schedule, one game vs. a
# batch, its own output).


def _check_run_class(run_class: str | None, analysis_plan: Path | None) -> None:
    """I-047: classify the run before anything is created. Ordinary runs stay playtests."""
    if run_class is not None and run_class not in {"playtest", "analytical"}:
        typer.echo("error: --run-class must be playtest or analytical", err=True)
        raise typer.Exit(code=2)
    if run_class == "analytical" and analysis_plan is None:
        typer.echo("error: --run-class analytical requires --analysis-plan", err=True)
        raise typer.Exit(code=2)
    if run_class == "playtest" and analysis_plan is not None:
        typer.echo("error: --analysis-plan makes a run analytical; drop --run-class playtest", err=True)
        raise typer.Exit(code=2)


def _load_analysis_plan(analysis_plan: Path | None):
    """I-047: an unusable plan costs no Game, no database and no provider call."""
    if analysis_plan is None:
        return None
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan

    try:
        return load_analysis_plan(analysis_plan)
    except AnalysisPlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _autonomous_settings(
    what: str,
    *,
    request_timeout_seconds: float | None,
    log_level: str | None,
    log_file: Path | None,
    max_llm_iterations: int | None,
    max_tool_calls: int | None,
    max_research_tool_calls: int | None,
    max_research_operations: int | None,
    max_messages_per_phase: int | None,
    max_activations_per_opportunity: int | None,
):
    """Settings with the flag overlay applied, plus the in-process admin token; the provider key
    is required before any further step."""
    settings, admin_token = _cli_settings_with_admin_token()
    if settings.openrouter_api_key is None or not settings.openrouter_api_key.get_secret_value():
        typer.echo(f"error: {what} require OPENROUTER_API_KEY to be configured", err=True)
        raise typer.Exit(code=2)
    settings = settings.model_copy(
        update={
            key: value
            for key, value in {
                "request_timeout_seconds": request_timeout_seconds,
                "log_level": log_level,
                "log_file": log_file,
                "max_llm_iterations": max_llm_iterations,
                "max_tool_calls": max_tool_calls,
                "max_research_tool_calls": max_research_tool_calls,
                "max_research_operations": max_research_operations,
                "max_messages_per_phase": max_messages_per_phase,
                "max_activations_per_opportunity": max_activations_per_opportunity,
            }.items()
            if value is not None
        }
    )
    return settings, admin_token


def _require_agent_stack(what: str) -> None:
    """Agent imports are deliberately deferred to the autonomous commands: ``amaw export`` and
    plain ``import amaw_cli`` must remain usable without the optional agent framework (D-025)."""
    try:
        import fastapi.testclient  # noqa: F401
        import agents.autonomous  # noqa: F401
        import agents.lifecycle  # noqa: F401
        import agents.player  # noqa: F401
        import agents.umpire  # noqa: F401
        import app.api  # noqa: F401
    except ImportError as exc:  # pragma: no cover - depends on installation extras
        typer.echo(
            f"error: {what} require the optional agent dependencies; "
            "install them with `uv sync --extra agents`",
            err=True,
        )
        raise typer.Exit(code=2) from exc


def _inference_profile(path: Path | None):
    """I-032: validate the declared profile before any Game, database or provider call exists."""
    from agents.inference_profile import (
        INFERENCE_PROFILE_UNIFORM,
        InferenceProfileError,
        load_inference_profile,
    )

    if path is None:
        return INFERENCE_PROFILE_UNIFORM
    try:
        return load_inference_profile(path)
    except InferenceProfileError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc


def _umpire_recipe(
    settings: Settings,
    *,
    model: str,
    temperature: float | None,
    max_tokens: int | None,
    reasoning_effort: str | None,
    prompt_version: str,
) -> UmpireRecipe:
    """The Umpire section of the resume recipe: the flat flag values, kept verbatim, from which
    the Umpire is composed (profile resolution happens at composition, not here).

    The flag must be one of OpenRouter's effort levels even when a profile's Umpire entry
    overrides it, because the recipe keeps it verbatim; an unknown level costs no Game and no
    provider call (I-129, D-101).
    """
    if reasoning_effort is not None and not is_reasoning_effort(reasoning_effort):
        typer.echo(
            "error: --umpire-reasoning-effort: reasoning_effort must be one of "
            f"{REASONING_EFFORTS}, got {reasoning_effort!r}",
            err=True,
        )
        raise typer.Exit(code=2)
    return {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "timeout": settings.request_timeout_seconds,
        "reasoning_effort": reasoning_effort,
        "prompt_version": prompt_version,
    }


def _compose_umpire(
    settings: Settings, profile: InferenceProfile, umpire_recipe: UmpireRecipe
) -> Any:
    """The one fixed Umpire of a game or batch from its recipe section.

    I-032: the Umpire runs in one phase, so its profile entry resolves once, here, over the flat
    values the recipe keeps verbatim.
    """
    from agents.umpire import AIUmpire

    inference = profile.resolve_umpire(
        reasoning_effort=umpire_recipe["reasoning_effort"],
        max_tokens=umpire_recipe["max_tokens"],
    )
    return AIUmpire.for_openrouter(
        settings,
        model=umpire_recipe["model"],
        prompt_version=umpire_recipe["prompt_version"],
        temperature=umpire_recipe["temperature"],
        max_tokens=inference["max_tokens"],
        timeout=settings.request_timeout_seconds,
        reasoning_effort=inference["reasoning_effort"],
    )


def _player_runner_factory(settings, player_config):
    """``(tools, actor_id, actor_name, model) -> LifecycleRunner`` over the shared config; only
    the model differs per Seat."""
    from agents.lifecycle import LifecycleRunner
    from agents.player import ReferencePlayer

    def make(tools, actor_id: str, actor_name: str, model: str) -> LifecycleRunner:
        actor_config = dataclasses.replace(player_config, model=model)
        player = ReferencePlayer.for_openrouter(
            tools, actor_id=actor_id, actor_name=actor_name, config=actor_config, settings=settings,
        )
        return LifecycleRunner(player)

    return make


def _run_runtime(
    settings: Settings, *, max_cycles: int, stream_game_details: bool, player_config: PlayerConfig
) -> RunRuntime:
    """The manifest's ``runtime`` section: how this process ran, not the experimental condition."""
    return {
        "request_timeout_seconds": settings.request_timeout_seconds,
        "max_cycles": max_cycles,
        "log_level": settings.log_level.upper(),
        "stream_game_details": stream_game_details,
        "guardrails": dataclasses.asdict(player_config.guardrails),
    }


@dataclasses.dataclass(frozen=True)
class ConditionFlags:
    """The condition-bearing flags ``run`` and ``benchmark`` share, exactly as Typer parsed them.

    ``None`` means the flag was not given: resolution then falls back to the ``AMAW_*`` setting,
    the scenario's declaration where one exists, and the released default, in that order. The
    player model and the inference-profile path are resolved by each command itself, because
    the two commands take them differently.
    """

    umpire_model: str | None
    max_cycles: int | None
    cons_concurrency: int | None
    diplomacy_concurrency: int | None
    diplomacy_mode: str | None
    diplomacy_rounds_per_turn: int | None
    con_mode: str | None
    con_challengers_per_argument: int | None
    con_reply_mode: str | None
    reply_max_points: int | None
    consequence_mode: str | None
    consequence_model: str | None
    umpire_prompt_version: str | None
    activation_mode: str | None
    research_mode: str | None
    search_contract: str | None
    research_history_mode: str | None
    tool_surface: str | None
    max_messages_per_activation: int | None
    max_llm_iterations: int | None
    max_tool_calls: int | None
    max_research_tool_calls: int | None
    max_research_operations: int | None
    max_messages_per_phase: int | None
    max_activations_per_opportunity: int | None
    player_temperature: float | None
    player_max_tokens: int | None
    player_reasoning_effort: str | None
    umpire_temperature: float | None
    umpire_max_tokens: int | None
    umpire_reasoning_effort: str | None

    @classmethod
    def from_command(cls, parameters: Mapping[str, Any]) -> ConditionFlags:
        """Pick this dataclass's fields from a command's parameters (its ``locals()`` on entry)."""
        return cls(**{field.name: parameters[field.name] for field in dataclasses.fields(cls)})


@dataclasses.dataclass(frozen=True)
class ResolvedCondition:
    """The experimental condition one invocation runs, resolved before anything executes.

    Built once per command, either from the flags for a new game or from the sealed recipe on
    resume, and then only read. ``resume_recipe`` is what the manifest seals; the other fields are
    what execution composes the Umpire, resolver, players and runner from.
    """

    profile: InferenceProfile
    player_config: PlayerConfig
    game_loop: GameLoopCondition
    umpire_recipe: UmpireRecipe
    consequence_recipe: ConsequenceRecipe | None
    resume_recipe: ResumeRecipe
    max_cycles: int
    cons_concurrency: int
    diplomacy_concurrency: int


def _resolve_new_condition(
    settings: Settings,
    flags: ConditionFlags,
    *,
    profile: InferenceProfile,
    player_model: str,
    scenario_condition: GameLoopConditionLayer,
    scenario_declares_rules: bool,
) -> ResolvedCondition:
    """The one resolution of a new game's condition, shared by ``run`` and ``benchmark``.

    The order is part of the contract: when several flags are invalid, the first refusal below
    is the one reported (the game loop, then the resolver, then the Umpire prompt, then the
    player configuration). Every refusal exits with code 2 before any Game exists.
    """
    from agents.autonomous import build_resume_recipe

    max_cycles = flags.max_cycles or 1000
    cons_concurrency = flags.cons_concurrency or settings.cons_concurrency
    diplomacy_concurrency = flags.diplomacy_concurrency or settings.diplomacy_concurrency
    game_loop = _game_loop_condition(
        settings,
        diplomacy_mode=flags.diplomacy_mode,
        diplomacy_rounds_per_turn=flags.diplomacy_rounds_per_turn,
        con_mode=flags.con_mode,
        con_challengers_per_argument=flags.con_challengers_per_argument,
        con_reply_mode=flags.con_reply_mode,
        reply_max_points=flags.reply_max_points,
        consequence_mode=flags.consequence_mode,
        scenario=scenario_condition,
    )
    umpire_model = flags.umpire_model or settings.umpire_model
    consequence_recipe = _consequence_recipe(
        settings, mode=game_loop.consequence_mode, model=flags.consequence_model,
        umpire_model=umpire_model,
    )
    umpire_recipe = _umpire_recipe(
        settings,
        model=umpire_model,
        temperature=flags.umpire_temperature,
        max_tokens=flags.umpire_max_tokens,
        reasoning_effort=flags.umpire_reasoning_effort,
        prompt_version=_umpire_prompt_version(
            settings, explicit=flags.umpire_prompt_version,
            con_reply_mode=game_loop.con_reply_mode,
            scenario_declares_rules=scenario_declares_rules,
        ),
    )
    player_config = _player_config(
        settings,
        player_model=player_model,
        player_temperature=flags.player_temperature,
        player_max_tokens=flags.player_max_tokens,
        player_reasoning_effort=flags.player_reasoning_effort,
        profile=profile,
        activation_mode=flags.activation_mode,
        research_mode=flags.research_mode,
        search_contract=flags.search_contract,
        research_history_mode=flags.research_history_mode,
        tool_surface=flags.tool_surface,
        max_messages_per_activation=flags.max_messages_per_activation,
        max_llm_iterations=flags.max_llm_iterations,
        max_tool_calls=flags.max_tool_calls,
        max_research_tool_calls=flags.max_research_tool_calls,
        max_research_operations=flags.max_research_operations,
        max_messages_per_phase=flags.max_messages_per_phase,
        max_activations_per_opportunity=flags.max_activations_per_opportunity,
        con_reply_mode=game_loop.con_reply_mode,
    )
    # Assignments are filled once Actor ids are known inside the coordinator; homogeneous runs
    # and batches use the empty sentinel and the manifest builder normalizes it.
    resume_recipe = build_resume_recipe(
        player_config,
        assignments={},
        umpire=umpire_recipe,
        max_cycles=max_cycles,
        cons_concurrency=cons_concurrency,
        diplomacy_concurrency=diplomacy_concurrency,
        condition=game_loop,
        consequence=consequence_recipe,
    )
    return ResolvedCondition(
        profile=profile,
        player_config=player_config,
        game_loop=game_loop,
        umpire_recipe=umpire_recipe,
        consequence_recipe=consequence_recipe,
        resume_recipe=resume_recipe,
        max_cycles=max_cycles,
        cons_concurrency=cons_concurrency,
        diplomacy_concurrency=diplomacy_concurrency,
    )


def _resolve_sealed_condition(
    resume_recipe: ResumeRecipe, player_config: PlayerConfig
) -> ResolvedCondition:
    """The condition a resumed game ran under, read back from its sealed recipe (I-048).

    ``player_config`` is the recipe's own inverse (``PlayerConfig.from_resume_recipe``), parsed
    by the caller so an unparseable recipe is reported as "not resumable".
    """
    from app.game_loop import GameLoopCondition

    return ResolvedCondition(
        profile=player_config.inference_profile,
        player_config=player_config,
        # I-046: a pre-condition recipe carries no Umpire prompt version; it ran released v1.3.
        umpire_recipe={"prompt_version": "umpire-v1.3", **resume_recipe["umpire"]},
        # T-074 / D-091: present only for a bounded_effects_v1 game; the server has already
        # checked it against the persisted condition (``resumption.describe``).
        consequence_recipe=resume_recipe.get("consequence"),
        resume_recipe=resume_recipe,
        max_cycles=resume_recipe["coordinator"]["max_cycles"],
        cons_concurrency=resume_recipe["coordinator"]["cons_concurrency"],
        diplomacy_concurrency=resume_recipe["coordinator"]["diplomacy_concurrency"],
        # A pre-condition recipe carries none of the condition keys and means the released
        # defaults; ``resumption.describe`` has already validated the pairing (I-060).
        game_loop=GameLoopCondition.from_mapping(resume_recipe["coordinator"]),
    )
