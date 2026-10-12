"""``amaw benchmark``: a rotated, repeated batch of autonomous games."""

from __future__ import annotations

from datetime import UTC, datetime
import logging
import secrets
import uuid
from pathlib import Path

import typer

from amaw_contracts import RunConfiguration
from amaw_cli._app import app
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
    PlanLevelOpt,
    PlayerMaxTokensOpt,
    PlayerReasoningEffortOpt,
    PlayerTemperatureOpt,
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
    _run_runtime,
)


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
