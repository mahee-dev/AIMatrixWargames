"""``report.md`` follows #16: per-turn timeline, marked Messages, rolled/unrolled closing list."""

from __future__ import annotations

from aar.markdown import render_markdown
from aar.model import build_report
from tests.aar_helpers import minimal_export


def test_headings(canonical_export: dict) -> None:
    text = render_markdown(build_report(canonical_export))
    assert text.startswith("# After-action report: deterministic-game\n")
    for n in range(1, 7):
        assert f"\n## Turn {n}\n" in text
    assert "\n## Highlights\n" in text
    assert "\n## Rolled and unrolled Arguments\n" in text


def test_known_ruling_line(canonical_export: dict) -> None:
    report = build_report(canonical_export)
    first = report.turns[0].arguments[0]
    assert first.ruling is not None and first.ruling.roll is not None
    line = f"**Ruling:** probabilistic · p=1.00 · roll={first.ruling.roll:.2f} → granted"
    text = render_markdown(report)
    assert line in text
    assert "- Effect: arkania advances its position (turn 1)" in text


def test_private_messages_are_marked(canonical_export: dict) -> None:
    text = render_markdown(build_report(canonical_export))
    assert "- **[private → Belvar]** Republic of Arkania:" in text


def test_public_messages_are_marked() -> None:
    export = minimal_export(messages=[{"id": "m1", "turn": 1, "sender_actor_id": "a",
                                       "recipients": [], "public": True, "text": "Hello all"}])
    assert "- **[public]** Alpha: Hello all" in render_markdown(build_report(export))


def test_closing_section_splits_rolled_from_unrolled() -> None:
    export = minimal_export(
        arguments=[
            {"id": "r1", "turn": 1, "actor_id": "a", "action": "Push", "desired_effect": "x"},
            {"id": "u1", "turn": 1, "actor_id": "b", "action": "Hold", "desired_effect": "y"},
        ],
        adjudications=[
            {"argument_id": "r1", "mode": "probabilistic", "probability": 0.25, "roll": 0.1,
             "success": True, "created_at": "2026-01-01T00:00:00"},
            {"argument_id": "u1", "mode": "automatic_failure", "probability": None,
             "roll": None, "success": False, "created_at": "2026-01-01T00:01:00"},
        ],
    )
    text = render_markdown(build_report(export))
    closing = text.split("## Rolled and unrolled Arguments")[1]
    rolled, unrolled = closing.split("### Decided without a roll")
    assert "Alpha: Push (probabilistic · p=0.25 · roll=0.10 → granted)" in rolled
    assert "Beta: Hold (automatic_failure → failed)" in unrolled
    assert "**Long shot:** Alpha, turn 1: Push" in text


def test_footer_warns_about_private_material(canonical_export: dict) -> None:
    text = render_markdown(build_report(canonical_export))
    assert "includes private material" in text
    assert "- **Belvar**\n  - Prevent unilateral Arkanian control of the Strait.\n" in text
