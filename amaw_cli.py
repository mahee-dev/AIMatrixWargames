"""The ``amaw`` command-line interface: ``export``, ``run``, ``benchmark``, ``analysis-plan``.

It lives **outside** ``app/`` so the agent stack can never leak into the engine package
(AGENTS.md §6 / TRD §4): ``run`` and ``benchmark`` import ``agents``/``pydantic_ai`` lazily
inside the command, and a plain ``import amaw_cli`` stays framework-free (the import-boundary
test asserts this). ``export`` shares the *one* export builder with
``GET /admin/games/{id}/export`` (:func:`app.export.build_game_export`) and is read-only
against the configured ``AMAW_DATABASE_PATH``. ``run`` and ``benchmark`` are one composition
path (I-061): parse → resolve the game-loop condition and inference profile → compose players,
Umpire and app → drive → write; ``benchmark`` adds rotation, repetition and the batch outputs
(:mod:`benchmark`). The resume recipe has one builder and the CLI one parser.
"""

from __future__ import annotations

import dataclasses
from contextlib import contextmanager
from datetime import UTC, datetime
import json
import logging
import secrets
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, cast
import uuid
from pathlib import Path

import typer

from amaw_contracts import (
    REASONING_EFFORTS,
    ConsequenceRecipe,
    ResumeRecipe,
    RunConfiguration,
    RunRuntime,
    UmpireRecipe,
    is_reasoning_effort,
)

if TYPE_CHECKING:  # typing only: the agent stack is imported lazily inside the commands
    from agents.inference_profile import InferenceProfile
    from agents.player import PlayerConfig
    from app.config import Settings
    from app.game_loop import GameLoopCondition, GameLoopConditionLayer

app = typer.Typer(add_completion=False, help="AMAW administrative CLI.")


@app.callback()
def _main() -> None:
    """AMAW administrative CLI.

    A callback (even an empty one) keeps this a multi-command group, so ``export`` stays a named
    subcommand — ``amaw export GAME_ID`` — rather than Typer collapsing the sole command into a
    nameless top-level one. Room for a later ``benchmark`` command (T-054) without a rename.
    """


def _build_export(game_id: str) -> dict:
    """The administrative export of GAME_ID from the configured database (api.md §15.1).

    Exits 2 for a malformed id and 1 for an unknown game.
    """
    # Imported lazily so `import amaw_cli` is cheap and stays framework-free.
    from app.config import Settings
    from app.database import engine_for_path
    from app.export import build_game_export
    from app.game_service import GameNotFound

    try:
        parsed = uuid.UUID(game_id)
    except ValueError as exc:
        typer.echo(f"error: {game_id!r} is not a valid game id", err=True)
        raise typer.Exit(code=2) from exc

    engine = engine_for_path(Settings().database_path)
    try:
        return build_game_export(engine, parsed)
    except GameNotFound:
        typer.echo(f"error: no game {game_id}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        engine.dispose()


@app.command()
def export(game_id: str) -> None:
    """Print the complete administrative JSON export of GAME_ID to stdout (api.md §15.1)."""
    record = _build_export(game_id)

    # sort_keys keeps the printed record stable regardless of dict insertion order.
    typer.echo(json.dumps(record, indent=2, sort_keys=True))


@app.command()
def report(
    source: str = typer.Argument(..., help="A game id, or the path of an export JSON file."),
    out: Path | None = typer.Option(
        None, "--out", help="Output directory. Default: <export dir>/<game id>-aar/."
    ),
) -> None:
    """Write an after-action report (report.md) for a game, from its export alone (TRD §34).

    A game id is exported from the configured database first, beside it in ``exports/``, so the
    report can always be rebuilt from the file it was made from.
    """
    from aar.markdown import render_markdown
    from aar.model import build_report
    from app.config import Settings
    from app.export import write_json_atomic

    path = Path(source)
    if path.suffix == ".json" or path.exists():
        if not path.is_file():
            typer.echo(f"error: no export file at {path}", err=True)
            raise typer.Exit(code=2)
        record = json.loads(path.read_text(encoding="utf-8"))
    else:
        record = _build_export(source)
        path = _default_export_path(Settings(), str(record["game"]["id"]))
        write_json_atomic(path, record)
        typer.echo(f"wrote {path}")

    target = out or path.parent / f"{record['game']['id']}-aar"
    target.mkdir(parents=True, exist_ok=True)
    report_path = target / "report.md"
    report_path.write_text(render_markdown(build_report(record)), encoding="utf-8")
    typer.echo(f"wrote {report_path} (includes private material)")


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


analysis_plan_app = typer.Typer(
    help="Validate a predeclared analysis plan or verify which games sealed it (I-047).",
    no_args_is_help=True,
)
app.add_typer(analysis_plan_app, name="analysis-plan")


@analysis_plan_app.command("validate")
def analysis_plan_validate(plan: Path) -> None:
    """Validate an analysis-plan-v1 or -v2 file and print its identity hash."""
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan

    try:
        loaded = load_analysis_plan(plan)
    except AnalysisPlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"{loaded.plan_id} {loaded.sha256}")


@analysis_plan_app.command("verify")
def analysis_plan_verify(plan: Path, exports: list[Path]) -> None:
    """Classify each game export as predeclared / post_hoc_plan / unplanned against the plan.

    Exit status 0 only when every export sealed exactly this plan before execution.
    """
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan, verify_registration

    try:
        loaded = load_analysis_plan(plan)
    except AnalysisPlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    documents = []
    for path in exports:
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            typer.echo(f"error: export {path} could not be read: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    report = verify_registration(loaded, documents)
    typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    raise typer.Exit(code=0 if report.all_predeclared else 1)


# --- Experimental-condition options shared by `run` and `benchmark` -------------------------
# One declaration per flag, so the two commands cannot drift apart (T-054).

PlayerModelOpt = Annotated[str | None, typer.Option(
    "--player-model",
    help="OpenRouter model for every autonomous Actor (default: AMAW_PLAYER_MODEL).",
)]
UmpireModelOpt = Annotated[str | None, typer.Option(
    "--umpire-model", help="OpenRouter model for the Umpire (default: AMAW_UMPIRE_MODEL)."
)]
MaxCyclesOpt = Annotated[int | None, typer.Option(
    "--max-cycles", min=1, help="Coordinator safety ceiling (default: 1000)."
)]
RequestTimeoutOpt = Annotated[float | None, typer.Option(
    "--request-timeout-seconds", min=1.0,
    help="Provider request timeout in seconds (default: AMAW_REQUEST_TIMEOUT_SECONDS).",
)]
LogLevelOpt = Annotated[str | None, typer.Option(
    "--log-level", help="Log level for this run (default: AMAW_LOG_LEVEL)."
)]
LogFileOpt = Annotated[Path | None, typer.Option(
    "--log-file", help="Write timestamped, redacted operational logs to this file."
)]
StreamGameDetailsOpt = Annotated[bool | None, typer.Option(
    "--stream-game-details/--no-stream-game-details",
    help="Log newly persisted game, event, resource-access and provenance records.",
)]
ConsConcurrencyOpt = Annotated[int | None, typer.Option(
    "--cons-concurrency", min=1,
    help="Max parallel Con activations per Argument; 1 = serial (default: AMAW_CONS_CONCURRENCY).",
)]
DiplomacyConcurrencyOpt = Annotated[int | None, typer.Option(
    "--diplomacy-concurrency", min=1,
    help=(
        "Max parallel diplomacy activations per round; 1 = serial "
        "(default: AMAW_DIPLOMACY_CONCURRENCY)."
    ),
)]
DiplomacyModeOpt = Annotated[str | None, typer.Option(
    "--diplomacy-mode",
    help=(
        "Game-loop condition: open_diplomacy (released default) or bounded_rounds. Default: "
        "AMAW_DIPLOMACY_MODE, else the scenario's game.diplomacy_mode (D-099)."
    ),
)]
DiplomacyRoundsOpt = Annotated[int | None, typer.Option(
    "--diplomacy-rounds-per-turn", min=1,
    help=(
        "Positive completed-round limit required by --diplomacy-mode bounded_rounds. Default: "
        "AMAW_DIPLOMACY_ROUNDS_PER_TURN, else the scenario's bound when its mode is resolved."
    ),
)]
ConModeOpt = Annotated[str | None, typer.Option(
    "--con-mode",
    help=(
        "Con-opposition condition: open_cons (released default, every opponent may Con) or "
        "sampled_sealed (the server seals K challengers per Argument). Default: AMAW_CON_MODE, "
        "else the scenario's game.con_mode (D-099)."
    ),
)]
ConChallengersOpt = Annotated[int | None, typer.Option(
    "--con-challengers-per-argument", min=1,
    help=(
        "Positive challenger count required by --con-mode sampled_sealed. Default: "
        "AMAW_CON_CHALLENGERS_PER_ARGUMENT, else the scenario's count when its mode is resolved."
    ),
)]
ConsequenceModeOpt = Annotated[str | None, typer.Option(
    "--consequence-mode",
    help=(
        "Consequence condition (D-091): none (released default) or bounded_effects_v1 (one "
        "bounded, evidence-linked end-of-turn consequence pass). Default: AMAW_CONSEQUENCE_MODE, "
        "else the scenario's game.consequence_mode."
    ),
)]
ConsequenceModelOpt = Annotated[str | None, typer.Option(
    "--consequence-model",
    help=(
        "OpenRouter model for the consequence resolver (default: AMAW_CONSEQUENCE_MODEL, "
        "else the Umpire model)."
    ),
)]
ConReplyModeOpt = Annotated[str | None, typer.Option(
    "--con-reply-mode",
    help=(
        "Con-reply condition: sealed_cons (released default, Cons reach only the Umpire) or "
        "visible_reply (Cons are revealed to every Actor and the owner replies once; "
        "default: AMAW_CON_REPLY_MODE)."
    ),
)]
ReplyMaxPointsOpt = Annotated[int | None, typer.Option(
    "--reply-max-points", min=1, max=3,
    help="Reply bound (1–3) required by --con-reply-mode visible_reply.",
)]
UmpirePromptVersionOpt = Annotated[str | None, typer.Option(
    "--umpire-prompt-version",
    help=(
        "Umpire prompt: umpire-v1.3 (released), umpire-v1.4 (reply-aware), umpire-v1.5 "
        "(rules-aware), umpire-v1.6 (rules-aware, Effect discipline) or umpire-v1.7 (v1.6 plus a "
        "branch audit). Default: AMAW_UMPIRE_PROMPT_VERSION, else umpire-v1.7 for every game. "
        "visible_reply refuses v1.3; a rules scenario refuses v1.3/v1.4; pin one version across "
        "a comparison."
    ),
)]
ActivationModeOpt = Annotated[str | None, typer.Option(
    "--activation-mode",
    help=(
        "Player harness: single_shot (one structured decision per activation) or tool_loop "
        "(one mutating tool per model call). Default: AMAW_PLAYER_ACTIVATION_MODE."
    ),
)]
ResearchModeOpt = Annotated[str | None, typer.Option(
    "--research-mode",
    help=(
        "Research tools: sequential-v1 (released surface) or batched-v1 (adds bounded "
        "batch search/read). Default: AMAW_PLAYER_RESEARCH_MODE."
    ),
)]
SearchContractOpt = Annotated[str | None, typer.Option(
    "--search-contract",
    help=(
        "Resource ranking: search-v2 (released token coverage) or search-v1 (original "
        "whole-query substring, for reproducing earlier runs). "
        "Default: AMAW_PLAYER_SEARCH_CONTRACT."
    ),
)]
ResearchHistoryModeOpt = Annotated[str | None, typer.Option(
    "--research-history-mode",
    help=(
        "Intra-activation research history: full-v1 or compact-success-v1. "
        "Default: AMAW_PLAYER_RESEARCH_HISTORY_MODE."
    ),
)]
ToolSurfaceOpt = Annotated[str | None, typer.Option(
    "--tool-surface",
    help=(
        "tool_loop only (I-092): broad (the whole mutating vocabulary; attempts outside "
        "available_actions stay measurable) or available_only (only the currently listed "
        "mutating tools). Sealed as given under single_shot. Default: AMAW_PLAYER_TOOL_SURFACE."
    ),
)]
MaxMessagesPerActivationOpt = Annotated[int | None, typer.Option(
    "--max-messages-per-activation", min=0,
    help=(
        "Outgoing Messages allowed per Actor activation; 0 disables sends "
        "(default: AMAW_MAX_MESSAGES_PER_ACTIVATION)."
    ),
)]
MaxLlmIterationsOpt = Annotated[int | None, typer.Option(
    "--max-llm-iterations", min=1,
    help="Per-activation model iteration ceiling (default: AMAW_MAX_LLM_ITERATIONS).",
)]
MaxToolCallsOpt = Annotated[int | None, typer.Option(
    "--max-tool-calls", min=1,
    help="Per-activation tool-call ceiling (default: AMAW_MAX_TOOL_CALLS).",
)]
MaxResearchToolCallsOpt = Annotated[int | None, typer.Option(
    "--max-research-tool-calls", min=0,
    help=(
        "Optional research calls per activation; 0 disables research tools "
        "(default: AMAW_MAX_RESEARCH_TOOL_CALLS)."
    ),
)]
MaxResearchOperationsOpt = Annotated[int | None, typer.Option(
    "--max-research-operations", min=0,
    help=(
        "Resource search/read operations per activation, however many research calls carry "
        "them; 0 disables Resource research (default: AMAW_MAX_RESEARCH_OPERATIONS)."
    ),
)]
MaxMessagesPerPhaseOpt = Annotated[int | None, typer.Option(
    "--max-messages-per-phase", min=0,
    help="Per-Actor phase Message ceiling (default: AMAW_MAX_MESSAGES_PER_PHASE).",
)]
MaxActivationsPerOpportunityOpt = Annotated[int | None, typer.Option(
    "--max-activations-per-opportunity", min=1,
    help=(
        "No-progress recovery ceiling for one Actor opportunity "
        "(default: AMAW_MAX_ACTIVATIONS_PER_OPPORTUNITY)."
    ),
)]
PlayerTemperatureOpt = Annotated[float | None, typer.Option(
    "--player-temperature", help="Optional temperature for Player model requests."
)]
PlayerMaxTokensOpt = Annotated[int | None, typer.Option(
    "--player-max-tokens", min=1, help="Optional Player completion-token budget."
)]
PlayerReasoningEffortOpt = Annotated[str | None, typer.Option(
    "--player-reasoning-effort", help="Optional Player reasoning effort."
)]
UmpireTemperatureOpt = Annotated[float | None, typer.Option(
    "--umpire-temperature", help="Optional temperature for Umpire requests."
)]
UmpireMaxTokensOpt = Annotated[int | None, typer.Option(
    "--umpire-max-tokens", min=1, help="Optional Umpire completion-token budget."
)]
UmpireReasoningEffortOpt = Annotated[str | None, typer.Option(
    "--umpire-reasoning-effort", help="Optional Umpire reasoning effort."
)]
InferenceProfileOpt = Annotated[Path | None, typer.Option(
    "--inference-profile",
    help=(
        "Declared inference-profile-v1 YAML/JSON: per-phase player reasoning effort / "
        "max_tokens and an Umpire entry, resolved over the flat --player-*/--umpire-* "
        "values and sealed into the manifest (I-032). Default: AMAW_INFERENCE_PROFILE."
    ),
)]
AnalysisPlanOpt = Annotated[Path | None, typer.Option(
    "--analysis-plan",
    help=(
        "Predeclared analysis-plan-v1 or -v2 YAML/JSON; validated and sealed into the immutable "
        "run manifest before execution, making this an analytical run (I-047)."
    ),
)]
RunClassOpt = Annotated[str | None, typer.Option(
    "--run-class", help="playtest (default) or analytical; analytical requires --analysis-plan."
)]
PlanLevelOpt = Annotated[str | None, typer.Option(
    "--plan-level",
    help=(
        "The treatment level this batch runs, for an analysis-plan-v2 plan with design scope "
        "cell; one of its treatment levels, recorded in batch.json (D-109)."
    ),
)]


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


# The files SQLite owns beside the database (rollback journal, WAL and its shared-memory index),
# spelled the way SQLite spells them: appended to the full database filename.
_SQLITE_SIBLING_SUFFIXES = ("-journal", "-wal", "-shm")


def _refuse_database_export_path(export_path: Path, database_path: Path) -> None:
    """I-097: ``--export-path`` must never name the database file or a file SQLite owns.

    Checked before the engine exists — the export would otherwise replace the database with JSON
    on success, or clobber it under a live engine on the best-effort failure path.
    """
    database = database_path.resolve()
    owned = {database, *(database.with_name(database.name + s) for s in _SQLITE_SIBLING_SUFFIXES)}
    if export_path.resolve() in owned:
        typer.echo(
            f"error: --export-path {export_path} is the SQLite database ({database_path}) or a "
            "file it owns; the export would overwrite it — choose another path",
            err=True,
        )
        raise typer.Exit(code=2)


def _default_export_path(settings: Settings, game_id: str) -> Path:
    """Where a run without ``--export-path`` leaves its export: beside the database (D-115).

    The export holds the same private data as the database, so it goes where the operator
    already keeps that, never into the working directory.
    """
    return settings.database_path.parent / "exports" / f"{game_id}.json"


def _write_export(engine, export_path: Path, game_id: str) -> None:
    """Best-effort partial administrative export after a failed run; never masks the failure."""
    try:
        from app.export import build_game_export, write_json_atomic

        write_json_atomic(export_path, build_game_export(engine, uuid.UUID(game_id)))
        logging.getLogger("amaw.cli").info(
            "wrote partial administrative export path=%s game_id=%s", export_path, game_id
        )
    except Exception as export_error:  # noqa: BLE001 - preserve original run failure
        logging.getLogger("amaw.cli").error(
            "could not write partial export path=%s error=%s",
            export_path,
            type(export_error).__name__,
        )


# The condition flags ``run --resume-game`` refuses, because the sealed recipe reconstructs
# them (I-048). Kept as an explicit, named set on purpose — no reflection over the Typer
# signature — and guarded by ``test_i061_001`` so it cannot silently miss a new flag.
RESUME_OVERRIDE_FLAGS: tuple[str, ...] = (
    "player-model",
    "umpire-model",
    "seed",
    "request-timeout-seconds",
    "cons-concurrency",
    "diplomacy-concurrency",
    "diplomacy-mode",
    "diplomacy-rounds-per-turn",
    "con-mode",
    "con-challengers-per-argument",
    "con-reply-mode",
    "reply-max-points",
    "consequence-mode",
    "consequence-model",
    "umpire-prompt-version",
    "activation-mode",
    "research-mode",
    "search-contract",
    "research-history-mode",
    "tool-surface",
    "max-messages-per-activation",
    "max-llm-iterations",
    "max-tool-calls",
    "max-research-tool-calls",
    "max-research-operations",
    "max-messages-per-phase",
    "max-activations-per-opportunity",
    "player-temperature",
    "player-max-tokens",
    "player-reasoning-effort",
    "umpire-temperature",
    "umpire-max-tokens",
    "umpire-reasoning-effort",
    "inference-profile",
    "max-cycles",
)


@app.command()
def run(
    ctx: typer.Context,
    scenario: str | None = typer.Argument(
        None, help="Scenario package path/name; omit only with --resume-game."
    ),
    resume_game: str | None = typer.Option(
        None, "--resume-game", help="Resume this incomplete Game from immutable provenance."
    ),
    player_model: PlayerModelOpt = None,
    umpire_model: UmpireModelOpt = None,
    seed: int | None = typer.Option(
        None, "--seed", help="Override the scenario seed for this game."
    ),
    max_cycles: MaxCyclesOpt = None,
    request_timeout_seconds: RequestTimeoutOpt = None,
    log_level: LogLevelOpt = None,
    log_file: LogFileOpt = None,
    stream_game_details: StreamGameDetailsOpt = None,
    cons_concurrency: ConsConcurrencyOpt = None,
    diplomacy_concurrency: DiplomacyConcurrencyOpt = None,
    diplomacy_mode: DiplomacyModeOpt = None,
    diplomacy_rounds_per_turn: DiplomacyRoundsOpt = None,
    con_mode: ConModeOpt = None,
    con_challengers_per_argument: ConChallengersOpt = None,
    con_reply_mode: ConReplyModeOpt = None,
    reply_max_points: ReplyMaxPointsOpt = None,
    consequence_mode: ConsequenceModeOpt = None,
    consequence_model: ConsequenceModelOpt = None,
    umpire_prompt_version: UmpirePromptVersionOpt = None,
    activation_mode: ActivationModeOpt = None,
    research_mode: ResearchModeOpt = None,
    search_contract: SearchContractOpt = None,
    research_history_mode: ResearchHistoryModeOpt = None,
    tool_surface: ToolSurfaceOpt = None,
    max_messages_per_activation: MaxMessagesPerActivationOpt = None,
    max_llm_iterations: MaxLlmIterationsOpt = None,
    max_tool_calls: MaxToolCallsOpt = None,
    max_research_tool_calls: MaxResearchToolCallsOpt = None,
    max_research_operations: MaxResearchOperationsOpt = None,
    max_messages_per_phase: MaxMessagesPerPhaseOpt = None,
    max_activations_per_opportunity: MaxActivationsPerOpportunityOpt = None,
    player_temperature: PlayerTemperatureOpt = None,
    player_max_tokens: PlayerMaxTokensOpt = None,
    player_reasoning_effort: PlayerReasoningEffortOpt = None,
    umpire_temperature: UmpireTemperatureOpt = None,
    umpire_max_tokens: UmpireMaxTokensOpt = None,
    umpire_reasoning_effort: UmpireReasoningEffortOpt = None,
    inference_profile: InferenceProfileOpt = None,
    export_path: Path | None = typer.Option(
        None,
        "--export-path",
        help=(
            "Write the final or best-effort partial administrative export to this file "
            "(default: exports/<game_id>.json beside the database)."
        ),
    ),
    analysis_plan: AnalysisPlanOpt = None,
    run_class: RunClassOpt = None,
) -> None:
    """Create or resume and autonomously complete one Game; print its game ID."""
    condition_flags = ConditionFlags.from_command(locals())
    # --- parse -------------------------------------------------------------------------------
    if (scenario is None) == (resume_game is None):
        typer.echo("error: supply exactly one of SCENARIO or --resume-game", err=True)
        raise typer.Exit(code=2)
    scenario_path = (
        ctx.with_resource(_resolve_scenario(scenario)) if scenario is not None else None
    )
    _check_run_class(run_class, analysis_plan)
    if resume_game is not None and (analysis_plan is not None or run_class is not None):
        typer.echo(
            "error: --resume-game reconstructs the sealed analysis plan; do not supply "
            "--analysis-plan or --run-class",
            err=True,
        )
        raise typer.Exit(code=2)
    sealed_plan = _load_analysis_plan(analysis_plan)
    settings, admin_token = _autonomous_settings(
        "autonomous runs",
        request_timeout_seconds=request_timeout_seconds,
        log_level=log_level,
        log_file=log_file,
        max_llm_iterations=max_llm_iterations,
        max_tool_calls=max_tool_calls,
        max_research_tool_calls=max_research_tool_calls,
        max_research_operations=max_research_operations,
        max_messages_per_phase=max_messages_per_phase,
        max_activations_per_opportunity=max_activations_per_opportunity,
    )
    if export_path is not None:
        _refuse_database_export_path(export_path, settings.database_path)
    _require_agent_stack("autonomous runs")
    from fastapi.testclient import TestClient

    from agents.autonomous import AutonomousGameError, AutonomousGameRunner
    from agents.code_version import CodeVersionError
    from agents.inference_profile import InferenceProfileError
    from agents.player import PlayerConfig, PlayerError
    from app import resumption
    from app.api import create_app
    from app.database import engine_for_path
    from app.game_service import GameNotFound

    # --- resolve the condition ---------------------------------------------------------------
    if resume_game is None:
        profile = _inference_profile(
            inference_profile if inference_profile is not None else settings.inference_profile
        )
    engine = engine_for_path(settings.database_path)
    try:
        assignments: dict[str, str] | None = None
        # The persisted manifest's own ``runtime`` on resume: read back as sealed, never rebuilt.
        stored_runtime: dict[str, Any] | None = None
        if resume_game is not None:
            # The sealed recipe reconstructs the whole condition; a flag that would change it is
            # refused rather than silently ignored. ``RESUME_OVERRIDE_FLAGS`` names the set.
            flags = locals()
            supplied = [
                flag for flag in RESUME_OVERRIDE_FLAGS if flags[flag.replace("-", "_")] is not None
            ]
            if supplied:
                typer.echo(
                    "error: --resume-game reconstructs the experimental condition; do not supply "
                    + ", ".join(f"--{name}" for name in supplied),
                    err=True,
                )
                raise typer.Exit(code=2)
            try:
                descriptor = resumption.describe(engine, uuid.UUID(resume_game))
                resume_recipe = descriptor.manifest["resume"]
                assignments = dict(resume_recipe["player"]["assignments"])
                player_model = next(iter(assignments.values()))
                player_config = PlayerConfig.from_resume_recipe(resume_recipe, model=player_model)
            except (ValueError, resumption.ResumeError, GameNotFound, InferenceProfileError) as exc:
                typer.echo(f"error: game {resume_game!r} is not resumable: {exc}", err=True)
                raise typer.Exit(code=1) from exc
            condition = _resolve_sealed_condition(resume_recipe, player_config)
            stored_runtime = descriptor.manifest.get("runtime") or {}
            stored_stream_game_details = bool(stored_runtime.get("stream_game_details", False))
            if stream_game_details is None:
                stream_game_details = stored_stream_game_details
            elif stream_game_details != stored_stream_game_details:
                logging.getLogger("amaw.cli").info(
                    "resume runtime presentation override stream_game_details=%s "
                    "sealed_stream_game_details=%s",
                    stream_game_details,
                    stored_stream_game_details,
                )
            settings = settings.model_copy(
                update={"request_timeout_seconds": player_config.timeout}
            )
        else:
            stream_game_details = bool(stream_game_details)
            player_model = player_model or settings.player_model
            condition = _resolve_new_condition(
                settings,
                condition_flags,
                profile=profile,
                player_model=player_model,
                scenario_condition=_scenario_condition_defaults(scenario_path),
                scenario_declares_rules=_scenario_declares_rules(scenario_path),
            )
        profile = condition.profile
        player_config = condition.player_config
        game_loop = condition.game_loop
        umpire_recipe = condition.umpire_recipe
        consequence_recipe = condition.consequence_recipe
        max_cycles = condition.max_cycles
        run_runtime = cast(RunRuntime, stored_runtime) if stored_runtime else _run_runtime(
            settings, max_cycles=max_cycles, stream_game_details=stream_game_details,
            player_config=player_config,
        )
        run_configuration: RunConfiguration = {
            "runtime": run_runtime, "resume": condition.resume_recipe,
        }

        # --- compose and drive ---------------------------------------------------------------
        make_player = _player_runner_factory(settings, player_config)

        def player_factory(tools, actor_id: str, actor_name: str):
            model = assignments[actor_id] if assignments is not None else player_model
            return make_player(tools, actor_id, actor_name, model)

        runner: AutonomousGameRunner | None = None
        umpire = _compose_umpire(settings, profile, umpire_recipe)
        application = create_app(
            engine=engine,
            settings=settings,
            umpire=umpire,
            consequence_resolver=_compose_consequence_resolver(settings, consequence_recipe),
        )
        logging.getLogger("amaw.cli").info(
            "autonomous run configuration scenario=%s player_model=%s umpire_model=%s "
            "umpire_prompt_version=%s "
            "timeout_seconds=%s max_cycles=%s diplomacy_concurrency=%s cons_concurrency=%s "
            "diplomacy_mode=%s diplomacy_rounds_per_turn=%s "
            "con_mode=%s con_challengers_per_argument=%s "
            "con_reply_mode=%s reply_max_points=%s "
            "consequence_mode=%s consequence_model=%s "
            "inference_profile=%s run_class=%s analysis_plan_sha256=%s "
            "stream_game_details=%s export_path=%s",
            scenario_path,
            player_model,
            umpire_recipe["model"],
            umpire_recipe["prompt_version"],
            settings.request_timeout_seconds,
            max_cycles,
            condition.diplomacy_concurrency,
            condition.cons_concurrency,
            game_loop.diplomacy_mode,
            game_loop.diplomacy_rounds_per_turn,
            game_loop.con_mode,
            game_loop.con_challengers_per_argument,
            game_loop.con_reply_mode,
            game_loop.reply_max_points,
            game_loop.consequence_mode,
            consequence_recipe["model"] if consequence_recipe is not None else None,
            profile.name,
            (
                "from-manifest" if resume_game is not None
                else "analytical" if sealed_plan is not None else "playtest"
            ),
            sealed_plan.sha256 if sealed_plan is not None else None,
            stream_game_details,
            export_path,
        )
        with TestClient(application, raise_server_exceptions=False) as client:
            runner = AutonomousGameRunner(
                client,
                admin_token=admin_token,
                scenario=scenario_path or ".",
                player_factory=player_factory,
                seed=seed,
                max_cycles=max_cycles,
                stream_game_details=stream_game_details,
                run_configuration=run_configuration,
                cons_concurrency=condition.cons_concurrency,
                diplomacy_concurrency=condition.diplomacy_concurrency,
                **game_loop.as_dict(),
                analysis_plan=sealed_plan,
            )
            result = runner.run() if resume_game is None else runner.resume(resume_game)
    # I-085: a detected checkout that cannot be identified is a failed run, never a run under
    # another identity system.
    except (AutonomousGameError, PlayerError, CodeVersionError) as exc:
        if runner is not None and runner.last_game_id is not None:
            partial_path = export_path or _default_export_path(settings, runner.last_game_id)
            _write_export(engine, partial_path, runner.last_game_id)
            typer.echo(f"partial export: {partial_path}", err=True)
        game_id = f" for game {runner.last_game_id}" if runner and runner.last_game_id else ""
        typer.echo(f"error: autonomous run{game_id} failed: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    finally:
        engine.dispose()

    # --- write -------------------------------------------------------------------------------
    from app.export import write_json_atomic

    export_path = export_path or _default_export_path(settings, result.game_id)
    write_json_atomic(export_path, result.export)
    logging.getLogger("amaw.cli").info(
        "wrote administrative export path=%s game_id=%s", export_path, result.game_id
    )
    typer.echo(f"export: {export_path}", err=True)
    typer.echo(result.game_id)


@app.command()
def benchmark(
    ctx: typer.Context,
    scenario: str = typer.Argument(..., help="Scenario package path/name."),
    models: str = typer.Option(
        ...,
        "--models",
        help=(
            "Comma-separated OpenRouter player models: one model for every Seat, or exactly one "
            "per Actor in turn order (with --rotate: every permutation)."
        ),
    ),
    rotate: bool = typer.Option(
        False, "--rotate", help="Rotate models over Actor Seats: every permutation (TRD §30)."
    ),
    rotation: str | None = typer.Option(
        None,
        "--rotation",
        help=(
            "Assignment policy: single (positional), permutations (= --rotate, equal counts) or "
            "cyclic (Actor[j] -> models[(k + j) % M]; every model in every Seat exactly once, "
            "unequal counts allowed; methodology §6)."
        ),
    ),
    benchmark_mode: str = typer.Option(
        ...,
        "--benchmark-mode",
        help=(
            "Interpretation of the result set (methodology §5): model — player model is the "
            "varied factor and the §5.1 held-constant set is sealed and verified; agent — "
            "agent-configuration comparison. Required; never inferred."
        ),
    ),
    repetitions: int = typer.Option(
        1, "--repetitions", min=1, max=999,
        help=(
            "Games per generated model/role assignment (at most 999: the recorded seed policy "
            "reuses seeds across assignments beyond that)."
        ),
    ),
    output_dir: Path = typer.Option(
        ..., "--output-dir", help="Batch directory: batch.json, games/<attempt>/, the CSV tables."
    ),
    experiment_id: str | None = typer.Option(
        None, "--experiment-id", help="Experiment identifier (default: a fresh UUID)."
    ),
    base_seed: int | None = typer.Option(
        None,
        "--base-seed",
        help=(
            "Seed policy base: game seed = base_seed + assignment_index * 1000 + "
            "repetition_index (default: random, recorded)."
        ),
    ),
    scheduling_seed: int | None = typer.Option(
        None,
        "--scheduling-seed",
        help="Seed for the recorded execution interleaving only (default: random, recorded).",
    ),
    umpire_model: UmpireModelOpt = None,
    max_cycles: MaxCyclesOpt = None,
    request_timeout_seconds: RequestTimeoutOpt = None,
    log_level: LogLevelOpt = None,
    log_file: LogFileOpt = None,
    stream_game_details: StreamGameDetailsOpt = False,
    cons_concurrency: ConsConcurrencyOpt = None,
    diplomacy_concurrency: DiplomacyConcurrencyOpt = None,
    diplomacy_mode: DiplomacyModeOpt = None,
    diplomacy_rounds_per_turn: DiplomacyRoundsOpt = None,
    con_mode: ConModeOpt = None,
    con_challengers_per_argument: ConChallengersOpt = None,
    con_reply_mode: ConReplyModeOpt = None,
    reply_max_points: ReplyMaxPointsOpt = None,
    consequence_mode: ConsequenceModeOpt = None,
    consequence_model: ConsequenceModelOpt = None,
    umpire_prompt_version: UmpirePromptVersionOpt = None,
    activation_mode: ActivationModeOpt = None,
    research_mode: ResearchModeOpt = None,
    search_contract: SearchContractOpt = None,
    research_history_mode: ResearchHistoryModeOpt = None,
    tool_surface: ToolSurfaceOpt = None,
    max_messages_per_activation: MaxMessagesPerActivationOpt = None,
    max_llm_iterations: MaxLlmIterationsOpt = None,
    max_tool_calls: MaxToolCallsOpt = None,
    max_research_tool_calls: MaxResearchToolCallsOpt = None,
    max_research_operations: MaxResearchOperationsOpt = None,
    max_messages_per_phase: MaxMessagesPerPhaseOpt = None,
    max_activations_per_opportunity: MaxActivationsPerOpportunityOpt = None,
    player_temperature: PlayerTemperatureOpt = None,
    player_max_tokens: PlayerMaxTokensOpt = None,
    player_reasoning_effort: PlayerReasoningEffortOpt = None,
    umpire_temperature: UmpireTemperatureOpt = None,
    umpire_max_tokens: UmpireMaxTokensOpt = None,
    umpire_reasoning_effort: UmpireReasoningEffortOpt = None,
    inference_profile: InferenceProfileOpt = None,
    analysis_plan: AnalysisPlanOpt = None,
    run_class: RunClassOpt = None,
    plan_level: PlanLevelOpt = None,
) -> None:
    """Schedule and run a benchmark batch of autonomous games (TRD §30).

    The full schedule is reported before any game exists; one failed game never aborts the batch.

    Every attempt is retained under --output-dir with its export, plus the five raw CSV tables.
    """
    condition_flags = ConditionFlags.from_command(locals())
    # --- parse -------------------------------------------------------------------------------
    scenario_path = ctx.with_resource(_resolve_scenario(scenario))
    from benchmark.controls import BENCHMARK_MODES, ControlError
    from benchmark.runner import ScheduleError, build_schedule, resolve_rotation

    # T-057: the interpretation is declared, never inferred from the roster or the flags.
    if benchmark_mode not in BENCHMARK_MODES:
        typer.echo(
            f"error: --benchmark-mode must be one of {', '.join(BENCHMARK_MODES)}, "
            f"got {benchmark_mode!r}",
            err=True,
        )
        raise typer.Exit(code=2)
    _check_run_class(run_class, analysis_plan)
    if plan_level is not None and analysis_plan is None:
        typer.echo("error: --plan-level names a level of --analysis-plan; pass the plan", err=True)
        raise typer.Exit(code=2)
    sealed_plan = _load_analysis_plan(analysis_plan)

    # The schedule needs only the scenario's Actor roster; it is planned before any database,
    # provider call or Game exists so an unusable batch costs nothing (methodology §8).
    from app.scenarios import ScenarioError, load_scenario

    try:
        loaded_scenario = load_scenario(scenario_path)
    except ScenarioError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    actor_ids = list(loaded_scenario.game.turn_order)
    model_list = [model.strip() for model in models.split(",") if model.strip()]
    roster = list(dict.fromkeys(model_list))  # the compared roster, distinct, in given order
    try:
        rotation_policy = resolve_rotation(rotate=rotate, rotation=rotation)
    except ScheduleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    # T-057: a rotated model benchmark compares distinct models; a repeated name would be
    # counted as two independent models (methodology §5.1 "reports must name the roster").
    if benchmark_mode == "model" and rotation_policy != "single" and len(roster) != len(model_list):
        typer.echo(
            "error: --benchmark-mode model with rotation needs distinct model identities in "
            f"--models; got {model_list}",
            err=True,
        )
        raise typer.Exit(code=2)
    # I-078: the evidence directory is batch-owned; refuse before any database or Game exists.
    from benchmark.runner import require_empty_output_dir

    try:
        require_empty_output_dir(output_dir)
    except ScheduleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    if experiment_id is None:
        experiment_id = str(uuid.uuid4())
    if base_seed is None:
        base_seed = secrets.randbelow(2**31)
    if scheduling_seed is None:
        scheduling_seed = secrets.randbelow(2**31)
    try:
        schedule = build_schedule(
            actor_ids=actor_ids,
            models=model_list,
            rotation=rotation_policy,
            repetitions=repetitions,
            base_seed=base_seed,
            scheduling_seed=scheduling_seed,
            experiment_id=experiment_id,
        )
    except ScheduleError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    counts = schedule.counts()
    # I-078: the predeclared plan must describe this schedule before any game or provider call.
    plan_record = None
    if sealed_plan is not None:
        from benchmark.controls import reconcile_plan

        try:
            plan_record = reconcile_plan(
                sealed_plan, schedule=schedule, roster=roster, plan_level=plan_level
            )
        except ControlError as exc:
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    typer.echo(
        f"benchmark schedule experiment_id={experiment_id} benchmark_mode={benchmark_mode} "
        f"rotation_policy={schedule.rotation_policy} "
        f"assignment_count={counts['assignment_count']} "
        f"repetitions_per_assignment={counts['repetitions_per_assignment']} "
        f"total_games={counts['total_games']} base_seed={base_seed} "
        f"scheduling_seed={scheduling_seed} output_dir={output_dir}",
        err=True,
    )

    settings, admin_token = _autonomous_settings(
        "benchmark batches",
        request_timeout_seconds=request_timeout_seconds,
        log_level=log_level,
        log_file=log_file,
        max_llm_iterations=max_llm_iterations,
        max_tool_calls=max_tool_calls,
        max_research_tool_calls=max_research_tool_calls,
        max_research_operations=max_research_operations,
        max_messages_per_phase=max_messages_per_phase,
        max_activations_per_opportunity=max_activations_per_opportunity,
    )
    _require_agent_stack("benchmark batches")
    from fastapi.testclient import TestClient

    from agents.autonomous import expected_manifest_configuration
    from agents.code_version import CodeVersionError
    from app.api import create_app
    from app.database import engine_for_path
    from benchmark.controls import (
        BatchControls, ExpectedBatchCondition, seal_consequence_condition, seal_umpire_condition,
    )
    from benchmark.runner import (
        BatchIntegrityError, autonomous_executor, batch_benchmark_section, run_batch,
    )

    # --- resolve the condition ---------------------------------------------------------------
    profile = _inference_profile(
        inference_profile if inference_profile is not None else settings.inference_profile
    )
    engine = engine_for_path(settings.database_path)
    try:
        # One PlayerConfig for the batch; the executor swaps in each attempt's assigned model.
        condition = _resolve_new_condition(
            settings,
            condition_flags,
            profile=profile,
            player_model=model_list[0],
            scenario_condition=loaded_scenario.game.condition_defaults(),
            scenario_declares_rules=bool(loaded_scenario.rules),
        )
        player_config = condition.player_config
        game_loop = condition.game_loop
        umpire_recipe = condition.umpire_recipe
        consequence_recipe = condition.consequence_recipe
        resume_recipe = condition.resume_recipe
        run_runtime = _run_runtime(
            settings, max_cycles=condition.max_cycles,
            stream_game_details=bool(stream_game_details), player_config=player_config,
        )
        run_configuration: RunConfiguration = {"runtime": run_runtime, "resume": resume_recipe}
        player_factory = _player_runner_factory(settings, player_config)

        # The experiment record (benchmark-methodology §4): what the batch holds constant and how
        # it is to be read. The sealed Umpire condition (T-056) and the mode/roster/interpretation
        # (T-057) are added from the declared controls once the Umpire exists.
        experiment = {
            "name": experiment_id,
            "benchmark_mode": benchmark_mode,
            "scenario": str(scenario_path),
            "scenario_id": loaded_scenario.scenario.id,
            "scenario_version": loaded_scenario.scenario.version,
            "models": model_list,
            "rotate": rotate,
            "run_class": "analytical" if sealed_plan is not None else "playtest",
            "analysis_plan_sha256": sealed_plan.sha256 if sealed_plan is not None else None,
            "analysis_plan": plan_record,
            "umpire": dict(resume_recipe["umpire"]),
            "player": {
                key: value for key, value in resume_recipe["player"].items() if key != "assignments"
            },
            "inference_profile": profile.name,
            "coordinator": dict(resume_recipe["coordinator"]),
            "game_loop": game_loop.as_dict(),
            "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        if consequence_recipe is not None:
            experiment["consequence"] = dict(consequence_recipe)
        secret_values = [settings.openrouter_api_key.get_secret_value(), admin_token]

        # --- compose and drive ---------------------------------------------------------------
        umpire = _compose_umpire(settings, profile, umpire_recipe)
        consequence_resolver = _compose_consequence_resolver(settings, consequence_recipe)
        # T-056: one Umpire condition per batch, sealed from the same provenance the server
        # will record, before any game exists. The recipe entry is the one every game seals.
        # I-078: the complete expected condition — the manifest configuration the runner will
        # submit for every attempt, through the runner's own builder — is declared beside it,
        # so attempt 1 is verified against a declaration rather than defining it.
        try:
            umpire_condition = seal_umpire_condition(
                umpire, inference_profile=profile, resume_umpire=resume_recipe["umpire"],
            )
            # I-114 / D-092: under bounded_effects_v1 the resolver is sealed beside the Umpire.
            consequence_condition = (
                seal_consequence_condition(
                    consequence_resolver, resume_consequence=resume_recipe["consequence"],
                )
                if consequence_resolver is not None else None
            )
            controls = BatchControls.declare(
                benchmark_mode=benchmark_mode,
                roster=roster,
                umpire_condition=umpire_condition,
                consequence_condition=consequence_condition,
                expected_condition=ExpectedBatchCondition.derive(
                    scenario=loaded_scenario,
                    configuration=expected_manifest_configuration(
                        player_config,
                        benchmark=batch_benchmark_section(
                            schedule, benchmark_mode=benchmark_mode
                        ),
                        run_configuration=run_configuration,
                        analysis_plan=sealed_plan,
                        max_cycles=condition.max_cycles,
                        cons_concurrency=condition.cons_concurrency,
                        diplomacy_concurrency=condition.diplomacy_concurrency,
                        condition=game_loop,
                    ),
                    umpire_condition=umpire_condition,
                ),
            )
        except (ControlError, CodeVersionError) as exc:  # I-085: no batch without an identity
            typer.echo(f"error: {exc}", err=True)
            raise typer.Exit(code=2) from exc
        experiment.update(controls.record())
        application = create_app(
            engine=engine, settings=settings, umpire=umpire,
            consequence_resolver=consequence_resolver,
        )
        logging.getLogger("amaw.cli").info(
            "benchmark batch configuration scenario=%s models=%s benchmark_mode=%s "
            "rotation_policy=%s umpire_model=%s umpire_condition_sha256=%s consequence_mode=%s "
            "consequence_model=%s consequence_condition_sha256=%s assignment_count=%s "
            "repetitions_per_assignment=%s total_games=%s base_seed=%s scheduling_seed=%s "
            "run_class=%s output_dir=%s",
            scenario_path, model_list, benchmark_mode, schedule.rotation_policy,
            umpire_recipe["model"], controls.umpire_condition_sha256,
            game_loop.consequence_mode,
            consequence_recipe["model"] if consequence_recipe is not None else None,
            controls.consequence_condition_sha256, counts["assignment_count"],
            counts["repetitions_per_assignment"], counts["total_games"], base_seed,
            scheduling_seed, experiment["run_class"], output_dir,
        )
        integrity_failure: BatchIntegrityError | None = None
        with TestClient(application, raise_server_exceptions=False) as client:
            try:
                result = run_batch(
                    schedule,
                    execute=autonomous_executor(
                        client,
                        admin_token=admin_token,
                        scenario=scenario_path,
                        player_factory=player_factory,
                        run_configuration=run_configuration,
                        analysis_plan=sealed_plan,
                        benchmark_mode=benchmark_mode,
                        max_cycles=condition.max_cycles,
                        stream_game_details=stream_game_details,
                        cons_concurrency=condition.cons_concurrency,
                        diplomacy_concurrency=condition.diplomacy_concurrency,
                        **game_loop.as_dict(),
                    ),
                    output_dir=output_dir,
                    experiment=experiment,
                    secret_values=secret_values,
                    controls=controls,
                )
            except BatchIntegrityError as exc:
                integrity_failure, result = exc, exc.result
    finally:
        engine.dispose()

    # --- write -------------------------------------------------------------------------------
    outcome = result.counts()
    typer.echo(
        f"benchmark {'stopped' if integrity_failure else 'complete'} "
        f"experiment_id={experiment_id} benchmark_mode={benchmark_mode} "
        f"total_games={outcome['total_games']} completed_games={outcome['completed_games']} "
        f"failed_games={outcome['failed_games']} not_run_games={outcome['not_run_games']} "
        f"output_dir={output_dir}",
        err=True,
    )
    if integrity_failure is not None:
        # D-058: a drifted condition is a different experiment; the batch is not a result set.
        typer.echo(
            f"error: {integrity_failure}; remaining attempts recorded as not_run — an Umpire or "
            "held-constant change needs a separate batch (benchmark-methodology §9)",
            err=True,
        )
        typer.echo(str(output_dir))
        raise typer.Exit(code=1)
    typer.echo(str(output_dir))


if __name__ == "__main__":  # pragma: no cover
    app()
