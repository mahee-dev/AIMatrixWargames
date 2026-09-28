"""I-143 — a scenario's declared version is locked to its content hashes.

Runs are comparable only when the declared scenario version names the same content
(scenario-format §20). ``scenarios/versions.lock`` records the hashes every released version
had, so content that changes without a version bump fails here rather than silently
producing runs that look comparable (D-104). Test IDs ``TEST-I143-001…004`` are the task's own
(I-143 in ``dev/BACKLOG_RESOLVED.md``).
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

import pytest

from scripts.lock_scenarios import (
    LockConflict,
    current_versions,
    live_packages,
    read_lock,
    stale_entries,
    update_lock,
)


ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "scenarios"
LOCK = SCENARIOS / "versions.lock"
SEMVER = re.compile(r"^\d+\.\d+\.\d+$")
# Integer versions released before D-104. They stay valid until the package's next bump, which
# must be semver; no other non-semver version may be added.
LEGACY_VERSIONS = {("northern-strait", "1"), ("svensk-regeringsbildning-2026", "6")}


def test_i143_001_every_live_package_matches_its_locked_hashes() -> None:
    problems = stale_entries(read_lock(LOCK), current_versions(SCENARIOS))
    assert not problems, "\n".join(problems)


def test_i143_002_lock_covers_the_live_packages_with_semver_versions() -> None:
    lock = read_lock(LOCK)
    directories = {entry["directory"] for entry in lock.values()}
    assert directories == {path.name for path in live_packages(SCENARIOS)}
    for scenario_id, entry in lock.items():
        assert entry["versions"], f"{scenario_id} has no locked version"
        for version, hashes in entry["versions"].items():
            assert set(hashes) == {"scenario_sha256", "resources_sha256"}
            assert SEMVER.match(version) or (scenario_id, version) in LEGACY_VERSIONS, (
                f"{scenario_id} {version!r} is not MAJOR.MINOR.PATCH"
            )


def test_i143_003_every_locked_version_has_a_changelog_section() -> None:
    for scenario_id, entry in read_lock(LOCK).items():
        changelog = SCENARIOS / entry["directory"] / "CHANGELOG.md"
        assert changelog.is_file(), f"{entry['directory']} has no CHANGELOG.md"
        headings = set(re.findall(r"^## (\S+)", changelog.read_text(encoding="utf-8"), re.M))
        for version in entry["versions"]:
            assert version in headings, f"{changelog.relative_to(ROOT)} has no '## {version}'"


def test_i143_004_lock_tool_appends_new_versions_and_refuses_rewrites(tmp_path: Path) -> None:
    library = tmp_path / "scenarios"
    shutil.copytree(SCENARIOS / "northern-strait", library / "northern-strait")
    lock = update_lock({}, current_versions(library))
    assert list(lock["northern-strait"]["versions"]) == ["1"]

    manifest = library / "northern-strait" / "scenario.yaml"
    text = manifest.read_text(encoding="utf-8")
    manifest.write_text(text.replace("max_turns: 6", "max_turns: 5"), encoding="utf-8")
    with pytest.raises(LockConflict, match="bump"):
        update_lock(lock, current_versions(library))
    assert stale_entries(lock, current_versions(library))

    manifest.write_text(
        text.replace("max_turns: 6", "max_turns: 5").replace("version: 1", "version: 1.1.0"),
        encoding="utf-8",
    )
    lock = update_lock(lock, current_versions(library))
    assert list(lock["northern-strait"]["versions"]) == ["1", "1.1.0"]
    assert not stale_entries(lock, current_versions(library))
