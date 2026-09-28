"""I-075 — the active task directory does not silently keep resolved-issue briefs.

A process-hygiene guard from the 2026-09-18 reassessment
(``dev/plans/2026-09-18-post-i063-reassessment.md §1``).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


DEV = Path(__file__).parent.parent / "dev"
_ISSUE_ID = re.compile(r"I-\d{3}")
# A resolution is a ``### I-0NN — …`` heading (batched: ``### I-0NN / I-0MM — …``); prose
# cross-references to still-open issues do not count.
_RESOLVED_HEADING = re.compile(r"^### ((?:I-\d{3}\s*/?\s*)+)—", re.MULTILINE)


@pytest.mark.skipif(
    not (DEV / "STATUS.md").is_file(), reason="task briefs are private (D-116)"
)
def test_i075_002_no_resolved_issue_briefs_left_active() -> None:
    # Only ``I-*`` briefs are governed by the resolved backlog; ``T-*`` briefs belong to the WBS.
    resolved_log = (DEV / "BACKLOG_RESOLVED.md").read_text(encoding="utf-8")
    resolved = {
        issue_id
        for heading in _RESOLVED_HEADING.findall(resolved_log)
        for issue_id in _ISSUE_ID.findall(heading)
    }
    assert {"I-022", "I-027", "I-071", "I-072", "I-073"} <= resolved
    assert "I-038" not in resolved  # mentioned in prose only; still open
    stale: list[str] = []
    for brief in sorted((DEV / "tasks").glob("I-*.md")):
        if brief.name.endswith(".PROMPT.md"):
            continue
        ids = set(_ISSUE_ID.findall(brief.name))
        assert ids, brief.name
        if ids <= resolved:
            stale.append(f"{brief.name} ({', '.join(sorted(ids))} all resolved)")
    assert stale == [], f"resolved-issue briefs still in dev/tasks/: {stale}"
