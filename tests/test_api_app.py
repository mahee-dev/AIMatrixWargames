"""T-027 acceptance: the FastAPI application skeleton.

The HTTP surface that fronts the existing offline engine (`api.md §2/§3/§5`). This module
proves the *skeleton*: the app boots, every error path renders the one documented envelope
`{"error": {"code", "message", "details"?}}` (api.md §3), the common response fields are stamped
where relevant (api.md §5). The `app/` import boundary is in `test_architecture_boundaries.py`.

Originally exercised through throwaway diagnostic routes (`/__boom`, `/__echo`); those were
retired when the real player endpoints landed (T-029+), so the envelope/validation paths are now
asserted against real endpoints and a monkeypatched service (T-035 close).

Runs offline, no OpenRouter key (testing.md §3). The app is bound to the isolated ``db_engine``
temp database via the application factory, exactly as production wires the default engine.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlalchemy import Engine

from app import context as context_module
from app import game_service
from app.api import common_response_fields, create_app
from app.models import Game, GamePhase, GameStatus
from tests.api_helpers import bearer, client, seed, start_to_argument


# --- the app boots ---------------------------------------------------------------------


def test_create_app_returns_fastapi_instance(db_engine: Engine) -> None:
    assert isinstance(create_app(engine=db_engine), FastAPI)


# --- every error path renders the documented envelope (api.md §3) ----------------------


def test_forced_error_returns_documented_envelope(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unexpected error inside a real endpoint → 500 internal_error, detail not leaked."""
    creation = seed(db_engine)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("boom: internal detail that must never leak to the client")

    monkeypatch.setattr(context_module, "actor_context", _boom)
    response = client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    )
    assert response.status_code == 500
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == "internal_error"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    # The raw exception text must never reach the client (api.md §3, no stack/detail leak).
    assert "boom" not in body["error"]["message"].lower()


def test_request_validation_error_maps_to_422(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    # Missing required fields on POST /arguments → schema validation failure.
    response = client(db_engine).post(
        f"/games/{creation.game_id}/arguments",
        headers=bearer(creation.seat_tokens["arkania"]),
        json={"action": "only an action"},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_domain_error_maps_to_documented_code(db_engine: Engine) -> None:
    creation = seed(db_engine)  # game in LOBBY, not running
    c = client(db_engine)

    # wrong_phase: an Argument in a non-argument phase (api.md §3.1).
    wrong_phase = c.post(
        f"/games/{creation.game_id}/arguments",
        headers=bearer(creation.seat_tokens["arkania"]),
        json={"action": "a", "desired_effect": "b", "pros": ["c"]},
    )
    assert wrong_phase.status_code == 409
    assert wrong_phase.json()["error"]["code"] == "wrong_phase"

    # idempotency_conflict: same key + different payload on a mutating endpoint.
    headers = {**bearer(creation.seat_tokens["arkania"]), "Idempotency-Key": "DUP"}
    c.post(f"/games/{creation.game_id}/notes", headers=headers, json={"text": "first"})
    conflict = c.post(
        f"/games/{creation.game_id}/notes", headers=headers, json={"text": "different"}
    )
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "idempotency_conflict"


def test_unmapped_domain_error_falls_back_to_internal_error(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bare GameServiceError with no explicit mapping must still render the envelope as 500."""
    creation = seed(db_engine)

    def _bare(*_args, **_kwargs):
        raise game_service.GameServiceError("an unmapped domain failure")

    monkeypatch.setattr(game_service, "save_note", _bare)
    response = client(db_engine).post(
        f"/games/{creation.game_id}/notes",
        headers=bearer(creation.seat_tokens["arkania"]),
        json={"text": "x"},
    )
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"


def test_unknown_route_renders_envelope_not_framework_default(db_engine: Engine) -> None:
    """Even a framework 404 must be the envelope, never Starlette's ``{"detail": ...}``."""
    response = client(db_engine).get("/no/such/route")
    assert response.status_code == 404
    body = response.json()
    assert "detail" not in body
    assert body["error"]["code"] == "not_found"


# --- common response fields (api.md §5) ------------------------------------------------


def test_common_response_fields_helper() -> None:
    game = Game(
        scenario_id="s",
        max_turns=6,
        seed=1,
        turn=2,
        phase=GamePhase.argument,
        status=GameStatus.running,
        turn_order=["a"],
    )
    fields = common_response_fields(game)
    assert fields == {"game_id": str(game.id), "turn": 2, "phase": "argument"}
