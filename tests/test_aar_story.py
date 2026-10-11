"""The story schema is the contract between the narrator and the cards: every claim cites the
export, quotes are verbatim private Messages or marked translated, and word budgets hold (#18)."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from aar.model import build_report
from aar.story import Story, fallback_story, response_format, validate_story
from tests.aar_helpers import minimal_export

SECRET = "We will back you in turn two, but only if the vote is held before the summit."


def _export() -> dict[str, Any]:
    return minimal_export(
        messages=[
            {"id": "m-priv", "turn": 1, "sender_actor_id": "a", "recipients": ["b"],
             "public": False, "text": SECRET},
            {"id": "m-pub", "turn": 1, "sender_actor_id": "b", "recipients": [],
             "public": True, "text": "We stand with everyone."},
        ],
        arguments=[{"id": "x1", "turn": 1, "actor_id": "a", "action": "Push", "desired_effect": "d"}],
        adjudications=[{"argument_id": "x1", "mode": "probabilistic", "probability": 0.3,
                        "roll": 0.1, "success": True, "created_at": "2026-01-01T00:00:00"}],
        effects=[{"id": "e1", "description": "Alpha wins the vote.", "source_argument_id": "x1",
                  "source_consequence_turn": None, "created_turn": 1, "active": True}],
    )


def _story() -> dict[str, Any]:
    return {
        "schema_version": "story-v1",
        "kicker": "2 AI agents played a test",
        "actor_noun": "sides",
        "actors": [
            {"id": "a", "short": "Alpha", "role": "Alpha, the bold", "number": None, "wants": "Win the vote."},
            {"id": "b", "short": "Beta", "role": "Beta", "number": "12", "wants": "Hold on."},
        ],
        "background": {"situation": "A vote is coming.", "complication": "Nobody has a majority.",
                       "question": "Who wins the vote?"},
        "result": {"headline": "Alpha wins against the odds", "dek": "A 30% shot came in.",
                   "figure": {"value": "30%", "unit": "odds", "label": "and it still worked"},
                   "facts": [{"value": "Beta: outplayed", "label": "never saw it coming"}],
                   "cites": ["x1"]},
        "closed_doors": {"title": "A promise with a catch",
                         "quotes": [{"message_id": "m-priv", "text": "We will back you … before the summit.",
                                     "translated": False}],
                         "public": "Beta stands with everyone.", "public_cites": ["m-pub"],
                         "payoff": {"when": "Turn 1", "text": "The vote passes.", "cites": ["e1"]}},
        "developments": {"title": "One turn, one upset",
                         "moments": [{"when": "Turn 1", "title": "The long shot", "text": "Alpha's push lands.",
                                      "tone": "win", "actor_ids": ["a"], "cites": ["x1", "e1"]}]},
    }


def _errors(story: dict[str, Any], export: dict[str, Any] | None = None) -> list[str]:
    return validate_story(Story.model_validate(story), export or _export())


def test_a_valid_story_passes() -> None:
    assert _errors(_story()) == []


def test_unknown_cited_id_is_rejected() -> None:
    story = _story()
    story["developments"]["moments"][0]["cites"] = ["nope"]
    assert any("nope" in e for e in _errors(story))


def test_claims_without_citations_are_rejected() -> None:
    story = _story()
    story["result"]["cites"] = []
    assert any("result" in e and "cite" in e for e in _errors(story))


def test_quoting_a_public_message_is_rejected() -> None:
    story = _story()
    story["closed_doors"]["quotes"][0] = {"message_id": "m-pub", "text": "We stand with everyone.",
                                          "translated": False}
    assert any("private" in e for e in _errors(story))


def test_untranslated_quotes_must_be_verbatim() -> None:
    story = _story()
    story["closed_doors"]["quotes"][0]["text"] = "We will betray you."
    assert any("verbatim" in e for e in _errors(story))
    story["closed_doors"]["quotes"][0]["translated"] = True
    assert _errors(story) == []


def test_quote_matching_ignores_whitespace_differences() -> None:
    export = _export()
    export["messages"][0]["text"] = SECRET.replace(" in turn", "\n  in turn")
    assert _errors(_story(), export) == []


def test_word_budgets_hold() -> None:
    story = _story()
    story["result"]["headline"] = "word " * 11
    assert any("headline" in e for e in _errors(story))


def test_every_actor_needs_an_entry_and_no_strangers() -> None:
    story = _story()
    story["actors"] = story["actors"][:1] + [dict(story["actors"][1], id="zed")]
    errors = _errors(story)
    assert any("b" in e and "missing" in e for e in errors)
    assert any("zed" in e for e in errors)


def test_moment_actor_ids_must_exist() -> None:
    story = _story()
    story["developments"]["moments"][0]["actor_ids"] = ["ghost"]
    assert any("ghost" in e for e in _errors(story))


def test_question_must_be_a_question() -> None:
    story = _story()
    story["background"]["question"] = "Who wins"
    assert any("question" in e for e in _errors(story))


def test_the_record_built_story_is_valid(canonical_export: dict) -> None:
    story = fallback_story(build_report(canonical_export), canonical_export)
    assert validate_story(story, canonical_export) == []
    assert story.narrated is False
    assert story.closed_doors is not None and story.closed_doors.quotes
    assert 1 <= len(story.developments.moments) <= 4


def test_the_record_built_story_without_private_messages() -> None:
    export = _export()
    export["messages"] = [m for m in export["messages"] if m["public"]]
    story = fallback_story(build_report(export), export)
    assert story.closed_doors is None
    assert validate_story(story, export) == []


def test_response_format_is_strict_and_closed() -> None:
    fmt = response_format()
    assert fmt["type"] == "json_schema" and fmt["json_schema"]["strict"] is True

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(copy.deepcopy(fmt["json_schema"]["schema"]))



def test_response_format_allows_every_field_of_a_valid_story() -> None:
    """A field that is merely *named* ``title`` is a real field, not schema metadata.

    The strict schema closes every object, so a field it omits is one the model cannot send, and
    the story then fails validation for lacking it. ``_strict`` stripped every ``title`` key, which
    removed ``closed_doors.title``, ``developments.title`` and each moment's ``title``.
    """
    schema = response_format()["json_schema"]["schema"]

    def object_branch(node: dict) -> dict:
        if "anyOf" in node:
            return next(branch for branch in node["anyOf"] if branch.get("type") == "object")
        return node

    def check(value: Any, node: dict, path: str) -> None:
        if isinstance(value, dict):
            node = object_branch(node)
            missing = set(value) - set(node["properties"])
            assert not missing, f"{path or 'story'}: schema omits {sorted(missing)}"
            for key, item in value.items():
                check(item, node["properties"][key], f"{path}.{key}".lstrip("."))
        elif isinstance(value, list):
            for item in value:
                check(item, node.get("items", {}), f"{path}[]")

    story = _story()
    story.pop("schema_version", None)
    check(story, schema, "")
