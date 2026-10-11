"""T-066 release-package inventory and synchronized user-facing metadata."""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.export import EXPORT_FORMAT_VERSION


ROOT = Path(__file__).resolve().parents[1]


def test_t066_release_inventory_and_version() -> None:
    required = (
        "README.md",
        "LICENSE",  # D-080 (I-089)
        ".env.example",
        "MANIFEST.in",
        "pyproject.toml",
        "uv.lock",
        "amaw_cli/__init__.py",
        "amaw_unattended.py",
        "app/export.py",
        "tests/test_integration_six_turn.py",
        "scenarios/orison-crisis/scenario.yaml",
        "scenarios/orison-crisis/README.md",
    )
    assert all((ROOT / path).is_file() for path in required)
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    assert metadata["name"] == "amaw"
    assert metadata["version"] == "0.1.0"
    assert metadata["license"] == "MIT"  # D-080 (I-089)
    assert metadata["scripts"]["amaw"] == "amaw_cli:app"
    assert metadata["scripts"]["amaw-unattended"] == "amaw_unattended:main"
    environment = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert "AMAW_PLAYER_ACTIVATION_MODE=single_shot" in environment


def test_t066_quick_start_and_export_guide_match_the_release() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    operations = (ROOT / "guide/operations.md").read_text(encoding="utf-8")
    assert "uv run amaw run orison-crisis" in readme
    assert f'export_format_version: "{EXPORT_FORMAT_VERSION}"' in operations
    assert all(
        field in operations
        for field in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "invalid_tool_call_breakdown",
            "request_topology",
        )
    )


def test_i059_docs_example_yaml_is_in_the_sdist() -> None:
    # I-059: benchmark-methodology.md §4.2 and guide/operations.md name
    # docs/analysis-plan-example.yaml as the complete example plan, so the sdist that ships
    # those documents must ship the file too — the `docs` include has to cover `*.yaml`.
    assert (ROOT / "docs/analysis-plan-example.yaml").is_file()
    manifest = (ROOT / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    docs_includes = [
        line.split()[2:] for line in manifest if line.startswith("recursive-include docs ")
    ]
    assert docs_includes, "MANIFEST.in has no recursive-include for docs/"
    assert any("*.yaml" in patterns for patterns in docs_includes)
