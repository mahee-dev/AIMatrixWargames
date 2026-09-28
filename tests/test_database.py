"""Database engine, schema creation and session lifecycle (T-007, WBS 2.2.1).

These prove the persistence foundation the rest of the stack sits on (TRD §26): a clean
SQLite file gains every registered table, re-running the creation is idempotent, a session
from the lifecycle helper commits and reloads a domain entity, and the engine factory
honours an explicit path (creating its parent) while the default engine follows
``settings.database_path`` (app/config.py). No CRUD/transactions here — that is T-008.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine, inspect, text
from sqlmodel import SQLModel, select

from app import database
from app.api import create_app
from app.config import Settings
from app.database import engine_for_path, initialize_database, session_scope
from app.models import Game

# The original nine TRD §7.1–7.9 entities plus T-045's Resource snapshot. These are the
# primary game records registered today (agent-run metadata arrives with T-053).
_CORE_ENTITY_TABLES = {
    "game",
    "actor",
    "seat",
    "message",
    "argument",
    "con",
    "effect",
    "gameevent",
    "note",
    "resource",
    "actorstrategicstate",
}
_D008_TABLES = {"conparticipation", "adjudicationrecord"}


def _sqlite_master_tables(engine: Engine) -> set[str]:
    """Table names SQLite actually holds, read from ``sqlite_master``."""
    return set(inspect(engine).get_table_names())


def test_create_db_and_tables_creates_registered_tables(tmp_path: Path) -> None:
    db_file = tmp_path / "fresh.db"
    engine = database.engine_for_path(db_file)

    database.create_db_and_tables(engine)

    tables = _sqlite_master_tables(engine)
    # Every table SQLModel registered is created — no more, no fewer (TRD §26).
    assert tables == set(SQLModel.metadata.tables)
    assert _CORE_ENTITY_TABLES <= tables
    assert _D008_TABLES <= tables
    engine.dispose()


def test_create_db_and_tables_is_idempotent(tmp_path: Path) -> None:
    engine = database.engine_for_path(tmp_path / "idem.db")

    database.create_db_and_tables(engine)
    before = _sqlite_master_tables(engine)
    database.create_db_and_tables(engine)  # must not raise on an existing schema
    after = _sqlite_master_tables(engine)

    assert before == after
    engine.dispose()


def test_initialize_database_adds_resource_format_projection_to_legacy_table(
    tmp_path: Path,
) -> None:
    engine = database.engine_for_path(tmp_path / "legacy-resource.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE resource (game_id CHAR(32) NOT NULL, id VARCHAR NOT NULL, "
            "PRIMARY KEY (game_id, id))"
        )

    database.initialize_database(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("resource")}
    assert {
        "representation_kind",
        "media_type",
        "text_content",
        "size_bytes",
    } <= columns
    engine.dispose()


def test_i053_009_initialize_database_adds_nullable_legacy_diplomacy_policy(
    tmp_path: Path,
) -> None:
    engine = database.engine_for_path(tmp_path / "legacy-game.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )

    database.initialize_database(engine)

    columns = {column["name"]: column for column in inspect(engine).get_columns("game")}
    assert columns["diplomacy_policy_version"]["nullable"] is True
    assert columns["diplomacy_mode"]["nullable"] is True
    assert columns["diplomacy_rounds_per_turn"]["nullable"] is True
    assert columns["diplomacy_rounds_completed"]["nullable"] is False
    engine.dispose()


def test_i055_009_initialize_database_adds_nullable_legacy_con_policy(tmp_path: Path) -> None:
    engine = database.engine_for_path(tmp_path / "legacy-cons.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE argument (id CHAR(32) PRIMARY KEY, game_id CHAR(32) NOT NULL, "
            "turn INTEGER NOT NULL, actor_id VARCHAR NOT NULL, action VARCHAR NOT NULL, "
            "desired_effect VARCHAR NOT NULL, pros TEXT NOT NULL, budget_slot INTEGER NOT NULL, "
            "status VARCHAR NOT NULL)"
        )

    database.initialize_database(engine)

    game_columns = {column["name"]: column for column in inspect(engine).get_columns("game")}
    for name in (
        "con_policy_version", "con_mode", "con_challengers_per_argument", "con_challenger_cursor",
    ):
        assert game_columns[name]["nullable"] is True
    argument_columns = {
        column["name"]: column for column in inspect(engine).get_columns("argument")
    }
    assert argument_columns["challenger_actor_ids"]["nullable"] is True
    engine.dispose()


def test_session_scope_roundtrips_entity(tmp_path: Path) -> None:
    engine = database.engine_for_path(tmp_path / "roundtrip.db")
    database.create_db_and_tables(engine)

    game = Game(scenario_id="scn-1", max_turns=6, seed=42)
    with database.session_scope(engine) as session:
        session.add(game)
        game_id = game.id  # captured before the session closes

    # A second session sees the committed row — proving the helper committed and closed.
    with database.session_scope(engine) as session:
        reloaded = session.get(Game, game_id)
        assert reloaded is not None
        assert reloaded.scenario_id == "scn-1"
        assert reloaded.max_turns == 6
    engine.dispose()


def test_engine_for_path_honours_explicit_path_and_creates_parent(tmp_path: Path) -> None:
    nested = tmp_path / "does" / "not" / "exist" / "amaw.db"
    assert not nested.parent.exists()

    engine = database.engine_for_path(nested)
    database.create_db_and_tables(engine)

    assert nested.parent.is_dir()
    assert nested.is_file()  # the DB was written where asked, not the default location
    engine.dispose()


def test_default_engine_uses_settings_path(tmp_path: Path, monkeypatch) -> None:
    # Point settings at a temp path so this never touches the real data/amaw.db.
    target = tmp_path / "cfg" / "amaw.db"
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(target))
    # Clear any default engine a prior test cached, so the assertion is order-independent.
    database.reset_default_engine()

    try:
        engine = database.get_engine()
        assert str(target) in str(engine.url)
        # Cached: a second call returns the same instance rather than rebuilding.
        assert database.get_engine() is engine
    finally:
        database.reset_default_engine()


# --- fresh databases and additive migrations ----------------------------------------------


def test_i006_create_app_initializes_a_fresh_database(tmp_path) -> None:
    engine = engine_for_path(tmp_path / "fresh-app.db")
    create_app(engine=engine, settings=Settings(AMAW_ADMIN_TOKEN="admin", _env_file=None))
    with session_scope(engine) as session:
        assert session.exec(select(Game)).all() == []


def test_i006_additive_migration_preserves_existing_game(tmp_path) -> None:
    engine = engine_for_path(tmp_path / "old.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )
        connection.execute(
            text("INSERT INTO game VALUES (:id, 'legacy', 6, 42)"),
            {"id": "0" * 32},
        )
    initialize_database(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT scenario_id, scenario_version, scenario_summary FROM game")
        ).one()
    assert row == ("legacy", None, None)


def test_i027_additive_migration_adds_activation_wall_time(tmp_path) -> None:
    engine = engine_for_path(tmp_path / "pre-i027.db")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE agentrun (id INTEGER PRIMARY KEY, game_id CHAR(32) NOT NULL, "
            "role VARCHAR NOT NULL, model_requested VARCHAR NOT NULL, latency_ms INTEGER)"
        )
        connection.exec_driver_sql(
            "CREATE TABLE game (id CHAR(32) PRIMARY KEY, scenario_id VARCHAR NOT NULL, "
            "max_turns INTEGER NOT NULL, seed INTEGER NOT NULL)"
        )
        connection.exec_driver_sql(
            "INSERT INTO agentrun VALUES (1, '00000000000000000000000000000000', "
            "'player', 'legacy/model', 1234)"
        )
    initialize_database(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("agentrun")}
    assert "activation_wall_ms" in columns
    with engine.connect() as connection:
        migrated = connection.execute(
            text("SELECT latency_ms, activation_wall_ms FROM agentrun WHERE id = 1")
        ).one()
    assert migrated == (None, 1234)
