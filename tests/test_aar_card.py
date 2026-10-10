"""The share card is one self-contained HTML file built from the report model (#18)."""

from __future__ import annotations

import json
import re
from typing import Any

from aar.card import card_data, render_card_html
from aar.model import build_report
from tests.aar_helpers import minimal_export

_DATA = re.compile(r'<script type="application/json" id="aar-data">(.*?)</script>', re.S)


def _embedded(html: str) -> Any:
    match = _DATA.search(html)
    assert match is not None
    return json.loads(match.group(1))


def _argument(i: int, actor: str, turn: int, p: float | None, success: bool,
              mode: str = "probabilistic", action: str | None = None, cons: int = 0
              ) -> dict[str, Any]:
    return {
        "argument": {"id": f"x{i:02d}", "turn": turn, "actor_id": actor,
                     "action": action or f"action {i}", "desired_effect": "d"},
        "ruling": {"argument_id": f"x{i:02d}", "mode": mode, "probability": p,
                   "roll": 0.5 if p is not None else None, "success": success,
                   "created_at": f"2026-01-01T00:{i:02d}:00"},
        "cons": [{"id": f"c{i}-{n}", "argument_id": f"x{i:02d}", "actor_id": "b",
                  "reason": "no"} for n in range(cons)],
    }


def _export(*items: dict[str, Any], **game: Any) -> dict[str, Any]:
    export = minimal_export(
        arguments=[i["argument"] for i in items], adjudications=[i["ruling"] for i in items],
        cons=[c for i in items for c in i["cons"]],
    )
    export["game"].update(game)
    return export


def test_embedded_data_matches_card_data(canonical_export: dict) -> None:
    report = build_report(canonical_export)
    html = render_card_html(report)
    assert _embedded(html) == card_data(report)


def test_card_data_lists_every_ruling_in_play_order(canonical_export: dict) -> None:
    data = card_data(build_report(canonical_export))
    assert data["title"] == "Deterministic Game"
    assert (data["turns"], len(data["actors"]), len(data["rulings"])) == (6, 3, 18)
    first = data["rulings"][0]
    assert (first["turn"], first["actor_id"], first["success"]) == (1, "arkania", True)
    assert [a["slot"] for a in data["actors"]] == [0, 1, 2]


def test_card_is_self_contained(canonical_export: dict) -> None:
    html = render_card_html(build_report(canonical_export))
    assert "<script src" not in html
    assert "https://" not in html and "http://" not in html


def test_data_cannot_close_the_script_element() -> None:
    report = build_report(_export(_argument(1, "a", 1, 0.4, True, action="</script><b>x")))
    html = render_card_html(report)
    assert "</script><b>" not in html
    assert _embedded(html)["rulings"][0]["action"] == "</script><b>x"


def test_long_actions_are_truncated() -> None:
    report = build_report(_export(_argument(1, "a", 1, 0.4, True, action="w" * 200)))
    action = card_data(report)["rulings"][0]["action"]
    assert len(action) == 90 and action.endswith("…")


def test_many_arguments_and_a_long_title_still_render() -> None:
    items = [_argument(i, "ab"[i % 2], 1 + i // 2, 0.5, i % 3 == 0) for i in range(12)]
    report = build_report(_export(*items, scenario_id="s" * 200, turn=6, max_turns=6))
    data = card_data(report)
    assert len(data["rulings"]) == 12
    assert len(data["title"]) == 200          # the template clamps and ellipsises it
    assert render_card_html(report)


def test_actors_past_eight_get_no_categorical_slot() -> None:
    export = minimal_export(actors=[{"id": f"a{i}", "name": f"A{i}"} for i in range(10)])
    slots = [a["slot"] for a in card_data(build_report(export))["actors"]]
    assert slots == [0, 1, 2, 3, 4, 5, 6, 7, None, None]


def test_highlight_prefers_a_real_long_shot() -> None:
    report = build_report(_export(_argument(1, "a", 1, 0.2, True), _argument(2, "b", 1, 0.9, False)))
    highlight = card_data(report)["highlight"]
    assert (highlight["kind"], highlight["actor_id"], highlight["probability"]) == (
        "long_shot", "a", 0.2)


def test_highlight_falls_back_to_bust_then_most_contested() -> None:
    bust = build_report(_export(_argument(1, "a", 1, 0.9, True), _argument(2, "b", 1, 0.8, False)))
    assert card_data(bust)["highlight"]["kind"] == "bust"
    contested = build_report(_export(_argument(1, "a", 1, 1.0, True, cons=2)))
    highlight = card_data(contested)["highlight"]
    assert (highlight["kind"], highlight["cons"]) == ("most_contested", 2)
    assert card_data(build_report(minimal_export()))["highlight"] is None


def test_tallies_follow_actor_order(canonical_export: dict) -> None:
    tallies = card_data(build_report(canonical_export))["tallies"]
    assert [t["actor_id"] for t in tallies] == ["arkania", "belvar", "cestia"]
    assert all(t["granted"] == 6 and t["failed"] == 0 for t in tallies)
