"""What every screen shares: glyphs and colours, panels, and the status vocabulary.

A colour never carries meaning alone: private, pending and failed things also say so in text.
Every glyph has an ASCII twin for terminals without Unicode.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from importlib import metadata

from rich import box
from rich.box import Box
from rich.console import Console, ConsoleOptions, RenderableType, RenderResult
from rich.panel import Panel
from rich.segment import Segment
from rich.text import Text

from observer.viewstate import PIPELINE, ViewState

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

WIDE = 120
NORMAL = 80

TITLE = "bold cyan"
BORDER = "grey42"
MUTED = "grey58"
PENDING = "bold yellow"
GOOD = "green"
BAD = "red"
PRIVATE = "magenta"
PUBLIC = "cyan"
# Identity markers for Actors, assigned in roster order. They only tell Actors apart; meaning
# (private, pending, success) is always carried by a label as well as a colour.
ACTOR_COLOURS = (
    "dodger_blue1", "hot_pink", "spring_green2", "gold1",
    "medium_purple1", "dark_orange", "turquoise2", "light_slate_blue",
)
TAGS = {
    "public": ("PUB", PUBLIC),
    "private": ("PRIV", PRIVATE),
    "argument": ("ARG", "yellow"),
    "con": ("CON", "yellow"),
    "ruling": ("RULE", "bold"),
    "effect": ("EFF", GOOD),
    "system": ("SYS", MUTED),
}


@dataclass(frozen=True)
class Glyphs:
    box: Box
    inner: Box
    dot: str
    ok: str
    bad: str
    arrow: str
    now: str
    todo: str
    unused: str
    full: str
    empty: str
    mark: str
    sep: str
    more: str
    h: str
    v: str
    down: str
    up: str
    corners: str  # top-left, top-right, bottom-left, bottom-right
    numbers: tuple[str, ...]


UNICODE = Glyphs(
    box.SQUARE, DASHED, "●", "✓", "✗", "→", "▶", "○", "–", "█", "░", "│", "›", "…",
    "─", "│", "╲", "╱", "┌┐└┘", ("①", "②", "③", "④", "⑤", "⑥"),
)
ASCII = Glyphs(
    box.ASCII, box.ASCII, "*", "+", "x", "->", ">", "o", "-", "#", ".", "|", ">", "...",
    "-", "|", "\\", "/", "++++", ("1.", "2.", "3.", "4.", "5.", "6."),
)


def glyphs(state: ViewState) -> Glyphs:
    return ASCII if state.ui.ascii else UNICODE


def _version() -> str:
    try:
        return metadata.version("amaw")
    except metadata.PackageNotFoundError:
        return "?"


VERSION = _version()


# --- small helpers ---------------------------------------------------------------------------


def clip(value: str, width: int, g: Glyphs = UNICODE) -> str:
    """``value`` on one line, cut to ``width`` cells with an ellipsis."""
    value = " ".join(str(value).split())
    if width <= 0:
        return ""
    if len(value) <= width:
        return value
    if width <= len(g.more):
        return value[:width]
    return value[: width - len(g.more)] + g.more


def actor_colour(state: ViewState, actor_id: str | None) -> str:
    ids = state.actor_ids()
    if actor_id in ids:
        return ACTOR_COLOURS[ids.index(actor_id) % len(ACTOR_COLOURS)]
    return "white"


def actor_label(state: ViewState, actor_id: str | None, width: int | None = None,
                *, dot: bool = True) -> Text:
    g = glyphs(state)
    name = state.name(actor_id)
    if width is not None:
        name = clip(name, max(1, width - (2 if dot else 0)), g)
    text = Text()
    if dot and actor_id and actor_id != "umpire":
        text.append(f"{g.dot} ", style=actor_colour(state, actor_id))
    text.append(name, style="bold " + (actor_colour(state, actor_id) if dot else "white"))
    return text


def fmt_probability(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}"


def probability_bar(probability: float | None, roll: float | None, width: int = 20,
                    g: Glyphs = UNICODE) -> Text:
    """The Umpire's probability as a filled bar, with the server's roll marked on it."""
    text = Text()
    if probability is None or width <= 0:
        return text
    filled = round(probability * width)
    mark = None if roll is None else min(width - 1, int(roll * width))
    for index in range(width):
        char = g.full if index < filled else g.empty
        if index == mark:
            text.append(g.mark, style="bold white")
        else:
            text.append(char, style=GOOD if index < filled else "dim")
    return text


def _clock_since(state: ViewState) -> str:
    if not state.started_at or not state.last_at:
        return "T+—"
    end = state.last_at
    if state.live and state.status == "running":
        end = datetime.now(state.last_at.tzinfo)
    seconds = max(0, int((end - state.started_at).total_seconds()))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    return f"T+{hours}h{minutes:02d}m" if hours else f"T+{minutes}m{secs:02d}s"


def visibility_label(state: ViewState, private: bool, audience: Iterable[str], width: int,
                     ) -> Text:
    if not private:
        return Text("public", style=PUBLIC)
    names = ", ".join(state.name(a) for a in audience)
    return Text(clip(f"private: {names}" if names else "private", width, glyphs(state)),
                style=PRIVATE)


class Sized:
    """A panel body built once its width and height are known.

    ``tail`` keeps the last lines when the body is taller than its space (feeds, timelines);
    otherwise the first lines are kept and the last visible line says there is more. A builder
    that raises draws its error instead of failing the frame.
    """

    def __init__(self, build: Callable[[int, int], RenderableType], *, name: str = "panel",
                 tail: bool = False, more: str | None = None) -> None:
        self.build = build
        self.name = name
        self.tail = tail
        self.more = more

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        width = options.max_width
        height = options.height if options.height is not None else 12
        try:
            renderable = self.build(width, height)
        except Exception as exc:  # noqa: BLE001 - a panel must never take the viewer down
            renderable = Text(f"{self.name} unavailable: {type(exc).__name__}: {exc}",
                              style=BAD)
        lines = console.render_lines(renderable, options.update(width=width, height=None),
                                     pad=False)
        if len(lines) > height:
            if self.tail:
                lines = lines[-height:]
            else:
                lines = lines[:height]
                if self.more and height > 0:
                    hint = Text(clip(self.more, width), style="dim italic")
                    lines[-1] = list(hint.render(console))
        for line in lines:
            yield from line
            yield Segment.line()


def panel(body: RenderableType, title: str, state: ViewState, *, tag: str | None = None,
          tag_style: str = MUTED, border: str = BORDER, accent: str = TITLE) -> Panel:
    heading = Text(f" {title} ", style=accent)
    if tag:
        heading.append(f"{tag} ", style=tag_style)
    return Panel(body, title=heading, title_align="left", box=glyphs(state).box,
                 border_style=border, padding=(0, 1))


def empty_state(message: str, hint: str | None = None) -> Text:
    text = Text(message, style=MUTED)
    if hint:
        text.append(f"\n{hint}", style="dim italic")
    return text


def layout_mode(width: int) -> str:
    return "wide" if width >= WIDE else "normal" if width >= NORMAL else "narrow"


# --- shared status vocabulary ------------------------------------------------------------------


def actor_state(state: ViewState, actor_id: str) -> tuple[str, str]:
    """A short label for where one Actor stands, from recorded state only."""
    if state.status == "complete":
        return "done", MUTED
    if state.status == "paused":
        return "paused", BAD
    if state.status != "running":
        return "—", MUTED
    pending = actor_id in state.pending()
    argument = state.argument()
    if state.phase == "diplomacy":
        return ("READY", GOOD) if actor_id in state.ready else ("NOT READY", PENDING)
    if state.phase == "argument":
        return ("ARGUMENT DUE", PENDING) if pending else ("—", MUTED)
    if argument is not None and actor_id == argument.owner:
        if state.phase == "reply":
            return "REPLY DUE", PENDING
        if state.phase == "cons":
            return "ARGUING", "bold cyan"
        return "UNDER RULING", "cyan"
    if state.phase == "cons" and argument is not None:
        status = argument.con_status(actor_id)
        return {
            "con": ("CON IN", "yellow"),
            "passed": ("PASSED", MUTED),
            "pending": ("CON DUE", PENDING),
            "no response": ("NO RESPONSE", BAD),
        }.get(status, ("—", MUTED))
    return "—", MUTED


PHASE_NAMES = {
    "diplomacy": "Diplomacy", "argument": "Argument", "cons": "Cons", "reply": "Reply",
    "adjudication": "Adjudication", "consequences": "Consequences", "complete": "Complete",
    "lobby": "Lobby",
}

SHORT_PHASES = {
    "diplomacy": "DIP", "argument": "ARG", "cons": "CONS", "reply": "RPLY",
    "adjudication": "ADJ", "consequences": "CSQ",
}

# What the rules let happen after each phase (state-machine §§15-22). These describe the
# procedure, not a prediction of what any Actor will do.
THEN = {
    "diplomacy": "Arguments open when every Actor is ready or the round limit is reached",
    "argument": "the submitted Argument opens for Cons",
    "cons": "the Umpire adjudicates once Con participation closes (a reply first, if used)",
    "reply": "the Umpire adjudicates after the owner's reply",
    "adjudication": "the server rolls against the Umpire's probability and records Effects",
    "consequences": "the turn closes and the next turn opens with diplomacy",
}


def pipeline_strip(state: ViewState, width: int) -> Text:
    g = glyphs(state)
    short = width < 70
    tight = width < 52
    text = Text()
    for index, phase in enumerate(PIPELINE):
        stage = state.stage_of(phase)
        label = SHORT_PHASES[phase] if short else PHASE_NAMES[phase]
        marker, style = {
            "done": (g.ok, GOOD), "current": (g.now, PENDING),
            "unused": (g.unused, "dim"), "todo": (g.todo, MUTED),
        }[stage]
        if index:
            text.append(" " if tight else f" {g.sep} ", style="dim")
        text.append(f"{marker}{'' if tight else ' '}{label}", style=style)
    return text
