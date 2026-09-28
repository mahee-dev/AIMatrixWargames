"""The declared per-phase inference profile: an ``inference-profile-v1`` document and its loader.

``InferenceProfile`` resolves per-phase reasoning effort / completion budget for the player
phases and the Umpire (I-032, I-046) and is sealed, resolved, into the run manifest as a held
constant of the Model Benchmark (benchmark-methodology.md §5.1). It owns nothing about
*applying* the settings — ``agents.player`` does that — and imports only the shared vocabulary
from ``amaw_contracts``, so ``app.resumption``'s structural twin validator reads the same names
without importing the agent layer (TRD §4). Extracted from ``agents/player.py`` in I-063.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from amaw_contracts import (
    INFERENCE_PROFILE_SCHEMA,
    INFERENCE_SETTING_KEYS,
    PLAYER_INFERENCE_PHASES,
    REASONING_EFFORTS,
)

# I-032: a declared per-phase inference profile. The flat ``reasoning_effort``/``max_tokens`` on
# :class:`PlayerConfig` remain the base; a profile overrides them for the named player phases
# (and the Umpire) and is sealed, resolved, into the run manifest. ``uniform`` is the released
# behaviour — no phase-specific setting at all. There are deliberately no built-in ``fast``/
# ``quality`` presets: budgets are pre-committed per experiment as an explicit document.
# The schema / phase / setting / effort vocabulary is the shared contract (``amaw_contracts``),
# so ``app.resumption``'s sealed-recipe validator reads the same names without importing this
# module (I-060); ``agents.player`` still re-exports that vocabulary under its historical names.
INFERENCE_PROFILE_UNIFORM_NAME = "uniform"
# I-046: an absent ``reply`` entry resolves from ``argument`` — a reply is Argument reasoning —
# so an existing profile stays valid and never acquires an implicit provider default.
_INFERENCE_PHASE_FALLBACK = {"reply": "argument"}


class InferenceProfileError(ValueError):
    """An inference-profile document is malformed; nothing downstream is built from it."""


def _validated_inference_entry(name: str, entry: Any) -> dict[str, Any]:
    if not isinstance(entry, dict):
        raise InferenceProfileError(f"inference profile {name!r} must be a mapping")
    unknown = sorted(set(entry) - set(INFERENCE_SETTING_KEYS))
    if unknown:
        raise InferenceProfileError(
            f"inference profile {name!r} has unknown key(s) {', '.join(unknown)}; "
            f"allowed: {', '.join(INFERENCE_SETTING_KEYS)}"
        )
    effort = entry.get("reasoning_effort")
    if effort is not None and effort not in REASONING_EFFORTS:
        raise InferenceProfileError(
            f"inference profile {name!r} reasoning_effort must be one of "
            f"{', '.join(REASONING_EFFORTS)}, got {effort!r}"
        )
    max_tokens = entry.get("max_tokens")
    if max_tokens is not None and (
        isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or max_tokens < 1
    ):
        raise InferenceProfileError(
            f"inference profile {name!r} max_tokens must be a positive integer, got {max_tokens!r}"
        )
    return {"reasoning_effort": effort, "max_tokens": max_tokens}


@dataclass(frozen=True)
class InferenceProfile:
    """A validated ``inference-profile-v1`` document (I-032).

    ``phases`` maps each player phase to its optional ``reasoning_effort``/``max_tokens``
    overrides; ``umpire`` does the same for the adjudicator. An unset value inherits the flat
    role setting at resolution time. The resolved table, not the name, is what provenance seals.
    """

    name: str
    phases: dict[str, dict[str, Any]] = field(default_factory=dict)
    umpire: dict[str, Any] = field(default_factory=dict)

    @property
    def is_uniform(self) -> bool:
        return self.name == INFERENCE_PROFILE_UNIFORM_NAME

    def to_document(self) -> dict[str, Any]:
        """The sealed recipe form: round-trips through :meth:`from_document` (``sealed=True``)."""
        return {
            "schema": INFERENCE_PROFILE_SCHEMA,
            "name": self.name,
            "phases": {phase: dict(entry) for phase, entry in self.phases.items()},
            "umpire": dict(self.umpire),
        }

    @classmethod
    def from_document(cls, document: Any, *, sealed: bool = False) -> InferenceProfile:
        """Validate a parsed YAML/JSON document; every defect fails closed.

        ``sealed`` reads a manifest recipe, where the reserved ``uniform`` name is legitimate —
        but only when the document is genuinely flat, so a tampered recipe cannot hide a
        phase-specific budget behind the released name.
        """
        if not isinstance(document, dict):
            raise InferenceProfileError("inference profile must be a mapping")
        if document.get("schema") != INFERENCE_PROFILE_SCHEMA:
            raise InferenceProfileError(
                f"inference profile schema must be {INFERENCE_PROFILE_SCHEMA!r}, "
                f"got {document.get('schema')!r}"
            )
        name = document.get("name")
        if not isinstance(name, str) or not name.strip():
            raise InferenceProfileError("inference profile needs a non-empty name")
        # I-083: structure first, whatever the name — a malformed document never escapes as the
        # released flat profile.
        unknown = sorted(set(document) - {"schema", "name", "phases", "umpire"})
        if unknown:
            raise InferenceProfileError(
                f"inference profile has unknown key(s) {', '.join(unknown)}"
            )
        phases_document = document.get("phases", {})
        if not isinstance(phases_document, dict):
            raise InferenceProfileError("inference profile 'phases' must be a mapping")
        unknown_phases = sorted(set(phases_document) - set(PLAYER_INFERENCE_PHASES))
        if unknown_phases:
            raise InferenceProfileError(
                f"inference profile names unknown phase(s) {', '.join(unknown_phases)}; "
                f"allowed: {', '.join(PLAYER_INFERENCE_PHASES)}"
            )
        phases = {
            phase: _validated_inference_entry(f"phase {phase}", phases_document[phase])
            for phase in PLAYER_INFERENCE_PHASES
            if phase in phases_document
        }
        umpire = _validated_inference_entry("umpire", document.get("umpire", {}))
        if name == INFERENCE_PROFILE_UNIFORM_NAME:
            if not sealed:
                raise InferenceProfileError(
                    f"inference profile name {name!r} is reserved for the released flat settings"
                )
            if any(
                value is not None for entry in phases.values() for value in entry.values()
            ) or any(value is not None for value in umpire.values()):
                raise InferenceProfileError(
                    "a sealed uniform inference profile cannot carry phase-specific settings"
                )
            return INFERENCE_PROFILE_UNIFORM
        return cls(name=name, phases=phases, umpire=umpire)

    def resolve_phase(
        self, phase: str, *, reasoning_effort: str | None, max_tokens: int | None
    ) -> dict[str, Any]:
        """The effective settings for a player phase over the flat base values."""
        entry = self.phases.get(phase)
        if entry is None:
            entry = self.phases.get(_INFERENCE_PHASE_FALLBACK.get(phase, phase), {})
        return {
            "reasoning_effort": (
                entry.get("reasoning_effort")
                if entry.get("reasoning_effort") is not None else reasoning_effort
            ),
            "max_tokens": (
                entry.get("max_tokens") if entry.get("max_tokens") is not None else max_tokens
            ),
        }

    def resolve_umpire(
        self, *, reasoning_effort: str | None, max_tokens: int | None
    ) -> dict[str, Any]:
        """The effective Umpire settings over the flat base values."""
        return {
            "reasoning_effort": (
                self.umpire.get("reasoning_effort")
                if self.umpire.get("reasoning_effort") is not None else reasoning_effort
            ),
            "max_tokens": (
                self.umpire.get("max_tokens")
                if self.umpire.get("max_tokens") is not None else max_tokens
            ),
        }


INFERENCE_PROFILE_UNIFORM = InferenceProfile(name=INFERENCE_PROFILE_UNIFORM_NAME)


def load_inference_profile(path: Path | str) -> InferenceProfile:
    """Load and validate a YAML or JSON ``inference-profile-v1`` document (I-032)."""
    import yaml

    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise InferenceProfileError(
            f"inference profile {source} could not be read: {exc}"
        ) from exc
    return InferenceProfile.from_document(raw)
