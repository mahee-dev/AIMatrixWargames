"""``amaw report``, ``watch`` and ``publish``: reading a finished or live game."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
import uuid
from pathlib import Path

import typer

from amaw_cli._app import app
from amaw_cli.exports import _build_export, _default_export_path

if TYPE_CHECKING:  # typing only: the agent stack is imported lazily inside the commands
    from aar.model import AfterActionReport
    from aar.story import Story
    from app.config import Settings


@app.command()
def report(
    source: str = typer.Argument(..., help="A game id, or the path of an export JSON file."),
    out: Path | None = typer.Option(
        None, "--out", help="Output directory. Default: <export dir>/<game id>-aar/."
    ),
    card: bool = typer.Option(
        False, "--card", help="Also write the story cards: card-1.png … card-5.png and card.mp4."
    ),
    no_video: bool = typer.Option(False, "--no-video", help="With --card, skip card.mp4."),
    narrate: bool = typer.Option(
        False, "--narrate", help="Have a model write the story (needs OPENROUTER_API_KEY)."
    ),
    narrate_model: str | None = typer.Option(
        None, "--narrate-model", help="Model for --narrate. Default: the configured Umpire model."
    ),
    renarrate: bool = typer.Option(
        False, "--renarrate", help="With --narrate, ignore a saved narration.json."
    ),
) -> None:
    """Write an after-action report (report.md) for a game, from its export alone (TRD §34).

    A game id is exported from the configured database first, beside it in ``exports/``, so the
    report can always be rebuilt from the file it was made from.
    """
    from aar.markdown import render_markdown
    from aar.model import build_report
    from app.config import Settings
    from app.export import write_json_atomic

    path = Path(source)
    if path.suffix == ".json" or path.exists():
        if not path.is_file():
            typer.echo(f"error: no export file at {path}", err=True)
            raise typer.Exit(code=2)
        record = json.loads(path.read_text(encoding="utf-8"))
    else:
        record = _build_export(source)
        path = _default_export_path(Settings(), str(record["game"]["id"]))
        write_json_atomic(path, record)
        typer.echo(f"wrote {path}")

    target = out or path.parent / f"{record['game']['id']}-aar"
    target.mkdir(parents=True, exist_ok=True)
    after_action = build_report(record)
    report_path = target / "report.md"
    report_path.write_text(render_markdown(after_action), encoding="utf-8")
    typer.echo(f"wrote {report_path} (includes private material)")
    if card:
        story = _story_for_cards(after_action, record, target, narrate=narrate,
                                 model=narrate_model, renarrate=renarrate)
        _write_cards(story, record, after_action, target, video=not no_video)
    elif narrate:
        typer.echo("error: --narrate writes the story for the cards; add --card", err=True)
        raise typer.Exit(code=2)


@app.command()
def watch(
    game_id: str = typer.Argument(..., help="The game to watch."),
    speed: float = typer.Option(
        4.0, "--speed", min=0.0, help="Replay speed of a finished game, in events per second."
    ),
    step: bool = typer.Option(
        False, "--step", help="Replay a finished game one event per keypress (space)."
    ),
    final: bool = typer.Option(
        False, "--final", help="Show a finished game's final frame instead of replaying it."
    ),
    screen: str = typer.Option(
        "live", "--screen",
        help="Screen to open on: live, network, adjudication or world (keys 1-4 switch).",
    ),
    ascii_only: bool = typer.Option(
        False, "--ascii", help="Draw with plain ASCII, for terminals without Unicode."
    ),
) -> None:
    """Watch a game in the terminal: follow a running game or replay a finished one.

    Four screens: 1 Live Command, 2 Actor Network, 3 Adjudication, 4 World State. Keys: f
    focus the feed, a cycle the Actor filter, s private only, p pause drawing, arrows select,
    enter inspects in full, esc closes, space or n steps a replay, ? help, q quits. Off a
    terminal, one frame of the chosen screen is printed.

    The viewer reads the configured database read-only and shows every Actor's material,
    private Messages included (TRD §41.1). Quitting or pausing never affects a run.
    """
    from sqlalchemy.exc import OperationalError

    from app.config import Settings
    from app.game_service import GameNotFound
    from observer.feed import read_only_engine
    from observer.watch import watch as run_viewer

    from observer.navigation import SCREENS

    try:
        parsed = uuid.UUID(game_id)
    except ValueError as exc:
        typer.echo(f"error: {game_id!r} is not a valid game id", err=True)
        raise typer.Exit(code=2) from exc
    if screen not in SCREENS:
        typer.echo(f"error: --screen must be one of {', '.join(SCREENS)}", err=True)
        raise typer.Exit(code=2)
    engine = read_only_engine(Settings().database_path)
    try:
        run_viewer(engine, parsed, replay=False if final else None, speed=speed, step=step,
                   screen=screen, ascii=True if ascii_only else None)
    except GameNotFound:
        typer.echo(f"error: no game {game_id}", err=True)
        raise typer.Exit(code=1) from None
    except OperationalError as exc:
        typer.echo(f"error: cannot read the database: {exc.orig}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        engine.dispose()


@app.command()
def publish(
    game_id: str = typer.Argument(..., help="The game to publish."),
    follow: bool = typer.Option(
        True, "--follow/--no-follow",
        help="Keep publishing until the game completes (default), or stop once caught up.",
    ),
) -> None:
    """Publish a game's observer feed to the relay in AMAW_RELAY_URL (TRD §41.1).

    A finished game is backfilled; a running game is followed until it completes. The relay holds
    the cursor, so publishing again resumes where it stopped. Published games include every
    Actor's private material.
    """
    from sqlalchemy.exc import OperationalError

    from app.config import Settings
    from app.game_service import GameNotFound
    from observer import publish as publishing
    from observer.feed import read_only_engine

    try:
        parsed = uuid.UUID(game_id)
    except ValueError as exc:
        typer.echo(f"error: {game_id!r} is not a valid game id", err=True)
        raise typer.Exit(code=2) from exc
    try:
        relay = publishing.relay_settings()
    except publishing.PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    engine = read_only_engine(Settings().database_path)
    client = publishing._client(relay)
    try:
        publisher = publishing.Publisher(engine, parsed, client=client, token=relay.token)
        sent = publisher.run(follow=follow)
    except GameNotFound:
        typer.echo(f"error: no game {game_id}", err=True)
        raise typer.Exit(code=1) from None
    except OperationalError as exc:
        typer.echo(f"error: cannot read the database: {exc.orig}", err=True)
        raise typer.Exit(code=1) from None
    except publishing.PublishError as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        client.close()
        engine.dispose()
    typer.echo(f"published {sent} item(s) to {relay.url} (cursor {publisher.cursor})")


def _story_for_cards(
    after_action: AfterActionReport, record: dict, target: Path, *,
    narrate: bool, model: str | None, renarrate: bool,
) -> Story:
    """The narrated story when there is a valid one, else the record-built story (#18).

    A saved ``narration.json`` for this exact export is reused without ``--narrate``, so its
    wording can be edited by hand and re-rendered; it is validated again either way."""
    from aar.narrator import Narration, load_narration, load_or_narrate, narrator_input
    from aar.story import fallback_story

    path = target / "narration.json"
    outcome: Narration | None
    if narrate:
        from agents.model_client import OpenRouterClient
        from app.config import Settings

        settings = Settings()
        if settings.openrouter_api_key is None:
            typer.echo("error: --narrate needs OPENROUTER_API_KEY in the environment or .env", err=True)
            raise typer.Exit(code=2)
        chosen = model or settings.umpire_model
        size = len(json.dumps(narrator_input(after_action, record)))
        typer.echo(f"narrating with {chosen} (input {size:,} characters, about {size // 4:,} tokens)")
        outcome = load_or_narrate(path, after_action, record,
                                  lambda: OpenRouterClient.from_settings(settings),
                                  model=chosen, renarrate=renarrate)
        cost = sum(float(u.get("cost") or 0) for u in outcome.usage)
        if outcome.usage:
            typer.echo(f"narration cost ${cost:.4f} over {len(outcome.usage)} call(s)")
    else:
        outcome = load_narration(path, record)
    if outcome is not None and outcome.story is not None:
        if outcome.cached:
            typer.echo(f"using the saved story in {path}")
        return outcome.story
    if outcome is not None and outcome.errors:
        typer.echo("warning: the narrated story was rejected; using the record-built story:", err=True)
        for problem in outcome.errors:
            typer.echo(f"  - {problem}", err=True)
    return fallback_story(after_action, record)


def _write_cards(
    story: Story, record: dict, after_action: AfterActionReport, target: Path, *, video: bool
) -> None:
    """The story cards as pages, PNGs and one MP4 (#18); exits 2 without the share extra."""
    import aar.capture
    from aar.card import render_screens
    from aar.facts import run_facts

    pages = []
    for n, (_, html) in enumerate(render_screens(story, run_facts(record), after_action), 1):
        page = target / f"card-{n}.html"
        page.write_text(html, encoding="utf-8")
        pages.append(page)
    try:
        for page in pages:
            aar.capture.capture_png(page, page.with_suffix(".png"))
        typer.echo(f"wrote {len(pages)} cards: {pages[0].with_suffix('.png')} …")
        if video:
            aar.capture.capture_mp4(pages, target / "card.mp4")
            typer.echo(f"wrote {target / 'card.mp4'}")
    except aar.capture.CaptureUnavailable as exc:
        typer.echo(f"error: {exc}", err=True)
        raise typer.Exit(code=2) from None
    typer.echo("note: the cards may contain private diplomacy from the administrative export")
