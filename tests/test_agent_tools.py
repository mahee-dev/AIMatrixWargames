"""T-037 acceptance: the agent HTTP tool layer (`agents/tools.py`, agents.md §5–§16).

The tool layer is a thin, authenticated wrapper around the player HTTP API (T-029…T-035). It
carries **no** game logic: identity comes only from the Seat token (INV-ACTOR-001), it never
recomputes `available_actions` (API-INV-009) or filters visibility, and it converts an API
error envelope (api.md §3) into a structured :class:`ToolResult` instead of leaking an HTTP
exception into the eventual LLM loop (agents.md §18 "malformed tool call → tool error
returned").

These tests drive the game into each phase through :mod:`app.game_service` (the T-042/T-043
stand-ins, exactly as the api_helpers do) while the tools themselves go over HTTP against a
``TestClient`` bound to the **same** ``db_engine``. Offline, no key.
"""

from __future__ import annotations

import io
import logging

from sqlalchemy import Engine

from agents.tools import PlayerTools, ToolResult
from tests.api_helpers import (
    ACTORS,
    client,
    seed,
    start_to_argument,
    start_to_cons,
    start_to_diplomacy,
)


def _tools(engine: Engine, creation, actor: str) -> PlayerTools:
    """A PlayerTools bound to ``actor``'s Seat token over a TestClient on the shared engine."""
    return PlayerTools(
        client(engine),
        game_id=str(creation.game_id),
        token=creation.seat_tokens[actor],
    )


# --- every required operation is reachable, and a legal op returns structured data -------


def test_all_required_operations_reachable(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    t = _tools(db_engine, creation, "arkania")

    # observe → the server context incl. available_actions (never recomputed locally).
    observed = t.observe()
    assert observed.ok
    assert observed.data["you"]["actor_id"] == "arkania"
    assert "available_actions" in observed.data
    assert "ready" in observed.data["available_actions"]

    # send_message (legal in diplomacy) → structured success data.
    sent = t.send_message(recipients=["belvar"], public=False, text="Opening contact.")
    assert sent.ok
    assert sent.data["message"]["sender_actor_id"] == "arkania"

    # read_messages, get_notes, save_note all reachable.
    assert t.read_messages().ok
    saved = t.save_note(text="Belvar seems cautious.")
    assert saved.ok and saved.data["note"]["text"] == "Belvar seems cautious."
    notes = t.get_notes()
    assert notes.ok and any(n["text"] == "Belvar seems cautious." for n in notes.data["notes"])

    # ready closes this Actor's diplomacy participation.
    assert t.ready().ok


def test_argument_con_pass_reachable(db_engine: Engine) -> None:
    creation = seed(db_engine)
    # Drive to CONS: arkania's primary Argument is open_for_cons.
    arg = start_to_cons(db_engine, creation)

    # make_con and pass_con for an opponent, through the tool layer.
    belvar = _tools(db_engine, creation, "belvar")
    con = belvar.make_con(argument_id=str(arg.id), reason="Rail capacity is constrained.")
    assert con.ok and con.data["con"]["actor_id"] == "belvar"
    passed = belvar.pass_con(argument_id=str(arg.id))
    assert passed.ok and passed.data["passed"] is True

    # make_argument for the active Actor (fresh game to reach the ARGUMENT phase).
    creation2 = seed(db_engine)
    start_to_argument(db_engine, creation2)
    arkania = _tools(db_engine, creation2, "arkania")
    made = arkania.make_argument(
        action="Mobilise reserves.",
        desired_effect="Formations deploy to the frontier.",
        pros=["Plans already exist."],
    )
    assert made.ok and made.data["argument"]["status"] == "open_for_cons"


# --- resource search/read reachable, visibility-filtered by the server (T-049) -----------


def test_search_and_read_resources_reachable(db_engine: Engine) -> None:
    """search_resources / read_resource are thin wrappers over the T-047/T-048 endpoints."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)  # Resources are running-only (D-036)
    arkania = _tools(db_engine, creation, "arkania")

    # search returns the server's {query, results} verbatim (agents.md §9 / api.md §10.1).
    found = arkania.search_resources(query="REGIONAL BACK")
    assert found.ok
    assert found.data["query"] == "REGIONAL BACK"
    # "trade" is the released `search-v2` default's token coverage (I-112), not a leak.
    assert [r["id"] for r in found.data["results"]] == ["background", "trade"]

    # read returns the authorised Resource projection (agents.md §10 / api.md §10.2).
    read = arkania.read_resource(resource_id="background")
    assert read.ok
    assert read.data["resource"]["id"] == "background"


def test_hidden_resource_is_not_reachable_through_tool_layer(db_engine: Engine) -> None:
    """A private Resource is invisible to a non-authorised Actor — search and read both (§10)."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    # arkania-intel is private to arkania (fixture). belvar sees nothing and its read 404s.
    belvar = _tools(db_engine, creation, "belvar")
    assert belvar.search_resources(query="intelligence").data["results"] == []
    denied = belvar.read_resource(resource_id="arkania-intel")
    assert not denied.ok
    assert denied.error_code == "not_found"
    assert denied.http_status == 404

    # arkania, the authorised Actor, reaches it.
    arkania = _tools(db_engine, creation, "arkania")
    assert [r["id"] for r in arkania.search_resources(query="intelligence").data["results"]] == [
        "arkania-intel"
    ]
    assert arkania.read_resource(resource_id="arkania-intel").ok


# --- Seat auth attached; identity derives from the token (INV-ACTOR-001) -----------------


def test_bearer_auth_attached_and_identity_from_token(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)

    for actor in ACTORS:
        observed = _tools(db_engine, creation, actor).observe()
        assert observed.ok
        # The identity the server reports is exactly the seat's Actor — never something the
        # caller chose.
        assert observed.data["you"]["actor_id"] == actor


def test_missing_or_invalid_token_returns_structured_invalid_token(db_engine: Engine) -> None:
    creation = seed(db_engine)
    bad = PlayerTools(client(db_engine), game_id=str(creation.game_id), token="not-a-real-token")
    result = bad.observe()
    assert not result.ok
    assert result.error_code == "invalid_token"
    assert result.http_status == 401


def test_actor_id_cannot_be_asserted_through_tool_layer(db_engine: Engine) -> None:
    """No tool method accepts or forwards ``actor_id`` — identity is the token's alone."""
    import inspect

    for name in (
        "observe",
        "read_messages",
        "send_message",
        "search_resources",
        "read_resource",
        "make_argument",
        "make_con",
        "pass_con",
        "get_notes",
        "save_note",
        "ready",
    ):
        params = set(inspect.signature(getattr(PlayerTools, name)).parameters)
        assert "actor_id" not in params, f"{name} must not expose actor_id"
        assert "sender_actor_id" not in params, f"{name} must not expose sender_actor_id"

    # And behaviourally: a message sent by belvar is attributed to belvar, full stop.
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    sent = _tools(db_engine, creation, "belvar").send_message(
        recipients=["arkania"], public=False, text="from belvar"
    )
    assert sent.ok and sent.data["message"]["sender_actor_id"] == "belvar"


# --- visibility is exactly what the API exposes (no extra filtering, no leak) -------------


def test_private_message_not_leaked_through_tool_layer(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    _tools(db_engine, creation, "arkania").send_message(
        recipients=["belvar"], public=False, text="PRIVATE-TO-BELVAR"
    )

    def texts(actor: str) -> set[str]:
        result = _tools(db_engine, creation, actor).read_messages()
        assert result.ok
        return {m["text"] for m in result.data["messages"]}

    assert "PRIVATE-TO-BELVAR" in texts("arkania")
    assert "PRIVATE-TO-BELVAR" in texts("belvar")
    # Non-party never sees it through the tool layer — the tool adds no visibility of its own.
    assert "PRIVATE-TO-BELVAR" not in texts("cestia")


# --- recoverable game-rule failures return structure, not exceptions ---------------------


def test_illegal_action_returns_structured_error_not_exception(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)  # ARGUMENT is not legal yet
    result = _tools(db_engine, creation, "arkania").make_argument(
        action="premature", desired_effect="x", pros=["y"]
    )
    assert not result.ok
    assert result.error_code == "wrong_phase"
    assert result.http_status == 409
    assert result.error_message  # a human-readable message for the agent


def test_not_active_actor_con_returns_structured_error(db_engine: Engine) -> None:
    creation = seed(db_engine)
    arg = start_to_cons(db_engine, creation)
    # The Argument owner cannot Con its own Argument (API-INV-004) — a recoverable rejection.
    result = _tools(db_engine, creation, "arkania").make_con(
        argument_id=str(arg.id), reason="self con"
    )
    assert not result.ok
    assert result.error_code == "forbidden"


# --- raw Seat tokens are never logged ----------------------------------------------------


def test_seat_token_never_logged(db_engine: Engine) -> None:
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    token = creation.seat_tokens["arkania"]

    buffer = io.StringIO()
    handler = logging.StreamHandler(buffer)
    handler.setLevel(logging.DEBUG)
    root = logging.getLogger()
    prior = root.level
    root.setLevel(logging.DEBUG)
    root.addHandler(handler)
    try:
        t = PlayerTools(client(db_engine), game_id=str(creation.game_id), token=token)
        t.observe()
        t.send_message(recipients=["belvar"], public=False, text="hi")
        t.ready()
    finally:
        root.removeHandler(handler)
        root.setLevel(prior)

    assert token not in buffer.getvalue()


# --- a full normal player turn driven over the tool layer --------------------------------


def test_http_tool_sequence_performs_a_normal_turn(db_engine: Engine) -> None:
    """Diplomacy (message + ready) then, once ARGUMENT opens, the active Actor argues — all
    dispatched off the server-provided ``available_actions``, never local phase logic."""
    creation = seed(db_engine)
    start_to_diplomacy(db_engine, creation)
    seats = {a: _tools(db_engine, creation, a) for a in ACTORS}

    # Diplomacy: each Actor acts only on what observe() advertises, then readies.
    for actor in ACTORS:
        ctx = seats[actor].observe()
        assert ctx.ok and "ready" in ctx.data["available_actions"]
        seats[actor].send_message(recipients=[], public=True, text=f"{actor} ready to talk")
        assert seats[actor].ready().ok

    # Diplomacy closed → ARGUMENT with the first turn-order Actor (arkania) active.
    ctx = seats["arkania"].observe()
    assert ctx.data["phase"] == "argument"
    assert ctx.data["active_actor_id"] == "arkania"
    assert "make_argument" in ctx.data["available_actions"]

    made = seats["arkania"].make_argument(
        action="Mobilise reserves.",
        desired_effect="Formations deploy.",
        pros=["Plans exist."],
    )
    assert made.ok
    argument_id = made.data["argument"]["id"]

    # CONS opens for opponents; a non-owner cons then passes off available_actions.
    belvar_ctx = seats["belvar"].observe()
    assert belvar_ctx.data["phase"] == "cons"
    assert "pass_con" in belvar_ctx.data["available_actions"]
    assert seats["belvar"].make_con(argument_id=argument_id, reason="constrained").ok
    assert seats["belvar"].pass_con(argument_id=argument_id).ok


def test_tool_result_is_structured() -> None:
    """A ToolResult exposes success/code/message/details/data — the stable agent-facing shape."""
    ok = ToolResult(ok=True, http_status=200, data={"x": 1})
    assert ok.ok and ok.data == {"x": 1} and ok.error_code is None
    err = ToolResult(
        ok=False, http_status=409, error_code="wrong_phase", error_message="nope", error_details={}
    )
    assert not err.ok and err.error_code == "wrong_phase" and err.data is None
