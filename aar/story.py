"""The story behind the share cards: what the narrator writes, and the checks it must pass.

A story is written for someone who knows nothing about the scenario, in SCQA order with the answer
first. Every claim cites Argument, Message or Effect ids from the export, every quote is a
verbatim fragment of a private Message unless it is marked translated, and word budgets keep each
field legible on a phone. :func:`fallback_story` builds a plainer story from the record alone, for
cards made without a narrator (#18, D-121).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, Field

from aar.model import AfterActionReport

SCHEMA_VERSION: Literal["story-v1"] = "story-v1"


class StoryActor(BaseModel):
    id: str
    short: str
    role: str
    number: str | None = None
    wants: str


class Background(BaseModel):
    situation: str
    complication: str | None = None
    question: str


class Figure(BaseModel):
    value: str
    unit: str = ""
    label: str


class Fact(BaseModel):
    value: str
    label: str


class Result(BaseModel):
    headline: str
    dek: str
    figure: Figure
    facts: list[Fact] = Field(default_factory=list, max_length=2)
    cites: list[str] = Field(default_factory=list)


class Quote(BaseModel):
    message_id: str
    text: str
    translated: bool = False


class Payoff(BaseModel):
    when: str
    text: str
    cites: list[str] = Field(default_factory=list)


class ClosedDoors(BaseModel):
    title: str
    quotes: list[Quote] = Field(min_length=1, max_length=2)
    public: str | None = None
    public_cites: list[str] = Field(default_factory=list)
    payoff: Payoff | None = None


class Moment(BaseModel):
    when: str
    title: str
    text: str
    tone: Literal["win", "fail"] | None = None
    actor_ids: list[str] = Field(default_factory=list, max_length=2)
    cites: list[str] = Field(default_factory=list)


class Developments(BaseModel):
    title: str
    moments: list[Moment] = Field(min_length=1, max_length=4)


class Story(BaseModel):
    schema_version: Literal["story-v1"] = SCHEMA_VERSION
    kicker: str
    actor_noun: str = "Actors"
    actors: list[StoryActor]
    background: Background
    result: Result
    closed_doors: ClosedDoors | None = None
    developments: Developments
    # Provenance, not narrator output: False for a story built from the record alone.
    narrated: bool = True


# (path, words) — the most each field may hold. Character limits for the tight slots.
WORD_BUDGETS: dict[str, int] = {
    "kicker": 14, "actor_noun": 1,
    "actors.short": 3, "actors.role": 5, "actors.wants": 8,
    "background.situation": 35, "background.complication": 35, "background.question": 12,
    "result.headline": 10, "result.dek": 30, "result.figure.unit": 2, "result.figure.label": 10,
    "result.facts.value": 5, "result.facts.label": 8,
    "closed_doors.title": 8, "closed_doors.quotes.text": 28, "closed_doors.public": 14,
    "closed_doors.payoff.text": 16, "closed_doors.payoff.when": 3,
    "developments.title": 6, "developments.moments.when": 3,
    "developments.moments.title": 5, "developments.moments.text": 13,
}
CHAR_BUDGETS: dict[str, int] = {"actors.number": 6, "result.figure.value": 6}


def _words(text: str) -> int:
    """Words as a reader counts them: symbols such as "+" or "→" are not words."""
    return sum(1 for token in text.split() if re.search(r"\w", token))


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _fields(node: Any, path: str = "") -> list[tuple[str, str]]:
    """Every string field as (dotted path without list indices, value)."""
    if isinstance(node, BaseModel):
        out: list[tuple[str, str]] = []
        for name in type(node).model_fields:
            out += _fields(getattr(node, name), f"{path}.{name}" if path else name)
        return out
    if isinstance(node, list):
        return [pair for item in node for pair in _fields(item, path)]
    if isinstance(node, str):
        return [(path, node)]
    return []


def validate_story(story: Story, export: dict[str, Any]) -> list[str]:
    """Every reason ``story`` may not be shown, as plain sentences; empty when it is valid."""
    errors: list[str] = []
    actor_ids = {a["id"] for a in export.get("actors") or []}
    messages = {m["id"]: m for m in export.get("messages") or []}
    known = (
        set(messages)
        | {a["id"] for a in export.get("arguments") or []}
        | {e["id"] for e in export.get("effects") or []}
    )

    for path, value in _fields(story):
        if path in WORD_BUDGETS and _words(value) > WORD_BUDGETS[path]:
            errors.append(f"{path} has {_words(value)} words; the budget is {WORD_BUDGETS[path]}")
        if path in CHAR_BUDGETS and len(value) > CHAR_BUDGETS[path]:
            errors.append(f"{path} has {len(value)} characters; the budget is {CHAR_BUDGETS[path]}")

    named = [a.id for a in story.actors]
    for missing in sorted(actor_ids - set(named)):
        errors.append(f"actor {missing!r} is missing from actors")
    for stranger in sorted(set(named) - actor_ids):
        errors.append(f"actors names {stranger!r}, which is not an Actor in this game")

    if not story.background.question.strip().endswith("?"):
        errors.append("background.question must be a question ending with '?'")

    def cited(where: str, ids: list[str]) -> None:
        if not ids:
            errors.append(f"{where} must cite at least one Argument, Message or Effect id")
        for unknown in [i for i in ids if i not in known]:
            errors.append(f"{where} cites {unknown!r}, which is not in the export")

    cited("result", story.result.cites)
    for n, moment in enumerate(story.developments.moments, 1):
        cited(f"developments.moments[{n}]", moment.cites)
        for stranger in [a for a in moment.actor_ids if a not in actor_ids]:
            errors.append(f"developments.moments[{n}] names {stranger!r}, which is not an Actor")

    doors = story.closed_doors
    if doors is not None:
        if doors.public:
            cited("closed_doors.public", doors.public_cites)
        if doors.payoff is not None:
            cited("closed_doors.payoff", doors.payoff.cites)
        for n, quote in enumerate(doors.quotes, 1):
            message = messages.get(quote.message_id)
            if message is None:
                errors.append(f"closed_doors.quotes[{n}] quotes {quote.message_id!r}, which is not a Message")
                continue
            if message.get("public"):
                errors.append(f"closed_doors.quotes[{n}] quotes a public Message; quotes must be private")
            if not quote.translated:
                source = _squash(message.get("text") or "")
                for fragment in (_squash(f) for f in quote.text.split("…")):
                    if fragment and fragment not in source:
                        errors.append(
                            f"closed_doors.quotes[{n}] is not verbatim: {fragment!r} is not in "
                            "the Message (mark it translated if it is a translation)"
                        )
    return errors


def _strict(node: Any, defs: dict[str, Any]) -> Any:
    """Inline ``$ref``s and close every object, as strict structured outputs require."""
    if isinstance(node, list):
        return [_strict(item, defs) for item in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        return _strict(defs[node["$ref"].split("/")[-1]], defs)
    out = {k: _strict(v, defs) for k, v in node.items() if k not in {"title", "default", "$defs"}}
    if out.get("type") == "object" and "properties" in out:
        out["additionalProperties"] = False
        out["required"] = list(out["properties"])
    return out


def response_format() -> dict[str, Any]:
    """The narrator's ``response_format``: the story schema, strict, without provenance fields."""
    schema = Story.model_json_schema()
    schema["properties"].pop("narrated")
    return {
        "type": "json_schema",
        "json_schema": {"name": "amaw_story", "strict": True,
                        "schema": _strict(schema, schema.get("$defs", {}))},
    }


# --- The record-built story ----------------------------------------------------------------


def _clip(text: str, words: int) -> str:
    parts = text.split()
    return " ".join(parts) if len(parts) <= words else " ".join(parts[: words - 1]) + " …"


def _first_words(text: str, words: int) -> str:
    """A verbatim opening of ``text``: whole words only, so it stays a fragment of the source."""
    return " ".join(text.split()[:words])


def scenario_title(report: AfterActionReport) -> str:
    """The scenario's display name, or its id made readable when the export carries no name."""
    if report.scenario_name and report.scenario_name != report.scenario_id:
        return report.scenario_name
    return report.scenario_id.replace("-", " ").replace("_", " ").title()


def fallback_story(report: AfterActionReport, export: dict[str, Any]) -> Story:
    """A plain story built from the record alone, valid under :func:`validate_story`."""
    title = scenario_title(report)
    adjudicated = [a for t in report.turns for a in t.arguments if a.ruling is not None]
    granted = [a for a in adjudicated if a.ruling and a.ruling.success]
    first_argument = adjudicated[0].id if adjudicated else None
    effects = [e for e in export.get("effects") or [] if e.get("source_argument_id")]
    effects_by_turn: dict[int, list[dict[str, Any]]] = {}
    for effect in effects:
        effects_by_turn.setdefault(effect.get("created_turn") or 0, []).append(effect)

    cites = [first_argument] if first_argument else [e["id"] for e in effects[:1]]
    summary = report.scenario_summary or f"{len(report.actors)} AI agents play {title}."

    moments: list[Moment] = []
    for turn in report.turns:
        if len(moments) == 4 or not turn.arguments:
            continue
        won = sum(1 for a in turn.arguments if a.ruling and a.ruling.success)
        turn_effects = effects_by_turn.get(turn.number) or []
        first_effect = turn_effects[0] if turn_effects else None
        text = first_effect["description"] if first_effect else turn.arguments[0].action
        moments.append(Moment(
            when=f"Turn {turn.number}",
            title=f"{won} of {len(turn.arguments)} granted",
            text=_clip(text, 13),
            actor_ids=list(dict.fromkeys(a.actor_id for a in turn.arguments))[:2],
            cites=[first_effect["id"]] if first_effect else [turn.arguments[0].id],
        ))

    private = [m for m in export.get("messages") or [] if not m.get("public")]
    doors = None
    if private:
        busiest = Counter(m["turn"] for m in private).most_common(1)[0][0]
        picks = sorted((m for m in private if m["turn"] == busiest),
                       key=lambda m: len(m.get("text") or ""), reverse=True)[:2]
        doors = ClosedDoors(
            title="What they said in private",
            quotes=[Quote(message_id=m["id"], text=_first_words(m["text"], 18)) for m in picks],
        )

    rolled = sum(1 for a in adjudicated if a.ruling and a.ruling.mode == "probabilistic")
    return Story(
        kicker=_clip(f"{len(report.actors)} AI agents played {title}", 14),
        actors=[
            StoryActor(id=a.id, short=_clip(a.name, 3), role=_clip(a.name, 5),
                       wants=_clip(a.objectives[0], 8) if a.objectives else "—")
            for a in report.actors
        ],
        background=Background(situation=_clip(summary, 35),
                              question="What happened when the AI agents played?"),
        result=Result(
            headline=f"{len(granted)} of {len(adjudicated)} moves succeeded",
            dek=f"{report.turns_played} turns of negotiation between {len(report.actors)} AI agents.",
            figure=Figure(value=f"{len(granted)}/{len(adjudicated)}", label="moves granted by the Umpire"),
            facts=[Fact(value=f"{rolled} dice rolls", label="moves decided by the server's dice"),
                   Fact(value=f"{report.turns_played} turns", label=f"of {report.max_turns} played")],
            cites=cites,
        ),
        closed_doors=doors,
        developments=Developments(title="Turn by turn", moments=moments or [Moment(
            when="Turn 1", title="No moves", text="No Argument was adjudicated.", cites=cites)]),
        narrated=False,
    )
