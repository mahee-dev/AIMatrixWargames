"""``amaw export`` and the export-file paths shared with ``run`` and ``report``."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING
import uuid
from pathlib import Path

import typer

from amaw_cli._app import app

if TYPE_CHECKING:  # typing only: the agent stack is imported lazily inside the commands
    from app.config import Settings


def _build_export(game_id: str) -> dict:
    """The administrative export of GAME_ID from the configured database (api.md §15.1).

    Exits 2 for a malformed id and 1 for an unknown game.
    """
    # Imported lazily so `import amaw_cli` is cheap and stays framework-free.
    from app.config import Settings
    from app.database import engine_for_path
    from app.export import build_game_export
    from app.game_service import GameNotFound

    try:
        parsed = uuid.UUID(game_id)
    except ValueError as exc:
        typer.echo(f"error: {game_id!r} is not a valid game id", err=True)
        raise typer.Exit(code=2) from exc

    engine = engine_for_path(Settings().database_path)
    try:
        return build_game_export(engine, parsed)
    except GameNotFound:
        typer.echo(f"error: no game {game_id}", err=True)
        raise typer.Exit(code=1) from None
    finally:
        engine.dispose()


@app.command()
def export(game_id: str) -> None:
    """Print the complete administrative JSON export of GAME_ID to stdout (api.md §15.1)."""
    record = _build_export(game_id)

    # sort_keys keeps the printed record stable regardless of dict insertion order.
    typer.echo(json.dumps(record, indent=2, sort_keys=True))


# The files SQLite owns beside the database (rollback journal, WAL and its shared-memory index),
# spelled the way SQLite spells them: appended to the full database filename.
_SQLITE_SIBLING_SUFFIXES = ("-journal", "-wal", "-shm")


def _refuse_database_export_path(export_path: Path, database_path: Path) -> None:
    """I-097: ``--export-path`` must never name the database file or a file SQLite owns.

    Checked before the engine exists — the export would otherwise replace the database with JSON
    on success, or clobber it under a live engine on the best-effort failure path.
    """
    database = database_path.resolve()
    owned = {database, *(database.with_name(database.name + s) for s in _SQLITE_SIBLING_SUFFIXES)}
    if export_path.resolve() in owned:
        typer.echo(
            f"error: --export-path {export_path} is the SQLite database ({database_path}) or a "
            "file it owns; the export would overwrite it — choose another path",
            err=True,
        )
        raise typer.Exit(code=2)


def _default_export_path(settings: Settings, game_id: str) -> Path:
    """Where a run without ``--export-path`` leaves its export: beside the database (D-115).

    The export holds the same private data as the database, so it goes where the operator
    already keeps that, never into the working directory.
    """
    return settings.database_path.parent / "exports" / f"{game_id}.json"


def _write_export(engine, export_path: Path, game_id: str) -> None:
    """Best-effort partial administrative export after a failed run; never masks the failure."""
    try:
        from app.export import build_game_export, write_json_atomic

        write_json_atomic(export_path, build_game_export(engine, uuid.UUID(game_id)))
        logging.getLogger("amaw.cli").info(
            "wrote partial administrative export path=%s game_id=%s", export_path, game_id
        )
    except Exception as export_error:  # noqa: BLE001 - preserve original run failure
        logging.getLogger("amaw.cli").error(
            "could not write partial export path=%s error=%s",
            export_path,
            type(export_error).__name__,
        )
