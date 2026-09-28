"""HTTP-only multi-Actor orchestration for autonomous games (T-044).

This module composes the existing per-Actor :class:`LifecycleRunner` instances with the private
admin API.  It deliberately owns no game rules: Actors observe and act only through Seat-token
``PlayerTools``; the server decides whether input is required; adjudication and advancement happen
only through ``POST /admin/games/{id}/advance``.  The coordinator observes authoritative state,
records bounded activation/failure provenance, and stops on completion, pause, API failure, or a
cycle/stall bound.

It imports no ``app`` internals.  A deployment can pass an ``httpx.Client`` pointed at a running
AMAW server; tests pass FastAPI's compatible in-process ``TestClient``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Callable, Mapping
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from importlib import metadata
import json
import logging
from pathlib import Path
import threading
from typing import Any, Protocol, cast

from amaw_contracts import (
    RESUME_RECIPE_SCHEMA,
    ConsequenceRecipe,
    ResumeRecipe,
    UmpireRecipe,
)

from agents.code_version import IN_PROCESS_TRANSPORT, code_version, runtime_fingerprint
from agents.lifecycle import AgentFailure, LifecycleRunner
from agents.player import (
    PLAYER_CONTEXT_MODE,
    PLAYER_INFERENCE_PHASES,
    PLAYER_MEMORY_VERSION,
    PlayerConfig,
    prompt_hash_for,
    tool_schema_version_for,
)
from agents.tools import HTTPClient, PlayerTools
from app.game_loop import (
    CON_MODE_OPEN,
    CON_REPLY_MODE_SEALED,
    CONDITION_FIELDS,
    CONSEQUENCE_MODE_NONE,
    DIPLOMACY_MODE_OPEN,
    GameLoopCondition,
)

logger = logging.getLogger("agents.autonomous")


PlayerFactory = Callable[[PlayerTools, str, str], LifecycleRunner]


def build_resume_recipe(
    config: PlayerConfig,
    *,
    assignments: dict[str, str],
    max_cycles: int,
    cons_concurrency: int,
    diplomacy_concurrency: int,
    condition: GameLoopCondition,
    umpire: UmpireRecipe | None = None,
    consequence: ConsequenceRecipe | None = None,
) -> ResumeRecipe:
    """The ``autonomous-resume-v1`` recipe a game seals so it can be resumed byte-for-byte.

    The one builder (I-061): the CLI seals it for a fresh game with the Umpire's flat flag
    values in ``umpire``; the coordinator rebuilds it from the players when a run supplies
    none. :meth:`agents.player.PlayerConfig.from_resume_recipe` is its inverse.
    """
    # Assembled as a plain mapping so the sections land in the sealed order (``umpire`` and
    # ``consequence`` are optional and sit before ``inference_profile``/``coordinator``).
    recipe: dict[str, Any] = {
        "schema": RESUME_RECIPE_SCHEMA,
        "player": {
            "assignments": dict(assignments),
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "timeout": config.timeout,
            "reasoning_effort": config.reasoning_effort,
            "activation_mode": config.activation_mode,
            "research_mode": config.research_mode,
            "search_contract": config.search_contract,
            "research_history_mode": config.research_history_mode,
            "tool_surface": config.tool_surface,
            "prompt_version": config.prompt_version,
            "guardrails": dataclasses.asdict(config.guardrails),
        },
    }
    if umpire is not None:
        recipe["umpire"] = umpire.copy()
    if consequence is not None:
        # T-074 / D-091: the consequence-resolver recipe, only for a ``bounded_effects_v1`` game.
        recipe["consequence"] = consequence.copy()
    recipe["inference_profile"] = config.inference_profile.to_document()
    recipe["coordinator"] = {
        "max_cycles": max_cycles,
        "cons_concurrency": cons_concurrency,
        "diplomacy_concurrency": diplomacy_concurrency,
        **condition.as_dict(),
    }
    return cast(ResumeRecipe, recipe)


# The manifest sections only the runner may write (I-083); see ``_manifest_configuration_for``.
RESERVED_MANIFEST_SECTIONS = frozenset(
    {"player", "code_version", "environment", "coordinator", "game_loop"}
)


def _framework_version() -> str | None:
    try:
        return metadata.version("pydantic-ai")
    except metadata.PackageNotFoundError:  # pragma: no cover - packaging edge
        return None


def _require_recipe_describes(
    recipe: dict[str, Any],
    config: PlayerConfig,
    *,
    condition: GameLoopCondition,
    max_cycles: int,
    cons_concurrency: int,
    diplomacy_concurrency: int,
) -> None:
    """Refuse a supplied resume recipe that does not describe this game's condition (I-083).

    Compared semantically through the recipe's inverse, so a legacy recipe that resolves to
    the players' released defaults is still sealed byte-for-byte (I-061), while one carrying
    another temperature, budget, coordinator setting or game-loop condition is refused.
    """
    try:
        described = PlayerConfig.from_resume_recipe(recipe, model=config.model)
        coordinator = recipe["coordinator"]
    except (KeyError, TypeError, ValueError) as exc:
        raise AutonomousGameError(
            f"resume recipe is not a complete autonomous-resume-v1 recipe: {exc!r}"
        ) from exc
    if not isinstance(coordinator, dict):
        raise AutonomousGameError("resume recipe coordinator must be a mapping")
    shared = config.shared_condition()
    differing = sorted(
        name for name, value in described.shared_condition().items() if value != shared[name]
    )
    expected_coordinator = {
        "max_cycles": max_cycles,
        "cons_concurrency": cons_concurrency,
        "diplomacy_concurrency": diplomacy_concurrency,
    }
    differing.extend(
        f"coordinator.{name}" for name, value in expected_coordinator.items()
        if coordinator.get(name) != value
    )
    persisted = GameLoopCondition.from_mapping(coordinator)
    differing.extend(
        f"coordinator.{name}" for name in CONDITION_FIELDS
        if getattr(persisted, name) != getattr(condition, name)
    )
    if differing:
        raise AutonomousGameError(
            "resume recipe does not describe this game's condition; differing: "
            f"{', '.join(differing)}"
        )


def _manifest_configuration_for(
    config: PlayerConfig,
    *,
    assignments: dict[str, str],
    benchmark: dict[str, Any],
    run_configuration: dict[str, Any],
    analysis_plan: dict[str, Any] | None,
    max_cycles: int,
    cons_concurrency: int,
    diplomacy_concurrency: int,
    condition: GameLoopCondition,
    transport: str = IN_PROCESS_TRANSPORT,
) -> dict[str, Any]:
    """The run-manifest configuration for a game whose players all share ``config``.

    The one builder (I-078): :meth:`AutonomousGameRunner._manifest_configuration` records a
    game through it after checking that its players really do share one configuration, and
    :func:`expected_manifest_configuration` declares a batch's expectation through it before
    any game exists — so the two cannot compute differently.
    """
    benchmark_section: dict[str, object] = {
        "mode": "single",
        "assignment_id": None,
        "repetition_index": None,
        "run_class": "analytical" if analysis_plan else "playtest",
        "analysis_plan": analysis_plan,
    }
    base = {
        "player": {
            "prompt_version": config.prompt_version,
            "prompt_hash": prompt_hash_for(config),
            "activation_mode": config.activation_mode,
            "research_mode": config.research_mode,
            "search_contract": config.search_contract,
            "research_history_mode": config.research_history_mode,
            # I-092 / D-079: the tool-loop mutating surface, sealed beside the activation mode.
            "tool_surface": config.tool_surface,
            # I-032: sealed *resolved* so the manifest states the budget each phase actually ran
            # under rather than a name that could drift.
            "inference_profile": {
                "name": config.inference_profile.name,
                "phases": {
                    phase: config.resolve_inference(phase) for phase in PLAYER_INFERENCE_PHASES
                },
            },
            "max_messages_per_activation": config.guardrails.max_messages_per_activation,
            "context_mode": PLAYER_CONTEXT_MODE,
            "memory_version": PLAYER_MEMORY_VERSION,
            "agent_framework": "pydantic-ai",
            "agent_framework_version": _framework_version(),
            "tool_schema_version": tool_schema_version_for(config),
            "assignments": dict(assignments),
        },
        "code_version": code_version(),
        # I-085: what ran the code — interpreter, SQLite, runtime dependencies, lockfile and the
        # transport the coordinator drives the server through. Its sha256 is a held constant.
        "environment": runtime_fingerprint(transport=transport),
        # How the coordinator scheduled activations: rotated deterministic result order,
        # isolated bounded diplomacy rounds (I-028), and bounded Cons sweeps (I-029).
        "coordinator": {
            "diplomacy_concurrency": diplomacy_concurrency,
            "cons_concurrency": cons_concurrency,
            "sweep": "seat-order-rotated",
        },
        "game_loop": condition.provenance(),
        "benchmark": benchmark_section,
    }
    # I-083 / TRD §28: the sections derived from the players, the checkout and the runner's own
    # settings state what the game ran under; a caller may add to the manifest (``runtime``,
    # ``resume``, the batch identity) but never substitute a claim for them.
    reserved = sorted(RESERVED_MANIFEST_SECTIONS & set(run_configuration))
    if reserved:
        raise AutonomousGameError(
            "run_configuration cannot replace the reserved manifest section(s) "
            f"{', '.join(reserved)}; they are derived by the runner"
        )
    configured = {**base, **run_configuration}
    # T-054: a batch supplies its assignment identity on top of the section, never instead of
    # it — run_class and the sealed plan stay the runner's own (D-054).
    configured["benchmark"] = {**benchmark_section, **benchmark}
    recipe = configured.get("resume")
    if isinstance(recipe, dict):
        _require_recipe_describes(
            recipe, config, condition=condition, max_cycles=max_cycles,
            cons_concurrency=cons_concurrency, diplomacy_concurrency=diplomacy_concurrency,
        )
    else:
        recipe = build_resume_recipe(
            config,
            assignments=assignments,
            max_cycles=max_cycles,
            cons_concurrency=cons_concurrency,
            diplomacy_concurrency=diplomacy_concurrency,
            condition=condition,
        )
    if isinstance(recipe, dict):
        recipe = json.loads(json.dumps(recipe))
        player_recipe = recipe.get("player")
        if isinstance(player_recipe, dict) and not player_recipe.get("assignments"):
            player_recipe["assignments"] = dict(assignments)
        configured["resume"] = recipe
    return configured



def sweep_order(actor_ids: list[str], turn: Any) -> list[str]:
    """The activation order for one coordinator sweep: seat order, rotated by turn.

    Result handling is deterministic in every phase. Argument remains serial; Cons is an
    order-independent concurrent sweep; diplomacy uses isolated round-start snapshots, so no
    Actor anchors on another same-round Actor's text. Rotation still spreads first-result/serial
    positions without introducing randomness; the server remains the sole RNG owner.
    """
    if not actor_ids:
        return []
    offset = ((turn if isinstance(turn, int) else 1) - 1) % len(actor_ids)
    return actor_ids[offset:] + actor_ids[:offset]


class _ActorWorker:
    """One Actor's long-lived thread and event loop (I-042).

    PydanticAI's synchronous facade drives its async model client on the calling thread's event
    loop. The OpenRouter client and its pooled connections persist across activations, so every
    activation for one Actor must return to the same loop — including serial Argument work after
    concurrent diplomacy. A single-thread executor provides that affinity; the coordinator still
    controls cross-Actor concurrency with a bounded semaphore.
    """

    def __init__(self, actor_id: str, runner: LifecycleRunner) -> None:
        self._runner = runner
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix=f"amaw-actor-{actor_id}"
        )
        self._loop: asyncio.AbstractEventLoop | None = None

    def submit(
        self,
        context: dict[str, Any] | None,
        gate: threading.BoundedSemaphore | None = None,
    ) -> Future[Any]:
        return self._executor.submit(self._step, context, gate)

    def _step(
        self,
        context: dict[str, Any] | None,
        gate: threading.BoundedSemaphore | None,
    ) -> Any:
        if self._loop is None:
            self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        if gate is None:
            return self._runner.step(context)
        with gate:
            return self._runner.step(context)

    def close(self) -> None:
        """Close owned provider resources and the loop on their Actor-affine thread."""
        try:
            if self._loop is not None:
                self._executor.submit(self._close_on_worker).result()
        finally:
            self._executor.shutdown(wait=True)

    def _close_on_worker(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._runner.player.aclose())
        finally:
            asyncio.set_event_loop(None)
            self._loop.close()


class AnalysisPlanLike(Protocol):
    """What the runner needs from ``benchmark.analysis_plan.AnalysisPlan`` without importing it."""

    plan_id: str
    content: dict[str, Any]
    sha256: str


def _sealed_analysis_plan(plan: AnalysisPlanLike | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    return {
        "schema": str(plan.content.get("schema")),
        "plan_id": plan.plan_id,
        "sha256": plan.sha256,
        "content": json.loads(json.dumps(plan.content)),
    }


# Phase-specific bounded scheduling — see ``AutonomousGameRunner._sweep``.
_PHASE_DIPLOMACY = "diplomacy"
_PHASE_CONS = "cons"
# Bounded pool for concurrent Con activations; ``1`` is the historical serial sweep.
DEFAULT_CONS_CONCURRENCY = 4
DEFAULT_DIPLOMACY_CONCURRENCY = 4
DEFAULT_DIPLOMACY_MODE = DIPLOMACY_MODE_OPEN
DEFAULT_CON_MODE = CON_MODE_OPEN
DEFAULT_CON_REPLY_MODE = CON_REPLY_MODE_SEALED  # I-046
# Which declared condition a resume mismatch names (I-053 / I-055 / I-046).
_CONDITION_LABELS = {
    "diplomacy_mode": "Diplomacy",
    "diplomacy_rounds_per_turn": "Diplomacy",
    "con_mode": "Con",
    "con_challengers_per_argument": "Con",
    "con_reply_mode": "Con-reply",
    "reply_max_points": "Con-reply",
    "consequence_mode": "Consequence",
}


def expected_manifest_configuration(
    config: PlayerConfig,
    *,
    benchmark: dict[str, Any] | None = None,
    run_configuration: Mapping[str, Any] | None = None,
    analysis_plan: AnalysisPlanLike | None = None,
    max_cycles: int = 1000,
    cons_concurrency: int = DEFAULT_CONS_CONCURRENCY,
    diplomacy_concurrency: int = DEFAULT_DIPLOMACY_CONCURRENCY,
    condition: GameLoopCondition | None = None,
    transport: str = IN_PROCESS_TRANSPORT,
) -> dict[str, Any]:
    """The manifest configuration an :class:`AutonomousGameRunner` built with these arguments
    will submit for a game whose Seats all run ``config`` (I-078).

    Assignment-dependent fields (``player.assignments``, ``resume.player.assignments`` and the
    ``benchmark`` assignment identity) are left empty: a batch declares them per attempt. The
    Umpire section is added by the server from its own provenance, not by the runner.
    """
    condition = condition or GameLoopCondition(
        diplomacy_mode=DEFAULT_DIPLOMACY_MODE,
        diplomacy_rounds_per_turn=None,
        con_mode=DEFAULT_CON_MODE,
        con_challengers_per_argument=None,
        con_reply_mode=DEFAULT_CON_REPLY_MODE,
        reply_max_points=None,
    )
    condition.validate()
    return _manifest_configuration_for(
        config,
        assignments={},
        benchmark=dict(benchmark or {}),
        run_configuration=dict(run_configuration or {}),
        analysis_plan=_sealed_analysis_plan(analysis_plan),
        max_cycles=max_cycles,
        cons_concurrency=cons_concurrency,
        diplomacy_concurrency=diplomacy_concurrency,
        condition=condition,
        transport=transport,
    )


class AutonomousGameError(RuntimeError):
    """The autonomous run could not safely reach a completed game."""


class AutonomousGameStalled(AutonomousGameError):
    """No Actor or server transition could make authoritative progress."""


@dataclass(frozen=True)
class ActivationTrace:
    """Inspectable, chain-of-thought-free provenance for one successful activation."""

    activation_id: str
    actor_id: str
    turn: int | None
    phase: str | None
    model_requested: str
    model_returned: str | None
    usage: dict[str, Any] | None
    latency_seconds: float
    activation_wall_seconds: float
    provider_wait_seconds: float
    model_iterations: int
    tool_call_names: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class AutonomousRunResult:
    """The completed export plus ephemeral run evidence retained by the coordinator."""

    game_id: str
    seat_tokens: dict[str, str]
    export: dict[str, Any]
    activations: list[ActivationTrace]
    failures: list[AgentFailure]
    activation_counts: dict[str, int]
    cycles: int
    summary: dict[str, Any]
    summary_text: str


@dataclass
class _GameLedger:
    """Every outcome that reached the coordinator for one game, recorded as it arrived (I-081).

    The durable half is the ``AgentRun`` row the coordinator writes through the admin API the
    moment a step's outcome reaches this thread — before the sweep it belongs to returns or
    raises. So a sibling's unexpected failure, a settlement rejection or an admin call that fails
    later in the same sweep can never leave committed Con/Message state without its provenance
    (agents.md §18 "Every failed activation is recorded", testing.md §20). The in-memory half is
    the run evidence handed back in :class:`AutonomousRunResult`.
    """

    game_id: str
    traces: list[ActivationTrace] = field(default_factory=list)
    failures: list[AgentFailure] = field(default_factory=list)
    activation_counts: dict[str, int] = field(default_factory=dict)


class AutonomousGameRunner:
    """Create, start and autonomously drive one scenario to completion over HTTP."""

    def __init__(
        self,
        client: HTTPClient,
        *,
        admin_token: str,
        scenario: str | Path,
        player_factory: PlayerFactory,
        seed: int | None = None,
        max_cycles: int = 1000,
        stream_game_details: bool = False,
        run_configuration: Mapping[str, Any] | None = None,
        cons_concurrency: int = DEFAULT_CONS_CONCURRENCY,
        diplomacy_concurrency: int = DEFAULT_DIPLOMACY_CONCURRENCY,
        diplomacy_mode: str = DEFAULT_DIPLOMACY_MODE,
        diplomacy_rounds_per_turn: int | None = None,
        con_mode: str = DEFAULT_CON_MODE,
        con_challengers_per_argument: int | None = None,
        con_reply_mode: str = DEFAULT_CON_REPLY_MODE,
        reply_max_points: int | None = None,
        consequence_mode: str = CONSEQUENCE_MODE_NONE,
        analysis_plan: AnalysisPlanLike | None = None,
        transport: str = IN_PROCESS_TRANSPORT,
    ) -> None:
        if max_cycles < 1:
            raise ValueError("max_cycles must be at least 1")
        if cons_concurrency < 1:
            raise ValueError("cons_concurrency must be at least 1")
        if diplomacy_concurrency < 1:
            raise ValueError("diplomacy_concurrency must be at least 1")
        # I-053 / I-055 / I-046: the runner declares the condition; the server persists it at
        # creation (K against the real Actor count is the server's check), the manifest records
        # it and the resume check compares it. One validator (I-060); a ValueError subclass.
        self._condition = GameLoopCondition(
            diplomacy_mode=diplomacy_mode,
            diplomacy_rounds_per_turn=diplomacy_rounds_per_turn,
            con_mode=con_mode,
            con_challengers_per_argument=con_challengers_per_argument,
            con_reply_mode=con_reply_mode,
            reply_max_points=reply_max_points,
            consequence_mode=consequence_mode,
        )
        self._condition.validate()
        self._cons_concurrency = cons_concurrency
        self._diplomacy_concurrency = diplomacy_concurrency
        # I-047: a predeclared analysis plan makes the run analytical; it is sealed into the
        # immutable manifest at creation so predeclaration is evidenced by the manifest, not by
        # the plan's own date. Without one the run is a playtest (D-054).
        self._analysis_plan = _sealed_analysis_plan(analysis_plan)
        self._client = client
        self._admin_token = admin_token
        self._scenario = str(scenario)
        self._player_factory = player_factory
        self._seed = seed
        self._max_cycles = max_cycles
        self._stream_game_details = stream_game_details
        self._run_configuration = dict(run_configuration or {})
        self._transport = transport
        self._seen_audit_records: dict[str, set[str]] = {}
        self._actor_workers: dict[str, _ActorWorker] = {}
        # The game this runner created, kept after a failed run so its authoritative export can
        # still be pulled for analysis (a paused game is the one worth reading — I-023).
        self.last_game_id: str | None = None

    def run(self) -> AutonomousRunResult:
        """Run with zero human transitions, returning only after authoritative completion."""
        # I-078: every autonomous game opts into the server-enforced pre-commitment — the
        # manifest is sealed before ``start`` below, and the server refuses to start without it.
        create_body: dict[str, Any] = {"scenario": self._scenario, "require_run_manifest": True}
        if self._seed is not None:
            create_body["seed"] = self._seed
        create_body.update(self._condition.as_dict())
        # The whole condition is always sent explicitly, so the server persists exactly what this
        # runner seals and never applies a scenario default the runner did not resolve. Callers
        # resolve scenario defaults first, through ``app.game_loop`` (D-091, D-099).
        create_body["consequence_mode"] = self._condition.consequence_mode
        created = self._admin_request("POST", "/admin/games", json=create_body)
        game_id = self._required_string(created, "game_id")
        self.last_game_id = game_id
        seat_tokens = created.get("seat_tokens")
        if not isinstance(seat_tokens, dict) or not seat_tokens:
            raise AutonomousGameError("admin create response omitted Seat assignments")

        seat_assignments: dict[str, str] = {}
        for actor_id, token in seat_tokens.items():
            if not isinstance(actor_id, str) or not isinstance(token, str):
                raise AutonomousGameError("admin create returned an invalid Seat assignment")
            seat_assignments[actor_id] = token
        runners = self._build_runners(game_id, seat_assignments)

        self._actor_workers = {
            actor_id: _ActorWorker(actor_id, runner) for actor_id, runner in runners.items()
        }
        try:
            return self._drive_game(game_id, seat_assignments, runners, initialize=True)
        finally:
            self._close_actor_workers()

    def resume(self, game_id: str) -> AutonomousRunResult:
        """Rotate credentials and drive one compatible persisted Game from current state."""
        descriptor = self._admin_request("GET", f"/admin/games/{game_id}/resume")
        manifest = descriptor.get("manifest")
        actors = descriptor.get("actors")
        if not isinstance(manifest, dict) or not isinstance(actors, dict) or not actors:
            raise AutonomousGameError("admin resume response omitted persisted provenance")
        recipe = manifest.get("resume")
        if recipe != self._run_configuration.get("resume"):
            raise AutonomousGameError("current autonomous configuration does not match resume recipe")
        if manifest.get("code_version") != code_version():
            raise AutonomousGameError("current code version does not match the interrupted run")
        # I-085: a manifest that recorded its runtime environment is resumed only under the
        # same one; a legacy manifest without the section is compared on the sections it has.
        recorded_environment = manifest.get("environment")
        if isinstance(recorded_environment, dict) and (
            recorded_environment.get("sha256")
            != runtime_fingerprint(transport=self._transport)["sha256"]
        ):
            raise AutonomousGameError(
                "current runtime environment does not match the interrupted run"
            )
        coordinator = manifest.get("coordinator") or {}
        if coordinator.get("cons_concurrency") != self._cons_concurrency or coordinator.get(
            "diplomacy_concurrency"
        ) != self._diplomacy_concurrency:
            raise AutonomousGameError("current coordinator settings do not match the interrupted run")
        persisted = GameLoopCondition.from_mapping(manifest.get("game_loop") or {})
        if persisted != self._condition:
            differing = sorted({
                _CONDITION_LABELS[name]
                for name in CONDITION_FIELDS
                if getattr(persisted, name) != getattr(self._condition, name)
            })
            raise AutonomousGameError(
                f"current {' / '.join(differing)} condition does not match the interrupted run"
            )
        # I-047: the sealed plan is part of the interrupted run's identity. A resume supplies
        # none and inherits it; supplying a different one is refused.
        sealed_plan = (manifest.get("benchmark") or {}).get("analysis_plan")
        if self._analysis_plan is None:
            self._analysis_plan = sealed_plan
        elif self._analysis_plan != sealed_plan:
            raise AutonomousGameError("current analysis plan does not match the interrupted run")

        rotated = self._admin_request(
            "POST", f"/admin/games/{game_id}/seat-tokens/rotate", json={}
        )
        tokens = rotated.get("seat_tokens")
        if not isinstance(tokens, dict) or set(tokens) != set(actors):
            raise AutonomousGameError("admin rotation response omitted Seat assignments")
        seat_tokens = {str(actor): str(token) for actor, token in tokens.items()}
        actor_names = {str(actor): str(name) for actor, name in actors.items()}
        runners = self._build_runners(game_id, seat_tokens, actor_names=actor_names)
        current = self._manifest_configuration(runners)
        for key in (
            "player", "game_loop", "code_version", "environment", "coordinator", "benchmark",
            "runtime", "resume",
        ):
            if key in manifest and current.get(key) != manifest.get(key):
                raise AutonomousGameError(
                    f"current {key} configuration does not match the interrupted run"
                )
        self.last_game_id = game_id
        self._actor_workers = {
            actor_id: _ActorWorker(actor_id, runner) for actor_id, runner in runners.items()
        }
        try:
            logger.info("autonomous game %s resumed with rotated Seat credentials", game_id)
            return self._drive_game(game_id, seat_tokens, runners, initialize=False)
        finally:
            self._close_actor_workers()

    def _build_runners(
        self,
        game_id: str,
        seat_tokens: dict[str, str],
        *,
        actor_names: dict[str, str] | None = None,
    ) -> dict[str, LifecycleRunner]:
        runners: dict[str, LifecycleRunner] = {}
        for actor_id, token in seat_tokens.items():
            tools = PlayerTools(self._client, game_id=game_id, token=token)
            actor_name = (actor_names or {}).get(actor_id)
            if actor_name is None:
                observed = tools.observe()
                if not observed.ok or observed.data is None:
                    raise AutonomousGameError(f"Actor {actor_id!r} could not observe the game")
                actor_name = str((observed.data.get("you") or {}).get("name") or actor_id)
            runners[actor_id] = self._player_factory(tools, actor_id, actor_name)
            logger.info(
                "autonomous actor=%s tools_offered=%s",
                actor_id,
                ",".join(sorted(runners[actor_id].player.tool_names)),
            )
        return runners

    def _drive_game(
        self,
        game_id: str,
        seat_tokens: dict[str, str],
        runners: dict[str, LifecycleRunner],
        *,
        initialize: bool,
    ) -> AutonomousRunResult:
        """Drive a created game while its Actor-affine model workers remain alive."""
        if initialize:
            self._admin_request(
                "POST",
                f"/admin/games/{game_id}/run-manifest",
                json={"configuration": self._manifest_configuration(runners)},
            )
            self._admin_request("POST", f"/admin/games/{game_id}/start", json={})
            logger.info("autonomous game %s started with %d Actors", game_id, len(runners))
        self._stream_new_records(game_id)

        ledger = _GameLedger(game_id, activation_counts={actor_id: 0 for actor_id in runners})
        actor_order = list(runners)
        last_position: tuple[Any, Any, Any] | None = None

        for cycle in range(1, self._max_cycles + 1):
            state = runners[actor_order[0]].observe()
            status = state.get("status")
            position = (state.get("turn"), state.get("phase"), state.get("active_actor_id"))
            sweep = sweep_order(actor_order, state.get("turn"))
            if position != last_position:
                logger.info(
                    "autonomous game %s position: turn=%s phase=%s active_actor=%s sweep=%s",
                    game_id,
                    *position,
                    ",".join(sweep),
                )
                last_position = position
            if status == "complete":
                exported = self._admin_request("GET", f"/admin/games/{game_id}/export")
                self._stream_new_records(game_id, exported=exported)
                logger.info(
                    "autonomous game %s completed after %d cycles and %d activations",
                    game_id,
                    cycle,
                    len(ledger.traces),
                )
                summary_response = self._admin_request(
                    "GET", f"/admin/games/{game_id}/summary"
                )
                summary_text = str(summary_response.pop("formatted"))
                summary = summary_response
                logger.info("autonomous game %s summary\n%s", game_id, summary_text)
                return AutonomousRunResult(
                    game_id=game_id,
                    seat_tokens=dict(seat_tokens),
                    export=exported,
                    activations=ledger.traces,
                    failures=ledger.failures,
                    activation_counts=ledger.activation_counts,
                    cycles=cycle,
                    summary=summary,
                    summary_text=summary_text,
                )
            if status != "running":
                raise AutonomousGameError(
                    f"game stopped before completion with status {status!r}"
                )

            # The server-owned boundaries: adjudication, and under ``bounded_effects_v1`` the
            # end-of-turn consequence pass (T-074). No Actor is activated in either (D-091).
            if state.get("phase") in ("adjudication", "consequences"):
                self._admin_request("POST", f"/admin/games/{game_id}/advance", json={})
                self._stream_new_records(game_id)
                continue

            made_progress = False
            # Every outcome the sweep hands back is already recorded (I-081): the judgement
            # below never precedes a sibling's provenance.
            for actor_id, outcome in self._sweep(runners, sweep, state.get("phase"), ledger):
                if outcome.activation is not None or outcome.failure is not None:
                    made_progress = True
                if outcome.failure is not None and outcome.stuck:
                    failure = outcome.failure
                    raise AutonomousGameStalled(
                        f"Actor {actor_id!r} failed with no legal recovery in "
                        f"{outcome.phase!r}: category={failure.category!r}; "
                        f"detail={failure.detail}; counts={failure.counts!r}"
                    )

            self._stream_new_records(game_id)

            if not made_progress:
                # A server-owned bounded-round close is itself progress even when every Actor
                # outcome reported no player mutation. Re-observe before declaring a stall.
                current = runners[actor_order[0]].observe()
                current_position = (
                    current.get("turn"), current.get("phase"), current.get("active_actor_id")
                )
                if current_position != position:
                    continue
                # A full sweep in a non-adjudication running phase with every Actor waiting means
                # there is no legal autonomous transition left.  Fail loudly instead of spinning.
                raise AutonomousGameStalled(
                    f"no Actor could progress game {game_id} in phase {state.get('phase')!r}"
                )

        raise AutonomousGameStalled(
            f"autonomous game {game_id} exceeded {self._max_cycles} coordinator cycles"
        )

    def _close_actor_workers(self) -> None:
        """Best-effort deterministic teardown without masking a run result or root failure."""
        workers, self._actor_workers = self._actor_workers, {}
        for actor_id, worker in workers.items():
            try:
                worker.close()
            except Exception:  # noqa: BLE001 - teardown is logged and must not mask run evidence
                logger.exception("failed to close model worker for Actor %s", actor_id)

    def _step_actor(
        self, actor_id: str, context: dict[str, Any] | None = None
    ) -> Any:
        """Run one Actor step on that Actor's persistent worker and event loop."""
        try:
            worker = self._actor_workers[actor_id]
        except KeyError as exc:  # pragma: no cover - internal construction invariant
            raise AutonomousGameError(f"no model worker configured for Actor {actor_id!r}") from exc
        return worker.submit(context).result()

    def _sweep(
        self,
        runners: dict[str, LifecycleRunner],
        sweep: list[str],
        phase: Any,
        ledger: _GameLedger,
    ) -> list[tuple[str, Any]]:
        """Step every runner once and return ``(actor_id, outcome)`` in sweep order.

        Every outcome returned is already recorded in ``ledger`` (I-081): each path below records
        an outcome the moment it reaches this thread — before the next Actor is observed, before
        a diplomacy round settles, and before a sibling's error is raised — so nothing that
        propagates out of a sweep can drop a completed sibling's provenance.

        Argument is stepped serially by the coordinator but still on the Actor's affine worker.
        **Cons is order-independent** for Actor
        input — a Con is read only by the Umpire (no player-facing Cons read exists), so each
        opponent judges the same Argument from the same context whether stepped one after another
        or at once. Diplomacy runs as explicit isolated rounds (I-028): all contexts and Message
        reads are captured before bounded workers start, readiness settles afterward, and only a
        private Message's recipients are eligible again next round. In Cons the steps run on a
        bounded pool (I-029); the server's guarded
        transactions keep the phase close single (state-machine §25, INV-GAME-005). Outcomes are
        still recorded and returned in sweep order, from this thread, so provenance and error
        handling are identical in both paths. A worker exception is re-raised here after every
        step has finished and every completed sibling is recorded, wrapped so the failing Actor
        is named.

        A serial sweep stops as soon as an Actor observes a phase other than the one this cycle
        was dispatched for (the arguer's Argument opened Cons, say): the coordinator re-observes
        and dispatches the new phase its own way, rather than letting the rest of the sweep take
        Con activations one at a time on this thread. Each Actor is observed once and that read is
        handed to ``step`` — no second request.
        """
        if phase != _PHASE_CONS or self._cons_concurrency == 1 or len(sweep) < 2:
            if phase == _PHASE_DIPLOMACY:
                return self._diplomacy_round(runners, sweep, ledger)
            results: list[tuple[str, Any]] = []
            for actor_id in sweep:
                context = runners[actor_id].observe()
                if context.get("phase") != phase or context.get("status") != "running":
                    break
                outcome = self._step_actor(actor_id, context)
                self._record_outcome(ledger, runners[actor_id], actor_id, outcome)
                results.append((actor_id, outcome))
            return results

        return self._concurrent_steps(
            sweep, runners, ledger, phase_label="Cons", concurrency=self._cons_concurrency,
        )

    def _diplomacy_round(
        self, runners: dict[str, LifecycleRunner], sweep: list[str], ledger: _GameLedger
    ) -> list[tuple[str, Any]]:
        """Capture, execute and settle one isolated diplomacy round (I-028).

        Every activation is recorded before the round settles (I-081): a deferred readiness
        the server refuses, or a failed ``…/diplomacy-rounds/complete`` call, propagates only
        after the Messages those activations committed have their provenance.
        """
        contexts: dict[str, dict[str, Any]] = {}
        eligible: list[str] = []
        prepared: list[str] = []
        try:
            # No worker exists until every Actor has been observed. Eligibility is therefore a
            # single logical snapshot even though the HTTP reads themselves are sequential.
            for actor_id in sweep:
                context = runners[actor_id].observe()
                contexts[actor_id] = context
                if (
                    context.get("phase") == _PHASE_DIPLOMACY
                    and context.get("status") == "running"
                    and context.get("requires_input")
                ):
                    eligible.append(actor_id)
            for actor_id in eligible:
                runners[actor_id].player.tools.prepare_diplomacy_round(contexts[actor_id])
                prepared.append(actor_id)

            if self._diplomacy_concurrency == 1 or len(eligible) < 2:
                results = []
                for actor_id in eligible:
                    outcome = self._step_actor(actor_id, contexts[actor_id])
                    self._record_outcome(ledger, runners[actor_id], actor_id, outcome)
                    results.append((actor_id, outcome))
            else:
                results = self._concurrent_steps(
                    eligible, runners, ledger, phase_label="Diplomacy",
                    concurrency=self._diplomacy_concurrency,
                    contexts=contexts,
                )

            reactivated: set[str] = set()
            for actor_id in eligible:
                _, recipients = runners[actor_id].player.tools.diplomacy_round_state()
                reactivated.update(recipients)
            for actor_id in eligible:
                tools = runners[actor_id].player.tools
                ready_deferred, _ = tools.diplomacy_round_state()
                settled = tools.settle_diplomacy_round(
                    commit_ready=ready_deferred and actor_id not in reactivated
                )
                prepared.remove(actor_id)
                if settled is not None and not settled.ok:
                    raise AutonomousGameError(
                        f"Actor {actor_id!r} deferred readiness failed: "
                        f"{settled.error_code or settled.http_status}"
                    )
            if eligible:
                diplomacy = contexts[eligible[0]].get("diplomacy") or {}
                self._admin_request(
                    "POST",
                    f"/admin/games/{contexts[eligible[0]]['game_id']}/diplomacy-rounds/complete",
                    json={
                        "turn": contexts[eligible[0]]["turn"],
                        "round_number": int(diplomacy.get("rounds_completed") or 0) + 1,
                    },
                )
            return results
        finally:
            for actor_id in prepared:
                runners[actor_id].player.tools.abort_diplomacy_round()

    def _concurrent_steps(
        self,
        actor_ids: list[str],
        runners: dict[str, LifecycleRunner],
        ledger: _GameLedger,
        *,
        phase_label: str,
        concurrency: int,
        contexts: dict[str, dict[str, Any]] | None = None,
    ) -> list[tuple[str, Any]]:
        """Run bounded actor-affine workers; record every completed sibling; raise the first error.

        Errors are surfaced in input order only after every worker has finished *and* every
        outcome that did complete is recorded (I-081). A worker exception is wrapped so the
        failing Actor is named; a failed provenance write is raised as the admin error it is.
        """
        gate = threading.BoundedSemaphore(min(concurrency, len(actor_ids)))
        futures = [
            (
                actor_id,
                self._actor_workers[actor_id].submit(
                    None if contexts is None else contexts[actor_id], gate
                ),
            )
            for actor_id in actor_ids
        ]
        results: list[tuple[str, Any]] = []
        first_error: tuple[str | None, BaseException] | None = None  # (failing Actor, error)
        for actor_id, future in futures:
            try:
                outcome = future.result()
            except Exception as exc:  # noqa: BLE001 - collected and named below
                if first_error is None:
                    first_error = (actor_id, exc)
                continue
            try:
                self._record_outcome(ledger, runners[actor_id], actor_id, outcome)
            except Exception as exc:  # noqa: BLE001 - the other siblings are still recorded
                if first_error is None:
                    first_error = (None, exc)
                continue
            results.append((actor_id, outcome))
        if first_error is not None:
            failed_actor, error = first_error
            if failed_actor is None:
                raise error  # a provenance write failed: already the admin error it is
            raise AutonomousGameError(
                f"Actor {failed_actor!r} failed during a concurrent {phase_label} activation: "
                f"{type(error).__name__}: {error}"
            ) from error
        return results

    def _record_outcome(
        self, ledger: _GameLedger, runner: LifecycleRunner, actor_id: str, outcome: Any
    ) -> None:
        """Record one step's activation/failure provenance, durably and in the ledger (I-081).

        Called on the coordinator thread the moment an outcome reaches it. It records; it does
        not judge — whether a ``stuck`` outcome stalls the game is decided by the drive loop only
        after the whole sweep is recorded.
        """
        game_id = ledger.game_id
        if outcome.activation is not None:
            activation = outcome.activation
            ledger.activation_counts[actor_id] += 1
            ledger.traces.append(
                ActivationTrace(
                    activation_id=activation.activation_id,
                    actor_id=actor_id,
                    turn=outcome.context.get("turn"),
                    phase=outcome.phase,
                    model_requested=activation.model_requested,
                    model_returned=activation.model_returned,
                    usage=activation.usage,
                    latency_seconds=activation.latency_seconds,
                    activation_wall_seconds=activation.activation_wall_seconds,
                    provider_wait_seconds=activation.provider_wait_seconds,
                    model_iterations=activation.model_iterations,
                    tool_call_names=list(activation.tool_call_names),
                )
            )
            self._record_activation(game_id, runner, outcome)
            logger.info(
                "autonomous activation actor=%s turn=%s phase=%s tool_calls=%s",
                actor_id,
                outcome.context.get("turn"),
                outcome.phase,
                ",".join(activation.tool_call_names) or "none",
            )
        if outcome.failure is not None:
            ledger.failures.append(outcome.failure)
            self._record_failure(game_id, runner, outcome)
            logger.warning(
                "autonomous failure actor=%s turn=%s phase=%s category=%s recovery=%s",
                actor_id,
                outcome.context.get("turn"),
                outcome.phase,
                outcome.failure.category,
                outcome.failure.recovery_action or "none",
            )

    def _manifest_configuration(self, runners: dict[str, LifecycleRunner]) -> dict[str, Any]:
        players = [runner.player for runner in runners.values()]
        first_config = players[0].config
        # One harness, one prompt, one budget, one inference profile per game (I-031 / I-032 /
        # agents.md §19): the manifest seals the *first* player's configuration for the whole
        # roster, so every field but the model must agree — the complete canonical projection,
        # not a hand-picked subset (I-083). A mixed roster is a configuration error, not data.
        shared = first_config.shared_condition()
        differing = sorted({
            name
            for player in players[1:]
            for name, value in player.config.shared_condition().items()
            if value != shared[name]
        })
        if differing:
            raise AutonomousGameError(
                "reference players do not share one player condition; differing: "
                f"{', '.join(differing)}"
            )
        # I-046: the players' prompt and schema follow the game's Con-reply condition; a player
        # built for the other condition would be told the wrong visibility rule.
        if first_config.con_reply_mode != self._condition.con_reply_mode:
            raise AutonomousGameError(
                f"reference players are configured for con_reply_mode "
                f"{first_config.con_reply_mode!r} but the game runs "
                f"{self._condition.con_reply_mode!r}"
            )
        return _manifest_configuration_for(
            first_config,
            assignments={
                actor_id: runner.player.config.model for actor_id, runner in runners.items()
            },
            benchmark=self._run_configuration.get("benchmark") or {},
            run_configuration=self._run_configuration,
            analysis_plan=self._analysis_plan,
            max_cycles=self._max_cycles,
            cons_concurrency=self._cons_concurrency,
            diplomacy_concurrency=self._diplomacy_concurrency,
            condition=self._condition,
            transport=self._transport,
        )

    @staticmethod
    def _framework_version() -> str | None:
        return _framework_version()

    @staticmethod
    def _cost(usage: dict[str, Any]) -> float | None:
        candidates = [usage.get("cost_usd"), usage.get("cost")]
        details = usage.get("details")
        if isinstance(details, dict):
            candidates.extend([details.get("cost_usd"), details.get("cost")])
        return next(
            (
                float(value)
                for value in candidates
                if isinstance(value, (int, float)) and value >= 0
            ),
            None,
        )

    def _agent_run_body(
        self, runner: LifecycleRunner, *, phase: str | None, usage: dict[str, Any]
    ) -> dict[str, Any]:
        """The AgentRun fields every player record shares (I-061, review F5): who and under
        which fixed condition, plus the token and cost accounting from ``usage``. The two
        callers overlay what differs — identity of the attempt, its outcome and its counts."""
        config = runner.player.config
        inference = config.resolve_inference(phase)
        return {
            "actor_id": runner.player.actor_id,
            "role": "player",
            "phase": phase,
            "prompt_version": runner.player.prompt_version,
            "prompt_hash": runner.player.prompt_hash,
            "model_parameters": {
                "temperature": config.temperature,
                # I-032: the phase-resolved budget this activation actually requested.
                "max_tokens": inference["max_tokens"],
                "inference_profile": config.inference_profile.name,
                "timeout": config.timeout,
                "activation_mode": config.activation_mode,
                "research_mode": config.research_mode,
                "search_contract": config.search_contract,
                "research_history_mode": config.research_history_mode,
                "tool_surface": config.tool_surface,
                **self._condition.as_dict(),
                "guardrails": dataclasses.asdict(config.guardrails),
            },
            "reasoning_configuration": (
                {"effort": inference["reasoning_effort"]}
                if inference["reasoning_effort"] is not None else None
            ),
            "agent_framework": "pydantic-ai",
            "tool_schema_version": runner.player.tool_schema_version,
            "context_mode": PLAYER_CONTEXT_MODE,
            "memory_version": PLAYER_MEMORY_VERSION,
            "input_tokens": int(usage.get("input_tokens") or 0),
            "output_tokens": int(usage.get("output_tokens") or 0),
            "reasoning_tokens": int(usage.get("reasoning_tokens") or 0),
            "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
            "cache_write_tokens": int(usage.get("cache_write_tokens") or 0),
            "costed_model_calls": (
                int(usage.get("billed_requests") or 0)
                if self._cost(usage) is not None else 0
            ),
            "cost_usd": self._cost(usage),
        }

    def _record_activation(self, game_id: str, runner: LifecycleRunner, outcome: Any) -> None:
        activation = outcome.activation
        assert activation is not None
        usage = dict(activation.usage or {})
        self._admin_request(
            "POST",
            f"/admin/games/{game_id}/agent-runs",
            json={
                **self._agent_run_body(runner, phase=outcome.phase, usage=usage),
                "activation_id": activation.activation_id,
                "turn": outcome.context.get("turn"),
                "model_requested": activation.model_requested,
                "model_returned": activation.model_returned,
                "provider": activation.provider,
                "agent_framework_version": self._framework_version(),
                "model_calls": activation.model_iterations,
                "tool_calls": len(activation.tool_call_names),
                "research_tool_calls": activation.research_tool_calls,
                "research_operations": activation.research_operations,
                "invalid_tool_calls": activation.invalid_tool_calls,
                "invalid_tool_call_breakdown": activation.invalid_tool_call_breakdown,
                "request_topology": activation.request_topology,
                "stalls": 0,
                "latency_ms": round(activation.provider_wait_seconds * 1000),
                "activation_wall_ms": round(activation.activation_wall_seconds * 1000),
                "completed": True,
                "failure_reason": None,
                "provider_metadata": {"usage": usage},
            },
        )

    def _record_failure(self, game_id: str, runner: LifecycleRunner, outcome: Any) -> None:
        failure = outcome.failure
        assert failure is not None
        usage = failure.counts.get("usage") or {}
        self._admin_request(
            "POST",
            f"/admin/games/{game_id}/agent-runs",
            json={
                **self._agent_run_body(runner, phase=failure.phase, usage=usage),
                "activation_id": failure.counts.get("activation_id"),
                "turn": failure.turn,
                "model_requested": runner.player.config.model,
                "model_returned": None,
                "provider": None,
                # Recorded as ``None`` on failure since T-053; kept as-is — the framework
                # version is a property of the run, and the manifest carries it.
                "agent_framework_version": None,
                "model_calls": int(failure.counts.get("model_calls") or 0),
                "tool_calls": int(failure.counts.get("tool_calls") or 0),
                "research_tool_calls": failure.counts.get("research_tool_calls"),
                "research_operations": failure.counts.get("research_operations"),
                "invalid_tool_calls": 0,
                "invalid_tool_call_breakdown": {},
                "request_topology": failure.counts.get("request_topology", {}),
                "stalls": 1 if failure.category == "stall" else 0,
                "latency_ms": failure.counts.get("provider_wait_ms"),
                "activation_wall_ms": failure.counts.get("activation_wall_ms"),
                "completed": False,
                "failure_reason": failure.detail,
                "provider_metadata": {
                    "category": failure.category,
                    "counts": {
                        name: value
                        for name, value in failure.counts.items()
                        if name not in {"usage", "request_topology"}
                    },
                    "usage": usage,
                    # The legal completion the runner took for this failure, so a recovery pass
                    # is never read as the Actor's own choice (I-150, D-105).
                    "recovery": (
                        {
                            "action": failure.recovery_action,
                            "argument_id": failure.recovery_argument_id,
                        }
                        if failure.recovery_action is not None
                        else None
                    ),
                },
            },
        )

    def _stream_new_records(
        self, game_id: str, *, exported: dict[str, Any] | None = None
    ) -> None:
        """Emit an opt-in stream of newly persisted, credential-free audit records.

        Static Actor briefs/objectives and Resource contents stay out of operational logs; the
        administrative export remains the complete sensitive research record. This stream carries
        the gameplay history, event clock, information-seeking activity and model-call metadata
        needed to diagnose an unattended run without logging credentials or chain-of-thought.
        """
        if not self._stream_game_details:
            return
        snapshot = exported or self._admin_request("GET", f"/admin/games/{game_id}/export")
        identity_fields = {
            "messages": "id",
            "arguments": "id",
            "cons": "id",
            "adjudications": "argument_id",
            "effects": "id",
            "notes": "id",
            "events": "id",
            "resource_access": "id",
            "agent_runs": "id",
        }
        for collection, identity_field in identity_fields.items():
            seen = self._seen_audit_records.setdefault(collection, set())
            rows = snapshot.get(collection, [])
            if not isinstance(rows, list):
                continue
            for row in rows:
                if not isinstance(row, dict):
                    continue
                identity = str(row.get(identity_field))
                if identity in seen:
                    continue
                seen.add(identity)
                logger.info(
                    "autonomous record kind=%s data=%s",
                    collection.rstrip("s"),
                    json.dumps(row, sort_keys=True, default=str),
                )
        manifest = snapshot.get("run_manifest")
        if isinstance(manifest, dict):
            seen = self._seen_audit_records.setdefault("run_manifest", set())
            digest = str(manifest.get("manifest_sha256"))
            if digest not in seen:
                seen.add(digest)
                logger.info(
                    "autonomous record kind=run_manifest data=%s",
                    json.dumps(manifest, sort_keys=True, default=str),
                )

    def _admin_request(self, method: str, path: str, *, json: object = None) -> dict[str, Any]:
        response = self._client.request(
            method,
            path,
            headers={"Authorization": f"Bearer {self._admin_token}"},
            json=json,
        )
        try:
            body = response.json()
        except ValueError as exc:
            raise AutonomousGameError(
                f"admin {method} {path} returned non-JSON status {response.status_code}"
            ) from exc
        if not response.is_success:
            error = body.get("error") if isinstance(body, dict) else None
            code = error.get("code") if isinstance(error, dict) else "unknown_error"
            message = error.get("message") if isinstance(error, dict) else "admin request failed"
            raise AutonomousGameError(
                f"admin {method} {path} failed ({response.status_code} {code}): {message}"
            )
        if not isinstance(body, dict):
            raise AutonomousGameError(f"admin {method} {path} returned a non-object response")
        return body

    @staticmethod
    def _required_string(body: dict[str, Any], key: str) -> str:
        value = body.get(key)
        if not isinstance(value, str) or not value:
            raise AutonomousGameError(f"admin response omitted {key}")
        return value
