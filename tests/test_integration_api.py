"""T-036 — the API integration test (testing.md §19).

The T-026 deterministic six-turn run with the **Actor half moved onto HTTP**. A
``TestClient(create_app(engine=db_engine))`` and the direct service calls share the *one*
``db_engine`` (else the game would split across two databases and nothing would line up), so
every Actor move is proven over the real player endpoints (T-029…T-035) while the game still
reaches the same T-026 outcomes.

**Hybrid harness (the crux, api.md §16 API-INV-008).** The player HTTP surface deliberately
cannot create a game, start it, adjudicate, or advance the turn — those are server/admin/Umpire
authority whose endpoints are T-042 (adjudication) and T-043 (admin API §14). So, exactly as the
offline T-026 driver already does, this harness plays:

    * **over HTTP (the Actor, via TestClient):** ``GET /context``, ``POST /messages``,
      ``POST /ready``, ``POST /arguments``, ``POST …/cons``, ``POST …/pass``, ``GET /messages``,
      ``GET /events``, ``GET /notes`` — what T-036 proves;
    * **through the service (server/admin/Umpire stand-ins):**
      ``scenarios.create_game_from_scenario``, ``game_service.start_game``,
      ``game_service.resolve_adjudication`` and ``game_service.advance_after_resolution`` — each
      annotated below with the endpoint (T-042/T-043) that will one day replace it.

The driver dispatches by phase read from ``GET /context`` / endpoint responses — never by
peeking at rows. Because RNG draws and Effect creation depend only on the seed and the Umpire's
proposals (not on Message/Argument text), a driver that submits valid moves in the T-026 shape
reproduces the 18-adjudication schedule, the same RNG stream and the same final Effects.

**Assertions:** the documented HTTP status/shape at each Actor step (api.md §6–§13), then the
nine testing.md §18 outcome assertions — read through the API where the endpoint exists (game
status/turn/phase via ``GET /context``), and via the database where none does yet (Arguments,
adjudications, RNG, Effects; export is T-043). Private data is checked for leaks **through the
endpoints** (the new coverage this task adds over T-026's ``app.visibility`` checks).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlmodel import select

from agents.scripted import (
    deterministic_umpire,
)
from app import database, game_service, scenarios
from app.database import session_scope
from app.models import (
    AdjudicationRecord,
    Effect,
    Game,
    GameStatus,
)
from tests import golden
from tests.api_helpers import FIXTURE_DIR, bearer, client


# --- HTTP driver -----------------------------------------------------------------------


def _context(c: TestClient, gid: uuid.UUID, token: str) -> dict:
    """GET /context for one Actor, asserting the documented 200 + shape (api.md §6.1)."""
    resp = c.get(f"/games/{gid}/context", headers=bearer(token))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # Documented envelope keys (api.md §6.1) — server-computed available_actions (API-INV-009).
    for key in ("turn", "phase", "status", "active_actor_id", "you", "available_actions"):
        assert key in body, f"missing {key} in context: {body}"
    assert body["you"]["actor_id"] is not None
    return body


def _http_diplomacy(
    c: TestClient,
    gid: uuid.UUID,
    tokens: dict[str, str],
    turn_order: list[str],
    *,
    verify_reset: bool,
) -> None:
    """Diplomacy over HTTP: each Actor sends one private Message, then all ready to close.

    Each Actor references an element of its *own* ``you.private_information`` (preserving the
    T-025 scripted property so outcomes match §18). On the first turn ``verify_reset`` also runs
    TEST-PHASE-003 over HTTP: ready one Actor, have another privately message it, and confirm its
    ``GET /context`` shows ``ready`` back in ``available_actions`` / ``requires_input`` before
    diplomacy closes. Closing the phase is the *server's* transition (API-INV-008); the driver
    only signals readiness.
    """
    for actor in turn_order:
        ctx = _context(c, gid, tokens[actor])
        assert "send_message" in ctx["available_actions"]
        secret = ctx["you"]["private_information"][0]
        recipient = turn_order[(turn_order.index(actor) + 1) % len(turn_order)]
        resp = c.post(
            f"/games/{gid}/messages",
            headers=bearer(tokens[actor]),
            json={
                "recipients": [recipient],
                "public": False,
                "text": f"{ctx['you']['name']} confides to {recipient}: {secret}",
            },
        )
        assert resp.status_code == 200, resp.text
        msg = resp.json()["message"]
        assert msg["sender_actor_id"] == actor  # identity from the token (API-INV-001)
        assert msg["recipients"] == [recipient]
        assert msg["public"] is False

    if verify_reset:
        arkania, belvar = turn_order[0], turn_order[1]
        ready = c.post(f"/games/{gid}/ready", headers=bearer(tokens[belvar]), json={})
        assert ready.status_code == 200 and ready.json()["ready"] is True
        # Once ready, the Actor is no longer offered `ready` (server-computed actions).
        assert "ready" not in _context(c, gid, tokens[belvar])["available_actions"]
        # A new private Message to the ready recipient clears its readiness (TEST-PHASE-003).
        reset = c.post(
            f"/games/{gid}/messages",
            headers=bearer(tokens[arkania]),
            json={"recipients": [belvar], "public": False, "text": "revised terms"},
        )
        assert reset.status_code == 200, reset.text
        belvar_ctx = _context(c, gid, tokens[belvar])
        assert belvar_ctx["phase"] == "diplomacy"
        assert "ready" in belvar_ctx["available_actions"]
        assert belvar_ctx["requires_input"] is True

    for actor in turn_order:
        ready = c.post(f"/games/{gid}/ready", headers=bearer(tokens[actor]), json={})
        assert ready.status_code == 200, ready.text
        assert ready.json()["ready"] is True


def _http_argument_cycle(
    engine: Engine,
    c: TestClient,
    gid: uuid.UUID,
    tokens: dict[str, str],
    turn_order: list[str],
    active: str,
    umpire,  # noqa: ANN001 — app.umpire.Umpire
) -> None:
    """One Actor's full cycle over HTTP: Argument → opponents con+pass → (server) resolve+advance.

    ``active`` is the authoritative active Actor read from ``GET /context`` — never chosen here.
    The Argument id is captured from ``POST /arguments`` and used for the opponents' Cons; the
    driver never re-derives "the current Argument" from rows.
    """
    ctx = _context(c, gid, tokens[active])
    assert "make_argument" in ctx["available_actions"]
    assert ctx["requires_input"] is True
    objective = ctx["you"]["objectives"][0]

    resp = c.post(
        f"/games/{gid}/arguments",
        headers=bearer(tokens[active]),
        json={
            "action": f"{ctx['you']['name']} acts to advance its objectives",
            "desired_effect": f"{active} advances its position",
            "pros": [objective],
        },
    )
    assert resp.status_code == 200, resp.text
    arg = resp.json()["argument"]
    assert arg["actor_id"] == active
    assert arg["status"] == "open_for_cons"  # api.md §8.1 — server transitions to Cons
    argument_id = arg["id"]
    assert _context(c, gid, tokens[active])["phase"] == "cons"

    for opponent in [a for a in turn_order if a != active]:
        octx = _context(c, gid, tokens[opponent])
        assert "make_con" in octx["available_actions"]
        assert "pass_con" in octx["available_actions"]
        # The current Argument is public game state — every opponent sees it to Con it.
        assert octx["current_argument"]["id"] == argument_id

        con = c.post(
            f"/games/{gid}/arguments/{argument_id}/cons",
            headers=bearer(tokens[opponent]),
            json={"reason": f"{opponent} questions the wisdom of {active}'s move"},
        )
        assert con.status_code == 200, con.text
        con_body = con.json()["con"]
        assert con_body["argument_id"] == argument_id
        assert con_body["actor_id"] == opponent

        passed = c.post(
            f"/games/{gid}/arguments/{argument_id}/pass",
            headers=bearer(tokens[opponent]),
            json={},
        )
        assert passed.status_code == 200, passed.text
        pass_body = passed.json()
        assert pass_body["passed"] is True
        assert pass_body["actor_id"] == opponent
        assert pass_body["argument_id"] == argument_id

    # The final eligible pass closes Cons to adjudication (the service's own transition).
    assert _context(c, gid, tokens[active])["phase"] == "adjudication"

    # --- Server/Umpire steps: no HTTP endpoint yet. Adjudication is T-042; turn advancement
    # is the admin API (§14), T-043. The offline T-026 driver calls these the same way.
    game_service.resolve_adjudication(engine, gid, umpire)
    game_service.advance_after_resolution(engine, gid)


def _play_api_game(
    engine: Engine,
) -> tuple[scenarios.LoadedScenario, scenarios.GameCreation, TestClient]:
    """Play the canonical fixture to completion, Actor moves over HTTP; return the pieces."""
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    # --- Server/admin steps: game creation + Seats and game start have no player endpoint;
    # they are the admin API (§14), T-043. The HTTP client below shares this same engine.
    creation = scenarios.create_game_from_scenario(engine, scenario)
    game_service.start_game(engine, creation.game_id)

    c = client(engine)
    gid = creation.game_id
    tokens = creation.seat_tokens
    turn_order = list(scenario.game.turn_order)
    umpire = deterministic_umpire()  # the fixture's fixed FakeUmpire, unchanged from T-026

    limit = (scenario.game.max_turns + 1) * (len(turn_order) + 2)
    reset_verified = False
    for _ in range(limit):
        game = _context(c, gid, tokens[turn_order[0]])
        if game["status"] == GameStatus.complete.value:
            return scenario, creation, c
        phase = game["phase"]
        if phase == "diplomacy":
            _http_diplomacy(
                c, gid, tokens, turn_order, verify_reset=not reset_verified
            )
            reset_verified = True
        elif phase == "argument":
            active = game["active_actor_id"]
            assert active is not None, "argument phase with no active Actor"
            _http_argument_cycle(engine, c, gid, tokens, turn_order, active, umpire)
        else:  # cons/adjudication are transient within a cycle — never observed at the top.
            raise AssertionError(f"driver observed unexpected top-level phase {phase!r}")

    raise AssertionError("API-driven game did not complete within the iteration bound")


@pytest.fixture(scope="module")
def played(
    tmp_path_factory: pytest.TempPathFactory,
) -> Iterator[tuple[Engine, scenarios.LoadedScenario, scenarios.GameCreation, TestClient]]:
    """Play the fixture over HTTP once for the module; the tests below only read the result.

    The game is deterministic, so one play serves every §19 expectation (I-135). The tests
    only read (GETs and database queries), which keeps them independent of execution order
    (testing.md §3). ``test_int_api_19_deterministic_across_runs`` plays its own two games.
    """
    engine = database.engine_for_path(tmp_path_factory.mktemp("int-api-19") / "amaw-test.db")
    database.initialize_database(engine)
    try:
        yield engine, *_play_api_game(engine)
    finally:
        engine.dispose()


# --- game completes / turn == 6 (read through GET /context) -----------------------------


def test_int_api_19_game_completes_at_turn_six(played) -> None:
    db_engine, scenario, creation, c = played
    # Read the terminal state back through the API (the endpoint that exists).
    ctx = _context(c, creation.game_id, creation.seat_tokens[scenario.game.turn_order[0]])
    assert ctx["status"] == "complete"
    assert ctx["phase"] == "complete"
    assert ctx["turn"] == 6
    assert ctx["active_actor_id"] is None
    assert ctx["available_actions"] == []  # nothing legal on a completed game


# --- 18 primary Arguments accepted / no budget violations (no endpoint — via DB) --------


def test_int_api_19_eighteen_arguments_and_no_budget_violations(played) -> None:
    db_engine, scenario, creation, _ = played  # all 18 submitted over HTTP
    golden.assert_arguments_and_budget(db_engine, creation.game_id, scenario.game.turn_order)


# --- all expected adjudications exist ---------------------------------------------------


def test_int_api_19_all_eighteen_adjudications_exist(played) -> None:
    db_engine, _, creation, _ = played
    golden.assert_adjudications(db_engine, creation.game_id)


# --- RNG sequence matches fixture ------------------------------------------------------


def test_int_api_19_rng_sequence_matches_fixture(played) -> None:
    db_engine, scenario, creation, _ = played
    # The server drew every roll (API-INV-006).
    golden.assert_rng_sequence(db_engine, creation.game_id, scenario.game.turn_order)


# --- final active Effects match fixture ------------------------------------------------


def test_int_api_19_final_active_effects_match_fixture(played) -> None:
    db_engine, scenario, creation, _ = played
    golden.assert_final_effects(db_engine, creation.game_id, scenario)


# --- private data never leaked, through the endpoints (TEST-VIS-003 at the API boundary) -


def test_int_api_19_no_private_data_leak_via_endpoints(played) -> None:
    db_engine, scenario, creation, c = played
    gid = creation.game_id
    tokens = creation.seat_tokens
    turn_order = list(scenario.game.turn_order)

    # Each Actor's secret text (brief/objectives/private_information).
    secrets = {
        actor_id: {
            *scenario.actors[actor_id].objectives,
            *scenario.actors[actor_id].private_information,
            scenario.actors[actor_id].private_brief,
        }
        for actor_id in turn_order
    }

    for viewer in turn_order:
        ctx = c.get(f"/games/{gid}/context", headers=bearer(tokens[viewer]))
        assert ctx.status_code == 200
        blob = ctx.text  # the entire serialized context this Actor receives
        for foreign in turn_order:
            if foreign == viewer:
                continue
            for secret in secrets[foreign]:
                assert secret not in blob, (
                    f"{viewer}'s context leaked {foreign}'s secret: {secret!r}"
                )

        # Messages: a viewer's GET /messages never carries a private Message it is not party to.
        msgs = c.get(f"/games/{gid}/messages", headers=bearer(tokens[viewer]))
        assert msgs.status_code == 200
        for message in msgs.json()["messages"]:
            if not message["public"]:
                assert (
                    viewer == message["sender_actor_id"]
                    or viewer in message["recipients"]
                )

        # Notes: none were created over HTTP; no Actor sees another's Notes.
        notes = c.get(f"/games/{gid}/notes", headers=bearer(tokens[viewer]))
        assert notes.status_code == 200
        assert notes.json()["notes"] == []

    # A private message_sent never appears in a non-party's GET /events (API-INV-010 / D-014).
    all_private: dict[str, set[str]] = {}
    for viewer in turn_order:
        events = c.get(f"/games/{gid}/events", headers=bearer(tokens[viewer]))
        assert events.status_code == 200
        all_private[viewer] = {
            e["payload"].get("message_id")
            for e in events.json()["events"]
            if e["type"] == "message_sent"
        }
    # Concretely: cestia never sees the arkania→belvar private message_sent, and vice versa.
    with session_scope(db_engine) as session:
        from app.models import Message

        private_ids_by_party: dict[str, set[str]] = {a: set() for a in turn_order}
        saw_private = False
        for m in session.exec(select(Message).where(Message.game_id == gid)).all():
            if m.public:
                continue
            saw_private = True
            for party in {m.sender_actor_id, *m.recipients}:
                private_ids_by_party[party].add(str(m.id))
    assert saw_private, "fixture run produced no private Messages to check"
    for viewer in turn_order:
        # Every private message event this viewer sees is one it is a party to.
        assert all_private[viewer] <= private_ids_by_party[viewer]


# --- private Effect visibility through GET /context (mid-game, while it is active) -------


def test_int_api_19_private_effect_not_leaked_via_context(db_engine: Engine) -> None:
    """The arkania-only starting Effect is visible to arkania's context alone (turn 1, active).

    Checked through the endpoint at a turn where the Effect is still active — at completion it
    has expired (turn 3) and ``GET /context`` returns active Effects only, so the leak must be
    ruled out while the Effect is live.
    """
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(db_engine, scenario)  # server step (T-043)
    game_service.start_game(db_engine, creation.game_id)  # server step (T-043)
    c = client(db_engine)
    gid = creation.game_id

    def private_descriptions(actor: str) -> list[str]:
        resp = c.get(f"/games/{gid}/context", headers=bearer(creation.seat_tokens[actor]))
        assert resp.status_code == 200
        return [e["description"] for e in resp.json()["effects"] if e["visibility"] == "private"]

    assert private_descriptions("arkania") == [
        "Arkania's reserve mobilisation plan is incomplete."
    ]
    assert private_descriptions("belvar") == []
    assert private_descriptions("cestia") == []


# --- private-message readiness reset over HTTP (TEST-PHASE-003 at the API boundary) -----


def test_int_api_19_private_message_readiness_reset_over_http(db_engine: Engine) -> None:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(db_engine, scenario)  # server step (T-043)
    game_service.start_game(db_engine, creation.game_id)  # server step (T-043)
    c = client(db_engine)
    gid = creation.game_id
    tokens = creation.seat_tokens

    ready = c.post(f"/games/{gid}/ready", headers=bearer(tokens["belvar"]), json={})
    assert ready.status_code == 200 and ready.json()["ready"] is True
    assert "ready" not in _context(c, gid, tokens["belvar"])["available_actions"]

    reset = c.post(
        f"/games/{gid}/messages",
        headers=bearer(tokens["arkania"]),
        json={"recipients": ["belvar"], "public": False, "text": "revised terms"},
    )
    assert reset.status_code == 200, reset.text

    belvar_ctx = _context(c, gid, tokens["belvar"])
    assert belvar_ctx["phase"] == "diplomacy"  # phase did not close
    assert "ready" in belvar_ctx["available_actions"]  # belvar is waited on again
    assert belvar_ctx["requires_input"] is True


# --- event sequence structurally valid, and visibility-filtered via GET /events ---------


def test_int_api_19_event_stream_structurally_valid_and_filtered(played) -> None:
    db_engine, scenario, creation, c = played
    gid = creation.game_id
    tokens = creation.seat_tokens

    # The authoritative stream's structure, read via the DB, is the §18 golden check.
    ids = golden.assert_event_structure(db_engine, gid)

    # Read through GET /events: each Actor's stream is visibility-filtered, monotonic, and
    # advertises last_event_id over visible events only (api.md §13.1).
    for actor in scenario.game.turn_order:
        resp = c.get(f"/games/{gid}/events", headers=bearer(tokens[actor]))
        assert resp.status_code == 200, resp.text
        body = resp.json()
        visible_ids = [e["id"] for e in body["events"]]
        assert visible_ids == sorted(visible_ids)  # monotonic
        assert set(visible_ids) <= set(ids)  # a subset of the authoritative stream
        expected_cursor = visible_ids[-1] if visible_ids else 0
        assert body["last_event_id"] == expected_cursor
        # Public lifecycle events are visible to every Actor (the stream is not empty).
        assert any(e["type"] == "game_completed" for e in body["events"])


# --- determinism across repeated runs (normalized state) -------------------------------


@pytest.mark.slow
def test_int_api_19_deterministic_across_runs(db_engine: Engine, tmp_path: Path) -> None:
    """Two independent API-driven runs produce the same normalized outcomes (as T-026)."""

    def normalized(engine: Engine, game_id: uuid.UUID) -> tuple:
        with session_scope(engine) as session:
            rolls = tuple(
                sorted(r.roll for r in session.exec(select(AdjudicationRecord)).all())
            )
            active_effects = tuple(
                sorted(
                    e.description
                    for e in session.exec(
                        select(Effect).where(Effect.game_id == game_id)
                    ).all()
                    if e.active
                )
            )
            game = session.get(Game, game_id)
            return (game.turn, game.status.value, game.phase.value, rolls, active_effects)

    _, first, _ = _play_api_game(db_engine)

    second_engine = database.engine_for_path(tmp_path / "amaw-second.db")
    database.create_db_and_tables(second_engine)
    try:
        _, second, _ = _play_api_game(second_engine)
        assert normalized(db_engine, first.game_id) == normalized(
            second_engine, second.game_id
        )
    finally:
        second_engine.dispose()
