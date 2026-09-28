"""Seat bearer-token minting and hashing (introduced for T-011, extended by T-028).

The single seat-token scheme for AMAW. A Seat stores only ``token_hash`` (TRD §7.3,
INV-ACTOR-001) — never the raw token — so the raw value exists just long enough to hand back
to the admin/runner that set the game up. Game creation persists the hash and returns the raw
tokens out-of-band (see ``app.scenarios.create_game_from_scenario``).

Kept deliberately tiny: T-028 (Seat authentication) builds bearer→game→Actor resolution on
top of :func:`hash_token`, rather than introducing a second scheme. ``app/`` imports no agent
framework (AGENTS.md §6).
"""

from __future__ import annotations

import hashlib
import secrets

# 32 bytes of entropy, URL-safe — a bearer token opaque to the client (TRD §16 "cryptographically
# random Seat tokens"). Full authentication enforcement is T-028.
_TOKEN_NBYTES = 32


def new_seat_token() -> str:
    """Return a fresh cryptographically-random opaque Seat token (raw, never persisted)."""
    return secrets.token_urlsafe(_TOKEN_NBYTES)


def hash_token(token: str) -> str:
    """Return the SHA-256 hex digest of ``token`` — the only representation ever persisted."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
