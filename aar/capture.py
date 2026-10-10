"""Capture the story cards as PNGs and one H.264 MP4 by stepping their animations frame by frame.

Seeking every animation to a fixed time, rather than recording the screen, makes captures
identical between runs and independent of machine speed (#18). Playwright and imageio-ffmpeg
come from the optional ``share`` extra; without them, or without Chromium, capture raises
:class:`CaptureUnavailable` carrying the install command.
"""

from __future__ import annotations

import subprocess
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

INSTALL_HINT = "uv sync --extra share && uv run playwright install chromium"
WIDTH, HEIGHT = 1200, 675
FPS = 30
SECONDS_PER_SCREEN = 5.0

_PAUSE = "document.getAnimations().forEach(a => a.pause())"
_SEEK = "t => document.getAnimations().forEach(a => { a.currentTime = t; })"
_FINAL = "document.body.classList.add('final')"


class CaptureUnavailable(RuntimeError):
    """The capture stack is missing; the message says how to install it."""


@contextmanager
def _page(html_path: Path, scale: float) -> Iterator[Any]:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise CaptureUnavailable(f"card capture needs the share extra: {INSTALL_HINT}") from exc
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch()
        except PlaywrightError as exc:
            raise CaptureUnavailable(f"Chromium is not installed: {INSTALL_HINT}") from exc
        try:
            page = browser.new_page(
                viewport={"width": WIDTH, "height": HEIGHT}, device_scale_factor=scale
            )
            page.goto(Path(html_path).resolve().as_uri(), wait_until="load")
            page.evaluate("document.fonts.ready.then(() => true)")
            yield page
        finally:
            browser.close()


def capture_png(html_path: Path, png_path: Path) -> None:
    """The final frame (the scorecard) at 2× resolution."""
    with _page(html_path, scale=2) as page:
        page.evaluate(_FINAL)
        page.screenshot(path=str(png_path), type="png", animations="disabled")


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
    except ImportError as exc:
        raise CaptureUnavailable(f"video capture needs the share extra: {INSTALL_HINT}") from exc
    return str(imageio_ffmpeg.get_ffmpeg_exe())


def capture_mp4(
    html_paths: Sequence[Path], mp4_path: Path, *, fps: int = FPS,
    seconds_per_screen: float = SECONDS_PER_SCREEN,
) -> None:
    """Every frame of every screen in turn, piped as PNGs to the bundled ffmpeg: H.264, yuv420p,
    1200×676 (one padding row)."""
    command = [
        _ffmpeg(), "-y", "-loglevel", "error",
        "-f", "image2pipe", "-c:v", "png", "-framerate", str(fps), "-i", "-",
        # H.264 needs even dimensions; one padding row in the page colour keeps 1200×675 intact.
        "-vf", "pad=ceil(iw/2)*2:ceil(ih/2)*2:0:0:color=0x0d0d0d",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18",
        "-movflags", "+faststart", str(mp4_path),
    ]
    encoder = subprocess.Popen(command, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    assert encoder.stdin is not None
    try:
        for html_path in html_paths:
            with _page(html_path, scale=1) as page:
                page.evaluate(_PAUSE)
                for frame in range(round(fps * seconds_per_screen)):
                    page.evaluate(_SEEK, frame * 1000 / fps)
                    encoder.stdin.write(page.screenshot(type="png"))
    finally:
        encoder.stdin.close()
        stderr = encoder.stderr.read().decode() if encoder.stderr else ""
        code = encoder.wait()
    if code != 0:
        raise RuntimeError(f"ffmpeg failed ({code}): {stderr.strip()}")
