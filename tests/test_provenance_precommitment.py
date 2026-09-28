"""I-078 — the server proves the run manifest preceded play (TEST-I078-007).

Manifest *creation* is lobby-only, the exact-retry idempotency of ``create_run_manifest`` is
preserved, and a Game created with ``require_run_manifest`` — an immutable creation-time
choice the autonomous runner always makes — cannot start without a sealed manifest. The
state-machine §14 start guard is unchanged for every Game that does not opt in.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app import game_service, provenance, scenarios
from app.api import create_app
from app.export import build_game_export
from tests.test_run_provenance import _configuration
from tests.api_helpers import FIXTURE_DIR


ADMIN_TOKEN = "admin-secret-token"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)


def _game(engine: Engine, **options):
    return scenarios.create_game_from_scenario(engine, scenarios.load_scenario(FIXTURE_DIR), **options)


def _admin() -> dict[str, str]:
    return {"Authorization": f"Bearer {ADMIN_TOKEN}"}


# --- (a) (b) (c): manifest creation is lobby-only; the exact retry survives --------------------


def test_i078_007a_manifest_creation_is_refused_once_the_game_has_left_lobby(db_engine: Engine):
    creation = _game(db_engine)
    game_service.start_game(db_engine, creation.game_id)

    with pytest.raises(provenance.ManifestConflict, match="lobby"):
        provenance.create_run_manifest(
            db_engine, creation.game_id, configuration=_configuration(commit="late")
        )
    assert build_game_export(db_engine, creation.game_id)["run_manifest"] is None


def test_i078_007b_exact_retry_returns_the_sealed_manifest_after_start(db_engine: Engine):
    creation = _game(db_engine)
    sealed = provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration(commit="one")
    )
    game_service.start_game(db_engine, creation.game_id)

    replay = provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration(commit="one")
    )
    assert replay.manifest_sha256 == sealed.manifest_sha256
    assert replay.created_at == sealed.created_at


def test_i078_007c_different_configuration_after_start_is_still_a_conflict(db_engine: Engine):
    creation = _game(db_engine)
    provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration(commit="one")
    )
    game_service.start_game(db_engine, creation.game_id)

    with pytest.raises(provenance.ManifestConflict, match="different"):
        provenance.create_run_manifest(
            db_engine, creation.game_id, configuration=_configuration(commit="two")
        )


def test_i078_007a_api_maps_late_sealing_to_409(db_engine: Engine):
    client = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    created = client.post("/admin/games", json={"scenario": str(FIXTURE_DIR)}, headers=_admin())
    game_id = created.json()["game_id"]
    assert client.post(f"/admin/games/{game_id}/start", json={}, headers=_admin()).status_code == 200

    response = client.post(
        f"/admin/games/{game_id}/run-manifest",
        json={"configuration": _configuration(commit="late")},
        headers=_admin(),
    )
    assert response.status_code == 409, response.text
    assert "lobby" in response.json()["error"]["message"]


# --- (d) (e) (f): the opt-in, immutable start guard ------------------------------------------


def test_i078_007d_require_run_manifest_is_persisted_at_creation_and_immutable(db_engine: Engine):
    client = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    flagged = client.post(
        "/admin/games", json={"scenario": str(FIXTURE_DIR), "require_run_manifest": True},
        headers=_admin(),
    )
    assert flagged.status_code == 201, flagged.text
    plain = client.post("/admin/games", json={"scenario": str(FIXTURE_DIR)}, headers=_admin())
    assert plain.status_code == 201

    def exported(response):
        return build_game_export(db_engine, uuid.UUID(response.json()["game_id"]))["game"]

    assert exported(flagged)["require_run_manifest"] is True
    assert exported(plain)["require_run_manifest"] is False
    # No endpoint changes it after creation: it is not a field of any mutation.
    assert not any("require_run_manifest" in route.path for route in client.app.routes)


def test_i078_007e_flagged_game_cannot_start_without_a_manifest(db_engine: Engine):
    creation = _game(db_engine, require_run_manifest=True)

    with pytest.raises(game_service.IllegalStart, match="run manifest"):
        game_service.start_game(db_engine, creation.game_id)
    export = build_game_export(db_engine, creation.game_id)
    assert export["game"]["status"] == "lobby" and export["events"] == []

    provenance.create_run_manifest(
        db_engine, creation.game_id, configuration=_configuration(commit="one")
    )
    started = game_service.start_game(db_engine, creation.game_id)
    assert started.turn == 1 and started.status.value == "running"


def test_i078_007e_api_maps_the_missing_manifest_to_409(db_engine: Engine):
    client = TestClient(create_app(engine=db_engine), raise_server_exceptions=False)
    created = client.post(
        "/admin/games", json={"scenario": str(FIXTURE_DIR), "require_run_manifest": True},
        headers=_admin(),
    )
    game_id = created.json()["game_id"]

    response = client.post(f"/admin/games/{game_id}/start", json={}, headers=_admin())
    assert response.status_code == 409, response.text
    assert "run manifest" in response.json()["error"]["message"]


def test_i078_007f_unflagged_game_starts_without_a_manifest_as_before(db_engine: Engine):
    creation = _game(db_engine)  # the default: every existing harness and scripted game
    started = game_service.start_game(db_engine, creation.game_id)
    assert started.turn == 1 and started.phase.value == "diplomacy"
