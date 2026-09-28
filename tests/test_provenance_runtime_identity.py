"""I-085: the real Git root in ``code_version`` and the runtime-environment fingerprint."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from agents.code_version import code_version

_DIRTY = re.compile(r"^[0-9a-f]{40}-dirty-[0-9a-f]{64}$")


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(tmp_path: Path) -> tuple[Path, str]:
    repo = tmp_path / "repo"
    (repo / "sub").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "tests@example.invalid")
    _git(repo, "config", "user.name", "AMAW Tests")
    (repo / "tracked.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo / "sub" / "inner.py").write_text("INNER = 1\n", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "fixture")
    return repo, _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def no_override(monkeypatch) -> None:
    monkeypatch.delenv("AMAW_CODE_VERSION", raising=False)


# TEST-I085-001 — the argument is a starting point; the repository is what is identified.
def test_i085_001_code_version_resolves_git_toplevel(tmp_path: Path, no_override) -> None:
    repo, commit = _repo(tmp_path)
    assert code_version(repo / "sub") == commit

    # A change outside the given subdirectory is part of the repository's identity.
    (repo / "tracked.py").write_text("VALUE = 2\n", encoding="utf-8")
    from_root = code_version(repo)
    from_sub = code_version(repo / "sub")
    assert _DIRTY.match(from_root)
    assert from_sub == from_root

    # The dev/runs evidence exemption (D-067) is evaluated against the real root, not the
    # argument: evidence written under <root>/dev/runs stays exempt from a subdirectory call.
    _git(repo, "add", "tracked.py")
    _git(repo, "commit", "-q", "-m", "clean")
    clean = _git(repo, "rev-parse", "HEAD")
    run_dir = repo / "dev" / "runs" / "batch-1"
    run_dir.mkdir(parents=True)
    (run_dir / "batch.json").write_text("{}\n", encoding="utf-8")
    assert code_version(repo / "sub") == clean
    # ...whereas the same name outside dev/runs is source, wherever the call starts.
    (repo / "sub" / "batch.json").write_text("{}\n", encoding="utf-8")
    assert _DIRTY.match(code_version(repo / "sub"))
    assert code_version(repo / "sub") == code_version(repo)


# TEST-I085-002 — one identity system per detected checkout: after detection, Git failure is
# an error, never a switch to the package/unknown-build identity.
def test_i085_002_git_failure_after_detection_raises_instead_of_falling_back(
    tmp_path: Path, no_override, monkeypatch
) -> None:
    from agents import code_version as module
    from agents.code_version import CodeVersionError

    repo, commit = _repo(tmp_path)
    real_run = subprocess.run

    def failing(command, *args, **kwargs):
        if command[:2] == ["git", "ls-files"]:
            raise subprocess.TimeoutExpired(command, kwargs.get("timeout", 2))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", failing)
    with pytest.raises(CodeVersionError, match="ls-files"):
        code_version(repo)

    # An unexpected diff-index status (not the documented 0/1) is an error as well.
    class Completed:
        returncode = 129
        args = ["git", "diff-index"]
        stdout = b""
        stderr = b"fatal: bad revision"

    def bad_status(command, *args, **kwargs):
        if command[:2] == ["git", "diff-index"]:
            return Completed()
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", bad_status)
    with pytest.raises(CodeVersionError, match="diff-index"):
        code_version(repo)

    # Detection itself failing keeps the fallback chain: not a repository, or no git at all.
    monkeypatch.setattr(module.subprocess, "run", real_run)
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(module.metadata, "version", lambda name: "9.9.9")
    assert code_version(outside) == "package:9.9.9"

    def no_git(command, *args, **kwargs):
        raise FileNotFoundError("git")

    monkeypatch.setattr(module.subprocess, "run", no_git)
    assert code_version(repo) == "package:9.9.9"

    # The explicit override never inspects the repository.
    monkeypatch.setenv("AMAW_CODE_VERSION", "pinned")
    monkeypatch.setattr(module.subprocess, "run", failing)
    assert code_version(repo) == "pinned"


# TEST-I085-003 — the runtime fingerprint has the frozen v1 vocabulary, faithful values and a
# stable canonical hash, and carries no path, username, hostname or environment value.
def test_i085_003_runtime_fingerprint_is_faithful_frozen_and_stable(
    tmp_path: Path, no_override, monkeypatch
) -> None:
    import getpass
    import hashlib
    import json
    import os
    import platform
    import socket
    import sqlite3
    import sys
    from importlib import metadata

    from agents import code_version as module
    from agents.code_version import (
        IN_PROCESS_TRANSPORT,
        RUNTIME_DEPENDENCIES,
        RUNTIME_FINGERPRINT_SCHEMA,
        runtime_fingerprint,
    )
    from amaw_contracts import canonical_json, canonical_sha256

    assert RUNTIME_FINGERPRINT_SCHEMA == "amaw-runtime-fingerprint-v1"
    assert IN_PROCESS_TRANSPORT == "in-process-asgi-testclient-v1"
    assert RUNTIME_DEPENDENCIES == {
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

    repo, _ = _repo(tmp_path)
    lock = repo / "uv.lock"
    lock.write_bytes(b"version = 1\n")
    fingerprint = runtime_fingerprint(repo / "sub")

    assert list(fingerprint) == [
        "schema", "python", "sqlite", "dependencies", "uv_lock_sha256", "transport", "sha256",
    ]
    assert fingerprint["schema"] == RUNTIME_FINGERPRINT_SCHEMA
    assert fingerprint["python"] == {
        "implementation": platform.python_implementation(),
        "version": platform.python_version(),
        "platform": sys.platform,
        "machine": platform.machine(),
    }
    assert fingerprint["sqlite"]["library_version"] == sqlite3.sqlite_version
    assert list(fingerprint["sqlite"]) == ["library_version", "module_version"]
    assert list(fingerprint["dependencies"]) == list(RUNTIME_DEPENDENCIES)
    for key, distribution in RUNTIME_DEPENDENCIES.items():
        assert fingerprint["dependencies"][key] == metadata.version(distribution)
    assert fingerprint["uv_lock_sha256"] == hashlib.sha256(b"version = 1\n").hexdigest()
    assert fingerprint["transport"] == IN_PROCESS_TRANSPORT
    without_hash = {key: value for key, value in fingerprint.items() if key != "sha256"}
    assert fingerprint["sha256"] == canonical_sha256(without_hash)

    # Stable within the process and in a fresh interpreter.
    assert runtime_fingerprint(repo / "sub") == fingerprint
    fresh = subprocess.run(
        [
            sys.executable, "-c",
            "import json, sys; from pathlib import Path; from agents.code_version import "
            "runtime_fingerprint; print(json.dumps(runtime_fingerprint(Path(sys.argv[1]))))",
            str(repo),
        ],
        cwd=Path(__file__).resolve().parents[1],
        check=True, capture_output=True, text=True,
    ).stdout
    assert json.loads(fresh) == fingerprint

    # Faithful: unavailable data is None, never invented; the hash follows.
    lock.unlink()
    without_lock = runtime_fingerprint(repo)
    assert without_lock["uv_lock_sha256"] is None
    assert without_lock["sha256"] != fingerprint["sha256"]
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "uv.lock").write_bytes(b"not a repository\n")
    assert runtime_fingerprint(outside)["uv_lock_sha256"] is None
    real_version = metadata.version

    def missing_pyyaml(name: str) -> str:
        if name == "PyYAML":
            raise metadata.PackageNotFoundError(name)
        return real_version(name)

    monkeypatch.setattr(module.metadata, "version", missing_pyyaml)
    degraded = runtime_fingerprint(repo)
    assert degraded["dependencies"]["pyyaml"] is None
    assert degraded["dependencies"]["pydantic"] == real_version("pydantic")
    monkeypatch.setattr(module.metadata, "version", real_version)

    # The transport identity is explicit, and part of the hash.
    other = runtime_fingerprint(repo, transport="other-transport-v1")
    assert other["transport"] == "other-transport-v1"
    assert other["sha256"] != without_lock["sha256"]

    # No secrets or machine-local identifiers (testing.md §24).
    text = canonical_json(fingerprint).decode("utf-8")
    for forbidden in (
        str(repo), str(Path.home()), os.getcwd(), getpass.getuser(), socket.gethostname(),
    ):
        assert forbidden not in text
    for value in os.environ.values():
        if len(value) >= 8:
            assert value not in text


# TEST-I085-004 — `environment` is runner-derived and reserved; `environment.sha256` is sealed
# before game 1 and held constant in both benchmark modes; a legacy manifest projects None.
def test_i085_004_environment_is_a_reserved_derived_section(db_engine) -> None:
    from agents.autonomous import (
        RESERVED_MANIFEST_SECTIONS,
        AutonomousGameError,
        expected_manifest_configuration,
    )
    from agents.code_version import IN_PROCESS_TRANSPORT, runtime_fingerprint
    from agents.player import PlayerConfig
    from benchmark.controls import (
        COMMON_CONDITION_PATHS,
        HELD_CONSTANT_PATHS,
        held_constant_condition,
    )

    config = PlayerConfig(model="fake/a", activation_mode="tool_loop")
    manifest = expected_manifest_configuration(config)
    assert manifest["environment"] == runtime_fingerprint()
    assert manifest["environment"]["transport"] == IN_PROCESS_TRANSPORT
    other = expected_manifest_configuration(config, transport="other-transport-v1")
    assert other["environment"]["transport"] == "other-transport-v1"

    assert "environment" in RESERVED_MANIFEST_SECTIONS
    with pytest.raises(AutonomousGameError, match="reserved.*environment"):
        expected_manifest_configuration(config, run_configuration={"environment": {"x": 1}})

    assert COMMON_CONDITION_PATHS.count("environment.sha256") == 1
    assert "environment.sha256" in HELD_CONSTANT_PATHS
    assert [path for path in HELD_CONSTANT_PATHS if path.startswith("environment")] == [
        "environment.sha256"
    ]
    assert held_constant_condition(manifest)["environment.sha256"] == manifest["environment"]["sha256"]
    legacy = {key: value for key, value in manifest.items() if key != "environment"}
    assert held_constant_condition(legacy)["environment.sha256"] is None


@pytest.mark.parametrize("benchmark_mode", ["model", "agent"])
@pytest.mark.slow
def test_i085_004_environment_drift_stops_the_batch_in_both_modes(
    db_engine, tmp_path, monkeypatch, benchmark_mode: str, short_game
) -> None:
    from agents import autonomous
    from agents.code_version import runtime_fingerprint
    from benchmark.runner import BatchIntegrityError
    from tests.benchmark_helpers import offline_batch
    from tests.test_benchmark_controls import _batch_json, _controls

    monkeypatch.setenv("AMAW_CODE_VERSION", "test-build")
    umpire, controls = _controls(benchmark_mode, short_game)
    expected_fingerprint = runtime_fingerprint()

    def umpire_for_attempt(attempt):
        if attempt.position == 1:  # the runtime moves under the batch
            monkeypatch.setattr(
                autonomous, "runtime_fingerprint",
                lambda *args, **kwargs: runtime_fingerprint(transport="moved-transport-v1"),
            )
        return None

    schedule, result, outcome = offline_batch(
        db_engine, tmp_path, scenario=short_game, models=["fake/x", "fake/y"], rotation="cyclic", repetitions=2,
        benchmark_mode=benchmark_mode, umpire=umpire, controls=controls,
        umpire_for_attempt=umpire_for_attempt,
    )

    assert isinstance(outcome, BatchIntegrityError), outcome
    assert result.counts() == {
        "total_games": 4, "completed_games": 2, "failed_games": 0, "not_run_games": 2,
    }
    drifted = result.attempts[1]
    assert drifted.umpire_condition_match is True
    assert drifted.condition_differences == ["environment.sha256"]
    batch = _batch_json(tmp_path)
    assert batch["expected_condition"]["environment.sha256"] == expected_fingerprint["sha256"]
    [failure] = batch["integrity"]["failures"]
    assert failure["kind"] == "expected_condition"
    assert failure["differences"] == ["environment.sha256"]


# TEST-I085-005 — a resume under a different runtime environment is refused, like a resume
# under a different code version: the interrupted run's identity is not renegotiated.
def test_i085_005_resume_refuses_a_different_runtime_environment(monkeypatch) -> None:
    from agents.autonomous import AutonomousGameError, AutonomousGameRunner
    from agents.code_version import runtime_fingerprint

    monkeypatch.setenv("AMAW_CODE_VERSION", "test-build")
    runner = AutonomousGameRunner(
        object(), admin_token="admin", scenario="unused-on-resume", player_factory=lambda *a: None
    )
    recorded = runtime_fingerprint(transport="moved-transport-v1")
    assert recorded["sha256"] != runtime_fingerprint()["sha256"]
    descriptor = {
        "manifest": {"code_version": "test-build", "environment": recorded},
        "actors": {"arkania": "Arkania"},
    }
    monkeypatch.setattr(runner, "_admin_request", lambda method, path, **kwargs: descriptor)
    with pytest.raises(AutonomousGameError, match="runtime environment does not match"):
        runner.resume("game-1")


# TEST-I085-006 — games.csv projects environment_sha256 beside code_version; an attempt without
# a manifest leaves it empty; batch.json carries the hash through the expected condition.
def test_i085_006_games_table_projects_the_environment_hash(tmp_path) -> None:
    import csv

    from benchmark.outputs import _TABLE_COLUMNS, build_tables, load_attempts
    from benchmark.runner import run_batch
    from tests.test_benchmark_output_mechanics import _export, _schedule

    columns = _TABLE_COLUMNS["games"]
    assert columns.index("environment_sha256") == columns.index("code_version") + 1

    schedule = _schedule(rotate=False, models=["provider/x"], repetitions=2, base_seed=10)

    def execute(attempt):
        if attempt.repetition_index == 1:
            raise RuntimeError("no game was ever created")
        export = _export(attempt)
        export["run_manifest"]["code_version"] = "abc123"
        export["run_manifest"]["environment"] = {"schema": "amaw-runtime-fingerprint-v1", "sha256": "f" * 64}
        return export

    run_batch(
        schedule, execute=execute, output_dir=tmp_path / "batch",
        experiment={"name": "i085", "benchmark_mode": None, "scenario_id": "fixture"},
    )
    rows = {
        row["repetition_index"]: row
        for row in build_tables(load_attempts(tmp_path / "batch"))["games"]
    }
    assert rows[0]["code_version"] == "abc123" and rows[0]["environment_sha256"] == "f" * 64
    assert rows[1]["environment_sha256"] is None
    with (tmp_path / "batch" / "games.csv").open(encoding="utf-8", newline="") as handle:
        csv_rows = {int(r["repetition_index"]): r for r in csv.DictReader(handle)}
    assert csv_rows[0]["environment_sha256"] == "f" * 64
    assert csv_rows[1]["environment_sha256"] == ""


# TEST-I085-007 — the production caller (module default root) reports the real checkout: HEAD
# plus, at most, the existing dirty digest; the identity does not depend on where the call
# starts inside the checkout, and the lockfile hash is the checkout's uv.lock.
def test_i085_007_production_checkout_identity_is_unchanged(no_override) -> None:
    import hashlib

    from agents.code_version import runtime_fingerprint

    checkout = Path(__file__).resolve().parents[1]
    head = _git(checkout, "rev-parse", "HEAD")
    reported = code_version()
    assert reported == head or (
        reported.startswith(f"{head}-dirty-") and _DIRTY.match(reported)
    )
    assert code_version(checkout / "agents") == reported
    assert code_version(checkout / "tests") == reported

    assert runtime_fingerprint()["uv_lock_sha256"] == hashlib.sha256(
        (checkout / "uv.lock").read_bytes()
    ).hexdigest()
    assert runtime_fingerprint(checkout / "agents") == runtime_fingerprint()


# TEST-I085-002 (CLI) — neither command starts a game under an unidentifiable checkout, and
# both say so instead of switching identity systems or dumping a traceback.
def test_i085_002_cli_commands_refuse_an_unidentifiable_checkout(monkeypatch, tmp_path) -> None:
    from typer.testing import CliRunner

    import amaw_cli
    from agents.code_version import CodeVersionError
    from tests.test_benchmark_runner import _invoke_benchmark
    from tests.test_cli_run import _scenario_package

    monkeypatch.delenv("AMAW_CODE_VERSION", raising=False)

    def unidentifiable(*args, **kwargs):
        raise CodeVersionError("cannot identify the Git checkout at /x: git ls-files failed")

    # amaw benchmark: the expected condition is derived before any game; the batch never runs.
    monkeypatch.setattr("agents.autonomous.runtime_fingerprint", unidentifiable)
    result, captured = _invoke_benchmark(
        monkeypatch, tmp_path, ["--models", "provider/x,provider/y,provider/z", "--rotate"]
    )
    assert result.exit_code == 2, result.output
    assert "cannot identify the Git checkout" in result.output
    assert "schedule" not in captured
    assert "Traceback" not in result.output

    # amaw run: the runner's manifest step raises; the CLI reports it as a failed run.
    package = _scenario_package(tmp_path)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "amaw.db"))
    monkeypatch.delenv("AMAW_ADMIN_TOKEN", raising=False)

    class FakeUmpire:
        @classmethod
        def for_openrouter(cls, settings, **kwargs):  # noqa: ANN001
            return cls()

    class UnidentifiableRunner:
        def __init__(self, client, **kwargs):  # noqa: ANN001
            self.last_game_id = None

        def run(self):
            unidentifiable()

    monkeypatch.setattr("agents.umpire.AIUmpire", FakeUmpire)
    monkeypatch.setattr("agents.autonomous.AutonomousGameRunner", UnidentifiableRunner)
    result = CliRunner().invoke(amaw_cli.app, ["run", str(package)])
    assert result.exit_code == 1
    assert "autonomous run failed: cannot identify the Git checkout" in result.output
    assert "Traceback" not in result.output
