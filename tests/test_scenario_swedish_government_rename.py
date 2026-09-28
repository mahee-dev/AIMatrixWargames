"""I-144 — the Swedish scenario is bundled as ``swedish-government``; the version is not in the name.

Scenario versions live in ``scenario.version`` and ``scenarios/versions.lock`` (D-104). The old
name ``swedish-government-v6`` is not an alias: it is an unknown name like any other, and the
error lists ``swedish-government`` among the names that work (D-119). ``TEST-I144-001`` is
I-144's own; ``TEST-I170-001`` replaced its retired-name test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from amaw_resources import (
    SUPPORTED_BUNDLED_SCENARIOS,
    RuntimeResourceError,
    bundled_scenario,
    resolve_scenario,
)
from app.scenarios import load_scenario
from scripts.lock_scenarios import read_lock


ROOT = Path(__file__).resolve().parents[1]


def test_i144_001_swedish_scenario_is_bundled_without_a_version_in_its_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert "swedish-government" in SUPPORTED_BUNDLED_SCENARIOS
    assert "swedish-government-v6" not in SUPPORTED_BUNDLED_SCENARIOS
    monkeypatch.chdir(tmp_path)
    with resolve_scenario("swedish-government") as path:
        assert load_scenario(path).scenario.id == "svensk-regeringsbildning-2026"
    lock = read_lock(ROOT / "scenarios" / "versions.lock")
    assert lock["svensk-regeringsbildning-2026"]["directory"] == "swedish-government"


def test_i170_001_the_old_name_is_an_ordinary_unknown_name(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(RuntimeResourceError, match="no bundled scenario 'swedish-government-v6'"):
        with bundled_scenario("swedish-government-v6"):
            pass
    with pytest.raises(RuntimeResourceError) as raised:
        with resolve_scenario("swedish-government-v6"):
            pass
    message = str(raised.value)
    assert message.startswith("no scenario 'swedish-government-v6'; available: ")
    assert "swedish-government" in message
    assert "scenario/" not in message
