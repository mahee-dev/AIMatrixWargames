"""Database engine, schema creation and session lifecycle (T-007, WBS 2.2.1).

The persistence foundation the rest of the stack sits on. TRD §26: "SQLite stores" the
domain tables and "Scenario source files may remain on disk" — this module owns the SQLite
engine and schema, nothing more. Create/read/update operations and the transactional
phase/eligibility revalidation are T-008 (INV-GAME-004); FastAPI dependency wiring is T-027.

The engine path is never hardcoded: the default engine is driven by
``settings.database_path`` (app/config.py, env ``AMAW_DATABASE_PATH``). Importing
``app.models`` registers every table on ``SQLModel.metadata`` so ``create_db_and_tables``
builds the whole schema in one call.

``app/`` imports no agent framework (AGENTS.md §6) — this module depends only on
SQLModel/SQLAlchemy, the standard library, and app-local config/models.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import Engine, event, inspect
from sqlmodel import Session, SQLModel, create_engine

from app.config import Settings

# Importing the models module is what registers the tables on SQLModel.metadata; without it
# create_all would build nothing. Kept as an explicit side-effecting import.
from app import models as _models  # noqa: F401

# I-082: the explicit identity of the database shape ``initialize_database`` produces. It is
# stamped into SQLite's own ``PRAGMA user_version`` so a database file says which shape it has
# instead of that being inferred from which additive columns happen to exist, and the export
# reports it (TRD §27/§28: "under what immutable configuration it ran"). Bump it when
# ``initialize_database`` learns a new step; ``0`` is a pre-I-082 (unstamped) database.
SCHEMA_VERSION = 1


class DatabaseSchemaError(RuntimeError):
    """The database was stamped by newer code than this one; it is refused, not reshaped."""


class DatabaseIntegrityError(RuntimeError):
    """A pre-stamp database already violates a declared parent relationship."""


def engine_for_path(database_path: Path | str) -> Engine:
    """Build a SQLModel engine for ``database_path``, creating its parent directory.

    A fresh checkout has no ``data/`` directory, and tests point at a nested temp path; in
    both cases the parent must exist before SQLite can open the file. The path is honoured
    exactly as given — callers, not this module, decide where the database lives.
    """
    path = Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{path}",
        connect_args={"timeout": 30},
    )
    event.listen(engine, "connect", _enforce_foreign_keys)
    event.listen(engine, "connect", _use_write_ahead_log)
    return engine


def _use_write_ahead_log(dbapi_connection, _connection_record) -> None:
    """Keep the database in WAL journal mode, so readers never block the game's writes.

    Under SQLite's default rollback journal, a process that holds a read transaction (an
    export, an observer) keeps a writer from committing until the busy timeout expires. In WAL
    mode readers keep a snapshot and the writer commits at once. Writes are still serialised
    by the one write lock, so ``BEGIN IMMEDIATE`` guards keep their meaning (TRD §26.1, #12,
    D-123). The mode is stored in the file, so setting it on every connect is a no-op after the
    first.
    """
    dbapi_connection.execute("PRAGMA journal_mode=WAL")


def _enforce_foreign_keys(dbapi_connection, _connection_record) -> None:
    """Turn the declared parent relationships into enforced ones (I-082).

    SQLite ignores ``FOREIGN KEY`` clauses unless ``PRAGMA foreign_keys=ON`` is set, and the
    setting is per connection — so it is applied on every ``connect`` of every app engine, not
    once per engine. With it on, an orphan child row is refused with ``IntegrityError`` instead
    of being stored as history that names a parent which does not exist.
    """
    dbapi_connection.execute("PRAGMA foreign_keys=ON")


def create_db_and_tables(engine: Engine) -> None:
    """Create every registered table on ``engine``; idempotent on re-run.

    ``SQLModel.metadata.create_all`` issues ``CREATE TABLE IF NOT EXISTS``, so a clean file
    gains the full schema and an already-initialised database is left unchanged (TRD §26).
    """
    SQLModel.metadata.create_all(engine)


def database_schema_version(engine: Engine) -> int:
    """Read the schema stamp (``PRAGMA user_version``) of the database behind ``engine``."""
    with engine.connect() as connection:
        return int(connection.exec_driver_sql("PRAGMA user_version").scalar() or 0)


def _require_referential_integrity(engine: Engine) -> None:
    """Refuse to stamp a database whose existing rows violate a declared foreign key.

    ``PRAGMA foreign_keys=ON`` guards writes from now on but says nothing about rows a
    pre-I-082 connection already stored; ``PRAGMA foreign_key_check`` names them.
    """
    with engine.connect() as connection:
        violations = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
    if violations:
        counts: dict[str, int] = {}
        for table, _rowid, _parent, _fkid in violations:
            counts[table] = counts.get(table, 0) + 1
        detail = ", ".join(f"{table}: {count}" for table, count in sorted(counts.items()))
        raise DatabaseIntegrityError(
            "database holds rows that violate a declared foreign key and cannot be brought to "
            f"schema version {SCHEMA_VERSION} ({detail})"
        )


def initialize_database(engine: Engine) -> None:
    """Bring a new or pre-remediation database to the current additive schema and stamp it.

    A database stamped by newer code is refused (:class:`DatabaseSchemaError`); a pre-stamp
    database is upgraded additively, checked for existing foreign-key violations
    (:class:`DatabaseIntegrityError`) and only then stamped with :data:`SCHEMA_VERSION`.
    """
    stamped = database_schema_version(engine)
    if stamped > SCHEMA_VERSION:
        raise DatabaseSchemaError(
            f"database schema version {stamped} is newer than this code's {SCHEMA_VERSION}"
        )
    create_db_and_tables(engine)
    columns = {column["name"] for column in inspect(engine).get_columns("game")}
    additions = {
        "scenario_version": "VARCHAR",
        "scenario_summary": "VARCHAR",
        "scenario_source_bytes": "BLOB",
        "scenario_content_sha256": "VARCHAR",
        "resource_manifest": "TEXT NOT NULL DEFAULT '[]'",
        "resource_manifest_sha256": "VARCHAR",
        # D-090: a pre-rules Game declared no rules, so an empty snapshot is honest.
        "scenario_rules": "TEXT NOT NULL DEFAULT '[]'",
        # D-095: a pre-inject Game scheduled none, so an empty snapshot is honest.
        "scenario_injects": "TEXT NOT NULL DEFAULT '[]'",
        # I-053: nullable policy fields preserve honest legacy provenance. The progress counter is
        # authoritative transition state and can safely default to zero for a pre-condition game.
        "diplomacy_policy_version": "VARCHAR",
        "diplomacy_mode": "VARCHAR",
        "diplomacy_rounds_per_turn": "INTEGER",
        "diplomacy_rounds_completed": "INTEGER NOT NULL DEFAULT 0",
        "diplomacy_close_reason": "VARCHAR",
        # I-055: nullable Con-opposition condition fields, same legacy-provenance rule.
        "con_policy_version": "VARCHAR",
        "con_mode": "VARCHAR",
        "con_challengers_per_argument": "INTEGER",
        "con_challenger_cursor": "INTEGER",
        # I-046: nullable Con-reply condition fields, same legacy-provenance rule.
        "con_reply_policy_version": "VARCHAR",
        "con_reply_mode": "VARCHAR",
        "reply_max_points": "INTEGER",
        # T-074 / D-091: nullable Consequence condition; a legacy Game ran Option A (``none``).
        "consequence_policy_version": "VARCHAR",
        "consequence_mode": "VARCHAR",
        # I-078: legacy Games never opted in; the §14 start guard is unchanged for them.
        "require_run_manifest": "BOOLEAN NOT NULL DEFAULT 0",
    }
    missing = [(name, sql_type) for name, sql_type in additions.items() if name not in columns]
    if missing:
        with engine.begin() as connection:
            for name, sql_type in missing:
                connection.exec_driver_sql(f"ALTER TABLE game ADD COLUMN {name} {sql_type}")

    effect_columns = {column["name"] for column in inspect(engine).get_columns("effect")}
    if "source_consequence_turn" not in effect_columns:
        # T-074 / D-091: no legacy Effect came from a consequence pass; NULL says exactly that.
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE effect ADD COLUMN source_consequence_turn INTEGER")
    if "source_inject_id" not in effect_columns:
        # T-081 / D-095: no legacy Effect came from an inject; NULL says exactly that.
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE effect ADD COLUMN source_inject_id VARCHAR")

    argument_columns = {column["name"] for column in inspect(engine).get_columns("argument")}
    if "challenger_actor_ids" not in argument_columns:
        # I-055: a legacy Argument had unrestricted opposition; NULL says exactly that.
        with engine.begin() as connection:
            connection.exec_driver_sql("ALTER TABLE argument ADD COLUMN challenger_actor_ids TEXT")

    resource_columns = {column["name"] for column in inspect(engine).get_columns("resource")}
    resource_additions = {
        "representation_kind": "VARCHAR NOT NULL DEFAULT 'attachment'",
        "media_type": "VARCHAR NOT NULL DEFAULT 'application/octet-stream'",
        "text_content": "TEXT",
        "size_bytes": "INTEGER NOT NULL DEFAULT 0",
    }
    missing_resource = [
        (name, sql_type)
        for name, sql_type in resource_additions.items()
        if name not in resource_columns
    ]
    if missing_resource:
        with engine.begin() as connection:
            for name, sql_type in missing_resource:
                connection.exec_driver_sql(f"ALTER TABLE resource ADD COLUMN {name} {sql_type}")

    resource_access_columns = {
        column["name"] for column in inspect(engine).get_columns("resourceaccess")
    }
    if "activation_id" not in resource_access_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE resourceaccess ADD COLUMN activation_id VARCHAR"
            )
    # I-099: nullable — a legacy search's result identity is unavailable, not empty.
    resource_access_additions = {
        "result_count": "INTEGER",
        "result_resource_ids": "TEXT",
        "result_set_sha256": "VARCHAR",
    }
    missing_resource_access = [
        (name, sql_type)
        for name, sql_type in resource_access_additions.items()
        if name not in resource_access_columns
    ]
    if missing_resource_access:
        with engine.begin() as connection:
            for name, sql_type in missing_resource_access:
                connection.exec_driver_sql(
                    f"ALTER TABLE resourceaccess ADD COLUMN {name} {sql_type}"
                )

    agent_run_columns = {
        column["name"] for column in inspect(engine).get_columns("agentrun")
    }
    if "activation_wall_ms" not in agent_run_columns:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "ALTER TABLE agentrun ADD COLUMN activation_wall_ms INTEGER"
            )
            # Before I-027 player latency timed the entire outer activation and was therefore
            # not a provider-wait measurement. Preserve it as activation wall-clock and mark the
            # unknowable historical provider component unavailable rather than relabeling it.
            connection.exec_driver_sql(
                "UPDATE agentrun SET activation_wall_ms = latency_ms "
                "WHERE activation_wall_ms IS NULL"
            )
            connection.exec_driver_sql(
                "UPDATE agentrun SET latency_ms = NULL WHERE role = 'player'"
            )
    agent_run_additions = {
        "context_mode": "VARCHAR",
        "memory_version": "VARCHAR",
        "activation_id": "VARCHAR",
        "reasoning_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_read_tokens": "INTEGER NOT NULL DEFAULT 0",
        "cache_write_tokens": "INTEGER NOT NULL DEFAULT 0",
        "invalid_tool_call_breakdown": "TEXT NOT NULL DEFAULT '{}'",
        "request_topology": "TEXT NOT NULL DEFAULT '{}'",
        # I-098: nullable — a legacy activation's research counts are unavailable, not zero.
        "research_tool_calls": "INTEGER",
        "research_operations": "INTEGER",
    }
    missing_agent_run = [
        (name, sql_type)
        for name, sql_type in agent_run_additions.items()
        if name not in agent_run_columns
    ]
    if missing_agent_run:
        with engine.begin() as connection:
            for name, sql_type in missing_agent_run:
                connection.exec_driver_sql(
                    f"ALTER TABLE agentrun ADD COLUMN {name} {sql_type}"
                )

    # I-082: ``create_all`` skips an existing table's indexes, so a legacy ``agentrun`` gains the
    # activation uniqueness index here. Duplicates would make this fail — an integrity refusal.
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_agent_run_activation "
            "ON agentrun (game_id, activation_id)"
        )

    if stamped < SCHEMA_VERSION:
        _require_referential_integrity(engine)
        with engine.begin() as connection:
            connection.exec_driver_sql(f"PRAGMA user_version = {SCHEMA_VERSION}")


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Yield a Session as a unit of work: commit on success, roll back on error, always close.

    Suitable for later FastAPI dependency wiring (T-027) — the transactional boundary lives
    here so callers do not each re-implement commit/rollback bookkeeping.
    """
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@contextmanager
def write_session_scope(engine: Engine) -> Iterator[Session]:
    """Serialize SQLite guard reads with their writes using ``BEGIN IMMEDIATE``."""
    session = Session(engine)
    try:
        session.connection().exec_driver_sql("BEGIN IMMEDIATE")
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


_default_engine: Engine | None = None


def get_engine() -> Engine:
    """Return the lazily-initialised default engine, built from ``settings.database_path``."""
    global _default_engine
    if _default_engine is None:
        _default_engine = engine_for_path(Settings().database_path)
    return _default_engine


def reset_default_engine() -> None:
    """Dispose and clear the cached default engine.

    A seam for teardown — used by tests to stay order-independent, and available to T-027's
    application shutdown so the process leaks no connection pool.
    """
    global _default_engine
    if _default_engine is not None:
        _default_engine.dispose()
        _default_engine = None
