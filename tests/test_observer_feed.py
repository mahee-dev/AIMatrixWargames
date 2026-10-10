"""The observer feed: a read-only, omniscient view of a game for an outside observer (#23).

TRD §41.1 (D-122) admits the observer view. These tests play the canonical fixture offline and
check that the feed walks the event log in order behind a resumable cursor, joins every event
to the rows it references using the admin export's shapes, tolerates unknown or dangling
events, and never writes to or holds a lock on the game database.
"""

from __future__ import annotations

import shutil
import sqlite3
import uuid
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError

from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from app import database, game_service, scenarios
from app.export import build_game_export
from app.models import GameEvent
from observer import feed as observer
from tests.api_helpers import FIXTURE_DIR


@pytest.fixture(scope="module")
def played(tmp_path_factory) -> tuple[Path, uuid.UUID, dict]:
    """The canonical game played to completion, its database path, id and admin export."""
    path = tmp_path_factory.mktemp("observer") / "game.db"
    engine = database.engine_for_path(path)
    database.initialize_database(engine)
    try:
        scenario = scenarios.load_scenario(FIXTURE_DIR)
        creation = scenarios.create_game_from_scenario(engine, scenario)
        game_service.start_game(engine, creation.game_id)
        players = build_scripted_players(scenario.game.turn_order)
        run_deterministic_game(engine, creation.game_id, players, deterministic_umpire())
        export = build_game_export(engine, creation.game_id)
    finally:
        engine.dispose()
    return path, creation.game_id, export


@pytest.fixture
def reader(played):
    path, _, _ = played
    engine = observer.read_only_engine(path)
    yield engine
    engine.dispose()


def _all_items(engine, game_id) -> list[dict]:
    return observer.feed(engine, game_id)["items"]


def test_feed_walks_every_event_in_order(played, reader) -> None:
    _, game_id, export = played
    page = observer.feed(reader, game_id)

    assert [item["seq"] for item in page["items"]] == [e["id"] for e in export["events"]]
    assert [item["type"] for item in page["items"]] == [e["type"] for e in export["events"]]
    assert page["cursor"] == export["events"][-1]["id"]


def test_feed_cursor_resumes_without_gaps_or_repeats(played, reader) -> None:
    _, game_id, _ = played
    everything = _all_items(reader, game_id)

    resumed: list[dict] = []
    cursor = 0
    while True:
        page = observer.feed(reader, game_id, after=cursor, limit=7)
        if not page["items"]:
            break
        resumed.extend(page["items"])
        cursor = page["cursor"]

    assert resumed == everything
    # A cursor at the end returns nothing and holds its position.
    assert observer.feed(reader, game_id, after=cursor) == {"items": [], "cursor": cursor}


def test_message_events_carry_the_full_message(played, reader) -> None:
    _, game_id, export = played
    messages = {m["id"]: m for m in export["messages"]}
    sent = [i for i in _all_items(reader, game_id) if i["type"] == "message_sent"]

    assert sent, "the canonical game sends Messages"
    for item in sent:
        assert item["refs"]["message"] == messages[item["payload"]["message_id"]]
    # Private Messages are included: the observer view is omniscient (TRD §41.1).
    assert any(not item["refs"]["message"]["public"] for item in sent)


def test_adjudication_events_carry_proposal_and_roll(played, reader) -> None:
    _, game_id, export = played
    rulings = {a["argument_id"]: a for a in export["adjudications"]}
    arguments = {a["id"]: a for a in export["arguments"]}
    adjudicated = [i for i in _all_items(reader, game_id) if i["type"] == "argument_adjudicated"]

    assert adjudicated
    for item in adjudicated:
        argument_id = item["payload"]["argument_id"]
        assert item["refs"]["argument"] == arguments[argument_id]
        assert item["refs"]["adjudication"] == rulings[argument_id]


def test_effect_and_con_events_carry_their_rows(played, reader) -> None:
    _, game_id, export = played
    effects = {e["id"]: e for e in export["effects"]}
    cons = {c["id"]: c for c in export["cons"]}
    items = _all_items(reader, game_id)

    created = [i for i in items if i["type"] == "effect_created"]
    assert created
    for item in created:
        assert item["refs"]["effect"] == effects[item["payload"]["effect_id"]]
    for item in items:
        if "con_id" in item["payload"]:
            assert item["refs"]["con"] == cons[item["payload"]["con_id"]]
        if "con_ids" in item["payload"]:
            assert item["refs"]["cons"] == [cons[c] for c in item["payload"]["con_ids"]]


def test_unknown_and_dangling_events_pass_through(played, tmp_path) -> None:
    source, game_id, _ = played
    path = tmp_path / "copy.db"
    shutil.copy(source, path)
    writer = database.engine_for_path(path)
    with database.session_scope(writer) as session:
        session.add(GameEvent(game_id=game_id, turn=9, type="mystery", payload={"x": 1}))
        session.add(
            GameEvent(
                game_id=game_id,
                turn=9,
                type="message_sent",
                payload={"message_id": str(uuid.uuid4())},
            )
        )
    writer.dispose()

    engine = observer.read_only_engine(path)
    try:
        mystery, dangling = _all_items(engine, game_id)[-2:]
    finally:
        engine.dispose()

    assert mystery["type"] == "mystery" and mystery["payload"] == {"x": 1}
    assert mystery["refs"] == {}
    assert dangling["type"] == "message_sent" and dangling["refs"] == {}


def test_snapshot_reports_position_actors_and_totals(played, reader) -> None:
    _, game_id, export = played
    snapshot = observer.snapshot(reader, game_id)

    game = export["game"]
    assert snapshot["game"]["status"] == game["status"] == "complete"
    assert snapshot["game"]["turn"] == game["turn"]
    assert snapshot["game"]["phase"] == game["phase"]
    assert snapshot["game"]["max_turns"] == game["max_turns"]
    assert [a["id"] for a in snapshot["actors"]] == [a["id"] for a in export["actors"]]
    assert {a["name"] for a in snapshot["actors"]} == {a["name"] for a in export["actors"]}
    assert snapshot["waiting_on"] == []
    assert snapshot["effects"] == [e for e in export["effects"] if e["active"]]
    assert snapshot["totals"]["cost_usd"] == sum(
        r["cost_usd"] or 0 for r in export["agent_runs"]
    )
    assert snapshot["cursor"] == export["events"][-1]["id"]


def test_snapshot_names_who_the_game_waits_on(tmp_path) -> None:
    """In diplomacy, every Actor not yet ready is listed; nobody is listed twice."""
    engine = database.engine_for_path(tmp_path / "live.db")
    database.initialize_database(engine)
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(engine, scenario)
    game_service.start_game(engine, creation.game_id)
    engine.dispose()

    reader = observer.read_only_engine(tmp_path / "live.db")
    try:
        snapshot = observer.snapshot(reader, creation.game_id)
    finally:
        reader.dispose()

    assert snapshot["game"]["phase"] == "diplomacy"
    assert sorted(snapshot["waiting_on"]) == sorted(scenario.game.turn_order)


def test_feed_rejects_an_unknown_game(reader) -> None:
    with pytest.raises(game_service.GameNotFound):
        observer.feed(reader, uuid.uuid4())
    with pytest.raises(game_service.GameNotFound):
        observer.snapshot(reader, uuid.uuid4())


def test_read_only_engine_cannot_write(played, reader) -> None:
    with pytest.raises(OperationalError, match="readonly"):
        with reader.begin() as connection:
            connection.exec_driver_sql("CREATE TABLE observer_write (x INTEGER)")


def test_reading_the_feed_leaves_no_lock_behind(played, reader) -> None:
    """After feed() and snapshot() return, a writer commits at once, whatever the journal mode."""
    path, game_id, _ = played
    observer.feed(reader, game_id)
    observer.snapshot(reader, game_id)

    writer = sqlite3.connect(path, timeout=0.5, isolation_level=None)
    try:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("CREATE TABLE IF NOT EXISTS observer_probe (x INTEGER)")
        writer.execute("COMMIT")
    finally:
        writer.close()
