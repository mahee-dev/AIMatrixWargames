"""Screen 2, Actor Network: who communicated with whom, from Messages actually sent.

Private Messages are drawn as links with their direction and count. Public broadcasts go to
everyone and are counted per sender instead of drawn as directed links. Nothing here infers
alliances, trust or hostility. Small games get a node-and-edge diagram; large games or narrow
terminals get a matrix or a list.
"""

from __future__ import annotations

import math

from rich.console import Console, Group, RenderableType
from rich.layout import Layout
from rich.table import Table
from rich.text import Text

from observer.screens.common import (
    ASCII,
    BORDER,
    MUTED,
    PENDING,
    PRIVATE,
    PUBLIC,
    empty_state,
    layout_mode,
    Glyphs,
    Sized,
    actor_colour,
    actor_label,
    actor_state,
    clip,
    glyphs,
    panel,
)
from observer.viewstate import ViewState

# --- Actor Network -----------------------------------------------------------------------------


class Canvas:
    """A grid of styled characters for drawing a small node-and-edge diagram."""

    def __init__(self, width: int, height: int) -> None:
        self.width, self.height = width, height
        self.chars = [[" "] * width for _ in range(height)]
        self.styles = [[""] * width for _ in range(height)]

    def put(self, x: int, y: int, char: str, style: str = "") -> None:
        if 0 <= x < self.width and 0 <= y < self.height:
            self.chars[y][x] = char
            self.styles[y][x] = style

    def text(self, x: int, y: int, value: str, style: str = "") -> None:
        for offset, char in enumerate(value):
            self.put(x + offset, y, char, style)

    def line(self, start: tuple[int, int], end: tuple[int, int], g: Glyphs,
             style: str) -> None:
        (x0, y0), (x1, y1) = start, end
        dx, dy = x1 - x0, y1 - y0
        # Terminal cells are about twice as tall as wide, so slope is judged at double height.
        angle = abs(math.degrees(math.atan2(dy * 2, dx))) if (dx or dy) else 0.0
        if angle < 25 or angle > 155:
            char = g.h
        elif 65 < angle < 115:
            char = g.v
        else:
            char = g.down if (dx > 0) == (dy > 0) else g.up
        steps = max(abs(dx), abs(dy), 1)
        for step in range(steps + 1):
            x = round(x0 + dx * step / steps)
            y = round(y0 + dy * step / steps)
            self.put(x, y, char, style)

    def box(self, x: int, y: int, width: int, height: int, g: Glyphs, style: str) -> None:
        tl, tr, bl, br = g.corners[0], g.corners[1], g.corners[2], g.corners[3]
        self.put(x, y, tl, style)
        self.put(x + width - 1, y, tr, style)
        self.put(x, y + height - 1, bl, style)
        self.put(x + width - 1, y + height - 1, br, style)
        for col in range(x + 1, x + width - 1):
            self.put(col, y, g.h, style)
            self.put(col, y + height - 1, g.h, style)
        for row in range(y + 1, y + height - 1):
            self.put(x, row, g.v, style)
            self.put(x + width - 1, row, g.v, style)
            for col in range(x + 1, x + width - 1):
                self.put(col, row, " ", "")

    def render(self) -> Text:
        text = Text(no_wrap=True, overflow="crop")
        for y in range(self.height):
            run, style = "", self.styles[y][0] if self.width else ""
            for x in range(self.width):
                if self.styles[y][x] != style:
                    text.append(run, style=style)
                    run, style = "", self.styles[y][x]
                run += self.chars[y][x]
            text.append(run, style=style)
            if y < self.height - 1:
                text.append("\n")
        return text


def _pairs(state: ViewState) -> dict[tuple[str, str], int]:
    """Private Message counts per unordered pair of Actors, in roster order."""
    order = {actor: index for index, actor in enumerate(state.actor_ids())}
    pairs: dict[tuple[str, str], int] = {}
    for (sender, recipient), count in state.links.items():
        a, b = sorted((sender, recipient), key=lambda x: order.get(x, len(order)))
        pairs[(a, b)] = pairs.get((a, b), 0) + count
    return pairs


def _arrow(dx: int, dy: int, forward: bool) -> str:
    """The arrow pointing along (dx, dy) on screen, or against it."""
    if not forward:
        dx, dy = -dx, -dy
    angle = math.degrees(math.atan2(dy * 2, dx)) % 360
    return "→↘↓↙←↖↑↗"[round(angle / 45) % 8]


def network_graph(state: ViewState, width: int, height: int) -> RenderableType:
    """A node-and-edge diagram of who sent private Messages to whom."""
    g = glyphs(state)
    ids = state.actor_ids()
    count = len(ids)
    canvas = Canvas(width, height)
    node_w = max(14, min(26, width // (3 if count <= 4 else 4)))
    node_h = 5 if height >= 16 else 4
    rx = max(1.0, (width - node_w) / 2 - 1)
    ry = max(1.0, (height - node_h) / 2)
    start = math.pi if count == 2 else -math.pi / 2
    centres: dict[str, tuple[int, int]] = {}
    for index, actor in enumerate(ids):
        angle = start + 2 * math.pi * index / max(1, count)
        centres[actor] = (round(width / 2 + rx * math.cos(angle)),
                          round(height / 2 + ry * math.sin(angle)))
    selected = ids[state.ui.actor_index % count] if count else None
    focus = state.actor_filter or selected
    for (a, b), total in _pairs(state).items():
        if a not in centres or b not in centres:
            continue
        involved = focus in (a, b)
        style = "bold magenta" if involved else ("magenta" if total >= 3 else "magenta dim")
        canvas.line(centres[a], centres[b], g, style)
    # Counts go on the lines after all lines are drawn, so a crossing never hides a number.
    # Each count carries an arrow for the direction its Messages went.
    for (a, b), total in _pairs(state).items():
        if a not in centres or b not in centres:
            continue
        (x0, y0), (x1, y1) = centres[a], centres[b]
        if g is ASCII:
            label = f" {total} "
        else:
            parts = [f"{count}{_arrow(x1 - x0, y1 - y0, forward)}" for count, forward in
                     ((state.links.get((a, b), 0), True), (state.links.get((b, a), 0), False))
                     if count]
            label = f" {' '.join(parts)} "
        canvas.text(round((x0 + x1) / 2) - len(label) // 2, round((y0 + y1) / 2), label,
                    "bold magenta")
    pending = set(state.pending())
    for actor in ids:
        cx, cy = centres[actor]
        x = max(0, min(width - node_w, cx - node_w // 2))
        y = max(0, min(height - node_h, cy - node_h // 2))
        border = "bold cyan" if actor == focus else (PENDING if actor in pending else BORDER)
        canvas.box(x, y, node_w, node_h, g, border)
        inner = node_w - 4
        colour = actor_colour(state, actor)
        canvas.text(x + 2, y + 1, g.dot, colour)
        canvas.text(x + 4, y + 1, clip(state.name(actor), inner - 2, g), "bold " + colour)
        row = y + 2
        if node_h == 5:
            canvas.text(x + 2, row, clip(state.model(actor) or "model n/a", inner, g), MUTED)
            row += 1
        label, style = actor_state(state, actor)
        canvas.text(x + 2, row, clip(label, inner, g), style)
        public = state.broadcasts.get(actor, 0)
        if public:
            tag = f"pub {public}"
            canvas.text(x + node_w - 2 - len(tag), row, tag, PUBLIC)
    return canvas.render()


def network_matrix(state: ViewState, width: int) -> RenderableType:
    """Who sent how many private Messages to whom, with public broadcasts in their own column."""
    ids = state.actor_ids()
    table = Table(box=glyphs(state).inner, expand=False, pad_edge=False, padding=(0, 1),
                  header_style="dim", border_style=BORDER)
    table.add_column("from \\ to", no_wrap=True, max_width=max(8, width // 3),
                     overflow="ellipsis")
    for index, _ in enumerate(ids, start=1):
        table.add_column(str(index), justify="right", no_wrap=True)
    table.add_column("pub", justify="right", no_wrap=True, style=PUBLIC)
    for index, sender in enumerate(ids, start=1):
        cells: list[RenderableType] = [Text(f"{index} ").append_text(
            actor_label(state, sender, dot=False))]
        for recipient in ids:
            value = state.links.get((sender, recipient), 0)
            cells.append(Text("·" if recipient == sender or not value else str(value),
                              style=PRIVATE if value else "dim"))
        cells.append(str(state.broadcasts.get(sender, 0) or "·"))
        table.add_row(*cells, style="bold" if sender == state.actor_filter else None)
    return table


def adjacency_list(state: ViewState, width: int) -> RenderableType:
    g = glyphs(state)
    rows = []
    for (sender, recipient), value in sorted(state.links.items(), key=lambda kv: -kv[1]):
        row = Text()
        row.append(clip(state.name(sender), width // 3, g), style="bold")
        row.append(f" {g.arrow} ", style="dim")
        row.append(clip(state.name(recipient), width // 3, g), style="bold")
        row.append(f"  {value} private", style=PRIVATE)
        rows.append(row)
    for sender, value in state.broadcasts.most_common():
        row = Text(clip(state.name(sender), width // 2, g), style="bold")
        row.append(f"  {value} public", style=PUBLIC)
        rows.append(row)
    return Group(*rows) if rows else empty_state("No messages yet.")


def network_legend(state: ViewState) -> Text:
    g = glyphs(state)
    text = Text()
    text.append(f"{g.h * 3} ", style=PRIVATE)
    text.append("private Messages between two Actors (count and direction on the line)  ", style=MUTED)
    text.append("pub n", style=PUBLIC)
    text.append(" public broadcasts sent to everyone, not drawn as links", style=MUTED)
    return text


def network_panel(state: ViewState) -> RenderableType:
    def build(width: int, height: int) -> RenderableType:
        ids = state.actor_ids()
        if not ids:
            return empty_state("No Actors yet.")
        legend = network_legend(state)
        legend_lines = len(legend.wrap(Console(width=width), width))
        graph_h = height - legend_lines - 1
        if len(ids) <= 8 and width >= 56 and graph_h >= 10:
            return Group(network_graph(state, width, graph_h), Text(""), legend)
        if width >= 14 + 4 * len(ids) and height >= len(ids) + 4:
            names = Text()
            for index, actor in enumerate(ids, start=1):
                names.append(f"{index} ", style="dim")
                names.append(clip(state.name(actor), 16), style=actor_colour(state, actor))
                names.append("  ")
            return Group(network_matrix(state, width), names)
        return adjacency_list(state, width)

    tag = f"{sum(state.links.values())} private · {sum(state.broadcasts.values())} public"
    return panel(Sized(build, name="network"), "COMMUNICATION GRAPH", state, tag=tag)


def links_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        if not state.links and not state.broadcasts:
            return empty_state("No messages yet.")
        peak = max([*state.links.values(), *state.broadcasts.values(), 1])
        name_w = max(6, (width - 14) // 2)
        rows = []
        entries: list[tuple[str, str | None, int]] = [
            (s, r, v) for (s, r), v in state.links.items()
        ] + [(s, None, v) for s, v in state.broadcasts.items()]
        entries.sort(key=lambda e: -e[2])
        for sender, recipient, value in entries[:height]:
            row = Text()
            row.append(clip(state.name(sender), name_w, g),
                       style="bold " + actor_colour(state, sender))
            row.append(f" {g.arrow} ", style="dim")
            if recipient is None:
                row.append(clip("everyone", name_w, g), style=PUBLIC)
            else:
                row.append(clip(state.name(recipient), name_w, g), style="bold")
            bar = max(1, round(6 * value / peak))
            row.append(f" {g.full * bar} {value}", style=PUBLIC if recipient is None
                       else PRIVATE)
            rows.append(row)
        return Group(*rows)

    return panel(Sized(build, name="links"), "LINKS (sender → recipient)", state)


def recent_comms_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        messages = [m for m in state.messages
                    if (not state.secret_only or not m.public)
                    and (state.actor_filter is None
                         or state.actor_filter in (m.sender, *m.recipients))]
        if not messages:
            return empty_state("No messages yet.")
        rows = []
        for message in messages[-max(1, height // 2):]:
            head = Text(f"{message.time} ", style="dim")
            head.append(clip(state.name(message.sender), 18, g),
                        style="bold " + actor_colour(state, message.sender))
            head.append(f" {g.arrow} ", style="dim")
            if message.public:
                head.append("everyone ", style="bold")
                head.append("PUB", style=PUBLIC)
            else:
                head.append(clip(", ".join(state.name(r) for r in message.recipients),
                                 max(6, width - 36), g) + " ", style="bold")
                head.append("PRIV", style=PRIVATE)
            rows.append(head)
            rows.append(Text("  " + clip(message.text, width - 2, g),
                             style=PUBLIC if message.public else PRIVATE))
        return Group(*rows)

    return panel(Sized(build, name="communications", tail=True), "RECENT COMMUNICATIONS",
                 state)


def network_screen(state: ViewState, width: int, height: int) -> Layout:
    layout = Layout(name="body")
    mode = layout_mode(width)
    if mode == "wide":
        side = Layout(name="side", ratio=2)
        side.split_column(Layout(links_panel(state), size=min(12, max(5, height // 3))),
                          Layout(recent_comms_panel(state), ratio=1))
        layout.split_row(Layout(network_panel(state), ratio=3), side)
    elif mode == "normal":
        bottom = Layout(name="bottom", ratio=2)
        bottom.split_row(Layout(links_panel(state), ratio=1),
                         Layout(recent_comms_panel(state), ratio=1))
        layout.split_column(Layout(network_panel(state), ratio=3), bottom)
    else:
        layout.split_column(Layout(network_panel(state), ratio=1),
                            Layout(recent_comms_panel(state), ratio=1))
    return layout
