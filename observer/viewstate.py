"""What the terminal viewer knows about a game, folded from observer feed items.

:class:`ViewState` is an incremental reducer: :meth:`ViewState.apply` folds one feed item at a
time, in feed order, so a live viewer never replays the log and a replay frame shows exactly what
was known at its cursor. The snapshot contributes only what the feed cannot: Actor names and
models, the game's fixed settings and, while following a live game, the authoritative current
position (phase, readiness, whom the game waits on).

Feed refs are joined when the feed is read, not when the event happened, so a ref can carry a
later state than its event: an Argument's ``status`` or an Effect's ``active`` flag. The reducer
therefore takes lifecycle from events only (``effect_deactivated``, ``argument_adjudicated``, …)
and uses refs for content that never changes once written (text, probability, roll).

Nothing here reads the database or decides anything about the game (TRD §41.1, D-122).
"""

from __future__ import annotations

from collections import Counter, OrderedDict, deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from observer.navigation import UiState

# One accent per kind of line; everything else stays dim so the accents carry the meaning.
STYLES = {
    "public": "cyan",
    "private": "magenta",
    "argument": "bold yellow",
    "con": "yellow",
    "ruling": "bold",
    "effect": "green",
    "system": "dim",
}

FEED_LINES = 400
RECENT_MESSAGES = 200
TIMELINE_ENTRIES = 400

# The engine's phases in the order one turn passes through them (state-machine §§15-21). Reply
# runs only under the visible_reply Con policy and consequences only under a bounded
# consequence mode, so the viewer marks them "not used" until the game shows them.
PIPELINE = ("diplomacy", "argument", "cons", "reply", "adjudication", "consequences")
OPTIONAL_PHASES = frozenset({"reply", "consequences"})


@dataclass(frozen=True)
class FeedLine:
    """One rendered line of the feed, with what the filters need to know about it."""

    time: str
    who: str
    kind: str
    text: str
    actors: frozenset[str] = frozenset()
    private: bool = False
    style: str | None = None
    seq: int = 0
    turn: int = 0


@dataclass
class Ruling:
    actor_id: str
    action: str
    mode: str
    probability: float | None
    roll: float | None
    success: bool | None
    argument_id: str = ""
    turn: int = 0


@dataclass
class ConView:
    actor_id: str
    reason: str
    seq: int
    time: str


@dataclass
class ArgumentView:
    """One Argument's lifecycle as far as the feed has shown it."""

    id: str
    seq: int
    turn: int
    time: str
    owner: str
    action: str
    desired_effect: str
    pros: list[str]
    eligible: list[str]
    sampled: bool = False
    cons: list[ConView] = field(default_factory=list)
    done: set[str] = field(default_factory=set)
    revealed: bool = False
    reply: dict[str, Any] | None = None
    adjudication: dict[str, Any] | None = None
    effect_ids: list[str] = field(default_factory=list)
    stage: str = "cons"

    def con_status(self, actor_id: str) -> str:
        """``con``, ``passed``, ``pending``, ``no response`` or ``not selected`` for one Actor.

        ``con_passed`` records that an Actor finished its Con participation, with or without
        Cons (state-machine §17), so an Actor with Cons and a pass is ``con``, not ``passed``.
        """
        if actor_id not in self.eligible:
            return "not selected"
        if any(con.actor_id == actor_id for con in self.cons):
            return "con"
        if actor_id in self.done:
            return "passed"
        return "pending" if self.stage == "cons" else "no response"


@dataclass
class EffectView:
    """An Effect and its lifecycle up to the cursor; ``created_seq == 0`` marks scenario setup."""

    id: str
    description: str
    visibility: str
    visible_to: list[str]
    created_turn: int
    expires_turn: int | None
    source_argument_id: str | None
    source_consequence_turn: int | None
    source_inject_id: str | None
    supersedes: list[str]
    created_seq: int = 0
    active: bool = True
    ended_seq: int | None = None
    ended_turn: int | None = None
    ended_reason: str | None = None
    superseded_by: str | None = None

    @classmethod
    def from_ref(cls, ref: dict[str, Any], seq: int) -> EffectView:
        return cls(
            id=ref["id"],
            description=ref.get("description", ""),
            visibility=ref.get("visibility", "public"),
            visible_to=list(ref.get("visible_to") or []),
            created_turn=ref.get("created_turn") or 0,
            expires_turn=ref.get("expires_turn"),
            source_argument_id=ref.get("source_argument_id"),
            source_consequence_turn=ref.get("source_consequence_turn"),
            source_inject_id=ref.get("source_inject_id"),
            supersedes=list(ref.get("supersedes") or []),
            created_seq=seq,
        )

    @property
    def private(self) -> bool:
        return self.visibility != "public"

    @property
    def source(self) -> str:
        if self.source_argument_id:
            return "argument"
        if self.source_consequence_turn is not None:
            return "consequence"
        if self.source_inject_id:
            return "inject"
        return "setup"


@dataclass(frozen=True)
class MessageView:
    seq: int
    turn: int
    time: str
    sender: str
    recipients: tuple[str, ...]
    public: bool
    text: str


@dataclass(frozen=True)
class TimelineEntry:
    seq: int
    turn: int
    time: str
    kind: str
    text: str
    style: str = ""


def _clock(item: dict[str, Any]) -> str:
    return datetime.fromisoformat(item["created_at"]).strftime("%H:%M:%S")


def _probability(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def setup_effects(snapshot: dict[str, Any], items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The Effects a game started with, which no ``effect_created`` event records.

    They are the snapshot's active Effects and the targets of ``effect_deactivated`` events that
    no event created, restricted to turn-0 Effects with no Argument, consequence or inject source.
    A replay starts from these, so an early frame shows setup Effects that later expired.
    """
    created = {
        item["payload"].get("effect_id") for item in items if item["type"] == "effect_created"
    }
    found: dict[str, dict[str, Any]] = {}
    candidates = list(snapshot.get("effects", []))
    candidates += [
        item["refs"]["effect"] for item in items
        if item["type"] == "effect_deactivated" and "effect" in item["refs"]
    ]
    for effect in candidates:
        if (
            effect["id"] not in created
            and (effect.get("created_turn") or 0) == 0
            and not effect.get("source_argument_id")
            and effect.get("source_consequence_turn") is None
            and not effect.get("source_inject_id")
        ):
            found.setdefault(effect["id"], effect)
    return list(found.values())


def describe(item: dict[str, Any], names: dict[str, str]) -> FeedLine | None:
    """The feed line for one item, or ``None`` for bookkeeping the feed does not need to show."""
    kind, payload, refs = item["type"], item["payload"], item["refs"]
    actor_id = item["actor_id"]
    clock = _clock(item)
    seq, turn = item["seq"], item["turn"]

    def name(actor: str | None) -> str:
        return names.get(actor, actor) if actor else "—"

    def line(who: str, kind: str, text: str, actors: frozenset[str] = frozenset(), *,
             private: bool = False, style: str | None = None) -> FeedLine:
        return FeedLine(clock, who, kind, text, actors, private, style, seq, turn)

    if kind == "message_sent" and "message" in refs:
        message = refs["message"]
        sender = message["sender_actor_id"]
        if message["public"]:
            return line(name(sender), "public", message["text"], frozenset({sender}),
                        style=STYLES["public"])
        recipients = list(message["recipients"])
        who = f"{name(sender)} → {', '.join(name(r) for r in recipients)}"
        return line(who, "private", message["text"], frozenset({sender, *recipients}),
                    private=True, style=STYLES["private"])
    if kind == "argument_submitted" and "argument" in refs:
        argument = refs["argument"]
        return line(
            name(argument["actor_id"]), "argument",
            f"ARGUES {argument['action']} — wants: {argument['desired_effect']}",
            frozenset({argument["actor_id"]}), style=STYLES["argument"],
        )
    if kind == "con_submitted" and "con" in refs:
        con = refs["con"]
        owner = refs.get("argument", {}).get("actor_id")
        return line(
            name(con["actor_id"]), "con", f"CON vs {name(owner)}: {con['reason']}",
            frozenset({con["actor_id"], *([owner] if owner else [])}), style=STYLES["con"],
        )
    if kind == "owner_reply_submitted" and "reply" in refs:
        points = refs["reply"]["points"]
        text = "passes on reply" if not points else f"replies with {len(points)} point(s)"
        return line(name(actor_id), "con", text, frozenset({actor_id or ""}))
    if kind == "argument_adjudicated" and "adjudication" in refs:
        ruling = refs["adjudication"]
        owner = refs.get("argument", {}).get("actor_id")
        verdict = "SUCCESS" if ruling["success"] else "FAILURE"
        mode = ruling["mode"].replace("_", " ")
        detail = (
            f"p={_probability(ruling['probability'])} roll={_probability(ruling['roll'])}"
            if ruling["mode"] == "probabilistic"
            else mode
        )
        return line(
            "Umpire", "ruling", f"{name(owner)}: {verdict} ({detail})",
            frozenset({owner} if owner else ()),
            style="bold green" if ruling["success"] else "bold red",
        )
    if kind in ("effect_created", "effect_deactivated") and "effect" in refs:
        effect = refs["effect"]
        private = effect["visibility"] != "public"
        prefix = "EFFECT" if kind == "effect_created" else "EFFECT ENDS"
        scope = "" if not private else f" [{', '.join(name(a) for a in effect['visible_to'])}]"
        return line(
            "World", "effect", f"{prefix}{scope}: {effect['description']}",
            frozenset(effect["visible_to"]), private=private, style=STYLES["effect"],
        )
    if kind == "phase_started":
        return line("", "system", f"── {payload.get('phase', '?')} ──", style="dim")
    if kind == "turn_completed":
        return line("", "system", f"══ turn {payload.get('turn', turn)} complete ══",
                    style="bold dim")
    if kind == "game_started":
        return line("", "system", "game started", style="bold")
    if kind == "game_completed":
        return line("", "system", "game complete", style="bold")
    if kind == "game_paused":
        return line("", "system", f"game paused: {payload.get('reason', '?')}", style="bold red")
    if kind in ("actor_ready", "con_passed", "con_challengers_selected", "cons_revealed"):
        return None
    if kind in ("diplomacy_round_completed", "diplomacy_closed", "argument_round_started",
                "consequences_resolved"):
        return line("", "system", kind.replace("_", " "), style="dim")
    return line(name(actor_id), "system", kind, style="dim")


@dataclass
class ViewState:
    """What the viewer shows, folded from feed items and refreshed from snapshots."""

    game: dict[str, Any] = field(default_factory=dict)
    actors: list[dict[str, Any]] = field(default_factory=list)
    names: dict[str, str] = field(default_factory=dict)
    turn: int = 0
    phase: str = "lobby"
    status: str = "lobby"
    ready: set[str] = field(default_factory=set)
    active: str | None = None
    arguments_used: dict[str, int] = field(default_factory=dict)
    waiting_on: list[str] = field(default_factory=list)
    totals: dict[str, Any] = field(default_factory=dict)
    ruling: Ruling | None = None
    lines: deque[FeedLine] = field(default_factory=lambda: deque(maxlen=FEED_LINES))
    cursor: int = 0
    started_at: datetime | None = None
    last_at: datetime | None = None
    live: bool = True
    paused: bool = False
    secret_only: bool = False
    actor_filter: str | None = None
    # Everything below is folded from the feed only, so it is correct at any replay cursor.
    turn_order: list[str] = field(default_factory=list)
    events: int = 0
    pause_reason: str | None = None
    diplomacy_round: int = 0
    diplomacy_close_reason: str | None = None
    phase_trail: list[str] = field(default_factory=list)
    phases_seen: set[str] = field(default_factory=set)
    arguments: OrderedDict[str, ArgumentView] = field(default_factory=OrderedDict)
    current_argument: str | None = None
    turn_arguments: int = 0
    effects: OrderedDict[str, EffectView] = field(default_factory=OrderedDict)
    links: Counter[tuple[str, str]] = field(default_factory=Counter)
    broadcasts: Counter[str] = field(default_factory=Counter)
    messages: deque[MessageView] = field(default_factory=lambda: deque(maxlen=RECENT_MESSAGES))
    timeline: deque[TimelineEntry] = field(
        default_factory=lambda: deque(maxlen=TIMELINE_ENTRIES)
    )
    actor_stats: dict[str, Counter[str]] = field(default_factory=dict)
    turn_stats: dict[int, Counter[str]] = field(default_factory=dict)
    probability_sums: dict[int, float] = field(default_factory=dict)
    items: OrderedDict[int, dict[str, Any]] = field(default_factory=OrderedDict)
    snapshot_cursor: int = 0
    ui: UiState = field(default_factory=UiState)

    # --- snapshot ------------------------------------------------------------------------------

    def take_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Adopt what the feed alone cannot give: names, models, settings, cost.

        A live viewer also adopts the snapshot's current position. A replay keeps only the
        static parts, so an early frame never shows the final readiness or Argument counts.
        """
        self.game = snapshot["game"]
        self.actors = snapshot["actors"]
        self.names = {actor["id"]: actor["name"] for actor in self.actors}
        self.totals = snapshot["totals"]
        self.snapshot_cursor = snapshot.get("cursor", 0)
        if self.live:
            self.waiting_on = snapshot["waiting_on"]
            self.status = snapshot["game"]["status"]
            self.phase = snapshot["game"]["phase"]
            self.turn = snapshot["game"]["turn"]
            self.ready = {a["id"] for a in self.actors if a["diplomacy_ready"]}
            self.active = snapshot["game"]["active_actor_id"]
            self.arguments_used = {a["id"]: a["arguments_used"] for a in self.actors}
            for effect in snapshot.get("effects", []):
                if effect["id"] not in self.effects:
                    self.effects[effect["id"]] = EffectView.from_ref(effect, 0)

    def seed_effects(self, effects: list[dict[str, Any]]) -> None:
        """Start from the game's setup Effects (see :func:`setup_effects`)."""
        for effect in effects:
            self.effects.setdefault(effect["id"], EffectView.from_ref(effect, 0))

    # --- folding -------------------------------------------------------------------------------

    def _stat(self, actor: str | None, key: str, amount: int = 1) -> None:
        if actor:
            self.actor_stats.setdefault(actor, Counter())[key] += amount

    def _turn_stat(self, key: str, amount: int = 1) -> None:
        self.turn_stats.setdefault(self.turn, Counter())[key] += amount

    def _mark(self, item: dict[str, Any], kind: str, text: str, style: str = "") -> None:
        self.timeline.append(TimelineEntry(item["seq"], item["turn"], _clock(item), kind, text,
                                           style))

    def name(self, actor: str | None) -> str:
        if not actor:
            return "—"
        if actor == "umpire":
            return "Umpire"
        return self.names.get(actor, actor)

    def apply(self, item: dict[str, Any]) -> None:
        """Fold one feed item into the state, in feed order."""
        self.cursor = item["seq"]
        self.turn = item["turn"]
        self.events += 1
        at = datetime.fromisoformat(item["created_at"])
        self.started_at = self.started_at or at
        self.last_at = at
        kind, payload, refs = item["type"], item["payload"], item["refs"]
        self.items[item["seq"]] = item
        while len(self.items) > FEED_LINES * 2:
            self.items.popitem(last=False)
        # Any progress after a pause means the game was resumed; no event records the resume.
        if self.status == "paused" and kind not in ("game_paused", "game_completed"):
            self.status = "running"
            self.pause_reason = None

        if kind == "game_started":
            self.status = "running"
            self.turn_order = list(payload.get("turn_order") or [])
            self._mark(item, "system", "game started", "bold")
        elif kind == "phase_started":
            self._phase_started(item, payload.get("phase", self.phase))
        elif kind == "actor_ready" and item["actor_id"]:
            self.ready.add(item["actor_id"])
        elif kind == "diplomacy_round_completed":
            self.diplomacy_round = int(payload.get("round") or self.diplomacy_round)
        elif kind == "diplomacy_closed":
            self.diplomacy_close_reason = payload.get("reason")
            stats = self.turn_stats.get(self.turn, Counter())
            self._mark(
                item, "diplomacy",
                f"diplomacy closed ({str(payload.get('reason', '?')).replace('_', ' ')}): "
                f"{stats['private']} private, {stats['public']} public message(s)",
                "magenta",
            )
        elif kind == "message_sent" and "message" in refs:
            self._message(item, refs["message"])
        elif kind == "argument_submitted" and "argument" in refs:
            self._argument(item, refs["argument"])
        elif kind == "con_challengers_selected":
            self._challengers(payload)
        elif kind == "con_submitted" and "con" in refs:
            self._con(item, refs["con"])
        elif kind == "con_passed":
            argument = self.arguments.get(str(payload.get("argument_id")))
            if argument is not None and item["actor_id"]:
                argument.done.add(item["actor_id"])
                if not any(c.actor_id == item["actor_id"] for c in argument.cons):
                    self._stat(item["actor_id"], "passes")
        elif kind == "cons_revealed":
            argument = self.arguments.get(str(payload.get("argument_id")))
            if argument is not None:
                argument.revealed = True
                argument.stage = "reply"
        elif kind == "owner_reply_submitted":
            argument = self.arguments.get(str(payload.get("argument_id")))
            if argument is not None:
                argument.reply = refs.get("reply") or {"points": []}
                argument.stage = "adjudication"
        elif kind == "argument_adjudicated":
            self._adjudicated(item, payload, refs)
        elif kind == "effect_created" and "effect" in refs:
            self._effect_created(item, refs["effect"])
        elif kind == "effect_deactivated":
            self._effect_ended(item, payload, refs)
        elif kind == "consequences_resolved":
            count = len(payload.get("effect_ids") or [])
            self._mark(item, "effect", f"consequences resolved: {count} effect(s)", "green")
        elif kind == "argument_round_started":
            self._mark(item, "system", f"argument round {payload.get('round', '?')}", "dim")
        elif kind == "turn_completed":
            self._mark(item, "turn", f"turn {payload.get('turn', item['turn'])} complete",
                       "bold")
        elif kind == "game_paused":
            self.status = "paused"
            self.pause_reason = str(payload.get("reason") or "unknown")
            self._mark(item, "paused", f"game paused: {self.pause_reason.replace('_', ' ')}",
                       "bold red")
        elif kind == "game_completed":
            self.status, self.phase = "complete", "complete"
            self.active = None
            self._mark(item, "system", "game complete", "bold")

        line = describe(item, self.names)
        if line is not None:
            self.lines.append(line)

    def _phase_started(self, item: dict[str, Any], phase: str) -> None:
        self.phase = phase
        self.phases_seen.add(phase)
        if phase == "diplomacy":
            self.ready = set()
            self.active = None
            self.arguments_used = {}
            self.phase_trail = []
            self.diplomacy_round = 0
            self.diplomacy_close_reason = None
            self.turn_arguments = 0
            self._mark(item, "turn", f"turn {item['turn']} begins", "bold cyan")
        self.phase_trail.append(phase)
        argument = self.arguments.get(self.current_argument or "")
        if argument is not None and argument.adjudication is None:
            if phase in ("adjudication", "reply"):
                argument.stage = phase

    def _message(self, item: dict[str, Any], message: dict[str, Any]) -> None:
        sender = message["sender_actor_id"]
        recipients = tuple(message.get("recipients") or ())
        public = bool(message["public"])
        self.messages.append(MessageView(item["seq"], item["turn"], _clock(item), sender,
                                         recipients, public, message["text"]))
        if public:
            self.broadcasts[sender] += 1
            self._stat(sender, "public")
            self._turn_stat("public")
        else:
            for recipient in recipients:
                self.links[(sender, recipient)] += 1
                self._stat(recipient, "received")
            self._stat(sender, "private")
            self._turn_stat("private")

    def _eligible(self, owner: str, challengers: list[str] | None) -> list[str]:
        everyone = [a["id"] for a in self.actors] or list(self.turn_order)
        opponents = [a for a in everyone if a != owner]
        if challengers is None:
            return opponents
        return [a for a in opponents if a in set(challengers)]

    def _argument(self, item: dict[str, Any], ref: dict[str, Any]) -> None:
        owner = ref["actor_id"]
        # The sealed challenger set is fixed when the Argument is submitted (I-055), so the
        # ref's value is the value at this event.
        challengers = ref.get("challenger_actor_ids")
        argument = ArgumentView(
            id=ref["id"], seq=item["seq"], turn=item["turn"], time=_clock(item), owner=owner,
            action=ref.get("action", ""), desired_effect=ref.get("desired_effect", ""),
            pros=list(ref.get("pros") or []), eligible=self._eligible(owner, challengers),
            sampled=challengers is not None,
        )
        self.arguments[argument.id] = argument
        self.current_argument = argument.id
        self.active = owner
        self.arguments_used[owner] = self.arguments_used.get(owner, 0) + 1
        self.turn_arguments += 1
        self._stat(owner, "arguments")
        self._turn_stat("arguments")
        self._mark(item, "argument", f"{self.name(owner)} argues: {argument.action}",
                   "yellow")

    def _challengers(self, payload: dict[str, Any]) -> None:
        argument = self.arguments.get(str(payload.get("argument_id")))
        challengers = payload.get("challenger_actor_ids")
        if argument is not None and isinstance(challengers, list):
            argument.eligible = self._eligible(argument.owner, challengers)
            argument.sampled = True

    def _con(self, item: dict[str, Any], con: dict[str, Any]) -> None:
        argument = self.arguments.get(str(con.get("argument_id")))
        if argument is not None:
            argument.cons.append(ConView(con["actor_id"], con.get("reason", ""), item["seq"],
                                         _clock(item)))
        self._stat(con["actor_id"], "cons")
        self._turn_stat("cons")

    def _adjudicated(self, item: dict[str, Any], payload: dict[str, Any],
                     refs: dict[str, Any]) -> None:
        argument_ref = refs.get("argument") or {}
        record = refs.get("adjudication") or {
            # The event payload repeats the server's resolution, so a missing record still
            # leaves the outcome visible.
            "mode": payload.get("mode", "?"), "probability": payload.get("probability"),
            "roll": payload.get("roll"), "success": payload.get("success"),
            "rationale": "", "success_effects": [], "failure_effects": [],
            "supersedes_effect_ids": [],
        }
        argument_id = str(payload.get("argument_id") or argument_ref.get("id"))
        argument = self.arguments.get(argument_id)
        owner = argument_ref.get("actor_id") or (argument.owner if argument else "")
        if argument is not None:
            argument.adjudication = record
            argument.stage = "resolved"
        self.ruling = Ruling(
            actor_id=owner,
            action=argument_ref.get("action") or (argument.action if argument else ""),
            mode=record["mode"],
            probability=record["probability"],
            roll=record["roll"],
            success=record["success"],
            argument_id=argument_id,
            turn=item["turn"],
        )
        outcome = "successes" if record["success"] else "failures"
        self._stat(owner, outcome)
        self._turn_stat(outcome)
        if record["mode"] == "probabilistic" and record["probability"] is not None:
            self._turn_stat("probabilistic")
            self.probability_sums[self.turn] = (
                self.probability_sums.get(self.turn, 0.0) + float(record["probability"])
            )
        verdict = "SUCCESS" if record["success"] else "FAILURE"
        detail = (
            f"p={_probability(record['probability'])}, roll {_probability(record['roll'])}"
            if record["mode"] == "probabilistic" else record["mode"].replace("_", " ")
        )
        self._mark(item, "ruling", f"ruling on {self.name(owner)}: {verdict} ({detail})",
                   "green" if record["success"] else "red")

    def _effect_created(self, item: dict[str, Any], ref: dict[str, Any]) -> None:
        effect = EffectView.from_ref(ref, item["seq"])
        self.effects[effect.id] = effect
        source = self.arguments.get(effect.source_argument_id or "")
        if source is not None:
            source.effect_ids.append(effect.id)
        self._turn_stat("effects_created")
        scope = "" if not effect.private else " (private)"
        self._mark(item, "effect", f"effect{scope}: {effect.description}", "green")

    def _effect_ended(self, item: dict[str, Any], payload: dict[str, Any],
                      refs: dict[str, Any]) -> None:
        effect_id = str(payload.get("effect_id"))
        effect = self.effects.get(effect_id)
        if effect is None and "effect" in refs:
            effect = EffectView.from_ref(refs["effect"], 0)
            self.effects[effect_id] = effect
        if effect is None:
            return
        effect.active = False
        effect.ended_seq = item["seq"]
        effect.ended_turn = item["turn"]
        effect.ended_reason = payload.get("reason")
        effect.superseded_by = payload.get("superseded_by")
        self._turn_stat("effects_ended")
        self._mark(item, "effect_end",
                   f"effect ends ({effect.ended_reason or '?'}): {effect.description}", "dim")

    # --- queries -------------------------------------------------------------------------------

    def visible_lines(self) -> list[FeedLine]:
        lines = list(self.lines)
        if self.secret_only:
            lines = [line for line in lines if line.private]
        if self.actor_filter is not None:
            lines = [line for line in lines if self.actor_filter in line.actors]
        return lines

    def cycle_actor_filter(self) -> None:
        ids = [actor["id"] for actor in self.actors]
        if not ids:
            return
        if self.actor_filter not in ids:
            self.actor_filter = ids[0]
        else:
            index = ids.index(self.actor_filter) + 1
            self.actor_filter = ids[index] if index < len(ids) else None

    def totals_known(self) -> bool:
        """Whether the run's model-call totals describe what is on screen.

        Totals come from the snapshot, which is read now, so a replay shows them only once it
        has caught up with the snapshot.
        """
        return bool(self.totals) and (self.live or self.cursor >= self.snapshot_cursor)

    def failures(self, actor_id: str) -> tuple[int, dict[str, Any] | None]:
        """How many of ``actor_id``'s activations failed, and the latest failure.

        Failed activations come from the snapshot, which is read now, so like the run totals a
        replay shows them only once it has caught up with the snapshot (#34).
        """
        if not (self.live or self.cursor >= self.snapshot_cursor):
            return 0, None
        for actor in self.actors:
            if actor["id"] == actor_id:
                return int(actor.get("failed_activations") or 0), actor.get("last_failure")
        return 0, None

    def failure_holding(self, actor_id: str) -> dict[str, Any] | None:
        """The failure the game is stuck behind for ``actor_id``, if any.

        That is the Actor's latest failure when it is newer than the last event: the game still
        needs this Actor's input and nothing has happened since the attempt failed. Whether the
        runner is retrying or about to stop is not recorded, so this never says which.
        """
        count, last = self.failures(actor_id)
        if not count or not last or self.status != "running":
            return None
        try:
            at = datetime.fromisoformat(last["at"])
        except (KeyError, TypeError, ValueError):
            return None
        if self.last_at is not None and at <= self.last_at:
            return None
        return last

    def actor_ids(self) -> list[str]:
        return [a["id"] for a in self.actors] or list(self.turn_order)

    def model(self, actor_id: str) -> str | None:
        for actor in self.actors:
            if actor["id"] == actor_id:
                return actor.get("model")
        return None

    def argument(self) -> ArgumentView | None:
        """The Argument in play now, or the latest one."""
        return self.arguments.get(self.current_argument or "")

    def pending(self) -> list[str]:
        """Whose input the game needs next, as Actor ids or ``"umpire"``.

        A live viewer takes this from the snapshot, which reads persisted state. A replay derives
        it from the feed up to its cursor. Either way it means "input needed", never that a model
        call is running: nothing records when one starts.
        """
        if self.live:
            return list(self.waiting_on)
        if self.status != "running":
            return []
        if self.phase == "diplomacy":
            return [a for a in self.actor_ids() if a not in self.ready]
        argument = self.argument()
        if self.phase == "cons" and argument is not None and argument.stage == "cons":
            return [a for a in argument.eligible if a not in argument.done]
        if self.phase == "reply" and argument is not None:
            return [argument.owner]
        if self.phase in ("adjudication", "consequences"):
            return ["umpire"]
        return []

    def active_effects(self) -> list[EffectView]:
        return [e for e in self.effects.values() if e.active]

    def ended_effects(self) -> list[EffectView]:
        return sorted((e for e in self.effects.values() if not e.active),
                      key=lambda e: e.ended_seq or 0)

    def stage_of(self, phase: str) -> str:
        """``done``, ``current``, ``todo`` or ``unused`` for one pipeline phase this turn."""
        if self.status == "complete" or self.phase == "complete":
            return "done" if phase in self.phase_trail or phase not in OPTIONAL_PHASES \
                else "unused"
        if phase == self.phase:
            return "current"
        if phase in OPTIONAL_PHASES and phase not in self.phases_seen:
            return "unused"
        if self.phase in PIPELINE:
            if PIPELINE.index(phase) < PIPELINE.index(self.phase) and phase in self.phase_trail:
                return "done"
        return "todo"
