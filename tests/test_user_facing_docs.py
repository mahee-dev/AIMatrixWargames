"""TEST-I168: the pages a newcomer reads are self-contained and agree with the adjudication rule.

Process identifiers (`D-`, `I-`, `T-` numbers) point into the maintainers' history and mean
nothing to a first-time reader, so the README, the guides and the scenario READMEs state each
rule in words. The normative `docs/` and the code keep their identifiers for traceability.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

USER_FACING = [
    ROOT / "README.md",
    ROOT / "guide" / "README.md",
    ROOT / "guide" / "operations.md",
    ROOT / "guide" / "scenario-authoring.md",
    ROOT / "scenarios" / "README.md",
    *sorted((ROOT / "scenarios").glob("*/README.md")),
]

PROCESS_ID = re.compile(r"\b[DIT]-\d{3}\b")


def _flat(path: Path) -> str:
    return " ".join(path.read_text(encoding="utf-8").split())


def test_i168_001_user_facing_pages_carry_no_process_identifiers() -> None:
    found = {
        str(path.relative_to(ROOT)): sorted(set(PROCESS_ID.findall(path.read_text(encoding="utf-8"))))
        for path in USER_FACING
    }
    assert {k: v for k, v in found.items() if v} == {}
    for path in USER_FACING:
        assert "Lean MVP" not in path.read_text(encoding="utf-8"), path


def test_i168_002_readme_example_follows_the_adjudication_rule_and_does_not_overclaim() -> None:
    readme = _flat(ROOT / "README.md")
    # The example must not show a roll on another played Actor's decision (agents.md §23).
    assert "endorsement is likely" not in readme
    assert "umpire-v1.7" in readme
    assert "No comparative model results are published yet" in readme
