"""The terminal viewer: a live command console for one game, drawn from the observer feed.

``amaw watch <game_id>`` attaches to a game from any terminal: it follows a running game and
replays a finished one. ``amaw run --watch`` opens the same view while the run plays. The viewer
only ever reads through :mod:`observer.feed`, so closing or crashing it cannot affect a run
(TRD §41.1, D-122).

The viewer has four screens (Live Command, Actor Network, Adjudication, World State) over one
state. The parts are:

* :mod:`observer.viewstate` folds feed items into :class:`ViewState`, one item at a time;
* :mod:`observer.navigation` holds the screen, selection and overlays, and maps keys to them;
* :mod:`observer.screens` draws a state for whatever terminal size it is given;
* this module polls the feed, reads keys and redraws, and nothing else.
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
from typing import Any

from rich.console import Console, RenderableType
from rich.live import Live
from rich.text import Text
from sqlalchemy import Engine

from observer import feed as observer
from observer.navigation import SCREENS, STEP_KEYS, handle_key
from observer.screens import DASHED, Frame, probability_bar
from observer.viewstate import (
    FEED_LINES,
    STYLES,
    FeedLine,
    Ruling,
    ViewState,
    describe,
    setup_effects,
)

__all__ = [
    "DASHED", "FEED_LINES", "SCREENS", "STYLES", "FeedLine", "Ruling", "ViewState",
    "describe", "render", "watch", "watch_in_background",
]


def _bar(probability: float | None, roll: float | None, width: int = 20) -> Text:
    """The Umpire's probability as a filled bar, with the server's roll marked on it."""
    return probability_bar(probability, roll, width)


def render(state: ViewState, *, feed_height: int | None = None, screen: str | None = None,
           height: int | None = None) -> RenderableType:
    """One full frame of the viewer, on ``screen`` or the state's current screen.

    The frame lays itself out for the space it is drawn in; ``height`` fixes its height when it
    is printed rather than drawn full-screen. ``feed_height`` is accepted for compatibility and
    no longer needed, since the feed fills whatever room its panel gets.
    """
    if screen is not None:
        state.ui.screen = screen
    return Frame(state, height=height)


# Terminal key sequences, decoded to the names :func:`observer.navigation.handle_key` takes.
_SEQUENCES = {
    "\x1b[A": "up", "\x1b[B": "down", "\x1b[C": "right", "\x1b[D": "left",
    "\x1bOA": "up", "\x1bOB": "down", "\x1bOC": "right", "\x1bOD": "left",
}
_SINGLE = {"\r": "enter", "\n": "enter", "\x7f": "backspace"}


def decode_keys(chunk: str) -> list[str]:
    """The keys in one read from the terminal: arrows, enter and esc by name, others as is."""
    keys: list[str] = []
    index = 0
    while index < len(chunk):
        char = chunk[index]
        if char == "\x1b":
            sequence = chunk[index:index + 3]
            if sequence in _SEQUENCES:
                keys.append(_SEQUENCES[sequence])
                index += 3
                continue
            if chunk[index + 1:index + 2] in ("[", "O"):
                # An unknown sequence (function keys, page up): skip to its final byte.
                end = index + 2
                while end < len(chunk) and not (chunk[end].isalpha() or chunk[end] == "~"):
                    end += 1
                index = end + 1
                continue
            keys.append("esc")
            index += 1
            continue
        keys.append(_SINGLE.get(char, char))
        index += 1
    return keys


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
    queued: deque[str] = deque()

    def read(timeout: float) -> str | None:
        if queued:
            return queued.popleft()
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return None
        # Read the descriptor, not the buffered stream, so an escape sequence is never split
        # between Python's buffer and the terminal.
        chunk = os.read(fd, 64).decode(errors="ignore")
        if chunk.endswith(("\x1b", "\x1b[", "\x1bO")):
            more, _, _ = select.select([fd], [], [], 0.03)
            if more:
                chunk += os.read(fd, 64).decode(errors="ignore")
        queued.extend(decode_keys(chunk))
        return queued.popleft() if queued else None

    try:
        yield read
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def _handle_key(state: ViewState, key: str | None) -> bool:
    """Apply one keypress; ``False`` means quit."""
    return handle_key(state, key)


def _use_ascii(console: Console, ascii: bool | None) -> bool:
    """Plain ASCII when asked, or when the terminal cannot encode box-drawing characters."""
    if ascii is not None:
        return ascii
    if os.environ.get("AMAW_ASCII", "").strip() not in ("", "0"):
        return True
    return not (console.encoding or "").lower().replace("-", "").startswith("utf")


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
    screen: str = "live",
    ascii: bool | None = None,
) -> ViewState:
    """Draw the viewer for one game until it ends, the viewer quits or ``stop`` is set.

    ``game_id`` may be a callable that returns ``None`` until the game exists, as under
    ``amaw run --watch``. A finished game replays at ``speed`` events per second, or one event
    per keypress with ``step``; pass ``replay=False`` to show its final frame. Off a terminal,
    one frame of ``screen`` with the game as it stands is printed and the function returns.
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
    state.ui.screen = screen if screen in SCREENS else "live"
    state.ui.ascii = _use_ascii(console, ascii)
    state.ui.step = step
    first = observer.snapshot(engine, target)
    if replay is None:
        replay = first["game"]["status"] == "complete"
    state.live = not replay
    state.take_snapshot(first)
    items = observer.feed(engine, target)["items"]
    state.seed_effects(setup_effects(first, items))

    if not console.is_terminal:
        for item in items:
            state.apply(item)
        state.live = True
        state.take_snapshot(observer.snapshot(engine, target))
        console.print(render(state))
        return state

    if replay:
        _replay(state, items, console, speed=speed, step=step, stop=stop, keys=keys)
    else:
        for item in items:
            state.apply(item)
        state.take_snapshot(observer.snapshot(engine, target))
        _follow(engine, target, state, console, poll_seconds=poll_seconds, stop=stop, keys=keys)
    return state


def _follow(
    engine: Engine, game_id: uuid.UUID, state: ViewState, console: Console, *,
    poll_seconds: float, stop: threading.Event, keys: bool,
) -> None:
    """Follow a live game: fold only new events, redraw only on a change, a key or a resize."""
    pending: list[dict[str, Any]] = []
    with _keyboard(keys) as read_key, Live(
        render(state), console=console, auto_refresh=False, screen=True,
        redirect_stderr=False,
    ) as live:
        last_poll = 0.0
        last_tick = time.monotonic()
        size = console.size
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
            if console.size != size:
                size, dirty = console.size, True
            key = read_key(0.2)
            if not _handle_key(state, key):
                break
            if dirty or key is not None:
                live.update(render(state), refresh=True)


def _replay(
    state: ViewState, items: list[dict[str, Any]], console: Console, *,
    speed: float, step: bool, stop: threading.Event, keys: bool,
) -> None:
    """Replay a finished game event by event, so each frame shows only what was known then."""
    index = 0
    interval = 0.0 if speed <= 0 else 1.0 / speed
    state.ui.replay_index, state.ui.replay_total = 0, len(items)
    with _keyboard(keys) as read_key, Live(
        render(state), console=console, auto_refresh=False, screen=True,
        redirect_stderr=False,
    ) as live:
        next_at = time.monotonic()
        size = console.size
        while not stop.is_set():
            advance = False
            key = read_key(0.05)
            if not _handle_key(state, key):
                break
            if index < len(items):
                if key in STEP_KEYS:
                    advance = True
                elif not step and not state.paused and time.monotonic() >= next_at:
                    advance = True
                    next_at = time.monotonic() + interval
            if advance:
                state.apply(items[index])
                index += 1
                state.ui.replay_index = index
            resized = console.size != size
            size = console.size
            # Redraw only on a change: a full-screen redraw on every tick floods the terminal.
            if advance or key is not None or resized:
                live.update(render(state), refresh=True)


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
