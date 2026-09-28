"""Raw benchmark output tables (T-058 — TRD §32, WBS 5.6, benchmark-methodology §25).

A pure projection over one ``(attempt, export)`` pair per scheduled attempt, read back from a
batch directory written by :mod:`benchmark.runner`. The §25 columns lead each table; the WBS 5.6
minimum metrics follow as extra raw columns. Nothing here interprets behaviour (TRD §32: raw data
first).

**Cell convention (I-086, D-075).** Model- and provider-authored text reaches these tables
(``action``, ``desired_effect``, ``query``, ``failure_reason``), and a spreadsheet interprets a
cell beginning with ``=``, ``+``, ``-``, ``@``, tab or CR as a formula. A ``str`` cell whose first
character is one of those — or ``'`` — is therefore written with a single ``'`` prefixed. Because
a genuine leading ``'`` is prefixed too, the encoding is reversible: strip exactly one leading
``'`` from every string cell to recover the original text. Numbers, booleans, empty cells and the
JSON-dumped structures (which begin with ``{`` or ``[``) are never prefixed. The per-attempt
``export.json`` remains the authority; the tables are a derived projection (methodology §25).
"""

from __future__ import annotations

from collections.abc import Iterable
import csv
import json
from pathlib import Path
from typing import Any


TABLE_NAMES = ("games", "agents", "arguments", "messages", "resource_access")


def load_attempts(output_dir: Path) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """Read every ``games/<attempt>/`` pair back from a batch directory, in schedule order.

    Schedule order is the integer ``attempt.position`` recorded in ``attempt.json``, not the
    directory name — ``1000-…`` sorts before ``998-…`` lexically (I-084).
    """
    pairs = []
    games_dir = Path(output_dir) / "games"
    for directory in games_dir.iterdir() if games_dir.is_dir() else []:
        attempt_path, export_path = directory / "attempt.json", directory / "export.json"
        if attempt_path.is_file() and export_path.is_file():
            pairs.append((
                json.loads(attempt_path.read_text(encoding="utf-8")),
                json.loads(export_path.read_text(encoding="utf-8")),
            ))
    pairs.sort(key=lambda pair: pair[0]["position"])
    return pairs


def build_tables(
    pairs: Iterable[tuple[dict[str, Any], dict[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    tables: dict[str, list[dict[str, Any]]] = {name: [] for name in TABLE_NAMES}
    for attempt, export in pairs:
        tables["games"].append(_game_row(attempt, export))
        tables["agents"].extend(_agent_rows(attempt, export))
        tables["arguments"].extend(_argument_rows(export))
        tables["messages"].extend(_message_rows(export))
        tables["resource_access"].extend(_resource_access_rows(export))
    return tables


def write_tables(output_dir: Path) -> dict[str, Path]:
    """Write the five CSV tables from what is on disk under ``output_dir``."""
    output_dir = Path(output_dir)
    tables = build_tables(load_attempts(output_dir))
    written = {}
    for name, rows in tables.items():
        columns = _TABLE_COLUMNS[name]
        path = output_dir / f"{name}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                writer.writerow({column: _cell(row.get(column)) for column in columns})
        written[name] = path
    return written


# Leading characters a spreadsheet treats as a formula (OWASP CSV injection), plus the escape
# character itself so the one-quote strip is lossless.
_FORMULA_LEADS = ("=", "+", "-", "@", "\t", "\r", "'")


def _cell(value: Any) -> Any:
    """CSV cells: booleans as ``true``/``false``, ``None`` empty, structures as JSON, and
    formula-leading strings prefixed with one ``'`` (see the module docstring)."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True, allow_nan=False)
    if isinstance(value, str) and value.startswith(_FORMULA_LEADS):
        return "'" + value
    return value


_TABLE_COLUMNS: dict[str, list[str]] = {
    "games": [
        # benchmark-methodology §25
        "game_id", "experiment_id", "scenario_id", "scenario_version", "assignment_id",
        "repetition_index", "seed", "scenario_content_hash", "resource_manifest_hash",
        "completed", "failure_reason", "started_at", "completed_at",
        # attempt identity and outcome (§21), condition provenance (D-054, §4)
        "attempt_position", "assignment_index", "failure_category", "turn_reached",
        "actor_affected", "run_class", "analysis_plan_sha256", "player_prompt_version",
        "player_prompt_hash",
        # I-105: the sealed player condition the game-level ``invalid_tool_calls`` was produced
        # under (D-046 ``activation_mode``, D-079 ``tool_surface``), copied from the manifest.
        "player_activation_mode", "player_tool_surface",
        "umpire_model", "umpire_prompt_version", "diplomacy_mode",
        "con_mode", "consequence_mode", "consequence_model", "consequence_prompt_version",
        "code_version", "environment_sha256", "turns_completed",
        # WBS 5.6 minimum metrics at game level
        "arguments", "argument_successes", "messages", "cons", "resource_searches",
        "resource_reads", "tool_calls", "invalid_tool_calls", "stalls", "input_tokens",
        "output_tokens", "cost_usd", "latency_ms",
        # T-055 / T-057: the assignment policy, the actual roster, and the interpretation label
        "rotation_policy", "roster", "benchmark_mode",
        # T-056: attempt status (completed | failed | not_run) and the sealed-condition check
        "status", "umpire_condition_match",
        # I-078: the *observed* seed (from the authoritative manifest; ``seed`` is the planned
        # one) and whether every declared expectation held for this attempt
        "seed_observed", "condition_match",
    ],
    "agents": [
        "game_id", "actor_id", "model_requested", "model_returned", "provider", "prompt_version",
        "prompt_hash", "model_parameters", "reasoning_configuration", "agent_framework",
        "agent_framework_version", "tool_schema_version", "input_tokens", "output_tokens",
        "tool_calls", "invalid_tool_calls", "stalls", "cost_usd", "latency_ms", "completed",
        "failure_reason",
        # I-102: the last failed activation of this Actor, recovered or not. ``completed`` /
        # ``failure_reason`` above are the §21 *attempt* outcome; ``failed_activations`` below
        # is the count this reason belongs to.
        "last_activation_failure_reason",
        "assignment_id", "repetition_index", "activations", "failed_activations", "model_calls",
        "arguments", "argument_successes", "messages", "public_messages", "private_messages",
        "cons", "resource_searches", "resource_reads",
        # T-057: the interpretation label (model | agent) on every Actor row
        "benchmark_mode",
        # I-078: the model the manifest recorded for this Seat (``model_requested`` is the
        # scheduled one, ``model_returned`` what the provider reported)
        "model_assigned",
    ],
    "arguments": [
        "game_id", "turn", "argument_id", "actor_id", "action", "desired_effect", "pros_count",
        "umpire_probability", "roll", "success",
        "budget_slot", "status", "adjudication_mode", "cons",
    ],
    "messages": [
        "game_id", "turn", "message_id", "sender_actor_id", "public", "recipient_count",
        "created_at",
    ],
    "resource_access": [
        "game_id", "turn", "actor_id", "operation", "query", "resource_id", "created_at",
        "activation_id", "result_count", "result_resource_ids", "result_set_sha256",
    ],
}


def _turns_completed(export: dict[str, Any]) -> int:
    return sum(1 for event in export.get("events") or [] if event.get("type") == "turn_completed")


def _game_row(attempt: dict[str, Any], export: dict[str, Any]) -> dict[str, Any]:
    game = export.get("game") or {}
    manifest = export.get("run_manifest") or {}
    benchmark = manifest.get("benchmark") or {}
    plan = benchmark.get("analysis_plan") or {}
    summary = export.get("run_summary") or {}
    hashes = export.get("scenario_hashes") or {}
    player_runs = [r for r in export.get("agent_runs") or [] if r.get("role") == "player"]
    access = export.get("resource_access") or []
    row = {
        "game_id": game.get("id"),
        "experiment_id": attempt.get("experiment_id") or benchmark.get("experiment_id"),
        "scenario_id": game.get("scenario_id"),
        "scenario_version": game.get("scenario_version"),
        "assignment_id": attempt["assignment_id"],
        "repetition_index": attempt["repetition_index"],
        "seed": attempt["seed"],
        "scenario_content_hash": hashes.get("scenario_content_sha256"),
        "resource_manifest_hash": hashes.get("resource_manifest_sha256"),
        "completed": attempt["completed"],
        "failure_reason": attempt.get("failure_reason"),
        "started_at": attempt.get("started_at"),
        "completed_at": attempt.get("finished_at") if attempt["completed"] else None,
        "attempt_position": attempt["position"],
        "assignment_index": attempt["assignment_index"],
        "failure_category": attempt.get("failure_category"),
        "turn_reached": attempt.get("turn_reached"),
        "actor_affected": attempt.get("actor_affected"),
        "run_class": benchmark.get("run_class"),
        "analysis_plan_sha256": plan.get("sha256"),
        "player_prompt_version": (manifest.get("player") or {}).get("prompt_version"),
        "player_prompt_hash": (manifest.get("player") or {}).get("prompt_hash"),
        # Copied, never derived: a manifest that predates D-079 has no ``tool_surface`` and
        # projects an empty cell (TRD §32, raw data first).
        "player_activation_mode": (manifest.get("player") or {}).get("activation_mode"),
        "player_tool_surface": (manifest.get("player") or {}).get("tool_surface"),
        "umpire_model": (manifest.get("umpire") or {}).get("model"),
        "umpire_prompt_version": (manifest.get("umpire") or {}).get("prompt_version"),
        "diplomacy_mode": (manifest.get("game_loop") or {}).get("diplomacy_mode"),
        "con_mode": (manifest.get("game_loop") or {}).get("con_mode"),
        # I-114 / D-092: the Consequence condition, copied. The mode is the export's ``game``
        # field because a ``none`` manifest omits it (D-091); the resolver columns are empty
        # for a ``none`` game, which seals no resolver.
        "consequence_mode": game.get("consequence_mode"),
        "consequence_model": (manifest.get("consequence") or {}).get("model"),
        "consequence_prompt_version": (manifest.get("consequence") or {}).get("prompt_version"),
        "code_version": manifest.get("code_version"),
        # I-085: the runtime-environment fingerprint's hash (the held-constant path).
        "environment_sha256": (manifest.get("environment") or {}).get("sha256"),
        # I-084: a turn is complete when state-machine §22 emits ``turn_completed``; ``game.turn``
        # (``turn_reached``) has already advanced into the turn an interrupted game was in.
        "turns_completed": _turns_completed(export) if game else None,
        "rotation_policy": attempt.get("rotation_policy") or benchmark.get("rotation_policy"),
        "roster": ";".join(f"{actor}={model}" for actor, model in attempt["models"].items()),
        "benchmark_mode": attempt.get("benchmark_mode") or benchmark.get("benchmark_mode"),
        "status": attempt.get("status") or ("completed" if attempt["completed"] else "failed"),
        "umpire_condition_match": attempt.get("umpire_condition_match"),
        "seed_observed": manifest.get("seed") if manifest else None,
        "condition_match": (
            attempt["condition_differences"] == []
            if attempt.get("condition_differences") is not None else None
        ),
    }
    if not game:
        return row
    row.update({
        "arguments": len(export.get("arguments") or []),
        "argument_successes": sum(
            1 for a in export.get("adjudications") or [] if a.get("success")
        ),
        "messages": len(export.get("messages") or []),
        "cons": len(export.get("cons") or []),
        "resource_searches": sum(1 for a in access if a.get("operation") == "search"),
        "resource_reads": sum(1 for a in access if a.get("operation") == "read"),
        "tool_calls": _total(player_runs, "tool_calls"),
        "invalid_tool_calls": _total(player_runs, "invalid_tool_calls"),
        "stalls": _total(player_runs, "stalls"),
        "input_tokens": summary.get("input_tokens", _total(player_runs, "input_tokens")),
        "output_tokens": summary.get("output_tokens", _total(player_runs, "output_tokens")),
        "cost_usd": summary.get("estimated_cost_usd"),
        "latency_ms": _total(player_runs, "latency_ms"),
    })
    return row


def _total(runs: list[dict[str, Any]], key: str) -> int:
    return sum(int(run.get(key) or 0) for run in runs)


def _agent_rows(attempt: dict[str, Any], export: dict[str, Any]) -> list[dict[str, Any]]:
    game = export.get("game") or {}
    game_id = game.get("id")
    runs_by_actor: dict[str, list[dict[str, Any]]] = {}
    for run in export.get("agent_runs") or []:
        if run.get("role") == "player" and run.get("actor_id"):
            runs_by_actor.setdefault(str(run["actor_id"]), []).append(run)
    messages = export.get("messages") or []
    arguments = export.get("arguments") or []
    successes = {a["argument_id"] for a in export.get("adjudications") or [] if a.get("success")}
    cons = export.get("cons") or []
    access = export.get("resource_access") or []
    player_condition = (export.get("run_manifest") or {}).get("player") or {}
    assigned = player_condition.get("assignments") or {}
    rows = []
    for actor_id, model in attempt["models"].items():
        runs = runs_by_actor.get(actor_id, [])
        first = runs[0] if runs else {}
        failures = [run for run in runs if not run.get("completed") or run.get("failure_reason")]
        row = {
            "game_id": game_id,
            "actor_id": actor_id,
            "model_requested": model,
            "model_returned": _distinct(run.get("model_returned") for run in runs),
            "provider": _distinct(run.get("provider") for run in runs),
            "prompt_version": first.get("prompt_version"),
            "prompt_hash": first.get("prompt_hash"),
            "model_parameters": first.get("model_parameters"),
            "reasoning_configuration": first.get("reasoning_configuration"),
            "agent_framework": first.get("agent_framework"),
            # I-102: the failure-path AgentRun records ``agent_framework_version: None`` on
            # purpose (T-053) — the version is a property of the run and the manifest seals
            # it. Read the manifest; without one, any completed run (``_distinct`` drops the
            # ``None``), never ``first``.
            "agent_framework_version": (
                player_condition.get("agent_framework_version")
                or _distinct(run.get("agent_framework_version") for run in runs)
            ),
            "tool_schema_version": first.get("tool_schema_version"),
            # methodology §21: ``completed`` / ``failure_reason`` are the *attempt* outcome,
            # attributed to the one ``actor_affected``. A fault the coordinator recovered from
            # is not an attempt failure; it is reported under ``failed_activations`` and
            # ``last_activation_failure_reason`` (I-102).
            "completed": bool(attempt["completed"]),
            "failure_reason": (
                attempt.get("failure_reason")
                if attempt.get("actor_affected") == actor_id else None
            ),
            "last_activation_failure_reason": (
                failures[-1].get("failure_reason") if failures else None
            ),
            "assignment_id": attempt["assignment_id"],
            "repetition_index": attempt["repetition_index"],
            "benchmark_mode": attempt.get("benchmark_mode"),
            "model_assigned": assigned.get(actor_id),
        }
        if game:
            row.update({
                "input_tokens": _total(runs, "input_tokens"),
                "output_tokens": _total(runs, "output_tokens"),
                "tool_calls": _total(runs, "tool_calls"),
                "invalid_tool_calls": _total(runs, "invalid_tool_calls"),
                "stalls": _total(runs, "stalls"),
                "cost_usd": (
                    sum(run["cost_usd"] for run in runs if run.get("cost_usd") is not None)
                    if any(run.get("cost_usd") is not None for run in runs) else None
                ),
                "latency_ms": _total(runs, "latency_ms"),
                "activations": len(runs),
                "failed_activations": len(failures),
                "model_calls": _total(runs, "model_calls"),
                "arguments": sum(1 for a in arguments if a.get("actor_id") == actor_id),
                "argument_successes": sum(
                    1 for a in arguments
                    if a.get("actor_id") == actor_id and a.get("id") in successes
                ),
                "messages": sum(1 for m in messages if m.get("sender_actor_id") == actor_id),
                "public_messages": sum(
                    1 for m in messages if m.get("sender_actor_id") == actor_id and m.get("public")
                ),
                "private_messages": sum(
                    1 for m in messages
                    if m.get("sender_actor_id") == actor_id and not m.get("public")
                ),
                "cons": sum(1 for c in cons if c.get("actor_id") == actor_id),
                "resource_searches": sum(
                    1 for a in access
                    if a.get("actor_id") == actor_id and a.get("operation") == "search"
                ),
                "resource_reads": sum(
                    1 for a in access
                    if a.get("actor_id") == actor_id and a.get("operation") == "read"
                ),
            })
        rows.append(row)
    return rows


def _distinct(values: Iterable[Any]) -> str | None:
    seen = sorted({str(value) for value in values if value})
    return "|".join(seen) if seen else None


def _argument_rows(export: dict[str, Any]) -> list[dict[str, Any]]:
    game_id = (export.get("game") or {}).get("id")
    adjudications = {a["argument_id"]: a for a in export.get("adjudications") or []}
    cons_per_argument: dict[str, int] = {}
    for con in export.get("cons") or []:
        cons_per_argument[con["argument_id"]] = cons_per_argument.get(con["argument_id"], 0) + 1
    rows = []
    for argument in export.get("arguments") or []:
        adjudication = adjudications.get(argument["id"]) or {}
        rows.append({
            "game_id": game_id,
            "turn": argument.get("turn"),
            "argument_id": argument["id"],
            "actor_id": argument.get("actor_id"),
            "action": argument.get("action"),
            "desired_effect": argument.get("desired_effect"),
            "pros_count": len(argument.get("pros") or []),
            "umpire_probability": adjudication.get("probability"),
            "roll": adjudication.get("roll"),
            "success": adjudication.get("success") if adjudication else None,
            "budget_slot": argument.get("budget_slot"),
            "status": argument.get("status"),
            "adjudication_mode": adjudication.get("mode"),
            "cons": cons_per_argument.get(argument["id"], 0),
        })
    return rows


def _message_rows(export: dict[str, Any]) -> list[dict[str, Any]]:
    game_id = (export.get("game") or {}).get("id")
    return [
        {
            "game_id": game_id,
            "turn": message.get("turn"),
            "message_id": message.get("id"),
            "sender_actor_id": message.get("sender_actor_id"),
            "public": bool(message.get("public")),
            "recipient_count": len(message.get("recipients") or []),
            "created_at": message.get("created_at"),
        }
        for message in export.get("messages") or []
    ]


def _resource_access_rows(export: dict[str, Any]) -> list[dict[str, Any]]:
    game_id = (export.get("game") or {}).get("id")
    return [
        {
            "game_id": game_id,
            "turn": access.get("turn"),
            "actor_id": access.get("actor_id"),
            "operation": access.get("operation"),
            "query": access.get("query"),
            "resource_id": access.get("resource_id"),
            "created_at": access.get("created_at"),
            "activation_id": access.get("activation_id"),
            # I-099; absent from a pre-format-13 export, exported as empty cells.
            "result_count": access.get("result_count"),
            "result_resource_ids": access.get("result_resource_ids"),
            "result_set_sha256": access.get("result_set_sha256"),
        }
        for access in export.get("resource_access") or []
    ]
