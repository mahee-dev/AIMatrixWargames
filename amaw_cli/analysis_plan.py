"""``amaw analysis-plan``: validate a predeclared plan or verify which games sealed it."""

from __future__ import annotations

import json
from pathlib import Path

import typer

from amaw_cli._app import app


analysis_plan_app = typer.Typer(
    help="Validate a predeclared analysis plan or verify which games sealed it (I-047).",
    no_args_is_help=True,
)
app.add_typer(analysis_plan_app, name="analysis-plan")


@analysis_plan_app.command("validate")
def analysis_plan_validate(plan: Path) -> None:
    """Validate an analysis-plan-v1 or -v2 file and print its identity hash."""
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan

    try:
        loaded = load_analysis_plan(plan)
    except AnalysisPlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    typer.echo(f"{loaded.plan_id} {loaded.sha256}")


@analysis_plan_app.command("verify")
def analysis_plan_verify(plan: Path, exports: list[Path]) -> None:
    """Classify each game export as predeclared / post_hoc_plan / unplanned against the plan.

    Exit status 0 only when every export sealed exactly this plan before execution.
    """
    from benchmark.analysis_plan import AnalysisPlanError, load_analysis_plan, verify_registration

    try:
        loaded = load_analysis_plan(plan)
    except AnalysisPlanError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from exc
    documents = []
    for path in exports:
        try:
            documents.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, ValueError) as exc:
            typer.echo(f"error: export {path} could not be read: {exc}", err=True)
            raise typer.Exit(code=2) from exc
    report = verify_registration(loaded, documents)
    typer.echo(json.dumps(report.to_dict(), indent=2, sort_keys=True))
    raise typer.Exit(code=0 if report.all_predeclared else 1)
