"""One-shot macOS launchd wrapper for an unattended ``amaw run``.

``launchctl submit`` restarts a command that exits non-zero, which can silently create a second
Game after a failed one-game playtest. This helper writes an explicit retained plist with
``KeepAlive=false`` and bootstraps it into the current GUI user's launchd domain. It persists no
environment variables or credentials; the launched AMAW process reads the repository's normal
settings sources from its working directory.
"""

from __future__ import annotations

import argparse
import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence


_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


def build_launchd_plist(
    *,
    label: str,
    run_dir: Path,
    workdir: Path,
    uv_path: Path,
    run_args: Sequence[str],
) -> dict[str, Any]:
    """Return a launchd job that executes exactly one foreground ``amaw run``."""
    if not _LABEL_RE.fullmatch(label):
        raise ValueError("label must contain only letters, digits, '.', '_' and '-'")
    if not run_args:
        raise ValueError("at least one `amaw run` argument (the scenario) is required")
    resolved_run_dir = run_dir.resolve()
    return {
        "Label": label,
        "ProgramArguments": [
            str(uv_path.absolute()),
            "run",
            "amaw",
            "run",
            *run_args,
        ],
        "WorkingDirectory": str(workdir.resolve()),
        "RunAtLoad": True,
        "KeepAlive": False,
        "ProcessType": "Background",
        "StandardOutPath": str(resolved_run_dir / "stdout.log"),
        "StandardErrorPath": str(resolved_run_dir / "stdout.log"),
    }


def prepare_launch(
    *,
    run_dir: Path,
    label: str,
    workdir: Path,
    uv_path: Path,
    run_args: Sequence[str],
) -> Path:
    """Create a fresh run directory and retain its exact one-shot launch manifest."""
    plist = build_launchd_plist(
        label=label,
        run_dir=run_dir,
        workdir=workdir,
        uv_path=uv_path,
        run_args=run_args,
    )
    run_dir.mkdir(parents=True, exist_ok=False)
    plist_path = run_dir / "launchd.plist"
    with plist_path.open("xb") as stream:
        plistlib.dump(plist, stream, sort_keys=True)
    return plist_path.resolve()


def launch(
    *, run_dir: Path, label: str, workdir: Path, run_args: Sequence[str]
) -> Path:
    """Prepare and bootstrap one macOS user launchd job."""
    if sys.platform != "darwin":
        raise RuntimeError("amaw-unattended currently supports macOS launchd only; use nohup")
    uv = shutil.which("uv")
    if uv is None:
        raise RuntimeError("uv is not available on PATH")
    plist_path = prepare_launch(
        run_dir=run_dir,
        label=label,
        workdir=workdir,
        uv_path=Path(uv),
        run_args=run_args,
    )
    domain = f"gui/{os.getuid()}"
    subprocess.run(
        ["/bin/launchctl", "bootstrap", domain, str(plist_path)],
        check=True,
        capture_output=True,
        text=True,
    )
    return plist_path


def _default_label(run_dir: Path) -> str:
    suffix = re.sub(r"[^A-Za-z0-9._-]+", "-", run_dir.name).strip("-.")
    if not suffix:
        raise ValueError("run directory name cannot form a launchd label")
    return f"com.amaw.{suffix}"


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        prog="amaw-unattended",
        description="Launch exactly one unattended `amaw run` as a one-shot macOS launchd job.",
    )
    parser.add_argument("--run-dir", required=True, type=Path)
    parser.add_argument("--label")
    parser.add_argument("--workdir", type=Path, default=Path.cwd())
    parser.add_argument("run_args", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    run_args = args.run_args[1:] if args.run_args[:1] == ["--"] else args.run_args
    label = args.label or _default_label(args.run_dir)
    try:
        plist_path = launch(
            run_dir=args.run_dir,
            label=label,
            workdir=args.workdir,
            run_args=run_args,
        )
    except (FileExistsError, OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        parser.exit(1, f"amaw-unattended: {exc}\n")
    print(f"label={label}")
    print(f"plist={plist_path}")
    print(f"stdout={args.run_dir.resolve() / 'stdout.log'}")


if __name__ == "__main__":  # pragma: no cover - console-script boundary
    main()
