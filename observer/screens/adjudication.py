"""Screen 3, Adjudication: one Argument from submission to recorded Effects.

The owner's desired effect and the Umpire's proposed branches are labelled as requests and
proposals. Only Effects the engine recorded are world state. The Umpire proposes a mode and a
probability; the server draws the roll (INV-RNG-001, agents.md §26).
"""

from __future__ import annotations

from rich.console import Group, RenderableType
from rich.layout import Layout
from rich.text import Text

from observer.screens.common import (
    BAD,
    GOOD,
    MUTED,
    PENDING,
    empty_state,
    layout_mode,
    fmt_probability,
    Sized,
    actor_colour,
    actor_label,
    clip,
    glyphs,
    panel,
    probability_bar,
    visibility_label,
)
from observer.viewstate import ArgumentView, EffectView, ViewState

# --- Adjudication ------------------------------------------------------------------------------


def selected_argument(state: ViewState) -> ArgumentView | None:
    if state.ui.argument_id in state.arguments:
        return state.arguments[state.ui.argument_id]  # type: ignore[index]
    if state.arguments:
        return next(reversed(state.arguments.values()))
    return None


def _stage_label(argument: ArgumentView) -> tuple[str, str]:
    return {
        "cons": ("OPEN FOR CONS", PENDING),
        "reply": ("AWAITING REPLY", PENDING),
        "adjudication": ("AWAITING RULING", PENDING),
        "resolved": ("RESOLVED", GOOD),
    }.get(argument.stage, (argument.stage.upper(), MUTED))


def lifecycle_strip(state: ViewState, argument: ArgumentView, width: int) -> Text:
    g = glyphs(state)
    record = argument.adjudication
    done = len([a for a in argument.eligible if a in argument.done])
    reply_used = argument.revealed or argument.reply is not None
    steps: list[tuple[str, str]] = [("submitted", "done")]
    steps.append((f"cons {done}/{len(argument.eligible)}",
                  "current" if argument.stage == "cons" else "done"))
    if argument.reply is not None:
        steps.append(("reply", "done"))
    elif argument.stage == "reply":
        steps.append(("reply", "current"))
    else:
        steps.append(("reply", "todo" if reply_used else "unused"))
    if record is not None:
        steps.append(("umpire", "done"))
        if record["mode"] == "probabilistic":
            steps.append((f"roll {fmt_probability(record['roll'])}", "done"))
        else:
            steps.append(("no roll", "unused"))
        steps.append((f"effects {len(argument.effect_ids)}", "done"))
    else:
        steps.append(("umpire", "current" if argument.stage == "adjudication" else "todo"))
        steps.append(("roll", "todo"))
        steps.append(("effects", "todo"))
    text = Text()
    short = width < 90
    for index, (label, stage) in enumerate(steps):
        marker, style = {
            "done": (g.ok, GOOD), "current": (g.now, PENDING),
            "unused": (g.unused, "dim"), "todo": (g.todo, MUTED),
        }[stage]
        if index:
            text.append(f" {g.sep} ", style="dim")
        number = "" if short else g.numbers[index] + " "
        text.append(f"{number}{label.upper()} {marker}", style=style)
    return text


def argument_panel(state: ViewState, argument: ArgumentView) -> RenderableType:
    g = glyphs(state)
    label, style = _stage_label(argument)

    def build(width: int, height: int) -> RenderableType:
        owner = actor_label(state, argument.owner)
        owner.append(f"  {state.model(argument.owner) or ''}", style=MUTED)
        rows: list[RenderableType] = [
            owner,
            Text(f"turn {argument.turn} · submitted {argument.time}", style="dim"),
            Text("Action", style="bold"),
            Text(argument.action),
            Text("Desired effect (requested, not yet world state)", style="bold"),
            Text(argument.desired_effect, style="italic"),
        ]
        if argument.pros:
            rows.append(Text("Pros", style="bold"))
            rows += [Text(f"{g.sep} {pro}") for pro in argument.pros]
        return Group(*rows)

    return panel(Sized(build, name="argument", more="… enter shows the full Argument"),
                 "ARGUMENT", state, tag=label, tag_style=style)


def cons_panel(state: ViewState, argument: ArgumentView) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        rows: list[RenderableType] = []
        if not argument.eligible:
            rows.append(empty_state("No Actor was eligible to Con this Argument."))
        badges = {
            "con": ("CON", "yellow"), "passed": ("PASSED · no Con", MUTED),
            "pending": ("PENDING", PENDING), "no response": ("NO RESPONSE", BAD),
        }
        for actor in argument.eligible:
            status = argument.con_status(actor)
            head = actor_label(state, actor)
            badge, style = badges.get(status, (status.upper(), MUTED))
            head.append(f"  {badge}", style=style)
            rows.append(head)
            for con in (c for c in argument.cons if c.actor_id == actor):
                rows.append(Text(f"  {g.sep} {con.reason}", style="yellow"))
        excluded = [a for a in state.actor_ids()
                    if a != argument.owner and a not in argument.eligible]
        if excluded and argument.sampled:
            rows.append(Text("not selected to challenge: " + ", ".join(
                state.name(a) for a in excluded), style="dim"))
        if argument.reply is not None:
            points = argument.reply.get("points") or []
            rows.append(Text(""))
            head = Text("OWNER REPLY ", style="bold")
            head.append("passed" if not points else f"{len(points)} point(s)", style=MUTED)
            rows.append(head)
            for point in points:
                rows.append(Text(f"  {g.sep} {point.get('text', point)}", style="cyan"))
        elif argument.revealed:
            rows.append(Text("Cons revealed to the owner; reply pending", style=PENDING))
        return Group(*rows)

    done = len([a for a in argument.eligible if a in argument.done])
    return panel(Sized(build, name="cons", more="… enter shows every Con in full"),
                 "COUNTERARGUMENTS", state,
                 tag=f"{len(argument.cons)} con(s) · {done}/{len(argument.eligible)} done")


def umpire_panel(state: ViewState, argument: ArgumentView) -> RenderableType:
    g = glyphs(state)
    record = argument.adjudication

    def build(width: int, height: int) -> RenderableType:
        if record is None:
            waiting = {
                "cons": "waiting for Con participation to close",
                "reply": "waiting for the owner's reply",
                "adjudication": "waiting for the Umpire's proposal",
            }.get(argument.stage, "waiting")
            if state.status == "paused":
                waiting = f"game paused ({state.pause_reason or '?'}) before a ruling"
            return Group(
                Text(f"Status: {waiting}", style=PENDING),
                Text("Probability: —   Server roll: —   Outcome: —", style=MUTED),
                Text("The Umpire proposes; the server rolls and records the outcome.",
                     style="dim italic"),
            )
        rows: list[RenderableType] = []
        mode = record["mode"]
        rows.append(Text(f"Mode: {mode.replace('_', ' ')}", style="bold"))
        if mode == "probabilistic":
            rows.append(Text(f"Umpire probability  {fmt_probability(record['probability'])}"))
            rows.append(Text(f"Server roll         {fmt_probability(record['roll'])}"))
            bar = Text("0 ")
            bar.append_text(probability_bar(record["probability"], record["roll"],
                                            max(10, min(40, width - 6)), g))
            bar.append(" 1")
            rows.append(bar)
            rule = "≤" if not state.ui.ascii else "<="
            rows.append(Text(f"success when roll {rule} probability (server RNG)",
                             style="dim italic"))
        else:
            rows.append(Text("No roll: the Umpire's mode decides the outcome.", style=MUTED))
        verdict = Text("Outcome: ")
        verdict.append(f"{g.ok} SUCCESS" if record["success"] else f"{g.bad} FAILURE",
                       style="bold green" if record["success"] else "bold red")
        rows.append(verdict)
        if record.get("rationale"):
            rows.append(Text("Rationale", style="bold"))
            rows.append(Text(record["rationale"], style=MUTED))
        return Group(*rows)

    tag, style = ("RULED", GOOD) if record is not None else ("PENDING", PENDING)
    return panel(Sized(build, name="umpire", more="… enter shows the full ruling"),
                 "UMPIRE RULING", state, tag=tag, tag_style=style)


def effect_row(state: ViewState, effect: EffectView, width: int) -> Text:
    g = glyphs(state)
    row = Text()
    row.append(f"{g.ok} " if effect.active else f"{g.unused} ",
               style=GOOD if effect.active else "dim")
    tail = Text("  ")
    tail.append_text(visibility_label(state, effect.private, effect.visible_to, 22))
    if not effect.active:
        tail.append(f"  ended: {effect.ended_reason or '?'}", style="dim")
    row.append(clip(effect.description, max(10, width - row.cell_len - tail.cell_len), g),
               style="" if effect.active else "dim")
    row.append_text(tail)
    return row


def argument_effects_panel(state: ViewState, argument: ArgumentView) -> RenderableType:
    g = glyphs(state)
    record = argument.adjudication

    def build(width: int, height: int) -> RenderableType:
        rows: list[RenderableType] = [
            Text("REQUESTED by the owner, not world state", style="bold dim"),
            Text(f"  {argument.desired_effect}", style="italic dim"),
        ]
        if record is not None:
            for branch, key in (("success", "success_effects"), ("failure", "failure_effects")):
                applied = bool(record["success"]) == (branch == "success")
                head = Text(f"UMPIRE PROPOSED, {branch} branch", style="bold dim")
                if applied:
                    head.append("  applied", style=GOOD)
                rows.append(head)
                proposals = record.get(key) or []
                if not proposals:
                    rows.append(Text("  (none)", style="dim"))
                rows += [Text(f"  {g.sep} {text}", style="" if applied else "dim")
                         for text in proposals]
        rows.append(Text("RECORDED BY THE ENGINE", style="bold green"))
        effects = [state.effects[e] for e in argument.effect_ids if e in state.effects]
        if effects:
            rows += [effect_row(state, effect, width) for effect in effects]
        elif record is None:
            rows.append(Text("  nothing yet: Effects are recorded after the ruling",
                             style="dim"))
        else:
            rows.append(Text("  no Effect recorded for this Argument", style="dim"))
        if record is not None and record.get("supersedes_effect_ids"):
            ended = [state.effects.get(x) for x in record["supersedes_effect_ids"]]
            rows.append(Text("SUPERSEDES", style="bold dim"))
            rows += [Text(f"  {g.unused} {clip(e.description if e else x, width - 4, g)}",
                          style="dim")
                     for e, x in zip(ended, record["supersedes_effect_ids"], strict=False)]
        return Group(*rows)

    return panel(Sized(build, name="effects", more="… enter shows every Effect in full"),
                 "EFFECTS", state, tag=f"{len(argument.effect_ids)} recorded")


def arguments_list_panel(state: ViewState, current: ArgumentView) -> RenderableType:
    g = glyphs(state)

    def build(width: int, height: int) -> RenderableType:
        ids = list(state.arguments)
        index = ids.index(current.id)
        start = max(0, min(index - height // 2, len(ids) - height))
        rows = []
        for argument in list(state.arguments.values())[start:start + height]:
            record = argument.adjudication
            mark = g.now if argument.id == current.id else " "
            outcome, style = (g.ok, GOOD) if record and record["success"] else (
                (g.bad, BAD) if record else (g.todo, PENDING))
            row = Text(f"{mark} T{argument.turn} ", style="bold" if mark != " " else "dim")
            row.append(f"{outcome} ", style=style)
            row.append(clip(state.name(argument.owner), width - 9, g),
                       style=actor_colour(state, argument.owner))
            if argument.id == current.id:
                row.stylize("reverse")
            rows.append(row)
        return Group(*rows)

    return panel(Sized(build, name="arguments"), "ARGUMENTS", state,
                 tag=f"{list(state.arguments).index(current.id) + 1}/{len(state.arguments)}")


def adjudication_screen(state: ViewState, width: int, height: int) -> Layout:
    layout = Layout(name="body")
    argument = selected_argument(state)
    if argument is None:
        layout.update(panel(empty_state(
            "No Argument submitted yet.",
            "Arguments open after diplomacy closes; each is Conned, then adjudicated."),
            "ADJUDICATION", state))
        return layout
    mode = layout_mode(width)
    position = list(state.arguments).index(argument.id) + 1
    steps = lifecycle_strip(state, argument, width)
    strip_h = 3 if steps.cell_len <= width - 4 else 4
    strip = panel(steps, "LIFECYCLE", state,
                  tag=f"Argument {position}/{len(state.arguments)} · turn {argument.turn}")
    if mode == "narrow":
        layout.split_column(
            Layout(strip, size=strip_h),
            Layout(argument_panel(state, argument), ratio=2),
            Layout(umpire_panel(state, argument), size=9),
            Layout(cons_panel(state, argument), ratio=2),
            Layout(argument_effects_panel(state, argument), ratio=2),
        )
        return layout
    left = Layout(name="left", ratio=3)
    left.split_column(Layout(argument_panel(state, argument), ratio=1),
                      Layout(umpire_panel(state, argument), size=min(12, height // 2)))
    middle = Layout(name="middle", ratio=3)
    middle.split_column(Layout(cons_panel(state, argument), ratio=1),
                        Layout(argument_effects_panel(state, argument), ratio=1))
    row = Layout(name="row", ratio=1)
    columns = [left, middle]
    if mode == "wide":
        columns.append(Layout(arguments_list_panel(state, argument), ratio=1))
    row.split_row(*columns)
    layout.split_column(Layout(strip, size=strip_h), row)
    return layout
