"""I-067: the macOS unattended launcher is one-shot and preserves run evidence."""

from __future__ import annotations

import plistlib
import subprocess
from pathlib import Path

import pytest

import amaw_unattended


def test_i067_003_launchd_plist_is_one_shot(tmp_path: Path) -> None:
    run_dir = tmp_path / "swedish-v6-overnight"
    plist = amaw_unattended.build_launchd_plist(
        label="com.amaw.swedish-v6-overnight",
        run_dir=run_dir,
        workdir=tmp_path,
        uv_path=Path("/opt/homebrew/bin/uv"),
        run_args=["scenarios/swedish-government", "--seed", "20260922"],
    )

    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is False
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/uv",
        "run",
        "amaw",
        "run",
        "scenarios/swedish-government",
        "--seed",
        "20260922",
    ]
    assert plist["ProgramArguments"].count("run") == 2  # `uv run` + `amaw run`, one command
    assert plist["StandardOutPath"] == plist["StandardErrorPath"]
    assert "EnvironmentVariables" not in plist  # never persist OPENROUTER_API_KEY
    assert plistlib.loads(plistlib.dumps(plist))["KeepAlive"] is False


def test_i067_004_launcher_refuses_existing_run_directory(tmp_path: Path) -> None:
    run_dir = tmp_path / "already-exists"
    run_dir.mkdir()

    with pytest.raises(FileExistsError):
        amaw_unattended.prepare_launch(
            run_dir=run_dir,
            label="com.amaw.already-exists",
            workdir=tmp_path,
            uv_path=Path("/opt/homebrew/bin/uv"),
            run_args=["orison-crisis"],
        )


def test_launcher_bootstraps_retained_plist_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured: list[list[str]] = []

    def fake_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        captured.append(command)
        assert kwargs == {"check": True, "capture_output": True, "text": True}
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(amaw_unattended.sys, "platform", "darwin")
    monkeypatch.setattr(amaw_unattended.shutil, "which", lambda _name: "/opt/homebrew/bin/uv")
    monkeypatch.setattr(amaw_unattended.subprocess, "run", fake_run)
    monkeypatch.setattr(amaw_unattended.os, "getuid", lambda: 501)

    run_dir = tmp_path / "fresh"
    plist_path = amaw_unattended.launch(
        run_dir=run_dir,
        label="com.amaw.fresh",
        workdir=tmp_path,
        run_args=["orison-crisis"],
    )

    assert plist_path == (run_dir / "launchd.plist").resolve()
    assert captured == [["/bin/launchctl", "bootstrap", "gui/501", str(plist_path)]]
    retained = plistlib.loads(plist_path.read_bytes())
    assert retained["KeepAlive"] is False
    assert retained["ProgramArguments"][1:4] == ["run", "amaw", "run"]
