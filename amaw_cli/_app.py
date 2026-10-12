"""The root ``amaw`` Typer application, which every command module registers on."""

from __future__ import annotations

import typer

app = typer.Typer(add_completion=False, help="AMAW administrative CLI.")


@app.callback()
def _main() -> None:
    """AMAW administrative CLI.

    A callback (even an empty one) keeps this a multi-command group, so ``export`` stays a named
    subcommand — ``amaw export GAME_ID`` — rather than Typer collapsing the sole command into a
    nameless top-level one. Room for a later ``benchmark`` command (T-054) without a rename.
    """
