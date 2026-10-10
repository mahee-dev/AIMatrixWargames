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


def _export_file(tmp_path: Path, export: dict) -> Path:
    path = tmp_path / "game.json"
    path.write_text(json.dumps(export))
    return path


def test_card_without_the_capture_stack_explains_how_to_install(
    tmp_path: Path, canonical_export: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aar.capture

    def unavailable(*args: object, **kwargs: object) -> None:
        raise aar.capture.CaptureUnavailable(f"missing: {aar.capture.INSTALL_HINT}")

    monkeypatch.setattr(aar.capture, "capture_png", unavailable)
    result = CliRunner().invoke(
        amaw_cli.app, ["report", str(_export_file(tmp_path, canonical_export)), "--card"]
    )
    assert result.exit_code == 2
    assert "playwright install chromium" in result.output
    # The report and the card page are still written; only the capture is missing.
    out = tmp_path / f"{canonical_export['game']['id']}-aar"
    assert (out / "report.md").is_file() and (out / "card.html").is_file()


def test_card_writes_html_png_and_mp4(
    tmp_path: Path, canonical_export: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    import aar.capture

    calls: list[str] = []
    monkeypatch.setattr(aar.capture, "capture_png", lambda html, png: calls.append(png.name))
    monkeypatch.setattr(
        aar.capture, "capture_mp4", lambda html, mp4, **kw: calls.append(mp4.name)
    )
    path = _export_file(tmp_path, canonical_export)
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card"])
    assert result.exit_code == 0, result.output
    assert calls == ["card.png", "card.mp4"]
    assert "private diplomacy" in result.output

    calls.clear()
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card", "--no-video"])
    assert result.exit_code == 0, result.output
    assert calls == ["card.png"]


@pytest.mark.slow
def test_card_end_to_end(tmp_path: Path, canonical_export: dict) -> None:
    pytest.importorskip("playwright.sync_api")
    path = _export_file(tmp_path, canonical_export)
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card", "--no-video"])
    if result.exit_code == 2 and "playwright install" in result.output:
        pytest.skip("Chromium is not installed")
    assert result.exit_code == 0, result.output
    assert (tmp_path / f"{canonical_export['game']['id']}-aar" / "card.png").stat().st_size > 0
