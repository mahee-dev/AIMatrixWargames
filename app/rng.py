"""Deterministic, restart-safe, server-owned game RNG (T-017, TRD §24, INV-RNG-001…004).

The server — never a model — performs every draw (INV-RNG-001). A game's random stream is
fully determined by its persisted ``seed`` and ``rng_draw_index`` (INV-RNG-002/003): the draw
at a given index is reconstructed from ``random.Random(seed)`` advanced past the already-used
draws, so it survives a process restart and continues from the next value rather than replaying
an old one. The caller (``app.game_service``) draws inside the adjudication transaction and
increments ``Game.rng_draw_index`` there, keeping draw and increment atomic with the
authoritative record.

This is deliberately **not** a general random-number service — it exists only to support
authoritative game randomness. ``random.Random`` is the exact PRNG the contract names
(TRD §24 ``rng = random.Random(seed)``); it is not swapped for hashing or another generator.
"""

from __future__ import annotations

import random


def draw(seed: int, draw_index: int) -> float:
    """Return the deterministic draw at ``draw_index`` for a game seeded with ``seed``.

    Equivalent to the ``draw_index``-th value (0-based) of ``random.Random(seed).random()``:
    a fresh generator is seeded and advanced past ``draw_index`` already-consumed draws, then
    one value is returned. Reconstructing from ``seed + draw_index`` (rather than holding a
    live generator in memory) is what makes the stream restart-safe (INV-RNG-003).
    """
    if draw_index < 0:
        raise ValueError(f"draw_index must be non-negative, got {draw_index!r}")
    generator = random.Random(seed)
    for _ in range(draw_index):
        generator.random()
    return generator.random()
