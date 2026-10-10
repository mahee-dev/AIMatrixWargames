"""The after-action report model: a pure, deterministic reading of one game's export.

Everything here comes from the export alone, never the database, so a report can be rebuilt
from any archived export and never invents an event (TRD §34, #16). Missing fields in older
exports map to ``None``; nothing is guessed.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any

from pydantic import BaseModel, Field

ROLLED_MODE = "probabilistic"


class ActorSummary(BaseModel):
    id: str
    name: str
    public_description: str = ""
    objectives: list[str] = Field(default_factory=list)
    model: str = "unknown"


class ConView(BaseModel):
    id: str
    actor_id: str
    reason: str


class Ruling(BaseModel):
    mode: str
    probability: float | None
    roll: float | None
    success: bool | None
    rationale: str = ""


class ArgumentView(BaseModel):
    id: str
    turn: int
    actor_id: str
    action: str
    desired_effect: str
    pros: list[str] = Field(default_factory=list)
    status: str | None = None
    cons: list[ConView] = Field(default_factory=list)
    reply_points: list[str] = Field(default_factory=list)
    ruling: Ruling | None = None
    effects: list[str] = Field(default_factory=list)


class MessageView(BaseModel):
    id: str
    turn: int
    sender_actor_id: str
    recipients: list[str] = Field(default_factory=list)
    public: bool
    text: str


class TurnView(BaseModel):
    number: int
    messages: list[MessageView] = Field(default_factory=list)
    arguments: list[ArgumentView] = Field(default_factory=list)
    consequence_effects: list[str] = Field(default_factory=list)


class ActorTally(BaseModel):
    granted: int = 0
    failed: int = 0
    unrolled: int = 0
    cons_raised: int = 0
    public_messages: int = 0
    private_messages: int = 0


class Highlights(BaseModel):
    """Argument ids (or a turn number) picked by fixed rules; ``None`` when nothing qualifies.

    Ties go to whichever Argument was played first.
    """

    long_shot: str | None = None
    bust: str | None = None
    most_contested: str | None = None
    swing_turn: int | None = None


class AfterActionReport(BaseModel):
    export_format_version: int | None
    game_id: str
    scenario_id: str
    scenario_name: str
    scenario_version: int | str | None
    scenario_summary: str | None
    seed: int | None
    turns_played: int
    max_turns: int | None
    status: str | None
    actors: list[ActorSummary]
    turns: list[TurnView]
    highlights: Highlights
    tallies: dict[str, ActorTally]
    rolled: list[str]
    unrolled: list[str]

    def argument(self, argument_id: str) -> ArgumentView:
        return next(a for t in self.turns for a in t.arguments if a.id == argument_id)

    def actor_name(self, actor_id: str) -> str:
        return next((a.name for a in self.actors if a.id == actor_id), actor_id)


def _actor_models(export: dict[str, Any]) -> dict[str, str]:
    """The model behind each Actor: the sealed assignment, else what its runs report serving."""
    assigned = export.get("model_assignments") or {}
    from_runs: dict[str, str] = {}
    for run in export.get("agent_runs") or []:
        actor_id = run.get("actor_id")
        model = run.get("model_returned") or run.get("model_requested")
        if run.get("role") == "player" and actor_id and model and actor_id not in from_runs:
            from_runs[actor_id] = model
    return {**from_runs, **assigned}


def _play_order(export: dict[str, Any]) -> dict[str, tuple[int, str, int]]:
    """Each Argument's position in play: its turn, then when it was adjudicated.

    Ids are random UUIDs, so they cannot order play. Unadjudicated Arguments sort after the
    adjudicated ones of their turn, in export order.
    """
    adjudicated_at = {
        r["argument_id"]: r.get("created_at") or "" for r in export.get("adjudications") or []
    }
    return {
        a["id"]: (a["turn"], adjudicated_at.get(a["id"], "￿"), index)
        for index, a in enumerate(export.get("arguments") or [])
    }


def build_report(export: dict[str, Any]) -> AfterActionReport:
    game = export["game"]
    models = _actor_models(export)
    actors = [
        ActorSummary(
            id=a["id"],
            name=a.get("name") or a["id"],
            public_description=(a.get("public_description") or "").strip(),
            objectives=list(a.get("objectives") or []),
            model=models.get(a["id"], "unknown"),
        )
        for a in export.get("actors") or []
    ]

    cons_by_argument: defaultdict[str, list[ConView]] = defaultdict(list)
    for con in export.get("cons") or []:
        cons_by_argument[con["argument_id"]].append(
            ConView(id=con["id"], actor_id=con["actor_id"], reason=con.get("reason") or "")
        )
    replies = {
        r["argument_id"]: [p["text"] for p in r.get("points") or []]
        for r in export.get("argument_replies") or []
    }
    rulings = {
        r["argument_id"]: Ruling(
            mode=r["mode"],
            probability=r.get("probability"),
            roll=r.get("roll"),
            success=r.get("success"),
            rationale=r.get("rationale") or "",
        )
        for r in export.get("adjudications") or []
    }

    effects_by_argument: defaultdict[str, list[str]] = defaultdict(list)
    consequence_effects: defaultdict[int, list[str]] = defaultdict(list)
    effects_per_turn: Counter[int] = Counter()
    for effect in export.get("effects") or []:
        if effect.get("source_argument_id"):
            effects_by_argument[effect["source_argument_id"]].append(effect["description"])
        elif effect.get("source_consequence_turn") is not None:
            consequence_effects[effect["source_consequence_turn"]].append(effect["description"])
        else:
            continue  # scenario-setting and inject Effects are not play
        if effect.get("created_turn") is not None:
            effects_per_turn[effect["created_turn"]] += 1

    order = _play_order(export)
    arguments = sorted(
        (
            ArgumentView(
                id=a["id"],
                turn=a["turn"],
                actor_id=a["actor_id"],
                action=a.get("action") or "",
                desired_effect=a.get("desired_effect") or "",
                pros=list(a.get("pros") or []),
                status=a.get("status"),
                cons=cons_by_argument.get(a["id"], []),
                reply_points=replies.get(a["id"], []),
                ruling=rulings.get(a["id"]),
                effects=effects_by_argument.get(a["id"], []),
            )
            for a in export.get("arguments") or []
        ),
        key=lambda a: order[a.id],
    )
    messages = sorted(
        (
            MessageView(
                id=m["id"],
                turn=m["turn"],
                sender_actor_id=m["sender_actor_id"],
                recipients=list(m.get("recipients") or []),
                public=bool(m.get("public")),
                text=m.get("text") or "",
            )
            for m in export.get("messages") or []
        ),
        key=lambda m: m.turn,  # stable: export order within a turn
    )

    turns_played = int(game.get("turn") or max((a.turn for a in arguments), default=0))
    turns = [
        TurnView(
            number=n,
            messages=[m for m in messages if m.turn == n],
            arguments=[a for a in arguments if a.turn == n],
            consequence_effects=consequence_effects.get(n, []),
        )
        for n in range(1, turns_played + 1)
    ]

    tallies = {a.id: ActorTally() for a in actors}
    for argument in arguments:
        tally = tallies.setdefault(argument.actor_id, ActorTally())
        if argument.ruling is not None:
            if argument.ruling.mode != ROLLED_MODE:
                tally.unrolled += 1
            if argument.ruling.success is True:
                tally.granted += 1
            elif argument.ruling.success is False:
                tally.failed += 1
        for con in argument.cons:
            tallies.setdefault(con.actor_id, ActorTally()).cons_raised += 1
    for message in messages:
        tally = tallies.setdefault(message.sender_actor_id, ActorTally())
        if message.public:
            tally.public_messages += 1
        else:
            tally.private_messages += 1

    # ``arguments`` is in play order and ``min``/``max`` keep the first of equals, so every
    # highlight tie goes to the earlier play.
    rolled = [a for a in arguments if a.ruling is not None and a.ruling.mode == ROLLED_MODE]
    unrolled = [a for a in arguments if a.ruling is not None and a.ruling.mode != ROLLED_MODE]
    wins = [(a.ruling.probability, a.id) for a in rolled
            if a.ruling and a.ruling.success is True and a.ruling.probability is not None]
    losses = [(a.ruling.probability, a.id) for a in rolled
              if a.ruling and a.ruling.success is False and a.ruling.probability is not None]
    contested = [(len(a.cons), a.id) for a in arguments if a.cons]
    highlights = Highlights(
        long_shot=min(wins, key=lambda w: w[0])[1] if wins else None,
        bust=max(losses, key=lambda w: w[0])[1] if losses else None,
        most_contested=max(contested, key=lambda c: c[0])[1] if contested else None,
        swing_turn=min(effects_per_turn, key=lambda t: (-effects_per_turn[t], t))
        if effects_per_turn else None,
    )

    return AfterActionReport(
        export_format_version=export.get("export_format_version"),
        game_id=str(game["id"]),
        scenario_id=game.get("scenario_id") or "",
        # Exports carry no display name today; the id stands in until one is exported.
        scenario_name=game.get("scenario_name") or game.get("scenario_id") or "",
        scenario_version=game.get("scenario_version"),
        scenario_summary=game.get("scenario_summary"),
        seed=game.get("seed"),
        turns_played=turns_played,
        max_turns=game.get("max_turns"),
        status=game.get("status"),
        actors=actors,
        turns=turns,
        highlights=highlights,
        tallies=tallies,
        rolled=[a.id for a in rolled],
        unrolled=[a.id for a in unrolled],
    )
