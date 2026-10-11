"""The detail and help overlays: any selected item in full, and the key list."""

from __future__ import annotations

from collections import Counter
import json
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.table import Table
from rich.text import Text

from observer.screens.adjudication import effect_row, selected_argument
from observer.screens.common import (
    MUTED,
    PRIVATE,
    PUBLIC,
    TITLE,
    empty_state,
    Sized,
    actor_label,
    glyphs,
    panel,
)
from observer.screens.world import effect_source, world_effects
from observer.viewstate import ArgumentView, EffectView, ViewState

# --- overlays ----------------------------------------------------------------------------------


def _field_lines(name: str, value: Any) -> list[Text]:
    if isinstance(value, (dict, list)):
        if not value:
            return [Text(f"  {name}: {json.dumps(value)}", style=MUTED)]
        body = json.dumps(value, indent=2, ensure_ascii=False, default=str)
        return [Text(f"  {name}:", style=MUTED)] + [Text(f"    {row}") for row in
                                                     body.splitlines()]
    return [Text(f"  {name}: ", style=MUTED).append(str(value), style="")]


def item_detail(state: ViewState, item: dict[str, Any]) -> list[Text]:
    """Everything the feed recorded for one event, with full text."""
    lines = [
        Text(f"event {item['seq']} · {item['type']} · turn {item['turn']} · "
             f"{item['created_at']}", style="bold"),
    ]
    if item.get("actor_id"):
        lines.append(Text("actor: ", style=MUTED).append(state.name(item["actor_id"])))
    for kind, ref in item["refs"].items():
        lines.append(Text(""))
        lines.append(Text(kind.upper(), style=TITLE))
        rows = ref if isinstance(ref, list) else [ref]
        for row in rows:
            for key, value in row.items():
                if kind == "message" and key == "recipients":
                    value = [state.name(r) for r in value]
                if kind in ("effect", "effects") and key == "active":
                    continue  # read now, not at the event; lifecycle is on screen 4
                if kind == "argument" and key == "status":
                    continue  # read now, not at the event
                lines += _field_lines(key, value)
    if item["payload"]:
        lines.append(Text(""))
        lines.append(Text("PAYLOAD", style=TITLE))
        for key, value in item["payload"].items():
            lines += _field_lines(key, value)
    return lines


def argument_detail(state: ViewState, argument: ArgumentView) -> list[Text]:
    g = glyphs(state)
    lines = [
        Text(f"Argument by {state.name(argument.owner)} · turn {argument.turn} · "
             f"{argument.time}", style="bold"),
        Text(""), Text("ACTION", style=TITLE), Text(argument.action),
        Text(""), Text("DESIRED EFFECT (requested)", style=TITLE), Text(argument.desired_effect),
    ]
    if argument.pros:
        lines += [Text(""), Text("PROS", style=TITLE)]
        lines += [Text(f"{g.sep} {pro}") for pro in argument.pros]
    lines += [Text(""), Text("CONS", style=TITLE)]
    for actor in argument.eligible:
        lines.append(Text(f"{state.name(actor)}: {argument.con_status(actor)}", style="bold"))
        lines += [Text(f"  {g.sep} {c.reason}", style="yellow")
                  for c in argument.cons if c.actor_id == actor]
    if argument.reply is not None:
        lines += [Text(""), Text("OWNER REPLY", style=TITLE)]
        points = argument.reply.get("points") or []
        lines += [Text(f"{g.sep} {p.get('text', p)}") for p in points] or [Text("passed")]
    record = argument.adjudication
    if record is not None:
        lines += [Text(""), Text("RULING", style=TITLE)]
        for key in ("mode", "probability", "roll", "success", "rationale", "success_effects",
                    "failure_effects", "supersedes_effect_ids"):
            lines += _field_lines(key, record.get(key))
    lines += [Text(""), Text("RECORDED EFFECTS", style=TITLE)]
    for effect_id in argument.effect_ids:
        effect = state.effects.get(effect_id)
        if effect is not None:
            lines.append(effect_row(state, effect, 200))
    return lines


def actor_detail(state: ViewState, actor_id: str) -> list[Text]:
    g = glyphs(state)
    stats = state.actor_stats.get(actor_id) or Counter()
    lines = [
        actor_label(state, actor_id),
        Text(f"model: {state.model(actor_id) or 'n/a'}", style=MUTED),
        Text(f"public {stats.get('public', 0)} · private sent {stats.get('private', 0)} · "
             f"received {stats.get('received', 0)} · arguments {stats.get('arguments', 0)} · "
             f"cons {stats.get('cons', 0)}", style=MUTED),
        Text(""), Text("MESSAGES (full text)", style=TITLE),
    ]
    messages = [m for m in state.messages if actor_id in (m.sender, *m.recipients)]
    if not messages:
        lines.append(Text("none", style="dim"))
    for message in messages:
        head = Text(f"{message.time} T{message.turn} ", style="dim")
        head.append(state.name(message.sender), style="bold")
        head.append(f" {g.arrow} ")
        head.append("everyone [public]" if message.public else
                    ", ".join(state.name(r) for r in message.recipients) + " [private]",
                    style=PUBLIC if message.public else PRIVATE)
        lines += [head, Text(message.text), Text("")]
    return lines


def effect_detail(state: ViewState, effect: EffectView) -> list[Text]:
    lines = [Text(effect.description, style="bold"), Text("")]
    rows = [
        ("state", "active" if effect.active else f"ended ({effect.ended_reason or '?'}) "
                                                 f"in turn {effect.ended_turn}"),
        ("visibility", effect.visibility),
        ("visible to", ", ".join(state.name(a) for a in effect.visible_to) or "everyone"),
        ("created turn", effect.created_turn),
        ("expires turn", effect.expires_turn if effect.expires_turn is not None else "—"),
        ("source", effect_source(state, effect)),
        ("id", effect.id),
    ]
    if effect.superseded_by:
        other = state.effects.get(effect.superseded_by)
        rows.append(("superseded by", other.description if other else effect.superseded_by))
    if effect.supersedes:
        rows.append(("supersedes", ", ".join(
            state.effects[x].description if x in state.effects else x
            for x in effect.supersedes)))
    for key, value in rows:
        lines += _field_lines(key, value)
    argument = state.arguments.get(effect.source_argument_id or "")
    if argument is not None:
        lines += [Text(""), Text("SOURCE ARGUMENT", style=TITLE), Text(argument.action)]
    return lines


def detail_content(state: ViewState) -> tuple[str, list[Text]]:
    ui = state.ui
    if ui.screen == "adjudication":
        argument = selected_argument(state)
        if argument is not None:
            return "ARGUMENT IN FULL", argument_detail(state, argument)
    elif ui.screen == "network":
        ids = state.actor_ids()
        if ids:
            return "ACTOR COMMUNICATIONS", actor_detail(state, ids[ui.actor_index % len(ids)])
    elif ui.screen == "world":
        effects = world_effects(state)
        if effects:
            return "EFFECT", effect_detail(state, effects[min(ui.effect_index,
                                                              len(effects) - 1)])
    else:
        lines = state.visible_lines()
        if lines:
            line = lines[max(0, len(lines) - 1 - ui.scroll)]
            item = state.items.get(line.seq)
            if item is not None:
                return "EVENT", item_detail(state, item)
            return "EVENT", [Text(f"{line.time} {line.who}", style="bold"), Text(line.text)]
    return "DETAIL", [empty_state("Nothing selected.")]


def detail_overlay(state: ViewState) -> RenderableType:
    title, lines = detail_content(state)

    def build(width: int, height: int) -> RenderableType:
        rendered = Console(width=width, color_system=None)
        flat: list[Text] = []
        for line in lines:
            flat += list(line.wrap(rendered, width))
        state.ui.detail_scroll = max(0, min(state.ui.detail_scroll, len(flat) - height))
        return Group(*flat[state.ui.detail_scroll: state.ui.detail_scroll + height])

    return panel(Sized(build, name="detail"), f"DETAIL · {title}", state,
                 tag="esc closes", border="cyan")


HELP = [
    ("1 2 3 4", "Live Command · Actor Network · Adjudication · World State"),
    ("f", "focus the event feed (wrapped, full width); f again to leave"),
    ("a", "cycle the Actor filter"),
    ("s", "show private material only"),
    ("p", "pause or resume drawing; the game itself is never paused"),
    ("↑ ↓", "scroll the feed, or choose the Argument, Actor or Effect"),
    ("g", "back to the newest line and the latest Argument"),
    ("enter", "inspect the selected item in full"),
    ("esc", "close a detail view or this help"),
    ("space n", "next event when replaying"),
    ("q", "quit the viewer; a running game carries on"),
]


def help_overlay(state: ViewState) -> RenderableType:
    table = Table(box=None, show_header=False, padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for key, action in HELP:
        table.add_row(key, action)
    note = Text("\nThe viewer only reads the game database. It shows every Actor's private "
                "material: it is an operator view, never an Actor's view.", style="dim italic")
    return panel(Group(table, note), "KEYS", state, border="cyan")
