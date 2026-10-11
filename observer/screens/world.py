"""Screen 4, World State: Effects, the timeline, the scenario and event-derived counts.

AMAW's world state is its Effects. The scenario exposes no numeric world variables, so the
figures here are counts from the event log and say so; no gauge, map or score is invented.
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
    PHASE_NAMES,
    PRIVATE,
    empty_state,
    layout_mode,
    Sized,
    actor_label,
    clip,
    glyphs,
    panel,
    visibility_label,
)
from observer.viewstate import EffectView, ViewState

# --- World State -------------------------------------------------------------------------------


def world_effects(state: ViewState) -> list[EffectView]:
    """Active Effects (newest first), then ended ones (most recently ended first)."""
    active = sorted(state.active_effects(), key=lambda e: (e.created_turn, e.created_seq),
                    reverse=True)
    return active + list(reversed(state.ended_effects()))


def effect_source(state: ViewState, effect: EffectView) -> str:
    if effect.source == "argument":
        argument = state.arguments.get(effect.source_argument_id or "")
        return f"Argument: {state.name(argument.owner)}" if argument else "Argument"
    if effect.source == "consequence":
        return f"consequences T{effect.source_consequence_turn}"
    if effect.source == "inject":
        return f"inject {effect.source_inject_id}"
    return "scenario setup"


def effects_table_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        effects = world_effects(state)
        if not effects:
            return empty_state("No Effects recorded.",
                          "Effects are AMAW's world state: setup facts and adjudicated outcomes.")
        selected = min(state.ui.effect_index, len(effects) - 1)
        narrow = width < 70
        table = Table(box=None, expand=True, pad_edge=False, padding=(0, 1),
                      header_style="dim")
        table.add_column("T", no_wrap=True, justify="right", style="dim")
        table.add_column("effect", ratio=1, no_wrap=True, overflow="ellipsis")
        table.add_column("visibility", no_wrap=True, max_width=24, overflow="ellipsis")
        if not narrow:
            table.add_column("source", no_wrap=True, max_width=24, overflow="ellipsis",
                             style=MUTED)
            table.add_column("state", no_wrap=True)
        start = max(0, min(selected - (height - 2) // 2, len(effects) - (height - 1)))
        for index, effect in enumerate(effects[start:start + max(1, height - 1)],
                                       start=start):
            cells: list[RenderableType] = [
                str(effect.created_turn),
                Text(effect.description, style="" if effect.active else "dim"),
                visibility_label(state, effect.private, effect.visible_to, 24),
            ]
            if not narrow:
                cells.append(effect_source(state, effect))
                if effect.active:
                    expires = f" until T{effect.expires_turn}" if effect.expires_turn else ""
                    cells.append(Text(f"active{expires}", style=GOOD))
                else:
                    cells.append(Text(f"ended T{effect.ended_turn} {effect.ended_reason or ''}",
                                      style="dim"))
            table.add_row(*cells, style="reverse" if index == selected else None)
        return table

    active = len(state.active_effects())
    ended = len(state.effects) - active
    return panel(Sized(build, name="effects"), "WORLD EFFECTS", state,
                 tag=f"{active} active · {ended} ended")


TIMELINE_DOTS = {
    "turn": "bold cyan", "diplomacy": PRIVATE, "argument": "yellow", "ruling": "bold",
    "effect": GOOD, "effect_end": "dim", "paused": BAD, "system": MUTED,
}


def timeline_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        if not state.timeline:
            return empty_state("Nothing recorded yet.")
        table = Table.grid(padding=(0, 1), expand=True)
        table.add_column(no_wrap=True)
        table.add_column(no_wrap=True, style="dim")
        table.add_column(no_wrap=True, style="dim")
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        for entry in list(state.timeline)[-height:]:
            colour = TIMELINE_DOTS.get(entry.kind, MUTED)
            if entry.kind == "ruling":
                colour = entry.style
            table.add_row(Text(g.dot, style=colour), entry.time, f"T{entry.turn}",
                          Text(clip(entry.text, width, g), style=entry.style))
        return table

    return panel(Sized(build, name="timeline", tail=True), "TIMELINE", state)


def scenario_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        game = state.game
        grid = Table.grid(padding=(0, 1), expand=True)
        grid.add_column(style=MUTED, no_wrap=True)
        grid.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        grid.add_row("Scenario", f"{game.get('scenario_id', '?')} "
                                 f"v{game.get('scenario_version') or '?'}")
        grid.add_row("Game", str(game.get("id", "?"))[:8])
        position = f"turn {state.turn}/{game.get('max_turns', '?')} · " + (
            state.status if state.phase == state.status
            else f"{PHASE_NAMES.get(state.phase, state.phase)} · {state.status}")
        grid.add_row("Position", position)
        if state.turn_order:
            grid.add_row("Turn order", clip(" › ".join(state.name(a) for a in state.turn_order),
                                            width - 12, g))
        grid.add_row("Arguments", f"{game.get('primary_arguments_per_actor', '?')} per Actor "
                                  "per turn")
        grid.add_row("Events", f"{state.events} folded · cursor {state.cursor}")
        for actor in state.actors:
            grid.add_row(clip(actor["name"], 16, g), actor.get("model") or "model n/a")
        totals = state.totals
        if state.totals_known():
            tokens = totals.get("input_tokens", 0) + totals.get("output_tokens", 0)
            label = "Run so far" if state.live else "Whole run"
            grid.add_row(label, f"{totals.get('model_calls', 0):,} calls · {tokens:,} tok · "
                                f"${totals.get('cost_usd', 0) or 0:.2f}")
        elif totals:
            grid.add_row("Run totals", Text("shown when the replay reaches the end",
                                            style="dim"))
        return grid

    return panel(Sized(build, name="scenario"), "SCENARIO", state)


def stats_panel(state: ViewState) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        if not state.events:
            return empty_state("No events yet.")
        actors = Table(box=None, expand=True, pad_edge=False, padding=(0, 1),
                       header_style="dim")
        actors.add_column("actor", ratio=1, no_wrap=True, overflow="ellipsis")
        for name in ("pub", "priv", "args", g.ok, g.bad, "cons"):
            actors.add_column(name, justify="right", no_wrap=True)
        for actor in state.actor_ids():
            stats = state.actor_stats.get(actor) or Counter()
            actors.add_row(actor_label(state, actor), *(
                str(stats.get(key, 0)) for key in
                ("public", "private", "arguments", "successes", "failures", "cons")))
        turns = Table(box=None, expand=True, pad_edge=False, padding=(0, 1),
                      header_style="dim")
        for name in ("turn", "msgs", "args", g.ok, g.bad, "mean p", "eff +/-"):
            turns.add_column(name, justify="right", no_wrap=True)
        for turn in sorted(state.turn_stats)[-max(1, height - len(state.actor_ids()) - 6):]:
            stats = state.turn_stats[turn]
            probabilistic = stats.get("probabilistic", 0)
            mean = (f"{state.probability_sums.get(turn, 0) / probabilistic:.2f}"
                    if probabilistic else "—")
            turns.add_row(
                str(turn), str(stats.get("public", 0) + stats.get("private", 0)),
                str(stats.get("arguments", 0)), str(stats.get("successes", 0)),
                str(stats.get("failures", 0)), mean,
                f"{stats.get('effects_created', 0)}/{stats.get('effects_ended', 0)}",
            )
        note = Text("Counted from the event log. The scenario records no numeric world "
                    "variables; Effects are its world state.", style="dim italic")
        return Group(actors, Text(""), turns, note)

    return panel(Sized(build, name="stats"), "ACTIVITY (event-derived)", state)


def world_screen(state: ViewState, width: int, height: int) -> Layout:
    layout = Layout(name="body")
    mode = layout_mode(width)
    scenario_h = min(height // 2, 9 + len(state.actors))
    if mode == "narrow":
        layout.split_column(Layout(scenario_panel(state), size=scenario_h),
                            Layout(effects_table_panel(state), ratio=1),
                            Layout(timeline_panel(state), ratio=1))
        return layout
    right = Layout(name="right", ratio=2)
    right.split_column(Layout(scenario_panel(state), size=scenario_h),
                       Layout(stats_panel(state), ratio=1))
    left = Layout(name="left", ratio=3)
    left.split_column(Layout(effects_table_panel(state), ratio=1),
                      Layout(timeline_panel(state), ratio=1))
    layout.split_row(left, right)
    return layout
