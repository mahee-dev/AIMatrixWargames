"""The story cards: five self-contained 1200×675 pages built from a story and the counted facts.

Every page carries the simulation badge and the brand footer; the run page shows only counted
data (#18, D-121)."""

from __future__ import annotations

from aar.card import REPO_URL, render_screens
from aar.facts import run_facts
from aar.model import build_report
from aar.story import Story, fallback_story
from tests.test_aar_facts import _export as facts_export
from tests.test_aar_story import _export as story_export, _story


def _screens(story: dict | None = None, export: dict | None = None) -> list[tuple[str, str]]:
    export = export or story_export()
    model = Story.model_validate(story or _story())
    return render_screens(model, run_facts(export), build_report(export))


def test_five_screens_in_scqa_order() -> None:
    names = [name for name, _ in _screens()]
    assert names == ["answer", "background", "closed-doors", "developments", "run"]


def test_every_screen_is_badged_branded_and_numbered() -> None:
    screens = _screens()
    for n, (_, html) in enumerate(screens, 1):
        assert "AI SIMULATION" in html
        assert "AI MATRIX WARGAMES" in html and REPO_URL in html
        assert f"<b>{n}</b> / {len(screens)}" in html


def test_pages_are_self_contained() -> None:
    for _, html in _screens():
        assert "<script src" not in html and "http://" not in html and "https://" not in html


def test_answer_screen_asks_then_answers() -> None:
    html = dict(_screens())["answer"]
    assert html.index("Who wins the vote?") < html.index("Alpha wins against the odds")
    assert "30%" in html and "1 secret message<" in html


def test_without_closed_doors_there_are_four_screens() -> None:
    story = _story()
    story["closed_doors"] = None
    screens = _screens(story)
    assert [n for n, _ in screens] == ["answer", "background", "developments", "run"]
    assert "<b>4</b> / 4" in screens[-1][1]


def test_quotes_are_escaped_and_translations_marked() -> None:
    story = _story()
    story["closed_doors"]["quotes"][0].update(text="<b>bold</b> lie", translated=True)
    html = dict(_screens(story))["closed-doors"]
    assert "<b>bold</b> lie" not in html and "&lt;b&gt;bold&lt;/b&gt; lie" in html
    assert "translated" in html


def test_story_text_is_escaped_everywhere() -> None:
    story = _story()
    story["result"]["headline"] = "<script>alert(1)</script>"
    assert "<script>alert" not in dict(_screens(story))["answer"]


def test_ai_written_and_record_built_stories_are_labelled() -> None:
    assert "Story written by AI from the game record" in dict(_screens())["answer"]
    export = story_export()
    plain = fallback_story(build_report(export), export)
    html = dict(render_screens(plain, run_facts(export), build_report(export)))["answer"]
    assert "Built from the game record" in html


def test_run_screen_shows_counted_data() -> None:
    export = facts_export()
    story = _story()
    story["closed_doors"] = None
    for entry, item in zip(story["actors"], export["actors"]):
        entry["id"] = item["id"]
    html = dict(_screens(story, export))["run"]
    assert "DeepSeek V4 Flash" in html and "Claude Opus 5.5" in html and "GPT 6" in html
    assert "$0.26+" in html and "reported for 8 of 10 calls" in html
    assert "seed 1" in html and "umpire prompt umpire-v1.7" in html
    assert "1 / 2" in html and "Arguments granted" in html and "1 decided by dice" in html


def test_layouts_for_every_cast_size(canonical_export: dict) -> None:
    report = build_report(canonical_export)
    story = fallback_story(report, canonical_export)
    assert len(render_screens(story, run_facts(canonical_export), report)) == 5
    many = story_export()
    many["actors"] = [{"id": f"a{i}", "name": f"Actor {i}", "objectives": ["Win"]} for i in range(9)]
    report = build_report(many)
    story = fallback_story(report, many)
    assert len(render_screens(story, run_facts(many), report)) == 5
