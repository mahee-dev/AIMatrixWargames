from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SHIPPED_DOCS = [
    ROOT / "README.md",
    ROOT / "docs" / "README.md",
    ROOT / "guide" / "README.md",
    ROOT / "guide" / "operations.md",
    ROOT / "guide" / "scenario-authoring.md",
    ROOT / "guide" / "manual.html",
    ROOT / "CONTRIBUTING.md",
]
LOCAL_LINK = re.compile(r"\[[^]]+\]\((?!https?://|mailto:|#)([^)#]+)(?:#[^)]+)?\)")


def test_t_064_shipped_operational_docs_cover_release_workflows() -> None:
    for path in SHIPPED_DOCS:
        assert path.is_file(), f"missing shipped documentation: {path.relative_to(ROOT)}"

    readme = (ROOT / "README.md").read_text()
    operations = (ROOT / "guide" / "operations.md").read_text()
    scenarios = (ROOT / "guide" / "scenario-authoring.md").read_text()

    assert "guide/README.md" in readme
    assert "uv sync --extra agents" in readme
    assert "uv run amaw run orison-crisis" in readme
    assert "uv run amaw export GAME_ID > game.json" in readme

    for variable in (
        "OPENROUTER_API_KEY",
        "AMAW_PLAYER_MODEL",
        "AMAW_UMPIRE_MODEL",
        "AMAW_ADMIN_TOKEN",
        "AMAW_DATABASE_PATH",
        "AMAW_LOG_LEVEL",
        "AMAW_REQUEST_TIMEOUT_SECONDS",
    ):
        assert variable in operations
    assert "uv run amaw benchmark" in operations  # T-054: the batch runner is shipped
    assert "--repetitions" in operations

    assert "scenario.yaml" in scenarios
    assert "turn_order" in scenarios
    assert "starting_effects" in scenarios
    assert "resources" in scenarios
    assert "docs/scenario-format.md" in scenarios

    # The contributor entry point is CONTRIBUTING.md; the guide index and README link to it and
    # to the Field Manual (D-102).
    assert "CONTRIBUTING.md" in (ROOT / "guide" / "README.md").read_text()
    assert "guide/manual.html" in readme


def test_t_064_shipped_documentation_local_links_resolve() -> None:
    for source in SHIPPED_DOCS:
        text = source.read_text()
        for raw_target in LOCAL_LINK.findall(text):
            target = (source.parent / raw_target).resolve()
            assert target.exists(), (
                f"broken link from {source.relative_to(ROOT)} to {raw_target}"
            )
