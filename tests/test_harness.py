"""T-003 — test harness self-checks.

No TEST-* catalogue ID applies; these assert the harness properties required by
testing.md §2/§3 and dev/WBS.md T-003: an isolated temporary SQLite database per test,
order independence, a registered `llm` marker excluded from the default run, and a
key-free default environment.
"""

from __future__ import annotations

import os

from sqlalchemy import inspect, text
from sqlmodel import SQLModel


def test_llm_marker_registered(pytestconfig):
    markers = pytestconfig.getini("markers")
    assert any(line.startswith("llm") for line in markers), markers


def test_default_run_excludes_llm(pytestconfig):
    # addopts pins `-m "not llm"`; when the suite is instead invoked with `-m llm`,
    # this unmarked test is simply deselected, so it never fails there. The inner loop
    # `-m "not slow and not llm"` still excludes `llm`.
    markexpr = pytestconfig.getoption("markexpr")
    assert markexpr in {"not llm", "not slow and not llm"}, markexpr


def test_slow_marker_registered(pytestconfig):
    """``slow`` marks tests that play full games; ``-m "not slow"`` is the quick inner loop."""
    markers = pytestconfig.getini("markers")
    assert any(line.startswith("slow") for line in markers), markers


def test_short_game_differs_from_the_canonical_fixture_only_in_turns(short_game):
    """The short game is the canonical fixture with fewer turns; the canonical one is unchanged."""
    from app.scenarios import load_scenario
    from tests.api_helpers import FIXTURE_DIR
    from tests.benchmark_helpers import SHORT_TURNS

    canonical, short = load_scenario(FIXTURE_DIR), load_scenario(short_game)
    assert canonical.game.max_turns == 6
    assert short.game.max_turns == SHORT_TURNS < canonical.game.max_turns
    assert short.game.model_copy(update={"max_turns": 6}) == canonical.game
    assert short.actors == canonical.actors
    assert short.resources == canonical.resources
    assert short.starting_effects == canonical.starting_effects


def test_openrouter_key_absent_by_default():
    assert os.environ.get("OPENROUTER_API_KEY") is None


def _make_marker_table(engine):
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE harness_marker (id INTEGER PRIMARY KEY)"))
        conn.execute(text("INSERT INTO harness_marker (id) VALUES (1)"))


def test_temp_db_is_isolated(db_engine):
    # Write a table into this test's database.
    _make_marker_table(db_engine)
    assert "harness_marker" in inspect(db_engine).get_table_names()


def test_temp_db_starts_empty(db_engine):
    # A different test's database must not carry the table written above, regardless of the
    # order the two tests run in — proving per-test isolation and order independence.
    assert "harness_marker" not in inspect(db_engine).get_table_names()


def test_db_engine_teardown_leaves_clean_metadata(db_engine):
    # The shared SQLModel metadata must not accumulate throwaway tables between tests.
    assert "harness_marker" not in SQLModel.metadata.tables
