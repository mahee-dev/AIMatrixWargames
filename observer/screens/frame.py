"""The full frame: header, the current screen or overlay, and the key footer.

The frame lays itself out for the space it is drawn in, so a resize only changes the next frame.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable

from rich.cells import cell_len
from rich.console import Console, Group, ConsoleOptions, RenderableType, RenderResult
from rich.layout import Layout
from rich.panel import Panel
from rich.segment import Segment
from rich.table import Table
from rich.text import Text

from observer.navigation import SCREENS, TITLES
from observer.screens.adjudication import adjudication_screen
from observer.screens.common import (
    BAD,
    MUTED,
    PENDING,
    PHASE_NAMES,
    PRIVATE,
    VERSION,
    _clock_since,
    layout_mode,
    clip,
    glyphs,
    panel,
)
from observer.screens.live import live_screen
from observer.screens.network import network_screen
from observer.screens.overlays import detail_overlay, help_overlay
from observer.screens.world import world_screen
from observer.viewstate import ViewState

# --- header and footer -------------------------------------------------------------------------


def header(state: ViewState, width: int) -> RenderableType:
    g = glyphs(state)
    game = state.game
    mode = layout_mode(width)
    title = Text()
    title.append("AMAW", style="bold cyan")
    if mode != "narrow":
        title.append(f" v{VERSION}  ", style="cyan")
    else:
        title.append("  ")
    if width >= 110:
        title.append("AI MATRIX WARGAMES  ", style="bold")
    title.append(TITLES[state.ui.screen], style="bold white")

    where = Text()
    scenario = game.get("scenario_id", "?")
    if width >= 110:
        where.append(f"{scenario} v{game.get('scenario_version') or '?'}", style=MUTED)
        where.append(f" {g.v} ", style="dim")
    where.append(f"turn {state.turn}/{game.get('max_turns', '?')}", style="bold")
    where.append(f" {g.v} ", style="dim")
    where.append(PHASE_NAMES.get(state.phase, state.phase), style=PENDING
                 if state.status == "running" else MUTED)
    where.append(f" {g.v} ", style="dim")
    where.append(_clock_since(state), style=MUTED)

    status = Text()
    badge, style = {
        "running": ("RUNNING", "bold green"), "paused": ("GAME PAUSED", "bold red"),
        "complete": ("COMPLETE", "bold cyan"), "lobby": ("LOBBY", MUTED),
    }.get(state.status, (state.status.upper(), MUTED))
    if not state.live:
        status.append("REPLAY ", style="bold magenta")
    status.append(badge, style=style)
    if state.status == "paused" and state.pause_reason:
        status.append(f" ({state.pause_reason.replace('_', ' ')})", style=BAD)
    if state.paused:
        status.append("  VIEW PAUSED", style=PENDING)
        if state.live:
            status.append(" (game continues)", style="dim")
    pending = state.pending()
    if pending:
        status.append("  awaiting input: ", style=MUTED)
        status.append(clip(", ".join(state.name(a) for a in pending),
                           max(10, width // (2 if mode == "narrow" else 3)), g),
                      style=PENDING)

    figures = Text(style=MUTED)
    if not state.live:
        index, total = state.ui.replay_index, state.ui.replay_total
        if total is not None:
            figures.append(f"event {index or 0}/{total}")
    elif state.totals_known():
        tokens = state.totals.get("input_tokens", 0) + state.totals.get("output_tokens", 0)
        figures.append(f"calls {state.totals.get('model_calls', 0):,} {g.v} tok {tokens:,}"
                       f" {g.v} ${state.totals.get('cost_usd', 0) or 0:.2f}")

    # One grid per line, so a long right side on one line never narrows the other.
    rows = []
    for left, right in ((title, where), (status, figures)):
        grid = Table.grid(expand=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_column(no_wrap=True, justify="right")
        grid.add_row(left, right)
        rows.append(grid)
    return Panel(Group(*rows), box=g.box, border_style="cyan", padding=(0, 1))


KEY_HINTS = {
    "live": [("↑↓", "scroll"), ("⏎", "inspect"), ("f", "feed")],
    "network": [("↑↓", "actor"), ("⏎", "messages")],
    "adjudication": [("↑↓", "argument"), ("⏎", "full text"), ("g", "latest")],
    "world": [("↑↓", "effect"), ("⏎", "detail")],
}


def footer(state: ViewState, width: int) -> RenderableType:
    ui = state.ui
    mode = layout_mode(width)
    tabs = Text()
    for index, screen in enumerate(SCREENS, start=1):
        label = f"{index} {screen[:5] if mode == 'narrow' else screen}"
        if screen == ui.screen:
            tabs.append(f" {label} ", style="bold black on cyan")
        else:
            tabs.append(f" {label} ", style=MUTED)
    optional: list[tuple[str, str]]
    if ui.detail or ui.help:
        optional, required = [("↑↓", "scroll")], [("esc", "close"), ("q", "quit")]
    else:
        optional = list(KEY_HINTS[ui.screen])
        if not state.live:
            optional.insert(0, ("space", "step"))
        optional += [("a", "actor"), ("s", "secret"), ("p", "pause")]
        required = [("?", "help"), ("q", "quit")]
    flags = Text()
    if state.secret_only:
        flags.append(" PRIVATE ONLY", style=PRIVATE)
    if state.actor_filter:
        flags.append(" actor: ", style=MUTED)
        flags.append(clip(state.name(state.actor_filter), 18), style="bold")
    mark = "■" if not ui.ascii else "#"
    if state.paused:
        flags.append(" ❚❚ PAUSED" if not ui.ascii else " || PAUSED", style=PENDING)
    elif not state.live:
        flags.append(f" {glyphs(state).now} REPLAY", style="bold magenta")
    elif state.status == "complete":
        flags.append(f" {mark} COMPLETE", style="bold cyan")
    else:
        flags.append(f" {glyphs(state).dot} LIVE", style="bold green")
    def hint(key: str, action: str) -> Text:
        if ui.ascii:
            key = {"↑↓": "up/dn", "⏎": "enter"}.get(key, key)
        text = Text(f" {key}", style="bold cyan")
        text.append(f" {action}", style=MUTED)
        return text

    room = width - tabs.cell_len - flags.cell_len - 1
    room -= sum(hint(*k).cell_len for k in required)
    hints = Text()
    for key in optional:
        piece = hint(*key)
        if piece.cell_len > room:
            break
        hints.append_text(piece)
        room -= piece.cell_len
    for key in required:
        hints.append_text(hint(*key))
    grid = Table.grid(expand=True)
    grid.add_column(no_wrap=True)
    grid.add_column(ratio=1, no_wrap=True, overflow="crop")
    grid.add_column(no_wrap=True, justify="right")
    grid.add_row(tabs, hints, flags)
    return grid



# --- the frame ---------------------------------------------------------------------------------

SCREEN_BUILDERS: dict[str, Callable[[ViewState, int, int], Layout]] = {
    "live": live_screen,
    "network": network_screen,
    "adjudication": adjudication_screen,
    "world": world_screen,
}


def build_frame(state: ViewState, width: int, height: int) -> Layout:
    root = Layout(name="root")
    body_h = max(1, height - 5)
    if state.ui.help:
        body: RenderableType = help_overlay(state)
    elif state.ui.detail:
        body = detail_overlay(state)
    else:
        try:
            body = SCREEN_BUILDERS[state.ui.screen](state, width, body_h)
        except Exception as exc:  # noqa: BLE001 - a broken screen must not end the viewer
            body = panel(Text(f"screen unavailable: {type(exc).__name__}: {exc}", style=BAD),
                         TITLES[state.ui.screen], state)
    root.split_column(
        Layout(header(state, width), size=4),
        Layout(body, name="body", ratio=1),
        Layout(footer(state, width), size=1),
    )
    return root


# One ASCII character for each non-ASCII character the viewer draws, so a translated frame keeps
# its layout cell for cell. Rich's own ellipsis and the feed's rules are included.
_ASCII = str.maketrans({
    **dict.fromkeys("─━┄┈═–—", "-"), **dict.fromkeys("│┃┆┊║", "|"),
    **dict.fromkeys("┌┐└┘╭╮╰╯├┤┬┴┼╔╗╚╝", "+"),
    "…": ".", "·": ".", "›": ">", "→": ">", "←": "<", "↑": "^", "↓": "v", "↗": "/",
    "↙": "/", "↘": "\\", "↖": "\\", "╱": "/", "╲": "\\", "█": "#", "░": ".", "●": "*",
    "✓": "+", "✗": "x", "▶": ">", "◂": "<", "○": "o", "■": "#", "❚": "|", "≤": "<",
    "⏎": "~", "①": "1", "②": "2", "③": "3", "④": "4", "⑤": "5", "⑥": "6",
})


def asciify(text: str) -> str:
    """``text`` with every character drawn in one ASCII cell (``?`` for the rest).

    Letters lose their accents; a double-width character becomes ``??`` so columns still line up.
    """
    text = text.translate(_ASCII)
    if text.isascii():
        return text
    out = []
    for char in text:
        if char.isascii():
            out.append(char)
            continue
        plain = unicodedata.normalize("NFKD", char).encode("ascii", "ignore").decode()
        out.append(plain[:1] if plain else "?" * cell_len(char))
    return "".join(out)


class Frame:
    """One full screen of the viewer, laid out for the space it is drawn in."""

    def __init__(self, state: ViewState, height: int | None = None) -> None:
        self.state = state
        self.height = height

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        height = options.height or self.height or max(console.height, 40)
        layout = build_frame(self.state, width, height)
        lines = console.render_lines(layout, options.update(width=width, height=height))
        new_line = Segment.line()
        ascii_only = self.state.ui.ascii
        for line in lines:
            if ascii_only:
                line = [Segment(asciify(s.text), s.style, s.control) for s in line]
            yield from line
            yield new_line
