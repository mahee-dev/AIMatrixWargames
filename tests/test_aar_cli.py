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
    # The report and the card pages are still written; only the capture is missing.
    out = tmp_path / f"{canonical_export['game']['id']}-aar"
    assert (out / "report.md").is_file() and (out / "card-1.html").is_file()


def _stub_capture(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    import aar.capture

    calls: list[str] = []
    monkeypatch.setattr(aar.capture, "capture_png", lambda html, png: calls.append(png.name))
    monkeypatch.setattr(aar.capture, "capture_mp4",
                        lambda pages, mp4, **kw: calls.append(f"{mp4.name}<{len(pages)}"))
    return calls


def test_card_writes_five_screens_and_one_video(
    tmp_path: Path, canonical_export: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = _stub_capture(monkeypatch)
    path = _export_file(tmp_path, canonical_export)
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card"])
    assert result.exit_code == 0, result.output
    assert calls == [f"card-{n}.png" for n in range(1, 6)] + ["card.mp4<5"]
    out = tmp_path / f"{canonical_export['game']['id']}-aar"
    assert "Built from the game record" in (out / "card-1.html").read_text()
    assert "private diplomacy" in result.output

    calls.clear()
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card", "--no-video"])
    assert result.exit_code == 0, result.output
    assert calls == [f"card-{n}.png" for n in range(1, 6)]


def test_narrate_needs_a_key(tmp_path: Path, canonical_export: dict) -> None:
    result = CliRunner().invoke(
        amaw_cli.app, ["report", str(_export_file(tmp_path, canonical_export)), "--card", "--narrate"]
    )
    assert result.exit_code == 2
    assert "OPENROUTER_API_KEY" in result.output


def test_narrate_without_card_is_refused(tmp_path: Path, canonical_export: dict) -> None:
    result = CliRunner().invoke(
        amaw_cli.app, ["report", str(_export_file(tmp_path, canonical_export)), "--narrate"]
    )
    assert result.exit_code == 2 and "add --card" in result.output


def test_narrate_writes_and_then_reuses_the_story(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from agents.model_client import OpenRouterClient
    from tests.test_aar_narrator import FakeClient, _model_story
    from tests.test_aar_story import _export as story_export

    export = story_export()
    path = _export_file(tmp_path, export)
    client = FakeClient(_model_story())
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")
    monkeypatch.setattr(OpenRouterClient, "from_settings", classmethod(lambda cls, settings, **kw: client))
    calls = _stub_capture(monkeypatch)

    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card", "--no-video", "--narrate"])
    assert result.exit_code == 0, result.output
    out = tmp_path / "g-1-aar"
    assert (out / "narration.json").is_file() and len(client.calls) == 1
    assert "Story written by AI" in (out / "card-1.html").read_text()
    assert "Alpha wins against the odds" in (out / "card-1.html").read_text()

    # Without --narrate the saved story is reused (and validated), with no model call.
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card", "--no-video"])
    assert result.exit_code == 0, result.output
    assert len(client.calls) == 1 and "Alpha wins against the odds" in (out / "card-1.html").read_text()
    assert len(calls) == 10  # five screens, twice


@pytest.mark.slow
def test_card_end_to_end(tmp_path: Path, canonical_export: dict) -> None:
    pytest.importorskip("playwright.sync_api")
    path = _export_file(tmp_path, canonical_export)
    result = CliRunner().invoke(amaw_cli.app, ["report", str(path), "--card", "--no-video"])
    if result.exit_code == 2 and "playwright install" in result.output:
        pytest.skip("Chromium is not installed")
    assert result.exit_code == 0, result.output
    assert (tmp_path / f"{canonical_export['game']['id']}-aar" / "card-5.png").stat().st_size > 0
