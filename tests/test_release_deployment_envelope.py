"""I-123 — the supported deployment envelope is stated, and the code still matches it.

The transaction guarantees (INV-GAME-004/005, the state-machine §25 protected races) hold
because every guarded write runs under SQLite's single-writer ``BEGIN IMMEDIATE`` on one local
database file, served by one application instance. D-100 makes that envelope normative in TRD
§26.1, so leaving it is a design change rather than a configuration change. These guards keep
the statement in the TRD and keep it true of the code. Test IDs ``TEST-I123-001/002`` are the
task's own.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import event

from app.database import engine_for_path, write_session_scope


ROOT = Path(__file__).resolve().parents[1]
TRD = ROOT / "docs/TRD.md"


def _persistence_section() -> str:
    text = TRD.read_text(encoding="utf-8")
    return text.split("\n# 26. Persistence", 1)[1].split("\n# 27. ", 1)[0]


def test_i123_001_trd_states_the_deployment_envelope() -> None:
    """TRD §26.1 exists and cites D-100; ``test_i123_002`` keeps the code side true."""
    section = _persistence_section()

    assert "Supported deployment envelope" in section
    assert "D-100" in section


def test_i123_002_engine_is_sqlite_and_writes_begin_immediate(tmp_path: Path) -> None:
    engine = engine_for_path(tmp_path / "amaw.db")
    statements: list[str] = []
    event.listen(
        engine,
        "before_cursor_execute",
        lambda _conn, _cur, statement, *_rest: statements.append(statement),
    )

    assert engine.dialect.name == "sqlite"
    with write_session_scope(engine):
        pass

    assert statements and statements[0] == "BEGIN IMMEDIATE"
