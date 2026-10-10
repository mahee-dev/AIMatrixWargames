"""The share card is captured as a 2× PNG and a 30 fps H.264 MP4 by stepping its animations, so
captures are identical between runs (#18). Skipped when Chromium is not installed."""

from __future__ import annotations

import struct
from collections.abc import Iterator
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api")
imageio_ffmpeg = pytest.importorskip("imageio_ffmpeg")

from aar.capture import CaptureUnavailable, capture_mp4, capture_png  # noqa: E402
from aar.card import render_card_html  # noqa: E402
from aar.model import build_report  # noqa: E402

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def card_html(tmp_path_factory: pytest.TempPathFactory, canonical_export: dict) -> Iterator[Path]:
    path = tmp_path_factory.mktemp("card") / "card.html"
    path.write_text(render_card_html(build_report(canonical_export)), encoding="utf-8")
    try:
        capture_png(path, path.with_suffix(".probe.png"))
    except CaptureUnavailable as exc:
        pytest.skip(str(exc))
    yield path


def _png_size(path: Path) -> tuple[int, int]:
    width, height = struct.unpack(">II", path.read_bytes()[16:24])
    return width, height


def test_png_is_the_final_frame_at_double_resolution(card_html: Path, tmp_path: Path) -> None:
    out = tmp_path / "card.png"
    capture_png(card_html, out)
    assert _png_size(out) == (2400, 1350)


def test_png_capture_is_repeatable(card_html: Path, tmp_path: Path) -> None:
    first, second = tmp_path / "a.png", tmp_path / "b.png"
    capture_png(card_html, first)
    capture_png(card_html, second)
    assert first.read_bytes() == second.read_bytes()


def test_mp4_has_every_frame(card_html: Path, tmp_path: Path) -> None:
    out = tmp_path / "card.mp4"
    capture_mp4(card_html, out, fps=10, seconds=3)
    frames, seconds = imageio_ffmpeg.count_frames_and_secs(str(out))
    assert abs(frames - 30) <= 1
    assert abs(seconds - 3) < 0.2
