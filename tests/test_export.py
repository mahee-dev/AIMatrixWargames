"""Admin endpoints and JSON game export (T-043).

The administrative export is the *unfiltered* counterpart to the Actor-facing API: it
deliberately includes game-scoped private data — objectives, private Messages, private Notes —
each with its ownership/visibility metadata, and it is reachable **only** with the separate
admin token, never a Seat token (api.md §14/§15.1, TRD §27, TEST-NOTE-003 / TEST-AUTH-004).

The suite proves: one export builder feeds both the HTTP endpoint and ``amaw export`` (same
record); the export is complete over a driven game and leaks no Seat token / admin token / API
key and no chain-of-thought; the §14 orchestration wrappers drive the real service transitions
behind admin auth and never let the read-only export advance state. The export builder's import
boundary is in ``test_architecture_boundaries.py``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from typer.testing import CliRunner

from agents.scripted import build_scripted_players, deterministic_umpire, run_deterministic_game
from app import export as export_module
from app import game_service, scenarios
from app.api import create_app

import amaw_cli
from tests.api_helpers import FIXTURE_DIR, bearer

ACTORS = ("arkania", "belvar", "cestia")
ADMIN_TOKEN = "admin-secret-token"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """The separate admin token every §14/§15 route authorises against (api.md §2.4)."""
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)


# --- harness ---------------------------------------------------------------------------


def _seed(engine: Engine) -> scenarios.GameCreation:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    return scenarios.create_game_from_scenario(engine, scenario)


def _client(engine: Engine) -> TestClient:
    # An injected deterministic Umpire so /admin/.../advance can resolve offline (no provider).
    app = create_app(engine=engine, umpire=deterministic_umpire())
    return TestClient(app, raise_server_exceptions=False)


def _admin(headers: dict[str, str] | None = None) -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}", **(headers or {})}


def _play_full_game(engine: Engine) -> scenarios.GameCreation:
    """Create, start and run the canonical fixture to completion, offline and deterministic."""
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    creation = scenarios.create_game_from_scenario(engine, scenario)
    game_service.start_game(engine, creation.game_id)
    players = build_scripted_players(scenario.game.turn_order)
    run_deterministic_game(engine, creation.game_id, players, deterministic_umpire())
    return creation


def _walk_keys(obj: object) -> set[str]:
    """Every dict key that appears anywhere in a nested JSON-like structure."""
    keys: set[str] = set()
    if isinstance(obj, dict):
        for key, value in obj.items():
            keys.add(key)
            keys |= _walk_keys(value)
    elif isinstance(obj, list):
        for item in obj:
            keys |= _walk_keys(item)
    return keys


# --- TEST-NOTE-003 — administrative export includes Notes with ownership --------------


def test_note_003_admin_export_includes_notes_with_ownership(db_engine: Engine) -> None:
    creation = _play_full_game(db_engine)
    gid = creation.game_id

    # Notes owned by two different Actors (private scratch space; legal in any phase).
    game_service.save_note(db_engine, gid, actor_id="arkania", text="arkania private note")
    game_service.save_note(db_engine, gid, actor_id="belvar", text="belvar private note")

    c = _client(db_engine)

    # The administrative export includes every Note with its owning actor_id + visibility.
    export = c.get(f"/admin/games/{gid}/export", headers=_admin())
    assert export.status_code == 200, export.text
    body = export.json()
    notes = {(n["actor_id"], n["text"]): n for n in body["notes"]}
    assert ("arkania", "arkania private note") in notes
    assert ("belvar", "belvar private note") in notes
    for note in body["notes"]:
        assert note["actor_id"] in ACTORS  # ownership metadata retained
        assert note["visibility"] == "private"  # visibility metadata retained

    # Notes remain inaccessible through another Actor's Seat-authenticated endpoints (T-033).
    a_notes = c.get(f"/games/{gid}/notes", headers=bearer(creation.seat_tokens["arkania"]))
    assert a_notes.status_code == 200
    texts = {n["text"] for n in a_notes.json()["notes"]}
    assert "arkania private note" in texts
    assert "belvar private note" not in texts  # B's Note never reaches A

    # A Seat token can never authorize the export (TEST-AUTH-004 parity).
    seat_export = c.get(
        f"/admin/games/{gid}/export", headers=bearer(creation.seat_tokens["arkania"])
    )
    assert seat_export.status_code == 403
    assert seat_export.json()["error"]["code"] == "forbidden"

    # The export must not claim or require hidden model chain-of-thought.
    keys = _walk_keys(body)
    assert not ({"chain_of_thought", "reasoning", "thoughts"} & keys)


# --- export completeness + no secret leak ----------------------------------------------


def test_export_is_complete_over_a_driven_game(db_engine: Engine) -> None:
    creation = _play_full_game(db_engine)
    body = _client(db_engine).get(
        f"/admin/games/{creation.game_id}/export", headers=_admin()
    ).json()

    # Immutable configuration + seed (TRD §27 "under what immutable configuration it ran").
    assert body["game"]["scenario_id"] == "deterministic-game"
    assert body["game"]["seed"] == 424242
    assert body["game"]["max_turns"] == 6
    assert body["game"]["status"] == "complete"
    assert body["game"]["turn_order"] == list(ACTORS)

    # Actors carry objectives, briefs and private information (private data, retained).
    actors = {a["id"]: a for a in body["actors"]}
    assert set(actors) == set(ACTORS)
    assert actors["arkania"]["objectives"]
    assert actors["arkania"]["private_brief"]
    assert "private_information" in actors["arkania"]

    # 18 primary Arguments, each adjudicated; every probabilistic roll is recorded.
    assert len(body["arguments"]) == 18
    assert len(body["adjudications"]) == 18
    for adj in body["adjudications"]:
        assert "mode" in adj and "success" in adj and "rationale" in adj
        assert "success_effects" in adj and "failure_effects" in adj  # both branches
    assert any(adj["roll"] is not None for adj in body["adjudications"])

    # Effects include retained *inactive* rows (superseded/expired), never deleted (INV-EFFECT-004).
    assert body["effects"], "export must include Effects"
    assert any(e["active"] is False for e in body["effects"]), "retained inactive Effect expected"
    assert any(e["supersedes"] for e in body["effects"]), "supersession link expected"

    # Messages, Events present; every section is valid JSON already (json() parsed it).
    assert body["messages"]
    assert body["events"]
    assert body["events"] == sorted(body["events"], key=lambda e: e["id"])  # monotonic order

    # No secret anywhere: no Seat token, admin token or API-key-shaped value.
    blob = json.dumps(body)
    for token in creation.seat_tokens.values():
        assert token not in blob
    assert ADMIN_TOKEN not in blob
    assert "token_hash" not in _walk_keys(body)


def test_scn_005_admin_export_includes_stored_hashes_without_filesystem_paths(
    db_engine: Engine
) -> None:
    creation = _seed(db_engine)
    body = export_module.build_game_export(db_engine, creation.game_id)
    hashes = body["scenario_hashes"]
    assert len(hashes["scenario_content_sha256"]) == 64
    assert len(hashes["resource_manifest_sha256"]) == 64
    assert [entry["id"] for entry in hashes["resources"]] == [
        "arkania-intel",
        "background",
        "trade",
    ]
    assert all(len(entry["content_sha256"]) == 64 for entry in hashes["resources"])
    assert "path" not in _walk_keys(hashes)
    assert str(FIXTURE_DIR) not in json.dumps(hashes)


def test_export_absent_game_returns_404(db_engine: Engine) -> None:
    import uuid

    missing = uuid.uuid4()
    resp = _client(db_engine).get(f"/admin/games/{missing}/export", headers=_admin())
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


# --- one builder: endpoint == CLI ------------------------------------------------------


def test_endpoint_and_cli_produce_the_same_record(
    db_engine: Engine, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    creation = _play_full_game(db_engine)
    gid = creation.game_id

    endpoint_body = _client(db_engine).get(
        f"/admin/games/{gid}/export", headers=_admin()
    ).json()

    # `amaw export GAME_ID` reads the DB via the configured engine and prints the same record.
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(db_path))
    result = CliRunner().invoke(amaw_cli.app, ["export", str(gid)])
    assert result.exit_code == 0, result.output
    cli_body = json.loads(result.stdout)

    assert cli_body == endpoint_body  # exactly one serialisation


def test_cli_export_absent_game_exits_nonzero(
    db_engine: Engine, db_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import uuid

    monkeypatch.setenv("AMAW_DATABASE_PATH", str(db_path))
    result = CliRunner().invoke(amaw_cli.app, ["export", str(uuid.uuid4())])
    assert result.exit_code != 0


# --- §14 admin auth + orchestration wrappers -------------------------------------------


@pytest.mark.parametrize(
    ("method", "path_suffix"),
    [
        ("post", ""),  # POST /admin/games (create) — path built without the game id below
        ("post", "/start"),
        ("post", "/advance"),
        ("get", "/export"),
    ],
)
def test_admin_routes_reject_missing_and_seat_tokens(
    db_engine: Engine, method: str, path_suffix: str
) -> None:
    creation = _seed(db_engine)
    c = _client(db_engine)
    if path_suffix == "":
        url = "/admin/games"
    else:
        url = f"/admin/games/{creation.game_id}{path_suffix}"
    call = getattr(c, method)
    kwargs: dict[str, object] = {"json": {}} if method == "post" else {}

    # Missing token → 401 invalid_token (api.md §3.1).
    missing = call(url, **kwargs)
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "invalid_token"

    # A Seat token → 403 forbidden (TEST-AUTH-004): a Seat token never authorizes an admin route.
    seat = call(url, headers=bearer(creation.seat_tokens["arkania"]), **kwargs)
    assert seat.status_code == 403
    assert seat.json()["error"]["code"] == "forbidden"


def test_admin_create_start_advance_drive_the_service(db_engine: Engine) -> None:
    c = _client(db_engine)

    # POST /admin/games creates a LOBBY game from a scenario and hands back raw Seat tokens
    # out-of-band (INV-ACTOR-001) — the only place a raw token is ever returned.
    created = c.post(
        "/admin/games", headers=_admin(), json={"scenario": str(FIXTURE_DIR)}
    )
    assert created.status_code == 201, created.text
    payload = created.json()
    gid = payload["game_id"]
    assert set(payload["seat_tokens"]) == set(ACTORS)
    assert payload["status"] == "lobby"

    # Tokens actually authorize their Seats (proves they are real, not fabricated).
    ctx = c.get(f"/games/{gid}/context", headers=bearer(payload["seat_tokens"]["arkania"]))
    assert ctx.status_code == 200

    # POST /admin/games/{id}/start runs the real start_game transition.
    started = c.post(f"/admin/games/{gid}/start", headers=_admin(), json={})
    assert started.status_code == 200, started.text
    assert started.json()["turn"] == 1
    assert started.json()["phase"] == "diplomacy"


def _drive_to_adjudication(engine: Engine, creation: scenarios.GameCreation) -> object:
    """Start the game and drive turn-1 arkania's cycle up to the ADJUDICATION boundary.

    Uses the authoritative service directly for the Actor half (message-free: everyone readies,
    arkania argues, the two opponents con+pass) so the game sits in ``adjudication`` with a single
    unresolved Argument — exactly the state ``…/advance`` must resolve then advance from.
    """
    gid = creation.game_id
    game_service.start_game(engine, gid)
    for actor in ACTORS:
        game_service.ready(engine, gid, actor)
    arg = game_service.make_argument(
        engine,
        gid,
        actor_id="arkania",
        action="Begin mobilisation of reserve forces.",
        desired_effect="Reserve formations deploy toward the frontier.",
        pros=["Mobilisation plans already exist."],
    )
    for opponent in ("belvar", "cestia"):
        game_service.make_con(
            engine, gid, argument_id=arg.id, actor_id=opponent, reason=f"{opponent} objects."
        )
        game_service.pass_con(engine, gid, argument_id=arg.id, actor_id=opponent)
    return arg


def test_admin_advance_resolves_then_advances_through_the_service(db_engine: Engine) -> None:
    """`…/advance` at the adjudication boundary resolves the current Argument (server-owned RNG)
    and advances — the same two transitions the offline driver calls, not a re-implementation."""
    from app import rng
    from app.models import AdjudicationRecord

    creation = _seed(db_engine)
    arg = _drive_to_adjudication(db_engine, creation)
    gid = creation.game_id
    c = _client(db_engine)

    # Precondition: adjudication phase, no adjudication record yet.
    from app.database import session_scope

    with session_scope(db_engine) as session:
        assert session.get(AdjudicationRecord, arg.id) is None

    adv = c.post(f"/admin/games/{gid}/advance", headers=_admin(), json={})
    assert adv.status_code == 200, adv.text

    # resolve happened: exactly one AdjudicationRecord, with the server's own first roll.
    body = c.get(f"/admin/games/{gid}/export", headers=_admin()).json()
    assert len(body["adjudications"]) == 1
    record = body["adjudications"][0]
    assert record["argument_id"] == str(arg.id)
    # The server owns the draw: it must equal a fresh reconstruction of the first draw (INV-RNG-003).
    assert record["roll"] == pytest.approx(rng.draw(body["game"]["seed"], 0))

    # advance happened: the phase moved on to the next Actor's ARGUMENT (belvar).
    assert body["game"]["phase"] == "argument"
    assert body["game"]["active_actor_id"] == "belvar"


def test_admin_advance_without_umpire_configured_is_503(db_engine: Engine) -> None:
    # No Umpire injected → resolution at the adjudication boundary cannot proceed; the server
    # never fabricates a result (TRD §35). It reports adjudicator_unavailable rather than 500.
    creation = _seed(db_engine)
    _drive_to_adjudication(db_engine, creation)
    app = create_app(engine=db_engine)  # umpire=None
    c = TestClient(app, raise_server_exceptions=False)
    resp = c.post(f"/admin/games/{creation.game_id}/advance", headers=_admin(), json={})
    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "adjudicator_unavailable"


def test_export_does_not_advance_state(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    game_service.start_game(db_engine, creation.game_id)
    c = _client(db_engine)

    before = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    # Repeated exports (a read-only endpoint) never mutate authoritative state (API-INV-008).
    for _ in range(3):
        assert (
            c.get(f"/admin/games/{creation.game_id}/export", headers=_admin()).status_code
            == 200
        )
    after = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert (before["turn"], before["phase"]) == (after["turn"], after["phase"])
