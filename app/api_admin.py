"""The administrative HTTP surface: orchestration (api.md §14) and the game export (§15).

Every route authorises with the separate admin token through :func:`app.api.require_admin`; a
Seat token never authorizes one (api.md §2.4). These routes may return private data with
ownership metadata, which is why they live apart from the Actor-facing routes in
:mod:`app.api_player`. They are registered by :func:`app.api.create_app`.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from app import export as export_module
from app import game_service, provenance, resumption, scenarios, summary
from app.api import (
    AdjudicatorUnavailable,
    ConsequenceResolverMissing,
    InvalidRequest,
    StateConflict,
    ValidationFailed,
    common_response_fields,
    get_request_engine,
    require_admin,
)
from app.database import session_scope
from app.models import Game
from app.consequences import (
    ConsequenceProvenanceSource,
    ConsequenceResolution,
    ConsequenceResolver,
    RecordedConsequenceResolver,
)
from app.umpire import RecordedUmpire, Umpire, UmpireAdjudication, UmpireProvenanceSource


def _record_resolution_attempts(
    engine: Engine,
    game_id: uuid.UUID,
    result: UmpireAdjudication | ConsequenceResolution | None,
    error: Exception | None,
    *,
    role: str,
    turn: int | None,
    phase: str,
) -> None:
    """Persist one server-side resolution's provider attempts as ``AgentRun`` rows.

    The Umpire and the consequence resolver emit already-normalised attempt records, owned by
    the request that made them (I-065, I-081). The server adds only what it alone knows: the
    game context, and an overlay when its *own* resolution failed after the provider returned a
    usable proposal. That overlay marks the attempt incomplete with the resolver's diagnosis, or
    the server error's type. An attempt that failed at the provider already carries its exact
    diagnosis and is recorded as it is. ``result`` is ``None`` when the configured resolver has
    no provenance capability; nothing is recorded then.
    """
    for record in result.attempts if result is not None else ():
        if error is not None and result is not None and result.proposal is not None:
            record = {
                **record,
                "completed": False,
                "failure_reason": result.failure or type(error).__name__,
            }
        provenance.record_agent_run(
            engine,
            game_id,
            actor_id=None,
            role=role,
            turn=turn,
            phase=phase,
            **record,
        )


class _CreateGameIn(BaseModel):
    """Body of POST /admin/games (api.md §14). ``scenario`` is the scenario package directory;
    an optional ``seed`` overrides the scenario's own seed.

    A condition field that is omitted or ``null`` was not supplied: creation falls back to the
    scenario's ``game.*`` default and then the released default, and persists the resolved value
    (D-091 for ``consequence_mode``, D-099 for the Diplomacy and Con fields)."""

    scenario: str
    seed: int | None = None
    diplomacy_mode: str | None = None
    diplomacy_rounds_per_turn: int | None = None
    con_mode: str | None = None
    con_challengers_per_argument: int | None = None
    con_reply_mode: str | None = None
    reply_max_points: int | None = None
    consequence_mode: str | None = None
    # I-078: opt the Game into the server-enforced pre-commitment (see ``Game``).
    require_run_manifest: bool = False


class _DiplomacyRoundIn(BaseModel):
    turn: int
    round_number: int


class _RunManifestIn(BaseModel):
    configuration: dict[str, Any]


class _AgentRunIn(BaseModel):
    activation_id: str | None = None
    actor_id: str | None = None
    role: str
    turn: int | None = None
    phase: str | None = None
    model_requested: str
    model_returned: str | None = None
    provider: str | None = None
    prompt_version: str
    prompt_hash: str
    model_parameters: dict[str, Any] = Field(default_factory=dict)
    reasoning_configuration: dict[str, Any] | None = None
    agent_framework: str
    agent_framework_version: str | None = None
    tool_schema_version: str
    context_mode: str | None = None
    memory_version: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    model_calls: int = 1
    costed_model_calls: int | None = None
    tool_calls: int = 0
    research_tool_calls: int | None = None
    research_operations: int | None = None
    invalid_tool_calls: int = 0
    invalid_tool_call_breakdown: dict[str, Any] = Field(default_factory=dict)
    request_topology: dict[str, Any] = Field(default_factory=dict)
    stalls: int = 0
    cost_usd: float | None = None
    latency_ms: int | None = None
    activation_wall_ms: int | None = None
    completed: bool = True
    failure_reason: str | None = None
    provider_metadata: dict[str, Any] = Field(default_factory=dict)


def register_admin_routes(app: FastAPI) -> None:
    """The private admin/orchestration surface (api.md §14) and the game export (§15).

    Every route authorises through :func:`require_admin` (the *separate* admin token, api.md §2.4)
    — a Seat token can never authorize any of them (TEST-AUTH-004). The orchestration routes are
    thin wrappers over :mod:`app.game_service` / :mod:`app.scenarios`: they add HTTP + admin auth
    and never re-implement a transition (AGENTS.md §6, API-INV-008). The export is the unfiltered
    administrative view built by :mod:`app.export` — it deliberately includes private data and is
    **never** routed through :mod:`app.visibility`. ``pause``/``end`` (api.md §14) are deferred:
    no ``pause_game``/``end_game`` transition exists yet (see DECISIONS.md).
    """

    @app.get("/admin/health")
    def _admin_health(_: Annotated[None, Depends(require_admin)]) -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/admin/games", status_code=201)
    def _admin_create_game(
        body: _CreateGameIn,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        # Create a LOBBY game from a scenario package. The raw Seat tokens are returned here
        # out-of-band exactly once (INV-ACTOR-001) — this is the only place a token is disclosed;
        # only their hash is persisted, and the export never contains them.
        try:
            scenario = scenarios.load_scenario(body.scenario)
        except scenarios.ScenarioValidationError as exc:
            raise ValidationFailed("scenario failed validation", {"errors": exc.errors}) from exc
        except scenarios.ScenarioError as exc:
            raise InvalidRequest(f"could not load scenario: {exc}") from exc
        try:
            creation = scenarios.create_game_from_scenario(
                engine,
                scenario,
                seed=body.seed,
                diplomacy_mode=body.diplomacy_mode,
                diplomacy_rounds_per_turn=body.diplomacy_rounds_per_turn,
                con_mode=body.con_mode,
                con_challengers_per_argument=body.con_challengers_per_argument,
                con_reply_mode=body.con_reply_mode,
                reply_max_points=body.reply_max_points,
                consequence_mode=body.consequence_mode,
                require_run_manifest=body.require_run_manifest,
            )
        except scenarios.GameCreationError as exc:
            raise InvalidRequest(str(exc)) from exc
        with session_scope(engine) as session:
            game = session.get(Game, creation.game_id)
            # ``create_game`` committed this row before returning its id, so it is present here.
            if game is None:
                raise RuntimeError(f"created game {creation.game_id} is missing")
            fields = common_response_fields(game)
            fields["status"] = game.status.value
        return {**fields, "seat_tokens": creation.seat_tokens}

    @app.post("/admin/games/{game_id}/start")
    def _admin_start_game(
        game_id: uuid.UUID,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        result = game_service.start_game(engine, game_id)  # the unchanged §14/§15 transition
        return {
            "game_id": str(game_id),
            "turn": result.turn,
            "phase": result.phase.value,
            "status": result.status.value,
            "active_actor_id": result.active_actor_id,
        }

    @app.post("/admin/games/{game_id}/diplomacy-rounds/complete")
    def _admin_complete_diplomacy_round(
        game_id: uuid.UUID,
        body: _DiplomacyRoundIn,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        result = game_service.complete_diplomacy_round(
            engine,
            game_id,
            turn=body.turn,
            round_number=body.round_number,
        )
        return {
            "game_id": str(game_id),
            "turn": result.turn,
            "round_number": result.round_number,
            "rounds_completed": result.rounds_completed,
            "phase": result.phase.value,
            "closed": result.closed,
            "close_reason": result.close_reason,
        }

    @app.post("/admin/games/{game_id}/run-manifest", status_code=201)
    def _admin_create_run_manifest(
        game_id: uuid.UUID,
        body: _RunManifestIn,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        request: Request,
    ) -> dict[str, Any]:
        configuration = dict(body.configuration)
        if "umpire" not in configuration:
            # I-065: provenance is one atomic optional Umpire capability (app.umpire).
            umpire = request.app.state.umpire
            if not isinstance(umpire, UmpireProvenanceSource):
                raise InvalidRequest("run configuration omitted Umpire provenance")
            configuration["umpire"] = umpire.provenance_configuration()
        resolver = getattr(request.app.state, "consequence_resolver", None)
        if "consequence" not in configuration and isinstance(
            resolver, ConsequenceProvenanceSource
        ):
            # T-074 / D-091: seal the resolver identity only for a game that runs the pass, so a
            # ``none`` game's manifest keeps the released shape.
            with session_scope(engine) as session:
                target = session.get(Game, game_id)
                runs_pass = target is not None and target.consequence_mode not in (None, "none")
            if runs_pass:
                configuration["consequence"] = resolver.provenance_configuration()
        recipe = configuration.get("resume")
        umpire_configuration = configuration.get("umpire")
        # Without an Umpire section there is nothing to derive from; the manifest validator
        # refuses the missing section itself.
        if (
            isinstance(recipe, dict)
            and "umpire" not in recipe
            and isinstance(umpire_configuration, dict)
        ):
            recipe = dict(recipe)
            try:
                recipe["umpire"] = provenance.resume_umpire_from_provenance(umpire_configuration)
            except provenance.ProvenanceError as exc:
                raise InvalidRequest(str(exc)) from exc
            configuration["resume"] = recipe
        try:
            record = provenance.create_run_manifest(
                engine, game_id, configuration=configuration
            )
        except provenance.ManifestConflict as exc:
            raise StateConflict(str(exc)) from exc
        except provenance.ProvenanceError as exc:
            raise InvalidRequest(str(exc)) from exc
        return {
            "game_id": str(game_id),
            "manifest_sha256": record.manifest_sha256,
            "created_at": record.created_at.isoformat(),
        }

    @app.get("/admin/games/{game_id}/resume")
    def _admin_describe_resume(
        game_id: uuid.UUID,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        try:
            descriptor = resumption.describe(engine, game_id)
        except resumption.ResumeError as exc:
            raise StateConflict(str(exc)) from exc
        return {
            "game_id": str(descriptor.game_id),
            "status": descriptor.status.value,
            "actors": descriptor.actors,
            "manifest": descriptor.manifest,
            "manifest_sha256": descriptor.manifest_sha256,
        }

    @app.post("/admin/games/{game_id}/seat-tokens/rotate")
    def _admin_rotate_seat_tokens(
        game_id: uuid.UUID,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        try:
            tokens = resumption.rotate_seat_tokens(engine, game_id)
        except resumption.ResumeError as exc:
            raise StateConflict(str(exc)) from exc
        return {"game_id": str(game_id), "seat_tokens": tokens}

    @app.post("/admin/games/{game_id}/agent-runs", status_code=201)
    def _admin_record_agent_run(
        game_id: uuid.UUID,
        body: _AgentRunIn,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        try:
            record = provenance.record_agent_run(
                engine, game_id, **body.model_dump(exclude_none=True)
            )
        except provenance.ProvenanceError as exc:
            raise InvalidRequest(str(exc)) from exc
        return {"game_id": str(game_id), "agent_run_id": record.id}

    @app.get("/admin/games/{game_id}/summary")
    def _admin_game_summary(
        game_id: uuid.UUID,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        report = summary.build_game_summary(engine, game_id)
        return {**report, "formatted": summary.format_game_summary(report)}

    @app.post("/admin/games/{game_id}/advance")
    def _admin_advance_game(
        game_id: uuid.UUID,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
        request: Request,
    ) -> dict[str, Any]:
        # The one server-owned advance step is the post-cons adjudication boundary. If the current
        # Argument is not yet resolved, resolve it first with the injected Umpire (server-owned
        # RNG), then advance — both are the existing service transitions, unchanged. No separate
        # ``resolve`` route: the §14 surface stays exactly as documented.
        if game_service.adjudication_pending(engine, game_id):
            umpire: Umpire | None = getattr(request.app.state, "umpire", None)
            if umpire is None:
                raise AdjudicatorUnavailable("no Umpire configured to resolve this game")
            with session_scope(engine) as session:
                pending_game = session.get(Game, game_id)
                turn = pending_game.turn if pending_game is not None else None
                phase = pending_game.phase.value if pending_game is not None else "adjudication"
            # I-081: the adjudication this request makes owns its provenance. The request-local
            # adapter hands the engine the plain ``Umpire`` contract and keeps the correlated
            # ``UmpireAdjudication`` — never a queue on the shared app-level Umpire that a
            # concurrent advance on another game could drain. An Umpire without the
            # capability records nothing.
            recorded = (
                RecordedUmpire(umpire) if isinstance(umpire, UmpireProvenanceSource) else None
            )
            resolution_error: Exception | None = None
            try:
                game_service.resolve_adjudication(
                    engine, game_id, umpire if recorded is None else recorded
                )
            except Exception as exc:
                resolution_error = exc
                raise
            finally:
                _record_resolution_attempts(
                    engine, game_id, recorded.result if recorded is not None else None,
                    resolution_error, role="umpire", turn=turn, phase=phase,
                )
        elif game_service.consequence_pending(engine, game_id):
            # T-074 / D-091 (state-machine §21 Option B): the turn's one consequence pass, run by
            # the injected resolver with the same invocation-owned provenance as the Umpire.
            resolver: ConsequenceResolver | None = getattr(
                request.app.state, "consequence_resolver", None
            )
            if resolver is None:
                raise ConsequenceResolverMissing(
                    "no consequence resolver configured to resolve this game"
                )
            with session_scope(engine) as session:
                pending_game = session.get(Game, game_id)
                turn = pending_game.turn if pending_game is not None else None
            recorded_pass = (
                RecordedConsequenceResolver(resolver)
                if isinstance(resolver, ConsequenceProvenanceSource)
                else None
            )
            pass_error: Exception | None = None
            try:
                game_service.resolve_consequences(
                    engine, game_id, resolver if recorded_pass is None else recorded_pass
                )
            except Exception as exc:
                pass_error = exc
                raise
            finally:
                _record_resolution_attempts(
                    engine, game_id, recorded_pass.result if recorded_pass is not None else None,
                    pass_error, role="consequence", turn=turn, phase="consequences",
                )
        result = game_service.advance_after_resolution(engine, game_id)
        return {
            "game_id": str(game_id),
            "turn": result.turn,
            "phase": result.phase.value,
            "status": result.status.value,
            "active_actor_id": result.active_actor_id,
            "turn_completed": result.turn_completed,
            "game_completed": result.game_completed,
        }

    @app.get("/admin/games/{game_id}/export")
    def _admin_export_game(
        game_id: uuid.UUID,
        _: Annotated[None, Depends(require_admin)],
        engine: Annotated[Engine, Depends(get_request_engine)],
    ) -> dict[str, Any]:
        # The complete authoritative record, unfiltered (never through app.visibility). Read-only:
        # it mutates no state (API-INV-008). An absent game → GameNotFound → 404 (domain handler).
        return export_module.build_game_export(engine, game_id)
