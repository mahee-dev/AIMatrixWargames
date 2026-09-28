"""The administrative JSON game export (T-043, api.md §15.1, TRD §27).

One builder — :func:`build_game_export` — reads the complete authoritative record of a game in a
single read transaction and returns a JSON-serialisable dict. It is the administrative counterpart
to the Actor-facing API: it **intentionally** includes game-scoped private data (Actor objectives
and briefs, private Messages, private Notes), each carrying its ownership/visibility metadata, so a
researcher can see what happened and under what immutable configuration it ran.

Two callers share this one function — the ``GET /admin/games/{id}/export`` endpoint (behind
``require_admin``) and ``amaw export`` — so there is exactly one serialisation. Three rules shape
it:

* **Never filtered.** The export is the unfiltered admin view and must *not* go through
  :mod:`app.visibility`; that service exists only for the Actor-facing API, and blurring the two
  would either leak through the Actor API or cripple the research export (CLAUDE.md).
* **Never a secret.** Seat tokens are returned out-of-band at game creation and never persisted;
  only their hash is stored, and even that is deliberately omitted here — the export carries no
  token, admin token or API key (testing.md §24), and no hidden model chain-of-thought (§15.1).
* **Faithful, not fabricated.** It exports what persisted state actually holds. Retained
  inactive Effects (superseded/expired) appear as inactive rows, never deletions (INV-EFFECT-004).
  T-053's model/Actor assignments, immutable run manifest, model-call history, errors and summary
  are included alongside T-045's byte provenance and T-050's append-only Resource-access log.

This module lives in ``app/`` and imports no agent framework (AGENTS.md §6 / TRD §4): it reads the
database only.
"""

from __future__ import annotations

import contextlib
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import Engine

from app import persistence, summary
from app.database import session_scope
from app.game_service import GameNotFound
from app.models import (
    Actor,
    ActorStrategicState,
    AgentRun,
    AdjudicationRecord,
    Argument,
    Con,
    ConParticipation,
    ConsequenceRecord,
    Effect,
    Game,
    GameEvent,
    Message,
    Note,
    Resource,
    ResourceAccess,
    RunManifest,
    Seat,
)
from app.models import ArgumentReply

def write_json_atomic(path: Path, payload: Any) -> None:
    """Write ``payload`` as JSON so that ``path`` is always either its previous or its new content.

    The one writer for evidence files — ``amaw run --export-path`` (I-097) and the benchmark's
    ``batch.json``/``attempt.json``/``export.json`` (I-084) — so a kill mid-write never leaves a
    truncated export. The text goes to a sibling temp file, is fsynced, then renamed over the
    target (atomic on POSIX); the directory entry is fsynced where the platform allows. On any
    failure before the rename the previous file is untouched and the temp file is removed. The
    encoding (``indent=2, sort_keys=True, default=str``, trailing newline) is the one both callers
    always used.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n"
    # A per-process sibling name (one process owns a batch directory, I-078, and one process
    # owns a run export) keeps the file's mode under the caller's umask, unlike
    # ``tempfile.mkstemp``.
    temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            temp_path.unlink()
        raise
    _fsync_directory(path.parent)


def _fsync_directory(directory: Path) -> None:
    """Persist the rename itself; directories cannot be opened for sync on every platform."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


# Bump when the export shape changes in a way a consumer must notice.
# 16: T-081 / D-095 game.scenario_injects and effects[].source_inject_id;
# 15: T-074 / D-091 game.consequence_policy_version / consequence_mode, consequence_records and
# effects[].source_consequence_turn; 14: T-073 / D-090 game.scenario_rules; 13: I-099 ResourceAccess search-result identity; 12: I-098 AgentRun research_tool_calls /
# research_operations; 11: I-082 database identity block (I-046 added argument_replies).
EXPORT_FORMAT_VERSION = "16"


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _str(value: uuid.UUID | None) -> str | None:
    return str(value) if value is not None else None


def _game(game: Game) -> dict:
    """The Game's immutable configuration, seed and current authoritative position (§15.1)."""
    return {
        "id": str(game.id),
        "scenario_id": game.scenario_id,
        "scenario_version": game.scenario_version,
        "scenario_summary": game.scenario_summary,
        # D-090: the snapshotted public scenario rules the Umpire received; [] when none.
        "scenario_rules": [dict(rule) for rule in game.scenario_rules],
        "scenario_injects": [dict(inject) for inject in game.scenario_injects],
        "seed": game.seed,
        "rng_draw_index": game.rng_draw_index,
        "max_turns": game.max_turns,
        "turn": game.turn,
        "phase": game.phase.value,
        "status": game.status.value,
        "turn_order": list(game.turn_order),
        "active_actor_id": game.active_actor_id,
        "primary_arguments_per_actor": game.primary_arguments_per_actor,
        "max_cons_per_actor": game.max_cons_per_actor,
        "in_world_turn_length": game.in_world_turn_length,
        "diplomacy_timeout_seconds": game.diplomacy_timeout_seconds,
        "cons_timeout_seconds": game.cons_timeout_seconds,
        "diplomacy_ready_actor_ids": list(game.diplomacy_ready_actor_ids),
        "diplomacy_policy_version": game.diplomacy_policy_version,
        "diplomacy_mode": game.diplomacy_mode,
        "diplomacy_rounds_per_turn": game.diplomacy_rounds_per_turn,
        "diplomacy_rounds_completed": game.diplomacy_rounds_completed,
        "diplomacy_close_reason": game.diplomacy_close_reason,
        "con_policy_version": game.con_policy_version,
        "con_mode": game.con_mode,
        "con_challengers_per_argument": game.con_challengers_per_argument,
        "con_challenger_cursor": game.con_challenger_cursor,
        # I-046: the Con-reply condition; null on legacy rows, never fabricated.
        "con_reply_policy_version": game.con_reply_policy_version,
        "con_reply_mode": game.con_reply_mode,
        "reply_max_points": game.reply_max_points,
        # T-074 / D-091: the Consequence condition; null on legacy rows (which ran ``none``).
        "consequence_policy_version": game.consequence_policy_version,
        "consequence_mode": game.consequence_mode,
        # I-078: whether the server enforced manifest-before-start for this Game.
        "require_run_manifest": bool(game.require_run_manifest),
    }


def _actor(actor: Actor) -> dict:
    """An Actor with its full private state — objectives, brief, private information (§15.1)."""
    return {
        "id": actor.id,
        "name": actor.name,
        "public_description": actor.public_description,
        "private_brief": actor.private_brief,
        "objectives": list(actor.objectives),
        "private_information": list(actor.private_information),
        "organisation_id": actor.organisation_id,
        "role": actor.role,
        "metadata": dict(actor.actor_metadata),
    }


def _message(message: Message) -> dict:
    """A Message with the metadata needed to reconstruct who could see it (sender/recipients)."""
    return {
        "id": str(message.id),
        "turn": message.turn,
        "sender_actor_id": message.sender_actor_id,
        "recipients": list(message.recipients),
        "public": message.public,
        "visibility": "public" if message.public else "private",
        "text": message.text,
        "created_at": _iso(message.created_at),
    }


def _argument(argument: Argument) -> dict:
    return {
        "id": str(argument.id),
        "turn": argument.turn,
        "actor_id": argument.actor_id,
        "action": argument.action,
        "desired_effect": argument.desired_effect,
        "pros": list(argument.pros),
        "budget_slot": argument.budget_slot,
        "status": argument.status,
        # I-055: the sealed challenger set, or null for unrestricted opposition (open/legacy).
        "challenger_actor_ids": (
            list(argument.challenger_actor_ids)
            if argument.challenger_actor_ids is not None else None
        ),
    }


def _con(con: Con) -> dict:
    return {
        "id": str(con.id),
        "argument_id": str(con.argument_id),
        "actor_id": con.actor_id,
        "reason": con.reason,
    }


def _argument_reply(reply: ArgumentReply) -> dict:
    """I-046: the owner's single reply to the revealed Cons (``points == []`` is the pass)."""
    return {
        "argument_id": str(reply.argument_id),
        "actor_id": reply.actor_id,
        "points": [dict(point) for point in reply.points],
        "created_at": _iso(reply.created_at),
    }


def _con_participation(participation: ConParticipation) -> dict:
    return {
        "argument_id": str(participation.argument_id),
        "actor_id": participation.actor_id,
        "completed_at": _iso(participation.completed_at),
    }


def _adjudication(record: AdjudicationRecord) -> dict:
    """One authoritative adjudication: the Umpire's proposal (both branches) and the server's
    resolution (mode/probability/roll/success). ``raw_output`` is the model's structured proposal
    — never chain-of-thought (§15.1)."""
    return {
        "argument_id": str(record.argument_id),
        "mode": record.mode.value,
        "probability": record.probability,
        "rationale": record.rationale,
        "success_effects": list(record.success_effects),
        "failure_effects": list(record.failure_effects),
        "supersedes_effect_ids": [str(x) for x in record.supersedes_effect_ids],
        "roll": record.roll,
        "success": record.success,
        "raw_output": dict(record.raw_output),
        "created_at": _iso(record.created_at),
    }


def _effect(effect: Effect) -> dict:
    """An Effect with its visibility/ownership and provenance. Inactive rows (superseded/expired)
    are retained and exported as ``active: false`` — supersession deactivates, never deletes
    (INV-EFFECT-004)."""
    return {
        "id": str(effect.id),
        "description": effect.description,
        "visibility": effect.visibility,
        "visible_to": list(effect.visible_to),
        "source_argument_id": _str(effect.source_argument_id),
        # T-074 / D-091: the turn whose consequence pass created it; null for every other Effect.
        "source_consequence_turn": effect.source_consequence_turn,
        "source_inject_id": effect.source_inject_id,
        "created_turn": effect.created_turn,
        "expires_turn": effect.expires_turn,
        "active": effect.active,
        "supersedes": [str(x) for x in effect.supersedes],
    }


def _consequence_record(record: ConsequenceRecord) -> dict:
    """One turn's accepted consequence pass: each Effect with its rationale and evidence (D-091)."""
    return {
        "turn": record.turn,
        "effects": [dict(entry) for entry in record.effects],
        "created_at": _iso(record.created_at),
    }


def _event(event: GameEvent) -> dict:
    return {
        "id": event.id,
        "turn": event.turn,
        "type": event.type,
        "actor_id": event.actor_id,
        "payload": dict(event.payload),
        "created_at": _iso(event.created_at),
    }


def _resource_access(access: ResourceAccess) -> dict:
    """One append-only Resource-access row (TRD §29): which Actor performed which operation
    (search vs read), with what query or on which Resource, in which turn, and when. Admin-only
    behavioural provenance for the benchmark-methodology §17 research metrics (testing.md §24)."""
    return {
        "id": access.id,
        "actor_id": access.actor_id,
        "turn": access.turn,
        "operation": access.operation,
        "query": access.query,
        "resource_id": access.resource_id,
        "activation_id": access.activation_id,
        # I-099: the ordered result identity of a search; null on reads and legacy rows.
        "result_count": access.result_count,
        "result_resource_ids": (
            list(access.result_resource_ids) if access.result_resource_ids is not None else None
        ),
        "result_set_sha256": access.result_set_sha256,
        "created_at": _iso(access.created_at),
    }


def _note(note: Note) -> dict:
    """A Note with its ownership + visibility metadata (TEST-NOTE-003). A Note is always private
    to its owning Actor (INV-NOTE-001); the export retains that ownership, it does not filter it."""
    return {
        "id": str(note.id),
        "actor_id": note.actor_id,  # ownership metadata
        "visibility": "private",  # a Note is private to its owner by definition
        "text": note.text,
        "created_at": _iso(note.created_at),
    }


def _strategic_state(state: ActorStrategicState) -> dict:
    """Current private rolling memory plus non-capability delivery audit metadata (D-048)."""
    return {
        "actor_id": state.actor_id,
        "visibility": "private",
        "memory_version": "strategic-memory-v1",
        "strategic_memory": dict(state.strategic_memory),
        "acknowledged_event_id": state.acknowledged_event_id,
        "pending_event_id": state.pending_event_id,
        "pending_delivery": state.pending_receipt is not None,
        "updated_at": _iso(state.updated_at),
    }


def _agent_run(run: AgentRun) -> dict:
    """One chain-of-thought-free player activation or Umpire provider call (TRD §28)."""
    return {
        "id": run.id,
        "activation_id": run.activation_id,
        "actor_id": run.actor_id,
        "role": run.role,
        "turn": run.turn,
        "phase": run.phase,
        "model_requested": run.model_requested,
        "model_returned": run.model_returned,
        "provider": run.provider,
        "prompt_version": run.prompt_version,
        "prompt_hash": run.prompt_hash,
        "model_parameters": dict(run.model_parameters),
        "reasoning_configuration": (
            dict(run.reasoning_configuration) if run.reasoning_configuration is not None else None
        ),
        "agent_framework": run.agent_framework,
        "agent_framework_version": run.agent_framework_version,
        "tool_schema_version": run.tool_schema_version,
        "context_mode": run.context_mode,
        "memory_version": run.memory_version,
        "input_tokens": run.input_tokens,
        "output_tokens": run.output_tokens,
        "reasoning_tokens": run.reasoning_tokens,
        "cache_read_tokens": run.cache_read_tokens,
        "cache_write_tokens": run.cache_write_tokens,
        "model_calls": run.model_calls,
        "costed_model_calls": run.costed_model_calls,
        "tool_calls": run.tool_calls,
        "research_tool_calls": run.research_tool_calls,
        "research_operations": run.research_operations,
        "invalid_tool_calls": run.invalid_tool_calls,
        "invalid_tool_call_breakdown": dict(run.invalid_tool_call_breakdown),
        "request_topology": dict(run.request_topology),
        "stalls": run.stalls,
        "cost_usd": run.cost_usd,
        "latency_ms": run.latency_ms,
        "activation_wall_ms": run.activation_wall_ms,
        "completed": run.completed,
        "failure_reason": run.failure_reason,
        "provider_metadata": dict(run.provider_metadata),
        "created_at": _iso(run.created_at),
    }


def build_game_export(engine: Engine, game_id: uuid.UUID) -> dict:
    """Return the complete, unfiltered JSON-serialisable record of ``game_id`` (api.md §15.1).

    Read-only: everything is read in one transaction and nothing is mutated (API-INV-008). Rows
    are ordered deterministically so the endpoint and ``amaw export`` produce byte-identical
    records for the same game. An absent game raises :class:`~app.game_service.GameNotFound`,
    which the API maps to ``404 not_found``.
    """
    with session_scope(engine) as session:
        game = session.get(Game, game_id)
        if game is None:
            raise GameNotFound(f"game {game_id!r} does not exist")

        actors = sorted(
            persistence.list_by_game(session, Actor, game_id), key=lambda a: a.id
        )
        seats = sorted(
            persistence.list_by_game(session, Seat, game_id), key=lambda s: s.actor_id
        )
        messages = sorted(
            persistence.list_by_game(session, Message, game_id),
            key=lambda m: (m.created_at, str(m.id)),
        )
        arguments = sorted(
            persistence.list_by_game(session, Argument, game_id),
            key=lambda a: (a.turn, a.budget_slot, str(a.id)),
        )
        effects = sorted(
            persistence.list_by_game(session, Effect, game_id),
            key=lambda e: (e.created_turn, str(e.id)),
        )
        events = sorted(
            persistence.list_by_game(session, GameEvent, game_id),
            key=lambda e: (e.id if e.id is not None else 0),
        )
        notes = sorted(
            persistence.list_by_game(session, Note, game_id),
            key=lambda n: (n.created_at, str(n.id)),
        )
        strategic_states = sorted(
            persistence.list_by_game(session, ActorStrategicState, game_id),
            key=lambda state: state.actor_id,
        )
        resources = sorted(
            persistence.list_by_game(session, Resource, game_id), key=lambda resource: resource.id
        )
        # Append-only observational log (T-050, TRD §29); ordered by its monotonic id so the
        # endpoint and ``amaw export`` stay byte-identical.
        resource_access = sorted(
            persistence.list_by_game(session, ResourceAccess, game_id),
            key=lambda access: (access.id if access.id is not None else 0),
        )
        consequence_records = sorted(
            persistence.list_by_game(session, ConsequenceRecord, game_id),
            key=lambda record: record.turn,
        )
        run_manifest = session.get(RunManifest, game_id)
        # I-082: the identity of the database this record was read from — the stamp the file
        # carries and whether this connection enforced the declared parent relationships. Both
        # are read live, not asserted from code constants (TRD §27: "under what immutable
        # configuration it ran").
        connection = session.connection()
        database_identity = {
            "schema_version": int(
                connection.exec_driver_sql("PRAGMA user_version").scalar() or 0
            ),
            "foreign_keys_enforced": bool(
                connection.exec_driver_sql("PRAGMA foreign_keys").scalar()
            ),
        }
        agent_runs = sorted(
            persistence.list_by_game(session, AgentRun, game_id),
            key=lambda run: run.id if run.id is not None else 0,
        )

        # Con / ConParticipation / AdjudicationRecord are keyed by Argument, not Game — gather
        # them across this game's Arguments (still one transaction, no visibility filtering).
        cons: list[Con] = []
        participations: list[ConParticipation] = []
        adjudications: list[AdjudicationRecord] = []
        replies: list[ArgumentReply] = []
        for argument in arguments:
            cons.extend(persistence.list_by_argument(session, Con, argument.id))
            participations.extend(
                persistence.list_by_argument(session, ConParticipation, argument.id)
            )
            record = session.get(AdjudicationRecord, argument.id)
            if record is not None:
                adjudications.append(record)
            reply = session.get(ArgumentReply, argument.id)
            if reply is not None:
                replies.append(reply)

        cons.sort(key=lambda c: str(c.id))
        participations.sort(key=lambda p: (str(p.argument_id), p.actor_id))
        adjudications.sort(key=lambda r: (r.created_at, str(r.argument_id)))
        replies.sort(key=lambda r: (r.created_at, str(r.argument_id)))

        export = {
            "export_format_version": EXPORT_FORMAT_VERSION,
            "database": database_identity,
            "game": _game(game),
            # Seat configuration without any credential material: which Actors have a Seat, no
            # token and no token hash (INV-ACTOR-001, testing.md §24).
            "seats": [{"actor_id": seat.actor_id} for seat in seats],
            "actors": [_actor(a) for a in actors],
            "messages": [_message(m) for m in messages],
            "arguments": [_argument(a) for a in arguments],
            "cons": [_con(c) for c in cons],
            "con_participation": [_con_participation(p) for p in participations],
            # I-046: empty for sealed_cons and legacy games.
            "argument_replies": [_argument_reply(r) for r in replies],
            "adjudications": [_adjudication(r) for r in adjudications],
            "effects": [_effect(e) for e in effects],
            # T-074 / D-091: one entry per committed consequence pass; empty under ``none``.
            "consequence_records": [_consequence_record(r) for r in consequence_records],
            "events": [_event(e) for e in events],
            "notes": [_note(n) for n in notes],
            "strategic_memories": [_strategic_state(s) for s in strategic_states],
            "model_assignments": (
                dict(run_manifest.manifest.get("player", {}).get("assignments", {}))
                if run_manifest is not None
                else None
            ),
            "run_manifest": (
                {
                    "manifest_sha256": run_manifest.manifest_sha256,
                    "created_at": _iso(run_manifest.created_at),
                    **dict(run_manifest.manifest),
                }
                if run_manifest is not None
                else None
            ),
            "agent_runs": [_agent_run(run) for run in agent_runs],
            # Authoritative byte-derived provenance only. Package paths and source bytes are
            # intentionally not serialised; format/read projections start at T-046+.
            "scenario_hashes": (
                {
                    "scenario_content_sha256": game.scenario_content_sha256,
                    "resource_manifest_sha256": game.resource_manifest_sha256,
                    "resources": [
                        {"id": resource.id, "content_sha256": resource.content_sha256}
                        for resource in resources
                    ],
                }
                if game.scenario_content_sha256 is not None
                else None
            ),
            # Append-only Resource-access log (T-050, TRD §29): admin-only research provenance.
            "resource_access": [_resource_access(a) for a in resource_access],
            "agent_errors": [
                _agent_run(run) for run in agent_runs if not run.completed or run.failure_reason
            ],
            # I-082: derived in the same transaction as the rows above, so a live game cannot
            # yield a summary that counts activations the record does not contain.
            "run_summary": summary.build_game_summary_in_session(session, game_id),
        }
    return export
