"""T-029 acceptance: the Actor context endpoint (api.md §6.1, agents.md §4).

`GET /games/{game_id}/context` returns one Actor's authorised context: the common fields,
its own private brief/objectives/private information, the Effects it may see, the current
Argument where relevant, and the server-computed `available_actions`/`requires_input`. All
private-information filtering goes through the visibility service (API-INV-002); no field may
leak another Actor's private state (agents.md §4 "do not include" list, TEST-VIS-003).

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

import json

from sqlalchemy import Engine

from app import context, game_service
from tests.api_helpers import (
    bearer,
    client,
    seed,
    start_to_argument,
    start_to_cons,
    start_to_diplomacy,
)

ARKANIA_BRIEF_MARKER = "greater regional influence"  # arkania-only phrase in its private_brief


# --- happy path + shape (api.md §6.1) --------------------------------------------------


def test_context_returns_common_fields_and_own_private_state(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    resp = client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["game_id"] == str(creation.game_id)
    assert body["turn"] == 1
    assert body["max_turns"] == 6
    assert body["phase"] == "diplomacy"
    assert body["status"] == "running"
    assert body["active_actor_id"] is None
    # "you" carries the requester's own private state (api.md §6.1).
    assert body["you"]["actor_id"] == "arkania"
    assert body["you"]["private_brief"]
    assert isinstance(body["you"]["objectives"], list) and body["you"]["objectives"]
    assert isinstance(body["you"]["private_information"], list)
    assert isinstance(body["effects"], list)
    assert body["current_argument"] is None


def test_context_effects_are_visibility_filtered(db_engine: Engine) -> None:
    """arkania sees its private starting Effect; belvar never does (INV-EFFECT-005)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)

    ark = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    bel = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["belvar"]),
    ).json()

    ark_descs = {e["description"] for e in ark["effects"]}
    bel_descs = {e["description"] for e in bel["effects"]}
    assert "Arkania's reserve mobilisation plan is incomplete." in ark_descs
    assert "Arkania's reserve mobilisation plan is incomplete." not in bel_descs
    # every returned Effect is active (active_only) and carries only documented fields.
    for effect in ark["effects"]:
        assert effect["active"] is True
        assert set(effect) == {
            "id",
            "description",
            "visibility",
            "created_turn",
            "expires_turn",
            "active",
        }


# --- available_actions is server-computed per Actor per phase (API-INV-009) -------------


def test_context_available_actions_diplomacy(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    c = client(db_engine)

    before = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert set(before["available_actions"]) >= {
        "send_message",
        "ready",
        "get_notes",
        "save_note",
    }
    assert before["requires_input"] is True
    # Resource actions are now advertised whenever the game is running (T-049, api.md §6.1).
    assert {"search_resources", "read_resource"} <= set(before["available_actions"])

    # Once ready, the Actor is no longer waited on and cannot ready again.
    game_service.ready(db_engine, creation.game_id, "arkania")
    after = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert "ready" not in after["available_actions"]
    assert after["requires_input"] is False


def test_context_available_actions_argument_phase(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_argument(db_engine, creation)
    c = client(db_engine)

    active = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert active["active_actor_id"] == "arkania"
    assert "make_argument" in active["available_actions"]
    assert active["requires_input"] is True
    # Research is legal in the Argument phase too (agents.md §6 observe example).
    assert {"search_resources", "read_resource"} <= set(active["available_actions"])

    inactive = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["belvar"]),
    ).json()
    assert "make_argument" not in inactive["available_actions"]
    assert inactive["requires_input"] is False


def test_context_current_argument_and_con_actions_in_cons(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    c = client(db_engine)

    # The current Argument is public game state — visible to opponents so they can Con it.
    bel = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["belvar"]),
    ).json()
    assert bel["current_argument"] is not None
    assert bel["current_argument"]["id"] == str(arg.id)
    assert bel["current_argument"]["status"] == "open_for_cons"
    assert {"make_con", "pass_con"} <= set(bel["available_actions"])
    assert bel["requires_input"] is True
    # Resources stay reachable during Cons — they are running-only, not phase-gated (D-036).
    assert {"search_resources", "read_resource"} <= set(bel["available_actions"])

    # The Argument owner may not Con its own Argument (API-INV-004).
    ark = c.get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert "make_con" not in ark["available_actions"]
    assert ark["requires_input"] is False


def test_context_resource_actions_absent_when_not_running(db_engine: Engine) -> None:
    """Resources are running-only (D-036): a LOBBY game advertises no actions at all."""
    creation = seed(db_engine)  # created in LOBBY — not yet started
    lobby = client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    ).json()
    assert lobby["status"] != "running"
    assert lobby["available_actions"] == []
    assert lobby["requires_input"] is False


# --- no field leaks another Actor's private state (agents.md §4, TEST-VIS-003) ----------


def test_context_never_leaks_another_actors_private_state(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    belvar_view = client(db_engine).get(
        f"/games/{creation.game_id}/context",
        headers=bearer(creation.seat_tokens["belvar"]),
    ).json()

    assert belvar_view["you"]["actor_id"] == "belvar"
    # arkania's private brief text must not appear anywhere in belvar's context.
    assert ARKANIA_BRIEF_MARKER not in json.dumps(belvar_view)


# --- authentication --------------------------------------------------------------------


def test_context_missing_token_rejected(db_engine: Engine) -> None:
    creation = seed(db_engine)
    resp = client(db_engine).get(f"/games/{creation.game_id}/context")
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "invalid_token"


def test_context_cross_game_token_forbidden(db_engine: Engine) -> None:
    creation = seed(db_engine)
    other = seed(db_engine)
    resp = client(db_engine).get(
        f"/games/{other.game_id}/context",
        headers=bearer(creation.seat_tokens["arkania"]),
    )
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


# --- the read-model itself (TDD'd at its layer, not only through the router) ------------


def test_actor_context_read_model_shape(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    result = context.actor_context(db_engine, creation.game_id, "arkania")

    assert set(result) == {
        "game_id",
        "scenario",
        "turn",
        "max_turns",
        "phase",
        "status",
        "active_actor_id",
        "diplomacy",
        "you",
        "effects",
        "current_argument",
        "con_slots_remaining",
        "requires_input",
        "available_actions",
    }
    assert result["diplomacy"] == {
        "policy_version": "diplomacy-policy-v1",
        "mode": "open_diplomacy",
        "rounds_per_turn": None,
        "rounds_completed": 0,
    }
