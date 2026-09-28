"""I-044 deterministic source-tree provenance regressions."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from agents.code_version import code_version


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "AMAW Tests")
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


def _version(repo: Path, monkeypatch) -> str:
    monkeypatch.delenv("AMAW_CODE_VERSION", raising=False)
    return code_version(repo)


def test_i044_clean_and_generated_run_evidence_report_exact_head(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    assert _version(repo, monkeypatch) == commit

    run_dir = repo / "dev" / "runs" / "adhoc-1"
    run_dir.mkdir(parents=True)
    for name in ("export.json", "run.json", "failures.json", "activations.json"):
        (run_dir / name).write_text(f'{{"file":"{name}"}}\n', encoding="utf-8")
    (run_dir / "run.log").write_text("provider output\n", encoding="utf-8")
    (run_dir / "stdout.log").write_text("console output\n", encoding="utf-8")
    (run_dir / "report.md").write_text("# Run report\n", encoding="utf-8")

    assert _version(repo, monkeypatch) == commit


def test_i044_tracked_state_digest_is_stable_and_content_sensitive(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    tracked = repo / "tracked.py"
    tracked.write_text("VALUE = 2\n", encoding="utf-8")

    first = _version(repo, monkeypatch)
    assert re.fullmatch(rf"{commit}-dirty-[0-9a-f]{{64}}", first)
    assert _version(repo, monkeypatch) == first

    tracked.write_text("VALUE = 3\n", encoding="utf-8")
    assert _version(repo, monkeypatch) != first


def test_i044_untracked_source_changes_digest_but_evidence_does_not(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    source = repo / "agents" / "experimental.py"
    source.parent.mkdir()
    source.write_text("POLICY = 'one'\n", encoding="utf-8")

    first = _version(repo, monkeypatch)
    assert re.fullmatch(rf"{commit}-dirty-[0-9a-f]{{64}}", first)

    evidence = repo / "dev" / "runs" / "adhoc-2" / "export.json"
    evidence.parent.mkdir(parents=True)
    evidence.write_text('{"large":"generated payload"}\n', encoding="utf-8")
    assert _version(repo, monkeypatch) == first

    source.write_text("POLICY = 'two'\n", encoding="utf-8")
    assert _version(repo, monkeypatch) != first


def test_i044_run_directory_does_not_hide_arbitrary_or_executable_files(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    run_dir = repo / "dev" / "runs" / "adhoc-3"
    run_dir.mkdir(parents=True)

    source = run_dir / "replay.py"
    source.write_text("print('replay')\n", encoding="utf-8")
    assert _version(repo, monkeypatch) != commit

    source.unlink()
    suspicious_evidence = run_dir / "run.json"
    suspicious_evidence.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    suspicious_evidence.chmod(0o755)
    assert _version(repo, monkeypatch) != commit


def test_i044_explicit_code_version_override_remains_authoritative(
    tmp_path: Path, monkeypatch
) -> None:
    repo, _ = _repo(tmp_path)
    (repo / "untracked.py").write_text("dirty = True\n", encoding="utf-8")
    monkeypatch.setenv("AMAW_CODE_VERSION", "release-build-17")

    assert code_version(repo) == "release-build-17"


# --- I-076: the batch runner's and launchd helper's own output is evidence, not source ---

_BATCH_TABLES = ("games", "agents", "arguments", "messages", "resource_access")


def _write_attempt(batch: Path, attempt: str) -> None:
    """The two files ``benchmark.runner._write_attempt`` puts under ``games/<attempt>/``."""
    directory = batch / "games" / attempt
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "attempt.json").write_text(f'{{"directory":"{attempt}"}}\n', encoding="utf-8")
    (directory / "export.json").write_text(f'{{"attempt":"{attempt}"}}\n', encoding="utf-8")


def _write_batch_state(batch: Path, attempts: int) -> None:
    """What ``benchmark.runner`` rewrites after every attempt: ``batch.json`` and the CSVs."""
    batch.mkdir(parents=True, exist_ok=True)
    (batch / "batch.json").write_text(f'{{"attempts":{attempts}}}\n', encoding="utf-8")
    for table in _BATCH_TABLES:
        (batch / f"{table}.csv").write_text(f"game_id\n{'x,' * attempts}\n", encoding="utf-8")


def test_i076_001_batch_output_under_dev_runs_keeps_the_identity_stable_across_attempts(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    batch = repo / "dev" / "runs" / "my-batch"

    # After attempt 000: batch.json, games/000/{attempt,export}.json, the five tables.
    _write_batch_state(batch, attempts=1)
    _write_attempt(batch, "000")
    assert _version(repo, monkeypatch) == commit

    # After attempt 001: batch.json rewritten, games/001 added, the tables rewritten — the
    # exact write set that made T-060 report `code_version` drift.
    _write_batch_state(batch, attempts=2)
    _write_attempt(batch, "001")
    assert _version(repo, monkeypatch) == commit


def test_i076_002_launchd_helper_run_directory_reports_exact_head(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    run_dir = repo / "dev" / "runs" / "overnight"
    run_dir.mkdir(parents=True)
    (run_dir / "launchd.plist").write_bytes(b"<?xml version=\"1.0\"?>\n<plist/>\n")
    (run_dir / "stdout.log").write_text("launchd output\n", encoding="utf-8")

    assert _version(repo, monkeypatch) == commit


def test_i076_003_batch_names_are_exempt_only_as_plain_files_under_dev_runs(
    tmp_path: Path, monkeypatch
) -> None:
    repo, commit = _repo(tmp_path)
    batch = repo / "dev" / "runs" / "my-batch"
    _write_batch_state(batch, attempts=1)
    _write_attempt(batch, "000")
    (batch / "launchd.plist").write_bytes(b"<plist/>\n")

    def dirties(path: Path, content: bytes, mode: int | None = None) -> bool:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        if mode is not None:
            path.chmod(mode)
        try:
            return _version(repo, monkeypatch) != commit
        finally:
            path.unlink()

    # The same names outside dev/runs are source.
    assert dirties(repo / "batch.json", b"{}\n")
    assert dirties(repo / "dev" / "attempt.json", b"{}\n")
    assert dirties(repo / "games.csv", b"game_id\n")
    assert dirties(repo / "launchd.plist", b"<plist/>\n")
    # An arbitrary file in the batch directory is source.
    assert dirties(batch / "notes.txt", b"observations\n")
    assert dirties(batch / "games" / "000" / "replay.py", b"print('replay')\n")
    # An executable evidence-shaped file is source.
    assert dirties(batch / "games" / "000" / "attempt.json", b"#!/bin/sh\nexit 0\n", 0o755)
    assert dirties(batch / "launchd.plist", b"#!/bin/sh\nexit 0\n", 0o755)
    assert dirties(batch / "games.csv", b"#!/bin/sh\nexit 0\n", 0o755)
