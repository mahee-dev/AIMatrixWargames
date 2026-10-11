"""The terminal viewer's navigation state and key bindings.

Navigation only changes what the viewer draws. No key reaches the game: pausing pauses the
drawing, never the run, and switching screens neither restarts nor resynchronises anything
(TRD §41.1).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from observer.viewstate import ViewState

SCREENS = ("live", "network", "adjudication", "world")
TITLES = {
    "live": "LIVE COMMAND",
    "network": "ACTOR NETWORK",
    "adjudication": "ADJUDICATION",
    "world": "WORLD STATE",
}

# Keys the replay loop consumes to advance one event.
STEP_KEYS = frozenset({" ", "n"})


@dataclass
class UiState:
    """Which screen is up, what is selected and which overlay is open."""

    screen: str = "live"
    feed_focus: bool = False
    # Feed lines back from the newest; 0 follows the newest line.
    scroll: int = 0
    # The Argument shown on the adjudication screen; ``None`` follows the latest.
    argument_id: str | None = None
    actor_index: int = 0
    effect_index: int = 0
    detail: bool = False
    detail_scroll: int = 0
    help: bool = False
    ascii: bool = False
    replay_index: int | None = None
    replay_total: int | None = None
    step: bool = False


def _move_argument(state: ViewState, delta: int) -> None:
    ids = list(state.arguments)
    if not ids:
        return
    current = state.ui.argument_id if state.ui.argument_id in ids else ids[-1]
    index = max(0, min(len(ids) - 1, ids.index(current) + delta))
    # Selecting the newest Argument goes back to following the latest one.
    state.ui.argument_id = None if index == len(ids) - 1 else ids[index]


def _move(state: ViewState, delta: int) -> None:
    """Up is ``-1``: an older feed line, an earlier Argument, the previous row."""
    ui = state.ui
    if ui.detail:
        ui.detail_scroll = max(0, ui.detail_scroll - delta)
        return
    if ui.screen == "live":
        limit = max(0, len(state.visible_lines()) - 1)
        ui.scroll = max(0, min(limit, ui.scroll - delta))
    elif ui.screen == "adjudication":
        _move_argument(state, delta)
    elif ui.screen == "network":
        count = len(state.actor_ids())
        if count:
            ui.actor_index = (ui.actor_index + delta) % count
    elif ui.screen == "world":
        count = len(state.effects)
        if count:
            ui.effect_index = max(0, min(count - 1, ui.effect_index + delta))


def handle_key(state: ViewState, key: str | None) -> bool:
    """Apply one keypress to the view; ``False`` means quit."""
    ui = state.ui
    if key is None:
        return True
    if key in ("q", "Q"):
        return False
    if key in ("1", "2", "3", "4"):
        ui.screen = SCREENS[int(key) - 1]
        ui.detail = ui.help = False
        ui.detail_scroll = 0
    elif key == "f":
        ui.screen = "live"
        ui.feed_focus = not ui.feed_focus
        ui.detail = False
    elif key == "p":
        state.paused = not state.paused
    elif key == "s":
        state.secret_only = not state.secret_only
        ui.scroll = 0
    elif key == "a":
        state.cycle_actor_filter()
        ui.scroll = 0
        ids = state.actor_ids()
        if state.actor_filter in ids:
            ui.actor_index = ids.index(state.actor_filter)
    elif key == "up":
        _move(state, -1)
    elif key == "down":
        _move(state, 1)
    elif key == "g":
        ui.scroll = 0
        ui.argument_id = None
    elif key == "enter":
        ui.detail = not ui.detail
        ui.detail_scroll = 0
        ui.help = False
    elif key == "esc":
        if ui.detail or ui.help:
            ui.detail = ui.help = False
        else:
            ui.feed_focus = False
    elif key == "?":
        ui.help = not ui.help
        ui.detail = False
    return True
