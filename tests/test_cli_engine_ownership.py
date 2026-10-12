"""I-096 — the CLI owns every created engine from creation through condition resolution."""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

import amaw_cli.conditions
from tests.api_helpers import REPO_ROOT


class _EngineProbe:
    disposed = False

    def dispose(self) -> None:
        self.disposed = True


def _fail_condition(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
    raise RuntimeError("condition construction failed")


def test_i096_001_run_disposes_engine_when_condition_construction_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    package = tmp_path / "scenario"
    package.mkdir()
    (package / "scenario.yaml").write_text("scenario: {}\n", encoding="utf-8")
    engine = _EngineProbe()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "run.db"))
    monkeypatch.setattr("app.database.engine_for_path", lambda path: engine)
    monkeypatch.setattr(amaw_cli.conditions, "_game_loop_condition", _fail_condition)

    result = CliRunner().invoke(amaw_cli.app, ["run", str(package)])

    assert result.exit_code == 1
    assert engine.disposed is True


def test_i096_001_benchmark_disposes_engine_when_condition_construction_raises(
    tmp_path: Path, monkeypatch,
) -> None:
    engine = _EngineProbe()
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(tmp_path / "benchmark.db"))
    monkeypatch.setattr("app.database.engine_for_path", lambda path: engine)
    monkeypatch.setattr(amaw_cli.conditions, "_game_loop_condition", _fail_condition)

    result = CliRunner().invoke(
        amaw_cli.app,
        [
            "benchmark",
            str(REPO_ROOT / "scenarios/orison-crisis"),
            "--models",
            "provider/model",
            "--benchmark-mode",
            "agent",
            "--output-dir",
            str(tmp_path / "batch"),
        ],
    )

    assert result.exit_code == 1
    assert engine.disposed is True
