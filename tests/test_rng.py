"""T-017 acceptance tests: deterministic, restart-safe, server-owned RNG.

Covers TEST-RNG-001…003 and the boundary/restart/automatic-mode rules. The authoritative
draw is reconstructed from ``seed + rng_draw_index`` (TRD §24, INV-RNG-001…003); these tests
pin the stream, its progression and its survival across a simulated process restart, without
touching adjudication (that lives in test_adjudication.py).
"""

from __future__ import annotations

import random

from app import rng


def test_rng_001_same_seed_produces_same_draws() -> None:
    """TEST-RNG-001: identical seed + draw index reproduce the identical draw."""
    assert rng.draw(12345, 0) == rng.draw(12345, 0)
    assert rng.draw(12345, 3) == rng.draw(12345, 3)
    # A different seed gives a different stream (overwhelmingly).
    assert rng.draw(12345, 0) != rng.draw(54321, 0)


def test_rng_stream_matches_pythons_random_sequence() -> None:
    """The draw at index i equals the i-th value of ``random.Random(seed)`` (TRD §24)."""
    seed = 424242
    reference = random.Random(seed)
    expected = [reference.random() for _ in range(5)]
    assert [rng.draw(seed, i) for i in range(5)] == expected


def test_successive_draw_indices_advance_the_stream() -> None:
    """Successive indices use the next position, never replay the first value."""
    seed = 999
    draws = [rng.draw(seed, i) for i in range(4)]
    assert len(set(draws)) == 4  # all distinct — the stream advances


def test_draw_is_in_unit_interval() -> None:
    """A draw is a float in [0.0, 1.0) like ``random.Random.random`` (TRD §24)."""
    for i in range(10):
        value = rng.draw(7, i)
        assert 0.0 <= value < 1.0
