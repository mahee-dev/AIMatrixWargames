"""The adjudication regression corpus: hard cases, their expected outcomes, and the scoring.

A corpus case is one real or authored adjudication: the complete context the server hands the
Umpire (agents.md §21), plus a key that says, claim by claim, what a correct proposal does with
it under the ordered gates of agents.md §23. The same corpus measures the released single Umpire
(T-067) and any candidate adjudicator (T-069), so it is versioned and hash-locked: a case, the
rubric or the thresholds cannot change after results are seen without the lock failing.

Scoring is split on purpose (D-110). What can be checked from structure is checked here: whether
a call failed, whether the proposal passes the server's own validation, and whether each
supersession target is one the selected branch may resolve. Whether a free-text Effect asserts an
inadmissible claim is a judgment no mechanical check can make (D-089), so a human rater records,
per claim, whether it appears in each branch, and the violations are derived from that record.

The corpus measures model reliability. It is not a semantic guarantee, and it lives outside
``app/`` because the engine never depends on an evaluation.
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import random
import time
import uuid
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import FIRST_EXCEPTION, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

from agents.umpire import RULES_AWARE_UMPIRE_PROMPT_VERSIONS, UMPIRE_PROMPT_VERSION, UMPIRE_PROMPT_VERSIONS
from app.game_errors import InvalidAdjudication
from app.game_service import _validate_proposal
from app.models import AdjudicationMode
from app.umpire import AdjudicationContext, AdjudicationProposal, UmpireAdjudication

CORPUS_V1_DIR = Path(__file__).resolve().parent.parent / "corpora" / "adjudication" / "v1"
CORPUS_V2_DIR = CORPUS_V1_DIR.parent / "v2"
# v2 cases carry a split. Only the held-out split was drawn after the rule was fixed and before
# the prompt judged on v2 was written, so only it is free of tuning (I-157).
SPLITS = ("development", "held_out")
LOCK_FILE = "corpus.lock"
MANIFEST_FILE = "corpus.yaml"
RATING_SHEET_FIELDS = ("case_id", "repetition", "item", "in_success", "in_failure", "quality", "note")
# The blinded sheet names a proposal only by an opaque id: no condition, prompt or repetition.
BLINDED_SHEET_FIELDS = ("proposal_id", "case_id", "item", "in_success", "in_failure", "quality", "note")


class CorpusError(ValueError):
    """The corpus is malformed, or its content no longer matches its lock."""


class Gate(StrEnum):
    """The agents.md §23 gate that decides a claim. Gate 1 is split into its two parts."""

    authority = "authority"
    causation = "causation"
    prerequisite = "prerequisite"
    deterministic = "deterministic"
    probability = "probability"


class Treatment(StrEnum):
    """What a correct proposal does with a claim (agents.md §22–§24).

    ``establish``: admissible and certain — it appears in every branch the server could select.
    ``uncertain``: admissible residual causal uncertainty — it may appear in the success branch
    of a probabilistic proposal, but not in every selectable branch.
    ``omit``: inadmissible — it appears in no branch; probability never rescues it.
    ``optional``: admissible either way; not scored.
    """

    establish = "establish"
    uncertain = "uncertain"
    omit = "omit"
    optional = "optional"


# An omitted claim that appears anyway is scored by the gate that makes it inadmissible: missing
# authority is an unsupported commitment; a missing prerequisite, or a scenario rule or
# derivation whose premise is absent, is a waived prerequisite; absent causal support is causal
# overreach (agents.md §23).
_OMIT_MEASURE = {
    Gate.authority: "unsupported_commitment",
    Gate.prerequisite: "waived_prerequisite",
    Gate.deterministic: "waived_prerequisite",
    Gate.causation: "causal_overreach",
}
SEMANTIC_MEASURES = (
    "unsupported_commitment",
    "waived_prerequisite",
    "causal_overreach",
    "self_binding_lost",
)


@dataclass(frozen=True)
class Claim:
    id: str
    text: str
    treatment: Treatment
    gate: Gate | None
    note: str = ""


@dataclass(frozen=True)
class Case:
    id: str
    title: str
    source: dict[str, Any]
    prompt_version: str
    focus: tuple[str, ...]
    context: AdjudicationContext
    claims: tuple[Claim, ...]
    allowed_supersession: dict[str, frozenset[str]]
    prohibited_supersession: dict[str, str]
    expected_modes: tuple[AdjudicationMode, ...]
    rater_notes: str = ""
    split: str | None = None

    def claim(self, claim_id: str) -> Claim:
        for claim in self.claims:
            if claim.id == claim_id:
                return claim
        raise KeyError(claim_id)


@dataclass(frozen=True)
class Corpus:
    version: str
    root: Path
    cases: tuple[Case, ...]
    lock_sha256: str

    def case(self, case_id: str) -> Case:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(case_id)


# --- loading and validation ---------------------------------------------------------------


def _context(raw: Mapping[str, Any]) -> AdjudicationContext:
    return AdjudicationContext(
        scenario_summary=raw["scenario_summary"],
        turn=raw["turn"],
        turn_length=raw.get("turn_length"),
        argument=dict(raw["argument"]),
        cons=[dict(c) for c in raw["cons"]],
        relevant_effects=[dict(e) for e in raw["relevant_effects"]],
        relevant_hidden_information=[dict(h) for h in raw.get("relevant_hidden_information", [])],
        owner_reply=raw.get("owner_reply"),
        scenario_rules=raw.get("scenario_rules"),
    )


def _case(raw: Mapping[str, Any]) -> Case:
    case_id = raw["id"]

    def fail(message: str) -> CorpusError:
        return CorpusError(f"case {case_id!r}: {message}")

    context = _context(raw["context"])
    effect_ids = {effect["id"] for effect in context.relevant_effects}

    claims: list[Claim] = []
    for item in raw["claims"]:
        treatment = Treatment(item["treatment"])
        gate = Gate(item["gate"]) if item.get("gate") else None
        if treatment is not Treatment.optional and gate is None:
            raise fail(f"claim {item['id']!r} names no agents.md §23 gate")
        if treatment is Treatment.omit and gate not in _OMIT_MEASURE:
            raise fail(f"omitted claim {item['id']!r} must fail gate 1, 2 or 3, not {gate}")
        claims.append(Claim(item["id"], item["text"], treatment, gate, item.get("note", "")))
    if len({c.id for c in claims}) != len(claims):
        raise fail("claim ids are not unique")
    if not claims:
        raise fail("has no claims")

    supersession = raw.get("supersession", {})
    allowed = {
        branch: frozenset(supersession.get("allowed", {}).get(branch, []))
        for branch in ("success", "failure")
    }
    prohibited = dict(supersession.get("prohibited", {}))
    for effect_id in set().union(*allowed.values(), prohibited):
        if effect_id not in effect_ids:
            raise fail(f"supersession target {effect_id} is not an active Effect in its context")
    if set().union(*allowed.values()) & prohibited.keys():
        raise fail("a supersession target is both allowed and prohibited")

    split = raw.get("split")
    if split is not None and split not in SPLITS:
        raise fail(f"unknown split {split!r}")

    prompt_version = raw["prompt_version"]
    if prompt_version not in UMPIRE_PROMPT_VERSIONS:
        raise fail(f"unknown Umpire prompt {prompt_version!r}")
    # The released CLI refuses these pairings (agents.md §21), so a case may not declare them.
    if context.scenario_rules and prompt_version not in RULES_AWARE_UMPIRE_PROMPT_VERSIONS:
        raise fail(f"declares scenario rules but a prompt without rules framing ({prompt_version})")
    if context.owner_reply is not None and prompt_version == UMPIRE_PROMPT_VERSION:
        raise fail(f"carries an owner reply but {prompt_version} cannot weigh one")

    return Case(
        id=case_id,
        title=raw["title"],
        source=dict(raw["source"]),
        prompt_version=prompt_version,
        focus=tuple(raw.get("focus", [])),
        context=context,
        claims=tuple(claims),
        allowed_supersession=allowed,
        prohibited_supersession=prohibited,
        expected_modes=tuple(AdjudicationMode(m) for m in raw["expected_modes"]),
        rater_notes=raw.get("rater_notes", ""),
        split=split,
    )


def locked_files(root: Path) -> list[str]:
    """Every file the lock covers, relative to ``root``: all content except the lock itself."""
    return sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*")
        if p.is_file() and p.name != LOCK_FILE and not p.name.startswith(".")
    )


def compute_lock(root: Path) -> dict[str, Any]:
    """The per-file SHA-256 of the corpus and one digest over all of them."""
    files = {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in locked_files(root)}
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":"))
    return {"corpus_sha256": hashlib.sha256(canonical.encode()).hexdigest(), "files": files}


def write_lock(root: Path) -> dict[str, Any]:
    lock = compute_lock(root)
    (root / LOCK_FILE).write_text(json.dumps(lock, indent=2, sort_keys=True) + "\n")
    return lock


def verify_lock(root: Path) -> str:
    """Return the corpus digest, or raise if any locked file was added, removed or edited."""
    path = root / LOCK_FILE
    if not path.is_file():
        raise CorpusError(f"{root} has no {LOCK_FILE}")
    recorded = json.loads(path.read_text())
    actual = compute_lock(root)
    if recorded != actual:
        changed = sorted(
            name
            for name in set(recorded["files"]) | set(actual["files"])
            if recorded["files"].get(name) != actual["files"].get(name)
        )
        raise CorpusError(f"corpus content does not match {LOCK_FILE}: {', '.join(changed)}")
    return str(actual["corpus_sha256"])


def load_corpus(root: Path = CORPUS_V1_DIR, *, check_lock: bool = True) -> Corpus:
    """Load and validate a corpus. The lock is checked unless the caller is re-authoring it."""
    root = Path(root)
    lock_sha256 = verify_lock(root) if check_lock else compute_lock(root)["corpus_sha256"]
    manifest = yaml.safe_load((root / MANIFEST_FILE).read_text())
    cases = tuple(
        _case(json.loads((root / "cases" / f"{case_id}.json").read_text()))
        for case_id in manifest["cases"]
    )
    for case_id, case in zip(manifest["cases"], cases):
        if case.id != case_id:
            raise CorpusError(f"cases/{case_id}.json declares id {case.id!r}")
    if len({c.id for c in cases}) != len(cases):
        raise CorpusError("case ids are not unique")
    if manifest.get("splits") and any(c.split is None for c in cases):
        raise CorpusError("a corpus with splits must give every case one")
    return Corpus(version=manifest["version"], root=root, cases=cases, lock_sha256=lock_sha256)


# --- mechanical scoring ---------------------------------------------------------------------


@dataclass(frozen=True)
class MechanicalScore:
    """What one adjudication call scores without a human (D-110)."""

    failure: str | None
    invalid_proposal: str | None
    mode: str | None
    mode_expected: bool | None
    invalid_supersession: tuple[dict[str, str], ...]
    branch_inconsistent: str | None = None

    @property
    def usable(self) -> bool:
        return self.failure is None and self.invalid_proposal is None


def score_mechanical(case: Case, result: UmpireAdjudication) -> MechanicalScore:
    """Score one call: failure, server validity, mode and supersession eligibility.

    A supersession target is invalid when the branch that lists it is not allowed to resolve it
    (agents.md §23: only a branch whose Action or new evidence resolves the earlier condition may
    supersede it, and each branch lists only its own targets). A target outside the context is
    invalid too; the server would refuse it.
    """
    proposal = result.proposal
    if proposal is None:
        return MechanicalScore(result.failure or "unavailable", None, None, None, ())
    inconsistent = branch_inconsistency(proposal)
    invalid: str | None = None
    try:
        _validate_proposal(proposal)
    except InvalidAdjudication as exc:
        invalid = str(exc)
    effect_ids = {e["id"] for e in case.context.relevant_effects}
    bad: list[dict[str, str]] = []
    for branch, targets in (
        ("success", proposal.success_supersedes_effect_ids),
        ("failure", proposal.failure_supersedes_effect_ids),
    ):
        for target in (str(t) for t in targets):
            if target not in effect_ids:
                reason = "not an active Effect in the context"
            elif target in case.prohibited_supersession:
                reason = case.prohibited_supersession[target]
            elif target not in case.allowed_supersession[branch]:
                reason = f"the {branch} branch does not resolve this Effect"
            else:
                continue
            bad.append({"branch": branch, "effect_id": target, "reason": reason})
    return MechanicalScore(
        failure=None,
        invalid_proposal=invalid,
        mode=proposal.mode.value,
        mode_expected=proposal.mode in case.expected_modes,
        invalid_supersession=tuple(bad),
        branch_inconsistent=inconsistent,
    )


def branch_inconsistency(proposal: AdjudicationProposal) -> str | None:
    """Name a mode/branch shape mismatch, or ``None``. Syntax only; meaning is never inspected.

    A probabilistic proposal lets the roll pick either branch, so each needs at least one Effect;
    an automatic proposal has one selectable branch, so the other must be empty (agents.md §24,
    §26). An unselectable branch with Effects, or an empty selectable one under a roll, usually
    means the adjudicator dropped state it meant to keep (I-157).
    """
    if proposal.mode is AdjudicationMode.probabilistic:
        empty = [b for b, e in (("success", proposal.success_effects), ("failure", proposal.failure_effects)) if not e]
        return f"probabilistic with an empty {' and '.join(empty)} branch" if empty else None
    if proposal.mode is AdjudicationMode.automatic_success and proposal.failure_effects:
        return "automatic_success with failure Effects"
    if proposal.mode is AdjudicationMode.automatic_failure and proposal.success_effects:
        return "automatic_failure with success Effects"
    return None


# --- semantic scoring from the human rating sheet -----------------------------------------------


def selectable_branches(mode: AdjudicationMode | str) -> tuple[str, ...]:
    """The branches the server could select for this mode (agents.md §24, §26)."""
    mode = AdjudicationMode(mode)
    if mode is AdjudicationMode.automatic_success:
        return ("success",)
    if mode is AdjudicationMode.automatic_failure:
        return ("failure",)
    return ("success", "failure")


def semantic_violations(
    case: Case, mode: AdjudicationMode | str, presence: Mapping[str, Mapping[str, bool]]
) -> dict[str, list[str]]:
    """Derive the semantic violations of one proposal from the rater's claim presence.

    ``presence[claim_id]`` is ``{"success": bool, "failure": bool}`` as the rater recorded it.
    An omitted claim that appears in any branch counts under its gate's measure. An established
    claim missing from a selectable branch is a lost self-binding act (agents.md §23, TEST-ADJ-008).
    An uncertain claim present in every selectable branch was made certain: causal overreach.
    """
    out: dict[str, list[str]] = {measure: [] for measure in SEMANTIC_MEASURES}
    branches = selectable_branches(mode)
    for claim in case.claims:
        if claim.treatment is Treatment.optional:
            continue
        if claim.id not in presence:
            raise CorpusError(f"case {case.id!r}: no rating for claim {claim.id!r}")
        seen = presence[claim.id]
        if claim.treatment is Treatment.omit and (seen["success"] or seen["failure"]):
            assert claim.gate is not None
            out[_OMIT_MEASURE[claim.gate]].append(claim.id)
        elif claim.treatment is Treatment.establish and not all(seen[b] for b in branches):
            out["self_binding_lost"].append(claim.id)
        elif claim.treatment is Treatment.uncertain and all(seen[b] for b in branches):
            out["causal_overreach"].append(claim.id)
    return out


def _flag(value: str, *, where: str) -> bool:
    value = value.strip().lower()
    if value in {"y", "yes", "1", "true"}:
        return True
    if value in {"n", "no", "0", "false"}:
        return False
    raise CorpusError(f"{where}: expected y or n, got {value!r}")


def _rate_row(entry: dict[str, Any], row: Mapping[str, str], *, where: str) -> None:
    if row["item"] == "quality":
        if not row["quality"].strip():
            raise CorpusError(f"{where}: quality is not rated")
        quality = int(row["quality"])
        if not 1 <= quality <= 5:
            raise CorpusError(f"{where}: quality must be 1-5")
        entry["quality"] = quality
    else:
        entry["presence"][row["item"]] = {
            "success": _flag(row["in_success"], where=where),
            "failure": _flag(row["in_failure"], where=where),
        }


def read_ratings(path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """Read a filled rating sheet into ``{(case_id, repetition): {"presence", "quality"}}``."""
    ratings: dict[tuple[str, int], dict[str, Any]] = {}
    with path.open(newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            key = (row["case_id"], int(row["repetition"]))
            entry = ratings.setdefault(key, {"presence": {}, "quality": None})
            _rate_row(entry, row, where=f"{path.name}:{line}")
    return ratings


def read_blinded_ratings(
    sheet_path: Path, key_path: Path
) -> dict[str, dict[tuple[str, int], dict[str, Any]]]:
    """Unblind a filled mixed sheet into ``{condition: {(case_id, repetition): rating}}``.

    Every row must name a proposal the key knows, under the case the key records for it, and
    every proposal in the key must carry a quality rating, so no proposal is silently dropped
    from either condition's summary.
    """
    proposals = json.loads(key_path.read_text())["proposals"]
    by_id: dict[str, dict[str, Any]] = {}
    with sheet_path.open(newline="") as handle:
        for line, row in enumerate(csv.DictReader(handle), start=2):
            where = f"{sheet_path.name}:{line}"
            known = proposals.get(row["proposal_id"])
            if known is None:
                raise CorpusError(f"{where}: {row['proposal_id']} is not in the blinding key")
            if known["case_id"] != row["case_id"]:
                raise CorpusError(f"{where}: {row['proposal_id']} belongs to {known['case_id']}")
            entry = by_id.setdefault(row["proposal_id"], {"presence": {}, "quality": None})
            _rate_row(entry, row, where=where)
    unrated = sorted(pid for pid in proposals if by_id.get(pid, {}).get("quality") is None)
    if unrated:
        raise CorpusError(f"{sheet_path.name}: no quality rating for {', '.join(unrated)}")
    out: dict[str, dict[tuple[str, int], dict[str, Any]]] = {}
    for pid, entry in by_id.items():
        known = proposals[pid]
        out.setdefault(known["condition"], {})[(known["case_id"], known["repetition"])] = entry
    return out


# --- the baseline harness -------------------------------------------------------------------


@dataclass
class CallRecord:
    """One adjudication call over one case: the proposal, its provenance and its mechanics."""

    condition: str
    case_id: str
    repetition: int
    prompt_version: str
    started_at: float
    wall_ms: int
    proposal: dict[str, Any] | None
    failure: str | None
    attempts: list[dict[str, Any]] = field(default_factory=list)
    mechanical: dict[str, Any] = field(default_factory=dict)

    def as_json(self) -> dict[str, Any]:
        return {
            "condition": self.condition,
            "case_id": self.case_id,
            "repetition": self.repetition,
            "prompt_version": self.prompt_version,
            "started_at": self.started_at,
            "wall_ms": self.wall_ms,
            "proposal": self.proposal,
            "failure": self.failure,
            "attempts": self.attempts,
            "mechanical": self.mechanical,
        }


def proposal_json(proposal: AdjudicationProposal) -> dict[str, Any]:
    return {
        "mode": proposal.mode.value,
        "probability": proposal.probability,
        "rationale": proposal.rationale,
        "success_effects": list(proposal.success_effects),
        "failure_effects": list(proposal.failure_effects),
        "success_supersedes_effect_ids": [str(i) for i in proposal.success_supersedes_effect_ids],
        "failure_supersedes_effect_ids": [str(i) for i in proposal.failure_supersedes_effect_ids],
    }


def _proposal_from_json(raw: Mapping[str, Any]) -> AdjudicationProposal:
    return AdjudicationProposal(
        mode=AdjudicationMode(raw["mode"]),
        rationale=raw["rationale"],
        probability=raw["probability"],
        success_effects=list(raw["success_effects"]),
        failure_effects=list(raw["failure_effects"]),
        success_supersedes_effect_ids=[uuid.UUID(i) for i in raw["success_supersedes_effect_ids"]],
        failure_supersedes_effect_ids=[uuid.UUID(i) for i in raw["failure_supersedes_effect_ids"]],
    )


class RecordingUmpire:
    """What the harness needs from an adjudicator: its fixed identity and one recorded call."""

    def provenance_configuration(self) -> dict[str, Any]: ...

    def adjudicate_recorded(self, context: AdjudicationContext) -> UmpireAdjudication: ...


CALLS_DIR = "calls"


def _call_file(case_id: str, repetition: int) -> str:
    return f"{case_id}__r{repetition}.json"


def _write_atomic(path: Path, text: str) -> None:
    """Write ``text`` so a reader sees the old file or the whole new one, never a torn one."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    with tmp.open("w") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _mechanical_json(score: MechanicalScore) -> dict[str, Any]:
    return {
        "failure": score.failure,
        "invalid_proposal": score.invalid_proposal,
        "mode": score.mode,
        "mode_expected": score.mode_expected,
        "invalid_supersession": list(score.invalid_supersession),
        "branch_inconsistent": score.branch_inconsistent,
    }


def run_corpus(
    corpus: Corpus,
    umpire_for: Callable[[str], RecordingUmpire],
    *,
    repetitions: int,
    condition: str,
    out_dir: Path,
    case_ids: Iterable[str] | None = None,
    prompt_for: Callable[[Case], str] | None = None,
    concurrency: int = 1,
) -> list[CallRecord]:
    """Run every case ``repetitions`` times and write the raw results and their provenance.

    ``prompt_for(case)`` names the prompt a case runs under; by default it is the prompt the
    released CLI resolves for the case's scenario (D-110), and a candidate condition pins one
    prompt for every case. ``umpire_for(prompt_version)`` builds the adjudicator for it. The
    provenance records each prompt's sealed adjudicator configuration beside the corpus digest,
    so a result is tied to the exact prompt, model, settings and cases that produced it.

    Each call has the identity ``(condition, case_id, repetition)`` and is written atomically to
    its own file under ``calls/`` as soon as it returns. Rerunning into the same directory makes
    only the calls not yet written, so an interrupted run resumes without a duplicate call; the
    rerun is refused unless its provenance is exactly the recorded one. Up to ``concurrency``
    calls are in flight at once; the server-owned roll is not involved, so order does not matter.
    ``calls.jsonl`` is rebuilt from the call files in identity order whenever the run stops.
    Returns the records made by this invocation.
    """
    if concurrency < 1:
        raise CorpusError("concurrency must be at least 1")
    wanted = None if case_ids is None else set(case_ids)
    selected = [c for c in corpus.cases if wanted is None or c.id in wanted]
    resolve = prompt_for or (lambda case: case.prompt_version)
    prompts = {case.id: resolve(case) for case in selected}
    umpires = {version: umpire_for(version) for version in sorted(set(prompts.values()))}
    provenance = {
        "condition": condition,
        "corpus_version": corpus.version,
        "corpus_sha256": corpus.lock_sha256,
        "repetitions": repetitions,
        "cases": [c.id for c in selected],
        "prompts": prompts,
        "adjudicators": {v: u.provenance_configuration() for v, u in umpires.items()},
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    calls_dir = out_dir / CALLS_DIR
    provenance_path = out_dir / "provenance.json"
    if provenance_path.exists():
        recorded = json.loads(provenance_path.read_text())
        if recorded != provenance:
            raise CorpusError(
                f"{out_dir} was started under different provenance; resume needs the same "
                "condition, corpus, repetitions, cases, prompts and adjudicators"
            )
    elif (out_dir / "calls.jsonl").exists() or calls_dir.exists():
        raise CorpusError(f"{out_dir} holds calls without provenance; choose a new directory")
    else:
        _write_atomic(provenance_path, json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    calls_dir.mkdir(exist_ok=True)

    pending = [
        (case, repetition)
        for repetition in range(1, repetitions + 1)
        for case in selected
        if not (calls_dir / _call_file(case.id, repetition)).exists()
    ]

    def one_call(case: Case, repetition: int) -> CallRecord:
        started = time.time()
        clock = time.monotonic()
        result = umpires[prompts[case.id]].adjudicate_recorded(case.context)
        wall_ms = int((time.monotonic() - clock) * 1000)
        record = CallRecord(
            condition=condition,
            case_id=case.id,
            repetition=repetition,
            prompt_version=prompts[case.id],
            started_at=started,
            wall_ms=wall_ms,
            proposal=proposal_json(result.proposal) if result.proposal else None,
            failure=result.failure,
            attempts=[dict(a) for a in result.attempts],
            mechanical=_mechanical_json(score_mechanical(case, result)),
        )
        _write_atomic(
            calls_dir / _call_file(case.id, repetition),
            json.dumps(record.as_json(), ensure_ascii=False, indent=1) + "\n",
        )
        return record

    records: list[CallRecord] = []
    executor = ThreadPoolExecutor(max_workers=concurrency)
    try:
        futures: list[Future[CallRecord]] = [executor.submit(one_call, *item) for item in pending]
        done, _ = wait(futures, return_when=FIRST_EXCEPTION)
        for future in futures:
            error = future.exception() if future in done else None
            if error is not None:
                raise error
        records = [future.result() for future in futures]
    finally:
        # On an error, calls already in flight finish and are kept; queued ones never start.
        executor.shutdown(wait=True, cancel_futures=True)
        _write_calls_jsonl(out_dir)
    return records


def _write_calls_jsonl(out_dir: Path) -> None:
    calls = [json.loads(p.read_text()) for p in (out_dir / CALLS_DIR).glob("*.json")]
    calls.sort(key=lambda c: (c["condition"], c["case_id"], c["repetition"]))
    _write_atomic(
        out_dir / "calls.jsonl",
        "".join(json.dumps(c, ensure_ascii=False) + "\n" for c in calls),
    )


def read_calls(out_dir: Path) -> list[dict[str, Any]]:
    lines = (out_dir / "calls.jsonl").read_text().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def write_rating_sheet(corpus: Corpus, calls: list[dict[str, Any]], out_dir: Path) -> None:
    """Write the blank rating sheet and a readable review of every usable proposal.

    The review shows the rater each proposal beside the case's claims, but not the case's
    treatment of them, so presence is recorded before the key is consulted.
    """
    rows: list[dict[str, Any]] = []
    review: list[str] = [f"# Rating review — {corpus.version}", ""]
    for call in sorted(calls, key=lambda c: (c["case_id"], c["repetition"])):
        if call["proposal"] is None:
            continue
        case = corpus.case(call["case_id"])
        proposal = call["proposal"]
        review += [
            f"## {case.id} · repetition {call['repetition']}",
            "",
            f"**Action ({case.context.argument['actor_id']}).** {case.context.argument['action']}",
            "",
            f"**Desired Effect.** {case.context.argument['desired_effect']}",
            "",
            f"**Mode** `{proposal['mode']}` · probability {proposal['probability']}",
            "",
            f"**Rationale.** {proposal['rationale']}",
            "",
            "**Success Effects**",
            *[f"- {e}" for e in proposal["success_effects"]],
            "",
            "**Failure Effects**",
            *[f"- {e}" for e in proposal["failure_effects"]],
            "",
            "**Claims to look for**",
            *[f"- `{c.id}` {c.text}" for c in case.claims if c.treatment is not Treatment.optional],
            "",
        ]
        for claim in case.claims:
            if claim.treatment is Treatment.optional:
                continue
            rows.append(
                {"case_id": case.id, "repetition": call["repetition"], "item": claim.id,
                 "in_success": "", "in_failure": "", "quality": "", "note": ""}
            )
        rows.append(
            {"case_id": case.id, "repetition": call["repetition"], "item": "quality",
             "in_success": "", "in_failure": "", "quality": "", "note": ""}
        )
    with (out_dir / "ratings.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RATING_SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (out_dir / "review.md").write_text("\n".join(review) + "\n")


def _review_block(case: Case, proposal: Mapping[str, Any], heading: str) -> list[str]:
    return [
        heading,
        "",
        f"**Action ({case.context.argument['actor_id']}).** {case.context.argument['action']}",
        "",
        f"**Desired Effect.** {case.context.argument['desired_effect']}",
        "",
        f"**Mode** `{proposal['mode']}` · probability {proposal['probability']}",
        "",
        f"**Rationale.** {proposal['rationale']}",
        "",
        "**Success Effects**",
        *[f"- {e}" for e in proposal["success_effects"]],
        "",
        "**Failure Effects**",
        *[f"- {e}" for e in proposal["failure_effects"]],
        "",
        "**Claims to look for**",
        *[f"- `{c.id}` {c.text}" for c in case.claims if c.treatment is not Treatment.optional],
        "",
    ]


def write_blinded_sheet(
    corpus: Corpus,
    runs: Mapping[str, list[dict[str, Any]]],
    sheet_dir: Path,
    key_path: Path,
    *,
    seed: int,
) -> None:
    """Write one rating sheet and review mixing every condition's proposals, blind (D-111).

    The proposals are put in identity order, shuffled once with ``seed`` and numbered in that
    order, so the ids carry nothing about where a proposal came from and the same seed always
    writes the same files. ``sheet_dir`` receives only ``ratings.csv`` and ``review.md``, which
    show a proposal's opaque id, case and content, and no condition, prompt version, repetition,
    run directory, timestamp or call id. The mapping back is ``key_path``, kept apart so the
    rater never needs to see it; ``read_blinded_ratings`` reverses it.
    """
    items = sorted(
        (condition, call["case_id"], call["repetition"], call)
        for condition, calls in runs.items()
        for call in calls
        if call["proposal"] is not None
    )
    random.Random(seed).shuffle(items)
    width = max(3, len(str(len(items))))
    rows: list[dict[str, Any]] = []
    review: list[str] = [f"# Rating review — {corpus.version}", ""]
    proposals: dict[str, dict[str, Any]] = {}
    for number, (condition, case_id, repetition, call) in enumerate(items, start=1):
        pid = f"P-{number:0{width}d}"
        case = corpus.case(case_id)
        proposals[pid] = {
            "condition": condition, "case_id": case_id, "repetition": repetition,
            "prompt_version": call["prompt_version"],
        }
        review += _review_block(case, call["proposal"], f"## {pid} · {case.id}")
        for claim in case.claims:
            if claim.treatment is not Treatment.optional:
                rows.append({"proposal_id": pid, "case_id": case.id, "item": claim.id,
                             "in_success": "", "in_failure": "", "quality": "", "note": ""})
        rows.append({"proposal_id": pid, "case_id": case.id, "item": "quality",
                     "in_success": "", "in_failure": "", "quality": "", "note": ""})
    sheet_dir.mkdir(parents=True, exist_ok=True)
    with (sheet_dir / "ratings.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=BLINDED_SHEET_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    (sheet_dir / "review.md").write_text("\n".join(review) + "\n")
    key = {"corpus_sha256": corpus.lock_sha256, "seed": seed, "proposals": proposals}
    key_path.parent.mkdir(parents=True, exist_ok=True)
    key_path.write_text(json.dumps(key, indent=2) + "\n")


def _usage(call: Mapping[str, Any]) -> dict[str, float]:
    attempts = call["attempts"]
    cost = [a.get("cost_usd") for a in attempts]
    return {
        "input_tokens": sum(a.get("input_tokens", 0) for a in attempts),
        "output_tokens": sum(a.get("output_tokens", 0) for a in attempts),
        "reasoning_tokens": sum(a.get("reasoning_tokens", 0) for a in attempts),
        "model_calls": sum(a.get("model_calls", 0) for a in attempts),
        "cost_usd": sum(c for c in cost if c is not None),
        "uncosted_attempts": sum(1 for c in cost if c is None),
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    return ordered[middle] if len(ordered) % 2 else (ordered[middle - 1] + ordered[middle]) / 2


def summarise(
    corpus: Corpus,
    calls: list[dict[str, Any]],
    ratings: Mapping[tuple[str, int], Mapping[str, Any]] | None,
) -> dict[str, Any]:
    """Aggregate one run into the measures the thresholds name, overall and per prompt stratum.

    Semantic measures need ``ratings``; without them they are reported as ``None`` rather than
    guessed. Rates are per usable adjudication; a proposal counts once per measure however many
    of its claims violate it.
    """

    def block(selected: list[dict[str, Any]]) -> dict[str, Any]:
        usable = [c for c in selected if c["failure"] is None and not c["mechanical"]["invalid_proposal"]]
        usage = [_usage(c) for c in selected]
        out: dict[str, Any] = {
            "calls": len(selected),
            "failures": sum(1 for c in selected if c["failure"] is not None),
            "invalid_proposals": sum(1 for c in selected if c["mechanical"]["invalid_proposal"]),
            "usable": len(usable),
            "invalid_supersession": sum(1 for c in usable if c["mechanical"]["invalid_supersession"]),
            "mode_unexpected": sum(1 for c in usable if not c["mechanical"]["mode_expected"]),
            # Calls recorded before v2 carry no such field; they were never scored for it.
            "branch_inconsistent": sum(1 for c in usable if c["mechanical"].get("branch_inconsistent")),
            "median_wall_ms": _median([c["wall_ms"] for c in selected]),
            "mean_cost_usd": (sum(u["cost_usd"] for u in usage) / len(usage)) if usage else None,
            "uncosted_attempts": sum(u["uncosted_attempts"] for u in usage),
            "input_tokens": sum(u["input_tokens"] for u in usage),
            "output_tokens": sum(u["output_tokens"] for u in usage),
            "reasoning_tokens": sum(u["reasoning_tokens"] for u in usage),
            "model_calls": sum(u["model_calls"] for u in usage),
        }
        if usable:
            out["failure_rate"] = out["failures"] / len(selected)
            out["invalid_supersession_rate"] = out["invalid_supersession"] / len(usable)
            out["branch_inconsistency_rate"] = out["branch_inconsistent"] / len(usable)
        if ratings is None:
            out.update(
                {m: None for m in SEMANTIC_MEASURES},
                semantic_violation_rate=None, mean_quality=None,
                cases=None, cases_violating=None, case_violation_rate=None,
            )
            return out
        counts = {m: 0 for m in SEMANTIC_MEASURES}
        any_core = 0
        qualities: list[int] = []
        per_case: dict[str, list[bool]] = {}
        for call in usable:
            rating = ratings.get((call["case_id"], call["repetition"]))
            if rating is None:
                raise CorpusError(f"no rating for {call['case_id']} repetition {call['repetition']}")
            found = semantic_violations(corpus.case(call["case_id"]), call["proposal"]["mode"], rating["presence"])
            for measure, claims in found.items():
                counts[measure] += bool(claims)
            core = bool(found["unsupported_commitment"] or found["waived_prerequisite"] or found["causal_overreach"])
            any_core += core
            per_case.setdefault(call["case_id"], []).append(core)
            qualities.append(rating["quality"])
        out.update(counts)
        out["semantic_violation_rate"] = any_core / len(usable) if usable else None
        out["mean_quality"] = sum(qualities) / len(qualities) if qualities else None
        # The repetitions of one case are correlated, so the independent unit is nearer the case
        # than the proposal. A case violates when more than half its usable repetitions do.
        out["cases"] = len(per_case)
        out["cases_violating"] = sum(1 for flags in per_case.values() if sum(flags) * 2 > len(flags))
        out["case_violation_rate"] = out["cases_violating"] / len(per_case) if per_case else None
        return out

    strata = sorted({c["prompt_version"] for c in calls})
    split_of = {case.id: case.split for case in corpus.cases}
    splits = sorted({split_of[c["case_id"]] for c in calls if split_of[c["case_id"]]})
    return {
        "corpus_version": corpus.version,
        "corpus_sha256": corpus.lock_sha256,
        "overall": block(calls),
        "by_prompt": {v: block([c for c in calls if c["prompt_version"] == v]) for v in strata},
        "by_split": {s: block([c for c in calls if split_of[c["case_id"]] == s]) for s in splits},
        "held_out_overlap": held_out_overlap(corpus),
        "by_case": {case.id: block([c for c in calls if c["case_id"] == case.id]) for case in corpus.cases if any(c["case_id"] == case.id for c in calls)},
    }


def source_run(case: Case) -> str | None:
    """The run directory a case was drawn from, from its export path under ``dev/runs/``."""
    source = case.source
    export = source.get("export")
    if source.get("kind") == "authored_variant" or not export:
        return None
    parts = Path(export).parts
    return parts[2] if len(parts) > 2 and parts[:2] == ("dev", "runs") else export


def held_out_overlap(corpus: Corpus) -> dict[str, dict[str, bool]]:
    """Whether each held-out case shares a run or a scenario with the development split.

    Argument-disjointness is guaranteed by construction, but two Arguments from one game are
    correlated, so the overlap is reported rather than assumed away (I-157).
    """
    development = [c for c in corpus.cases if c.split == "development"]
    runs = {source_run(c) for c in development} - {None}
    scenarios = {c.source.get("scenario") for c in development} - {None}
    return {
        case.id: {
            "shares_run_with_development": source_run(case) in runs,
            "shares_scenario_with_development": case.source.get("scenario") in scenarios,
        }
        for case in corpus.cases
        if case.split == "held_out"
    }


def rescore(corpus: Corpus, calls: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recompute the mechanical scores of recorded calls against the current corpus key."""
    rescored = []
    for call in calls:
        proposal = _proposal_from_json(call["proposal"]) if call["proposal"] else None
        score = score_mechanical(
            corpus.case(call["case_id"]),
            UmpireAdjudication(proposal=proposal, attempts=(), failure=call["failure"]),
        )
        rescored.append({**call, "mechanical": _mechanical_json(score)})
    return rescored
