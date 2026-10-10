"""Build the after-action share card: one self-contained HTML page drawn from the report model.

The card shows only what the export records; the page needs no network to render, so a capture
is the same on every machine that has the same fonts (#18, D-121).
"""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

from aar.model import AfterActionReport

ACTION_CHARS = 90
CATEGORICAL_SLOTS = 8
# A long shot or a bust is only worth the headline when the odds were actually against it.
LONG_SHOT_MAX = 0.5
BUST_MIN = 0.5
_DATA_MARKER = "/*AAR_DATA*/null"


def _truncate(text: str, limit: int = ACTION_CHARS) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _title(report: AfterActionReport) -> str:
    """The scenario's display name, or its id made readable when the export carries no name."""
    if report.scenario_name and report.scenario_name != report.scenario_id:
        return report.scenario_name
    return report.scenario_id.replace("-", " ").replace("_", " ").title()


def _highlight(report: AfterActionReport) -> dict[str, Any] | None:
    """The one moment the card calls out: a real long shot, else a real bust, else the most
    contested Argument."""
    h = report.highlights
    for kind, argument_id in (("long_shot", h.long_shot), ("bust", h.bust)):
        if argument_id is None:
            continue
        argument = report.argument(argument_id)
        p = argument.ruling.probability if argument.ruling else None
        if p is None:
            continue
        if (kind == "long_shot" and p <= LONG_SHOT_MAX) or (kind == "bust" and p >= BUST_MIN):
            return {"kind": kind, "actor_id": argument.actor_id, "turn": argument.turn,
                    "action": _truncate(argument.action), "probability": p,
                    "cons": len(argument.cons)}
    if h.most_contested is not None:
        argument = report.argument(h.most_contested)
        return {"kind": "most_contested", "actor_id": argument.actor_id, "turn": argument.turn,
                "action": _truncate(argument.action),
                "probability": argument.ruling.probability if argument.ruling else None,
                "cons": len(argument.cons)}
    return None


def card_data(report: AfterActionReport) -> dict[str, Any]:
    """The JSON the card template renders; every value comes from the report."""
    return {
        "title": _title(report),
        "turns": report.turns_played,
        "max_turns": report.max_turns,
        "seed": report.seed,
        "actors": [
            {"id": a.id, "name": a.name, "model": a.model,
             # Colours follow the Actor in fixed order and are never cycled (dataviz rule).
             "slot": i if i < CATEGORICAL_SLOTS else None}
            for i, a in enumerate(report.actors)
        ],
        "rulings": [
            {"turn": a.turn, "actor_id": a.actor_id, "action": _truncate(a.action),
             "mode": a.ruling.mode if a.ruling else None,
             "probability": a.ruling.probability if a.ruling else None,
             "roll": a.ruling.roll if a.ruling else None,
             "success": a.ruling.success if a.ruling else None}
            for t in report.turns for a in t.arguments
        ],
        "highlight": _highlight(report),
        "tallies": [
            {"actor_id": a.id, **report.tallies[a.id].model_dump()} for a in report.actors
        ],
    }


def render_card_html(report: AfterActionReport) -> str:
    template = resources.files("aar").joinpath("templates/card.html").read_text(encoding="utf-8")
    # "</" is escaped so no value can close the data <script> element.
    payload = json.dumps(card_data(report), ensure_ascii=False).replace("</", "<\\/")
    return template.replace(_DATA_MARKER, payload)
