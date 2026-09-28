"""I-089 — the repository is publishable as open source without moving code.

Guard tests for the release-readiness conditions the backlog entry and
``dev/plans/2026-09-18-public-release-readiness.md`` state observably: the licence (D-080), the
retained run evidence (D-081), the prompt index, the scenario index, and the orientation and
community files. No ``docs/testing.md`` ID governs this item; the tests pin what the plan says
a context-free clone must find.
"""

from __future__ import annotations

import re
import subprocess
import tomllib
from pathlib import Path

import pytest

from agents.player import PLAYER_PROMPT_VERSIONS, REPLY_PLAYER_PROMPT_VERSIONS
from agents.umpire import UMPIRE_PROMPT_VERSIONS

ROOT = Path(__file__).resolve().parent.parent
# The public source snapshot ships without dev/ (D-083); process-record checks skip there.
# The public repository ships dev/ history too (D-116); only the private one keeps STATUS.md.
requires_dev = pytest.mark.skipif(
    not (ROOT / "dev" / "STATUS.md").is_file(), reason="maintainer process state is private (D-116)"
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _tracked(prefix: str) -> set[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", prefix], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout
    return {line for line in out.splitlines() if line}


# --- TEST-I089-001: licence -----------------------------------------------------------------


def test_i089_001_mit_licence_declared_everywhere():
    licence = _read("LICENSE")
    assert licence.startswith("MIT License")
    assert "Copyright (c) 2026 Martin Heumannbauer" in licence
    assert "Permission is hereby granted, free of charge" in licence

    project = tomllib.loads(_read("pyproject.toml"))["project"]
    assert project["license"] == "MIT"

    assert "SPDX-License-Identifier: MIT" in _read("README.md")


# --- TEST-I089-002: prompt index --------------------------------------------------------------


def _current_prompt_versions() -> set[str]:
    return (
        set(PLAYER_PROMPT_VERSIONS.values())
        | set(REPLY_PLAYER_PROMPT_VERSIONS.values())
        | set(UMPIRE_PROMPT_VERSIONS)
    )


def test_i089_002_prompt_indexes_name_every_file_and_mark_the_loaded_versions_current():
    files = sorted(p.name for p in (ROOT / "docs" / "prompts").glob("*.md") if p.name != "README.md")
    assert files, "docs/prompts is empty"
    prompts_readme = _read("docs/prompts/README.md")

    # `docs/prompts/README.md` is the one prompt index; the specification index links to it
    # rather than keeping a second list that can drift (D-102).
    assert "prompts/README.md" in _read("docs/README.md")
    for name in files:
        assert name in prompts_readme, f"docs/prompts/README.md does not list {name}"

    # Whatever the index marks current must be what the code loads — nothing else.
    marked_current = set(re.findall(r"`((?:player|umpire)-v[\d.]+)\.md`[^\n]*current", prompts_readme))
    assert marked_current == _current_prompt_versions()
    for version in _current_prompt_versions():
        row = next((line for line in prompts_readme.splitlines() if f"{version}.md" in line), "")
        assert "current" in row, f"docs/prompts/README.md does not mark {version} current"


# --- TEST-I089-003: scenario index ------------------------------------------------------------


def test_i089_003_scenario_index_covers_every_package():
    scenarios = ROOT / "scenarios"
    packages = sorted(p.name for p in scenarios.iterdir() if p.is_dir() and p.name != "archive")
    assert packages, "scenarios/ has no packages"
    index = _read("scenarios/README.md")
    for name in packages:
        assert f"`{name}`" in index
        assert (scenarios / name / "README.md").is_file(), f"{name} has no README.md"
    assert "`archive/`" in index
    assert (scenarios / "archive" / "README.md").is_file()


# --- TEST-I089-004: orientation and community files -------------------------------------------


def test_i089_004_orientation_and_community_files_exist_and_readme_is_self_contained():
    for rel in ("CONTRIBUTING.md", "SECURITY.md", "app/README.md", "docs/prompts/README.md"):
        assert (ROOT / rel).is_file(), f"{rel} missing"

    app_readme = _read("app/README.md")
    modules = sorted(p.stem for p in (ROOT / "app").glob("*.py") if p.stem != "__init__")
    for module in modules:
        assert f"`{module}`" in app_readme, f"app/README.md does not name app/{module}.py"

    readme = _read("README.md")
    assert "<repository-url>" not in readme
    assert "dev/STATUS.md" not in readme


@requires_dev
def test_i089_004_maintainer_repository_has_a_process_readme():
    assert (ROOT / "dev/README.md").is_file()


# --- TEST-I089-005: retained run evidence (D-081, D-083) --------------------------------------

# D-081 retains a deliberately named set here and publishes the rest to `amaw-runs`; D-083 keeps
# newer evidence in this repository while that owner step is pending, naming the current
# directories "rather than pretending the post-I-106 evidence is absent". Adding a directory to
# this set is the retention decision — read it first (I-111 brief records the audit).
RETAINED_RUN_DIRS = {
    "dev/runs/2026-09-12-db54f331",  # run 6 — the smallest complete canonical game
    "dev/runs/2026-09-13-i027-deepseek-v4-flash",  # the I-027 report, linked from STATUS
    "dev/runs/t060-batch-smoke-20260918",  # run 10 — I-076 evidence
    "dev/runs/t060-batch-smoke-20260919",  # run 11 — first green provider batch
    "dev/runs/t060-batch-smoke-20260921",  # run 12 — I-106 failure evidence
    "dev/runs/t060-batch-smoke-20260921-post-i106",  # run 13 — green post-I-106 rerun
    "dev/runs/t060-batch-smoke-20260922-current",  # run 14 — I-110 mixed-failure evidence
    "dev/runs/i100-search-contract-20260922",  # run 15 — the I-100 report and metrics
    "dev/runs/i100-search-contract-20260922-v1",  # run 15 — raw search-v1 arm
    "dev/runs/i100-search-contract-20260922-v2",  # run 15 — raw search-v2 arm
    "dev/runs/t075-skaro-crisis-20260923",  # run 17 — T-075 portability acceptance run
    "dev/runs/i116-skaro-crisis-20260924",  # run 18 — I-116 validation run (1.1.0)
    "dev/runs/i146-ai-2027-20260925",  # run 19 — first ai-2027 playtest (1.0.0)
    "dev/runs/t067-adjudication-baseline",  # run 20 — T-067 rated single-Umpire baseline
    "dev/runs/i156-adjudication-v2-baseline",  # run 21 — released Umpire on corpus v2
    "dev/runs/i156-adjudication-v2-v1-7",  # run 21 — umpire-v1.7 on corpus v2
    "dev/runs/i156-adjudication-v2-rating",  # run 21 — blinded sheet, key, ratings, summary
    "dev/runs/swedish-government-7.0.0-20260927-221639",  # run 22 — first Swedish 7.0.0 playtest
    "dev/runs/i160-northern-strait-umpire-v13-20260928-110000",  # run 23 — umpire-v1.3 arm
    "dev/runs/i160-northern-strait-umpire-v16-20260928-112326",  # run 23 — umpire-v1.6 arm
    "dev/runs/i160-northern-strait-umpire-v17-20260928-110000",  # run 23 — umpire-v1.7 arm
}


@requires_dev
def test_i089_005_tracked_run_evidence_is_exactly_the_retained_set():
    tracked_dirs = {"/".join(path.split("/")[:3]) for path in _tracked("dev/runs")}
    assert tracked_dirs == RETAINED_RUN_DIRS
    for rel in RETAINED_RUN_DIRS:
        assert (ROOT / rel).is_dir()
    runs = _read("dev/RUNS.md")
    assert "amaw-runs" in runs
