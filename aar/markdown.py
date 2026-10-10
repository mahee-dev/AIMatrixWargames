"""Render the after-action report as Markdown: the complete, readable record of one game.

The export is an administrative record, so private Messages are included and marked as such,
and the footer says so (#16).
"""

from __future__ import annotations

from aar.model import AfterActionReport, ArgumentView, Ruling

_OUTCOME = {True: "granted", False: "failed", None: "undecided"}


def ruling_text(ruling: Ruling | None) -> str:
    if ruling is None:
        return "not adjudicated"
    outcome = _OUTCOME[ruling.success]
    if ruling.probability is None or ruling.roll is None:
        return f"{ruling.mode} → {outcome}"
    return f"{ruling.mode} · p={ruling.probability:.2f} · roll={ruling.roll:.2f} → {outcome}"


def _argument(report: AfterActionReport, argument: ArgumentView) -> list[str]:
    lines = [
        f"#### {report.actor_name(argument.actor_id)}: {argument.action}",
        "",
        f"*Desired effect:* {argument.desired_effect}",
        "",
    ]
    lines += [f"- Pro: {pro}" for pro in argument.pros]
    lines += [f"- Con ({report.actor_name(c.actor_id)}): {c.reason}" for c in argument.cons]
    lines += [f"- Reply: {point}" for point in argument.reply_points]
    lines += ["", f"**Ruling:** {ruling_text(argument.ruling)}"]
    if argument.ruling is not None and argument.ruling.rationale:
        lines += ["", f"> {argument.ruling.rationale}"]
    if argument.effects:
        lines += [""] + [f"- Effect: {effect}" for effect in argument.effects]
    return lines + [""]


def _closing_line(report: AfterActionReport, argument_id: str) -> str:
    argument = report.argument(argument_id)
    return (
        f"- turn {argument.turn}, {report.actor_name(argument.actor_id)}: {argument.action} "
        f"({ruling_text(argument.ruling)})"
    )


def render_markdown(report: AfterActionReport) -> str:
    out = [f"# After-action report: {report.scenario_name}", ""]
    out.append(
        f"Scenario `{report.scenario_id}` v{report.scenario_version} · "
        f"{report.turns_played} of {report.max_turns} turns · seed {report.seed} · "
        f"status {report.status}"
    )
    if report.scenario_summary:
        out += ["", report.scenario_summary.strip()]
    out += ["", "| Actor | Model |", "|---|---|"]
    out += [f"| {a.name} | `{a.model}` |" for a in report.actors]
    out += ["", "### Objectives (private)", ""]
    for actor in report.actors:
        out.append(f"- **{actor.name}**")
        out += [f"  - {objective}" for objective in actor.objectives] or ["  - none stated"]

    for turn in report.turns:
        out += ["", f"## Turn {turn.number}", "", "### Messages", ""]
        for m in turn.messages:
            recipients = ", ".join(report.actor_name(r) for r in m.recipients)
            tag = "**[public]**" if m.public else f"**[private → {recipients}]**"
            out.append(f"- {tag} {report.actor_name(m.sender_actor_id)}: {m.text}")
        if not turn.messages:
            out.append("- none")
        out += ["", "### Arguments", ""]
        for argument in turn.arguments:
            out += _argument(report, argument)
        if not turn.arguments:
            out += ["- none", ""]
        if turn.consequence_effects:
            out += ["### Consequences", ""] + [f"- {e}" for e in turn.consequence_effects]

    h = report.highlights
    out += ["", "## Highlights", ""]
    for label, argument_id in (
        ("Long shot", h.long_shot), ("Bust", h.bust), ("Most contested", h.most_contested)
    ):
        if argument_id is not None:
            argument = report.argument(argument_id)
            out.append(
                f"- **{label}:** {report.actor_name(argument.actor_id)}, turn {argument.turn}: "
                f"{argument.action} ({ruling_text(argument.ruling)})"
            )
    if h.swing_turn is not None:
        out.append(f"- **Swing turn:** {h.swing_turn}")
    out += [
        "",
        "| Actor | Granted | Failed | No roll | Cons raised | Public msgs | Private msgs |",
        "|---|---|---|---|---|---|---|",
    ]
    for actor in report.actors:
        t = report.tallies[actor.id]
        out.append(
            f"| {actor.name} | {t.granted} | {t.failed} | {t.unrolled} | {t.cons_raised} | "
            f"{t.public_messages} | {t.private_messages} |"
        )

    out += ["", "## Rolled and unrolled Arguments", "", "### Rolled", ""]
    out += [_closing_line(report, i) for i in report.rolled] or ["- none"]
    out += ["", "### Decided without a roll", ""]
    out += [_closing_line(report, i) for i in report.unrolled] or ["- none"]
    out += [
        "",
        "---",
        "",
        f"*Generated from export format v{report.export_format_version} of game "
        f"`{report.game_id}`. This report includes private material (objectives and private "
        "Messages) from the administrative export.*",
        "",
    ]
    return "\n".join(out)
