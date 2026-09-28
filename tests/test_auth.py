"""T-028 acceptance: Seat authentication (testing.md §6, api.md §2.3/§2.4, INV-ACTOR-001).

The bearer token is the only source of Actor identity. The server resolves
``seat-token → Seat → (game_id, actor_id)`` (api.md §2.3); a request body can never assert
identity (INV-ACTOR-001 / API-INV-001); and a Seat token can never authorize an admin
endpoint (api.md §2.4). All four ``TEST-AUTH-00N`` behaviours are asserted here, offline,
against the canonical fixture on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

import logging

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from app import scenarios
from app.api import create_app
from tests.api_helpers import FIXTURE_DIR, bearer


ADMIN_TOKEN = "admin-secret-token"


@pytest.fixture(autouse=True)
def _admin_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Configure the separate admin token the way TRD §16 expects (env-driven)."""
    monkeypatch.setenv("AMAW_ADMIN_TOKEN", ADMIN_TOKEN)


def _seed(engine: Engine) -> scenarios.GameCreation:
    scenario = scenarios.load_scenario(FIXTURE_DIR)
    return scenarios.create_game_from_scenario(engine, scenario)


def _client(engine: Engine) -> TestClient:
    return TestClient(create_app(engine=engine), raise_server_exceptions=False)


# --- TEST-AUTH-001 — valid Seat token resolves Actor -----------------------------------


def test_auth_001_valid_seat_token_resolves_actor(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    token = creation.seat_tokens["arkania"]

    # The context endpoint is the first authenticated player endpoint (T-029); it resolves the
    # token to its Actor and stamps the common response fields (api.md §5).
    response = _client(db_engine).get(
        f"/games/{creation.game_id}/context", headers=bearer(token)
    )

    assert response.status_code == 200
    body = response.json()
    # Token maps to exactly one game + Actor (testing.md §6 TEST-AUTH-001).
    assert body["you"]["actor_id"] == "arkania"
    assert body["game_id"] == str(creation.game_id)
    assert body["turn"] == 0
    assert body["phase"] == "diplomacy"


# --- TEST-AUTH-002 — invalid token rejected --------------------------------------------


def test_auth_002_missing_token_rejected(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    response = _client(db_engine).get(f"/games/{creation.game_id}/context")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_auth_002_unknown_token_rejected(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    response = _client(db_engine).get(
        f"/games/{creation.game_id}/context", headers=bearer("not-a-real-token")
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


def test_auth_002_malformed_authorization_header_rejected(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    response = _client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers={"Authorization": "Basic something"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# --- TEST-AUTH-003 — request Actor ID cannot impersonate another Actor -----------------


def test_auth_003_body_actor_id_cannot_impersonate(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    client = _client(db_engine)
    gid = creation.game_id

    # A mutating endpoint with a body that tries to assert a different identity.
    response = client.post(
        f"/games/{gid}/notes",
        headers=bearer(creation.seat_tokens["arkania"]),
        json={"text": "MINE", "actor_id": "belvar"},  # bogus identity in the body
    )
    assert response.status_code == 200

    # The Note belongs to the token's Actor, never the body's (INV-ACTOR-001 / API-INV-001):
    # arkania owns it; belvar's Seat-authenticated read never sees it.
    ark = client.get(f"/games/{gid}/notes", headers=bearer(creation.seat_tokens["arkania"]))
    bel = client.get(f"/games/{gid}/notes", headers=bearer(creation.seat_tokens["belvar"]))
    assert [n["text"] for n in ark.json()["notes"]] == ["MINE"]
    assert bel.json()["notes"] == []


# --- a token for one game does not authorize another game ------------------------------


def test_seat_token_does_not_authorize_a_different_game(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    other = _seed(db_engine)  # a second, distinct game
    token = creation.seat_tokens["arkania"]

    response = _client(db_engine).get(
        f"/games/{other.game_id}/context", headers=bearer(token)
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


# --- TEST-AUTH-004 — Seat token cannot call admin endpoint -----------------------------


def test_auth_004_seat_token_cannot_call_admin_endpoint(db_engine: Engine) -> None:
    creation = _seed(db_engine)
    seat_token = creation.seat_tokens["arkania"]

    response = _client(db_engine).get("/admin/health", headers=bearer(seat_token))
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_admin_token_authorizes_admin_endpoint(db_engine: Engine) -> None:
    response = _client(db_engine).get("/admin/health", headers=bearer(ADMIN_TOKEN))
    assert response.status_code == 200


def test_admin_endpoint_without_token_rejected(db_engine: Engine) -> None:
    response = _client(db_engine).get("/admin/health")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_token"


# --- the bearer token is never written to the log stream (testing.md §24) --------------


def test_seat_token_never_appears_in_logs(
    db_engine: Engine, caplog: pytest.LogCaptureFixture
) -> None:
    creation = _seed(db_engine)
    token = creation.seat_tokens["arkania"]

    with caplog.at_level(logging.DEBUG):
        _client(db_engine).get(f"/games/{creation.game_id}/context", headers=bearer(token))

    assert caplog.records, "request logging emitted nothing to assert on"
    assert all(token not in record.getMessage() for record in caplog.records)
