# AMAW — Backlog

Small work that isn't part of the planned breakdown: bugs, errors to fix, cleanups, spec
defects, follow-ups deferred out of a task to keep its scope honest.

**The planned development steps are not here** — they live in [`WBS.md`](WBS.md), which is
both the plan and the progress record. This file is for what gets discovered along the way.

- IDs are `I-0NN`. Planned steps use `T-0NN`; the prefixes never mix.
- File an item with `/backlog-add`. Filing is the point — do not fix it inside an unrelated
  task.
- Closing an item: move its entry to [`BACKLOG_RESOLVED.md`](BACKLOG_RESOLVED.md) with the
  date, commit and what actually fixed it.
- An item that blocks a planned step must say so, and the block must also appear in
  `STATUS.md`.

## Entry format

```markdown
- [ ] **I-0NN** One-line summary — *blocks T-0NN* / *severity: low*

  What is wrong, where, and how it was found. What resolving it requires. Any decision
  reference (`D-0NN`).
```

---

## Open

### External full-code review follow-ups (I-077…I-088)

From the 2026-09-18 external full-code review of `agents/`, `app/`, `benchmark/`, root
composition/provenance and the current WBS/backlog. Every finding filed here was re-checked
against the code, the contracts and the existing `D-0NN`/`T-0NN` record before filing; findings
that turned out to be intentional design (unconditional activation acknowledgement under
D-046/D-048, Umpire access to hidden information, public messages not reactivating, `agent`
benchmark mode varying only the model, unenforced scenario hard facts, no objective scoring yet)
are not filed. Two low-value observations are consciously dropped: `complete_diplomacy_round`
accepting `completed + 1` while the phase is already `argument` (admin-only, observational
counter, bounded to one step per call by the `round_number == completed + 1` guard), and
`rng.draw()` being O(draw_index) (by design, INV-RNG-002; draws per game ≈ turns × Actors).
Items are grouped by invariant, not one per observation, so each can land as one
coherent change. I-077 closed 2026-09-18 (`15bf912`); I-078 closed 2026-09-18 (`def0a8d`,
D-068), so nothing here blocks a planned step any more; I-083 closed 2026-09-18 (`ffb6189`);
I-079/I-080 closed 2026-09-18 (`5082c03`/D-069, `c2ec01b`/D-070); I-082 closed 2026-09-19
(`65a2b80`, D-071); I-081 closed 2026-09-19 (`82292e0`, D-072); I-084 closed 2026-09-19 (`9b57f46`, D-073); I-085 closed 2026-09-19 (`db7d35a`, D-074);
I-086 closed 2026-09-19 (`e716b0c`, D-075); I-088 closed in T-071 (`7592008`). No item in this review cluster remains open. The launcher-file finding was folded into I-076 (closed 2026-09-18, `dc72c19`, D-067).

### Public release readiness (I-089)
I-089 closed 2026-09-21 (`9b3aceb`, D-080/D-081; `BACKLOG_RESOLVED.md`). Two owner steps sit
outside the repository: create the `amaw-runs` repository and push `~/Projects/AMAW-runs-archive/`;
run the plan §4 provider game when a budget is allocated (`dev/gates/release-readiness.md`).

### Structural cleanup cluster (I-060…I-065)

From the 2026-09-16 structural review of `app/`, `agents/`, `benchmark/` and `amaw_cli.py`
(`dev/plans/2026-09-16-structural-cleanup-review.md` — findings F1…F13 with file:line evidence,
risk and the protecting test IDs per item). Goal: make the flow
`scenario → game → phase → activation → mutation → adjudication → export` easier to trace.
**Not** fewer lines, not smaller files, no new features/architecture/persistence, no Phase-2 work.
Every item is behaviour-preserving: no manifest hash, export shape, API body or persisted column
may change; `uv run pytest` green and pasted into the brief; the `test_release_package.py`
import-boundary test stays green. Do them **in order** — each makes the next one smaller — with a
reassessment (re-run the dead-code scan and flow read-through) after I-063. Explicitly left alone:
`game_service.py`'s size and its `captured`/`mutate`/`_run_idempotent` pattern, `agents/scripted.py`,
all of `benchmark/`, and the measurement instruments (`_TimedModel`, `RequestTopologyProfiler`,
`compact_research_history`). None blocks a planned step; post-MVP housekeeping.

### Follow-up from I-078 (I-090 closed 2026-09-26, D-109; I-155 open)

*I-090 closed 2026-09-26 (`9ebe4ef`, D-109) — see `BACKLOG_RESOLVED.md`.*

- [ ] **I-155** A v2 cell batch's `--plan-level` is recorded, not checked against the batch's condition — *severity: low · analysis-plan integrity · found by I-090*

  `analysis-plan-v2` (D-109) lets a `scope: cell` batch name its treatment level with
  `--plan-level`, which must be one of `treatment.levels` and is recorded in `batch.json`.
  Nothing checks that the batch really ran that level: `treatment.factor` is prose ("con_mode"),
  so an operator could run the `open_cons` condition and label it `sampled_sealed`.

  **To resolve (if wanted):** let a v2 plan name the factor as a sealed condition path from
  `HELD_CONSTANT_PATHS` (e.g. `game_loop.con_mode`), and have `reconcile_plan` compare the named
  level with the expected batch condition's value at that path. Needs a methodology §4.2 change.
  Not needed while no analytical cell batch is run.

### Run 11 invalid-tool-call follow-ups (I-091…I-092)

I-091 closed 2026-09-19 (`8bf01ee`, D-077): the tool-loop surface now follows the server's
refreshed declaration after the Actor's own mutation. I-092 closed 2026-09-21 (D-079): the
never-declared Cons-phase attempts stay measured under the default `broad` surface.

*I-092 closed 2026-09-21 (`5579f28`, D-079) — see `BACKLOG_RESOLVED.md`. `tool_loop` is an explicit experimental condition with `tool_surface ∈ {broad, available_only}`; I-103 now takes its documented-property branch.*

### External review of run 11 follow-ups (I-093…I-094)

*I-093 closed 2026-09-21 (`23d3236`) and I-094 closed in T-071 (`7592008`) — see `BACKLOG_RESOLVED.md`.*

### Second external code review 2026-09-19 (I-095…I-097)

*I-095 and I-097 closed 2026-09-21 (`74db7a4`, `f846b4a`); I-096 closed in T-071 (`7592008`) — see `BACKLOG_RESOLVED.md`.*

From a second external review of the supplied code snapshot (`amaw_contracts`, `amaw_cli`),
2026-09-19. Every finding was re-run against the code before filing: findings 2 and 4 were
already I-093 and I-094 (I-093 extended above with the identity-collision and recursive-alias
cases); findings 1, 5 and 6 are new below. The review's architectural note — resolve CLI/
settings/manifest precedence into an immutable `RunSpec`/`BenchmarkSpec` before execution — is
recorded here as context for I-096 and not filed on its own: TRD §5 says refactor when a module
is genuinely unwieldy, and `amaw_cli.py` is close, but the two defects below are the concrete
work; the spec object is the shape a fix for I-096 may naturally take.

### Run 11 external analysis follow-ups 2026-09-20 (I-098…I-103)

From an external LLM-assisted read of run 11 (`t060-batch-smoke-20260919`, two games, `tool_loop`)
against the backlog, WBS and agent code. Every item below was re-checked against the code before
filing. Not filed, because they are experiments or already covered: the batched×compact 2×2, a
phase-specific inference profile instead of uniform-low, `max_pros` 3 vs 5, scenario incentives
for public diplomacy, a cross-activation research ledger, more seeds / an independent Umpire /
heterogeneous cross-play (T-063, deferred by D-076), objective scoring (T-061). The review's
proposed decision — `single_shot` is the normal player, `tool_loop` an explicit experimental
condition, so I-092 is not a normal-path defect — is recorded on I-092 as option (d); D-046
already makes `single_shot` the default. Chunked/bounded `read_resource` (I-104) was closed as not warranted at the portability-milestone triage.

*I-098, I-099 and I-100 closed 2026-09-21/23 (`b699c0f`, `c499ad3`, I-100 comparison record); see `BACKLOG_RESOLVED.md`. I-101 (coordinate with I-098's operation budget) is also closed.*

*I-102 closed 2026-09-21 (`4415181`, doc `e6c69e7`) — see `BACKLOG_RESOLVED.md`. `agents.csv` `completed`/`failure_reason` are the §21 attempt outcome; recovered faults sit under `failed_activations` + `last_activation_failure_reason`; `agent_framework_version` comes from the manifest.*

*I-101 closed 2026-09-22 (`af16495`, spec `2ba655c`; D-085) — see
`BACKLOG_RESOLVED.md`. Batched research now returns one ordered outcome per dispatched item,
continues through recoverable failures and retains D-078 truncation.*

*I-108 closed 2026-09-22 (`87a0bcf`) — see `BACKLOG_RESOLVED.md`. Every batched search item now
forwards the same sealed `search_contract` as sequential search; `TEST-I108-001` proves the
non-default `search-v2` value survives both paths.*

*I-111 closed 2026-09-23 — see `BACKLOG_RESOLVED.md`. `TEST-I089-005` now names all ten
retained directories: run 14's I-110 evidence and run 15's three I-100 directories were added
under D-083 after the D-081 read-before-retention audit. No run evidence was altered.*

*I-112 closed 2026-09-23 — see `BACKLOG_RESOLVED.md`. `search-v2` is the released default
(D-088, `api.md` 0.4.5); explicit `search-v1` remains supported, and a record sealed before the
change still resolves to `search-v1`.*

*I-103 closed 2026-09-21 (`0ebcd46`, spec `23c8f2f`, D-082) — see `BACKLOG_RESOLVED.md`. The sibling-mutation race is a documented property of the experimental `tool_loop` condition under both surfaces; scheduling unchanged.*

*I-105 closed 2026-09-21 (`0933813`, doc `6db976d`) — see `BACKLOG_RESOLVED.md`. `games.csv` carries `player_activation_mode` / `player_tool_surface` copied from the manifest.*

### T-060 provider smoke rerun 2026-09-21 (I-106)

*I-106 closed 2026-09-21 (`fa09df3`) — see `BACKLOG_RESOLVED.md`. Player requests now apply the
same conservative inclusive-parent normalisation as the Umpire and retain every correction.*

Post-fix run 13 passed both six-turn attempts with integrity `ok`; 24 malformed player usage
reports exercised I-106 and all 178 player `AgentRun` writes returned 201. No new correctness or
release-blocking item was found. The recovered Umpire/provider faults stay covered by closed
I-023/I-071…I-074; the invalid tool calls stay measured under the explicit experimental
`tool_loop` condition settled by closed I-092/I-103. The run's 82 searches / 1 read reinforced
the I-100 candidate, which was later resolved by Run 15; I-101 remains conditional on
`batched-v1`. No new backlog ID was allocated.

### Swedish v6 full-run findings and attention follow-ups (I-051…I-057)

Verified against game `524ba2d8-aa79-4b91-b11a-93fb37b9494b`, its export/log and the current
implementation. The completed eight-Actor/four-turn run is useful evidence, but remains one
DeepSeek playtest rather than a benchmark result. Its export is format v4, predating I-050's
inclusive failed/retried-request usage accounting. The post-I-050 provider reconciliation records
approximately 63.7M inclusive tokens and $2.24 spend versus 13.9M tokens and `$0.0402` in the old
export: 270 successful responses lost usage when their activations ultimately failed. Provider
evidence reports a 50.8% cache-hit rate, but the run predates I-056 and cannot be assigned
historical component topology or per-request cache counts. OpenRouter's simultaneous 71.1M
ranking-card value remains an explicitly unresolved dashboard discrepancy. See
`runs/swedish-government-v6-resume/usage-reconciliation.md` and its hashed screenshot manifest;
no separate cost-display defect is filed.

Every item in this cluster is now closed (I-051…I-057; see `BACKLOG_RESOLVED.md`). The header is
kept so the run context above stays attached to their resolutions.

### Deferred adjudication and matrix-game portability feedback (I-038, I-039, I-109, I-113; I-116 closed 2026-09-25)

Discovered from external review of the Swedish government-formation run; I-109 added 2026-09-22
from the matrix-game portability review (D-086). I-036/I-037 and I-046 are resolved in
`BACKLOG_RESOLVED.md`; I-113 added 2026-09-23 (D-089); I-116 added 2026-09-24 (T-075, D-093) and resolved 2026-09-25 by run 18. These items remain deliberately separate:
each changes the engine model or game loop and is not made “done” by stronger Umpire/scenario
prose. The two-stage adjudication evaluation (T-067…T-069) measures model reliability and is not
I-038's route (D-089); Consequence Management shipped in T-074 (I-040 resolved); the I-046 `visible_reply` treatment (D-060)
must not be combined with the adjudicator change in one evaluation. A critic or general
event-driven activation queue remains Phase-2 work unless evidence creates a narrower need.

- [ ] **I-038** Free-text Effects cannot provide a server-enforced authority guarantee — *accepted limitation for narrative state · no universal typed authority model · mechanically authoritative domains → external systems (I-113) · revisit only on domain-independent evidence from T-075 or later scenarios*

  `umpire-v1.3` makes narrowing mandatory but an LLM remains the semantic interpreter. Making it
  impossible for an unsupported commitment to become authoritative regardless of model output
  requires a minimal typed commitment/derivation representation, explicit evidence references or
  another mechanically checkable design. Evaluate this against the TRD's deliberate exclusion of
  authority graphs and formal Claims before changing the generic domain model. D-044.
  *2026-09-22:* unchanged. T-067…T-069 remain the planned experimental route; do not couple this
  to the Matrix Scenario Portability milestone unless T-072/T-075 produces evidence requiring it.
  *2026-09-23 (D-089):* reframed. The problem statement stands — free-text Effects cannot carry a
  mechanical semantic guarantee, and `umpire-v1.3` remains the semantic narrowing layer for
  narrative-only scenarios. The assumed solution direction changes: AMAW will not introduce a
  universal authority graph, typed commitment model or world ontology merely to remove this
  limitation. Where a scenario needs mechanically enforced state or authority, that domain lives
  in an authoritative external system reached through the connector boundary (I-113); AMAW
  controls which Actor may invoke which capability and records the returned evidence.
  T-067…T-069 measure model reliability and cannot deliver this guarantee, so the cross-reference
  is removed. Revisit native typed state only if real scenarios show a common, domain-independent
  need. Kept open because the limitation is real for every prose-only scenario.
  *2026-09-23 (T-072):* supports D-089. Semantic authority-first adjudication was expressive
  enough for every audited matrix structure (BEAR RISING's "negotiate, don't order" rule maps to
  D-044); none needed a typed authority/Claim graph. No domain-independent reopening trigger
  found. `dev/portability/T-072-portability-audit.md` §H6, §8.5.
  *2026-09-24 (T-075, D-093):* unchanged. In run 17, first-class semantic `rules` carried the
  overlapping civil/military authority. 23 of 24 adjudications cited a rule by `id`, and
  Defence Command's out-of-authority request failed automatically at the authority gate. No
  domain-independent need for typed authority was found.

- [ ] **I-039** Actors cannot send reaction diplomacy during Cons or after adjudication — *deferred · evidence-gated by T-072/T-075 · game-loop behaviour change*

  Messaging is already legal in diplomacy and the active Argument phase, so the feedback's “only
  at turn start” description is too broad. It is unavailable during Cons/adjudication, however,
  leaving no bounded reaction window after a consequential submitted/resolved Argument. I-046
  (`visible_reply`, D-060, resolved) now supplies a bounded pre-adjudication response, weakening
  the original Matrix-fidelity argument. Retain this item only for scenarios that need
  communication after a consequential action/result outside the Diplomacy phase; T-072/T-075 must
  identify a concrete case before promotion. If promoted, define an explicit bounded reaction
  window — eligibility, activation count, message budget, visibility, closure, resume semantics,
  provenance and benchmark comparability. Do not implement unrestricted messaging in arbitrary
  phases and do not introduce a general event-driven activation queue under this item.
  *2026-09-23 (T-072):* keep deferred. No audited source makes a post-adjudication reaction window
  a portability requirement; Diplomacy, Argument-phase Messages and `visible_reply` cover the
  demonstrated negotiation needs. T-075 reassesses. `dev/portability/T-072-portability-audit.md` §H5.
  *2026-09-24 (T-075, D-093):* keep deferred. No Actor in run 17 needed to communicate
  after an adjudication and before the next Diplomacy phase. Same-turn Arguments, Cons, owner
  replies and the next Diplomacy phase carried every reaction.

- [ ] **I-109** One primary Argument per Actor per turn limits portability of matrix-game variants with multiple actions — *partly delivered by T-080 (round-robin rounds) · remainder deferred: conditional / once-per-game / post-round bonus actions, scarce/trigger resources, slot decline*

  The schema exposes `game.primary_arguments_per_actor` and the engine keeps `budget_slot`, but
  the loader refuses any value other than 1 (`app/scenarios.py:195`) because the state machine
  advances to the next Actor after one resolved primary Argument. Some matrix-game variants give
  players multiple actions, preparatory actions or more than one Argument opportunity per turn.
  Do not implement generic action points and do not silently relax the loader. T-072/T-075 must
  first establish a concrete scenario requirement and answer the sequencing question —
  consecutive slots for one Actor, round-robin Argument rounds, or another explicit policy. If
  promoted, the planned task must cover state-machine sequencing, Actor context, activation
  scheduling, budget slots, resume/restart, events, export, prompts and provenance while keeping
  the released one-Argument behaviour as the default. Filed 2026-09-22 (D-086).
  *2026-09-23 (T-072):* evidence gate met. Three external structures need more than one action
  opportunity and they differ in kind — conditional bonus action (ISIS Crisis), once-per-game
  bonus after the normal round and before end-turn handling (BEAR RISING), typed per-turn action
  slots (Israel-Hizbullah wartime) — and the UK *Influence Wargaming Handbook* independently
  flags one-action-per-turn as insufficient for influence problems. The later sequencing task must
  distinguish typed/round-robin slots, conditional bonus actions, post-round bonus actions and
  scarce/trigger resources; never arbitrary consecutive N Arguments. Not in T-073/T-074; T-075
  stays single-action; plan after T-075 unless T-075 shows an earlier dependency. `dev/portability/T-072-portability-audit.md` §H4, §8.4.
  *2026-09-24 (T-075, D-093):* stays deferred, ready to plan. In run 17 the Cabinet
  bundled several decisions into one Argument ("in one decision: (1)… (2)…"). The Umpire
  narrowed each component, but one roll decided the whole bundle. That is a fidelity cost, not a
  blocker, so T-075 promotes nothing. Plan the sequencing task when a scenario needs typed,
  conditional or timed extra actions.
  *2026-09-24 (T-080, D-094):* **partly delivered.** Round-robin Argument rounds shipped:
  `primary_arguments_per_actor` 2–3, one pass through `turn_order` per slot, and never
  consecutive Arguments by one Actor. Typed slots are expressible as semantic `rules` that cite
  `budget_slot`. Still deferred, each waiting for a scenario that needs its own trigger or
  ledger semantics:
  - conditional bonus actions (ISIS Crisis);
  - once-per-game or post-round bonus actions before Consequence Management (BEAR RISING);
  - scarce or trigger resources;
  - declining a slot.

  No live run yet at a budget above 1.

- [ ] **I-113** Generic external-system connector for specialist authoritative mechanics — *deferred · post-release architecture · evidence-gated by T-075 · extends TRD §43.3 / WBS §11.3*

  AMAW handles decisions, negotiation, private information and narrative causality; specialist
  mechanics (logistics, operational warfare, economics, …) belong to external systems that
  already model them, not to a growing AMAW state model (D-089, I-038). TRD §43.3 already
  defines a read-only `ToolProvider` gateway (MCP/HTTP/local-Python adapters, Actor/scenario
  authorisation, call logging, provenance) whose invariant is that external tools never mutate
  authoritative AMAW state. This item extends it with a transport-neutral connector contract —
  `capabilities` / `observe` / `execute` / `events` — in which the **external system**, not the
  adapter and not AMAW, is authoritative over its own domain state, while AMAW remains
  authoritative over its own state and commits every AMAW-side change. MCP is one adapter, never
  the contract. The contract must settle: scenario-declared, per-Actor capability permissions
  with an explicit mapping layer (a discovered MCP tool never becomes an Actor tool
  automatically); idempotency keys; external revision before/after; request/result provenance
  with connector and external-system version; failure and unavailability semantics (fail
  closed); visibility of returned data; versioning; where `execute` sits relative to the
  Argument → Cons → adjudication → roll sequence; how external randomness reconciles with
  INV-RNG-001 (AMAW-supplied seeds or an explicit carve-out); and connector identity as part of
  the sealed benchmark condition. Changing the TRD §43.3 invariant requires its own
  `DECISIONS.md` entry and doc commit. Planned route: T-076 (contract design, docs only), queued
  only if T-075 records a concrete need; T-077…T-079 (read-only prototype, authoritative
  commands, synchronisation against a small in-repo logistics simulator) are sketched, not
  queued. Do not build this before the Matrix Scenario Portability milestone closes, do not add
  connector support to T-073/T-074, and do not allow a connector or plugin in T-075.
  *2026-09-23 (T-072):* supported in principle, not triggered. Connector fits among audited
  mechanics: authoritative unit position/zoned movement, specialist combat resolution, free
  tactical movement/combat, exact political-support scores where analytically essential, hybrid
  handoff to another rule system (the clearest fit), plus FOI's logistics evidence. Not connector
  cases: subgame switches and role rotation, which would reconfigure AMAW's own loop/Actors. No
  audited portability target needs a connector; gate stays at T-075. `dev/portability/T-072-portability-audit.md` §H7, §8.7.
  *2026-09-24 (T-075, D-093):* not triggered, so **T-076 does not open**. The acceptance
  scenario kept fuel, lead times and posture qualitative, and no adjudication turned on an exact
  quantity or a position. Exact logistics, spatial or combat state remain the fit (I-115). Reopen
  on a scenario that needs an authoritative external domain.

### Run 17 analysis follow-ups 2026-09-24 (I-117 closed 2026-09-24, D-098; I-118 closed 2026-09-24, D-099; I-128 closed 2026-09-25)

Every item in this cluster is now closed; see `BACKLOG_RESOLVED.md`.


### Maintainability reviews 2026-09-24 (I-119…I-127)

From an external LLM maintainability review of the codebase on 2026-09-24. Every item was
checked against the code and the decision record before it was filed. None blocks a planned
step. All three are behaviour-preserving. No manifest hash, export shape, API body, prompt or
tool identity, or persisted column may change.

Two findings were adjusted:
- **Not filed:** "comments lean on `I-/D-/T-` references". This is a convention for code being
  touched anyway. It has no completion state, so it does not belong here. It was adopted as a
  rule in AGENTS.md §4, "Comments explain the rule first, then give the reference".
- **Made conditional:** "move to Alembic". D-071 decided against Alembic until a non-additive
  change, and the reasons still hold. That is I-121.

A second external review on 2026-09-24 endorsed I-117…I-121. It added I-122 and I-123. It also
added evidence to I-119 (the long functions and the `request._body` helper) and to I-117 (fix
it before any cost comparison). It made these suggestions, which were not filed:
- **Query-plan and index audit.** There is no measurement of a problem. Actor event polls do
  scan a game's whole event history, but scenarios are small by design. Re-open this if a
  measured poll or context build becomes slow. Any new index is additive, so it does not
  depend on I-121.
- **Honour `Retry-After` and add backoff.** The bounded single retry is part of the
  contract: exactly one retry on a transient failure (agents.md §18, TRD §35, D-018). Changing
  it is a contract and experiment decision, not a hardening fix.
- **Admin RBAC, tenancy, rate limiting.** These apply only to a public multi-user deployment,
  which is out of scope (TRD §41).
- **Stateful property tests over operation sequences.** This is a good idea but not a defect.
  Consider it when a state-machine change next lands.

*Closed 2026-09-24:*
- I-119, the plan `dev/plans/2026-09-24-structural-hotspot-plan.md`, with its splits I-124,
  I-125 and I-126;
- I-120;
- I-122 (D-097);
- I-117 (D-098);
- I-123 (D-100);
- I-127 (D-101);
- I-129 (D-101), closed 2026-09-26.

See `BACKLOG_RESOLVED.md`. Open: I-121 (conditional).

- [ ] **I-121** Adopt versioned migrations (Alembic) at the first non-additive schema change — *severity: low · conditional · trigger not yet met (D-071)*

  `app/database.initialize_database()` upgrades legacy databases in place. It checks which
  columns exist, adds the missing ones with additive `ALTER TABLE` statements (about ten
  column groups so far) and a few backfills, then stamps `SCHEMA_VERSION`, which is still `1`.
  D-071 decided on no Alembic until "a non-additive change makes a migration tool worth its
  weight". Every change so far has been additive, so the trigger has not been met. Until it is,
  migrating would add a dependency and a baseline for a single schema version.

  **Trigger:** the first schema change that is not a nullable-column addition. Examples are a
  table rebuild, a new constraint on existing data, a renamed or dropped column, or a backfill
  that cannot be expressed idempotently. Starting then, and before that change is made:
  - an Alembic baseline equal to the current schema, stamped over existing databases without
    rewriting them;
  - the existing additive steps made into the first ordered migration;
  - keep refusing a newer database, and keep refusing orphans with
    `PRAGMA foreign_key_check`;
  - migration tests: a fresh database, a pre-stamp legacy database with backfill, an
    already-current database, and a newer database that must be refused.

  SQLite stays the store. Needs a `D-0NN` that supersedes D-071's "No Alembic" clause.

### Structural review revisit 2026-09-26 (I-153…I-154 closed 2026-09-26, D-108)

A re-read of the I-119 hotspot plan revised two of its verdicts. Both splits landed; see
`BACKLOG_RESOLVED.md`.

### Round-throughput cluster (I-027…I-034)

Discovered from the T-044 timing (3 Actors × 6 turns took 71 min; throughput bit at turn 4,
`RUNS.md`) and the `scenarios/` Swedish formation scenario: 8 Actors × 3 turns ≈ 24 Arguments,
and every Argument makes the other 7 Actors eligible to Con or pass — ~168 Con decisions before
diplomacy and Umpire, ~240 agent/Umpire activations per game, each possibly several model rounds.
All concurrency work lives in the orchestration layer (`agents/`, `benchmark/`) — **never the
server**, which stays sequential and transactional (INV-GAME-004/005; the T-024 race guards must
hold under concurrent POSTs). **Bucket A** = behaviour-neutral (wall-time/cost only; safe to
implement as an optimization). **Bucket B** = changes what the benchmark measures (needs a
`DECISIONS.md` entry, usually a prompt/spec bump, and ideally a config flag so runs stay
comparable — `benchmark-methodology`). These are post-MVP, for the Benchmarking / Experimental
Evaluation milestone; none blocks T-051. I-027 supplied the first live measurement; run 7 now adds
a partial current-HEAD eight-Actor turn with downstream phase evidence: 252 model calls, 3,532,316
input tokens and 727,296 output tokens overall, of which Diplomacy used 173 calls and 2,392,059
input tokens. Use the existing T-053 per-phase report to validate any change rather than treating
raw token reduction as a proxy for preserved behaviour.

Every item in this cluster is now closed (I-027…I-034; see `BACKLOG_RESOLVED.md`). The header is
kept so the throughput evidence above stays attached to their resolutions.


### Run 19 (`ai-2027-branch-point` 1.0.0) follow-ups 2026-09-25 (I-147…I-150 closed 2026-09-26; I-151 open)

- [ ] **I-151** A ten-minute network drop stops an unattended run; resuming it is manual — *severity: low · run robustness · found by run 19*

  A player activation has 2 attempts, each capped at the 60 s request timeout, and then fails the
  run with "no legal recovery" in the `argument` phase. The Umpire has 4 attempts, about 4
  minutes, before the server pauses the game (D-021, D-063). Run 19 stopped three times on local
  connection drops (`ConnectError`, `ConnectTimeout`, `RemoteProtocolError`) and needed a manual
  `--resume-game` each time. An overnight run would have waited for the owner.

  **To resolve (choose one, record it in a `D-0NN`):**
  - an opt-in `--auto-resume N` on `amaw run`: after a transient-category stop or pause, wait with
    backoff and resume the same game, up to N times, logging each resume in the manifest; or
  - a longer, backoff-spaced transport-retry budget for connection-level errors only, which do
    not reach the model and so do not change what was generated.

  Either must keep the game's provenance unchanged and must never fabricate an outcome
  (INV-ADJ-003).

  **Owner, 2026-09-26:** deferred until a network drop recurs; run 19's may have been a one-off.

  **Recurred in run 22 (2026-09-28), with a worse effect than a stop.** At 02:32 UTC in turn 3
  Diplomacy, one drop failed all eight activations at once (`ModelAPIError` after the single
  retry). Each failure's recovery action was `ready`, so the phase closed after one round and 14
  Messages, against 13 rounds and 165–250 Messages in the other three turns. The run did not stop,
  so nothing flagged it: a transport fault silently shortened the negotiation the game measures.
  Whatever option is chosen should also keep a transport failure from counting as an Actor's
  decision to be ready.

### Move to the public repository 2026-09-26 (I-152)

- [ ] **I-152** Migrate development to the public repository — *severity: medium · release · owner direction (D-106)*

  D-106 makes the public GitHub repository the upstream and freezes this one. A public build made
  on 2026-09-26 with `scripts/build_public_source.py` (342 files) is not yet fit to become the
  upstream:
  - 198 files carry 1,741 `I-`/`D-`/`T-` citations, 963 of them in `app/`, `agents/`,
    `benchmark/` and the root modules. None resolve, because `DECISIONS.md` and the backlog are
    not shipped.
  - `app/__init__.py` and `amaw_cli.py` cite `AGENTS.md §6`, which is not shipped.
  - 5 files cite `dev/` paths: `tests/test_config.py`, `tests/test_harness.py`,
    `tests/test_i123_deployment_envelope.py`, `docs/TRD.md` and
    `scenarios/skaro-crisis/AUTHOR_NOTES.md`.
  - 63 of 127 test files are named after issue numbers. *(2026-09-26: none since I-140.
    D-107 says scoped `TEST-*` IDs are traceability, and the builder no longer excludes test
    files, I-137.)*
  - `~/Projects/AMAW-public-ready` dates from 2026-09-22 and predates the `ai-2027` and
    `swedish-government` 7.0.0 packages.

  **To resolve:**
  - Check the rights for `scenarios/ai-2027` (derived from the third-party AI 2027) and for the
    sourced Resources in `scenarios/swedish-government`, before they ship under MIT.
  - Audit `AGENTS.md` and `DECISIONS.md` for private material, then add both to the builder's
    allowlist.
  - Rewrite `AGENTS.md` and `CLAUDE.md` for a GitHub-issues workflow. Drop the STATUS/WBS
    session-start steps, and drop the slash commands or retarget them to issues. Keep §6.
  - Make the `dev/` references and the non-`D-` IDs resolvable or self-explanatory in the public
    tree, touching comments only where they are otherwise unreadable (AGENTS.md §4).
  - Turn open backlog items worth keeping into GitHub issues, and leave the rest archived here.
  - Rebuild the snapshot, then run its locked install, suite, `uv build` and CLI help surfaces.
    Create the repository, push, and create `amaw-runs` with `~/Projects/AMAW-runs-archive/`.
  - Freeze this repository: its `STATUS.md` names the public repository as the upstream.
  - *2026-09-27 (T-067):* decide whether `corpora/adjudication/v1/` and the two corpus scripts
    ship; the builder's allowlist does not include `corpora/` yet (D-110). The baseline results
    live in `dev/runs/t067-adjudication-baseline/` and cite `dev/` paths.
  - *2026-09-27 (I-156/I-157):* the same question for `corpora/adjudication/v2/`,
    `scripts/run_adjudication_v2.py` and `scripts/select_heldout_cases.py`.
  - *2026-09-27, slice 1 done (D-115):* one command starts any scenario. Every checkout
    scenario runs by name, the consequence resolver defaults to the Umpire model, and `amaw run`
    writes its export beside the database. Bundling `ai-2027`/`skaro-crisis` into the wheel
    still waits for the rights check above.
  - *2026-09-27, slice 2 done (D-116):* the audit, the public `AGENTS.md`/`CLAUDE.md`
    (`public_overlay/`), resolvable identifiers (the history ships), the issue drafts
    (`dev/tasks/I-152-github-issues.md`) and a verified snapshot. `corpora/` ships. Left: the
    rights checks, creating and pushing both repositories, opening the issues, the freeze.
  - *2026-09-27, slice 3 (D-117):* `swedish-government` cleared by the owner. `ai-2027` is held
    back, with `corpora/` and the tests that need them, until the AI 2027 authors agree; adding
    it later is one public change. Left: creating and pushing both repositories, the issues,
    the freeze.
  - *2026-09-27, owner:* the short `ai-2027` descriptions in `scenarios/README.md` and the Field
    Manual stay, and `amaw-runs` ships complete, run 19 included.

  **What is left (2026-09-27), in order:**
  0. *Owner, 2026-09-28:* the public repository is `mahee-dev/AIMatrixWargames` (D-120), so the
     private `mahee-dev/AMAW` keeps its name. After the move, archive it read-only rather than
     delete it: it holds the commits, briefs, run evidence and scenario tags the frozen `dev/`
     files cite.
  1. *Owner go-ahead:* build a fresh snapshot (`uv run python scripts/build_public_source.py
     <empty dir>`), make the root commit there, rerun its locked install, suite, `uv build` and
     CLI help (`PUBLIC_SOURCE.md`), tag the root commit with the `scenario/…` tags the builder
     prints (I-169), then create the public `AIMatrixWargames` repository on GitHub and push.
  2. *Owner go-ahead:* create `amaw-runs` and push `~/Projects/AMAW-runs-archive/` (21 run
     directories, run 19 included). `dev/RUNS.md` already cites `amaw-runs/<dir>/`.
  3. *Owner go-ahead:* in the public repository, create the labels and open the 14 issues drafted
     in `dev/tasks/I-152-github-issues.md`.
  4. Check the public repository's CI (`.github/workflows/ci.yml`) is green on the first push.
  5. Freeze this repository: `STATUS.md` names the public repository as the upstream, the I-152
     brief is closed and archived, I-152 moves to `BACKLOG_RESOLVED.md`, final commit.
  6. *Later, when the AI 2027 authors agree (D-117):* in the public repository, add
     `scenarios/ai-2027/`, `corpora/`, `tests/test_scenario_ai_2027*.py` and
     `tests/test_adjudication_corpus*.py` from this repository's last commit, restore the
     `ai-2027-branch-point` entry in `scenarios/versions.lock`, and remove the held-back notes in
     `README.md`, `scenarios/README.md` and `guide/manual.html`. Its own pull request, with a
     `D-` entry.

### I-156 run 21 follow-up 2026-09-27 (I-158)

- [ ] **I-158** The rating review hides the claim notes the rubric tells the rater to apply — *severity: low · measurement · found by the run 21 rating*

  Rubric v2 says "A claim's note may say that one state entails another … Apply the entailment
  the note gives", but `write_rating_sheet` and `write_blinded_sheet` print only the claim text.
  The rater of run 21 never saw the entailment notes on `ob-directed-members` and
  `wh-directed-members` (a recorded YES vote entails the direction) and marked recorded votes
  `n`. Showing every note is not the fix: most notes explain the key's treatment and would
  unblind it. Run 21 corrected the 30 affected rows by a blind re-rate that showed only those
  notes (D-113). **To resolve:** in the next corpus version, give claims a separate rater-facing
  field (entailments only), render it in `review.md`, and test that key-only notes never appear.

### Runs 22–23 follow-ups 2026-09-28 (I-164…I-166 open; I-160 closed by D-118, I-161…I-163 closed; I-151 updated)

- [ ] **I-164** `umpire-v1.7` still rolled twice on other Actors' decisions — *severity: medium · adjudication · found by run 23*

  In run 23 (v1.7, game `04b7bb63…`) two of the three probabilistic rulings rolled on decisions
  §23 and D-118 leave to played Actors:
  - Turn 2, Cestia, p = 0.60: whether Arkania accepts a wider audit of its execution role and
    whether Belvar finalises stand-down and insurance terms.
  - Turn 3, Belvar, p = 0.60: whether Cestia accepts a bilateral insurance agreement.
  The third (turn 2, Belvar, p = 0.65) is clean: it rolls on an independent verification mission
  no Actor plays. *Corrected 2026-09-28 (I-168):* this entry first said once; the turn 2 case
  was found while choosing the README example.
  **To resolve:** add both cases to the next adjudication corpus as held-out items and check the
  rate under v1.7. Two in 18 rulings is a recurrence, so a prompt amendment is likely needed.

- [ ] **I-165** Concurrent games share SQLite in rollback-journal mode — *severity: low · operations · found by run 23*

  `engine_for_path` sets only `timeout=30` and foreign keys; the database stays in SQLite's
  default journal mode, where a write locks the file. Run 23 ran three games on one database with
  no lock errors, so nothing is failing. **To resolve, before larger concurrent batches:** set
  `PRAGMA journal_mode=WAL` on connect (with a test), or give each game its own database file.
  PostgreSQL is not needed for this (TRD, lean MVP).

- [ ] **I-166** Provider latency sets game length and AMAW cannot pin a fast host — *severity: low · operations · found by run 23*

  DeepSeek v4 Flash calls took 30–300 s each in run 23, so a `northern-strait` game took about
  2 hours against 73–79 minutes in run 11 at similar call counts. OpenRouter chooses the host.
  **To resolve:** allow an optional OpenRouter provider preference (order / allow-list) per run,
  recorded in the run manifest as part of the model assignment, so a faster host can be chosen
  without changing the model.

---

### Pre-publication docs review 2026-09-28 (I-168 closed 2026-09-28)

### Development continuity after the move 2026-09-28 (I-169 closed 2026-09-28)

## Deferred — out of lean-MVP scope

Not scheduled. `TRD §41` and the archived original WBS §7
(`dev/archive/docs/original-vision/AMAW_WBS_v0.2.1.md`) list what the lean MVP excludes; its
§11 describes Phase-2 work (organisations, communication topology, external tool gateway
/ MCP, event-driven activation, Actor tiers, authority policies, hybrid human mode,
large-scenario runtime).

Phase 2 opens only when the post-MVP Benchmarking / Experimental Evaluation milestone runs
reliably, repeated games produce usable behaviour, a scenario genuinely needs a large Actor
count, and one external tool integration has a concrete user case (WBS §11.10).

- [ ] **I-115** Matrix-game mechanics left unsupported after T-075 — *deferred · explicit dispositions · no planned work (D-093)*

  WBS T-075 requires "every remaining unsupported mechanic has an explicit deferred
  disposition". Numbers `#` are rows in `dev/portability/T-072-portability-audit.md` §5. None
  was needed by the acceptance scenario (run 17). Each reopens only on a concrete scenario need,
  through its named route.

  | Mechanic | Audit rows | Disposition |
  |---|---|---|
  | Multiple, conditional or timed extra actions | #20, #21, #36, #37, #49 | I-109 (deferred, ready to plan) |
  | Reaction window after adjudication | — | I-039 (deferred) |
  | Exact spendable ledgers, tracks, thresholds and modifier tables | #19, #26, #31, #42 | Semantic Effects plus rules for qualitative use. A generic capability only on reusable, domain-independent evidence; otherwise an external system (I-113). |
  | Authoritative position, movement, combat, logistics flows | #24, #29, #43, FOI S11 | External system via I-113 (T-076 not opened) |
  | Hybrid handoff to another ruleset | #40 | External system via I-113 |
  | Timed exogenous events, event decks, event-driven activation | #34 | Deferred (scenario-format §22, WBS §11). Timing is expressed as deterministic rules plus the consequence pass. |
  | Secret primary actions | #16 | Deferred (scenario-format §22, Secret Argument triggers) |
  | Dynamic initiative | #28 | Deferred. Fixed `turn_order`; no evidence of need. |
  | Subgame switch, faction-role rotation | #46, #47 | Deferred. These would reconfigure AMAW's own loop or Actors, and are not connector cases. |
  | Mechanical authority guarantee | #14 | I-038 (accepted limitation, D-089) |
  | Live map or token objects | #22, #23 | Not needed. Map Resource plus Effects (T-075 pattern). |

  Filed 2026-09-24 (T-075).
