"""``amaw run``: play one autonomous game, or resume one from its sealed recipe."""

from __future__ import annotations

import contextlib
from contextlib import contextmanager
from datetime import UTC, datetime
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast
import uuid
from pathlib import Path

import typer

from amaw_contracts import RunConfiguration, RunRuntime
from amaw_cli._app import app
from amaw_cli.exports import _default_export_path, _refuse_database_export_path, _write_export
from amaw_cli.options import (
    ActivationModeOpt,
    AnalysisPlanOpt,
    ConChallengersOpt,
    ConModeOpt,
    ConReplyModeOpt,
    ConsConcurrencyOpt,
    ConsequenceModeOpt,
    ConsequenceModelOpt,
    DiplomacyConcurrencyOpt,
    DiplomacyModeOpt,
    DiplomacyRoundsOpt,
    InferenceProfileOpt,
    LogFileOpt,
    LogLevelOpt,
    MaxActivationsPerOpportunityOpt,
    MaxCyclesOpt,
    MaxLlmIterationsOpt,
    MaxMessagesPerActivationOpt,
    MaxMessagesPerPhaseOpt,
    MaxResearchOperationsOpt,
    MaxResearchToolCallsOpt,
    MaxToolCallsOpt,
    PlayerMaxTokensOpt,
    PlayerModelOpt,
    PlayerReasoningEffortOpt,
    PlayerTemperatureOpt,
    PublishOpt,
    ReplyMaxPointsOpt,
    RequestTimeoutOpt,
    ResearchHistoryModeOpt,
    ResearchModeOpt,
    RunClassOpt,
    SearchContractOpt,
    StreamGameDetailsOpt,
    ToolSurfaceOpt,
    UmpireMaxTokensOpt,
    UmpireModelOpt,
    UmpirePromptVersionOpt,
    UmpireReasoningEffortOpt,
    UmpireTemperatureOpt,
    WatchOpt,
)
from amaw_cli.conditions import (
    ConditionFlags,
    _autonomous_settings,
    _check_run_class,
    _compose_consequence_resolver,
    _compose_umpire,
    _inference_profile,
    _load_analysis_plan,
    _player_runner_factory,
    _require_agent_stack,
    _resolve_new_condition,
    _resolve_scenario,
    _resolve_sealed_condition,
    _run_runtime,
    _scenario_condition_defaults,
    _scenario_declares_rules,
)

if TYPE_CHECKING:  # typing only: the agent stack is imported lazily inside the commands
    from agents.player import PlayerConfig
    from app.config import Settings


@contextmanager
def _publisher_during_run(settings: Settings, relay: Any, game_id: Callable[[], str | None]):
    """Publish the run's game to the relay on a daemon thread, draining it when the run ends.

    The publisher reads through its own read-only engine and never raises into the run; a dead
    relay costs the run nothing but the bounded drain at the end (TRD §41.1).
    """
    from observer import publish as publishing
    from observer.feed import read_only_engine

    engine = read_only_engine(settings.database_path)
    client = publishing._client(relay)

    def current() -> uuid.UUID | None:
        value = game_id()
        return uuid.UUID(value) if value else None

    thread, stop = publishing.publish_in_background(
        engine, current, client=client, token=relay.token
    )
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=60)
        client.close()
        engine.dispose()


@contextmanager
def _viewer_during_run(settings: Settings, game_id: Callable[[], str | None]):
    """Show the live viewer while a run plays, with operational logs kept off the terminal.

    The viewer reads the database through its own read-only engine on a daemon thread. Logs that
    would go to the terminal are detached for the duration, since the run logs to its log file.
    The viewer is always closed, and the terminal restored, before the run reports its result.
    """
    from observer.feed import read_only_engine
    from observer.watch import watch_in_background

    root = logging.getLogger()
    detached = [
        handler for handler in root.handlers
        if type(handler) is logging.StreamHandler
    ]
    for handler in detached:
        root.removeHandler(handler)
    engine = read_only_engine(settings.database_path)

    def current() -> uuid.UUID | None:
        value = game_id()
        return uuid.UUID(value) if value else None

    thread, stop = watch_in_background(engine, current)
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
        engine.dispose()
        for handler in detached:
            root.addHandler(handler)


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
    watch: WatchOpt = False,
    publish: PublishOpt = False,
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
    if watch and settings.log_file is None:
        stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        settings = settings.model_copy(
            update={"log_file": settings.database_path.parent / "logs" / f"run-{stamp}.log"}
        )
    if watch:
        typer.echo(f"logs: {settings.log_file}", err=True)
    relay = None
    if publish:
        from observer.publish import PublishError, relay_settings

        try:
            relay = relay_settings()
        except PublishError as exc:
            typer.echo(f"error: --publish: {exc}", err=True)
            raise typer.Exit(code=2) from None
        typer.echo(f"publishing to {relay.url}", err=True)
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
            def current_game() -> str | None:
                return runner.last_game_id if runner else None

            with contextlib.ExitStack() as presentation:
                if relay is not None:
                    presentation.enter_context(
                        _publisher_during_run(settings, relay, current_game)
                    )
                if watch:
                    presentation.enter_context(_viewer_during_run(settings, current_game))
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
