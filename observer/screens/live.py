"""Screen 1, Live Command: where the game stands and what just happened.

Actors and whose input is due, the turn's phase pipeline, the event feed, the last ruling and
the active Effects. "Awaiting input" means the persisted state needs that Actor's move; nothing
records when a model call is running, so nothing here claims one is.
"""

from __future__ import annotations

from collections import Counter
from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.table import Table
from rich.text import Text

from observer.screens.common import (
    BAD,
    GOOD,
    MUTED,
    PENDING,
    PHASE_NAMES,
    PRIVATE,
    PUBLIC,
    TAGS,
    THEN,
    empty_state,
    layout_mode,
    fmt_probability,
    Sized,
    actor_colour,
    actor_label,
    actor_state,
    clip,
    glyphs,
    panel,
    pipeline_strip,
    probability_bar,
)
from observer.viewstate import PIPELINE, FeedLine, ViewState

# --- Live Command ------------------------------------------------------------------------------


def actors_panel(state: ViewState) -> RenderableType:
    def build(width: int, height: int) -> RenderableType:
        if not state.actors:
            return empty_state("No Actors yet.", "The roster appears when the game is created.")
        mode = "wide" if width >= 60 else "normal" if width >= 40 else "narrow"
        budget = state.game.get("primary_arguments_per_actor", 1)
        pending = set(state.pending())
        rows = state.actors
        show_header = height > len(rows)
        # Model ids identify what is being benchmarked, so they get a line of their own under
        # each name whenever there is room, rather than being cut to fit a column.
        stacked = mode != "narrow" and height >= 2 * len(rows) + (1 if show_header else 0)
        fit = height - (1 if show_header else 0)
        show = rows if len(rows) <= fit else rows[: max(1, fit - 1)]
        table = Table(box=None, expand=True, pad_edge=False, padding=(0, 1),
                      show_header=show_header, header_style="dim")
        table.add_column("actor · model" if stacked else "actor", ratio=3, no_wrap=True,
                         overflow="ellipsis")
        if mode != "narrow" and not stacked:
            table.add_column("model", ratio=2, no_wrap=True, overflow="ellipsis", style=MUTED)
        table.add_column("state", no_wrap=True)
        table.add_column("args", no_wrap=True, justify="right")
        if mode == "wide":
            table.add_column("msgs", no_wrap=True, justify="right", style=MUTED)
        for actor in show:
            actor_id = actor["id"]
            label, style = actor_state(state, actor_id)
            name = actor_label(state, actor_id)
            if actor_id in pending:
                name.stylize("reverse", 2)
            if state.actor_filter == actor_id:
                name.append(" ◂" if not state.ui.ascii else " <", style="bold cyan")
            model = actor.get("model") or "model n/a"
            if stacked:
                name.append(f"\n  {model}", style=MUTED)
            cells: list[RenderableType] = [name]
            if mode != "narrow" and not stacked:
                cells.append(model)
            cells.append(Text(label, style=style))
            cells.append(Text(f"{state.arguments_used.get(actor_id, 0)}/{budget}"))
            if mode == "wide":
                stats = state.actor_stats.get(actor_id) or Counter()
                cells.append(f"{stats.get('public', 0)}+{stats.get('private', 0)}")
            table.add_row(*cells)
        if len(show) < len(rows):
            return Group(table, Text(f"+{len(rows) - len(show)} more (screen 2)", style="dim"))
        return table

    return panel(Sized(build, name="actors"), "ACTORS", state,
                 tag=f"{len(state.actors)}" if state.actors else None)


def activity_lines(state: ViewState, width: int, *, strip: bool) -> list[Text]:
    g = glyphs(state)
    lines: list[Text] = []
    if strip:
        lines.append(pipeline_strip(state, width))
    head = Text()
    if state.status == "complete":
        head.append("GAME COMPLETE", style="bold cyan")
        head.append(f"  after {state.turn} turn(s)", style=MUTED)
    elif state.status == "paused":
        head.append("GAME PAUSED", style="bold red")
        head.append(f"  {str(state.pause_reason or '').replace('_', ' ')}", style=BAD)
    elif state.status == "lobby" or not state.events:
        head.append("WAITING FOR THE GAME TO START", style=MUTED)
    else:
        head.append(PHASE_NAMES.get(state.phase, state.phase).upper(), style=PENDING)
        if state.phase == "diplomacy" and state.diplomacy_round:
            head.append(f"  round {state.diplomacy_round + 1}", style=MUTED)
        if state.phase in ("argument", "cons", "reply", "adjudication") \
                and state.turn_arguments:
            head.append(f"  Argument #{state.turn_arguments} this turn", style=MUTED)
    lines.append(head)

    pending = state.pending()
    if state.status == "running":
        who = Text("Awaiting input: ", style=MUTED)
        if pending:
            who.append(clip(", ".join(state.name(a) for a in pending), width - 16, g),
                       style=PENDING)
        else:
            who.append("none recorded", style="dim")
        lines.append(who)

    argument = state.argument()
    if argument is not None and argument.turn == state.turn and state.phase in (
            "cons", "reply", "adjudication") and argument.adjudication is None:
        table = Text("On the table: ", style=MUTED)
        table.append(state.name(argument.owner), style="bold " + actor_colour(state,
                                                                               argument.owner))
        table.append(f" {g.sep} " + clip(argument.action, max(5, width - 20 - len(
            state.name(argument.owner))), g))
        lines.append(table)
        if state.phase == "cons":
            done = len([a for a in argument.eligible if a in argument.done])
            lines.append(Text(f"Con participation: {done}/{len(argument.eligible)} done, "
                              f"{len(argument.cons)} Con(s) in", style=MUTED))

    latest = next((line for line in reversed(state.lines) if line.kind != "system"), None)
    if latest is not None:
        text = Text("Latest: ", style=MUTED)
        text.append(f"{latest.time} ", style="dim")
        sender = clip(latest.who, min(24, max(6, width // 3)), g)
        if sender:
            text.append(sender + " ", style=latest.style or "")
        text.append(clip(latest.text, max(5, width - text.cell_len), g))
        lines.append(text)
    if state.status == "running" and state.phase in THEN:
        lines.append(Text(clip(f"Then: {THEN[state.phase]}", width, g), style="dim italic"))
    return lines


def activity_panel(state: ViewState, *, strip: bool) -> RenderableType:
    def build(width: int, height: int) -> RenderableType:
        return Group(*activity_lines(state, width, strip=strip))

    return panel(Sized(build, name="activity"), "CURRENT ACTIVITY", state)


def pipeline_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        rows = []
        argument = state.argument()
        for index, phase in enumerate(PIPELINE, start=1):
            stage = state.stage_of(phase)
            marker, style = {
                "done": (g.ok, GOOD), "current": (g.now, PENDING),
                "unused": (g.unused, "dim"), "todo": (g.todo, MUTED),
            }[stage]
            row = Text(f"{marker} {index}. {PHASE_NAMES[phase]}", style=style)
            if stage == "current":
                row.append("  ◂ now" if not state.ui.ascii else "  < now", style=PENDING)
                if phase == "cons" and argument is not None:
                    done = len([a for a in argument.eligible if a in argument.done])
                    row.append(f" {done}/{len(argument.eligible)}", style=MUTED)
            elif stage == "unused":
                row.append("  (not used)", style="dim")
            rows.append(row)
        rows.append(Text(f"turn {state.turn} of {state.game.get('max_turns', '?')}",
                         style=MUTED))
        return Group(*rows)

    return panel(Sized(build, name="pipeline"), "TURN PIPELINE", state)


def _tag(line: FeedLine) -> Text:
    label, style = TAGS.get(line.kind, ("SYS", MUTED))
    return Text(f"{label:<4}", style=style)


def feed_body(state: ViewState, *, wrap: bool) -> Sized:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        lines = state.visible_lines()
        if not lines:
            if state.secret_only or state.actor_filter:
                return empty_state("No events match the filter.", "s / a to change the filter")
            return empty_state("No events yet.", "Events appear as the game records them.")
        scroll = min(state.ui.scroll, len(lines) - 1)
        end = len(lines) - scroll
        window = lines[max(0, end - height): end]
        selected = len(window) - 1 if (scroll or state.ui.feed_focus) else None
        narrow = width < 70
        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(no_wrap=True, style="dim")
        table.add_column(no_wrap=True, width=4)
        table.add_column(no_wrap=True, max_width=14 if narrow else 18 if width < 100 else 26,
                         overflow="ellipsis")
        table.add_column(ratio=1, no_wrap=not wrap, overflow="fold" if wrap else "ellipsis")
        for index, line in enumerate(window):
            style = line.style or ""
            who = Text(line.who or "", style=style)
            body = Text(line.text if wrap else clip(line.text, width, g), style=style)
            cells: list[RenderableType] = [line.time[:5] if narrow else line.time,
                                           _tag(line), who, body]
            table.add_row(*cells, style="reverse" if index == selected else None)
        return table

    return Sized(build, name="feed", tail=True)


def feed_panel(state: ViewState, *, wrap: bool = False) -> RenderableType:
    count = len(state.visible_lines())
    tag = f"{count} lines"
    if state.ui.scroll:
        tag += f" · ↑{state.ui.scroll}" if not state.ui.ascii else f" - up {state.ui.scroll}"
    title = "EVENT FEED · focused" if state.ui.feed_focus else "EVENT FEED"
    return panel(feed_body(state, wrap=wrap), title, state, tag=tag)


def ruling_lines(state: ViewState, width: int) -> list[Text]:
    g = glyphs(state)
    ruling = state.ruling
    if ruling is None:
        return [empty_state("No ruling yet.",
                       "The Umpire proposes a probability; the server rolls.")]
    lines = []
    head = actor_label(state, ruling.actor_id)
    head.append(f"  turn {ruling.turn}", style=MUTED)
    lines.append(head)
    lines.append(Text(clip(ruling.action, width, g)))
    if ruling.mode == "probabilistic":
        row = Text(f"p {fmt_probability(ruling.probability)} ")
        row.append_text(probability_bar(ruling.probability, ruling.roll,
                                        max(8, min(30, width - 22)), g))
        row.append(f" roll {fmt_probability(ruling.roll)}")
        lines.append(row)
    else:
        lines.append(Text(f"{ruling.mode.replace('_', ' ')} (no roll)", style=MUTED))
    verdict = Text()
    verdict.append(f"{g.ok} SUCCESS" if ruling.success else f"{g.bad} FAILURE",
                   style="bold green" if ruling.success else "bold red")
    argument = state.arguments.get(ruling.argument_id)
    if argument is not None:
        verdict.append(f"  {len(argument.effect_ids)} effect(s) recorded", style=MUTED)
    lines.append(verdict)
    return lines


def ruling_panel(state: ViewState) -> RenderableType:
    def build(width: int, height: int) -> RenderableType:
        return Group(*ruling_lines(state, width))

    return panel(Sized(build, name="ruling"), "LAST RULING", state)


def effects_list(state: ViewState, width: int, height: int) -> RenderableType:
    g = glyphs(state)
    effects = list(reversed(state.active_effects()))
    if not effects:
        return empty_state("No active Effects recorded.")
    rows = []
    for effect in effects[:height]:
        row = Text()
        row.append(f"T{effect.created_turn} ", style="dim")
        if effect.private:
            row.append("priv ", style=PRIVATE)
        row.append(clip(effect.description, max(5, width - row.cell_len), g))
        rows.append(row)
    if len(effects) > height:
        rows[-1] = Text(f"+{len(effects) - height + 1} more (screen 4)", style="dim")
    return Group(*rows)


def effects_panel(state: ViewState) -> RenderableType:
    return panel(Sized(lambda w, h: effects_list(state, w, h), name="effects"),
                 "ACTIVE EFFECTS", state, tag=str(len(state.active_effects())))


def latest_message_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        messages = [m for m in state.messages
                    if (not state.secret_only or not m.public)
                    and (state.actor_filter is None
                         or state.actor_filter in (m.sender, *m.recipients))]
        if not messages:
            return empty_state("No messages yet.")
        message = messages[-1]
        head = Text(f"{message.time}  ", style="dim")
        head.append_text(actor_label(state, message.sender))
        head.append(f" {g.arrow} ", style="dim")
        if message.public:
            head.append("everyone ", style="bold")
            head.append("[public]", style=PUBLIC)
        else:
            head.append(", ".join(state.name(r) for r in message.recipients) + " ",
                        style="bold")
            head.append("[private]", style=PRIVATE)
        return Group(head, Text(message.text, style=PUBLIC if message.public else PRIVATE))

    return panel(Sized(build, name="message", more="… enter on the feed shows the full text"),
                 "LATEST MESSAGE", state)


def live_screen(state: ViewState, width: int, height: int) -> Layout:
    mode = layout_mode(width)
    layout = Layout(name="body")
    if state.ui.feed_focus:
        layout.update(feed_panel(state, wrap=True))
        return layout
    actors = len(state.actors)
    if mode == "wide":
        top = max(9, min(2 * actors + 4, height // 2))
        rows = [Layout(name="top", size=top)]
        show_message = height - top >= 18 and state.messages
        middle = Layout(name="middle", ratio=1)
        side = Layout(name="side", ratio=2)
        side.split_column(Layout(ruling_panel(state), size=7),
                          Layout(effects_panel(state), ratio=1))
        middle.split_row(Layout(feed_panel(state), ratio=3), side)
        rows.append(middle)
        if show_message:
            rows.append(Layout(latest_message_panel(state), size=6))
        layout.split_column(*rows)
        layout["top"].split_row(
            Layout(actors_panel(state), ratio=5),
            Layout(activity_panel(state, strip=False), ratio=4),
            Layout(pipeline_panel(state), ratio=3),
        )
        return layout
    if mode == "normal":
        top = max(8, min(2 * actors + 4, height // 2 - 1))
        layout.split_column(
            Layout(name="top", size=top),
            Layout(feed_panel(state), ratio=1),
            Layout(name="bottom", size=7 if height >= 24 else 0),
        )
        layout["top"].split_row(Layout(actors_panel(state), ratio=1),
                                Layout(activity_panel(state, strip=True), ratio=1))
        layout["bottom"].split_row(Layout(ruling_panel(state), ratio=3),
                                   Layout(effects_panel(state), ratio=2))
        if height < 24:
            layout["bottom"].visible = False
        return layout
    # Narrow: one column; the position and whose input is due come first.
    parts = [Layout(activity_panel(state, strip=True), size=min(8, max(5, height // 3)))]
    if height >= 22:
        parts.append(Layout(actors_panel(state), size=min(actors + 3, max(5, height // 3))))
    if height >= 28:
        parts.append(Layout(ruling_panel(state), size=6))
    parts.append(Layout(feed_panel(state), ratio=1))
    layout.split_column(*parts)
    return layout
