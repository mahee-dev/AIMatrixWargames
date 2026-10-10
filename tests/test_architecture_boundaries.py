"""Import boundaries between the game core, the agents and the CLI, in one table.

- The FastAPI core (``app/``) never imports an agent framework, and OpenRouter integration lives
  outside the core game domain (AGENTS.md §6.1–§6.2, TRD §4).
- The player side (``agents/``) talks to the game over HTTP only, never through app internals.
- Inside ``app/``, recording and reporting stay one-directional (I-062), and the context builder
  reads through queries, not the mutation service (I-064), and the Actor-facing routes never
  import the administrative export (I-153).

Framework checks run in a clean interpreter (D-017). A session-global ``sys.modules`` assertion
would fail falsely once any other test imports ``pydantic_ai``; a subprocess proves that
importing the module imports no agent framework, whatever the rest of the session loaded.
Static checks read the module's source with ``ast``.
"""

from __future__ import annotations

import ast
import subprocess
import sys

import pytest

from tests.api_helpers import REPO_ROOT

_AGENT_FRAMEWORK = ("pydantic_ai",)
_AGENTS_PACKAGE = ("pydantic_ai", "agents", "agents.consequence", "agents.model_client")

# (modules imported together in a clean interpreter, modules that must stay unloaded)
_CLEAN_IMPORTS = [
    pytest.param(("app",), _AGENTS_PACKAGE, id="every-app-module"),
    pytest.param(("app.export", "amaw_cli"), _AGENT_FRAMEWORK, id="export-and-cli"),
    pytest.param(("agents.umpire", "app.game_service"), _AGENT_FRAMEWORK, id="ai-umpire"),
    pytest.param(("amaw_cli", "benchmark.runner"), _AGENT_FRAMEWORK, id="TEST-T054-005"),
    pytest.param(
        ("app.consequences", "app.game_service", "app.api", "app.export"),
        _AGENTS_PACKAGE,
        id="TEST-T074-010",
    ),
    # #16: the after-action report reads an export and needs no agent code or provider client.
    pytest.param(("aar.model", "aar.markdown"), _AGENTS_PACKAGE, id="aar-report"),
]


@pytest.mark.parametrize(("modules", "forbidden"), _CLEAN_IMPORTS)
def test_import_leaves_the_agent_side_unloaded(
    modules: tuple[str, ...], forbidden: tuple[str, ...]
) -> None:
    # ``app`` alone stands for every module in the package, imported one by one.
    check = (
        "import importlib, pkgutil, sys\n"
        f"for name in {list(modules)!r}:\n"
        "    module = importlib.import_module(name)\n"
        "    if name == 'app':\n"
        "        for info in pkgutil.iter_modules(module.__path__):\n"
        "            importlib.import_module('app.' + info.name)\n"
        f"bad = [m for m in {list(forbidden)!r} if m in sys.modules]\n"
        "assert not bad, bad\n"
        "print('ok')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", check], capture_output=True, text=True, cwd=REPO_ROOT
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ok"


def test_t074_010_consequence_resolver_core_has_no_provider_client() -> None:
    """TEST-T074-010: ``app/consequences.py`` neither calls a provider nor imports ``agents``."""
    source = (REPO_ROOT / "app" / "consequences.py").read_text(encoding="utf-8")
    assert "import httpx" not in source and "from agents" not in source


def _imported_modules(relative: str) -> set[str]:
    """Every module a source file imports; ``from app import x`` counts as ``app.x``."""
    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
            imported.update(f"{node.module}.{alias.name}" for alias in node.names)
    return imported


_APP_INTERNALS = frozenset({
    "app.game_service",
    "app.persistence",
    "app.database",
    "app.visibility",
    "app.models",
    "sqlmodel",
    "sqlalchemy",
})

# (source file, modules it must not import)
_STATIC_BOUNDARIES = [
    pytest.param("agents/tools.py", _APP_INTERNALS, id="player-tools"),
    pytest.param("agents/player.py", _APP_INTERNALS, id="reference-player"),
    pytest.param("agents/lifecycle.py", _APP_INTERNALS, id="lifecycle"),
    pytest.param("agents/autonomous.py", _APP_INTERNALS, id="autonomous-coordinator"),
    pytest.param("app/provenance.py", frozenset({"app.summary", "summary"}), id="TEST-I062-002"),
    pytest.param("app/context.py", frozenset({"app.game_service"}), id="TEST-I064-003"),
    # The Actor-facing routes never reach the unfiltered export, its summary or the admin routes.
    pytest.param(
        "app/api_player.py",
        frozenset({"app.export", "app.summary", "app.api_admin"}),
        id="TEST-I153-001",
    ),
]


@pytest.mark.parametrize(("relative", "forbidden"), _STATIC_BOUNDARIES)
def test_module_does_not_import_across_its_boundary(
    relative: str, forbidden: frozenset[str]
) -> None:
    imported = _imported_modules(relative)
    assert imported.isdisjoint(forbidden), sorted(imported & forbidden)


def test_app_never_imports_the_after_action_report() -> None:
    """#16: the after-action report is built from exports after the game; the core never uses it."""
    offenders = {
        str(path.relative_to(REPO_ROOT)): sorted(
            m for m in _imported_modules(str(path.relative_to(REPO_ROOT)))
            if m == "aar" or m.startswith("aar.")
        )
        for path in (REPO_ROOT / "app").glob("*.py")
    }
    assert not {k: v for k, v in offenders.items() if v}
