"""Shared pytest fixtures for AMAW (T-003, WBS 1.1.3).

Provides the temporary-SQLite database each test gets in isolation, and keeps the default
run hermetic and key-free (testing.md §3: "Default CI should be able to run without an
OpenRouter key"; "Tests must not depend on execution order").

The engine is built by the application factory in ``app.database`` (T-007), so the harness
exercises the same engine and schema-creation path as the app — with each test getting its
own isolated temp database (the basis of order independence).
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine

from app import database


@pytest.fixture(autouse=True)
def _hermetic_env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make non-``llm`` tests independent of the developer's real environment.

    Removes ``OPENROUTER_API_KEY`` so the default run is provably key-free even on a machine
    that has one exported. ``llm``-marked tests (layer 4) keep the key, since they need it.
    """
    if "llm" not in request.keywords:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """A unique on-disk SQLite path for a single test (auto-removed with ``tmp_path``)."""
    return tmp_path / "amaw-test.db"


@pytest.fixture
def db_engine(db_path: Path) -> Iterator[Engine]:
    """A function-scoped app engine on an isolated temp database, with tables created.

    Built via ``app.database.engine_for_path`` and populated with the full, stamped schema via
    ``initialize_database`` — the same path the application uses. Disposed on teardown, so
    no connection or file handle leaks between tests. Each test gets a fresh database with an
    empty schema — the basis of order independence.
    """
    engine = database.engine_for_path(db_path)
    database.initialize_database(engine)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture(scope="session")
def short_game(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """The canonical fixture with ``max_turns`` lowered, for tests that only check bookkeeping.

    Derived once per session into a temporary directory; the canonical fixture is never edited
    (testing.md §4), and tests that need the six-turn game keep playing it (I-135).
    """
    from tests.benchmark_helpers import derive_short_fixture

    return derive_short_fixture(tmp_path_factory.mktemp("short-game") / "deterministic-game")
