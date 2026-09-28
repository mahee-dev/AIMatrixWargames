"""T-050 acceptance: append-only Resource access logging (TRD §29, agents.md §10/§29).

Every *authorised* Resource search or read writes one append-only ``ResourceAccess`` row as a
server-side side effect (agents.md §10) — game, Actor, turn, operation, query, resource read and
timestamp (TRD §29) — so a full game yields a queryable access log for the benchmark-methodology
§17 research metrics. The log is admin-only: it surfaces through the unfiltered admin export
(testing.md §24), never on an Actor-facing endpoint. A denied read logs nothing (D-038); the
logged ``actor_id`` is the Seat token's Actor (INV-ACTOR-001).

Offline, no key, on the isolated ``db_engine`` temp database.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import Engine
from sqlmodel import select

from app import export as export_module
from app.database import session_scope
from app.models import Argument, Game, ResourceAccess
from tests.api_helpers import bearer, client, seed, start_to_diplomacy


@dataclass(frozen=True)
class _Row:
    """A detached snapshot of one ``ResourceAccess`` row, safe to read after the session closes."""

    id: int
    actor_id: str
    turn: int
    operation: str
    query: str | None
    resource_id: str | None
    created_at: datetime


def _running_game(engine: Engine):
    creation = seed(engine)
    start_to_diplomacy(engine, creation)
    return creation


def _access_rows(engine: Engine, game_id: uuid.UUID) -> list[_Row]:
    with session_scope(engine) as session:
        rows = session.exec(
            select(ResourceAccess).where(ResourceAccess.game_id == game_id)
        ).all()
        snapshots = [
            _Row(
                id=r.id,
                actor_id=r.actor_id,
                turn=r.turn,
                operation=r.operation,
                query=r.query,
                resource_id=r.resource_id,
                created_at=r.created_at,
            )
            for r in rows
        ]
    # ordered by the monotonic insertion id
    return sorted(snapshots, key=lambda r: r.id)


def _search(c, game_id, token, query: str):
    return c.get(
        f"/games/{game_id}/resources/search", params={"q": query}, headers=bearer(token)
    )


def _read(c, game_id, token, resource_id: str):
    return c.get(f"/games/{game_id}/resources/{resource_id}", headers=bearer(token))


# --- a search and a read each capture the TRD §29 fields --------------------------------


def test_search_logs_operation_query_actor_turn_and_timestamp(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    assert _search(c, creation.game_id, creation.seat_tokens["arkania"], "regional").status_code == 200

    rows = _access_rows(db_engine, creation.game_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "search"
    assert row.query == "regional"  # the raw query the Actor sent
    assert row.resource_id is None
    assert row.actor_id == "arkania"
    assert row.turn == 1
    assert row.created_at is not None


def test_read_logs_operation_resource_id_actor_and_turn(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    assert _read(c, creation.game_id, creation.seat_tokens["arkania"], "background").status_code == 200

    rows = _access_rows(db_engine, creation.game_id)
    assert len(rows) == 1
    row = rows[0]
    assert row.operation == "read"
    assert row.resource_id == "background"
    assert row.query is None
    assert row.actor_id == "arkania"
    assert row.turn == 1


# --- append-only: repeated searches are distinct rows (§17 "repeated searches") ---------


def test_repeated_search_appends_a_second_row(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    assert _search(c, creation.game_id, token, "regional").status_code == 200
    assert _search(c, creation.game_id, token, "regional").status_code == 200

    rows = _access_rows(db_engine, creation.game_id)
    assert [(r.operation, r.query) for r in rows] == [("search", "regional"), ("search", "regional")]


def test_zero_result_search_still_logs(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    resp = _search(c, creation.game_id, creation.seat_tokens["arkania"], "no-such-topic-xyzzy")
    assert resp.status_code == 200
    assert resp.json()["results"] == []

    rows = _access_rows(db_engine, creation.game_id)
    assert len(rows) == 1
    assert rows[0].operation == "search"
    assert rows[0].query == "no-such-topic-xyzzy"


# --- a denied read / blank query write nothing (D-038) ----------------------------------


def test_denied_read_logs_nothing(db_engine: Engine) -> None:
    """belvar reading arkania's private Resource is a 404 (INV-RES-004) and must not log."""
    creation = _running_game(db_engine)
    c = client(db_engine)
    resp = _read(c, creation.game_id, creation.seat_tokens["belvar"], "arkania-intel")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"

    assert _access_rows(db_engine, creation.game_id) == []


def test_blank_query_logs_nothing(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    resp = _search(c, creation.game_id, creation.seat_tokens["arkania"], "   ")
    assert resp.status_code == 422

    assert _access_rows(db_engine, creation.game_id) == []


# --- attribution is the Seat token's Actor (INV-ACTOR-001) ------------------------------


def test_access_is_attributed_to_the_seat_actor(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    assert _search(c, creation.game_id, creation.seat_tokens["belvar"], "trade").status_code == 200
    assert _read(c, creation.game_id, creation.seat_tokens["cestia"], "background").status_code == 200

    rows = _access_rows(db_engine, creation.game_id)
    by_actor = {(r.actor_id, r.operation) for r in rows}
    assert ("belvar", "search") in by_actor
    assert ("cestia", "read") in by_actor


# --- the rows surface in the admin export (testing.md §24, admin-only) ------------------


def test_access_log_surfaces_in_admin_export(db_engine: Engine) -> None:
    creation = _running_game(db_engine)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    assert _search(c, creation.game_id, token, "regional").status_code == 200
    assert _read(c, creation.game_id, token, "arkania-intel").status_code == 200

    export = export_module.build_game_export(db_engine, creation.game_id)
    log = export["resource_access"]
    assert len(log) == 2
    kinds = {entry["operation"] for entry in log}
    assert kinds == {"search", "read"}
    for entry in log:
        assert set(entry) == {
            "id",
            "actor_id",
            "turn",
            "operation",
            "query",
            "resource_id",
            "activation_id",
            "result_count",
            "result_resource_ids",
            "result_set_sha256",
            "created_at",
        }
        assert entry["actor_id"] == "arkania"


def test_export_resource_access_is_deterministically_ordered(db_engine: Engine) -> None:
    """CLI and endpoint must be byte-identical, so the log is ordered by its monotonic id."""
    creation = _running_game(db_engine)
    c = client(db_engine)
    token = creation.seat_tokens["arkania"]
    _search(c, creation.game_id, token, "regional")
    _read(c, creation.game_id, token, "background")
    _search(c, creation.game_id, token, "trade")

    log = export_module.build_game_export(db_engine, creation.game_id)["resource_access"]
    assert [entry["id"] for entry in log] == sorted(entry["id"] for entry in log)


def test_read_before_argument_is_reconstructable_from_the_log(db_engine: Engine) -> None:
    """§17 'Resources read before first Argument' must be answerable from the raw log: a read in
    turn 1 is recorded before any Argument row exists for that turn."""
    creation = _running_game(db_engine)
    c = client(db_engine)
    assert _read(c, creation.game_id, creation.seat_tokens["arkania"], "background").status_code == 200

    with session_scope(db_engine) as session:
        arguments = session.exec(
            select(Argument).where(Argument.game_id == creation.game_id)
        ).all()
        assert arguments == []  # no Argument yet — the read is genuinely pre-Argument
        game = session.get(Game, creation.game_id)
        assert game.turn == 1
    rows = _access_rows(db_engine, creation.game_id)
    assert rows and rows[0].operation == "read" and rows[0].turn == 1
