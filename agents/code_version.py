"""The run's code identity and the runtime environment that executed it.

``code_version()`` is the ``code_version`` manifest field (TRD §28) — part of the run's
provenance identity, so its string must not change for a given checkout. Precedence: an
explicit ``AMAW_CODE_VERSION`` wins; then Git — the repository is detected with ``git rev-parse
--show-toplevel`` from the caller's ``root`` or this file's ``parents[1]`` (I-085: the argument
is a starting point, the toplevel is what is identified) and reported as ``git rev-parse HEAD``
suffixed ``-dirty-<sha256>`` when the index or worktree differs from HEAD (I-044/I-076:
generated run evidence under ``dev/runs`` — the ad-hoc ``amaw run`` files, the ``amaw
benchmark`` batch files and the launchd helper's manifest — is excluded only while it is an
ordinary non-executable file); then the installed package version; then ``unknown-build``.
Only *detection* may fall through to the package identity. Once a repository is detected, any
later Git failure is a :class:`CodeVersionError` (I-085), never a switch of identity system.

``runtime_fingerprint()`` is the ``environment`` manifest section (I-085): what ran the code.
Extracted from ``agents/autonomous.py`` in I-063 (review F11).
"""

from __future__ import annotations

import hashlib
from importlib import metadata
import os
from pathlib import Path
import platform
import sqlite3
import stat
import subprocess
import sys
from typing import Any
import warnings

from amaw_contracts import canonical_sha256

# Files emitted by the supported run workflows: the ad-hoc `amaw run` evidence (I-044), the
# `amaw benchmark` batch layout — `batch.json`, `games/<attempt>/attempt.json` and the five CSV
# tables, rewritten after every attempt — and the launchd helper's `launchd.plist` (I-076, D-067).
# A batch's own evidence must not move `code_version` between its attempts, or the D-058
# held-constant check fails on the batch itself. The exception is deliberately exact: an
# arbitrary file below dev/runs remains part of the source identity, as does even one of these
# names when it is executable or lives outside dev/runs.
_GENERATED_RUN_EVIDENCE_NAMES = frozenset(
    {
        # amaw run
        "activations.json",
        "export.json",
        "failures.json",
        "report.md",
        "run.json",
        "run.log",
        "stdout.log",
        # amaw benchmark (benchmark/runner.py, benchmark/outputs.py)
        "attempt.json",
        "batch.json",
        "agents.csv",
        "arguments.csv",
        "games.csv",
        "messages.csv",
        "resource_access.csv",
        # amaw-unattended (amaw_unattended.py)
        "launchd.plist",
    }
)


class CodeVersionError(RuntimeError):
    """A detected Git checkout could not be identified reliably (I-085).

    Raised once ``git rev-parse --show-toplevel`` has succeeded and a later Git step fails —
    a timeout, an unexpected ``diff-index`` status, a failing ``ls-files``. The run must not
    start under a different identity system (``package:``/``unknown-build``) for a checkout
    that Git can see; the same checkout would then carry two ``code_version`` strings and the
    D-058/D-068 held-constant check would report drift that is not there.
    """


def code_version(root: Path | None = None) -> str:
    configured = os.environ.get("AMAW_CODE_VERSION")
    if configured:
        return configured
    # The argument is where detection starts; the repository it belongs to is what is
    # identified, so every Git command and the D-067 dev/runs exemption use the toplevel.
    # Only detection may fall back: no git, or a path outside any repository.
    try:
        root = _git_toplevel(root or Path(__file__).resolve().parents[1])
    except (OSError, subprocess.SubprocessError):
        try:
            return f"package:{metadata.version('amaw')}"
        except metadata.PackageNotFoundError:
            return "unknown-build"
    try:
        commit = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        ).stdout.strip()
        digest = _dirty_source_digest(root)
    except (OSError, subprocess.SubprocessError) as error:
        raise CodeVersionError(
            f"cannot identify the Git checkout at {root}: {_describe(error)}"
        ) from error
    return f"{commit}-dirty-{digest}" if digest else commit


# I-085: the runtime-environment fingerprint. `code_version` identifies the source; this
# identifies what ran it — interpreter, SQLite, the runtime dependencies `pyproject.toml` only
# bounds from below, the lockfile and the transport the coordinator drives the server through.
# It is reproducibility metadata (methodology §8: nothing here claims identical LLM output) and
# its `sha256` is one held-constant condition path. The v1 vocabulary is frozen: renaming a key,
# adding or removing a dependency, or changing which distribution supplies a field is a new
# schema version, not a refactor.
RUNTIME_FINGERPRINT_SCHEMA = "amaw-runtime-fingerprint-v1"
IN_PROCESS_TRANSPORT = "in-process-asgi-testclient-v1"
RUNTIME_DEPENDENCIES = {  # logical key -> distribution name
    "pydantic": "pydantic",
    "pydantic_core": "pydantic-core",
    "sqlmodel": "sqlmodel",
    "sqlalchemy": "SQLAlchemy",
    "fastapi": "fastapi",
    "starlette": "starlette",
    "pydantic_ai": "pydantic-ai",
    "httpx": "httpx",
    "pyyaml": "PyYAML",
}


def runtime_fingerprint(
    root: Path | None = None, *, transport: str = IN_PROCESS_TRANSPORT
) -> dict[str, Any]:
    """Return the ``environment`` manifest section: versions, fixed identifiers and hashes only.

    Unavailable data is ``None`` — no ``uv.lock`` (or no detected repository to find it in), a
    distribution that is not installed. Nothing here is a path, a username, a hostname, an
    environment-variable value or a time (testing.md §24).
    """
    fingerprint: dict[str, Any] = {
        "schema": RUNTIME_FINGERPRINT_SCHEMA,
        "python": {
            "implementation": platform.python_implementation(),
            "version": platform.python_version(),
            "platform": sys.platform,
            "machine": platform.machine(),
        },
        "sqlite": {
            "library_version": sqlite3.sqlite_version,
            "module_version": _sqlite_module_version(),
        },
        "dependencies": {
            key: _distribution_version(name) for key, name in RUNTIME_DEPENDENCIES.items()
        },
        "uv_lock_sha256": _uv_lock_sha256(root),
        "transport": transport,
    }
    fingerprint["sha256"] = canonical_sha256(fingerprint)
    return fingerprint


def _sqlite_module_version() -> str | None:
    # `sqlite3.version` is deprecated and removed in Python 3.14; it is recorded while it exists.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        return getattr(sqlite3, "version", None)


def _distribution_version(name: str) -> str | None:
    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _uv_lock_sha256(root: Path | None) -> str | None:
    try:
        toplevel = _git_toplevel(root or Path(__file__).resolve().parents[1])
        content = (toplevel / "uv.lock").read_bytes()
    except (OSError, subprocess.SubprocessError):
        return None
    return hashlib.sha256(content).hexdigest()


def _describe(error: BaseException) -> str:
    command = getattr(error, "cmd", None) or getattr(error, "args", None)
    if isinstance(command, (list, tuple)) and command and command[0] == "git":
        return f"git {command[1]} failed ({type(error).__name__})"
    return f"{type(error).__name__}: {error}"


def _git_toplevel(start: Path) -> Path:
    output = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=start,
        check=True,
        capture_output=True,
        text=True,
        timeout=2,
    ).stdout.strip()
    return Path(output)


def _dirty_source_digest(root: Path) -> str | None:
    """Return a deterministic digest when the runnable Git tree differs from HEAD.

    Git's index metadata plus current tracked/untracked file bytes represent staged and
    unstaged states without relying on human-formatted diffs. Known run outputs are omitted
    only while they are ordinary non-executable files.
    """
    tracked_state = subprocess.run(
        ["git", "diff-index", "--quiet", "HEAD", "--"],
        cwd=root,
        check=False,
        capture_output=True,
        timeout=2,
    )
    if tracked_state.returncode not in (0, 1):
        raise subprocess.CalledProcessError(
            tracked_state.returncode,
            tracked_state.args,
            tracked_state.stdout,
            tracked_state.stderr,
        )

    index = subprocess.run(
        ["git", "ls-files", "--stage", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=2,
    ).stdout
    untracked_output = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=root,
        check=True,
        capture_output=True,
        timeout=2,
    ).stdout
    untracked = sorted(
        Path(os.fsdecode(raw)) for raw in untracked_output.split(b"\0") if raw
    )
    relevant_untracked = [
        path
        for path in untracked
        if not _is_generated_run_evidence(root, path)
    ]
    if tracked_state.returncode == 0 and not relevant_untracked:
        return None

    digest = hashlib.sha256()
    digest.update(b"amaw-source-tree-v1\0index\0")
    digest.update(len(index).to_bytes(8, "big"))
    digest.update(index)

    tracked_paths: set[Path] = set()
    for entry in index.split(b"\0"):
        if not entry:
            continue
        _, raw_path = entry.split(b"\t", 1)
        tracked_paths.add(Path(os.fsdecode(raw_path)))
    for path in sorted(tracked_paths | set(relevant_untracked)):
        _hash_worktree_path(digest, root, path)
    return digest.hexdigest()


def _is_generated_run_evidence(root: Path, path: Path) -> bool:
    if path.parts[:2] != ("dev", "runs"):
        return False
    candidate = root / path
    try:
        mode = candidate.lstat().st_mode
    except OSError:
        return False
    return (
        path.name in _GENERATED_RUN_EVIDENCE_NAMES
        and stat.S_ISREG(mode)
        and not mode & 0o111
    )


def _hash_worktree_path(digest: Any, root: Path, path: Path) -> None:
    raw_path = os.fsencode(path.as_posix())
    digest.update(len(raw_path).to_bytes(8, "big"))
    digest.update(raw_path)
    candidate = root / path
    try:
        mode = candidate.lstat().st_mode
    except FileNotFoundError:
        digest.update(b"\0missing\0")
        return
    if stat.S_ISLNK(mode):
        digest.update(b"\0symlink\0")
        content = os.fsencode(os.readlink(candidate))
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    elif stat.S_ISREG(mode):
        digest.update(b"\0file+x\0" if mode & 0o111 else b"\0file\0")
        content = candidate.read_bytes()
        digest.update(len(content).to_bytes(8, "big"))
        digest.update(content)
    else:
        # A tracked gitlink or other special path is still identity-relevant. Its index
        # entry is already included; the marker prevents it aliasing a missing/regular file.
        digest.update(b"\0special\0")
