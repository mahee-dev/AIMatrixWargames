"""The terminal viewer: a live dashboard of one game, drawn from the observer feed.

``amaw watch <game_id>`` attaches to a game from any terminal: it follows a running game and
replays a finished one. ``amaw run --watch`` opens the same view while the run plays. The viewer
only ever reads through :mod:`observer.feed`, so closing or crashing it cannot affect a run
(TRD §41.1, D-122).

The module splits into pure parts and a thin loop. :class:`ViewState` folds feed items into what
the screen shows, :func:`describe` turns one item into a feed line, and :func:`render` draws a
state. :func:`watch` polls the feed, reads keys and redraws.
"""

from __future__ import annotations

import os
import select
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from rich import box
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from sqlalchemy import Engine

from observer import feed as observer

# Dashed panels, in the style of a terminal status board.
DASHED = box.Box(
    "╭┄┬╮\n"
    "┆ ┆┆\n"
    "├┄┼┤\n"
    "┆ ┆┆\n"
    "├┄┼┤\n"
    "├┄┼┤\n"
    "┆ ┆┆\n"
    "╰┄┴╯\n"
)

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


@dataclass
class Ruling:
    actor_id: str
    action: str
    mode: str
    probability: float | None
    roll: float | None
    success: bool | None


@dataclass
class ViewState:
    """What the dashboard shows, folded from feed items and refreshed from snapshots."""

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

    def take_snapshot(self, snapshot: dict[str, Any]) -> None:
        """Adopt the parts of a snapshot that the feed alone cannot give: names, models, cost."""
        self.game = snapshot["game"]
        self.actors = snapshot["actors"]
        self.names = {actor["id"]: actor["name"] for actor in self.actors}
        self.totals = snapshot["totals"]
        if self.live:
            self.waiting_on = snapshot["waiting_on"]
            self.status = snapshot["game"]["status"]
            self.phase = snapshot["game"]["phase"]
            self.turn = snapshot["game"]["turn"]
            self.ready = {a["id"] for a in self.actors if a["diplomacy_ready"]}
            self.active = snapshot["game"]["active_actor_id"]
            self.arguments_used = {a["id"]: a["arguments_used"] for a in self.actors}

    def apply(self, item: dict[str, Any]) -> None:
        """Fold one feed item into the state, in feed order."""
        self.cursor = item["seq"]
        self.turn = item["turn"]
        at = datetime.fromisoformat(item["created_at"])
        self.started_at = self.started_at or at
        self.last_at = at
        kind, payload, refs = item["type"], item["payload"], item["refs"]
        if kind == "game_started":
            self.status = "running"
        elif kind == "phase_started":
            self.phase = payload.get("phase", self.phase)
            if self.phase == "diplomacy":
                self.ready = set()
                self.active = None
                self.arguments_used = {}
        elif kind == "actor_ready" and item["actor_id"]:
            self.ready.add(item["actor_id"])
        elif kind == "game_paused":
            self.status = "paused"
        elif kind == "game_completed":
            self.status, self.phase = "complete", "complete"
        elif kind == "argument_submitted" and "argument" in refs:
            owner = refs["argument"]["actor_id"]
            self.active = owner
            self.arguments_used[owner] = self.arguments_used.get(owner, 0) + 1
        elif kind == "argument_adjudicated" and "adjudication" in refs and "argument" in refs:
            ruling, argument = refs["adjudication"], refs["argument"]
            self.ruling = Ruling(
                actor_id=argument["actor_id"],
                action=argument["action"],
                mode=ruling["mode"],
                probability=ruling["probability"],
                roll=ruling["roll"],
                success=ruling["success"],
            )
        line = describe(item, self.names)
        if line is not None:
            self.lines.append(line)

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


def _clock(item: dict[str, Any]) -> str:
    return datetime.fromisoformat(item["created_at"]).strftime("%H:%M:%S")


def _probability(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def describe(item: dict[str, Any], names: dict[str, str]) -> FeedLine | None:
    """The feed line for one item, or ``None`` for bookkeeping the feed does not need to show."""
    kind, payload, refs = item["type"], item["payload"], item["refs"]
    actor_id = item["actor_id"]
    clock = _clock(item)

    def name(actor: str | None) -> str:
        return names.get(actor, actor) if actor else "—"

    if kind == "message_sent" and "message" in refs:
        message = refs["message"]
        sender = message["sender_actor_id"]
        if message["public"]:
            return FeedLine(
                clock, name(sender), "public", message["text"],
                frozenset({sender}), style=STYLES["public"],
            )
        recipients = list(message["recipients"])
        who = f"{name(sender)} → {', '.join(name(r) for r in recipients)}"
        return FeedLine(
            clock, who, "private", message["text"],
            frozenset({sender, *recipients}), private=True, style=STYLES["private"],
        )
    if kind == "argument_submitted" and "argument" in refs:
        argument = refs["argument"]
        return FeedLine(
            clock, name(argument["actor_id"]), "argument",
            f"ARGUES {argument['action']} — wants: {argument['desired_effect']}",
            frozenset({argument["actor_id"]}), style=STYLES["argument"],
        )
    if kind == "con_submitted" and "con" in refs:
        con = refs["con"]
        owner = refs.get("argument", {}).get("actor_id")
        return FeedLine(
            clock, name(con["actor_id"]), "con", f"CON vs {name(owner)}: {con['reason']}",
            frozenset({con["actor_id"], *([owner] if owner else [])}), style=STYLES["con"],
        )
    if kind == "owner_reply_submitted" and "reply" in refs:
        points = refs["reply"]["points"]
        text = "passes on reply" if not points else f"replies with {len(points)} point(s)"
        return FeedLine(clock, name(actor_id), "con", text, frozenset({actor_id or ""}))
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
        return FeedLine(
            clock, "Umpire", "ruling", f"{name(owner)}: {verdict} ({detail})",
            frozenset({owner} if owner else ()),
            style="bold green" if ruling["success"] else "bold red",
        )
    if kind in ("effect_created", "effect_deactivated") and "effect" in refs:
        effect = refs["effect"]
        private = effect["visibility"] != "public"
        prefix = "EFFECT" if kind == "effect_created" else "EFFECT ENDS"
        scope = "" if not private else f" [{', '.join(name(a) for a in effect['visible_to'])}]"
        return FeedLine(
            clock, "World", "effect", f"{prefix}{scope}: {effect['description']}",
            frozenset(effect["visible_to"]), private=private, style=STYLES["effect"],
        )
    if kind == "phase_started":
        return FeedLine(clock, "", "system", f"── {payload.get('phase', '?')} ──", style="dim")
    if kind == "turn_completed":
        return FeedLine(
            clock, "", "system", f"══ turn {payload.get('turn', item['turn'])} complete ══",
            style="bold dim",
        )
    if kind == "game_started":
        return FeedLine(clock, "", "system", "game started", style="bold")
    if kind == "game_completed":
        return FeedLine(clock, "", "system", "game complete", style="bold")
    if kind == "game_paused":
        return FeedLine(
            clock, "", "system", f"game paused: {payload.get('reason', '?')}", style="bold red"
        )
    if kind in ("actor_ready", "con_passed", "con_challengers_selected", "cons_revealed"):
        return None
    if kind in ("diplomacy_round_completed", "diplomacy_closed", "argument_round_started",
                "consequences_resolved"):
        return FeedLine(clock, "", "system", kind.replace("_", " "), style="dim")
    return FeedLine(clock, name(actor_id), "system", kind, style="dim")


def _bar(probability: float | None, roll: float | None, width: int = 20) -> Text:
    """The Umpire's probability as a filled bar, with the server's roll marked on it."""
    text = Text()
    if probability is None:
        return text
    filled = round(probability * width)
    mark = None if roll is None else min(width - 1, int(roll * width))
    for index in range(width):
        char = "█" if index < filled else "░"
        if index == mark:
            text.append("│", style="bold white")
        else:
            text.append(char, style="green" if index < filled else "dim")
    return text


def _header(state: ViewState) -> RenderableType:
    game = state.game
    title = Text()
    title.append("AMAW ", style="bold")
    title.append(f"· {game.get('scenario_id', '?')} v{game.get('scenario_version', '?')} ")
    title.append(f"· turn {state.turn}/{game.get('max_turns', '?')} ", style="bold")
    title.append(f"· {state.phase} ", style="cyan")
    title.append(f"· {state.status}", style="bold red" if state.status == "paused" else "")
    elapsed = ""
    if state.started_at and state.last_at:
        end = datetime.now(state.last_at.tzinfo) if state.live and state.status == "running" \
            else state.last_at
        minutes = int((end - state.started_at).total_seconds() // 60)
        elapsed = f"T+{minutes}m · "
    right = Text(
        f"{elapsed}${state.totals.get('cost_usd', 0):.2f} · "
        f"{state.totals.get('input_tokens', 0) + state.totals.get('output_tokens', 0):,} tok",
        style="dim",
    )
    grid = Table.grid(expand=True)
    grid.add_column()
    grid.add_column(justify="right")
    grid.add_row(title, right)
    if state.live and state.waiting_on:
        waiting = ", ".join(state.names.get(a, a) for a in state.waiting_on)
        grid.add_row(Text(f"waiting on: {waiting}", style="yellow"), "")
    return grid


def _actor_panels(state: ViewState) -> RenderableType:
    row = Table.grid(expand=True, padding=(0, 1))
    panels = []
    budget = state.game.get("primary_arguments_per_actor", 1)
    for actor in state.actors:
        body = Text()
        body.append(f"{actor.get('model') or 'model ?'}\n", style="dim")
        if state.phase == "diplomacy":
            ready = actor["id"] in state.ready
            body.append("ready ✓" if ready else "talking…", style="green" if ready else "yellow")
        elif actor["id"] == state.active and state.phase != "complete":
            body.append("arguing", style="bold yellow")
        else:
            body.append("—", style="dim")
        body.append(f"\nargs {state.arguments_used.get(actor['id'], 0)}/{budget}", style="dim")
        highlight = state.actor_filter == actor["id"]
        panels.append(
            Panel(
                body, title=actor["name"], title_align="left", box=DASHED,
                border_style="bold cyan" if highlight else "dim",
            )
        )
    for _ in panels:
        row.add_column(ratio=1)
    if panels:
        row.add_row(*panels)
    return row


def _ruling(state: ViewState) -> RenderableType:
    ruling = state.ruling
    if ruling is None:
        return Text("no ruling yet", style="dim")
    line = Text()
    line.append(f"{state.names.get(ruling.actor_id, ruling.actor_id)}: ", style="bold")
    action = ruling.action if len(ruling.action) <= 60 else ruling.action[:59] + "…"
    line.append(f"{action}  ")
    if ruling.mode == "probabilistic":
        line.append(f"p={_probability(ruling.probability)} ")
        line.append_text(_bar(ruling.probability, ruling.roll))
        line.append(f" roll {_probability(ruling.roll)} ")
    else:
        line.append(f"{ruling.mode.replace('_', ' ')} ", style="dim")
    line.append(
        "✓ success" if ruling.success else "✗ failure",
        style="bold green" if ruling.success else "bold red",
    )
    return line


def _feed(state: ViewState, height: int) -> RenderableType:
    table = Table.grid(padding=(0, 1), expand=True)
    table.add_column(style="dim", no_wrap=True)
    table.add_column(no_wrap=True, max_width=28)
    table.add_column(ratio=1, overflow="fold")
    for line in state.visible_lines()[-height:]:
        tag = " [private]" if line.kind == "private" else ""
        table.add_row(
            line.time, Text(line.who + tag, style=line.style or ""),
            Text(line.text, style=line.style or ""),
        )
    return table


def _keys(state: ViewState) -> Text:
    flags = []
    if state.paused:
        flags.append("PAUSED")
    if state.secret_only:
        flags.append("secret only")
    if state.actor_filter:
        flags.append(f"actor: {state.names.get(state.actor_filter, state.actor_filter)}")
    keys = "q quit · p pause · s secret only · a filter actor"
    if not state.live:
        keys += " · space step"
    return Text(keys + ("   [" + " · ".join(flags) + "]" if flags else ""), style="dim")


def render(state: ViewState, *, feed_height: int = 20) -> RenderableType:
    """One full frame of the dashboard."""
    return Panel(
        Group(
            _header(state),
            _actor_panels(state),
            Panel(_ruling(state), title="last ruling", title_align="left", box=DASHED,
                  border_style="dim"),
            Panel(_feed(state, feed_height), title="feed", title_align="left", box=DASHED,
                  border_style="dim"),
            _keys(state),
        ),
        box=DASHED,
        border_style="dim",
    )


@contextmanager
def _keyboard(enabled: bool) -> Iterator[Callable[[float], str | None]]:
    """Single keypresses without Enter, on a POSIX terminal; a no-op reader elsewhere."""
    if not enabled or not sys.stdin.isatty() or os.name != "posix":
        def wait(timeout: float) -> None:
            time.sleep(timeout)
            return None
        yield wait
        return
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    tty.setcbreak(fd)

    def read(timeout: float) -> str | None:
        ready, _, _ = select.select([sys.stdin], [], [], timeout)
        return sys.stdin.read(1) if ready else None

    try:
        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _handle_key(state: ViewState, key: str | None) -> bool:
    """Apply one keypress; ``False`` means quit."""
    if key in ("q", "Q"):
        return False
    if key == "p":
        state.paused = not state.paused
    elif key == "s":
        state.secret_only = not state.secret_only
    elif key == "a":
        state.cycle_actor_filter()
    return True


def _feed_height(console: Console) -> int:
    return max(5, console.size.height - 16)


def watch(
    engine: Engine,
    game_id: uuid.UUID | Callable[[], uuid.UUID | None],
    *,
    console: Console | None = None,
    replay: bool | None = None,
    speed: float = 4.0,
    step: bool = False,
    poll_seconds: float = 1.0,
    stop: threading.Event | None = None,
    keys: bool = True,
) -> ViewState:
    """Draw the dashboard for one game until it ends, the viewer quits or ``stop`` is set.

    ``game_id`` may be a callable that returns ``None`` until the game exists, as under
    ``amaw run --watch``. A finished game replays at ``speed`` events per second, or one event
    per keypress with ``step``; pass ``replay=False`` to show its final frame. Off a terminal,
    one frame of the game as it stands is printed and the function returns.
    """
    console = console or Console()
    stop = stop or threading.Event()
    resolve = game_id if callable(game_id) else (lambda: game_id)

    target = resolve()
    while target is None:
        if stop.wait(poll_seconds):
            return ViewState()
        target = resolve()

    state = ViewState()
    first = observer.snapshot(engine, target)
    if replay is None:
        replay = first["game"]["status"] == "complete"
    state.live = not replay
    state.take_snapshot(first)

    if not console.is_terminal:
        for item in observer.feed(engine, target)["items"]:
            state.apply(item)
        state.live = True
        state.take_snapshot(observer.snapshot(engine, target))
        console.print(render(state))
        return state

    if replay:
        _replay(engine, target, state, console, speed=speed, step=step, stop=stop, keys=keys)
    else:
        _follow(engine, target, state, console, poll_seconds=poll_seconds, stop=stop, keys=keys)
    return state


def _follow(
    engine: Engine, game_id: uuid.UUID, state: ViewState, console: Console, *,
    poll_seconds: float, stop: threading.Event, keys: bool,
) -> None:
    pending: list[dict[str, Any]] = []
    with _keyboard(keys) as read_key, Live(
        render(state, feed_height=_feed_height(console)), console=console,
        auto_refresh=False, screen=True, redirect_stderr=False,
    ) as live:
        last_poll = 0.0
        last_tick = time.monotonic()
        while not stop.is_set():
            dirty = False
            now = time.monotonic()
            if now - last_poll >= poll_seconds:
                last_poll = now
                page = observer.feed(engine, game_id, after=state.cursor if not pending
                                     else pending[-1]["seq"])
                pending.extend(page["items"])
                if not state.paused:
                    dirty = bool(pending)
                    for item in pending:
                        state.apply(item)
                    pending.clear()
                    state.take_snapshot(observer.snapshot(engine, game_id))
            # The elapsed clock in the header moves once a second even when nothing happens.
            if now - last_tick >= 1.0:
                last_tick, dirty = now, True
            key = read_key(0.2)
            if not _handle_key(state, key):
                break
            if dirty or key is not None:
                live.update(render(state, feed_height=_feed_height(console)), refresh=True)


def _replay(
    engine: Engine, game_id: uuid.UUID, state: ViewState, console: Console, *,
    speed: float, step: bool, stop: threading.Event, keys: bool,
) -> None:
    items = observer.feed(engine, game_id)["items"]
    index = 0
    interval = 0.0 if speed <= 0 else 1.0 / speed
    with _keyboard(keys) as read_key, Live(
        render(state, feed_height=_feed_height(console)), console=console,
        auto_refresh=False, screen=True, redirect_stderr=False,
    ) as live:
        next_at = time.monotonic()
        while not stop.is_set():
            advance = False
            key = read_key(0.05)
            if not _handle_key(state, key):
                break
            if index < len(items):
                if step:
                    advance = key in (" ", "\n", "n")
                elif not state.paused and time.monotonic() >= next_at:
                    advance = True
                    next_at = time.monotonic() + interval
            if advance:
                state.apply(items[index])
                index += 1
            # Redraw only on a change: a full-screen redraw on every tick floods the terminal.
            if advance or key is not None:
                live.update(render(state, feed_height=_feed_height(console)), refresh=True)


def watch_in_background(
    engine: Engine, game_id: Callable[[], uuid.UUID | None], *, console: Console | None = None,
    poll_seconds: float = 1.0,
) -> tuple[threading.Thread, threading.Event]:
    """Start the live viewer on a daemon thread; set the returned event to close it.

    Any error inside the viewer ends the viewer only. It is reported once on the console and
    never raised into the caller, so a run is never failed by its dashboard.
    """
    stop = threading.Event()

    def target() -> None:
        try:
            watch(engine, game_id, console=console, replay=False,
                  poll_seconds=poll_seconds, stop=stop)
        except Exception as exc:  # noqa: BLE001 - the viewer must never fail the run
            (console or Console(stderr=True)).print(f"[dim]viewer closed: {exc}[/dim]")

    thread = threading.Thread(target=target, name="amaw-watch", daemon=True)
    thread.start()
    return thread, stop
