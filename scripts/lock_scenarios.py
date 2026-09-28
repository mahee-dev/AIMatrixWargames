"""Keep ``scenarios/versions.lock`` in step with the scenario library.

A scenario's declared version must always name the same content, because runs are pooled by
id and version (scenario-format §20). The lock records the two hashes a run manifest seals for
every released version, as append-only history. Changed content under a locked version is
refused with an instruction to bump ``scenario.version`` (D-104).

    uv run python scripts/lock_scenarios.py          # append newly declared versions
    uv run python scripts/lock_scenarios.py --check  # report drift, change nothing
"""

from __future__ import annotations

import argparse
import copy
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
LIBRARY = ROOT / "scenarios"
LOCK = LIBRARY / "versions.lock"
HEADER = """\
# Every released scenario version and the hashes a run manifest seals for it (D-104).
# Append-only history. After bumping `scenario.version`, run:
#   uv run python scripts/lock_scenarios.py
# A package whose content no longer matches its declared version fails TEST-I143-001.
"""


class LockConflict(ValueError):
    """A locked version's content changed without a version bump."""


@dataclass(frozen=True)
class ScenarioVersion:
    scenario_id: str
    directory: str
    version: str
    scenario_sha256: str
    resources_sha256: str

    @property
    def hashes(self) -> dict[str, str]:
        return {"scenario_sha256": self.scenario_sha256, "resources_sha256": self.resources_sha256}


def live_packages(library: Path) -> list[Path]:
    """Return the scenario packages in ``library``; ``archive/`` holds frozen copies."""
    return sorted(
        path
        for path in library.iterdir()
        if path.is_dir() and path.name != "archive" and (path / "scenario.yaml").is_file()
    )


def current_versions(library: Path) -> list[ScenarioVersion]:
    from app.scenarios import load_scenario

    result = []
    for package in live_packages(library):
        loaded = load_scenario(package)
        result.append(
            ScenarioVersion(
                scenario_id=loaded.scenario.id,
                directory=package.name,
                version=str(loaded.scenario.version),
                scenario_sha256=loaded.scenario_content_sha256,
                resources_sha256=loaded.resource_manifest_sha256,
            )
        )
    return result


def read_lock(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    for entry in data.values():
        entry["versions"] = {str(key): value for key, value in entry["versions"].items()}
    return data


def stale_entries(lock: dict[str, Any], current: list[ScenarioVersion]) -> list[str]:
    """Describe every package whose declared version and content disagree with the lock."""
    problems = []
    for item in current:
        entry = lock.get(item.scenario_id)
        locked = entry["versions"].get(item.version) if entry else None
        if locked is None:
            problems.append(
                f"{item.directory}: version {item.version!r} is not locked; "
                "run `uv run python scripts/lock_scenarios.py`"
            )
        elif locked != item.hashes:
            problems.append(
                f"{item.directory}: content changed under locked version {item.version!r}; "
                "bump scenario.version, then run `uv run python scripts/lock_scenarios.py`"
            )
        elif entry["directory"] != item.directory:
            problems.append(
                f"{item.scenario_id}: locked in {entry['directory']!r}, found in {item.directory!r}"
            )
    return problems


def update_lock(lock: dict[str, Any], current: list[ScenarioVersion]) -> dict[str, Any]:
    """Return ``lock`` with each current version appended; never rewrite a locked version."""
    updated = copy.deepcopy(lock)
    for item in current:
        entry = updated.setdefault(item.scenario_id, {"directory": item.directory, "versions": {}})
        locked = entry["versions"].get(item.version)
        if locked is not None and locked != item.hashes:
            raise LockConflict(
                f"{item.directory}: content changed under locked version {item.version!r}; "
                "bump scenario.version"
            )
        entry["directory"] = item.directory
        entry["versions"][item.version] = item.hashes
    return updated


def write_lock(path: Path, lock: dict[str, Any]) -> None:
    body = yaml.safe_dump(lock, sort_keys=False, allow_unicode=True, width=100)
    path.write_text(HEADER + "\n" + body, encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="report drift without writing")
    args = parser.parse_args(argv)

    lock = read_lock(LOCK)
    current = current_versions(LIBRARY)
    if args.check:
        problems = stale_entries(lock, current)
        print("\n".join(problems) or "scenarios/versions.lock is up to date")
        return 1 if problems else 0
    try:
        updated = update_lock(lock, current)
    except LockConflict as exc:
        print(exc, file=sys.stderr)
        return 1
    write_lock(LOCK, updated)
    added = [
        f"{item.scenario_id} {item.version}"
        for item in current
        if item.version not in lock.get(item.scenario_id, {}).get("versions", {})
    ]
    print("locked: " + ", ".join(added) if added else "scenarios/versions.lock is up to date")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(ROOT))
    raise SystemExit(main())
