"""I-145 — the Swedish scenario 7.0.0 describes the real post-election state at a fixed freeze.

Version 7.0.0 replaces v6's pre-final seat fixture and fictional procedure with the result
Valmyndigheten fixed on 2026-09-19 and the actual procedural state at the 2026-09-25 12:00
freeze. Allegations stay in a Resource that marks what is established and what is not; they
never become starting state or private instructions. Test IDs ``TEST-I145-001…004`` are the
task's own (I-145 in ``dev/BACKLOG_RESOLVED.md``).
"""

from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.scenarios import load_scenario


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "scenarios" / "swedish-government"
BUNDLED = ROOT / "amaw_resources" / "scenarios" / "swedish-government"
FREEZE = "Fryst informationsläge: 25 september 2026 kl. 12.00."
OFFICIAL_2026 = {"S": 99, "M": 70, "SD": 62, "C": 25, "V": 30, "KD": 22, "MP": 22, "L": 19}
INTEGRITY_MARKERS = ("borlänge", "valfusk", "valintegritet", "förundersökning", "abdukardir")


@pytest.fixture(scope="module")
def loaded():  # noqa: ANN201
    return load_scenario(PACKAGE)


def _seats() -> dict[str, int]:
    with (PACKAGE / "resources" / "valresultat_2026.csv").open(encoding="utf-8") as handle:
        return {row["parti"]: int(row["mandat"]) for row in csv.DictReader(handle)}


def test_i145_001_version_7_uses_the_official_2026_seat_allocation(loaded) -> None:  # noqa: ANN001
    assert str(loaded.scenario.version) == "7.1.0"
    seats = _seats()
    assert seats == OFFICIAL_2026
    assert sum(seats.values()) == 349
    assert sum(seats[p] for p in ("S", "V", "MP", "C")) == 176
    assert sum(seats[p] for p in ("M", "SD", "KD", "L")) == 173
    assert {party.lower() for party in seats} == set(loaded.actors)


def test_i145_002_starting_state_is_the_real_procedure_without_allegations(loaded) -> None:  # noqa: ANN001
    effects = "\n".join(effect.description for effect in loaded.starting_effects).lower()
    assert "fiktivt" not in effects
    for fact in ("19 september", "17 september", "övergångsregering", "sonderingsuppdrag"):
        assert fact in effects, fact
    assert "andersson" in effects and "kristersson" in effects
    for marker in INTEGRITY_MARKERS:
        assert marker not in effects, marker
    for actor_id, actor in loaded.actors.items():
        private = " ".join([actor.private_brief, *actor.private_information]).lower()
        for marker in INTEGRITY_MARKERS:
            assert marker not in private, f"{actor_id} private text names {marker}"


def test_i145_003_rules_kernel_and_sourced_resources_at_the_freeze(loaded) -> None:  # noqa: ANN001
    rules = {rule.id: rule.text for rule in loaded.rules}
    assert 3 <= len(rules) <= 8, "the kernel stays small; explanation lives in regeringsregler"
    text = " ".join(rules.values())
    assert "175" in text and "OKÄND" in text and "AVSTÅR" in text
    assert "valresultat_2026.csv" in text

    declared = {resource.id: resource for resource in loaded.resources}
    assert {"regeringsbildning-status", "valintegritet-2026", "forhandlingsfragor"} <= set(declared)
    for resource in declared.values():
        if not resource.path.endswith(".md") or resource.id == "regeringsregler":
            continue
        body = (PACKAGE / resource.path).read_text(encoding="utf-8")
        assert FREEZE in body, f"{resource.path} does not state the freeze"
        sources = body.split("## Källor", 1)
        assert len(sources) == 2 and "https://" in sources[1], f"{resource.path} cites no sources"


def test_i145_004_package_and_bundle_hold_the_same_runtime_files() -> None:
    assert not (PACKAGE / "resources" / "VALRESULTAT_REQUIRED.md").exists()

    def runtime(root: Path) -> dict[str, bytes]:
        files = [root / "scenario.yaml", *sorted((root / "resources").iterdir())]
        return {path.relative_to(root).as_posix(): path.read_bytes() for path in files}

    assert runtime(BUNDLED) == runtime(PACKAGE)
