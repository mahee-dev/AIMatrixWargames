"""I-063 — ``agents.inference_profile`` and ``agents.code_version`` own what they name.

Structural guards for the two extractions (review F10 module, F11). Behaviour is proven elsewhere: the profile by ``test_model_inference_profile.py``
and ``test_cons_visible_reply.py``, the ``code_version`` string by ``test_code_provenance.py``
(i044). These tests only pin the seam — plus the one relocation semantic the i044 suite does not
touch, the default repository root.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _module_ast(relative: str) -> ast.Module:
    return ast.parse((ROOT / relative).read_text(encoding="utf-8"))


def _defined_names(tree: ast.Module) -> set[str]:
    return {
        node.name
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.ClassDef))
    }


def _assigned_names(tree: ast.Module) -> set[str]:
    return {
        target.id
        for node in tree.body
        if isinstance(node, ast.Assign)
        for target in node.targets
        if isinstance(target, ast.Name)
    }


def _imported_modules(tree: ast.Module) -> set[str]:
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module)
    return imported


def test_i063_001_inference_profile_module_owns_the_profile() -> None:
    profile_module = importlib.import_module("agents.inference_profile")
    player = importlib.import_module("agents.player")

    tree = _module_ast("agents/inference_profile.py")
    defined = _defined_names(tree)
    for name in ("InferenceProfileError", "InferenceProfile", "load_inference_profile"):
        assert name in defined, f"agents.inference_profile must define {name}"
        assert callable(getattr(profile_module, name))
    assert "INFERENCE_PROFILE_UNIFORM" in _assigned_names(tree)
    assert profile_module.INFERENCE_PROFILE_UNIFORM.name == "uniform"

    # ``PlayerConfig`` still holds a profile, so ``InferenceProfile``/``INFERENCE_PROFILE_UNIFORM``
    # are legitimately *imported* by player.py; the error type and the loader are not, and a
    # compatibility shim for either would fail the ``hasattr`` check.
    player_defs = _defined_names(_module_ast("agents/player.py"))
    for name in ("InferenceProfileError", "InferenceProfile", "load_inference_profile"):
        assert name not in player_defs, f"agents.player still defines {name}"
    for name in ("InferenceProfileError", "load_inference_profile"):
        assert not hasattr(player, name), f"agents.player still exposes {name}"

    imports = _imported_modules(tree)
    assert not any(module.startswith("agents.") for module in imports), imports


def test_i063_002_code_version_module_owns_worktree_hashing() -> None:
    """``agents.code_version`` owns worktree hashing and depends on no other AMAW module.

    Only the public names are pinned, so private helpers can be refactored freely.
    """
    code_version = importlib.import_module("agents.code_version")

    tree = _module_ast("agents/code_version.py")
    assert {"code_version", "runtime_fingerprint", "CodeVersionError"} <= _defined_names(tree)
    assert callable(code_version.code_version)
    assert "_GENERATED_RUN_EVIDENCE_NAMES" not in _assigned_names(_module_ast("agents/autonomous.py"))

    imports = _imported_modules(tree)
    assert not any(module.startswith(("agents.", "app.")) for module in imports), imports


def test_i063_003_code_version_default_root_is_the_repository(monkeypatch) -> None:
    # The move changes ``__file__``; ``parents[1]`` must still be the repository root, so the
    # default-root call and the explicit-root call agree on this very checkout.
    monkeypatch.delenv("AMAW_CODE_VERSION", raising=False)
    from agents.code_version import code_version

    assert code_version() == code_version(ROOT)
