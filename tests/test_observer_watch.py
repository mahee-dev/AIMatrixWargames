"""The terminal viewer over the observer feed: ``amaw watch`` and ``amaw run --watch`` (#24).

The viewer's state folding, feed lines and frame rendering are pure and checked directly over
the canonical fixture played offline. The CLI is checked off a terminal, where the viewer prints
one frame and returns, and ``--watch`` is checked through the offline ``amaw run`` harness: it
must leave the run's exit code and output unchanged and always close the viewer.
"""

from __future__ import annotations

import io
import logging
import threading
import uuid
from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

import amaw_cli
from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from app import database, game_service, scenarios
from observer import feed as observer
from observer import watch as viewer
from tests.api_helpers import FIXTURE_DIR
from tests.cli_helpers import capture_run_cli, invoke_run


@pytest.fixture(scope="module")
def played(tmp_path_factory) -> tuple[Path, uuid.UUID]:
    path = tmp_path_factory.mktemp("watch") / "game.db"
    engine = database.engine_for_path(path)
    database.initialize_database(engine)
    try:
        scenario = scenarios.load_scenario(FIXTURE_DIR)
        creation = scenarios.create_game_from_scenario(engine, scenario)
        game_service.start_game(engine, creation.game_id)
        players = build_scripted_players(scenario.game.turn_order)
        run_deterministic_game(engine, creation.game_id, players, deterministic_umpire())
    finally:
        engine.dispose()
    return path, creation.game_id


@pytest.fixture
def reader(played):
    engine = observer.read_only_engine(played[0])
    yield engine
    engine.dispose()


def _replayed(reader, game_id, upto: int | None = None) -> viewer.ViewState:
    state = viewer.ViewState(live=False)
    state.take_snapshot(observer.snapshot(reader, game_id))
    for item in observer.feed(reader, game_id)["items"][:upto]:
        state.apply(item)
    return state


def _text(renderable, width: int = 140) -> str:
    console = Console(file=io.StringIO(), width=width, color_system=None)
    console.print(renderable)
    return console.file.getvalue()  # type: ignore[attr-defined]


def test_replay_folds_the_whole_game(played, reader) -> None:
    _, game_id = played
    state = _replayed(reader, game_id)
    snapshot = observer.snapshot(reader, game_id)

    assert state.status == "complete" and state.phase == "complete"
    assert state.turn == snapshot["game"]["turn"]
    assert state.cursor == snapshot["cursor"]
    assert state.ruling is not None
    assert state.lines


def test_replay_tracks_the_arguing_actor_mid_game(played, reader) -> None:
    """Mid-replay, who is arguing comes from the feed, not from the final snapshot."""
    _, game_id = played
    items = observer.feed(reader, game_id)["items"]
    first = next(i for i, item in enumerate(items) if item["type"] == "argument_submitted")
    state = _replayed(reader, game_id, upto=first + 1)
    owner = items[first]["refs"]["argument"]["actor_id"]

    assert state.active == owner
    assert state.arguments_used == {owner: 1}
    assert state.status == "running"


def test_every_canonical_event_gets_a_line_or_is_bookkeeping(played, reader) -> None:
    _, game_id = played
    names = {a["id"]: a["name"] for a in observer.snapshot(reader, game_id)["actors"]}
    bookkeeping = {"actor_ready", "con_passed", "con_challengers_selected", "cons_revealed"}

    for item in observer.feed(reader, game_id)["items"]:
        line = viewer.describe(item, names)
        if item["type"] in bookkeeping:
            assert line is None
        else:
            assert line is not None and line.text, item["type"]


def test_private_messages_name_both_ends_and_are_marked(played, reader) -> None:
    _, game_id = played
    names = {a["id"]: a["name"] for a in observer.snapshot(reader, game_id)["actors"]}
    private = [
        item for item in observer.feed(reader, game_id)["items"]
        if item["type"] == "message_sent" and not item["refs"]["message"]["public"]
    ]
    assert private
    message = private[0]["refs"]["message"]
    line = viewer.describe(private[0], names)

    assert line is not None and line.private and line.kind == "private"
    assert names[message["sender_actor_id"]] in line.who
    assert all(names[r] in line.who for r in message["recipients"])
    assert line.text == message["text"]


def test_filters_narrow_the_feed(played, reader) -> None:
    _, game_id = played
    state = _replayed(reader, game_id)

    state.secret_only = True
    assert state.visible_lines() and all(line.private for line in state.visible_lines())
    state.secret_only = False

    ids = [a["id"] for a in state.actors]
    seen = []
    for _ in ids:
        state.cycle_actor_filter()
        seen.append(state.actor_filter)
        assert all(state.actor_filter in line.actors for line in state.visible_lines())
    assert seen == ids
    state.cycle_actor_filter()
    assert state.actor_filter is None


def test_a_frame_shows_position_actors_ruling_and_feed(played, reader) -> None:
    _, game_id = played
    state = _replayed(reader, game_id)
    frame = _text(viewer.render(state))

    assert f"turn {state.turn}/" in frame
    for actor in state.actors:
        assert actor["name"] in frame
    assert "last ruling" in frame.lower() and ("SUCCESS" in frame or "FAILURE" in frame)
    assert "feed" in frame and "game complete" in frame
    assert "q quit" in frame


def test_the_ruling_bar_marks_the_roll() -> None:
    bar = viewer._bar(0.5, 0.75, width=20).plain
    assert len(bar) == 20
    assert bar[:10] == "█" * 10
    assert bar[15] == "│"


def test_watch_off_a_terminal_prints_one_frame(played, reader) -> None:
    _, game_id = played
    console = Console(file=io.StringIO(), width=140, color_system=None)

    state = viewer.watch(reader, game_id, console=console)

    assert state.cursor == observer.snapshot(reader, game_id)["cursor"]
    assert state.actors[0]["name"] in console.file.getvalue()  # type: ignore[attr-defined]


def test_watch_waits_for_a_game_and_honours_stop(reader) -> None:
    stop = threading.Event()
    stop.set()
    state = viewer.watch(reader, lambda: None, stop=stop, poll_seconds=0.01)
    assert state.cursor == 0 and state.actors == []


def test_background_viewer_never_raises_into_the_caller(played) -> None:
    path, _ = played
    engine = observer.read_only_engine(path)
    console = Console(file=io.StringIO(), width=140, color_system=None)
    try:
        thread, stop = viewer.watch_in_background(
            engine, lambda: uuid.uuid4(), console=console, poll_seconds=0.01
        )
        thread.join(timeout=5)
        stop.set()
    finally:
        engine.dispose()

    assert not thread.is_alive()
    assert "viewer closed" in console.file.getvalue()  # type: ignore[attr-defined]


def test_cli_watch_prints_a_finished_game(played, monkeypatch) -> None:
    path, game_id = played
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(path))

    result = CliRunner().invoke(amaw_cli.app, ["watch", str(game_id)])

    assert result.exit_code == 0, result.output
    assert "game complete" in result.output


def test_cli_watch_refuses_bad_and_unknown_ids(played, monkeypatch) -> None:
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(played[0]))

    assert CliRunner().invoke(amaw_cli.app, ["watch", "not-a-uuid"]).exit_code == 2
    unknown = CliRunner().invoke(amaw_cli.app, ["watch", str(uuid.uuid4())])
    assert unknown.exit_code == 1 and "no game" in unknown.output


def test_run_watch_leaves_the_run_unchanged_and_closes_the_viewer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    root = logging.getLogger()
    handlers_before = list(root.handlers)

    plain = invoke_run(FIXTURE_DIR)
    watched = invoke_run(FIXTURE_DIR, "--watch")

    assert plain.exit_code == watched.exit_code == 0, watched.output
    assert plain.stdout == watched.stdout  # the game id the run prints
    assert "logs: " in watched.output and str(tmp_path / "logs") in watched.output
    assert "runner_options" in captured
    assert not [t for t in threading.enumerate() if t.name == "amaw-watch"]
    assert [h for h in root.handlers if type(h) is logging.StreamHandler] == [
        h for h in handlers_before if type(h) is logging.StreamHandler
    ]
