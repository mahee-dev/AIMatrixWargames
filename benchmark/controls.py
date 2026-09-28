"""Benchmark experimental controls (T-055, T-056, T-057, I-078 — WBS 5.3–5.5).

What a batch *declares* before its first game and *verifies* after every attempt: the
interpretation (`benchmark_mode`, methodology §5), the compared roster, the sealed Umpire
condition (§9, agents.md §27, INV-ADJ-004), the complete expected batch condition — the
manifest every attempt is expected to record, derived before any game exists (I-078) — and one
expected attempt per scheduled game (seed and Actor→model assignment, methodology §6, §8). The
server remains the authority on every game; this module only compares the authoritative
manifests it produced against what the batch declared. A mismatch is an experiment-integrity
failure, never ordinary data (D-058).
"""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any

from amaw_contracts import ConsequenceRecipe, UmpireRecipe, canonical_sha256

from app.provenance import resume_umpire_from_provenance

BENCHMARK_MODES = ("model", "agent")

# methodology §5.1 / §5.2 — the interpretation a result set carries. The roster is named in
# the record beside it because the mixed-roster design measures behaviour *in that ecology*.
INTERPRETATIONS = {
    "model": (
        "model benchmark: the compared independent variable is the player model assigned to "
        "each Actor Seat; scenario version, Actor definitions, player prompt, agent framework, "
        "tool schemas, memory and context mechanisms, resource set, Umpire model/prompt/"
        "settings, turn order, action and Message budgets, Con limits and inference profile are "
        "held fixed and verified (benchmark-methodology §5.1). Results describe each model in "
        "the tested interaction ecology of the named roster, not an intrinsic property."
    ),
    "agent": (
        "agent benchmark: the compared unit is the complete agent configuration (model together "
        "with prompt, framework, memory, planning and tool access) rather than model identity "
        "alone; results are agent-configuration comparisons, not pure model comparisons "
        "(benchmark-methodology §5.2). This batch ran one reference player implementation and "
        "one batch-level player configuration; only the model varied by assignment, and no "
        "other agent component is claimed to have varied."
    ),
}


class ControlError(ValueError):
    """A benchmark control is missing, invalid, or contradicted before execution."""


# --- T-056: the fixed Umpire condition ---------------------------------------------------


def seal_umpire_condition(
    umpire: Any,
    *,
    inference_profile: Any,
    resume_umpire: UmpireRecipe | None = None,
) -> dict[str, Any]:
    """The one Umpire condition a batch runs under, from the same provenance the server records.

    ``provenance`` is ``umpire.provenance_configuration()`` — model, provider, prompt version and
    hash, settings (temperature, max tokens, reasoning, timeout, routing) and the adjudication
    schema identity (agents.md §27). ``resume`` is the recipe entry every game must seal
    (derived exactly as the run-manifest endpoint derives it when the recipe omits it), and
    ``inference_profile_umpire`` the profile's Umpire entry, which methodology §9 / D-055 make
    part of the condition.
    """
    provenance = _plain(umpire.provenance_configuration())
    return {
        "provenance": provenance,
        "resume": _plain(resume_umpire) if resume_umpire is not None else (
            resume_umpire_from_provenance(provenance)
        ),
        "inference_profile_umpire": _plain(dict(getattr(inference_profile, "umpire", {}) or {})),
    }


def observed_umpire_condition(manifest: dict[str, Any]) -> dict[str, Any]:
    """The same three parts, read from an authoritative persisted run manifest."""
    raw_resume = manifest.get("resume")
    resume = raw_resume if isinstance(raw_resume, dict) else {}
    raw_profile = resume.get("inference_profile")
    profile = raw_profile if isinstance(raw_profile, dict) else {}
    return {
        "provenance": manifest.get("umpire"),
        "resume": resume.get("umpire"),
        "inference_profile_umpire": dict(profile.get("umpire") or {}),
    }


# --- I-114 / D-092: the fixed consequence resolver ---------------------------------------


def seal_consequence_condition(
    resolver: Any, *, resume_consequence: ConsequenceRecipe
) -> dict[str, Any]:
    """The one consequence-resolver condition a ``bounded_effects_v1`` batch runs under (D-092).

    ``provenance`` is ``resolver.provenance_configuration()``: what the run-manifest endpoint
    seals as the manifest's ``consequence`` for a game that runs the pass (D-091). ``resume`` is
    the recipe section every game seals as ``resume.consequence``. The resolver has no inference
    profile entry, so there is no third part.
    """
    return {
        "provenance": _plain(resolver.provenance_configuration()),
        "resume": _plain(resume_consequence),
    }


def observed_consequence_condition(manifest: dict[str, Any]) -> dict[str, Any]:
    """The same two parts, read from an authoritative persisted run manifest."""
    raw_resume = manifest.get("resume")
    resume = raw_resume if isinstance(raw_resume, dict) else {}
    return {"provenance": manifest.get("consequence"), "resume": resume.get("consequence")}


def differences(sealed: Any, observed: Any, *, path: str = "") -> list[str]:
    """Key paths at which ``observed`` departs from ``sealed`` (recursing through dicts)."""
    if isinstance(sealed, dict) and isinstance(observed, dict):
        found: list[str] = []
        for key in sorted(set(sealed) | set(observed)):
            child = f"{path}.{key}" if path else str(key)
            if key not in sealed or key not in observed:
                found.append(child)
            else:
                found.extend(differences(sealed[key], observed[key], path=child))
        return found
    return [] if _plain(sealed) == _plain(observed) else [path or "<root>"]


def _plain(value: Any) -> Any:
    """A JSON round trip: tuples become lists, so equivalent content compares equal."""
    return json.loads(json.dumps(value, sort_keys=True, default=str))


# --- T-056 / I-078: the batch condition (methodology §5.1, §5.2) --------------------------

# Key paths into the authoritative run manifest. Actor definitions, turn order and the action
# budget live in the scenario document, so its content hash carries them; the resource set is
# the resource manifest hash. Everything a batch varies by design — ``seed``, ``game_id``,
# timestamps, ``player.assignments`` / ``resume.player.assignments`` and the
# ``benchmark.assignment*`` / ``repetition_index`` identity — is deliberately absent here: it
# is verified per attempt by :class:`ExpectedAttempt` instead.

# The common condition: what *every* benchmark holds constant, whichever mode. Methodology
# §5.2 lets an agent benchmark vary the model, prompt, framework, memory, planning and tool
# access — nothing here is on that list (I-078).
COMMON_CONDITION_PATHS: tuple[str, ...] = (
    # scenario version, Actor definitions, turn order, action budget, resource set
    "scenario.id", "scenario.version", "scenario.content_sha256",
    "scenario.resource_manifest_sha256", "scenario.resources",
    # Umpire model, prompt, settings (the sealed condition, seen through the manifest)
    "umpire", "resume.umpire",
    # Diplomacy / Con conditions and the coordinator that schedules activations
    "game_loop", "coordinator", "resume.coordinator", "resume.schema",
    # benchmark analysis code, classification, sealed plan, interpretation, policies
    "code_version", "environment.sha256", "benchmark.run_class", "benchmark.analysis_plan",
    "benchmark.benchmark_mode", "benchmark.rotation_policy", "benchmark.experiment_id",
    "benchmark.seed_policy", "benchmark.scheduling_seed", "benchmark.mode",
)

# The player condition: what a *model* benchmark additionally holds constant (§5.1) and an
# agent benchmark records but may vary (§5.2, D-059).
PLAYER_CONDITION_PATHS: tuple[str, ...] = (
    # player prompt, agent framework, tool schemas, memory mechanism, context delivery mode
    "player.prompt_version", "player.prompt_hash", "player.agent_framework",
    "player.agent_framework_version", "player.tool_schema_version", "player.memory_version",
    "player.context_mode", "player.activation_mode", "player.research_mode",
    "player.search_contract", "player.research_history_mode", "player.tool_surface",
    # per-activation outgoing Message budget, guardrails, inference profile, player settings
    "player.max_messages_per_activation", "player.inference_profile",
    "resume.player.guardrails", "resume.player.temperature", "resume.player.max_tokens",
    "resume.player.timeout", "resume.player.reasoning_effort", "resume.player.prompt_version",
    "resume.player.activation_mode", "resume.player.research_mode",
    "resume.player.search_contract", "resume.player.research_history_mode",
    "resume.player.tool_surface",
    "resume.inference_profile",
)

# The full §5.1 held-constant set of a model benchmark, as one tuple.
HELD_CONSTANT_PATHS: tuple[str, ...] = COMMON_CONDITION_PATHS + PLAYER_CONDITION_PATHS

# What one attempt is expected to record of its scheduled identity (methodology §6 "always
# store actual assignment per run", §8 "seed recorded"): the server's seed and the Actor→model
# assignment as the players, the resume recipe and the batch section each recorded it.
EXPECTED_ATTEMPT_PATHS: tuple[str, ...] = (
    "seed", "player.assignments", "resume.player.assignments", "benchmark.assignment",
    "benchmark.assignment_id", "benchmark.assignment_index", "benchmark.repetition_index",
)


# The Umpire's §5.1 entries are verified by the sealed Umpire condition (both modes, stricter:
# it also covers the profile's Umpire entry), so the held-constant check leaves them to it and
# one drift is reported once, under its own kind.
UMPIRE_CONDITION_PATHS: tuple[str, ...] = ("umpire", "resume.umpire")


def held_constant_condition(manifest: dict[str, Any]) -> dict[str, Any]:
    """Project a manifest onto the §5.1 held-constant paths (missing paths record as ``None``)."""
    return {path: _plain(_lookup(manifest, path)) for path in HELD_CONSTANT_PATHS}


@dataclass(frozen=True)
class ExpectedBatchCondition:
    """The condition every attempt of a batch is expected to record, declared before game 1.

    ``condition`` maps each :data:`HELD_CONSTANT_PATHS` path to its expected value, projected
    from the manifest configuration the runner will submit (one builder —
    :func:`agents.autonomous.expected_manifest_configuration`), the scenario the batch loaded
    and the sealed Umpire condition. Under D-058 the first manifest *was* the condition; here
    the first manifest is verified like every other (I-078).
    """

    condition: dict[str, Any]
    sha256: str

    @classmethod
    def derive(
        cls,
        *,
        scenario: Any,
        configuration: dict[str, Any],
        umpire_condition: dict[str, Any],
    ) -> ExpectedBatchCondition:
        """``scenario`` is an ``app.scenarios.LoadedScenario``; ``configuration`` the runner's
        expected manifest configuration; ``umpire_condition`` the :func:`seal_umpire_condition`
        result. The Umpire sections are placed exactly as the run-manifest endpoint places
        them."""
        manifest: dict[str, Any] = {
            "scenario": {
                "id": scenario.scenario.id,
                "version": str(scenario.scenario.version),
                "content_sha256": scenario.scenario_content_sha256,
                "resource_manifest_sha256": scenario.resource_manifest_sha256,
                "resources": [
                    {"id": str(entry.get("id")), "content_sha256": entry.get("content_sha256")}
                    for entry in scenario.resource_manifest
                ],
            },
            **_plain(configuration),
            "umpire": umpire_condition["provenance"],
        }
        resume = manifest.get("resume")
        if isinstance(resume, dict) and "umpire" not in resume:
            resume["umpire"] = umpire_condition["resume"]
        condition = held_constant_condition(manifest)
        return cls(condition=condition, sha256=canonical_sha256(condition))

    def record(self) -> dict[str, Any]:
        return {"expected_condition": self.condition, "expected_condition_sha256": self.sha256}

    def verify(self, manifest: dict[str, Any], *, benchmark_mode: str) -> list[str]:
        """Paths at which ``manifest`` departs from the expectation for this mode.

        A model benchmark is held to the whole §5.1 set; an agent benchmark to the common
        condition only (§5.2). The Umpire paths are verified by the sealed Umpire condition
        (both modes, stricter) so one Umpire drift is reported once, under its own kind.
        """
        paths = HELD_CONSTANT_PATHS if benchmark_mode == "model" else COMMON_CONDITION_PATHS
        observed = held_constant_condition(manifest)
        return [
            path for path in paths
            if path not in UMPIRE_CONDITION_PATHS and self.condition.get(path) != observed.get(path)
        ]


@dataclass(frozen=True)
class ExpectedAttempt:
    """What one scheduled attempt's manifest must record of its identity (methodology §6, §8)."""

    assignment_id: str
    assignment_index: int
    repetition_index: int
    seed: int
    assignments: dict[str, str]

    @classmethod
    def of(cls, attempt: Any) -> ExpectedAttempt:
        """From a ``benchmark.runner.ScheduledAttempt``."""
        return cls(
            assignment_id=attempt.assignment_id,
            assignment_index=attempt.assignment_index,
            repetition_index=attempt.repetition_index,
            seed=attempt.seed,
            assignments=dict(attempt.models),
        )

    def expected(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "player.assignments": dict(self.assignments),
            "resume.player.assignments": dict(self.assignments),
            "benchmark.assignment": dict(self.assignments),
            "benchmark.assignment_id": self.assignment_id,
            "benchmark.assignment_index": self.assignment_index,
            "benchmark.repetition_index": self.repetition_index,
        }

    def verify(self, manifest: dict[str, Any]) -> list[str]:
        """Differing key paths between the scheduled identity and what the manifest recorded."""
        found: list[str] = []
        for path, expected in self.expected().items():
            found.extend(differences(expected, _lookup(manifest, path), path=path))
        return found


def _lookup(value: Any, path: str) -> Any:
    for key in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


# --- I-078: the predeclared plan is reconciled with the built schedule ---------------------


def reconcile_plan(
    plan: Any, *, schedule: Any, roster: list[str], plan_level: str | None = None
) -> dict[str, Any]:
    """Check an analysis plan's ``design`` against the schedule before any game exists.

    A batch is either the whole declared experiment (``planned_attempts`` equals the batch and
    the treatment levels are exactly the compared roster) or one *cell* of a treatment varied
    across batches — the shipped example's shape, one batch per level, each with the plan's
    repetitions (``planned_attempts == total_games × len(levels)``). Anything else is refused by
    name (methodology §7, §21: the denominator is fixed before execution).

    ``analysis-plan-v1`` states ``seed_policy`` and ``rotation_policy`` as prose, so they are
    recorded beside the resolved policies, not compared, and the scope is inferred from the
    counts. ``analysis-plan-v2`` declares all three by value, and each must equal what the
    schedule does; a v2 ``cell`` batch must name its level (``plan_level``), which is recorded
    with the batch and never sealed in the plan (methodology §4.2, D-109).
    """
    from benchmark.analysis_plan import ANALYSIS_PLAN_SCHEMA_V2
    from benchmark.runner import SEED_POLICY

    structured = plan.content["schema"] == ANALYSIS_PLAN_SCHEMA_V2
    design = plan.content["design"]
    counts = schedule.counts()
    total = counts["total_games"]
    levels = list(plan.content["treatment"]["levels"])
    planned = design["planned_attempts"]
    if design["repetitions_per_assignment"] != schedule.repetitions:
        raise ControlError(
            f"analysis plan design repetitions_per_assignment "
            f"{design['repetitions_per_assignment']} disagrees with the scheduled "
            f"{schedule.repetitions}"
        )
    if structured:
        if design["rotation_policy"] != schedule.rotation_policy:
            raise ControlError(
                f"analysis plan design rotation_policy {design['rotation_policy']!r} disagrees "
                f"with the scheduled {schedule.rotation_policy!r}"
            )
        if design["seed_policy"] != SEED_POLICY:
            raise ControlError(
                f"analysis plan design seed_policy {design['seed_policy']!r} disagrees with the "
                f"runner's {SEED_POLICY!r}"
            )
    if planned == total:
        scope = "experiment"
    elif len(levels) > 1 and planned == total * len(levels):
        scope = "cell"
    else:
        raise ControlError(
            f"analysis plan design planned_attempts {planned} disagrees with the scheduled "
            f"total_games {total} (the whole experiment) and with {total} × {len(levels)} "
            "treatment levels (one batch per level)"
        )
    if structured and design["scope"] != scope:
        raise ControlError(
            f"analysis plan design scope {design['scope']!r} disagrees with the schedule: "
            f"planned_attempts {planned} over total_games {total} and {len(levels)} treatment "
            f"levels make this batch {scope!r}"
        )
    if scope == "experiment" and sorted(levels) != sorted(roster):
        raise ControlError(
            f"analysis plan treatment levels {levels} are not the compared roster "
            f"{list(roster)}; a batch that is the whole planned experiment varies only "
            "the player model"
        )
    if structured and scope == "cell":
        if plan_level is None:
            raise ControlError(
                f"analysis plan scope cell needs --plan-level, one of the treatment levels {levels}"
            )
        if plan_level not in levels:
            raise ControlError(
                f"--plan-level {plan_level!r} is not one of the treatment levels {levels}"
            )
    elif plan_level is not None:
        raise ControlError(
            "--plan-level names the level of a cell batch, which only an analysis-plan-v2 plan "
            "with design scope cell declares"
        )

    record: dict[str, Any] = {
        "plan_id": plan.plan_id,
        "plan_sha256": plan.sha256,
        "planned_attempts": planned,
        "repetitions_per_assignment": design["repetitions_per_assignment"],
        "batch_total_games": total,
        "plan_scope": scope,
        "treatment_levels": levels,
        "plan_seed_policy": design["seed_policy"],
        "plan_rotation_policy": design["rotation_policy"],
        "rotation_policy": schedule.rotation_policy,
        "seed_policy": SEED_POLICY,
    }
    if structured:
        # A v1 record keeps exactly its I-078 keys; only v2 adds the schema and the named level.
        record["plan_schema"] = ANALYSIS_PLAN_SCHEMA_V2
        record["plan_level"] = plan_level
    return record


# --- T-057: the declared batch controls ---------------------------------------------------


@dataclass(frozen=True)
class BatchControls:
    """What one batch declares up front (methodology §4): mode, roster, sealed Umpire, the
    sealed consequence resolver under ``bounded_effects_v1`` (D-092), and the complete expected
    condition every attempt is held to (I-078)."""

    benchmark_mode: str
    roster: tuple[str, ...]
    interpretation: str
    umpire_condition: dict[str, Any]
    umpire_condition_sha256: str
    expected_condition: ExpectedBatchCondition
    consequence_condition: dict[str, Any] | None = None
    consequence_condition_sha256: str | None = None

    @classmethod
    def declare(
        cls,
        *,
        benchmark_mode: str,
        roster: list[str],
        umpire_condition: dict[str, Any],
        expected_condition: ExpectedBatchCondition,
        consequence_condition: dict[str, Any] | None = None,
    ) -> BatchControls:
        if benchmark_mode not in BENCHMARK_MODES:
            raise ControlError(
                f"--benchmark-mode must be one of {', '.join(BENCHMARK_MODES)}, "
                f"got {benchmark_mode!r}"
            )
        condition = _plain(umpire_condition)
        consequence = _plain(consequence_condition) if consequence_condition is not None else None
        declared_mode = expected_condition.condition.get("benchmark.benchmark_mode")
        if declared_mode != benchmark_mode:
            raise ControlError(
                f"expected condition declares benchmark_mode {declared_mode!r}, "
                f"the batch runs {benchmark_mode!r}"
            )
        return cls(
            benchmark_mode=benchmark_mode,
            roster=tuple(roster),
            interpretation=INTERPRETATIONS[benchmark_mode],
            umpire_condition=condition,
            umpire_condition_sha256=canonical_sha256(condition),
            expected_condition=expected_condition,
            consequence_condition=consequence,
            consequence_condition_sha256=(
                canonical_sha256(consequence) if consequence is not None else None
            ),
        )

    def record(self) -> dict[str, Any]:
        """The ``batch.json`` fields this declaration contributes.

        The consequence condition appears only when declared, so a ``none`` batch records
        exactly the released fields (D-092, as D-091 keeps a ``none`` manifest's shape).
        """
        record: dict[str, object] = {
            "benchmark_mode": self.benchmark_mode,
            "roster": list(self.roster),
            "interpretation": self.interpretation,
            "umpire_condition": self.umpire_condition,
            "umpire_condition_sha256": self.umpire_condition_sha256,
        }
        if self.consequence_condition is not None:
            record["consequence_condition"] = self.consequence_condition
            record["consequence_condition_sha256"] = self.consequence_condition_sha256
        return {**record, **self.expected_condition.record()}

    def verify_condition(self, manifest: dict[str, Any]) -> list[str]:
        """Expected-condition paths at which a manifest departs from the declaration."""
        return self.expected_condition.verify(manifest, benchmark_mode=self.benchmark_mode)

    def verify_umpire(self, manifest: dict[str, Any] | None) -> list[str]:
        """Differing key paths between the sealed Umpire condition and a game's manifest."""
        if not isinstance(manifest, dict):
            return []
        return differences(self.umpire_condition, observed_umpire_condition(manifest))

    def verify_consequence(self, manifest: dict[str, Any] | None) -> list[str]:
        """Differing key paths between the sealed consequence condition and a game's manifest.

        Both benchmark modes: the resolver is common to the batch, not player configuration
        (methodology §9.1, D-092). A batch that declared none expects neither manifest section.
        """
        if not isinstance(manifest, dict):
            return []
        sealed = self.consequence_condition or {"provenance": None, "resume": None}
        return differences(sealed, observed_consequence_condition(manifest))
