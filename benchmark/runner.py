"""Benchmark batch runner (T-054, T-055, T-056, T-059 — TRD §30; methodology §6–§9, §21).

The complete execution schedule is generated before any game exists: every attempt has a stable
``(assignment_id, repetition_index)`` identity and a game seed derived *only* from that identity.
``scheduling_seed`` permutes execution order and nothing else (methodology §8), so re-running a
batch with a different interleaving reproduces every game's server randomness. Assignments come
from a named rotation policy (D-057). A batch that declares :class:`benchmark.controls.BatchControls`
is verified after every attempt against its sealed conditions and stopped on drift (D-058). The
raw CSV tables written at the end of a batch live in :mod:`benchmark.outputs`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
import itertools
import math
from pathlib import Path
import random
from dataclasses import asdict, dataclass
from typing import Any

from app.config import redact_secrets
from app.export import write_json_atomic
from benchmark.controls import BatchControls, ExpectedAttempt
from benchmark.outputs import write_tables

SEED_POLICY = "base_seed + assignment_index * 1000 + repetition_index"
# I-084: the recorded policy is collision-free only while ``repetition_index < 1000`` — at 1000
# it meets the next assignment's repetition 0 (methodology §8 "no accidental reuse ambiguity").
# The range is bounded rather than the policy versioned, so every existing schedule keeps its
# identity (D-073).
MAX_REPETITIONS = 999
# benchmark-methodology §21: the failure categories a batch reports. Anything the executor did
# not classify itself is a failure of the batch/server side, never silently a "completed" game.
FAILURE_CATEGORIES = (
    "agent_stall",
    "provider_timeout",
    "invalid_tool_loop",
    "umpire_failure",
    "server_failure",
    "game_incompletion",
)


class ScheduleError(ValueError):
    """The requested batch cannot be scheduled from the given models and Actors."""


@dataclass(frozen=True)
class Assignment:
    """One model/role assignment: which model drives which Actor Seat."""

    assignment_id: str
    assignment_index: int
    models: dict[str, str]


@dataclass(frozen=True)
class ScheduledAttempt:
    """One planned game. ``position`` is the execution order; the rest is stable identity."""

    experiment_id: str
    scheduling_seed: int
    position: int
    assignment_id: str
    assignment_index: int
    models: dict[str, str]
    repetition_index: int
    seed: int
    rotation_policy: str = "single"


@dataclass(frozen=True)
class Schedule:
    experiment_id: str
    base_seed: int
    scheduling_seed: int
    repetitions: int
    assignments: tuple[Assignment, ...]
    attempts: tuple[ScheduledAttempt, ...]
    rotation_policy: str = "single"

    def counts(self) -> dict[str, int]:
        """The three numbers TRD §30 requires to be reported before execution."""
        return {
            "assignment_count": len(self.assignments),
            "repetitions_per_assignment": self.repetitions,
            "total_games": len(self.attempts),
        }


# T-055 (WBS 5.3, methodology §6): the three named assignment policies. ``single`` and
# ``permutations`` are exactly what T-054 generated; ``cyclic`` is the explicit balancing rule for
# unequal counts. Each policy has its own canonical order, so no index or seed of an existing
# policy ever moves (D-057).
ROTATION_POLICIES = ("single", "permutations", "cyclic")
CYCLIC_FORMULA = "Actor[j] -> models[(k + j) % M] for k in 0..M-1"
CYCLIC_GUARANTEE = (
    "across the complete cyclic assignment set, every listed model occupies every Actor Seat "
    "exactly once"
)
CYCLIC_NOT_GUARANTEED = (
    "opponent/composition balance: which other models a model faces is not balanced; when M > N "
    "an assignment uses only N models, when N > M a model may hold several Seats at once"
)


def resolve_rotation(*, rotate: bool = False, rotation: str | None = None) -> str:
    """Map the compatible ``--rotate`` flag and the explicit ``--rotation`` onto one policy name."""
    if rotation is not None and rotation not in ROTATION_POLICIES:
        raise ScheduleError(
            f"unknown rotation policy {rotation!r}; expected one of {', '.join(ROTATION_POLICIES)}"
        )
    if rotate and rotation not in (None, "permutations"):
        raise ScheduleError(f"--rotate means --rotation permutations, not {rotation!r}")
    if rotate:
        return "permutations"
    return rotation or "single"


def describe_rotation(policy: str, *, actor_ids: list[str], models: list[str]) -> dict[str, Any]:
    """The persisted rotation record: name, parameters and the balance claim it may make (§6)."""
    record: dict[str, Any] = {
        "policy": policy,
        "model_count": len(models),
        "seat_count": len(actor_ids),
        "models": list(models),
        "seats": list(actor_ids),
    }
    if policy == "single":
        record.update({
            "assignment_count": 1,
            "formula": (
                "Actor[j] -> models[0]" if len(models) == 1 else "Actor[j] -> models[j]"
            ),
            "balance_guarantee": "none: one positional assignment",
            "not_guaranteed": "seat balance; opponent/composition balance",
        })
    elif policy == "permutations":
        record.update({
            "assignment_count": math.factorial(len(models)),
            "formula": "itertools.permutations(models), in that order",
            "balance_guarantee": (
                "every model occupies every Seat equally often and faces every ordered "
                "opponent composition equally often (all N! permutations)"
            ),
            "not_guaranteed": "nothing beyond the permutation set",
        })
    else:
        record.update({
            "assignment_count": len(models),
            "formula": CYCLIC_FORMULA,
            "balance_guarantee": CYCLIC_GUARANTEE,
            "not_guaranteed": CYCLIC_NOT_GUARANTEED,
        })
    return record


def build_assignments(
    actor_ids: list[str],
    models: list[str],
    *,
    rotate: bool = False,
    rotation: str | None = None,
) -> tuple[Assignment, ...]:
    """Generate the canonical assignment list (WBS 5.2.2, 5.3; TRD §30; methodology §6).

    ``single``: one model drives every Seat, or N models map positionally onto the N Actors in
    turn order. ``permutations`` (the T-054 ``rotate`` behaviour): every permutation of N models
    over N Seats, in ``itertools`` order. ``cyclic`` (T-055): for M models and N Seats, assignment
    ``k`` gives Seat ``j`` the model ``models[(k + j) % M]``, ``k = 0..M-1`` — every model in
    every Seat exactly once across the set, nothing claimed about opponent composition.
    ``assignment_index`` is the position in the policy's canonical order, never a scheduling
    artefact, and existing policies keep their T-054 indices (D-056, D-057).
    """
    policy = resolve_rotation(rotate=rotate, rotation=rotation)
    if not actor_ids or not models:
        raise ScheduleError("a schedule needs at least one Actor and one model")
    if policy == "permutations":
        if len(models) != len(actor_ids):
            raise ScheduleError(
                f"--rotate needs one model per Actor ({len(actor_ids)}), got {len(models)}"
            )
        rosters = [list(p) for p in itertools.permutations(models)]
    elif policy == "cyclic":
        count = len(models)
        rosters = [
            [models[(k + j) % count] for j in range(len(actor_ids))] for k in range(count)
        ]
    elif len(models) == 1:
        rosters = [models * len(actor_ids)]
    elif len(models) == len(actor_ids):
        rosters = [list(models)]
    else:
        raise ScheduleError(
            f"--models must name one model or exactly one per Actor ({len(actor_ids)}), "
            f"got {len(models)}; use --rotation cyclic for unequal counts"
        )
    return tuple(
        Assignment(
            assignment_id=f"assignment-{index + 1:02d}",
            assignment_index=index,
            models=dict(zip(actor_ids, roster)),
        )
        for index, roster in enumerate(rosters)
    )


def game_seed(base_seed: int, assignment_index: int, repetition_index: int) -> int:
    """The recorded seed policy (methodology §8): identity in, seed out; no scheduling input."""
    return base_seed + assignment_index * 1000 + repetition_index


def build_schedule(
    *,
    actor_ids: list[str],
    models: list[str],
    rotate: bool = False,
    repetitions: int,
    base_seed: int,
    scheduling_seed: int,
    experiment_id: str,
    rotation: str | None = None,
) -> Schedule:
    """Plan every attempt of a batch before execution (TRD §30, methodology §7–§8)."""
    if repetitions < 1:
        raise ScheduleError("repetitions must be at least 1")
    if repetitions > MAX_REPETITIONS:
        raise ScheduleError(
            f"repetitions must be at most {MAX_REPETITIONS}: the recorded seed policy "
            f"({SEED_POLICY}) reuses seeds across assignments beyond that"
        )
    policy = resolve_rotation(rotate=rotate, rotation=rotation)
    assignments = build_assignments(actor_ids, models, rotation=policy)
    planned = [
        (assignment, repetition)
        for assignment in assignments
        for repetition in range(repetitions)
    ]
    # Deterministic interleaving from the recorded scheduling seed, so the repetitions of one
    # assignment are not executed back to back under one provider-load window.
    random.Random(scheduling_seed).shuffle(planned)
    attempts = tuple(
        ScheduledAttempt(
            experiment_id=experiment_id,
            scheduling_seed=scheduling_seed,
            position=position,
            assignment_id=assignment.assignment_id,
            assignment_index=assignment.assignment_index,
            models=dict(assignment.models),
            repetition_index=repetition,
            seed=game_seed(base_seed, assignment.assignment_index, repetition),
            rotation_policy=policy,
        )
        for position, (assignment, repetition) in enumerate(planned)
    )
    return Schedule(
        experiment_id=experiment_id,
        base_seed=base_seed,
        scheduling_seed=scheduling_seed,
        repetitions=repetitions,
        assignments=assignments,
        attempts=attempts,
        rotation_policy=policy,
    )


def batch_benchmark_section(
    scheduled: Schedule | ScheduledAttempt, *, benchmark_mode: str | None
) -> dict[str, Any]:
    """The manifest's ``benchmark`` section a batch supplies (T-054, T-057).

    From a :class:`Schedule` it is the batch-level part every attempt shares — what the batch
    declares as its expected condition before any game (I-078). From a
    :class:`ScheduledAttempt` it adds that attempt's assignment identity.
    """
    section: dict[str, Any] = {
        "mode": "batch",
        "experiment_id": scheduled.experiment_id,
        "scheduling_seed": scheduled.scheduling_seed,
        "seed_policy": SEED_POLICY,
        "rotation_policy": scheduled.rotation_policy,
        "benchmark_mode": benchmark_mode,
    }
    if isinstance(scheduled, ScheduledAttempt):
        section.update({
            "assignment_id": scheduled.assignment_id,
            "assignment_index": scheduled.assignment_index,
            "assignment": dict(scheduled.models),
            "repetition_index": scheduled.repetition_index,
        })
    return section


class AttemptFailed(Exception):
    """One scheduled attempt did not reach a completed game (methodology §21).

    Raised by an attempt executor with what it knows: the §21 ``category``, a one-line
    ``reason`` (exception type and message, never a traceback), the partial administrative export
    if a game existed, the turn reached and the Actor affected. The batch records it and moves on.
    """

    def __init__(
        self,
        *,
        category: str,
        reason: str,
        export: dict[str, Any] | None = None,
        turn_reached: int | None = None,
        actor_affected: str | None = None,
    ) -> None:
        if category not in FAILURE_CATEGORIES:
            raise ValueError(f"unknown failure category {category!r}")
        super().__init__(reason)
        self.category = category
        self.reason = reason
        self.export = export
        self.turn_reached = turn_reached
        self.actor_affected = actor_affected


@dataclass(frozen=True)
class AttemptRecord:
    """What happened to one scheduled attempt: identity plus outcome (methodology §21)."""

    experiment_id: str
    position: int
    assignment_id: str
    assignment_index: int
    models: dict[str, str]
    repetition_index: int
    seed: int
    directory: str
    game_id: str | None
    completed: bool
    failure_category: str | None
    failure_reason: str | None
    turn_reached: int | None
    actor_affected: str | None
    started_at: str
    finished_at: str
    # T-055/T-057: the policy and interpretation every row of the output tables must carry.
    rotation_policy: str = "single"
    benchmark_mode: str | None = None
    # T-056: post-attempt verification against the sealed batch condition. ``None`` when the
    # batch declared no controls or the attempt left no manifest to compare.
    umpire_condition_match: bool | None = None
    condition_differences: list[str] | None = None
    # ``completed`` | ``failed`` | ``not_run`` — the last only after an integrity stop (T-056).
    status: str = "completed"


@dataclass(frozen=True)
class BatchResult:
    output_dir: Path
    schedule: Schedule
    attempts: tuple[AttemptRecord, ...]

    def counts(self) -> dict[str, int]:
        return _counts(self.attempts)


def _counts(records: list[AttemptRecord] | tuple[AttemptRecord, ...]) -> dict[str, int]:
    """Planned denominator, completed, failed and (after an integrity stop) not-run attempts."""
    by_status = {status: 0 for status in ("completed", "failed", "not_run")}
    for record in records:
        by_status[record.status] += 1
    return {
        "total_games": len(records),
        "completed_games": by_status["completed"],
        "failed_games": by_status["failed"],
        "not_run_games": by_status["not_run"],
    }


class BatchIntegrityError(RuntimeError):
    """A sealed batch condition drifted (T-056, D-058): the batch stopped; ``result`` is retained.

    Not a game failure. The attempts already executed keep their evidence, every still-scheduled
    attempt is recorded as ``not_run`` so the planned denominator stays visible, and the caller
    exits non-zero. A different Umpire condition is a different experiment and needs its own batch
    (methodology §9, §10).
    """

    def __init__(self, message: str, result: BatchResult) -> None:
        super().__init__(message)
        self.result = result


def _empty_export() -> dict[str, Any]:
    """The minimal failed-attempt export for an attempt whose game was never created.

    Shaped like ``app.export.build_game_export`` output with nothing in it — ``game`` and
    ``run_manifest`` are ``None`` rather than fabricated — so the output tables stay a pure
    projection over one ``(attempt, export)`` pair per scheduled attempt.
    """
    return {
        "export_format_version": None,
        "game": None,
        "actors": [],
        "messages": [],
        "arguments": [],
        "cons": [],
        "adjudications": [],
        "effects": [],
        "events": [],
        "agent_runs": [],
        "agent_errors": [],
        "resource_access": [],
        "run_manifest": None,
        "run_summary": None,
    }


def _attempt_directory(attempt: ScheduledAttempt) -> str:
    return f"{attempt.position:03d}-{attempt.assignment_id}-rep{attempt.repetition_index:02d}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def run_batch(
    schedule: Schedule,
    *,
    execute: Callable[[ScheduledAttempt], dict[str, Any]],
    output_dir: Path,
    experiment: dict[str, Any],
    secret_values: list[str] | tuple[str, ...] = (),
    controls: BatchControls | None = None,
) -> BatchResult:
    """Execute every scheduled attempt serially, retaining failures (WBS 5.2.4, §21).

    ``execute`` runs one attempt and returns its completed administrative export, or raises
    :class:`AttemptFailed`. Any other exception is a server-side failure of that attempt. Either
    way the attempt is recorded under ``games/<attempt>/`` with ``attempt.json`` and an
    ``export.json`` (partial, or the minimal stub when no game existed), ``batch.json`` is
    rewritten after every attempt, and the batch continues. The denominator is the schedule.

    With ``controls`` (T-056/T-057/I-078) the declared mode, roster, interpretation, sealed
    Umpire condition (and, under ``bounded_effects_v1``, the sealed consequence resolver, D-092)
    and the complete expected condition go into ``batch.json`` before the first game; every
    attempt's manifest — the first included — is verified against the Umpire and consequence seals,
    the expected condition (the whole §5.1 set in ``model`` mode, the common non-player subset
    in ``agent`` mode) and its own scheduled seed and assignment; a completed attempt without a
    manifest is itself a failure. Drift stops the batch with :class:`BatchIntegrityError` after
    the remaining attempts are recorded as ``not_run``.
    """
    output_dir = Path(output_dir)
    require_empty_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    records: list[AttemptRecord] = []
    integrity = _Integrity(
        consequence_declared=controls is not None and controls.consequence_condition is not None
    )
    if controls is not None:
        experiment = {**experiment, **controls.record()}
    _write_batch_record(output_dir, schedule, experiment, records, integrity)
    for attempt in schedule.attempts:
        directory = _attempt_directory(attempt)
        started_at = _now()
        export: dict[str, Any] | None
        outcome: dict[str, Any]
        try:
            export = execute(attempt)
            outcome = {
                "completed": True, "failure_category": None, "failure_reason": None,
                "turn_reached": _game_field(export, "turn"), "actor_affected": None,
            }
        except AttemptFailed as failure:
            export = failure.export
            outcome = {
                "completed": False,
                "failure_category": failure.category,
                "failure_reason": redact_secrets(failure.reason, secret_values),
                "turn_reached": failure.turn_reached,
                "actor_affected": failure.actor_affected,
            }
        except Exception as exc:  # noqa: BLE001 - one attempt must never abort the batch
            export = None
            outcome = {
                "completed": False,
                "failure_category": "server_failure",
                "failure_reason": redact_secrets(f"{type(exc).__name__}: {exc}", secret_values),
                "turn_reached": None,
                "actor_affected": None,
            }
        if export is None:
            export = _empty_export()
        verification = _verify_attempt(controls, attempt, export, integrity)
        record = AttemptRecord(
            experiment_id=attempt.experiment_id,
            position=attempt.position,
            assignment_id=attempt.assignment_id,
            assignment_index=attempt.assignment_index,
            models=dict(attempt.models),
            repetition_index=attempt.repetition_index,
            seed=attempt.seed,
            directory=directory,
            game_id=_game_field(export, "id"),
            started_at=started_at,
            finished_at=_now(),
            rotation_policy=attempt.rotation_policy,
            benchmark_mode=experiment.get("benchmark_mode"),
            **outcome,
            **verification,
            status="completed" if outcome["completed"] else "failed",
        )
        _write_attempt(output_dir, record, export)
        records.append(record)
        _write_batch_record(output_dir, schedule, experiment, records, integrity)
        if not integrity.ok:
            # D-058: a control violation never becomes ordinary data. Stop here, keep the
            # denominator, and let the caller exit non-zero.
            reason = (
                f"not_run: batch stopped after experiment-integrity failure at attempt "
                f"{directory} ({', '.join(f['kind'] for f in integrity.failures)})"
            )
            for remaining in schedule.attempts[attempt.position + 1:]:
                skipped = _not_run_record(remaining, experiment, reason)
                _write_attempt(output_dir, skipped, _empty_export())
                records.append(skipped)
            _write_batch_record(output_dir, schedule, experiment, records, integrity)
            write_tables(output_dir)
            result = BatchResult(output_dir=output_dir, schedule=schedule, attempts=tuple(records))
            raise BatchIntegrityError(
                f"experiment integrity failure: {integrity.describe()}", result
            )
    write_tables(output_dir)
    return BatchResult(output_dir=output_dir, schedule=schedule, attempts=tuple(records))


def require_empty_output_dir(output_dir: Path) -> None:
    """A batch owns its evidence directory (I-078): it must be absent or empty.

    ``outputs.load_attempts`` projects every ``games/*`` pair it finds, so anything already
    there — another experiment's attempts, a stale ``batch.json``, a stray file — would enter
    this batch's tables. There is no resume protocol; nothing pre-existing is admitted.
    """
    output_dir = Path(output_dir)
    if output_dir.exists() and (not output_dir.is_dir() or any(output_dir.iterdir())):
        raise ScheduleError(
            f"output directory {output_dir} must be absent or empty; a batch never writes into "
            "a directory that already has contents"
        )


def _write_attempt(output_dir: Path, record: AttemptRecord, export: dict[str, Any]) -> None:
    write_json_atomic(output_dir / "games" / record.directory / "export.json", export)
    write_json_atomic(output_dir / "games" / record.directory / "attempt.json", asdict(record))


def _not_run_record(attempt: ScheduledAttempt, experiment: dict[str, Any], reason: str) -> AttemptRecord:
    now = _now()
    return AttemptRecord(
        experiment_id=attempt.experiment_id,
        position=attempt.position,
        assignment_id=attempt.assignment_id,
        assignment_index=attempt.assignment_index,
        models=dict(attempt.models),
        repetition_index=attempt.repetition_index,
        seed=attempt.seed,
        directory=_attempt_directory(attempt),
        game_id=None,
        completed=False,
        failure_category=None,
        failure_reason=reason,
        turn_reached=None,
        actor_affected=None,
        started_at=now,
        finished_at=now,
        rotation_policy=attempt.rotation_policy,
        benchmark_mode=experiment.get("benchmark_mode"),
        status="not_run",
    )


class _Integrity:
    """Experiment-integrity state of one batch (T-056, I-078): declared conditions held or not."""

    def __init__(self, *, consequence_declared: bool = False) -> None:
        self.umpire_fixed = True
        self.consequence_declared = consequence_declared
        self.consequence_fixed = True
        self.condition_fixed = True
        self.attempts_as_scheduled = True
        self.failures: list[dict[str, Any]] = []

    @property
    def ok(self) -> bool:
        return (
            self.umpire_fixed and self.consequence_fixed and self.condition_fixed
            and self.attempts_as_scheduled
        )

    def record(self) -> dict[str, Any]:
        # D-092: ``consequence_fixed`` is recorded for a batch that declared a resolver, or when a
        # ``none`` batch met one; a clean ``none`` batch keeps the released record.
        consequence = (
            {"consequence_fixed": self.consequence_fixed}
            if self.consequence_declared or not self.consequence_fixed else {}
        )
        return {
            "ok": self.ok,
            "umpire_fixed": self.umpire_fixed,
            **consequence,
            "condition_fixed": self.condition_fixed,
            "attempts_as_scheduled": self.attempts_as_scheduled,
            "failures": list(self.failures),
        }

    def describe(self) -> str:
        return "; ".join(
            f"{failure['kind']} drifted at {', '.join(failure['differences'])}"
            for failure in self.failures
        )


def _verify_attempt(
    controls: BatchControls | None,
    attempt: ScheduledAttempt,
    export: dict[str, Any],
    integrity: _Integrity,
) -> dict[str, Any]:
    """Compare an attempt's authoritative manifest with what the batch declared (T-056, I-078).

    Every expectation existed before the first game: the sealed Umpire condition, the sealed
    consequence condition (D-092), the expected batch condition and this attempt's scheduled seed
    and assignment. A completed attempt whose
    export carries no manifest cannot be verified and is reported as such; a failed attempt
    that never produced a manifest is a §21 game failure, not an integrity failure.
    """
    if controls is None:
        return {"umpire_condition_match": None, "condition_differences": None}
    manifest = export.get("run_manifest") if isinstance(export, dict) else None
    if not isinstance(manifest, dict):
        game = export.get("game") if isinstance(export, dict) else None
        if isinstance(game, dict) and game.get("status") == "complete":
            integrity.attempts_as_scheduled = False
            integrity.failures.append({
                "kind": "missing_manifest", "game_id": game.get("id"),
                "differences": ["run_manifest"],
            })
            return {"umpire_condition_match": None, "condition_differences": ["run_manifest"]}
        return {"umpire_condition_match": None, "condition_differences": None}
    umpire_differences = controls.verify_umpire(manifest)
    if umpire_differences:
        integrity.umpire_fixed = False
        integrity.failures.append({
            "kind": "umpire_condition", "game_id": manifest.get("game_id"),
            "differences": umpire_differences,
        })
    consequence_differences = controls.verify_consequence(manifest)
    if consequence_differences:
        integrity.consequence_fixed = False
        integrity.failures.append({
            "kind": "consequence_condition", "game_id": manifest.get("game_id"),
            "differences": consequence_differences,
        })
    condition_differences = controls.verify_condition(manifest)
    if condition_differences:
        integrity.condition_fixed = False
        integrity.failures.append({
            "kind": "expected_condition", "game_id": manifest.get("game_id"),
            "differences": condition_differences,
        })
    attempt_differences = ExpectedAttempt.of(attempt).verify(manifest)
    if attempt_differences:
        integrity.attempts_as_scheduled = False
        integrity.failures.append({
            "kind": "expected_attempt", "game_id": manifest.get("game_id"),
            "differences": attempt_differences,
        })
    return {
        "umpire_condition_match": not umpire_differences,
        "condition_differences": (
            umpire_differences + consequence_differences + condition_differences
            + attempt_differences
        ),
    }


def _game_field(export: dict[str, Any] | None, key: str) -> Any:
    game = (export or {}).get("game")
    return game.get(key) if isinstance(game, dict) else None


def _write_batch_record(
    output_dir: Path,
    schedule: Schedule,
    experiment: dict[str, Any],
    records: list[AttemptRecord],
    integrity: _Integrity | None = None,
) -> None:
    """``batch.json``: the experiment record (§4) plus the schedule and every outcome so far.

    Two rates, named for their denominators (I-084). ``planned_completion_rate`` (and its
    §21/§24 alias ``completion_rate``) divides by the planned attempts — failed and ``not_run``
    games stay in the denominator (methodology §21, D-068). ``execution_success_rate`` divides
    by the attempts actually executed and is ``None`` before the first one.
    """
    counts = _counts(records)
    executed = counts["completed_games"] + counts["failed_games"]
    planned = len(schedule.attempts)
    planned_completion_rate = (counts["completed_games"] / planned) if planned else None
    integrity = integrity or _Integrity()
    payload = {
        "integrity": integrity.record(),
        **experiment,
        "experiment_id": schedule.experiment_id,
        "base_seed": schedule.base_seed,
        "scheduling_seed": schedule.scheduling_seed,
        "seed_policy": SEED_POLICY,
        "rotation_policy": schedule.rotation_policy,
        "rotation": describe_rotation(
            schedule.rotation_policy,
            actor_ids=list(schedule.assignments[0].models),
            models=list(experiment.get("models") or _models_of(schedule)),
        ),
        "schedule": {
            **schedule.counts(),
            "assignments": [asdict(assignment) for assignment in schedule.assignments],
            "attempts": [asdict(attempt) for attempt in schedule.attempts],
        },
        "attempts": [asdict(record) for record in records],
        "executed_games": executed,
        "completed_games": counts["completed_games"],
        "failed_games": counts["failed_games"],
        "not_run_games": counts["not_run_games"],
        "completion_rate": planned_completion_rate,
        "planned_completion_rate": planned_completion_rate,
        "execution_success_rate": (counts["completed_games"] / executed) if executed else None,
    }
    write_json_atomic(output_dir / "batch.json", payload)


def _models_of(schedule: Schedule) -> list[str]:
    """The distinct compared roster in first-seen canonical order (methodology §5.1)."""
    seen: list[str] = []
    for assignment in schedule.assignments:
        for model in assignment.models.values():
            if model not in seen:
                seen.append(model)
    return seen


def classify_failure(exc: BaseException, export: dict[str, Any] | None) -> str:
    """Map a failed autonomous run onto one benchmark-methodology §21 category.

    Uses what the export says before what the exception says: a paused game is the server's
    Umpire-outage pause (D-021); a stall whose last recorded player failure was transient or a
    guardrail ceiling is reported as that, not as a bare stall.
    """
    from agents.autonomous import AutonomousGameError, AutonomousGameStalled

    if _game_field(export, "status") == "paused":
        return "umpire_failure"
    last_error = ((export or {}).get("agent_errors") or [None])[-1]
    last_category = (
        ((last_error.get("provider_metadata") or {}).get("category"))
        if isinstance(last_error, dict) else None
    )
    if isinstance(exc, AutonomousGameStalled):
        if last_category == "transient":
            return "provider_timeout"
        if last_category == "guardrail":
            return "invalid_tool_loop"
        return "agent_stall"
    if isinstance(exc, AutonomousGameError):
        return "server_failure" if "admin " in str(exc) else "game_incompletion"
    if export is None or _game_field(export, "id") is None:
        return "server_failure"
    return "game_incompletion"


def autonomous_executor(
    client: Any,
    *,
    admin_token: str,
    scenario: str | Path,
    player_factory: Callable[[Any, str, str, str], Any],
    run_configuration: Mapping[str, Any] | None = None,
    analysis_plan: Any = None,
    benchmark_mode: str | None = None,
    **runner_kwargs: Any,
) -> Callable[[ScheduledAttempt], dict[str, Any]]:
    """Build the per-attempt executor that drives one scheduled game to completion.

    ``player_factory(tools, actor_id, actor_name, model)`` is the ``amaw run`` factory with the
    attempt's assigned model added. Each attempt gets its own :class:`AutonomousGameRunner` —
    the scheduled seed (D-009: the batch seed overrides the manifest seed), the assignment
    identity in the manifest's ``benchmark`` section, and the batch-wide analysis plan. On any
    failure the partial administrative export is pulled through the admin API and raised inside
    :class:`AttemptFailed` so the batch retains it (testing.md §24).
    """
    from agents.autonomous import AutonomousGameRunner

    # T-056 / D-058: the batch takes exactly one Umpire configuration — the application's — and
    # no per-attempt override may ride in on the benchmark section.
    benchmark_overrides = (run_configuration or {}).get("benchmark") or {}
    if any(key.startswith("umpire") for key in benchmark_overrides):
        raise ScheduleError(
            "a benchmark batch runs under one Umpire condition; per-attempt Umpire overrides "
            f"in run_configuration['benchmark'] are refused: {sorted(benchmark_overrides)}"
        )
    # I-114 / D-092: likewise one consequence resolver, sealed and verified like the Umpire.
    if any(key.startswith("consequence") for key in benchmark_overrides):
        raise ScheduleError(
            "a benchmark batch runs under one consequence resolver; per-attempt consequence "
            f"overrides in run_configuration['benchmark'] are refused: {sorted(benchmark_overrides)}"
        )

    def execute(attempt: ScheduledAttempt) -> dict[str, Any]:
        def factory(tools: Any, actor_id: str, actor_name: str) -> Any:
            return player_factory(tools, actor_id, actor_name, attempt.models[actor_id])

        configuration = dict(run_configuration or {})
        configuration["benchmark"] = {
            **(configuration.get("benchmark") or {}),
            **batch_benchmark_section(attempt, benchmark_mode=benchmark_mode),
        }
        runner = AutonomousGameRunner(
            client,
            admin_token=admin_token,
            scenario=scenario,
            player_factory=factory,
            seed=attempt.seed,
            run_configuration=configuration,
            analysis_plan=analysis_plan,
            **runner_kwargs,
        )
        try:
            return runner.run().export
        except Exception as exc:  # noqa: BLE001 - classified and retained, never swallowed
            export = _partial_export(client, admin_token, runner.last_game_id)
            raise AttemptFailed(
                category=classify_failure(exc, export),
                reason=f"{type(exc).__name__}: {exc}",
                export=export,
                turn_reached=_game_field(export, "turn"),
                actor_affected=_actor_affected(export),
            ) from exc

    return execute


def _partial_export(client: Any, admin_token: str, game_id: str | None) -> dict[str, Any] | None:
    """The authoritative export of a game that did not complete, or ``None`` without a game."""
    if game_id is None:
        return None
    response = client.request(
        "GET",
        f"/admin/games/{game_id}/export",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    if not response.is_success:
        return None
    body = response.json()
    return body if isinstance(body, dict) else None


def _actor_affected(export: dict[str, Any] | None) -> str | None:
    errors = (export or {}).get("agent_errors") or []
    last = errors[-1] if errors else None
    if isinstance(last, dict) and last.get("actor_id"):
        return str(last["actor_id"])
    return _game_field(export, "active_actor_id")
