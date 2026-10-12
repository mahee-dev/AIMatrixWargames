"""The experimental-condition options that ``run`` and ``benchmark`` both declare."""

from __future__ import annotations

from typing import Annotated
from pathlib import Path

import typer


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
PublishOpt = Annotated[bool, typer.Option(
    "--publish",
    help="Publish the game live to the relay in AMAW_RELAY_URL (includes private material).",
)]
WatchOpt = Annotated[bool, typer.Option(
    "--watch",
    help="Show the live terminal console (keys 1-4 switch screens, q closes it and the run "
         "carries on); logs go to --log-file.",
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
