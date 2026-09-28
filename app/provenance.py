"""Immutable run provenance: the manifest and the append-only ``AgentRun`` ledger (TRD §28).

An observational layer the authoritative engine never depends on. It owns two records and their
validation: one content-hashed, byte-equivalent-retry-only :class:`~app.models.RunManifest` per
Game saying exactly what configuration ran, and one :class:`~app.models.AgentRun` row per player
activation or Umpire provider completion/failure (D-039). Rows are appended, never updated;
credential-shaped keys are refused at the boundary (``is_sensitive_key``), and request-topology and
invalid-tool-call breakdowns are validated before they are persisted (I-052, I-056).

Nothing here derives totals. Reports built *from* these rows live in :mod:`app.summary`, which
imports this module and never the reverse.
"""

from __future__ import annotations

import json
import uuid
from collections import Counter
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import col, select

from amaw_contracts import (
    INVALID_TOOL_CALL_CATEGORIES,
    INVALID_TOOL_CALL_REASONS,
    INVALID_TOOL_CALL_SCHEMA_VERSION,
    REQUEST_TOPOLOGY_COMPONENTS,
    REQUEST_TOPOLOGY_SCHEMA_VERSION,
    UmpireRecipe,
    canonical_json,
    canonical_sha256,
    first_non_json_scalar,
    is_sensitive_key,
)
from app.database import session_scope, write_session_scope
from app.game_loop import ARGUMENT_SEQUENCING_ROUND_ROBIN
from app.game_service import GameNotFound
from app.models import Actor, AgentRun, Game, GameStatus, RunManifest


# T-074 / D-091: ``consequence`` records one consequence-resolver model attempt.
ROLES = {"player", "umpire", "consequence"}
_REQUIRED_CONFIGURATION = {"player", "umpire", "code_version", "benchmark"}


class ProvenanceError(ValueError):
    """A provenance record is invalid or conflicts with already-persisted history."""


class ManifestConflict(ProvenanceError):
    """A Game already owns a different immutable run manifest."""


def _validate_invalid_tool_call_breakdown(
    breakdown: Any, *, invalid_tool_calls: int
) -> None:
    """Validate the content-free v1 ledger and reconcile it to the compatible aggregate."""
    if breakdown == {}:
        return  # A legacy/unclassified aggregate is explicitly representable.
    if not isinstance(breakdown, dict):
        raise ProvenanceError("invalid_tool_call_breakdown must be an object")
    expected_keys = {
        "schema_version", "total", "counts_by_category", "counts_by_reason", "events"
    }
    if set(breakdown) != expected_keys:
        raise ProvenanceError("invalid_tool_call_breakdown has an invalid shape")
    if breakdown["schema_version"] != INVALID_TOOL_CALL_SCHEMA_VERSION:
        raise ProvenanceError("invalid_tool_call_breakdown has an unsupported schema_version")
    events = breakdown["events"]
    if not isinstance(events, list):
        raise ProvenanceError("invalid_tool_call_breakdown.events must be a list")
    event_keys = {
        "sequence", "category", "reason", "action", "error_code", "http_status",
        "ensuing_model_retry",
    }
    category_counts: Counter[str] = Counter()
    reason_counts: Counter[str] = Counter()
    for sequence, event in enumerate(events, start=1):
        if not isinstance(event, dict) or set(event) != event_keys:
            raise ProvenanceError("invalid_tool_call_breakdown contains an invalid event")
        if event["sequence"] != sequence:
            raise ProvenanceError("invalid_tool_call_breakdown event sequence must be contiguous")
        if event["category"] not in INVALID_TOOL_CALL_CATEGORIES:
            raise ProvenanceError("invalid_tool_call_breakdown contains an invalid category")
        if event["reason"] not in INVALID_TOOL_CALL_REASONS:
            raise ProvenanceError("invalid_tool_call_breakdown contains an invalid reason")
        for name in ("action", "error_code"):
            if event[name] is not None and not isinstance(event[name], str):
                raise ProvenanceError(f"invalid_tool_call_breakdown event {name} must be a string")
        status = event["http_status"]
        if status is not None and (not isinstance(status, int) or isinstance(status, bool)):
            raise ProvenanceError(
                "invalid_tool_call_breakdown event http_status must be an integer"
            )
        if not isinstance(event["ensuing_model_retry"], bool):
            raise ProvenanceError(
                "invalid_tool_call_breakdown event ensuing_model_retry must be boolean"
            )
        category_counts[event["category"]] += 1
        reason_counts[event["reason"]] += 1
    if breakdown["total"] != invalid_tool_calls or len(events) != invalid_tool_calls:
        raise ProvenanceError(
            "invalid_tool_call_breakdown total must equal invalid_tool_calls"
        )
    if breakdown["counts_by_category"] != dict(category_counts):
        raise ProvenanceError("invalid_tool_call_breakdown category counts do not reconcile")
    if breakdown["counts_by_reason"] != dict(sorted(reason_counts.items())):
        raise ProvenanceError("invalid_tool_call_breakdown reason counts do not reconcile")


def _non_negative_integer(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _validate_request_topology(
    topology: Any,
    *,
    role: str,
    model_calls: int,
    input_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
) -> None:
    """Validate the content-free v1 request ledger and its byte/usage reconciliation."""
    if topology == {}:
        return  # Legacy, Umpire and otherwise unavailable measurement is explicit.
    if role != "player":
        raise ProvenanceError("an Umpire run cannot claim player request_topology")
    if not isinstance(topology, dict) or set(topology) != {
        "schema_version", "measurement", "requests"
    }:
        raise ProvenanceError("request_topology has an invalid shape")
    if topology["schema_version"] != REQUEST_TOPOLOGY_SCHEMA_VERSION:
        raise ProvenanceError("request_topology has an unsupported schema_version")
    if topology["measurement"] != {
        "boundary": "pydantic-ai-model-wrapper-logical-request",
        "encoding": "canonical-json-utf8",
        "hash_scheme": "hmac-sha256-ephemeral-activation-key",
        "provider_wire_exact": False,
        "tokenizer": None,
    }:
        raise ProvenanceError("request_topology has unsupported measurement semantics")
    requests = topology["requests"]
    if not isinstance(requests, list) or len(requests) != model_calls:
        raise ProvenanceError("request_topology request count must equal model_calls")

    request_keys = {
        "sequence", "measured_bytes", "growth_bytes", "stable_prefix_bytes",
        "stable_prefix_digest", "component_bytes", "provider_usage",
    }
    previous_bytes: int | None = None
    reported_usage: list[dict[str, Any]] = []
    for sequence, request in enumerate(requests, start=1):
        if not isinstance(request, dict) or set(request) != request_keys:
            raise ProvenanceError("request_topology contains an invalid request record")
        if request["sequence"] != sequence:
            raise ProvenanceError("request_topology sequence must be contiguous")
        measured = request["measured_bytes"]
        prefix = request["stable_prefix_bytes"]
        if not _non_negative_integer(measured) or not _non_negative_integer(prefix):
            raise ProvenanceError("request_topology byte counts must be non-negative integers")
        components = request["component_bytes"]
        if not isinstance(components, dict) or set(components) != set(
            REQUEST_TOPOLOGY_COMPONENTS
        ):
            raise ProvenanceError("request_topology component_bytes has an invalid shape")
        if not all(_non_negative_integer(value) for value in components.values()):
            raise ProvenanceError("request_topology component bytes must be non-negative integers")
        if sum(components.values()) != measured:
            raise ProvenanceError("request_topology component bytes do not reconcile to total")

        growth = request["growth_bytes"]
        digest = request["stable_prefix_digest"]
        if previous_bytes is None:
            if growth is not None or prefix != 0 or digest is not None:
                raise ProvenanceError("request_topology first-request baseline is invalid")
        else:
            if (
                not isinstance(growth, int)
                or isinstance(growth, bool)
                or growth != measured - previous_bytes
            ):
                raise ProvenanceError("request_topology growth bytes do not reconcile")
            if prefix > min(previous_bytes, measured):
                raise ProvenanceError("request_topology stable prefix exceeds request size")
            if prefix == 0:
                if digest is not None:
                    raise ProvenanceError("request_topology empty prefix cannot have a digest")
            elif (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ProvenanceError("request_topology stable prefix digest is invalid")

        usage = request["provider_usage"]
        usage_keys = {
            "available", "input_tokens", "cache_read_tokens", "cache_write_tokens"
        }
        if not isinstance(usage, dict) or set(usage) != usage_keys:
            raise ProvenanceError("request_topology provider_usage has an invalid shape")
        if not isinstance(usage["available"], bool):
            raise ProvenanceError("request_topology provider_usage availability must be boolean")
        token_values = [
            usage["input_tokens"], usage["cache_read_tokens"], usage["cache_write_tokens"]
        ]
        if not usage["available"]:
            if any(value is not None for value in token_values):
                raise ProvenanceError("request_topology unavailable provider_usage must be null")
        elif not all(_non_negative_integer(value) for value in token_values):
            raise ProvenanceError("request_topology provider_usage must contain token integers")
        elif usage["cache_read_tokens"] > usage["input_tokens"] or (
            usage["cache_write_tokens"] > usage["input_tokens"]
        ):
            raise ProvenanceError("request_topology provider_usage cache subsets exceed input")
        if usage["available"]:
            reported_usage.append(usage)
        previous_bytes = measured

    topology_usage = (
        sum(usage["input_tokens"] for usage in reported_usage),
        sum(usage["cache_read_tokens"] for usage in reported_usage),
        sum(usage["cache_write_tokens"] for usage in reported_usage),
    )
    if topology_usage != (input_tokens, cache_read_tokens, cache_write_tokens):
        raise ProvenanceError("request_topology provider_usage does not reconcile to AgentRun")


def resume_umpire_from_provenance(umpire_config: dict[str, Any]) -> UmpireRecipe:
    """The ``autonomous-resume-v1`` Umpire entry implied by an Umpire provenance configuration.

    The run-manifest endpoint uses it to complete a recipe that omitted the Umpire; the benchmark
    layer uses the same derivation to seal what every game's recipe must then show (T-056).
    A configuration without a model string is refused, because a resume recipe cannot compose an
    Umpire without one (I-127, D-101).
    """
    model = umpire_config.get("model")
    if not isinstance(model, str):
        raise ProvenanceError("the Umpire configuration has no model to derive resume.umpire from")
    settings_config = umpire_config.get("settings") or {}
    reasoning = settings_config.get("reasoning")
    return {
        "model": model,
        "temperature": settings_config.get("temperature"),
        "max_tokens": settings_config.get("max_tokens"),
        "timeout": settings_config.get("timeout"),
        "reasoning_effort": reasoning.get("effort") if isinstance(reasoning, dict) else None,
    }


def _reject_sensitive_keys(value: Any, *, path: str = "configuration") -> None:
    """Fail closed rather than persisting credential-shaped fields into an exportable record."""
    if isinstance(value, dict):
        for key, nested in value.items():
            if is_sensitive_key(key):
                raise ProvenanceError(f"{path} contains prohibited credential field {key!r}")
            _reject_sensitive_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sensitive_keys(nested, path=f"{path}[{index}]")


def _require_json_scalars(configuration: dict[str, Any]) -> None:
    """The manifest is sealed over canonical JSON; a non-finite number has no such form (I-086)."""
    found = first_non_json_scalar(configuration, path="configuration")
    if found is not None:
        path, scalar, reason = found
        if reason == "key":
            raise ProvenanceError(
                f"run configuration contains a non-string mapping key at {path}: {scalar!r}"
            )
        if reason == "cycle":
            raise ProvenanceError(f"run configuration contains a circular structure at {path}")
        raise ProvenanceError(f"run configuration contains a non-JSON value at {path}: {scalar!r}")


def create_run_manifest(
    engine: Engine, game_id: uuid.UUID, *, configuration: dict[str, Any]
) -> RunManifest:
    """Create the Game's immutable manifest; an exact retry returns the existing record.

    Scenario identity/hashes, per-Resource hashes, seed and Game id come from persisted trusted
    state rather than the caller. The caller supplies the execution configuration that only the
    orchestrator knows (player/Umpire prompts and models, code build, benchmark assignment).
    """
    missing = sorted(_REQUIRED_CONFIGURATION - configuration.keys())
    if missing:
        raise ProvenanceError(f"run configuration missing required fields: {', '.join(missing)}")
    # I-093: the JSON walk runs first — it is the one traversal with a cycle guard, so the
    # sensitive-key walk below only ever sees a tree already proven acyclic.
    _require_json_scalars(configuration)
    _reject_sensitive_keys(configuration)

    with write_session_scope(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        resources = [
            {"id": str(entry.get("id")), "content_sha256": entry.get("content_sha256")}
            for entry in game.resource_manifest
        ]
        resolved_configuration = json.loads(canonical_json(configuration))
        if game.diplomacy_policy_version is not None:
            game_loop = {
                "diplomacy_policy_version": game.diplomacy_policy_version,
                "diplomacy_mode": game.diplomacy_mode,
                "diplomacy_rounds_per_turn": game.diplomacy_rounds_per_turn,
            }
            if game.con_policy_version is not None:
                # I-055: the persisted Con condition is part of the same game-loop provenance.
                game_loop.update({
                    "con_policy_version": game.con_policy_version,
                    "con_mode": game.con_mode,
                    "con_challengers_per_argument": game.con_challengers_per_argument,
                })
            if game.con_reply_policy_version is not None:
                # I-046: likewise the persisted Con-reply condition.
                game_loop.update({
                    "con_reply_policy_version": game.con_reply_policy_version,
                    "con_reply_mode": game.con_reply_mode,
                    "reply_max_points": game.reply_max_points,
                })
            if game.consequence_mode not in (None, "none"):
                # T-074 / D-091: sealed only when it departs from the released loop, so a
                # ``none`` game's manifest is byte-identical to the released shape.
                game_loop.update({
                    "consequence_policy_version": game.consequence_policy_version,
                    "consequence_mode": game.consequence_mode,
                })
            supplied = resolved_configuration.get("game_loop")
            if supplied is not None and supplied != game_loop:
                raise ProvenanceError(
                    "run configuration game-loop condition conflicts with the Game snapshot"
                )
            resolved_configuration["game_loop"] = game_loop
        scenario: dict[str, object] = {
            "id": game.scenario_id,
            "version": game.scenario_version,
            "content_sha256": game.scenario_content_sha256,
            "resource_manifest_sha256": game.resource_manifest_sha256,
            "resources": resources,
        }
        if game.primary_arguments_per_actor > 1:
            # T-080 / D-094: round-robin Argument rounds are scenario-owned, so the server seals
            # them beside the scenario identity — never in the runner-declared ``game_loop``,
            # which a resume re-derives and compares exactly. Absent at budget 1, keeping the
            # released manifest shape.
            scenario["argument_sequencing"] = ARGUMENT_SEQUENCING_ROUND_ROBIN
            scenario["primary_arguments_per_actor"] = game.primary_arguments_per_actor
        manifest = {
            "game_id": str(game.id),
            "scenario": scenario,
            "seed": game.seed,
            **resolved_configuration,
        }
        digest = canonical_sha256(manifest)
        existing = session.get(RunManifest, game_id)
        if existing is not None:
            if existing.manifest_sha256 != digest:
                raise ManifestConflict(f"game {game_id} already has a different run manifest")
            return RunManifest.model_validate(existing.model_dump())
        # I-078: manifest *creation* is lobby-only, so any manifest that exists was provably
        # sealed before play. The exact retry above still returns the immutable record after
        # start; only a first sealing after the lobby is refused.
        if game.status is not GameStatus.lobby:
            raise ManifestConflict(
                f"game {game_id} has left the lobby (status {game.status.value!r}); a run "
                "manifest can only be sealed before the game starts"
            )
        record = RunManifest(game_id=game_id, manifest=manifest, manifest_sha256=digest)
        session.add(record)
        session.flush()
        session.refresh(record)
        return RunManifest.model_validate(record.model_dump())


def record_agent_run(engine: Engine, game_id: uuid.UUID, **values: Any) -> AgentRun:
    """Append one chain-of-thought-free player/Umpire execution record."""
    _reject_sensitive_keys(values, path="agent_run")
    role = values.get("role")
    actor_id = values.get("actor_id")
    activation_id = values.get("activation_id")
    if role not in ROLES:
        raise ProvenanceError("role must be 'player', 'umpire' or 'consequence'")
    if role == "player" and not actor_id:
        raise ProvenanceError("a player run requires actor_id")
    if role != "player" and actor_id is not None:
        raise ProvenanceError(f"a non-player ({role}) run cannot claim an Actor identity")
    if activation_id is not None:
        try:
            activation_id = str(uuid.UUID(activation_id))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ProvenanceError("activation_id must be an opaque UUID") from exc
        if role != "player":
            raise ProvenanceError("only a player run may claim an activation_id")
        values["activation_id"] = activation_id
    for field in (
        "input_tokens", "output_tokens", "reasoning_tokens", "cache_read_tokens",
        "cache_write_tokens", "model_calls", "costed_model_calls", "tool_calls",
        "invalid_tool_calls", "stalls",
    ):
        value = values.get(field, 0)
        if not isinstance(value, int) or value < 0:
            raise ProvenanceError(f"{field} must be a non-negative integer")
    # I-098 / D-078: the research counts are nullable (legacy rows) but never negative, never a
    # bool, never a string.
    for field in ("research_tool_calls", "research_operations"):
        value = values.get(field)
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value < 0
        ):
            raise ProvenanceError(f"{field} must be a non-negative integer or null")
    _validate_invalid_tool_call_breakdown(
        values.get("invalid_tool_call_breakdown", {}),
        invalid_tool_calls=values.get("invalid_tool_calls", 0),
    )
    _validate_request_topology(
        values.get("request_topology", {}),
        role=role,
        model_calls=values.get("model_calls", 1),
        input_tokens=values.get("input_tokens", 0),
        cache_read_tokens=values.get("cache_read_tokens", 0),
        cache_write_tokens=values.get("cache_write_tokens", 0),
    )
    if values.get("latency_ms") is not None and values["latency_ms"] < 0:
        raise ProvenanceError("latency_ms must be non-negative")
    if values.get("activation_wall_ms") is not None and values["activation_wall_ms"] < 0:
        raise ProvenanceError("activation_wall_ms must be non-negative")
    if values.get("cost_usd") is not None and values["cost_usd"] < 0:
        raise ProvenanceError("cost_usd must be non-negative")
    if "costed_model_calls" not in values:
        values["costed_model_calls"] = (
            values.get("model_calls", 1) if values.get("cost_usd") is not None else 0
        )
    if values["costed_model_calls"] > values.get("model_calls", 1):
        raise ProvenanceError("costed_model_calls cannot exceed model_calls")
    if values.get("reasoning_tokens", 0) > values.get("output_tokens", 0):
        raise ProvenanceError("reasoning_tokens cannot exceed inclusive output_tokens")
    for field in ("cache_read_tokens", "cache_write_tokens"):
        if values.get(field, 0) > values.get("input_tokens", 0):
            raise ProvenanceError(f"{field} cannot exceed inclusive input_tokens")

    with session_scope(engine) as session:
        if session.get(Game, game_id) is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        if actor_id is not None and session.get(Actor, (game_id, actor_id)) is None:
            raise ProvenanceError(f"Actor {actor_id!r} does not belong to game {game_id}")
        if activation_id is not None:
            duplicate = session.exec(
                select(AgentRun).where(
                    AgentRun.game_id == game_id,
                    AgentRun.activation_id == activation_id,
                )
            ).first()
            if duplicate is not None:
                raise ProvenanceError(f"activation_id {activation_id!r} is already recorded")
        record = AgentRun(game_id=game_id, **values)
        session.add(record)
        try:
            session.flush()
        except IntegrityError as exc:
            # I-082: the unique index closes the read-before-insert race; report it the same way.
            raise ProvenanceError(
                f"activation_id {activation_id!r} is already recorded"
            ) from exc
        session.refresh(record)
        return AgentRun.model_validate(record.model_dump())


def list_agent_runs(engine: Engine, game_id: uuid.UUID) -> list[AgentRun]:
    with session_scope(engine) as session:
        if session.get(Game, game_id) is None:
            raise GameNotFound(f"game {game_id!r} does not exist")
        rows = session.exec(
            select(AgentRun).where(AgentRun.game_id == game_id).order_by(col(AgentRun.id))
        ).all()
        return [AgentRun.model_validate(row.model_dump()) for row in rows]

