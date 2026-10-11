"""The terminal console's four screens over the observer feed (TRD §41.1).

The canonical fixture played offline checks the screens against a real game: every screen draws
at wide, normal and narrow sizes; a replay frame shows only what was known at its cursor; links
follow the Messages actually sent; rulings show the Umpire's probability and the server's roll.
Scripted feed items cover what the fixture never reaches: public broadcasts, passes without a
Con, sampled challengers, the owner's reply, automatic rulings, pauses, long Messages and more
than three Actors. The viewer must stay read-only and must survive a panel that fails to draw.
"""

from __future__ import annotations

import hashlib
import io
import threading
import uuid
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

import amaw_cli
from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from app import database, game_service, scenarios
from observer import feed as observer
from observer import watch as viewer
from observer.navigation import SCREENS, TITLES, handle_key
from observer.screens import frame as frame_module
from observer.screens.common import Sized
from observer.screens.overlays import detail_content
from observer.viewstate import ViewState, setup_effects
from tests.api_helpers import FIXTURE_DIR

SIZES = [(140, 45), (100, 32), (72, 30), (60, 24)]


# --- fixtures and helpers ----------------------------------------------------------------------


@pytest.fixture(scope="module")
def played(tmp_path_factory) -> tuple[Path, uuid.UUID]:
    path = tmp_path_factory.mktemp("console") / "game.db"
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


@pytest.fixture
def game(played, reader) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _, game_id = played
    return observer.snapshot(reader, game_id), observer.feed(reader, game_id)["items"]


def replayed(snapshot: dict[str, Any], items: list[dict[str, Any]],
             upto: int | None = None) -> ViewState:
    """A replay folded up to ``upto`` items, as ``amaw watch`` builds it."""
    state = ViewState(live=False)
    state.take_snapshot(snapshot)
    state.seed_effects(setup_effects(snapshot, items))
    for item in items[:upto]:
        state.apply(item)
    return state


def frame(state: ViewState, screen: str | None = None, width: int = 140,
          height: int = 45) -> str:
    console = Console(file=io.StringIO(), width=width, height=height, color_system=None)
    console.print(viewer.render(state, screen=screen, height=height))
    return console.file.getvalue()  # type: ignore[attr-defined]


def index_of(items: list[dict[str, Any]], kind: str, nth: int = 0, **payload: Any) -> int:
    matches = [i for i, item in enumerate(items) if item["type"] == kind
               and all(item["payload"].get(k) == v for k, v in payload.items())]
    return matches[nth]


class Script:
    """Feed items written by hand, in the observer feed's shapes."""

    def __init__(self, actors: list[str], models: dict[str, str] | None = None,
                 budget: int = 1) -> None:
        self.actors = actors
        self.models = models or {}
        self.budget = budget
        self.items: list[dict[str, Any]] = []
        self.turn = 1
        self.ids = 0

    def _id(self) -> str:
        self.ids += 1
        return str(uuid.UUID(int=self.ids))

    def add(self, kind: str, actor: str | None = None, payload: dict | None = None,
            refs: dict | None = None) -> dict[str, Any]:
        seq = len(self.items) + 1
        item = {
            "seq": seq, "turn": self.turn, "type": kind, "actor_id": actor,
            "created_at": f"2026-10-11T10:{seq // 60:02d}:{seq % 60:02d}",
            "payload": payload or {}, "refs": refs or {},
        }
        self.items.append(item)
        return item

    def snapshot(self, **game: Any) -> dict[str, Any]:
        return {
            "game": {"id": str(uuid.UUID(int=999)), "scenario_id": "scripted",
                     "scenario_version": "1", "status": "running", "phase": "diplomacy",
                     "turn": 1, "max_turns": 3, "active_actor_id": None,
                     "primary_arguments_per_actor": self.budget, **game},
            "actors": [{"id": a, "name": a.title() + " Command", "model": self.models.get(a),
                        "diplomacy_ready": False, "arguments_used": 0, "cost_usd": 0,
                        "input_tokens": 0, "output_tokens": 0} for a in self.actors],
            "waiting_on": [], "effects": [],
            "totals": {"cost_usd": 0.12, "input_tokens": 1000, "output_tokens": 234,
                       "model_calls": 7},
            "cursor": len(self.items),
        }

    def start(self) -> None:
        self.add("game_started", payload={"max_turns": 3, "turn_order": self.actors})
        self.phase("diplomacy")

    def phase(self, phase: str) -> None:
        self.add("phase_started", payload={"phase": phase})

    def message(self, sender: str, recipients: list[str], text: str, public: bool = False):
        message_id = self._id()
        self.add("message_sent", sender, {"message_id": message_id, "public": public},
                 {"message": {"id": message_id, "turn": self.turn, "sender_actor_id": sender,
                              "recipients": recipients, "public": public,
                              "visibility": "public" if public else "private", "text": text,
                              "created_at": "2026-10-11T10:00:00"}})

    def argument(self, owner: str, action: str = "Act", desired: str = "Gain",
                 challengers: list[str] | None = None) -> dict[str, Any]:
        ref = {"id": self._id(), "turn": self.turn, "actor_id": owner, "action": action,
               "desired_effect": desired, "pros": ["A reason."], "budget_slot": 1,
               "status": "resolved", "challenger_actor_ids": challengers}
        self.phase("argument")
        self.add("argument_submitted", owner, {"argument_id": ref["id"]}, {"argument": ref})
        if challengers is not None:
            self.add("con_challengers_selected", None,
                     {"argument_id": ref["id"], "challenger_actor_ids": challengers})
        self.phase("cons")
        return ref

    def con(self, actor: str, argument: dict, reason: str) -> None:
        con = {"id": self._id(), "argument_id": argument["id"], "actor_id": actor,
               "reason": reason}
        self.add("con_submitted", actor, {"argument_id": argument["id"], "con_id": con["id"]},
                 {"argument": argument, "con": con})

    def passed(self, actor: str, argument: dict) -> None:
        self.add("con_passed", actor, {"argument_id": argument["id"]}, {"argument": argument})

    def adjudicate(self, argument: dict, mode: str, probability: float | None,
                   roll: float | None, success: bool) -> None:
        record = {"argument_id": argument["id"], "mode": mode, "probability": probability,
                  "rationale": "Because.", "success_effects": ["It works."],
                  "failure_effects": ["It fails."], "supersedes_effect_ids": [],
                  "roll": roll, "success": success, "raw_output": {}}
        self.add("argument_adjudicated", argument["actor_id"],
                 {"argument_id": argument["id"], "mode": mode, "probability": probability,
                  "roll": roll, "success": success},
                 {"argument": argument, "adjudication": record})

    def effect(self, argument: dict | None, description: str, visibility: str = "public",
               visible_to: list[str] | None = None) -> dict[str, Any]:
        ref = {"id": self._id(), "description": description, "visibility": visibility,
               "visible_to": visible_to or [], "source_argument_id":
                   argument["id"] if argument else None,
               "source_consequence_turn": None, "source_inject_id": None,
               "created_turn": self.turn, "expires_turn": None, "active": False,
               "supersedes": []}
        self.add("effect_created", None, {"effect_id": ref["id"]}, {"effect": ref})
        return ref


def scripted(script: Script, live: bool = False, **game: Any) -> ViewState:
    snapshot = script.snapshot(**game)
    state = ViewState(live=live)
    state.take_snapshot(snapshot)
    for item in script.items:
        state.apply(item)
    if live:
        state.take_snapshot(snapshot)
    return state


# --- every screen, every size ------------------------------------------------------------------


@pytest.mark.parametrize("screen", SCREENS)
@pytest.mark.parametrize("width,height", SIZES)
def test_every_screen_fits_every_terminal_size(game, screen, width, height) -> None:
    snapshot, items = game
    for upto in (0, len(items) // 3, None):
        text = frame(replayed(snapshot, items, upto), screen, width, height)
        lines = text.splitlines()

        assert len(lines) == height
        assert all(Console(width=width).measure(line).maximum <= width for line in lines)
        assert TITLES[screen] in text
        assert "q quit" in lines[-1]


def test_screens_switch_by_number_without_touching_the_fold(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items, 60)
    cursor, lines = state.cursor, len(state.lines)

    for key, screen in zip("2341", ["network", "adjudication", "world", "live"], strict=True):
        assert handle_key(state, key)
        assert state.ui.screen == screen and TITLES[screen] in frame(state)
    assert (state.cursor, len(state.lines)) == (cursor, lines)


# --- Live Command -------------------------------------------------------------------------------


def test_live_command_shows_actors_models_and_whose_input_is_due() -> None:
    script = Script(["north", "south", "east"],
                    models={"north": "deepseek/deepseek-v4.1", "south": "openai/gpt-x"})
    script.start()
    snapshot = script.snapshot(phase="diplomacy")
    snapshot["actors"][0]["diplomacy_ready"] = True
    snapshot["waiting_on"] = ["south", "east"]
    state = ViewState(live=True)
    state.take_snapshot(snapshot)
    for item in script.items:
        state.apply(item)
    state.take_snapshot(snapshot)
    text = frame(state)

    assert "North Command" in text and "deepseek/deepseek-v4.1" in text
    assert "openai/gpt-x" in text and "model n/a" in text  # east has no recorded model
    assert "awaiting input: South Command, East Command" in text
    assert "READY" in text and "NOT READY" in text
    assert "calls 7" in text and "$0.12" in text
    # Input due is not a running model call; the screen never claims one.
    assert "thinking" not in text.lower() and "generating" not in text.lower()


# --- failed activations (#34) -------------------------------------------------------------------

REASON = "activation failed for 'south': UnexpectedModelBehavior <- ToolRetryError"


def _failing(script: Script, at: str, count: int = 3) -> dict[str, Any]:
    snapshot = script.snapshot(phase="diplomacy")
    snapshot["waiting_on"] = ["south"]
    for actor in snapshot["actors"]:
        actor["failed_activations"], actor["last_failure"] = 0, None
    south = next(a for a in snapshot["actors"] if a["id"] == "south")
    south["failed_activations"] = count
    south["last_failure"] = {"at": at, "turn": 1, "phase": "diplomacy", "reason": REASON}
    return snapshot


def _live(script: Script, snapshot: dict[str, Any]) -> ViewState:
    state = ViewState(live=True)
    state.take_snapshot(snapshot)
    for item in script.items:
        state.apply(item)
    state.take_snapshot(snapshot)
    return state


def test_live_command_shows_a_failure_newer_than_the_last_event() -> None:
    """While the game waits on an Actor whose last activation failed, the screen says so (#34).

    It names when and why from the recorded failure, and never claims a retry is running: the
    runner may be retrying or about to give up, and nothing records which.
    """
    script = Script(["north", "south", "east"])
    script.start()
    state = _live(script, _failing(script, at="2026-10-11T10:59:00"))
    text = frame(state).lower()

    assert "3 failed" in text
    assert "south command failed at 10:59:00" in text
    assert "toolretryerror" in text
    assert "retrying" not in text


def test_a_failure_older_than_the_last_event_is_not_the_current_hold() -> None:
    """The game moved on after the failure: the count stays, the waiting notice does not."""
    script = Script(["north", "south", "east"])
    script.start()
    state = _live(script, _failing(script, at="2026-10-11T09:00:00"))
    text = frame(state).lower()

    assert "3 failed" in text
    assert "failed at" not in text


def test_a_replay_shows_failures_only_once_it_reaches_the_snapshot() -> None:
    """Failures describe the present, like run totals, so an early replay frame omits them."""
    script = Script(["north", "south", "east"])
    script.start()
    script.message("north", ["south"], "Hello.")
    snapshot = _failing(script, at="2026-10-11T10:59:00")
    early = ViewState(live=False)
    early.take_snapshot(snapshot)
    early.apply(script.items[0])
    assert "failed" not in frame(early).lower()

    late = ViewState(live=False)
    late.take_snapshot(snapshot)
    for item in script.items:
        late.apply(item)
    assert "3 failed" in frame(late).lower()


def test_live_pipeline_follows_the_phases(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items, index_of(items, "phase_started", phase="cons") + 1)

    assert [state.stage_of(p) for p in
            ("diplomacy", "argument", "cons", "reply", "adjudication", "consequences")] == [
        "done", "done", "current", "unused", "todo", "unused"]
    text = frame(state)
    assert "CONS" in text and "On the table:" in text and "Con participation: 0/2" in text

    done = replayed(snapshot, items)
    assert done.stage_of("adjudication") == "done" and "GAME COMPLETE" in frame(done)


def test_feed_scrolls_and_enter_inspects_the_full_event(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items)
    newest = state.visible_lines()[-1]

    handle_key(state, "up")
    handle_key(state, "up")
    assert state.ui.scroll == 2
    selected = state.visible_lines()[-3]
    handle_key(state, "enter")
    title, lines = detail_content(state)
    detail = "\n".join(line.plain for line in lines)

    assert title == "EVENT" and f"event {selected.seq}" in detail
    assert newest.seq != selected.seq
    handle_key(state, "esc")
    assert not state.ui.detail
    handle_key(state, "g")
    assert state.ui.scroll == 0


def test_feed_focus_wraps_long_messages_and_detail_keeps_the_whole_text() -> None:
    script = Script(["north", "south", "east"])
    script.start()
    long_text = " ".join(f"word{n}" for n in range(400)) + " FINAL-WORD"
    script.message("north", ["south"], long_text)
    state = scripted(script)

    for width, height in SIZES:
        lines = frame(state, "live", width, height).splitlines()
        assert all(Console(width=width).measure(line).maximum <= width for line in lines)
    assert "FINAL-WORD" not in frame(state, "live", 100, 32)  # clipped on the dashboard

    handle_key(state, "f")
    focused = frame(state, "live", 100, 40)
    assert "EVENT FEED · focused" in focused and "FINAL-WORD" in focused

    handle_key(state, "enter")
    _, detail = detail_content(state)
    assert long_text in "\n".join(line.plain for line in detail)


# --- Actor Network ------------------------------------------------------------------------------


def test_links_follow_private_messages_and_broadcasts_are_not_links(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items)
    expected: dict[tuple[str, str], int] = {}
    for item in items:
        message = item["refs"].get("message")
        if item["type"] == "message_sent" and message and not message["public"]:
            for recipient in message["recipients"]:
                key = (message["sender_actor_id"], recipient)
                expected[key] = expected.get(key, 0) + 1

    assert dict(state.links) == expected
    assert not state.broadcasts

    script = Script(["north", "south", "east"])
    script.start()
    script.message("north", ["south", "east"], "To all.", public=True)
    script.message("south", ["east"], "Just you.")
    scripted_state = scripted(script)

    assert dict(scripted_state.links) == {("south", "east"): 1}
    assert dict(scripted_state.broadcasts) == {"north": 1}
    text = frame(scripted_state, "network")
    assert "pub 1" in text and "1 private · 1 public" in text
    assert "not drawn as links" in text


def test_network_marks_direction_on_each_link() -> None:
    script = Script(["north", "south"])
    script.start()
    for _ in range(3):
        script.message("north", ["south"], "East-bound.")
    script.message("south", ["north"], "West-bound.")
    text = frame(scripted(script), "network")

    # Two Actors sit left and right: three Messages went right, one went left.
    assert "3→ 1←" in text


@pytest.mark.parametrize("count", [6, 10])
def test_network_handles_more_actors(count) -> None:
    actors = [f"a{n:02d}" for n in range(count)]
    script = Script(actors)
    script.start()
    for n, actor in enumerate(actors):
        script.message(actor, [actors[(n + 1) % count]], f"Hello from {actor}.")
    state = scripted(script)
    text = frame(state, "network", 140, 45)

    if count <= 8:
        assert all(f"A{n:02d} Command" in text for n in range(count))
        assert "from \\ to" not in text
    else:
        assert "from \\ to" in text  # too many for a diagram: a matrix
    narrow = frame(state, "network", 60, 30)
    assert all(Console(width=60).measure(line).maximum <= 60 for line in narrow.splitlines())
    for screen in SCREENS:
        assert TITLES[screen] in frame(state, screen, 100, 32)


# --- Adjudication -------------------------------------------------------------------------------


def test_adjudication_shows_probability_roll_and_recorded_effects(game) -> None:
    snapshot, items = game
    first_ruling = index_of(items, "argument_adjudicated")
    state = replayed(snapshot, items, first_ruling + 2)  # the ruling and its effect_created
    record = items[first_ruling]["refs"]["adjudication"]
    text = frame(state, "adjudication")

    assert f"Umpire probability  {record['probability']:.2f}" in text
    assert f"Server roll         {record['roll']:.2f}" in text
    assert "success when roll ≤ probability" in text
    assert "SUCCESS" in text and "RECORDED BY THE ENGINE" in text
    effect = items[first_ruling + 1]["refs"]["effect"]
    assert effect["description"] in text
    assert "REQUESTED by the owner, not world state" in text


def test_adjudication_waits_before_the_ruling(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items, index_of(items, "phase_started", phase="adjudication") + 1)
    text = frame(state, "adjudication")

    assert "waiting for the Umpire's proposal" in text
    assert "Probability: —   Server roll: —   Outcome: —" in text
    assert "nothing yet: Effects are recorded after the ruling" in text
    assert state.argument() is not None and state.argument().adjudication is None


def test_cons_track_cons_passes_pending_and_unselected_actors() -> None:
    script = Script(["north", "south", "east", "west"])
    script.start()
    argument = script.argument("north", challengers=["south", "east"])
    script.con("south", argument, "Too risky.")
    script.passed("south", argument)
    state = scripted(script)
    view = state.argument()

    assert view is not None and view.sampled
    assert [view.con_status(a) for a in ("south", "east", "west")] == [
        "con", "pending", "not selected"]
    assert state.pending() == ["east"]
    text = frame(state, "adjudication")
    assert "Too risky." in text and "PENDING" in text and "not selected to challenge" in text

    script.passed("east", argument)
    assert scripted(script).argument().con_status("east") == "passed"  # type: ignore[union-attr]


def test_reply_phase_and_automatic_rulings() -> None:
    script = Script(["north", "south"])
    script.start()
    argument = script.argument("north")
    script.con("south", argument, "No.")
    script.passed("south", argument)
    script.add("cons_revealed", None, {"argument_id": argument["id"], "con_ids": []})
    script.phase("reply")
    state = scripted(script)
    assert state.argument().stage == "reply"  # type: ignore[union-attr]
    assert state.pending() == ["north"] and "REPLY DUE" in frame(state)
    assert state.stage_of("reply") == "current"

    script.add("owner_reply_submitted", "north", {"argument_id": argument["id"]},
               {"argument": argument, "reply": {"argument_id": argument["id"],
                                                "actor_id": "north",
                                                "points": [{"con_id": "x", "text": "Yes."}]}})
    script.phase("adjudication")
    script.adjudicate(argument, "automatic_failure", None, None, False)
    state = scripted(script)
    text = frame(state, "adjudication")

    assert "OWNER REPLY" in text and "Yes." in text
    assert "automatic failure" in text and "No roll" in text and "FAILURE" in text
    assert "Server roll" not in text
    assert "no Effect recorded for this Argument" in text


def test_previous_rulings_are_selectable_and_enter_shows_them_in_full(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items)
    handle_key(state, "3")
    ids = list(state.arguments)
    handle_key(state, "up")
    handle_key(state, "up")

    assert state.ui.argument_id == ids[-3]
    first = state.arguments[ids[-3]]
    text = frame(state)
    assert f"Argument {len(ids) - 2}/{len(ids)}" in text
    handle_key(state, "enter")
    _, lines = detail_content(state)
    detail = "\n".join(line.plain for line in lines)
    assert first.action in detail and "RULING" in detail
    handle_key(state, "esc")
    for _ in ids:
        handle_key(state, "down")
    assert state.ui.argument_id is None  # back to following the latest


# --- World State and honesty about data ---------------------------------------------------------


def test_world_lists_effects_with_lifecycle_and_scenario(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items)
    text = frame(state, "world")

    assert {e.id for e in state.active_effects()} == {e["id"] for e in snapshot["effects"]}
    assert len(state.ended_effects()) == 2
    assert "deterministic-game v1" in text and "Counted from the event log" in text
    assert "Whole run" in text  # a replay at its end labels its totals as the whole run's
    handle_key(state, "4")
    for _ in state.effects:
        handle_key(state, "down")
    assert "scenario setup" in frame(state)  # the selection scrolls to the oldest Effects


def test_no_fabricated_world_metrics(game) -> None:
    """Placeholder gauges from the design mock-up appear only if the game's own text has them."""
    snapshot, items = game
    corpus = str(items).lower() + str(snapshot).lower()
    for upto in (40, None):
        state = replayed(snapshot, items, upto)
        for screen in SCREENS:
            text = frame(state, screen).lower()
            for word in ("tension", "stability", "military posture", "goals", "region map",
                         "thinking", "%"):
                assert word not in text or word in corpus, (screen, word)


# --- replay correctness -------------------------------------------------------------------------


def test_an_early_replay_frame_never_shows_the_future(game) -> None:
    snapshot, items = game
    upto = index_of(items, "phase_started", phase="cons") + 1
    state = replayed(snapshot, items, upto)

    assert state.ruling is None and len(state.arguments) == 1
    assert all(e.created_seq == 0 for e in state.effects.values())
    assert state.status == "running" and state.turn == 1
    assert state.arguments_used == {items[upto - 2]["actor_id"]: 1}
    # The private setup Effect that expires in turn 3 is still active this early.
    reserve = next(e for e in state.effects.values() if e.expires_turn == 3)
    assert reserve.active and reserve.private
    final_effect = items[index_of(items, "effect_created", -1)]["refs"]["effect"]["description"]
    for screen in SCREENS:
        text = frame(state, screen)
        assert "game complete" not in text and "GAME COMPLETE" not in text
        assert final_effect not in text
        assert "calls " not in text  # final run totals stay off a replay's header


def test_effects_are_active_at_creation_although_refs_are_read_later(game) -> None:
    """Feed refs are joined when read; an Effect deactivated later still starts active."""
    snapshot, items = game
    ended = index_of(items, "effect_deactivated", 1)
    effect_id = items[ended]["payload"]["effect_id"]
    created = next(i for i, item in enumerate(items) if item["type"] == "effect_created"
                   and item["payload"]["effect_id"] == effect_id)
    assert items[created]["refs"]["effect"]["active"] is False  # read at the end of the game

    assert replayed(snapshot, items, created + 1).effects[effect_id].active
    after = replayed(snapshot, items, ended + 1).effects[effect_id]
    assert not after.active and after.ended_reason == "superseded"


def test_replay_and_live_fold_agree_at_the_end(game) -> None:
    snapshot, items = game
    replay = replayed(snapshot, items)
    live = ViewState(live=True)
    live.take_snapshot(snapshot)
    live.seed_effects(setup_effects(snapshot, items))
    for item in items:
        live.apply(item)
    live.take_snapshot(snapshot)

    assert (replay.turn, replay.status, replay.cursor) == (live.turn, live.status, live.cursor)
    assert {k: v.active for k, v in replay.effects.items()} == {
        k: v.active for k, v in live.effects.items()}
    assert dict(replay.links) == dict(live.links)


def test_a_paused_game_resumes_on_the_next_event() -> None:
    script = Script(["north", "south"])
    script.start()
    argument = script.argument("north")
    script.passed("south", argument)
    script.phase("adjudication")
    script.add("game_paused", None, {"reason": "adjudicator_unavailable",
                                     "argument_id": argument["id"]})
    state = scripted(script)

    assert state.status == "paused" and state.pending() == []
    text = frame(state)
    assert "GAME PAUSED" in text and "adjudicator unavailable" in text
    assert "game paused" in frame(state, "adjudication")

    script.adjudicate(argument, "probabilistic", 0.6, 0.2, True)
    resumed = scripted(script)
    assert resumed.status == "running" and resumed.pause_reason is None


# --- empty and missing data ---------------------------------------------------------------------


def test_screens_draw_with_no_events_and_with_no_snapshot() -> None:
    script = Script(["north", "south", "east"])
    lobby = scripted(script, live=True, status="lobby", turn=0)
    texts = {screen: frame(lobby, screen) for screen in SCREENS}

    assert "No events yet." in texts["live"] and "No ruling yet." in texts["live"]
    assert "No Argument submitted yet." in texts["adjudication"]
    assert "No messages yet." in texts["network"]
    assert "No Effects recorded." in texts["world"]
    for screen in SCREENS:
        for width, height in SIZES:
            assert TITLES[screen] in frame(ViewState(), screen, width, height)


# --- navigation and the loops -------------------------------------------------------------------


def test_keys_cover_screens_overlays_filters_and_quit(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items)

    assert handle_key(state, "f") and state.ui.feed_focus and state.ui.screen == "live"
    assert handle_key(state, "esc") and not state.ui.feed_focus
    assert handle_key(state, "?") and state.ui.help and "KEYS" in frame(state)
    assert handle_key(state, "esc") and not state.ui.help
    assert handle_key(state, "s") and state.secret_only
    assert handle_key(state, "p") and state.paused
    assert handle_key(state, "a") and state.actor_filter == state.actors[0]["id"]
    assert handle_key(state, "2") and state.ui.actor_index == 0
    handle_key(state, "down")
    assert state.ui.actor_index == 1
    assert handle_key(state, None)
    assert not handle_key(state, "q")


def test_terminal_keys_are_decoded() -> None:
    assert viewer.decode_keys("\x1b[A\x1b[Bq\n\r\x1b") == [
        "up", "down", "q", "enter", "enter", "esc"]
    assert viewer.decode_keys("\x1bOA\x1b[5~2") == ["up", "2"]


def test_live_pause_holds_drawing_and_resume_catches_up(played, reader) -> None:
    """Pausing stops folding new events into the view; the game is never asked to wait."""
    _, game_id = played
    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=True,
                      color_system=None)
    state = ViewState(live=True)
    state.take_snapshot(observer.snapshot(reader, game_id))
    state.paused = True

    def run_for(seconds: float) -> None:
        stop = threading.Event()
        threading.Timer(seconds, stop.set).start()
        viewer._follow(reader, game_id, state, console, poll_seconds=0.01, stop=stop,
                       keys=False)

    run_for(0.3)
    assert state.cursor == 0 and not state.lines
    state.paused = False
    run_for(0.3)
    assert state.cursor == observer.snapshot(reader, game_id)["cursor"]


def test_replay_loop_steps_only_on_keys_in_step_mode(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items, 0)
    keys = iter([" ", "n", None, "3", "q"])

    from contextlib import contextmanager

    @contextmanager
    def fake_keyboard(enabled: bool):
        yield lambda timeout: next(keys, "q")

    console = Console(file=io.StringIO(), width=120, height=40, force_terminal=True,
                      color_system=None)
    original = viewer._keyboard
    viewer._keyboard = fake_keyboard  # type: ignore[assignment]
    try:
        viewer._replay(state, items, console, speed=0, step=True, stop=threading.Event(),
                       keys=True)
    finally:
        viewer._keyboard = original  # type: ignore[assignment]

    assert state.ui.replay_index == 2 and state.cursor == items[1]["seq"]
    assert state.ui.screen == "adjudication"


# --- terminals, safety and the CLI --------------------------------------------------------------


def test_ascii_frames_are_pure_ascii(game) -> None:
    snapshot, items = game
    state = replayed(snapshot, items, 120)
    state.ui.ascii = True
    for screen in SCREENS:
        for width, height in SIZES:
            assert frame(state, screen, width, height).isascii(), (screen, width)


def test_off_a_terminal_watch_prints_the_chosen_screen_once(played, reader) -> None:
    _, game_id = played
    for screen in SCREENS:
        console = Console(file=io.StringIO(), width=120, color_system=None)
        viewer.watch(reader, game_id, console=console, screen=screen, ascii=True)
        text = console.file.getvalue()  # type: ignore[attr-defined]
        assert TITLES[screen] in text and text.isascii()


def test_the_viewer_never_writes_the_game_database(played) -> None:
    path, game_id = played
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    engine = observer.read_only_engine(path)
    try:
        for screen in SCREENS:
            console = Console(file=io.StringIO(), width=100, color_system=None)
            viewer.watch(engine, game_id, console=console, screen=screen)
    finally:
        engine.dispose()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


def test_a_failing_panel_or_screen_is_contained(game, monkeypatch) -> None:
    snapshot, items = game
    state = replayed(snapshot, items)

    def broken(width: int, height: int):
        raise RuntimeError("boom")

    console = Console(file=io.StringIO(), width=60, color_system=None)
    console.print(Sized(broken, name="actors"))
    assert "actors unavailable: RuntimeError: boom" in console.file.getvalue()  # type: ignore

    def broken_screen(*args):
        raise ValueError("bad layout")

    monkeypatch.setitem(frame_module.SCREEN_BUILDERS, "world", broken_screen)
    text = frame(state, "world")
    assert "screen unavailable: ValueError: bad layout" in text and "q quit" in text


def test_cli_watch_opens_on_a_chosen_screen(played, monkeypatch) -> None:
    path, game_id = played
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(path))

    result = CliRunner().invoke(amaw_cli.app, ["watch", str(game_id), "--screen", "world",
                                               "--ascii"])
    assert result.exit_code == 0, result.output
    assert "WORLD STATE" in result.output and result.output.isascii()

    bad = CliRunner().invoke(amaw_cli.app, ["watch", str(game_id), "--screen", "map"])
    assert bad.exit_code == 2 and "--screen must be one of" in bad.output
