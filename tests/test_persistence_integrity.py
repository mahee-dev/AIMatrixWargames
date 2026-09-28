"""I-082 — declared SQLite integrity rules are enforced database guarantees.

``app/models.py`` declares every parent relationship, but SQLite ignores foreign keys unless
``PRAGMA foreign_keys=ON`` is set on the connection. These tests prove the app engine enforces
them on every connection, that the database carries an explicit schema identity reported in
provenance, that ``AgentRun`` activation uniqueness is a database constraint, and that the
export and activation projections are read in one transaction (INV-GAME-004).
"""

from __future__ import annotations

import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

import pytest
from sqlalchemy import Engine, inspect
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session

from app.models import AgentRun, Con, Effect, Game, Seat
from tests import api_helpers
from tests.db_helpers import actor_row


def _game(session: Session) -> Game:
    game = Game(scenario_id="scn", max_turns=6, seed=1)
    session.add(game)
    session.commit()
    session.refresh(game)
    return game


def _refused(session: Session, row) -> None:
    session.add(row)
    with pytest.raises(IntegrityError, match="FOREIGN KEY"):
        session.commit()
    session.rollback()


# TEST-I082-001


def test_i082_001_orphan_child_rows_are_refused_on_every_connection(db_engine: Engine) -> None:
    with Session(db_engine) as session:
        game = _game(session)
        session.add(actor_row(game.id))
        session.commit()

        # Actor without a Game.
        _refused(session, actor_row(uuid.uuid4()))
        # Effect without a Game.
        _refused(
            session,
            Effect(game_id=uuid.uuid4(), description="orphan", created_turn=1, visibility="public"),
        )
        # Con without an Argument.
        _refused(session, Con(argument_id=uuid.uuid4(), actor_id="arkania", reason="orphan"))
        # Seat whose (game, actor) pair names no Actor.
        _refused(session, Seat(game_id=game.id, actor_id="nobody", token_hash="h"))

    # A second pooled connection is enforced too: the pragma is per connection, not per engine.
    for _ in range(3):
        with Session(db_engine) as session:
            assert session.connection().exec_driver_sql("PRAGMA foreign_keys").scalar() == 1
            _refused(session, actor_row(uuid.uuid4(), "belvar"))


# TEST-I082-002


def _user_version(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar())


def test_i082_002_initialize_database_stamps_and_guards_the_schema_identity(
    tmp_path: Path,
) -> None:
    from app import database

    # A fresh database carries the code's schema version once initialised.
    fresh = database.engine_for_path(tmp_path / "fresh.db")
    database.initialize_database(fresh)
    assert _user_version(fresh) == database.SCHEMA_VERSION
    assert database.database_schema_version(fresh) == database.SCHEMA_VERSION
    fresh.dispose()

    # A pre-stamp database (tables only, no additive columns) is upgraded, then stamped.
    legacy_path = tmp_path / "legacy.db"
    legacy = database.engine_for_path(legacy_path)
    database.create_db_and_tables(legacy)
    assert _user_version(legacy) == 0
    with sqlite3.connect(legacy_path) as raw:
        raw.execute("ALTER TABLE game DROP COLUMN require_run_manifest")
    database.initialize_database(legacy)
    assert _user_version(legacy) == database.SCHEMA_VERSION
    columns = {column["name"] for column in inspect(legacy).get_columns("game")}
    assert "require_run_manifest" in columns
    # Idempotent on a stamped database.
    database.initialize_database(legacy)
    assert _user_version(legacy) == database.SCHEMA_VERSION
    legacy.dispose()

    # A database stamped by newer code is refused rather than silently reshaped.
    newer_path = tmp_path / "newer.db"
    newer = database.engine_for_path(newer_path)
    database.initialize_database(newer)
    with sqlite3.connect(newer_path) as raw:
        raw.execute(f"PRAGMA user_version = {database.SCHEMA_VERSION + 1}")
    with pytest.raises(database.DatabaseSchemaError, match="newer"):
        database.initialize_database(newer)
    assert _user_version(newer) == database.SCHEMA_VERSION + 1
    newer.dispose()

    # A pre-stamp database holding an orphan row is refused and left unstamped: the pragma
    # only guards new writes, so history that already violates a parent is named, not hidden.
    orphaned_path = tmp_path / "orphaned.db"
    orphaned = database.engine_for_path(orphaned_path)
    database.create_db_and_tables(orphaned)
    with sqlite3.connect(orphaned_path) as raw:  # the raw driver has the pragma off
        raw.execute(
            "INSERT INTO actor (game_id, id, name, public_description, private_brief, "
            "objectives, private_information, actor_metadata) "
            "VALUES (?, 'ghost', 'Ghost', 'p', 'b', '[]', '[]', '{}')",
            (uuid.uuid4().hex,),
        )
    with pytest.raises(database.DatabaseIntegrityError, match="actor"):
        database.initialize_database(orphaned)
    assert _user_version(orphaned) == 0
    orphaned.dispose()


# TEST-I082-003


def _run(game_id: uuid.UUID, *, role: str, actor_id: str | None, activation_id: str | None):
    return AgentRun(
        game_id=game_id,
        actor_id=actor_id,
        activation_id=activation_id,
        role=role,
        model_requested="fake/model",
        prompt_version="p",
        prompt_hash="a" * 64,
        agent_framework="fake",
        tool_schema_version="t",
    )


def test_i082_003_agent_run_activation_uniqueness_is_a_database_guarantee(
    db_engine: Engine, tmp_path: Path
) -> None:
    from app import database, provenance

    with Session(db_engine) as session:
        game = _game(session)
        session.add(actor_row(game.id))
        session.commit()
        activation = str(uuid.uuid4())

        # Written directly — bypassing the service's query-before-insert — the duplicate is
        # still refused at the database.
        session.add(_run(game.id, role="player", actor_id="arkania", activation_id=activation))
        session.commit()
        session.add(_run(game.id, role="player", actor_id="arkania", activation_id=activation))
        with pytest.raises(IntegrityError, match="UNIQUE"):
            session.commit()
        session.rollback()

        # Umpire rows legitimately carry no activation; NULLs are distinct, so two are fine.
        session.add(_run(game.id, role="umpire", actor_id=None, activation_id=None))
        session.add(_run(game.id, role="umpire", actor_id=None, activation_id=None))
        session.commit()

        # The service still reports the duplicate as a provenance error, never a raw 500.
        with pytest.raises(provenance.ProvenanceError, match="already recorded"):
            provenance.record_agent_run(
                db_engine,
                game.id,
                activation_id=activation,
                actor_id="arkania",
                role="player",
                model_requested="fake/model",
                prompt_version="p",
                prompt_hash="a" * 64,
                agent_framework="fake",
                tool_schema_version="t",
            )

    # A legacy ``agentrun`` table (created before the index existed) gains it on initialise.
    legacy_path = tmp_path / "legacy-runs.db"
    legacy = database.engine_for_path(legacy_path)
    database.create_db_and_tables(legacy)
    with sqlite3.connect(legacy_path) as raw:
        raw.execute("DROP INDEX IF EXISTS uq_agent_run_activation")
        assert not raw.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='uq_agent_run_activation'"
        ).fetchall()
    database.initialize_database(legacy)
    with sqlite3.connect(legacy_path) as raw:
        rows = raw.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uq_agent_run_activation'"
        ).fetchall()
    assert rows and "UNIQUE" in rows[0][0].upper()
    legacy.dispose()


# TEST-I082-004


def test_i082_004_export_reports_the_database_identity_it_was_read_from(db_engine: Engine) -> None:
    from app import database, export

    with Session(db_engine) as session:
        game_id = _game(session).id

    record = export.build_game_export(db_engine, game_id)

    # The identity block arrived in format 11; later additive bumps (T-073: 14) keep it.
    assert record["export_format_version"] == export.EXPORT_FORMAT_VERSION
    assert int(record["export_format_version"]) >= 11
    assert record["database"] == {
        "schema_version": database.SCHEMA_VERSION,
        "foreign_keys_enforced": True,
    }


# TEST-I082-005 / TEST-I082-006 — one transaction per projection (INV-GAME-004)


@contextmanager
def _no_second_transaction(*_args, **_kwargs):
    raise AssertionError("a second transaction was opened for a projection that must share one")
    yield  # pragma: no cover


def test_i082_005_export_summary_is_read_in_the_export_transaction(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import export, summary

    creation = api_helpers.seed(db_engine)
    api_helpers.start_to_diplomacy(db_engine, creation)
    expected = summary.build_game_summary(db_engine, creation.game_id)

    # If the export built its summary through ``summary.build_game_summary(engine, …)`` it
    # would open a second session after the record's transaction had closed.
    monkeypatch.setattr(summary, "session_scope", _no_second_transaction)
    record = export.build_game_export(db_engine, creation.game_id)

    assert record["run_summary"] == expected


def test_i082_006_activation_context_is_read_in_the_delivery_transaction(
    db_engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app import context

    creation = api_helpers.seed(db_engine)
    api_helpers.start_to_diplomacy(db_engine, creation)
    client = api_helpers.client(db_engine)
    expected = client.get(
        f"/games/{creation.game_id}/context",
        headers=api_helpers.bearer(creation.seat_tokens["arkania"]),
    ).json()

    # The delivery snapshot and the context it is returned with must be one read.
    monkeypatch.setattr(context, "session_scope", _no_second_transaction)
    response = client.post(
        f"/games/{creation.game_id}/activations",
        headers=api_helpers.bearer(creation.seat_tokens["arkania"]),
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["delivery_receipt"]
    assert body["messages_received"] == []
    assert {key: body[key] for key in expected} == expected
