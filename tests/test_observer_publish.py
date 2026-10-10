"""The publisher: pushes the observer feed to an operator-run relay over HTTPS (#25).

TRD §41.1 (D-122). A fake relay behind ``httpx.MockTransport`` implements the ingest contract
(``amaw-observer-ingest-v1``) in memory, so nothing touches the network. The tests check that
batches arrive in order and complete, that a restart resumes from the relay's cursor, that a
lost response is retried without duplicates, that a dead relay never fails a run, and that a
missing configuration is refused before a run starts.
"""

from __future__ import annotations

import json
import threading
import uuid
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

import amaw_cli
from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from app import database, game_service, scenarios
from observer import feed as observer
from observer import publish
from tests.api_helpers import FIXTURE_DIR
from tests.cli_helpers import capture_run_cli, invoke_run

TOKEN = "relay-token-for-tests-0123456789"


class FakeRelay:
    """The relay's ingest contract, in memory."""

    def __init__(self) -> None:
        self.items: dict[str, dict[int, dict]] = {}
        self.snapshots: dict[str, dict] = {}
        self.posts: list[dict] = []
        self.fail_next_after_storing = 0
        self.reject_with: int | None = None

    def cursor(self, game_id: str) -> int:
        return max(self.items.get(game_id, {}), default=0)

    def handler(self, request: httpx.Request) -> httpx.Response:
        if request.headers.get("authorization") != f"Bearer {TOKEN}":
            return httpx.Response(401, json={"detail": "ingest token required"})
        if self.reject_with is not None:
            return httpx.Response(self.reject_with, json={"detail": "rejected"})
        parts = request.url.path.strip("/").split("/")
        game_id = parts[2]
        if request.method == "GET" and parts[-1] == "cursor":
            return httpx.Response(200, json={"cursor": self.cursor(game_id)})
        body = json.loads(request.content)
        assert body["contract"] == "amaw-observer-ingest-v1"
        assert body["snapshot"]["game"]["id"] == game_id
        seqs = [item["seq"] for item in body["items"]]
        assert seqs == sorted(set(seqs))
        self.posts.append(body)
        self.snapshots[game_id] = body["snapshot"]
        for item in body["items"]:
            self.items.setdefault(game_id, {}).setdefault(item["seq"], item)
        if self.fail_next_after_storing:
            self.fail_next_after_storing -= 1
            return httpx.Response(502, text="bad gateway")
        return httpx.Response(200, json={"cursor": self.cursor(game_id)})

    def client(self) -> httpx.Client:
        return httpx.Client(base_url="https://relay.test", transport=httpx.MockTransport(self.handler))


def _play(path: Path, *, finish: bool = True) -> uuid.UUID:
    engine = database.engine_for_path(path)
    database.initialize_database(engine)
    try:
        scenario = scenarios.load_scenario(FIXTURE_DIR)
        creation = scenarios.create_game_from_scenario(engine, scenario)
        game_service.start_game(engine, creation.game_id)
        if finish:
            players = build_scripted_players(scenario.game.turn_order)
            run_deterministic_game(engine, creation.game_id, players, deterministic_umpire())
    finally:
        engine.dispose()
    return creation.game_id


@pytest.fixture(scope="module")
def finished(tmp_path_factory) -> tuple[Path, uuid.UUID]:
    path = tmp_path_factory.mktemp("publish") / "game.db"
    return path, _play(path)


@pytest.fixture
def reader(finished):
    engine = observer.read_only_engine(finished[0])
    yield engine
    engine.dispose()


def _publisher(reader, game_id, relay: FakeRelay, **kwargs) -> publish.Publisher:
    return publish.Publisher(
        reader, game_id, client=relay.client(), token=TOKEN,
        batch_size=kwargs.pop("batch_size", 50), poll_seconds=0.01, retry_seconds=0.01, **kwargs,
    )


def test_backfill_sends_the_whole_feed_in_order(finished, reader) -> None:
    _, game_id = finished
    relay = FakeRelay()

    sent = _publisher(reader, game_id, relay).run(follow=False)

    expected = observer.feed(reader, game_id)["items"]
    stored = [relay.items[str(game_id)][seq] for seq in sorted(relay.items[str(game_id)])]
    assert sent == len(expected)
    # The relay receives exactly the feed's items: nothing added, nothing filtered.
    assert stored == json.loads(json.dumps(expected))
    assert len(relay.posts) > 1 and all(len(p["items"]) <= 50 for p in relay.posts)
    assert relay.snapshots[str(game_id)]["game"]["status"] == "complete"


def test_a_restart_resumes_from_the_relays_cursor(finished, reader) -> None:
    _, game_id = finished
    relay = FakeRelay()
    _publisher(reader, game_id, relay, batch_size=40).push_once()
    resumed_from = relay.cursor(str(game_id))
    relay.posts.clear()

    _publisher(reader, game_id, relay).run(follow=False)

    assert relay.posts[0]["items"][0]["seq"] > resumed_from
    assert sorted(relay.items[str(game_id)]) == [
        item["seq"] for item in observer.feed(reader, game_id)["items"]
    ]


def test_a_lost_response_is_retried_without_duplicates(finished, reader) -> None:
    _, game_id = finished
    relay = FakeRelay()
    relay.fail_next_after_storing = 2

    _publisher(reader, game_id, relay).run(follow=False)

    seqs = [item["seq"] for item in observer.feed(reader, game_id)["items"]]
    assert sorted(relay.items[str(game_id)]) == seqs


@pytest.mark.parametrize("status", [401, 413, 422])
def test_a_refusal_stops_the_publisher_with_a_clear_error(finished, reader, status) -> None:
    _, game_id = finished
    relay = FakeRelay()
    relay.reject_with = status
    with pytest.raises(publish.PublishError, match=str(status)):
        _publisher(reader, game_id, relay).run(follow=False)


def test_following_a_live_game_ends_when_it_completes(tmp_path) -> None:
    path = tmp_path / "live.db"
    game_id = _play(path, finish=False)
    relay = FakeRelay()
    engine = observer.read_only_engine(path)
    done = threading.Event()
    try:
        publisher = _publisher(engine, game_id, relay)
        thread = threading.Thread(target=lambda: (publisher.run(follow=True), done.set()))
        thread.start()

        writer = database.engine_for_path(path)
        scenario = scenarios.load_scenario(FIXTURE_DIR)
        run_deterministic_game(
            writer, game_id, build_scripted_players(scenario.game.turn_order),
            deterministic_umpire(),
        )
        writer.dispose()
        thread.join(timeout=30)
        final = observer.feed(engine, game_id)["items"]
    finally:
        engine.dispose()

    assert done.is_set(), "the publisher should stop once the game is complete and sent"
    assert sorted(relay.items[str(game_id)]) == [item["seq"] for item in final]
    assert relay.snapshots[str(game_id)]["game"]["status"] == "complete"


def test_a_dead_relay_never_raises_from_the_background(finished) -> None:
    path, game_id = finished
    engine = observer.read_only_engine(path)

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url="https://relay.test", transport=httpx.MockTransport(refuse))
    try:
        thread, stop = publish.publish_in_background(
            engine, lambda: game_id, client=client, token=TOKEN,
            poll_seconds=0.01, retry_seconds=0.01, drain_seconds=0.2,
        )
        stop.set()
        thread.join(timeout=5)
    finally:
        engine.dispose()
    assert not thread.is_alive()


def test_the_background_publisher_drains_before_it_stops(finished) -> None:
    """Setting stop still sends what the feed holds, so the end of a run reaches the relay."""
    path, game_id = finished
    engine = observer.read_only_engine(path)
    relay = FakeRelay()
    try:
        thread, stop = publish.publish_in_background(
            engine, lambda: game_id, client=relay.client(), token=TOKEN,
            poll_seconds=0.01, retry_seconds=0.01, drain_seconds=10,
        )
        stop.set()
        thread.join(timeout=15)
        expected = [item["seq"] for item in observer.feed(engine, game_id)["items"]]
    finally:
        engine.dispose()
    assert sorted(relay.items[str(game_id)]) == expected


# --- configuration and CLI -----------------------------------------------------------------


@pytest.fixture
def no_relay_env(monkeypatch, tmp_path):
    monkeypatch.delenv("AMAW_RELAY_URL", raising=False)
    monkeypatch.delenv("AMAW_RELAY_TOKEN", raising=False)
    monkeypatch.chdir(tmp_path)  # no .env to fall back on


def test_relay_settings_need_url_and_token(no_relay_env, monkeypatch) -> None:
    with pytest.raises(publish.PublishError, match="AMAW_RELAY_URL"):
        publish.relay_settings()
    monkeypatch.setenv("AMAW_RELAY_URL", "http://relay.example.com")
    monkeypatch.setenv("AMAW_RELAY_TOKEN", TOKEN)
    with pytest.raises(publish.PublishError, match="https"):
        publish.relay_settings()
    monkeypatch.setenv("AMAW_RELAY_URL", "https://relay.example.com/")
    settings = publish.relay_settings()
    assert settings.url == "https://relay.example.com" and settings.token == TOKEN


def test_relay_settings_allow_plain_http_only_to_this_machine(no_relay_env, monkeypatch) -> None:
    monkeypatch.setenv("AMAW_RELAY_URL", "http://127.0.0.1:8000")
    monkeypatch.setenv("AMAW_RELAY_TOKEN", TOKEN)
    assert publish.relay_settings().url == "http://127.0.0.1:8000"


def test_cli_publish_refuses_without_configuration(no_relay_env, finished, monkeypatch) -> None:
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(finished[0]))
    result = CliRunner().invoke(amaw_cli.app, ["publish", str(finished[1])])
    assert result.exit_code == 2 and "AMAW_RELAY_URL" in result.output


def test_cli_publish_backfills_a_finished_game(finished, monkeypatch) -> None:
    path, game_id = finished
    relay = FakeRelay()
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(path))
    monkeypatch.setenv("AMAW_RELAY_URL", "https://relay.test")
    monkeypatch.setenv("AMAW_RELAY_TOKEN", TOKEN)
    monkeypatch.setattr(publish, "_client", lambda settings: relay.client())

    result = CliRunner().invoke(amaw_cli.app, ["publish", str(game_id)])

    assert result.exit_code == 0, result.output
    assert f"cursor {relay.cursor(str(game_id))}" in result.output
    assert relay.snapshots[str(game_id)]["game"]["status"] == "complete"


def test_run_publish_refuses_before_the_run_without_configuration(
    tmp_path, monkeypatch, no_relay_env
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    result = invoke_run(FIXTURE_DIR, "--publish")
    assert result.exit_code == 2 and "AMAW_RELAY_URL" in result.output
    assert "runner_options" not in captured


def test_run_publish_with_an_unreachable_relay_leaves_the_run_unchanged(
    tmp_path, monkeypatch, no_relay_env
) -> None:
    captured = capture_run_cli(tmp_path, monkeypatch)
    # A closed local port: connection refused at once, no network involved.
    monkeypatch.setenv("AMAW_RELAY_URL", "http://127.0.0.1:9")
    monkeypatch.setenv("AMAW_RELAY_TOKEN", TOKEN)

    plain = invoke_run(FIXTURE_DIR)
    published = invoke_run(FIXTURE_DIR, "--publish")

    assert plain.exit_code == published.exit_code == 0, published.output
    assert plain.stdout == published.stdout
    assert "runner_options" in captured
    assert not [t for t in threading.enumerate() if t.name == "amaw-publish"]
