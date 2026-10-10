"""The counted facts behind the cards: who played which role with which model, and what it cost.

Everything here is read from the export (the run manifest, the agent runs, the run summary, the
Messages and the adjudications), never written by a model, so the run screen and the model credit
can be trusted as they stand (#18). Exports from before run manifests existed still render; the
missing values are ``None`` or ``"unknown"``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

_BRANDS = {
    "deepseek": "DeepSeek", "gpt": "GPT", "claude": "Claude", "gemini": "Gemini", "llama": "Llama",
    "qwen": "Qwen", "mistral": "Mistral", "grok": "Grok", "kimi": "Kimi", "glm": "GLM",
}


def model_name(model_id: str) -> str:
    """``deepseek/deepseek-v4-flash-0731`` → ``DeepSeek V4 Flash``: provider and date dropped."""
    name = re.sub(r"-\d{4,}$", "", model_id.split("/")[-1])
    words = []
    for token in name.split("-"):
        if token in _BRANDS:
            words.append(_BRANDS[token])
        elif re.fullmatch(r"v?\d[\w.]*", token):
            words.append(token.upper())
        else:
            words.append(token.capitalize())
    return " ".join(words)


@dataclass
class UsageRow:
    """One role's line on the run screen."""

    key: str
    model: str = "unknown"
    calls: int = 0
    tokens: int = 0
    cost: float | None = None
    messages: int = 0
    secret: int = 0
    prompt_version: str | None = None


@dataclass
class RunFacts:
    rows: dict[str, UsageRow] = field(default_factory=dict)
    assignments: dict[str, str] = field(default_factory=dict)
    cost: float | None = None
    cost_partial: bool = False
    costed_calls: int | None = None
    total_calls: int | None = None
    tokens: int = 0
    reasoning_tokens: int = 0
    runtime_hours: float | None = None
    model_calls: int = 0
    granted: int = 0
    adjudicated: int = 0
    rolled: int = 0
    messages: int = 0
    secret_messages: int = 0
    turns: int = 0
    scenario_id: str = ""
    scenario_version: str | None = None
    seed: int | None = None
    prompt_versions: dict[str, str] = field(default_factory=dict)

    def row(self, key: str) -> UsageRow:
        return self.rows.get(key) or UsageRow(key=key)

    def model_credit(self, names: dict[str, str], noun: str) -> str:
        """``DeepSeek V4 Flash played all 8 parties``, or each Actor's model when they differ."""
        if not self.assignments:
            return ""
        models = {model_name(m) for m in self.assignments.values()}
        if len(models) == 1:
            count = len(self.assignments)
            return f"{models.pop()} played {'both' if count == 2 else f'all {count}'} {noun}"
        return " · ".join(
            f"{names.get(actor, actor)}: {model_name(model)}" for actor, model in self.assignments.items()
        )


def run_facts(export: dict[str, Any]) -> RunFacts:
    manifest = export.get("run_manifest") or {}
    summary = export.get("run_summary") or {}
    assignments = dict(export.get("model_assignments") or {})
    facts = RunFacts(assignments=assignments)

    rows: dict[str, UsageRow] = {}
    for run in export.get("agent_runs") or []:
        key = run.get("actor_id") or run.get("role") or "unknown"
        row = rows.setdefault(key, UsageRow(key=key))
        row.calls += run.get("model_calls") or 0
        row.tokens += (run.get("input_tokens") or 0) + (run.get("output_tokens") or 0)
        if run.get("cost_usd") is not None:
            row.cost = (row.cost or 0.0) + run["cost_usd"]
        if row.model == "unknown":
            served = run.get("model_returned") or run.get("model_requested")
            row.model = model_name(served) if served else "unknown"
    for actor in export.get("actors") or []:
        row = rows.setdefault(actor["id"], UsageRow(key=actor["id"]))
        if actor["id"] in assignments:
            row.model = model_name(assignments[actor["id"]])
    for role in ("umpire", "consequence"):
        config = manifest.get(role) or {}
        if config:
            row = rows.setdefault(role, UsageRow(key=role))
            if config.get("model"):
                row.model = model_name(config["model"])
            row.prompt_version = config.get("prompt_version")

    sent: defaultdict[str, list[int]] = defaultdict(lambda: [0, 0])
    for message in export.get("messages") or []:
        sent[message["sender_actor_id"]][0] += 1
        if not message.get("public"):
            sent[message["sender_actor_id"]][1] += 1
    for key, (total, secret) in sent.items():
        row = rows.setdefault(key, UsageRow(key=key))
        row.messages, row.secret = total, secret
    facts.rows = rows

    coverage = summary.get("cost_coverage") or {}
    facts.cost = summary.get("estimated_cost_usd")
    facts.cost_partial = bool(coverage) and not coverage.get("complete", True)
    facts.costed_calls = coverage.get("calls_with_cost")
    facts.total_calls = coverage.get("total_calls")
    facts.tokens = (summary.get("input_tokens") or 0) + (summary.get("output_tokens") or 0)
    facts.reasoning_tokens = summary.get("reasoning_tokens") or 0
    runtime = summary.get("total_runtime_ms")
    facts.runtime_hours = round(runtime / 3_600_000, 2) if runtime else None
    facts.model_calls = (summary.get("player_model_calls") or 0) + (summary.get("umpire_calls") or 0)

    adjudications = export.get("adjudications") or []
    facts.adjudicated = len(adjudications)
    facts.granted = sum(1 for a in adjudications if a.get("success"))
    facts.rolled = sum(1 for a in adjudications if a.get("mode") == "probabilistic")
    messages = export.get("messages") or []
    facts.messages = len(messages)
    facts.secret_messages = sum(1 for m in messages if not m.get("public"))

    game = export.get("game") or {}
    facts.turns = int(game.get("turn") or 0)
    facts.scenario_id = game.get("scenario_id") or ""
    version = game.get("scenario_version")
    facts.scenario_version = str(version) if version is not None else None
    facts.seed = game.get("seed")
    for label, section in (("player", "player"), ("umpire", "umpire"), ("consequences", "consequence")):
        version_name = (manifest.get(section) or {}).get("prompt_version")
        if version_name:
            facts.prompt_versions[label] = version_name
    return facts
