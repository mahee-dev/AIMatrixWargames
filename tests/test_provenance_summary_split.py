"""I-062 — derived reports live in ``app.summary``; ``app.provenance`` records only.

Structural guards for the split (review F6). The
report *behaviour* is proven elsewhere (``test_run_provenance.py``, ``test_i045_…``,
``test_i054_…``, ``test_i055_…``, ``test_export.py``); these tests only pin the seam.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_FUNCTIONS = (
    "build_game_summary",
    "format_game_summary",
    "build_resource_research_report",
)


def _module_ast(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _defined_names(tree: ast.Module) -> set[str]:
    return {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}


def test_i062_001_summary_module_owns_the_derived_reports() -> None:
    summary = importlib.import_module("app.summary")
    provenance = importlib.import_module("app.provenance")

    summary_defs = _defined_names(_module_ast("app/summary.py"))
    provenance_defs = _defined_names(_module_ast("app/provenance.py"))
    for name in REPORT_FUNCTIONS:
        assert callable(getattr(summary, name)), f"app.summary must expose {name}"
        assert name in summary_defs, f"app.summary must define {name}, not re-export it"
        # Absent from provenance both as a definition and as a namespace attribute — a stray
        # ``from app.summary import …`` compatibility shim would fail the second check.
        assert name not in provenance_defs, f"app.provenance still defines {name}"
        assert not hasattr(provenance, name), f"app.provenance still exposes {name}"
