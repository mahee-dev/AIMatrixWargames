"""``amaw report`` writes ``report.md`` from an export file or from a game in the database (#16)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

import amaw_cli
from tests.aar_helpers import canonical_export as play


def test_report_from_export_path(tmp_path: Path, canonical_export: dict) -> None:
    path = tmp_path / "game.json"
    path.write_text(json.dumps(canonical_export))
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path)])
    assert result.exit_code == 0, result.output
    out = tmp_path / f"{canonical_export['game']['id']}-aar" / "report.md"
    assert out.read_text().startswith("# After-action report: deterministic-game")


def test_report_honours_out(tmp_path: Path, canonical_export: dict) -> None:
    path = tmp_path / "game.json"
    path.write_text(json.dumps(canonical_export))
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--out", str(tmp_path / "o")])
    assert result.exit_code == 0, result.output
    assert (tmp_path / "o" / "report.md").is_file()


def test_report_rejects_a_missing_export(tmp_path: Path) -> None:
    result = CliRunner().invoke(amaw_cli.app, ["report", str(tmp_path / "nope.json")])
    assert result.exit_code == 2


def test_report_rejects_an_invalid_game_id() -> None:
    result = CliRunner().invoke(amaw_cli.app, ["report", "not-a-game"])
    assert result.exit_code == 2


def test_report_from_game_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "amaw.db"
    game_id = play(db)["game"]["id"]
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(db))
    result = CliRunner().invoke(amaw_cli.app, ["report", game_id])
    assert result.exit_code == 0, result.output
    exports = tmp_path / "exports"
    assert json.loads((exports / f"{game_id}.json").read_text())["game"]["id"] == game_id
    assert (exports / f"{game_id}-aar" / "report.md").is_file()


def test_report_for_an_unknown_game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db = tmp_path / "amaw.db"
    play(db)
    monkeypatch.setenv("AMAW_DATABASE_PATH", str(db))
    result = CliRunner().invoke(
        amaw_cli.app, ["report", "00000000-0000-0000-0000-000000000000"]
    )
    assert result.exit_code == 1
