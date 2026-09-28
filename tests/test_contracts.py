"""I-060 — the shared framework-neutral helpers in ``amaw_contracts``.

Canonical JSON, its SHA-256 and the credential-shaped key set used to be re-implemented per
module (six and two copies). These pin the one definition: the serialisation every persisted
hash — run manifest, analysis plan, Umpire condition, idempotency payload, request topology —
already uses, so no stored hash moves.
"""

from __future__ import annotations

import hashlib
import json

from amaw_contracts import (
    INFERENCE_PROFILE_SCHEMA,
    INFERENCE_SETTING_KEYS,
    PLAYER_INFERENCE_PHASES,
    REASONING_EFFORTS,
    SENSITIVE_KEYS,
    canonical_json,
    canonical_json_size,
    canonical_sha256,
)


def test_canonical_json_is_sorted_minimal_and_keeps_non_ascii() -> None:
    value = {"b": [1, {"z": "ü", "a": None}], "a": True}
    assert canonical_json(value) == b'{"a":true,"b":[1,{"a":null,"z":"\xc3\xbc"}]}'
    # Key order never matters; list order always does.
    assert canonical_json({"a": True, "b": [1, {"a": None, "z": "ü"}]}) == canonical_json(value)
    assert canonical_json({"x": [1, 2]}) != canonical_json({"x": [2, 1]})


def test_canonical_sha256_hashes_the_canonical_bytes() -> None:
    value = {"k": "v", "n": 3}
    assert canonical_sha256(value) == hashlib.sha256(canonical_json(value)).hexdigest()
    assert canonical_sha256({"n": 3, "k": "v"}) == canonical_sha256(value)


def test_canonical_json_size_agrees_with_canonical_json() -> None:
    value = {"text": "naïve", "n": [1, 2, 3]}
    assert canonical_json_size(value) == len(canonical_json(value))
    assert canonical_json_size(value) == len(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    )


def test_sensitive_keys_cover_the_credential_shapes_provenance_rejects() -> None:
    assert {
        "api_key", "authorization", "password", "secret", "seat_token", "seat_tokens",
        "admin_token", "bearer_token",
    } <= SENSITIVE_KEYS


def test_inference_profile_vocabulary_is_the_agents_md_contract() -> None:
    assert INFERENCE_PROFILE_SCHEMA == "inference-profile-v1"
    assert PLAYER_INFERENCE_PHASES == ("diplomacy", "argument", "cons", "reply")
    assert INFERENCE_SETTING_KEYS == ("reasoning_effort", "max_tokens")
    assert REASONING_EFFORTS == ("none", "minimal", "low", "medium", "high", "xhigh")
