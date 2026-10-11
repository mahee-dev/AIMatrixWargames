"""The terminal viewer's screens: Live Command, Actor Network, Adjudication and World State.

Every panel draws from :class:`~observer.viewstate.ViewState` alone, so what a screen shows is
what the feed recorded up to the cursor. A panel with nothing recorded says so; nothing is
inferred, scored or invented (no "tension" gauges, maps or Actor intentions).

Layouts are chosen from the terminal's size when a frame is drawn: wide (120+ columns),
normal (80-119) and narrow (under 80). Panels are built once their width and height are known,
so tails of feeds show the newest lines and nothing overflows the screen. A panel that fails to
draw shows the error in place; it never takes the viewer down.
"""

from observer.screens.common import ASCII, DASHED, UNICODE, Glyphs, clip, probability_bar
from observer.screens.frame import Frame, build_frame

__all__ = [
    "ASCII", "DASHED", "UNICODE", "Frame", "Glyphs", "build_frame", "clip", "probability_bar",
]
