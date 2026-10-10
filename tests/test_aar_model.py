"""The after-action report model is a pure, deterministic reading of an export (#16)."""

from __future__ import annotations

from typing import Any

from aar.model import build_report
from tests.aar_helpers import minimal_export


def test_report_covers_every_turn_and_actor(canonical_export: dict) -> None:
    report = build_report(canonical_export)
    assert report.turns_played == 6
    assert [t.number for t in report.turns] == [1, 2, 3, 4, 5, 6]
    assert [a.id for a in report.actors] == ["arkania", "belvar", "cestia"]
    assert sum(len(t.arguments) for t in report.turns) == 18


def test_arguments_follow_play_order(canonical_export: dict) -> None:
    turn_one = build_report(canonical_export).turns[0]
    assert [a.actor_id for a in turn_one.arguments] == ["arkania", "belvar", "cestia"]


def test_report_carries_a_known_ruling(canonical_export: dict) -> None:
    first = build_report(canonical_export).turns[0].arguments[0]
    assert first.actor_id == "arkania"
    assert first.ruling is not None
    assert first.ruling.mode == "probabilistic"
    assert first.ruling.probability == 1.0
    assert first.ruling.success is True
    assert first.ruling.roll is not None
    assert first.effects == ["arkania advances its position (turn 1)"]
    assert len(first.cons) == 2


def test_report_is_deterministic(canonical_export: dict) -> None:
    assert build_report(canonical_export) == build_report(canonical_export)


def test_private_messages_keep_their_recipients(canonical_export: dict) -> None:
    messages = [m for t in build_report(canonical_export).turns for m in t.messages]
    assert len(messages) == 18
    assert all(m.recipients for m in messages if not m.public)


def test_setting_effects_are_not_counted_as_play(canonical_export: dict) -> None:
    report = build_report(canonical_export)
    assert report.highlights.swing_turn == 1
    assert all("mobilisation plan" not in e for t in report.turns for a in t.arguments
               for e in a.effects)


def _rolled(
    arg_id: str, actor: str, turn: int, p: float | None, success: bool,
    mode: str = "probabilistic", roll: float | None = 0.5, minute: int = 0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    argument = {"id": arg_id, "turn": turn, "actor_id": actor, "action": f"act {arg_id}",
                "desired_effect": "d", "pros": [], "status": "resolved"}
    ruling = {"argument_id": arg_id, "mode": mode, "probability": p, "roll": roll,
              "success": success, "rationale": "r",
              "created_at": f"2026-01-0{turn}T00:{minute:02d}:00"}
    return argument, ruling


def test_highlights_pick_long_shot_bust_and_most_contested() -> None:
    pairs = [_rolled("x1", "a", 1, 0.12, True), _rolled("x2", "b", 1, 0.9, False, minute=1),
             _rolled("x3", "a", 2, 0.5, True)]
    export = minimal_export(
        arguments=[a for a, _ in pairs], adjudications=[r for _, r in pairs],
        cons=[{"id": "c1", "argument_id": "x3", "actor_id": "b", "reason": "no"},
              {"id": "c2", "argument_id": "x3", "actor_id": "b", "reason": "never"}],
        effects=[{"id": "e1", "description": "E", "source_argument_id": "x3",
                  "source_consequence_turn": None, "created_turn": 2, "active": True}],
    )
    h = build_report(export).highlights
    assert (h.long_shot, h.bust, h.most_contested, h.swing_turn) == ("x1", "x2", "x3", 2)


def test_highlight_ties_go_to_the_earlier_play() -> None:
    pairs = [_rolled("zz", "a", 1, 0.3, True), _rolled("aa", "b", 1, 0.3, True, minute=1)]
    export = minimal_export(arguments=[a for a, _ in reversed(pairs)],
                            adjudications=[r for _, r in pairs])
    assert build_report(export).highlights.long_shot == "zz"


def test_unrolled_rulings_are_listed_and_tallied_separately() -> None:
    pairs = [_rolled("u1", "a", 1, None, True, mode="automatic_success", roll=None),
             _rolled("r1", "b", 1, 0.4, False, minute=1)]
    report = build_report(minimal_export(
        arguments=[a for a, _ in pairs], adjudications=[r for _, r in pairs]))
    assert (report.rolled, report.unrolled) == (["r1"], ["u1"])
    assert report.tallies["a"].unrolled == 1 and report.tallies["a"].granted == 1
    assert report.tallies["b"].failed == 1
    assert report.highlights.long_shot is None


def test_model_falls_back_from_assignments_to_agent_runs_to_unknown() -> None:
    export = minimal_export(agent_runs=[{"actor_id": "a", "role": "player",
                                         "model_requested": "x/req", "model_returned": "x/ret"}])
    assert [a.model for a in build_report(export).actors] == ["x/ret", "unknown"]
    export["model_assignments"] = {"a": "m/a", "b": "m/b"}
    assert [a.model for a in build_report(export).actors] == ["m/a", "m/b"]
