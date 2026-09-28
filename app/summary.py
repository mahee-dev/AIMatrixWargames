"""Derived post-game reports: the ``GET /admin/games/{id}/summary`` body and its text rendering.

Everything here is *read-only interpretation* of what :mod:`app.provenance` recorded and of the
event clock the engine already keeps (D-039): call, token, cost and wall-time totals per role and
phase (:func:`build_game_summary`), the activation-level Resource research join
(:func:`build_resource_research_report`, I-045), the Con and Con-reply condition summaries
(I-055, I-046), and the human-readable rendering (:func:`format_game_summary`). No function here
writes, and no mutable counter exists to drift from the audit record — recompute is the only path.
The export embeds :func:`build_game_summary` as ``run_summary`` (:mod:`app.export`).
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from datetime import datetime
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from amaw_contracts import (
    INVALID_TOOL_CALL_SCHEMA_VERSION,
    REQUEST_TOPOLOGY_COMPONENTS,
    REQUEST_TOPOLOGY_SCHEMA_VERSION,
)
from app.database import session_scope
from app.game_service import GameNotFound
from app.models import (
    Actor,
    AgentRun,
    Argument,
    ArgumentReply,
    Con,
    ConParticipation,
    Game,
    GameEvent,
    Resource,
    ResourceAccess,
)

PHASES = ("diplomacy", "argument", "cons", "reply", "adjudication", "consequences")


def _phase_runtimes(events: list[GameEvent]) -> tuple[int | None, dict[str, int]]:
    starts = [e for e in events if e.type == "phase_started" and e.created_at is not None]
    completed = next((e for e in reversed(events) if e.type == "game_completed"), None)
    game_started = next((e for e in events if e.type == "game_started"), None)
    runtimes: defaultdict[str, int] = defaultdict(int)
    for index, event in enumerate(starts):
        phase = event.payload.get("phase")
        if not isinstance(phase, str):
            continue
        end: datetime | None = starts[index + 1].created_at if index + 1 < len(starts) else None
        if end is None and completed is not None:
            end = completed.created_at
        if end is not None:
            runtimes[phase] += max(0, round((end - event.created_at).total_seconds() * 1000))
    total = None
    if game_started is not None and completed is not None:
        total = max(
            0, round((completed.created_at - game_started.created_at).total_seconds() * 1000)
        )
    return total, dict(runtimes)


def _normalized_query(query: str | None) -> str:
    """Mirror Resource search's exact mechanical normalization: trim, then case-fold."""
    return (query or "").strip().casefold()


def _request_topology_summary(runs: list[AgentRun]) -> dict[str, Any]:
    """Aggregate validated content-free request measurements without inventing legacy data."""
    player_runs = [run for run in runs if run.role == "player"]
    measured: list[dict[str, Any]] = []
    measured_activations = 0
    versions: Counter[str] = Counter()
    for run in player_runs:
        topology = run.request_topology or {}
        if topology.get("schema_version") != REQUEST_TOPOLOGY_SCHEMA_VERSION:
            continue
        measured_activations += 1
        versions[topology["schema_version"]] += 1
        measured.extend(topology.get("requests", []))
    components: Counter[str] = Counter()
    for request in measured:
        components.update(request["component_bytes"])
    usage_rows = [request["provider_usage"] for request in measured]
    reported = [usage for usage in usage_rows if usage["available"]]
    player_calls = sum(run.model_calls for run in player_runs)
    return {
        "player_model_calls": player_calls,
        "measured_requests": len(measured),
        "unmeasured_requests": player_calls - len(measured),
        "measured_activations": measured_activations,
        "unmeasured_activations": len(player_runs) - measured_activations,
        "schema_versions": dict(sorted(versions.items())),
        "measured_bytes": sum(request["measured_bytes"] for request in measured),
        "component_bytes": {
            component: components[component] for component in REQUEST_TOPOLOGY_COMPONENTS
        },
        "provider_usage": {
            "reported_requests": len(reported),
            "input_tokens": sum(usage["input_tokens"] for usage in reported),
            "cache_read_tokens": sum(usage["cache_read_tokens"] for usage in reported),
            "cache_write_tokens": sum(usage["cache_write_tokens"] for usage in reported),
        },
    }


def _research_activation(
    run: AgentRun,
    accesses: list[ResourceAccess],
    resource_versions: dict[str, str],
) -> dict[str, Any]:
    searches = [access for access in accesses if access.operation == "search"]
    reads = [access for access in accesses if access.operation == "read"]
    query_counts = Counter(_normalized_query(access.query) for access in searches)
    resource_counts = Counter(
        access.resource_id for access in reads if access.resource_id is not None
    )

    conversions = 0
    for index, access in enumerate(accesses):
        if access.operation != "search":
            continue
        following_segment = accesses[index + 1 :]
        next_search = next(
            (offset for offset, row in enumerate(following_segment) if row.operation == "search"),
            len(following_segment),
        )
        if any(row.operation == "read" for row in following_segment[:next_search]):
            conversions += 1

    # I-099: result-identity metrics over the searches that carry result metadata. Definitions
    # (the brief for I-099, benchmark-methodology §17):
    #   zero_result_searches — metadata-bearing searches with result_count == 0;
    #   distinct_result_sets — distinct ordered-result digests (the empty list is one);
    #   reformulations_after_identical_set — a search whose folded query differs from the
    #     immediately preceding search's, has not appeared earlier in this activation (an exact
    #     repeat is a `repeated_queries` datum, never a reformulation) and whose digest equals
    #     the immediately preceding search's; both rows must carry metadata;
    #   search_returned_read_conversions — reads of a Resource that a preceding search in this
    #     activation returned; every qualifying read is one observation;
    #   read_ranks — for each converted read, the 1-based rank of the Resource in the *most
    #     recent preceding* search result list that contains it;
    #   searches_without_result_metadata — legacy NULL rows, excluded from all of the above.
    with_metadata = [
        access for access in searches if access.result_resource_ids is not None
    ]
    zero_result_searches = sum(access.result_count == 0 for access in with_metadata)
    distinct_result_sets = len({access.result_set_sha256 for access in with_metadata})
    reformulations = 0
    previous_search: ResourceAccess | None = None
    seen_queries: set[str] = set()
    for access in searches:
        folded = _normalized_query(access.query)
        if (
            previous_search is not None
            and access.result_resource_ids is not None
            and previous_search.result_resource_ids is not None
            and folded != _normalized_query(previous_search.query)
            and folded not in seen_queries
            and access.result_set_sha256 == previous_search.result_set_sha256
        ):
            reformulations += 1
        seen_queries.add(folded)
        previous_search = access
    read_ranks: list[dict[str, Any]] = []
    returned_so_far: list[ResourceAccess] = []
    for access in accesses:
        if access.operation == "search":
            if access.result_resource_ids is not None:
                returned_so_far.append(access)
            continue
        if access.operation != "read" or access.resource_id is None:
            continue
        source = next(
            (
                search for search in reversed(returned_so_far)
                if access.resource_id in search.result_resource_ids
            ),
            None,
        )
        if source is not None:
            read_ranks.append({
                "resource_access_id": access.id,
                "resource_id": access.resource_id,
                "rank": source.result_resource_ids.index(access.resource_id) + 1,
                "search_resource_access_id": source.id,
            })

    unique_resources = [
        {
            "resource_id": resource_id,
            "content_sha256": resource_versions.get(resource_id),
            "read_count": count,
        }
        for resource_id, count in resource_counts.items()
    ]
    return {
        "agent_run_id": run.id,
        "activation_id": run.activation_id,
        "actor_id": run.actor_id,
        "turn": run.turn,
        "phase": run.phase,
        "completed": run.completed,
        "searches": len(searches),
        "reads": len(reads),
        "normalized_queries": [_normalized_query(access.query) for access in searches],
        "repeated_queries": [
            {"query": query, "count": count}
            for query, count in query_counts.items()
            if count > 1
        ],
        "unique_resources": unique_resources,
        "repeated_resources": [
            resource for resource in unique_resources if resource["read_count"] > 1
        ],
        # A search converts only when at least one read occurs after it and before the next
        # search in this activation. This is deterministic access-log sequencing, not a claim
        # about which provider call caused either operation.
        "search_to_read_conversions": conversions,
        "searches_followed_by_no_read": len(searches) - conversions,
        "zero_result_searches": zero_result_searches,
        "distinct_result_sets": distinct_result_sets,
        "reformulations_after_identical_set": reformulations,
        "search_returned_read_conversions": len(read_ranks),
        "read_ranks": read_ranks,
        "searches_without_result_metadata": len(searches) - len(with_metadata),
        "provider_calls": run.model_calls,
        "continued_after_research": bool(accesses) and run.model_calls > 1,
    }


def build_resource_research_report(engine: Engine, game_id: uuid.UUID) -> dict[str, Any]:
    """Join ResourceAccess to player AgentRun at activation granularity (I-045).

    Classification is deliberately syntactic. Queries use the same trim+casefold normalization
    as search, Resource versions are immutable content hashes, and continuation means only that a
    research-bearing activation contained more than one provider request. No semantic query or
    provider-call-to-tool sequencing is inferred.
    """
    with session_scope(engine) as session:
        return build_resource_research_report_in_session(session, game_id)


def build_resource_research_report_in_session(
    session: Session, game_id: uuid.UUID
) -> dict[str, Any]:
    """The research report read inside a caller's transaction (I-082)."""
    if session.get(Game, game_id) is None:
        raise GameNotFound(f"game {game_id!r} does not exist")
    runs = list(session.exec(
        select(AgentRun).where(
            AgentRun.game_id == game_id,
            AgentRun.role == "player",
        ).order_by(AgentRun.id)
    ))
    accesses = list(session.exec(
        select(ResourceAccess).where(ResourceAccess.game_id == game_id)
        .order_by(ResourceAccess.id)
    ))
    resource_versions = {
        resource.id: resource.content_sha256
        for resource in session.exec(
            select(Resource).where(Resource.game_id == game_id).order_by(Resource.id)
        )
    }

    access_by_activation: defaultdict[tuple[str, str], list[ResourceAccess]] = defaultdict(list)
    for access in accesses:
        if access.activation_id is not None:
            access_by_activation[(access.activation_id, access.actor_id)].append(access)
    run_ids = {
        (run.activation_id, run.actor_id)
        for run in runs
        if run.activation_id is not None and run.actor_id is not None
    }
    activations = [
        _research_activation(
            run,
            access_by_activation.get((run.activation_id, run.actor_id), [])
            if run.activation_id and run.actor_id else [],
            resource_versions,
        )
        for run in runs
    ]
    unattributed = [
        access
        for access in accesses
        if access.activation_id is None
        or (access.activation_id, access.actor_id) not in run_ids
    ]
    unattributed_payload = [
        {
            "resource_access_id": access.id,
            "activation_id": access.activation_id,
            "actor_id": access.actor_id,
            "turn": access.turn,
            "operation": access.operation,
        }
        for access in unattributed
    ]

    return {
        "totals": {
            "player_activations": len(activations),
            "research_activations": sum(
                item["searches"] + item["reads"] > 0 for item in activations
            ),
            "searches": sum(item["searches"] for item in activations),
            "reads": sum(item["reads"] for item in activations),
            "search_to_read_conversions": sum(
                item["search_to_read_conversions"] for item in activations
            ),
            "searches_followed_by_no_read": sum(
                item["searches_followed_by_no_read"] for item in activations
            ),
            "zero_result_searches": sum(item["zero_result_searches"] for item in activations),
            "reformulations_after_identical_set": sum(
                item["reformulations_after_identical_set"] for item in activations
            ),
            "search_returned_read_conversions": sum(
                item["search_returned_read_conversions"] for item in activations
            ),
            "searches_without_result_metadata": sum(
                item["searches_without_result_metadata"] for item in activations
            ),
            "activations_continued_after_research": sum(
                item["continued_after_research"] for item in activations
            ),
            "unattributed_accesses": len(unattributed_payload),
        },
        "activations": activations,
        "unattributed_access": unattributed_payload,
    }


def build_game_summary(engine: Engine, game_id: uuid.UUID) -> dict[str, Any]:
    """Derive machine-readable call, token, cost and wall-time totals for one Game."""
    with session_scope(engine) as session:
        return build_game_summary_in_session(session, game_id)


def build_game_summary_in_session(session: Session, game_id: uuid.UUID) -> dict[str, Any]:
    """The summary read inside a caller's transaction — the export's, so its totals count
    exactly the rows the record contains (I-082, INV-GAME-004)."""
    game = session.get(Game, game_id)
    if game is None:
        raise GameNotFound(f"game {game_id!r} does not exist")
    runs = list(
        session.exec(
            select(AgentRun).where(AgentRun.game_id == game_id).order_by(AgentRun.id)
        )
    )
    events = list(
        session.exec(
            select(GameEvent).where(GameEvent.game_id == game_id).order_by(GameEvent.id)
        )
    )
    total_runtime, phase_runtime = _phase_runtimes(events)
    phase_metrics: dict[str, dict[str, int]] = {
        phase: {
            "runtime_ms": phase_runtime.get(phase, 0),
            "activation_wall_ms": 0,
            "provider_wait_ms": 0,
            "activations": 0,
            "model_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "tool_calls": 0,
        }
        for phase in PHASES
    }
    for run in runs:
        if run.phase in phase_metrics:
            metrics = phase_metrics[run.phase]
            metrics["activation_wall_ms"] += run.activation_wall_ms or 0
            metrics["provider_wait_ms"] += run.latency_ms or 0
            metrics["activations"] += 1
            metrics["model_calls"] += run.model_calls
            metrics["input_tokens"] += run.input_tokens
            metrics["output_tokens"] += run.output_tokens
            metrics["reasoning_tokens"] += run.reasoning_tokens
            metrics["cache_read_tokens"] += run.cache_read_tokens
            metrics["cache_write_tokens"] += run.cache_write_tokens
            metrics["tool_calls"] += run.tool_calls
    total_calls = sum(run.model_calls for run in runs)
    calls_with_cost = sum(run.costed_model_calls for run in runs)
    known_costs = [run.cost_usd for run in runs if run.cost_usd is not None]
    invalid_categories: Counter[str] = Counter()
    invalid_reasons: Counter[str] = Counter()
    classified_invalid = 0
    for run in runs:
        breakdown = run.invalid_tool_call_breakdown or {}
        if breakdown.get("schema_version") != INVALID_TOOL_CALL_SCHEMA_VERSION:
            continue
        classified_invalid += int(breakdown.get("total", 0))
        invalid_categories.update(breakdown.get("counts_by_category", {}))
        invalid_reasons.update(breakdown.get("counts_by_reason", {}))
    invalid_total = sum(run.invalid_tool_calls for run in runs)
    round_events = [event for event in events if event.type == "diplomacy_round_completed"]
    close_events = [event for event in events if event.type == "diplomacy_closed"]
    recorded_condition = game.diplomacy_policy_version is not None
    turns: list[dict[str, Any]] = []
    for turn in range(1, game.turn + 1):
        turn_rounds = [event for event in round_events if event.turn == turn]
        turn_closes = [event for event in close_events if event.turn == turn]
        turn_runs = [
            run for run in runs
            if run.role == "player" and run.phase == "diplomacy" and run.turn == turn
        ]
        turns.append({
            "turn": turn,
            "rounds_completed": (
                max(int(event.payload["round"]) for event in turn_rounds)
                if turn_rounds else (0 if recorded_condition else None)
            ),
            "actor_activations": len(turn_runs),
            "provider_calls": sum(run.model_calls for run in turn_runs),
            "input_tokens": sum(run.input_tokens for run in turn_runs),
            "output_tokens": sum(run.output_tokens for run in turn_runs),
            "activation_wall_ms": sum(run.activation_wall_ms or 0 for run in turn_runs),
            "messages_sent": sum(
                event.type == "message_sent"
                and event.turn == turn
                and event.payload.get("phase") == "diplomacy"
                for event in events
            ),
            "close_reason": (
                turn_closes[-1].payload.get("reason") if turn_closes else None
            ),
        })
    summary = {
        "total_runtime_ms": total_runtime,
        "activation_wall_ms": sum(run.activation_wall_ms or 0 for run in runs),
        "provider_wait_ms": sum(run.latency_ms or 0 for run in runs),
        "player_model_calls": sum(run.model_calls for run in runs if run.role == "player"),
        "umpire_calls": sum(run.model_calls for run in runs if run.role == "umpire"),
        "by_phase": phase_metrics,
        "activations": [
            {
                "agent_run_id": run.id,
                "activation_id": run.activation_id,
                "actor_id": run.actor_id,
                "role": run.role,
                "turn": run.turn,
                "phase": run.phase,
                "activation_wall_ms": run.activation_wall_ms,
                "provider_wait_ms": run.latency_ms,
                "model_calls": run.model_calls,
                "input_tokens": run.input_tokens,
                "output_tokens": run.output_tokens,
                "reasoning_tokens": run.reasoning_tokens,
                "cache_read_tokens": run.cache_read_tokens,
                "cache_write_tokens": run.cache_write_tokens,
                "tool_calls": run.tool_calls,
                "invalid_tool_calls": run.invalid_tool_calls,
                "invalid_tool_call_breakdown": dict(run.invalid_tool_call_breakdown),
                "request_topology_available": bool(run.request_topology),
                "measured_request_bytes": sum(
                    request.get("measured_bytes", 0)
                    for request in (run.request_topology or {}).get("requests", [])
                ),
                "completed": run.completed,
            }
            for run in runs
        ],
        "input_tokens": sum(run.input_tokens for run in runs),
        "output_tokens": sum(run.output_tokens for run in runs),
        "reasoning_tokens": sum(run.reasoning_tokens for run in runs),
        "cache_read_tokens": sum(run.cache_read_tokens for run in runs),
        "cache_write_tokens": sum(run.cache_write_tokens for run in runs),
        "estimated_cost_usd": round(sum(known_costs), 12) if known_costs else None,
        "cost_coverage": {
            "calls_with_cost": calls_with_cost,
            "total_calls": total_calls,
            "complete": total_calls > 0 and calls_with_cost == total_calls,
        },
        "invalid_tool_calls": {
            "total": invalid_total,
            "classified": classified_invalid,
            "unclassified": invalid_total - classified_invalid,
            "counts_by_category": dict(invalid_categories),
            "counts_by_reason": dict(sorted(invalid_reasons.items())),
        },
        "request_topology": _request_topology_summary(runs),
        "diplomacy_condition": {
            "policy_version": game.diplomacy_policy_version,
            "mode": game.diplomacy_mode,
            "rounds_per_turn": game.diplomacy_rounds_per_turn,
            "turns": turns,
        },
        "con_condition": _con_condition_summary(session, game),
        "con_reply_condition": _con_reply_condition_summary(session, game),
    }
    summary["resource_research"] = build_resource_research_report_in_session(session, game_id)
    return summary


def _con_condition_summary(session: Session, game: Game) -> dict[str, Any]:
    """Per-Argument opposition evidence for the persisted Con condition (I-055 / D-053).

    ``eligible`` is the sealed challenger count under ``sampled_sealed`` and the opponent
    count otherwise — exactly the set whose completion closed the phase. The condition fields
    stay null for a legacy game; the counts are derived from rows, never from the condition.
    """
    actor_count = len(session.exec(select(Actor.id).where(Actor.game_id == game.id)).all())
    arguments = session.exec(
        select(Argument)
        .where(Argument.game_id == game.id)
        .order_by(Argument.turn, Argument.budget_slot, Argument.id)
    ).all()
    # A pass the runner took after a failed Con activation is not the Actor's choice; its failure
    # row names the Argument, so it is counted apart from chosen passes (I-150, D-105).
    recovery_passes: Counter[str] = Counter()
    for run in session.exec(
        select(AgentRun).where(
            AgentRun.game_id == game.id,
            AgentRun.role == "player",
            AgentRun.completed == False,  # noqa: E712 - SQL comparison
        )
    ).all():
        recovery = (run.provider_metadata or {}).get("recovery") or {}
        if recovery.get("action") == "pass_con" and recovery.get("argument_id"):
            recovery_passes[str(recovery["argument_id"])] += 1
    entries: list[dict[str, Any]] = []
    totals = Counter()
    for argument in arguments:
        cons = session.exec(select(Con).where(Con.argument_id == argument.id)).all()
        completed = session.exec(
            select(ConParticipation.actor_id).where(
                ConParticipation.argument_id == argument.id
            )
        ).all()
        eligible = (
            len(argument.challenger_actor_ids)
            if argument.challenger_actor_ids is not None else max(actor_count - 1, 0)
        )
        conning = {con.actor_id for con in cons}
        entry = {
            "argument_id": str(argument.id),
            "turn": argument.turn,
            "owner_id": argument.actor_id,
            "challenger_actor_ids": (
                list(argument.challenger_actor_ids)
                if argument.challenger_actor_ids is not None else None
            ),
            "eligible": eligible,
            "completed": len(completed),
            "cons": len(cons),
            "challengers_with_con": len(conning),
            "passes": sum(actor_id not in conning for actor_id in completed),
            "recovery_passes": recovery_passes[str(argument.id)],
        }
        entries.append(entry)
        totals.update({
            "arguments": 1,
            "eligible": eligible,
            "completed": entry["completed"],
            "cons": entry["cons"],
            "challengers_with_con": entry["challengers_with_con"],
            "passes": entry["passes"],
            "recovery_passes": entry["recovery_passes"],
        })
    return {
        "policy_version": game.con_policy_version,
        "mode": game.con_mode,
        "challengers_per_argument": game.con_challengers_per_argument,
        "opponents_per_argument": max(actor_count - 1, 0),
        "arguments": entries,
        "totals": {
            key: int(totals.get(key, 0))
            for key in (
                "arguments", "eligible", "completed", "cons", "challengers_with_con", "passes",
                "recovery_passes",
            )
        },
    }


def _con_reply_condition_summary(session: Session, game: Game) -> dict[str, Any]:
    """Per-Argument reply evidence for the persisted Con-reply condition (I-046 / D-060).

    ``arguments_contested`` counts Arguments that had at least one Con (and so, under
    ``visible_reply``, a reply phase); ``arguments_skipped_no_cons`` those that had none;
    ``replies``/``empty_replies``/``reply_points`` describe what the owners actually did.
    """
    arguments = session.exec(
        select(Argument)
        .where(Argument.game_id == game.id)
        .order_by(Argument.turn, Argument.budget_slot, Argument.id)
    ).all()
    entries: list[dict[str, Any]] = []
    totals = Counter()
    for argument in arguments:
        con_count = len(session.exec(select(Con.id).where(Con.argument_id == argument.id)).all())
        reply = session.get(ArgumentReply, argument.id)
        entry = {
            "argument_id": str(argument.id),
            "turn": argument.turn,
            "owner_id": argument.actor_id,
            "cons_revealed": con_count if reply is not None else 0,
            "replied": reply is not None,
            "reply_points": len(reply.points) if reply is not None else 0,
        }
        entries.append(entry)
        totals["arguments"] += 1
        totals["arguments_contested"] += int(con_count > 0)
        totals["arguments_skipped_no_cons"] += int(con_count == 0)
        if reply is not None:
            totals["replies"] += 1
            totals["empty_replies"] += int(not reply.points)
            totals["reply_points"] += len(reply.points)
    return {
        "policy_version": game.con_reply_policy_version,
        "con_reply_mode": game.con_reply_mode,
        "reply_max_points": game.reply_max_points,
        "arguments": entries,
        **{
            key: int(totals.get(key, 0))
            for key in (
                "arguments_contested", "arguments_skipped_no_cons", "replies", "empty_replies",
                "reply_points",
            )
        },
    }


def _duration(milliseconds: int | None) -> str:
    if milliseconds is None:
        return "unavailable"
    seconds = round(milliseconds / 1000)
    hours, remainder = divmod(seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours}h {minutes:02d}m {seconds:02d}s" if hours else f"{minutes}m {seconds:02d}s"


def format_game_summary(summary: dict[str, Any]) -> str:
    """Render the compact human post-game report requested for T-053."""
    lines = [
        f"Total runtime {_duration(summary['total_runtime_ms'])}",
        f"Activation wall time {_duration(summary['activation_wall_ms'])}",
        f"Provider wait {_duration(summary['provider_wait_ms'])}",
        f"Player model calls {summary['player_model_calls']}",
        f"Umpire calls {summary['umpire_calls']}",
        "By phase:",
    ]
    condition = summary.get("diplomacy_condition") or {}
    if condition.get("policy_version"):
        limit = condition.get("rounds_per_turn")
        lines.append(
            "Diplomacy condition " + str(condition.get("mode"))
            + (f" ({limit} rounds/turn)" if limit is not None else "")
        )
    cons = summary.get("con_condition") or {}
    if cons.get("policy_version"):
        limit = cons.get("challengers_per_argument")
        totals = cons.get("totals") or {}
        lines.append(
            "Con condition " + str(cons.get("mode"))
            + (
                f" ({limit} of {cons.get('opponents_per_argument')} challengers/Argument)"
                if limit is not None else ""
            )
            + f": {totals.get('arguments', 0)} Arguments, {totals.get('eligible', 0)} eligible, "
            f"{totals.get('cons', 0)} Cons, {totals.get('passes', 0)} passes"
            + (
                f" ({totals['recovery_passes']} after failed activations)"
                if totals.get("recovery_passes") else ""
            )
        )
    reply = summary.get("con_reply_condition") or {}
    if reply.get("policy_version"):
        limit = reply.get("reply_max_points")
        lines.append(
            "Con-reply condition " + str(reply.get("con_reply_mode"))
            + (f" ({limit} points max)" if limit is not None else "")
            + f": {reply.get('arguments_contested', 0)} contested, "
            f"{reply.get('replies', 0)} replies, {reply.get('empty_replies', 0)} empty, "
            f"{reply.get('reply_points', 0)} points"
        )
    for phase, values in summary["by_phase"].items():
        lines.append(
            f"  {phase.title()} {values['activations']} activations / "
            f"{values['model_calls']} model calls / {values['tool_calls']} tool calls / "
            f"{_duration(values['runtime_ms'])} phase wall / "
            f"{_duration(values['provider_wait_ms'])} provider wait"
        )
    research = summary.get("resource_research", {}).get("totals", {})
    if research:
        lines.append(
            "Resource research: "
            f"{research['research_activations']} activations, "
            f"{research['searches']} searches, {research['reads']} reads, "
            f"{research['unattributed_accesses']} unattributed accesses"
        )
    invalid = summary.get("invalid_tool_calls", {})
    if invalid.get("total"):
        categories = ", ".join(
            f"{name}={count}"
            for name, count in invalid.get("counts_by_category", {}).items()
        )
        classification = (
            f"; {categories}" if categories else "; no classified legacy detail"
        )
        lines.append(
            f"Invalid tool-call aggregate {invalid['total']} "
            f"({invalid['classified']} classified, {invalid['unclassified']} unclassified"
            f"{classification})"
        )
    topology = summary.get("request_topology", {})
    if topology.get("player_model_calls"):
        provider_usage = topology["provider_usage"]
        lines.append(
            "Request topology "
            f"{topology['measured_requests']}/{topology['player_model_calls']} player calls "
            f"measured ({topology['measured_bytes']:,} logical bytes); provider cache usage "
            f"reported for {provider_usage['reported_requests']} calls"
        )
    lines.extend(
        [
            f"Input tokens {summary['input_tokens']:,}",
            f"Output tokens {summary['output_tokens']:,}",
        ]
    )
    cost = summary["estimated_cost_usd"]
    coverage = summary["cost_coverage"]
    if cost is None:
        lines.append("Estimated cost unavailable")
    elif coverage["complete"]:
        lines.append(f"Estimated cost ${cost:.6f}")
    else:
        lines.append(
            f"Estimated cost ${cost:.6f} (partial: {coverage['calls_with_cost']}/"
            f"{coverage['total_calls']} calls)"
        )
    return "\n".join(lines)
