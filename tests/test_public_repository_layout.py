"""I-152 acceptance: the public snapshot is fit to become the upstream (D-106, D-116).

The public repository carries the working agreement rewritten for GitHub issues, the living
decision log, and this repository's frozen process history at its original paths, so every
``D-``, ``I-`` and ``T-`` identifier the code cites resolves. Maintainer state, task briefs, run
evidence and local tool configuration stay out.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
OVERLAY = ROOT / "public_overlay"
HISTORY = {
    "dev/DECISIONS.md",
    "dev/BACKLOG.md",
    "dev/BACKLOG_RESOLVED.md",
    "dev/WBS.md",
    "dev/RUNS.md",
}
# The overlay and the maintainer layer exist only in the private repository; the public
# repository is itself a snapshot and has neither.
requires_maintainer = pytest.mark.skipif(
    not (ROOT / "dev" / "STATUS.md").is_file(), reason="builds from the private repository"
)


@pytest.fixture(scope="module")
def snapshot(tmp_path_factory: pytest.TempPathFactory) -> tuple[Path, set[str]]:
    """The public tree: built from the private repository, or the public repository itself."""
    if not (ROOT / "dev" / "STATUS.md").is_file():
        listed = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=ROOT, check=True, capture_output=True, text=True,
        ).stdout.splitlines()
        return ROOT, {path for path in listed if (ROOT / path).is_file()}
    from scripts.build_public_source import build_public_source

    output = tmp_path_factory.mktemp("i152") / "public"
    return output, build_public_source(ROOT, output)


@requires_maintainer
def test_i152_004_the_snapshot_ships_the_public_agreement_and_the_history(snapshot) -> None:
    output, copied = snapshot

    for name in ("AGENTS.md", "CLAUDE.md", "dev/README.md"):
        assert (output / name).read_bytes() == (OVERLAY / name).read_bytes(), name
    assert HISTORY <= copied
    assert any(path.startswith("dev/gates/") for path in copied)
    assert any(path.startswith("dev/plans/") for path in copied)
    assert "dev/archive/docs/original-vision/AMAW_WBS_v0.2.1.md" in copied

    excluded = ("dev/STATUS.md", "dev/tasks/", "dev/runs/", "dev/archive/tasks/", ".claude/",
                "public_overlay/", "PUBLIC_SOURCE.md", "dev/GATE3_PREPARATION.md", "dev/portability/")
    assert not [path for path in copied if path.startswith(excluded)]
    # The public agreement keeps the section numbers the code cites.
    agreement = (output / "AGENTS.md").read_text(encoding="utf-8")
    assert "## 6. Architecture rules that are not negotiable" in agreement
    assert "## 7. Technology" in agreement
    assert "dev/STATUS.md" not in agreement and "/task-start" not in agreement


def test_i152_005_every_cited_identifier_resolves_in_the_snapshot(snapshot) -> None:
    output, copied = snapshot
    decisions = (output / "dev/DECISIONS.md").read_text(encoding="utf-8")
    issues = "".join(
        (output / name).read_text(encoding="utf-8")
        for name in ("dev/BACKLOG.md", "dev/BACKLOG_RESOLVED.md")
    )
    plan = (output / "dev/WBS.md").read_text(encoding="utf-8")
    defined = {
        "D": set(re.findall(r"^### (D-\d{3})\b", decisions, re.M)),
        "I": set(re.findall(r"\b(I-\d{3})\b", issues)),
        "T": set(re.findall(r"^### (?:✅ )?(T-\d{3})\b", plan, re.M)),
    }

    unresolved: dict[str, set[str]] = {}
    for relative in sorted(copied):
        if relative.startswith("dev/") or not relative.endswith((".py", ".md", ".yaml", ".toml", ".html")):
            continue
        text = (output / relative).read_text(encoding="utf-8")
        for cited in set(re.findall(r"\b([DIT]-\d{3})\b", text)):
            if cited not in defined[cited[0]]:
                unresolved.setdefault(cited, set()).add(relative)
    assert unresolved == {}


def test_i152_006_the_snapshot_names_no_private_path(snapshot) -> None:
    output, copied = snapshot
    offenders = []
    for relative in sorted(copied):
        try:
            text = (output / relative).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        # A macOS home directory names its user; the literal is split so this file passes.
        if "/" + "Users/" in text:
            offenders.append(f"{relative}: home directory path")
        # Briefs stayed private; outside the frozen history nothing may point at one.
        if not relative.startswith("dev/") and re.search(r"dev/(?:archive/)?tasks/[\w.-]+\.md", text):
            offenders.append(f"{relative}: task brief path")
    assert offenders == []


@requires_maintainer
def test_i152_007_ai_2027_and_the_corpora_derived_from_it_are_held_back(snapshot) -> None:
    """Until the AI 2027 authors agree, the scenario and the corpora built from its runs stay out
    of the public tree, and nothing left in it depends on them (D-117)."""
    import yaml

    output, copied = snapshot
    held = ("scenarios/ai-2027/", "corpora/", "tests/test_scenario_ai_2027", "tests/test_adjudication_corpus")
    assert not [path for path in copied if path.startswith(held)]
    assert "scenarios/skaro-crisis/scenario.yaml" in copied

    source_lock = yaml.safe_load((ROOT / "scenarios/versions.lock").read_text(encoding="utf-8"))
    public_lock = yaml.safe_load((output / "scenarios/versions.lock").read_text(encoding="utf-8"))
    assert "ai-2027-branch-point" in source_lock
    assert public_lock == {k: v for k, v in source_lock.items() if k != "ai-2027-branch-point"}
    header = (ROOT / "scenarios/versions.lock").read_text(encoding="utf-8").split("\n\n")[0]
    assert (output / "scenarios/versions.lock").read_text(encoding="utf-8").startswith(header)


# --- TEST-I169: the public repository carries development on its own --------------------------


@requires_maintainer
def test_i169_001_maintainer_tools_and_corpus_scripts_stay_out_and_run_evidence_is_ignored(
    snapshot,
) -> None:
    """The builder needs the private overlay and the corpus scripts need the held-back corpora,
    so neither ships; run exports carry private Actor material, so the public tree ignores
    ``dev/runs/`` while this repository keeps committing its retained runs."""
    output, copied = snapshot
    for path in (
        "scripts/build_public_source.py",
        "tests/test_release_public_source.py",
        "scripts/extract_adjudication_case.py",
        "scripts/run_adjudication_baseline.py",
        "scripts/run_adjudication_v2.py",
        "scripts/select_heldout_cases.py",
    ):
        assert path not in copied, path
    assert "scripts/lock_scenarios.py" in copied
    assert "tests/test_public_repository_layout.py" in copied

    public_ignore = (output / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "dev/runs/" in public_ignore
    assert "dev/runs/" not in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()


@requires_maintainer
def test_i169_002_the_builder_names_a_tag_for_every_shipped_scenario_version(snapshot) -> None:
    """Scenario tags in this repository point at private commits; the public root commit gets
    one ``scenario/<id>/<version>`` tag per shipped package at its current version (§20)."""
    import yaml

    from scripts.build_public_source import scenario_tags

    output, _ = snapshot
    expected = set()
    for manifest in (output / "scenarios").glob("*/scenario.yaml"):
        scenario = yaml.safe_load(manifest.read_text(encoding="utf-8"))["scenario"]
        expected.add(f"scenario/{scenario['id']}/{scenario['version']}")
    assert expected and scenario_tags(output) == sorted(expected)
    assert "scenario/svensk-regeringsbildning-2026/7.1.0" in expected
    assert not any("ai-2027" in tag for tag in expected)


def test_i169_003_runs_md_is_living_in_the_public_repository(snapshot) -> None:
    output, _ = snapshot
    readme = " ".join((output / "dev" / "README.md").read_text(encoding="utf-8").split())
    row = next(line for line in readme.split("| `") if line.startswith("RUNS.md`"))
    assert "Living" in row and "Frozen" not in row
    agreement = " ".join((output / "AGENTS.md").read_text(encoding="utf-8").split())
    assert "new runs are recorded in `dev/RUNS.md`" in agreement


@requires_maintainer
def test_i169_004_scenario_baseline_fixtures_equal_the_commits_they_replace() -> None:
    baselines = ROOT / "tests" / "fixtures" / "scenario-baselines"
    for fixture, commit, path in (
        ("skaro-crisis-1.1.0.yaml", "994c747", "scenarios/skaro-crisis/scenario.yaml"),
        ("swedish-government-7.0.0.yaml", "a6b4a83", "scenarios/swedish-government/scenario.yaml"),
    ):
        shown = subprocess.run(
            ["git", "show", f"{commit}:{path}"], cwd=ROOT, check=True, capture_output=True, text=True
        ).stdout
        assert (baselines / fixture).read_text(encoding="utf-8") == shown, fixture
