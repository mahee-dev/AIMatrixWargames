"""Predeclared analysis and assurance plans for analytical runs (I-047, D-054).

`benchmark-methodology.md §4` distinguishes a *playtest* from an *analytical* run. An analytical
run declares, before execution, what question it asks and how it will answer it; the plan is
sealed into the game's immutable run manifest (T-053, TRD §28) so the manifest's creation time,
not a self-reported date, is the evidence of predeclaration. This module lives outside ``app/``:
the engine stores the sealed plan as opaque configuration and understands no research semantics.
"""

from __future__ import annotations

from datetime import datetime
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from amaw_contracts import (
    canonical_json,
    canonical_sha256,
    first_non_json_scalar,
    is_sensitive_key,
)

import yaml


ANALYSIS_PLAN_SCHEMA = "analysis-plan-v1"
# v2 is v1 with a structured ``design`` that ``reconcile_plan`` compares with the schedule
# (methodology §4.2, I-090, D-109). v1 stays loadable, so a sealed v1 plan keeps its meaning.
ANALYSIS_PLAN_SCHEMA_V2 = "analysis-plan-v2"
ANALYSIS_PLAN_SCHEMAS = (ANALYSIS_PLAN_SCHEMA, ANALYSIS_PLAN_SCHEMA_V2)
MEASURE_ROLES = ("primary", "secondary")
PLAN_SCOPES = ("experiment", "cell")

# Everything an analytical run must declare before execution (I-047). Optional keys carry
# context only; anything else is rejected so an undeclared metric cannot masquerade as a plan.
_REQUIRED_TOP_LEVEL = (
    "schema", "plan_id", "title", "research_question", "hypotheses", "experimental_unit",
    "treatment", "fixed_conditions", "outcome_measures", "estimand", "design", "failure_rules",
    "analysis_method", "assurance_checks", "authored_at",
)
_OPTIONAL_TOP_LEVEL = ("descriptive_telemetry", "notes", "author")
_REQUIRED_TREATMENT = ("factor", "levels")
_REQUIRED_MEASURE = ("id", "definition", "source", "role")
_REQUIRED_DESIGN = (
    "planned_attempts", "repetitions_per_assignment", "seed_policy", "rotation_policy"
)
_REQUIRED_DESIGN_V2 = ("scope", *_REQUIRED_DESIGN)
_REQUIRED_FAILURE_RULES = ("failed_games", "exclusion_criteria", "missing_data")
_REQUIRED_HYPOTHESIS = ("id", "statement")
_REQUIRED_ASSURANCE = ("id", "description")
# I-086: every nested mapping the §4.2 vocabulary names has an explicit key set, as strict as the
# top level. No nested section has optional keys (v1 or v2), so allowed == required.
_ALLOWED_HYPOTHESIS = _REQUIRED_HYPOTHESIS
_ALLOWED_TREATMENT = _REQUIRED_TREATMENT
_ALLOWED_MEASURE = _REQUIRED_MEASURE
_ALLOWED_FAILURE_RULES = _REQUIRED_FAILURE_RULES
_ALLOWED_ASSURANCE = _REQUIRED_ASSURANCE
# Declarations the contract types: prose is a non-empty string, enumerations are lists of them.
# ``design.planned_attempts`` / ``repetitions_per_assignment`` keep their positive-integer rule;
# ``notes`` and ``author`` are untyped context.
_STRING_PATHS = (
    "plan_id", "title", "research_question", "experimental_unit", "estimand",
    "analysis_method", "authored_at", "treatment.factor", "design.scope", "design.seed_policy",
    "design.rotation_policy", "failure_rules.failed_games", "failure_rules.missing_data",
)
_STRING_LIST_PATHS = (
    "fixed_conditions", "treatment.levels", "failure_rules.exclusion_criteria",
    "descriptive_telemetry",
)
_STRING_ENTRY_FIELDS = {
    "hypotheses": _REQUIRED_HYPOTHESIS,
    "outcome_measures": _REQUIRED_MEASURE,
    "assurance_checks": _REQUIRED_ASSURANCE,
}


class AnalysisPlanError(ValueError):
    """An analysis plan is missing a required declaration or is otherwise unusable."""


@dataclass(frozen=True)
class AnalysisPlan:
    plan_id: str
    content: dict[str, Any]
    sha256: str

    def outcome_ids(self) -> tuple[str, ...]:
        return tuple(str(m["id"]) for m in self.content["outcome_measures"])

    def primary_outcome_ids(self) -> tuple[str, ...]:
        return tuple(
            str(m["id"]) for m in self.content["outcome_measures"] if m["role"] == "primary"
        )


def plan_sha256(content: dict[str, Any]) -> str:
    """Identity of a plan: the hash of its canonical JSON (key order and whitespace independent)."""
    return canonical_sha256(content)


def _present(value: Any) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return len(value) > 0
    return value is not None


def _require(mapping: dict[str, Any], keys: tuple[str, ...], *, where: str, allow_empty=()) -> None:
    if not isinstance(mapping, dict):
        raise AnalysisPlanError(f"analysis plan {where} must be a mapping")
    for key in keys:
        if key not in mapping:
            raise AnalysisPlanError(f"analysis plan {where} is missing required '{key}'")
        if key not in allow_empty and not _present(mapping[key]):
            raise AnalysisPlanError(f"analysis plan {where} '{key}' must not be empty")


def _reject_undeclared(mapping: dict[str, Any], allowed: tuple[str, ...], *, path: str) -> None:
    undeclared = sorted(set(mapping) - set(allowed))
    if undeclared:
        raise AnalysisPlanError(
            f"analysis plan '{path}' has undeclared keys: {', '.join(undeclared)}"
        )


def _require_entries(
    entries: Any, keys: tuple[str, ...], *, where: str, allowed: tuple[str, ...] | None = None
) -> None:
    if not isinstance(entries, list) or not entries:
        raise AnalysisPlanError(f"analysis plan '{where}' must be a non-empty list")
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise AnalysisPlanError(f"analysis plan '{where}[{index}]' must be a mapping")
        _require(entry, keys, where=f"'{where}[{index}]'")
        _reject_undeclared(entry, allowed or keys, path=f"{where}[{index}]")


def _lookup(content: dict[str, Any], dotted: str) -> tuple[bool, Any]:
    value: Any = content
    for step in dotted.split("."):
        if not isinstance(value, dict) or step not in value:
            return False, None
        value = value[step]
    return True, value


def _require_string(value: Any, *, path: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise AnalysisPlanError(f"analysis plan '{path}' must be a non-empty string")


def _require_string_list(value: Any, *, path: str) -> None:
    if not isinstance(value, list) or any(
        not isinstance(item, str) or not item.strip() for item in value
    ):
        raise AnalysisPlanError(f"analysis plan '{path}' must be a list of non-empty strings")


def _require_types(content: dict[str, Any]) -> None:
    """I-086: nested declarations are typed where the §4.2 contract implies a type."""
    for dotted in _STRING_PATHS:
        present, value = _lookup(content, dotted)
        if present:
            _require_string(value, path=dotted)
    for dotted in _STRING_LIST_PATHS:
        present, value = _lookup(content, dotted)
        if present:
            _require_string_list(value, path=dotted)
    for section, fields in _STRING_ENTRY_FIELDS.items():
        for index, entry in enumerate(content[section]):
            for field in fields:
                _require_string(entry[field], path=f"{section}[{index}].{field}")


def _reject_sensitive_keys(value: Any, *, path: str) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if is_sensitive_key(key):
                raise AnalysisPlanError(f"analysis plan {path} contains prohibited field {key!r}")
            _reject_sensitive_keys(nested, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            _reject_sensitive_keys(nested, path=f"{path}[{index}]")


def validate_analysis_plan(content: dict[str, Any]) -> None:
    """Fail closed, naming the first missing or empty declaration."""
    if not isinstance(content, dict):
        raise AnalysisPlanError("analysis plan must be a mapping")
    unknown = sorted(set(content) - set(_REQUIRED_TOP_LEVEL) - set(_OPTIONAL_TOP_LEVEL))
    if unknown:
        raise AnalysisPlanError(f"analysis plan has undeclared keys: {', '.join(unknown)}")
    _reject_sensitive_keys(content, path="plan")
    _require(content, _REQUIRED_TOP_LEVEL, where="document")
    if content["schema"] not in ANALYSIS_PLAN_SCHEMAS:
        raise AnalysisPlanError(
            f"analysis plan schema must be one of {ANALYSIS_PLAN_SCHEMAS}, "
            f"got {content['schema']!r}"
        )
    _require_entries(
        content["hypotheses"], _REQUIRED_HYPOTHESIS, where="hypotheses", allowed=_ALLOWED_HYPOTHESIS
    )
    _require(content["treatment"], _REQUIRED_TREATMENT, where="'treatment'")
    _reject_undeclared(content["treatment"], _ALLOWED_TREATMENT, path="treatment")
    if not isinstance(content["treatment"]["levels"], list):
        raise AnalysisPlanError("analysis plan 'treatment' 'levels' must be a list")
    if not isinstance(content["fixed_conditions"], list):
        raise AnalysisPlanError("analysis plan 'fixed_conditions' must be a list")
    _require_entries(
        content["outcome_measures"], _REQUIRED_MEASURE, where="outcome_measures",
        allowed=_ALLOWED_MEASURE,
    )
    roles = [m["role"] for m in content["outcome_measures"]]
    if any(role not in MEASURE_ROLES for role in roles):
        raise AnalysisPlanError(f"analysis plan outcome_measures role must be one of {MEASURE_ROLES}")
    if "primary" not in roles:
        raise AnalysisPlanError("analysis plan outcome_measures must declare at least one primary")
    ids = [m["id"] for m in content["outcome_measures"]]
    if len(set(ids)) != len(ids):
        raise AnalysisPlanError("analysis plan outcome_measures ids must be unique")
    design_keys = (
        _REQUIRED_DESIGN_V2 if content["schema"] == ANALYSIS_PLAN_SCHEMA_V2 else _REQUIRED_DESIGN
    )
    _require(content["design"], design_keys, where="'design'")
    _reject_undeclared(content["design"], design_keys, path="design")
    for key in ("planned_attempts", "repetitions_per_assignment"):
        value = content["design"][key]
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise AnalysisPlanError(f"analysis plan 'design' '{key}' must be a positive integer")
    # Predeclared exclusions may legitimately be "none", so the list must exist but may be empty.
    _require(
        content["failure_rules"], _REQUIRED_FAILURE_RULES, where="'failure_rules'",
        allow_empty=("exclusion_criteria",),
    )
    _reject_undeclared(content["failure_rules"], _ALLOWED_FAILURE_RULES, path="failure_rules")
    if not isinstance(content["failure_rules"]["exclusion_criteria"], list):
        raise AnalysisPlanError("analysis plan 'failure_rules' 'exclusion_criteria' must be a list")
    _require_entries(
        content["assurance_checks"], _REQUIRED_ASSURANCE, where="assurance_checks",
        allowed=_ALLOWED_ASSURANCE,
    )
    _require_types(content)
    if content["schema"] == ANALYSIS_PLAN_SCHEMA_V2:
        _validate_structured_design(content)


def _validate_structured_design(content: dict[str, Any]) -> None:
    """A v2 ``design`` names the runner's own policies, so a batch can compare them by value.

    The vocabularies are the runner's: a plan can only declare a policy the runner can execute
    (methodology §4.2, §6, §8; D-109).
    """
    from benchmark.runner import ROTATION_POLICIES, SEED_POLICY

    design = content["design"]
    if design["scope"] not in PLAN_SCOPES:
        raise AnalysisPlanError(
            f"analysis plan 'design' 'scope' must be one of {PLAN_SCOPES}, got {design['scope']!r}"
        )
    if design["rotation_policy"] not in ROTATION_POLICIES:
        raise AnalysisPlanError(
            f"analysis plan 'design' 'rotation_policy' must be one of {ROTATION_POLICIES}, "
            f"got {design['rotation_policy']!r}"
        )
    if design["seed_policy"] != SEED_POLICY:
        raise AnalysisPlanError(
            f"analysis plan 'design' 'seed_policy' must be {SEED_POLICY!r}, "
            f"got {design['seed_policy']!r}"
        )
    if design["scope"] == "cell" and len(content["treatment"]["levels"]) < 2:
        raise AnalysisPlanError(
            "analysis plan 'design' 'scope' cell varies the treatment across batches and needs "
            "at least two 'treatment' 'levels'"
        )


def load_analysis_plan(path: Path | str) -> AnalysisPlan:
    """Load and validate a YAML or JSON `analysis-plan-v1` or `-v2` document."""
    source = Path(path)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise AnalysisPlanError(f"analysis plan {source} could not be read: {exc}") from exc
    if not isinstance(raw, dict):
        raise AnalysisPlanError(f"analysis plan {source} must be a mapping")
    # I-086: reject, do not coerce. An unquoted YAML date/timestamp or a non-finite number has no
    # canonical JSON form; coercing it would seal text the author never wrote (methodology §4.2).
    # I-093: mapping keys are part of that contract (an unquoted ``2:`` must not seal as ``"2"``),
    # and a recursive alias is refused here rather than surfacing as a ``RecursionError``.
    found = first_non_json_scalar(raw)
    if found is not None:
        path, scalar, reason = found
        if reason == "key":
            raise AnalysisPlanError(
                f"analysis plan '{path or 'plan'}' has a non-string mapping key {scalar!r} "
                f"({type(scalar).__name__}); quote it"
            )
        if reason == "cycle":
            raise AnalysisPlanError(
                f"analysis plan '{path}' is a circular structure (recursive alias)"
            )
        if isinstance(scalar, float):
            raise AnalysisPlanError(
                f"analysis plan '{path}' must be a finite number, got {scalar!r}"
            )
        raise AnalysisPlanError(
            f"analysis plan '{path}' must be a quoted string, got YAML {type(scalar).__name__} "
            f"{scalar!r}"
        )
    content = json.loads(canonical_json(raw))
    validate_analysis_plan(content)
    return AnalysisPlan(plan_id=str(content["plan_id"]), content=content, sha256=plan_sha256(content))


def classify_measure(plan: AnalysisPlan, measure_id: str) -> str:
    """A declared outcome is an ``outcome``; anything else recorded is ``telemetry``."""
    return "outcome" if measure_id in plan.outcome_ids() else "telemetry"


# --- Registration verification for reports (T-058 boundary) ---------------------------------

REGISTRATIONS = ("predeclared", "post_hoc_plan", "unplanned")


def run_class_of(export: dict[str, Any]) -> str:
    """Read a game export's run class; anything without the I-047 field is a playtest."""
    manifest = export.get("run_manifest") or {}
    benchmark = manifest.get("benchmark") or {}
    return str(benchmark.get("run_class") or "playtest")


@dataclass(frozen=True)
class GameRegistration:
    game_id: str
    registration: str
    sealed_at: str | None
    sealed_plan_sha256: str | None
    # I-078: whether the manifest's creation preceded the export's ``game_started`` event;
    # ``None`` when the export carries no such event (a legacy export) or no plan.
    sealed_before_start: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "game_id": self.game_id,
            "registration": self.registration,
            "sealed_at": self.sealed_at,
            "sealed_plan_sha256": self.sealed_plan_sha256,
            "sealed_before_start": self.sealed_before_start,
        }


@dataclass(frozen=True)
class RegistrationReport:
    plan_id: str
    plan_sha256: str
    games: tuple[GameRegistration, ...]

    @property
    def counts(self) -> dict[str, int]:
        return {
            name: sum(1 for game in self.games if game.registration == name)
            for name in REGISTRATIONS
        }

    @property
    def all_predeclared(self) -> bool:
        """True only when every analysed game sealed exactly this plan before execution."""
        return bool(self.games) and all(g.registration == "predeclared" for g in self.games)

    def to_dict(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_sha256": self.plan_sha256,
            "all_predeclared": self.all_predeclared,
            "counts": self.counts,
            "games": [game.to_dict() for game in self.games],
        }


def _game_started_at(export: dict[str, Any]) -> str | None:
    for event in export.get("events") or []:
        if isinstance(event, dict) and event.get("type") == "game_started":
            return event.get("created_at")
    return None


def _sealed_before_start(sealed_at: str | None, started_at: str | None) -> bool | None:
    """Whether the manifest was created no later than the game started (I-078).

    Both timestamps are ISO-8601 with offsets, as ``app.export`` writes them; an export without
    a ``game_started`` event (legacy) cannot be timed and reports ``None``.
    """
    if sealed_at is None or started_at is None:
        return None
    try:
        return datetime.fromisoformat(sealed_at) <= datetime.fromisoformat(started_at)
    except ValueError:
        return None


def verify_registration(plan: AnalysisPlan, exports: list[dict[str, Any]]) -> RegistrationReport:
    """Classify each game export against ``plan``.

    ``predeclared``: the immutable manifest sealed this exact plan (same sha256) at creation,
    and — where the export can be timed — that creation preceded ``game_started`` (I-078).
    ``post_hoc_plan``: a different plan was sealed, or this plan was sealed after play started —
    the plan is not evidence of predeclaration either way.
    ``unplanned``: a playtest or legacy game with no sealed plan.

    The time check is an assurance check: for games the server flagged
    ``require_run_manifest`` it is redundant with lobby-only sealing; for legacy or unflagged
    analytical games it is what still separates predeclaration from late sealing.
    """
    games: list[GameRegistration] = []
    for export in exports:
        manifest = export.get("run_manifest") or {}
        sealed = (manifest.get("benchmark") or {}).get("analysis_plan") or {}
        sealed_sha = sealed.get("sha256")
        game_id = str((export.get("game") or {}).get("id") or manifest.get("game_id") or "?")
        sealed_at = manifest.get("created_at") if sealed_sha is not None else None
        before_start = (
            _sealed_before_start(sealed_at, _game_started_at(export))
            if sealed_sha is not None else None
        )
        if sealed_sha is None:
            registration = "unplanned"
        elif sealed_sha == plan.sha256 and before_start is not False:
            registration = "predeclared"
        else:
            registration = "post_hoc_plan"
        games.append(
            GameRegistration(
                game_id=game_id,
                registration=registration,
                sealed_at=sealed_at,
                sealed_plan_sha256=sealed_sha,
                sealed_before_start=before_start,
            )
        )
    return RegistrationReport(plan_id=plan.plan_id, plan_sha256=plan.sha256, games=tuple(games))
