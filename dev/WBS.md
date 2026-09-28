# AMAW — Development Breakdown

Derived from the original delivery WBS, now archived at
`dev/archive/docs/original-vision/AMAW_WBS_v0.2.1.md` — its 66 ordered, individually-shippable
development steps — and extended by recorded post-MVP tasks (T-067 onwards) as the project's research
questions have been added.

**This file is the plan, the queue and the progress record.** The next task is the first
step below without a ✅. WBS chapter numbers are preserved so every step traces back to the
delivery document.

`BACKLOG.md` is something different — small work discovered along the way (bugs, errors,
spec defects), numbered `I-0NN`. Planned steps and discovered issues never share a prefix.

## How to read a step

```text
### T-0NN — Title
`WBS ref` · `spec sections to read` · after <dependencies>
What the step delivers, in one or two lines.
**Done when:** the observable, testable exit condition (test IDs where they exist).
```

Steps are ordered along the critical path in the original WBS §9. Dependencies are
listed where they are not simply "the previous step". Test IDs come from `docs/testing.md`
and are the definition of done.

Optional steps are marked **[optional]** — recommended but not gate-blocking.

## How a step is closed

`/task-done` marks the heading and appends the evidence line, in place:

```text
### ✅ T-0NN — Title
...
**Closed:** YYYY-MM-DD · **Commit:** `abc1234` · **Tests:** TEST-ARG-001…008, 34 passed
**Brief:** `archive/tasks/T-0NN-slug.md`
```

Closed steps stay where they are. The delivery record and the contract the work was built
against sit on the same screen, which is the point.

---

# Gate 0 — Foundation

*Can development proceed reproducibly from a fresh checkout?*

### ✅ T-001 — Repository scaffold and packaging
`WBS 1.1.1` · `TRD §5, §6` · —
`pyproject.toml` with uv, Python 3.12+, dependency set (FastAPI, Pydantic v2, SQLModel,
PyYAML, httpx, Typer, pytest). Package skeleton `app/ agents/ benchmark/ scenarios/ tests/`.
`.env.example`, baseline `README.md`. Do not create empty speculative modules.
**Done when:** `uv sync` completes from a clean checkout and `uv run python -c "import app"`
succeeds.
**Closed:** 2026-09-10 · **Commit:** `42e0d23` · **Tests:** no TEST-* IDs (harness is
T-003); acceptance = `uv sync` + `import app` + TRD §4 boundary, all green; `pytest` 0 items
**Brief:** `archive/tasks/T-001-repository-scaffold.md`

### ✅ T-002 — Runtime configuration and logging
`WBS 1.1.2` · `TRD §6, §36` · after T-001
Central settings object: OpenRouter API key, database path, log level, request timeouts.
Environment-driven, no secrets in code or logs.
**Done when:** config loads from environment with documented defaults; a unit test asserts
no API key or bearer token is ever emitted to logs.
**Closed:** 2026-09-10 · **Commit:** `e518085` · **Tests:** no TEST-* IDs; 4 unit tests
in `tests/test_config.py` (defaults, env override, SecretStr, no-secret-in-logs), 4 passed
**Brief:** `archive/tasks/T-002-config-and-logging.md`

### ✅ T-003 — Test harness
`WBS 1.1.3` · `testing.md §2, §3` · after T-002
pytest configuration, temporary-SQLite fixture, session/teardown fixtures, registered `llm`
marker excluded from the default run. Tests must not depend on execution order.
**Done when:** `uv run pytest` passes with no OpenRouter key present.
**Closed:** 2026-09-10 · **Commit:** `3c9d05e` · **Tests:** no TEST-* IDs; 6 harness
self-tests in `tests/test_harness.py` (marker registered/excluded, temp-DB isolation +
order independence, key-free env), full suite 10 passed with no key
**Brief:** `archive/tasks/T-003-test-harness.md`

---

# Gate 0 continued — Domain foundation

*Is the game engine correct without relying on AI?*

### ✅ T-004 — Domain models: Game, Actor, Seat
`WBS 2.1.1–2.1.3` · `TRD §7.1–7.3` · after T-003
SQLModel entities. Game carries turn, max turns, phase, status, turn order, active Actor and
RNG seed. Actor carries public description plus private brief, objectives and starting
information. Seat carries the bearer-token **hash** only.
**Done when:** entities instantiate and round-trip through SQLite with the documented fields.
**Closed:** 2026-09-10 · **Commit:** `40ccd03` · **Tests:** no TEST-* IDs; `tests/test_models.py`
Game/Actor/Seat round-trips + Seat stores only `token_hash`; full suite 25 passed, no key.
Batched with T-005/T-006 (one `app/models.py`, TRD §5). Decision **D-007**.
Post-close correction **I-003/D-008** scopes Actor identity per game and snapshots execution
rules before T-007 (`9d0bc96`).
**Brief:** `archive/tasks/T-004-domain-models.md`

### ✅ T-005 — Domain models: Message, Argument, Con
`WBS 2.1.4–2.1.6` · `TRD §7.4–7.6` · `state-machine §5–§7`
Message (sender, recipients, public/private, text, turn, timestamp), Argument (Actor, action,
desired Effect, Pros, status, turn), Con (Argument, submitting Actor, reason).
**Done when:** entities persist and enforce their required-field shape.
**Closed:** 2026-09-10 · **Commit:** `40ccd03` · **Tests:** no TEST-* IDs; `tests/test_models.py`
Message/Argument/Con round-trips + required-field shape as `NOT NULL` at persistence
(table models skip Pydantic validation); full suite 25 passed. Batched with T-004/T-006.
**Brief:** `archive/tasks/T-004-domain-models.md`

### ✅ T-006 — Domain models: Effect, Event, Note
`WBS 2.1.7–2.1.9` · `TRD §7.7–7.9` · `state-machine §8`
Effect (description, visibility, visible Actors, source Argument, creation turn, optional
expiry, active flag, `supersedes`). Event as an immutable record. Note per Actor.
**Done when:** entities persist; Effect supports the supersession relationship.
**Closed:** 2026-09-10 · **Commit:** `40ccd03` · **Tests:** no TEST-* IDs; `tests/test_models.py`
Effect/GameEvent/Note round-trips; `Effect.supersedes` references a prior Effect id and the
superseded Effect remains in history (INV-EFFECT-004); GameEvent int ids monotonic. Batched
with T-004/T-005; full suite 25 passed.
Post-close correction **I-003/D-008** adds UUID-preserving supersession and the minimal
Con-completion/adjudication persistence records (`9d0bc96`; full suite 32 passed).
**Brief:** `archive/tasks/T-004-domain-models.md`

### ✅ T-007 — Database engine and session lifecycle
`WBS 2.2.1` · `TRD §26` · after T-006
SQLModel engine, table creation, session lifecycle, the temp-DB path used by tests.
**Done when:** tables create from a clean file and the test harness gets an isolated DB.
**Closed:** 2026-09-11 · **Commit:** `72835dd` · **Tests:** no TEST-* IDs; 5 in
`tests/test_database.py` (registered-table creation vs `sqlite_master`, idempotent re-run,
`session_scope` round-trip, explicit-path + parent-dir creation, settings-driven default
engine); harness refactored onto the factory; full suite 37 passed, no OpenRouter key.
Creates the 11 tables registered today — the 9 TRD §7.1–7.9 entities + 2 D-008 records;
`resource`/`agent-run` metadata (TRD §26) arrive with T-045/T-053.
**Brief:** `archive/tasks/T-007-database-engine.md`

### ✅ T-008 — Persistence operations and transactions
`WBS 2.2.2–2.2.3` · `TRD §26` · `INV-GAME-004`
Create/read/update operations for every entity. **All state-changing game operations run in
a single transaction that revalidates phase and Actor eligibility inside the transaction.**
**Done when:** a game survives process restart with no reliance on in-memory state; a test
proves a failed operation leaves no partial write.
**Closed:** 2026-09-11 · **Commit:** `a5efbc3` · **Tests:** no TEST-* IDs; 11 in
`tests/test_persistence.py` (nine-entity round-trip + list-by-game/argument + mutable-field
update; restart safety across a fresh engine; guarded-wrapper atomic rollback, in-transaction
revalidation refuse/commit, no-double-advance INV-GAME-005, absent-game fail-closed); full
suite 48 passed, no OpenRouter key. New `app/persistence.py` on `session_scope` (TRD §5);
no hard-delete op (INV-EFFECT-004); ConParticipation/AdjudicationRecord ops deferred to
T-016/T-019.
**Brief:** `archive/tasks/T-008-persistence-operations.md`

### ✅ T-009 — Scenario manifest schema and validation
`WBS 2.3.1–2.3.2` · `scenario-format §4–§11, §19` · after T-008
Pydantic validation of scenario metadata, `game` config (incl. `primary_arguments_per_actor`),
Actor definitions, turn order and starting Effects. Reject duplicate Actor IDs, unknown
Actors in turn order, invalid visibility, invalid turn counts, malformed starting Effects,
with the documented error shapes.
**Done when:** `TEST-SCN-001`, `TEST-SCN-002` pass.
**Closed:** 2026-09-11 · **Commit:** `14f4bbd` · **Tests:** `TEST-SCN-001`, `TEST-SCN-002`
pass + 15 focused §6/§11/§19 rule tests in `tests/test_scenarios.py`. `app/scenarios.py`:
dup-key-aware YAML boundary, strict Pydantic-v2 schema, parse/validation error separation.
Resource materialisation (SCN-003/004/005) deferred to T-045. D-009 logged. Batched with
T-010/T-011; full suite 74 passed, no key.
**Brief:** `archive/tasks/T-009-011-scenario-to-game.md`

### ✅ T-010 — Canonical deterministic test fixture
`testing.md §4` · `scenario-format §18` · after T-009
`tests/fixtures/deterministic-game/`: 3 Actors, 6 turns, 1 primary Argument per Actor/turn,
private and public Messages and Resources, starting Effects, fixed turn order, known
FakeUmpire results, fixed seed. **This fixture is authoritative — changing it means updating
every expected output.**
**Done when:** the fixture validates against the loader and is documented in the tests README.
**Closed:** 2026-09-11 · **Commit:** `14f4bbd` · **Tests:** 2 in `tests/test_scenarios.py`
(fixture validates through the loader; turn_order matches Actors). Fixture pins seed 424242,
turn order `[arkania, belvar, cestia]`, public+private starting Effects and Resource
declarations with backing files; authoritative role + deferrals (FakeUmpire → T-018, Resource
loading → T-045, runtime Messages → T-025) documented in `tests/fixtures/README.md`.
**Brief:** `archive/tasks/T-009-011-scenario-to-game.md`

### ✅ T-011 — Game creation from scenario
`WBS 2.3.3` · `scenario-format §2, §11` · after T-010
Build Game, Actors, Seats and starting Effects from a validated scenario package.
**Done when:** a valid scenario directory produces a playable game in `LOBBY`.
**Closed:** 2026-09-11 · **Commit:** `14f4bbd` · **Tests:** 9 in `tests/test_scenarios.py`
(LOBBY game; execution-rule snapshot; Actor snapshots round-trip; one Seat/Actor, hash-only;
starting Effects persisted; fresh-engine reload; seed required; **atomic rollback** on injected
mid-creation failure — INV-GAME-004). `create_game_from_scenario` runs in one `session_scope`
transaction; raw Seat tokens returned out-of-band via new `app/security.py` (T-028 extends it),
never persisted (INV-ACTOR-001). Game start deferred to T-013; full suite 74 passed, no key.
**Brief:** `archive/tasks/T-009-011-scenario-to-game.md`

> ### ✅ GATE 0 CHECK — run `/gate-check 0`
> Fresh checkout installs · pytest runs · fixture scenario loads · temporary database works.
> (`testing.md §23`)

---

# Gate 1 — Engine Proven (MVP-0)

*Is the game engine correct without relying on AI?*

### ✅ T-012 — Visibility service
`WBS 2.5` · `state-machine §5, §8–§10` · `TRD §36` · after T-011
**One** service answering "what may this Actor see". Message visibility, Actor private
briefs/objectives/information, Effect filtering, Note isolation. No endpoint-local filtering
anywhere in the codebase.
**Done when:** `TEST-VIS-001`, `TEST-VIS-002`, `TEST-VIS-003`, `TEST-NOTE-001`,
`TEST-NOTE-002` pass.
**Closed:** 2026-09-11 · **Commit:** `7e917a6` · **Tests:** `TEST-VIS-001/002/003`,
`TEST-NOTE-001/002` pass + 14 focused tests in `tests/test_visibility.py` (Actor
private-state isolation across three Actors; public/private Effect with no hidden-metadata
leak; cross-game isolation with identical Actor IDs; fail-closed on unknown Actor/Game).
New `app/visibility.py` is the single Actor-facing decision path — `require_actor`,
`get_actor_view`, `visible_messages/effects/notes`, `can_view_message/effect`; game-scoped
at the query boundary; one predicate per row reused by list + predicate entry points
(no drift). `active_only` filters the stored flag only — expiry stays with T-013/T-020.
Security review: no duplicate Actor-facing visibility path (scenario validation is load-time
schema only). Full suite 93 passed, no key.
**Brief:** `archive/tasks/T-012-visibility-service.md`

### ✅ T-013 — Game start and turn start
`WBS 2.4.1` · `state-machine §14, §15` · after T-012
`LOBBY → DIPLOMACY`; turn-start bookkeeping and Effect expiry evaluation at turn boundaries.
**Done when:** `TEST-PHASE-001` passes.
**Closed:** 2026-09-11 · **Commit:** `b919805` · **Tests:** `TEST-PHASE-001` pass + 6 focused
tests in `tests/test_game_service.py` (illegal/repeat start & non-lobby refusal leaving state
unchanged, missing-Seat and invalid-turn_order guards, absent-game `GameNotFound`, Effect
expiry at the turn-start boundary — deactivates `expires_turn<=turn`, retains the row, leaves
`None`/later/already-inactive Effects untouched, survives reload). New `app/game_service.py`
`start_game` + `_start_turn`/`_expire_effects` via T-008 `apply_guarded`. Expiry boundary
logged as **D-010**. Batched with T-014; full suite 118 passed, no key.
**Brief:** `archive/tasks/T-013-014-game-start-diplomacy.md`

### ✅ T-014 — Diplomacy phase
`WBS 2.4.2` · `state-machine §16` · `api.md §7, §12`
Public and private Messages, per-Actor readiness, phase closure on all-ready. **A new private
Message to an already-ready recipient clears that recipient's readiness before closure.**
**Done when:** `TEST-PHASE-002`, `TEST-PHASE-003` pass.
**Closed:** 2026-09-11 · **Commit:** `b919805` · **Tests:** `TEST-PHASE-002`, `TEST-PHASE-003`
pass + 17 focused tests in `tests/test_game_service.py` (public→all & private→sender/recipient
visibility via the T-012 service; message shape/recipient/phase rejection; readiness persists;
all-ready closes to ARGUMENT on first turn-order Actor; private-message readiness reset incl.
multiple recipients, public-message no-reset, and the stale-set boundary race). New
`app/game_service.py` `send_message` + `ready` + `_close_diplomacy_if_ready`. No visibility
rule duplicated; no events (T-022)/timeout/force-advance. Batched with T-013; full suite 118
passed, no key.
**Brief:** `archive/tasks/T-013-014-game-start-diplomacy.md`

### ✅ T-015 — Argument phase
`WBS 2.4.3` · `state-machine §17` · `INV-ARG-001…005`
Active-Actor enforcement, primary Argument submission, required structure (action, desired
Effect, ≥1 Pro, Pro ceiling), budget from `game.primary_arguments_per_actor`, one current
Argument at a time.
**Done when:** `TEST-ARG-001` … `TEST-ARG-008` pass.
**Closed:** 2026-09-11 · **Commit:** `5223793` · **Tests:** TEST-ARG-001…008,
TEST-PHASE-004; focused Argument/Cons file 45 passed; full suite 163 passed, no key.
Budget values 1 and 2, fresh-session persistence, rollback and repeated Actor-ID cross-game
isolation covered. Batched with T-016 (shared lifecycle; separately evidenced).
**Brief:** `archive/tasks/T-015-argument-phase.md`

### ✅ T-016 — Cons phase
`WBS 2.4.4` · `state-machine §18` · `INV-CON-001…006`
Eligible opposing Actors, Con submission, no self-Con, maximum Cons, explicit `pass` closing
participation, Cons bound to the current Argument only.
**Done when:** `TEST-CON-001` … `TEST-CON-005`, `TEST-PHASE-004`, `TEST-PHASE-005` pass.
**Closed:** 2026-09-11 · **Commit:** `5223793` · **Tests:** TEST-CON-001…005,
TEST-PHASE-004/005; focused Argument/Cons file 45 passed; full suite 163 passed, no key.
Limits 0/1/3, fresh-Engine pass persistence, atomic final closure/rollback and repeated
Actor-ID cross-game isolation covered. Batched with T-015 (shared lifecycle; separately
evidenced). Stops at `ADJUDICATION`; T-017 not started.
**Brief:** `archive/tasks/T-016-cons-phase.md`

### ✅ T-017 — RNG
`WBS 2.7` · `TRD §24` · `INV-RNG-001…004` · after T-016
Deterministic per-game RNG seeded from the scenario/run seed. `roll = rng.random();
success = roll <= probability`. Probability, roll and result recorded on every draw.
**The server owns every draw. The Umpire never sees or chooses one.**
**Done when:** `TEST-RNG-001`, `TEST-RNG-002`, `TEST-RNG-003` pass.
**Closed:** 2026-09-11 · **Commit:** `72b5276` · **Tests:** TEST-RNG-001/002/003;
tests/test_rng.py 4 passed + probabilistic-draw/restart coverage in test_adjudication.py;
full suite 196 passed, no key. `app/rng.py` reconstructs the draw from `seed + rng_draw_index`
(restart-safe); draw+increment commit inside the adjudication transaction (INV-RNG-003).
Automatic modes consume no draw; `roll == probability` → success verified. Batched with
T-018/019/020 (shared resolution pipeline; separately evidenced).
**Brief:** `archive/tasks/T-017-T-020-resolution-pipeline.md`

### ✅ T-018 — Umpire interface and FakeUmpire
`WBS 2.8` · `agents.md §21, §22, §24` · after T-017
Minimal structured adjudication interface plus a deterministic in-process implementation
supporting automatic success, automatic failure, probabilistic mode, and success/failure
Effect proposals.
**Done when:** the engine can complete a game with no model provider configured.
**Closed:** 2026-09-11 · **Commit:** `72b5276` · **Tests:** tests/test_umpire.py 7 passed;
full suite 196 passed, no key. Framework-neutral `app/umpire.py` (`AdjudicationProposal`,
`AdjudicationContext`, `Umpire` protocol) + `agents/umpire.py` `FakeUmpire`; the engine
resolves fully offline (FakeUmpire performs no RNG, carries no roll). Verified: importing the
`app/` resolution path pulls in no `pydantic_ai` and no `agents` package. Batched with
T-017/019/020.
**Brief:** `archive/tasks/T-017-T-020-resolution-pipeline.md`

### ✅ T-019 — Adjudication phase and server resolution
`WBS 2.4.5` · `state-machine §19` · `agents.md §26` · `INV-ADJ-001…003`
Freeze player state-changing operations, request adjudication, validate the structured
result, draw the roll server-side, select the branch. Single authoritative resolution.
**Done when:** `TEST-ADJ-001` … `TEST-ADJ-006` pass.
**Closed:** 2026-09-11 · **Commit:** `72b5276` · **Tests:** TEST-ADJ-001…006; full suite
196 passed, no key. `resolve_adjudication` validates the single adjudicating Argument and the
proposal, owns the probabilistic roll (`roll <= probability`), persists exactly one
`AdjudicationRecord` (PK `argument_id` + service guard → INV-ADJ-003), and rejects a second
resolution with no extra draw/record/Effect. Invalid proposals roll back with no partial
mutation and no RNG consumed; player mutation frozen (make_con after adjudication rejected).
Phase stays `adjudication`; advancement is T-021, not started. Batched with T-017/018/020.
**Brief:** `archive/tasks/T-017-T-020-resolution-pipeline.md`

### ✅ T-020 — Effect application, supersession and expiry
`WBS 2.4.6` · `state-machine §21` · `INV-EFFECT-001…005`
Create authoritative Effects from the resolved branch. Supersession **deactivates**, never
deletes. Expiry evaluated at the documented boundary.
**Done when:** `TEST-EFF-001` … `TEST-EFF-005` pass.
**Closed:** 2026-09-11 · **Commit:** `72b5276` · **Tests:** TEST-EFF-001…005; full suite
196 passed, no key. Only the selected branch materialises Effects (unselected branch retained
as audit data on the record, not applied); provenance = source Argument + creation turn +
public default visibility. Supersession deactivates & retains targets, recording the link on
the primary created Effect; unknown/cross-game (TEST-ADJ-006)/duplicate/already-inactive
targets rejected (D-011). Expiry stays the single `_expire_effects` boundary (D-010). Batched
with T-017/018/019.
**Brief:** `archive/tasks/T-017-T-020-resolution-pipeline.md`

### ✅ T-021 — Actor advancement, turn sequencing and completion
`WBS 2.4.7–2.4.9` · `state-machine §20, §22` · after T-020
Advance through the turn order, advance the turn, complete the game at `turn == max_turns`.
**Done when:** `TEST-PHASE-006`, `TEST-PHASE-007`, `TEST-PHASE-008` pass.
**Closed:** 2026-09-11 · **Commit:** `6f4a2f8` · **Tests:** `TEST-PHASE-006…008` + off-by-one,
restart, terminal-mutation and invalid-advancement rejection (`tests/test_sequencing.py`,
15 tests); full suite 221 passed. `advance_after_resolution` is the one authoritative
advancement path (by `turn_order` position; Consequences disabled → immediate turn completion;
reuses `_start_turn`; completes at `turn == max_turns` with no overshoot). Batched with T-022.
**Brief:** `archive/tasks/T-021-T-022-sequencing-events.md`

### ✅ T-022 — Event log
`WBS 2.1.8` · `state-machine §24` · after T-021
Emit the minimum expected events for every transition and player action, in order, with the
payloads the contract requires.
**Done when:** a full game produces the documented event sequence and event IDs are
monotonic.
**Closed:** 2026-09-11 · **Commit:** `6f4a2f8` · **Tests:** monotonic ids, per-mutation events
with payloads, full §24 single-turn sequence, event/state rollback atomicity, cross-game
isolation (`tests/test_events.py`, 10 tests); full suite 221 passed. `_emit_event` appends an
append-only `GameEvent` in the same transaction as each mutation (TRD §7.8; not event
sourcing). Batched with T-021.
**Brief:** `archive/tasks/T-021-T-022-sequencing-events.md`

### ✅ T-023 — Idempotency
`WBS 2.6.1–2.6.2` · `api.md §4` · `INV-IDEM-001…003` · after T-022
Idempotency keys for Message, Argument, Con, pass, ready and Note save. Replay returns the
original result; a reused key with a different payload is a conflict.
**Done when:** `TEST-IDEM-001` … `TEST-IDEM-004` pass.
**Closed** 2026-09-11 · commit `e6d5ec6` · `TEST-IDEM-001…004` + 7 supporting tests pass
(237 total, no key) · Brief `archive/tasks/T-023-T-024-idempotency-concurrency.md`

### ✅ T-024 — Concurrency and race guards
`WBS 2.6.3` · `state-machine §25` · `INV-GAME-005`
Prevent simultaneous Arguments, double phase closure and double adjudication callbacks under
concurrent requests.
**Done when:** `TEST-RACE-001`, `TEST-RACE-002`, `TEST-RACE-003` pass.
**Closed** 2026-09-11 · commit `e6d5ec6` · `TEST-RACE-001…003` pass (audit — the T-013…T-021
in-transaction guards already satisfy §25; no production change needed) · Brief
`archive/tasks/T-023-T-024-idempotency-concurrency.md`

### ✅ T-025 — ScriptedPlayers
`WBS 2.9.1–2.9.2` · `testing.md §18` · after T-024
Deterministic scripted behaviour for three Actors covering messages, Arguments, Cons, pass
and ready.
**Done when:** each scripted Actor drives a full turn without manual intervention.
**Closed** 2026-09-11 · commit `a65b58c` · `agents/scripted.py` (`ScriptedPlayer`,
`build_scripted_players`, `run_deterministic_game`, `deterministic_umpire`) · evidenced by
`tests/test_scripted_players.py` (4 tests: determinism, private-information use, the four §18
player requirements, offline completion) · no `app/` change, no agent-framework import · Brief
`archive/tasks/T-025-T-026-scripted-integration.md`

### ✅ T-026 — Deterministic six-turn integration test
`WBS 2.9.3` · `testing.md §18` · after T-025
The authoritative engine integration test over the canonical fixture.
**Done when:** three ScriptedPlayers and FakeUmpire complete six turns with correct
visibility, sequencing and Effects — semantically identical after normalizing generated IDs
and timestamps, or byte-identical under an injected deterministic clock/ID source.
**Closed** 2026-09-11 · commit `a65b58c` · `tests/test_integration_six_turn.py` (7 tests):
all nine §18 assertions — game completes / turn==6 / 18 Arguments / no budget violations / 18
adjudications / RNG matches reconstructed `random.Random(seed)` stream / final active Effects
match golden schedule / no private-data leak via `app.visibility` / event sequence structurally
valid. Normalized-id determinism (verified stable across repeated runs). Full suite green (248).
This is the Gate 1 exit run. · Brief `archive/tasks/T-025-T-026-scripted-integration.md`

> ### ✅ GATE 1 CHECK — run `/gate-check 1`
> Deterministic six-turn integration test passes. No external model required.

---

# Gate 2 — Autonomous Game Proven (MVP-1)

*Do autonomous games actually work and produce interesting behaviour?*

### ✅ T-027 — FastAPI application skeleton
`WBS 3.1` · `api.md §2, §3, §5` · after T-026
App wiring, the documented error envelope, common response fields, request logging.
**`app/` must not import PydanticAI** (TRD §4).
**Done when:** the app starts, returns the documented envelope on a forced error, and an
import test asserts no agent-framework dependency inside `app/`.
**Closed:** 2026-09-11 · **Commit:** `1b66745` · **Tests:** `tests/test_api_app.py` 8 passed
(create_app factory; forced-error/validation/domain-map/unmapped-fallback/unknown-route all
render the §3 envelope; common-fields helper; no `pydantic_ai` import). `AMAW_ADMIN_TOKEN` added
to `Settings` + `.env.example`. Full suite 266 passed, no key. Batched with T-028 (shared
`app/api.py` + api.md §2/§3 boundary; separately evidenced). Cross-game 403 logged as **D-012**.
**Brief:** `archive/tasks/T-027-T-028-api-skeleton-auth.md`

### ✅ T-028 — Seat authentication
`WBS 3.2` · `api.md §2` · `TRD §16` · `INV-ACTOR-001`
Cryptographically random Seat tokens, hashes stored (never raw), bearer → game → Actor
resolution, enforcement on every player endpoint. Actor identity comes from the token; a
request body can never assert it.
**Done when:** `TEST-AUTH-001` … `TEST-AUTH-004` pass.
**Closed:** 2026-09-11 · **Commit:** `1b66745` · **Tests:** `TEST-AUTH-001…004` pass +
`tests/test_auth.py` 11 passed (missing/unknown/malformed token → 401; body `actor_id` cannot
impersonate — INV-ACTOR-001; cross-game token → 403; admin-token separation → 403/401; Seat
token never in logs — testing.md §24). `seat_identity`/`require_admin` on `app.security.hash_token`
+ `secrets.compare_digest`; no second token scheme. Full suite 266 passed, no key. Batched with
T-027 (separately evidenced). Real player/admin endpoints (T-029+) not started.
**Brief:** `archive/tasks/T-027-T-028-api-skeleton-auth.md`

### ✅ T-029 — Context endpoint
`WBS 3.1.1` · `api.md §6.1` · `agents.md §4` · after T-028
`GET /games/{game_id}/context` — turn, phase, active Actor, Actor-specific context, visible
Effects, available actions. All filtering via the visibility service from T-012.
**Done when:** each Actor receives only its own authorised context; no field leaks another
Actor's private state.
**Closed** 2026-09-11 · commit `daf9cb3` · `app/context.py` server-computed read-model;
`you`/`effects` via `app.visibility`, `available_actions`/`requires_input` per Actor/phase
(API-INV-009); D-013 (Resource actions omitted). tests/test_api_context.py (9). No-leak asserted
(agents.md §4 / TEST-VIS-003). Suite 327 passed. Brief:
`archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-030 — Message endpoints
`WBS 3.1.2` · `api.md §7.1, §7.2` · after T-029
`GET`/`POST /games/{game_id}/messages`, including the readiness-clearing rule from T-014.
**Closed** 2026-09-11 · commit `daf9cb3` · visible-only GET via `app.visibility`; sender from
token (INV-ACTOR-001); idempotent (API-INV-007); readiness-clearing re-asserted (TEST-PHASE-003);
D-015 (`after` deferred to `/events`). tests/test_api_messages.py (11), incl. TEST-VIS-001/002/003.
Brief: `archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-031 — Argument endpoint
`WBS 3.1.3` · `api.md §8.1` · after T-030
`POST /games/{game_id}/arguments`, enforcing the T-015 invariants at the API boundary.
**Closed** 2026-09-11 · commit `daf9cb3` · active-Actor only (API-INV-003), structural + phase
validation mapped to the envelope. tests/test_api_arguments.py (8) re-assert TEST-ARG-001…007.
Brief: `archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-032 — Con and pass endpoints
`WBS 3.1.4` · `api.md §9.1, §9.2` · after T-031
`POST /arguments/{argument_id}/cons` and `/pass`. `pass` is distinct from `ready`.
**Closed** 2026-09-11 · commit `daf9cb3` · path `argument_id` is the target, not identity; pass
is its own endpoint; all-passed closes Cons via the service (not a client advance — API-INV-008).
tests/test_api_cons.py (8) re-assert TEST-CON-001…005. Brief:
`archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-033 — Notes endpoints
`WBS 3.1.5` · `api.md §11` · `INV-NOTE-001, INV-NOTE-002` · after T-032
`GET`/`POST /games/{game_id}/notes`. Private to the owning Actor. No chain-of-thought
requirement is imposed on the agent.
**Closed** 2026-09-11 · commit `daf9cb3` · new `game_service.save_note` (idempotent via the
reserved `save_note` op; `InvalidNote`/`NoteResult`; datetime round-trip for replay); reads via
`visibility.visible_notes`. tests/test_notes.py (4) + tests/test_api_notes.py (7),
TEST-NOTE-001/002. Brief: `archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-034 — Ready endpoint
`WBS 3.1.6` · `api.md §12.1` · after T-033
`POST /games/{game_id}/ready`, legal only where readiness is legal.
**Closed** 2026-09-11 · commit `daf9cb3` · legal only in diplomacy; all-ready closes to Argument
(the service's own transition); keyed ready idempotent, keyless not. tests/test_api_ready.py (7).
Brief: `archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-035 — Event polling endpoint
`WBS 3.1.7` · `api.md §13.1` · after T-034
`GET /games/{game_id}/events?after={event_id}`, visibility-filtered. SSE is not required.
**Closed** 2026-09-11 · commit `daf9cb3` · new `visibility.can_view_event`/`visible_events`
(D-014 policy): public lifecycle events to all, `message_sent` to its parties, effect events by
Effect visibility; `last_event_id` advances over visible events only. Side-channel closed
(TEST-VIS-003 events vector). tests/test_api_events.py (7). Brief:
`archive/tasks/T-029-T-035-player-endpoints.md`.

### ✅ T-036 — API integration test
`testing.md §19` · after T-035
A full game driven entirely over HTTP with Seat tokens.
**Done when:** the API-level game test passes with the same outcomes as T-026.
**Closed** 2026-09-11 · commit `489e313` · `tests/test_integration_api.py` (10, `test_int_api_19_*`):
the T-026 six-turn run with the Actor half over HTTP (`TestClient` + service sharing one
`db_engine`; hybrid harness — Actor moves over the T-029…T-035 endpoints, create/start/resolve/
advance through the service as T-042/T-043 stand-ins, API-INV-008). Asserts documented HTTP
status/shapes (api.md §6–§13), the nine `testing.md §18` outcomes (complete · turn 6 · 18
Arguments, no budget violation · 18 adjudications · RNG == `random.Random(seed)` stream · golden
final Effects · valid event stream), no private-data leak **through the endpoints** (new coverage
over T-026), TEST-PHASE-003 reset over HTTP, and determinism across two runs. No production change.
`uv run pytest` green (337, no key). Brief: `archive/tasks/T-036-api-integration-test.md`.

### ✅ T-037 — Agent tool layer
`WBS 3.3` · `agents.md §5–§16` · after T-036
Thin HTTP tools: `observe`, `read_messages`, `send_message`, `make_argument`, `make_con`,
`pass_con`, `get_notes`, `save_note`, `ready`. Exact schemas and structured error returns per
the contract. Tools carry no game logic.
**Done when:** every required game action is reachable through tools, and recoverable errors
return structured guidance rather than exceptions.
**Closed** 2026-09-11 · commit `a53e5ab` (batched with T-038) · `agents/tools.py`
(`PlayerTools`/`PlayerToolContext`/`ToolResult`; imports only httpx + stdlib). Identity from the
Seat token only (INV-ACTOR-001), `observe` returns server `available_actions` verbatim
(API-INV-009), api.md §3 rejections → structured `ToolResult` (infra still raises). 12 tests
in `tests/test_agent_tools.py` (all reachable, auth, no-actor_id, no-leak, structured errors,
no app-internal imports, token never logged, full turn over HTTP). Suite 362 green. Brief:
`dev/archive/tasks/T-037-T-038-agent-tools-model-client.md`.

### ✅ T-038 — OpenRouter model client
`TRD §6` · `agents.md §19` · after T-037
Model gateway client with per-Actor model configuration, timeouts, and a single retry on
transient provider failure. Lives outside `app/`.
**Done when:** a model call succeeds against a configured model and records requested vs
returned model identifiers.
**Closed** 2026-09-11 · commit `a53e5ab` (batched with T-037) · `agents/model_client.py`
(`OpenRouterClient`/`ModelConfig`/`ModelResult`/`ModelClientError`/`ProviderErrorCategory`) on
httpx (D-016 — not PydanticAI). Per-player model, timeout, exactly one transient retry / none
deterministic, requested-vs-returned model + provider + usage + latency; key/timeout from
`Settings`, key never logged. 13 tests + 1 `@pytest.mark.llm` (skips keyless) in
`tests/test_model_client.py`, driven by an `httpx.MockTransport`. Suite 362 green (was 337).
Brief: `dev/archive/tasks/T-037-T-038-agent-tools-model-client.md`.

### ✅ T-039 — PydanticAI reference player
`WBS 3.4.1–3.4.3, 3.4.5` · `agents.md §2, §17` · `docs/prompts/player-v1.1.md` · after T-038
Reference PydanticAI player bound to a Seat token, wired to the versioned neutral player prompt.
Each player receives only its own brief, objectives, private information, Messages, Notes and
visible Effects.
**Done when:** one autonomous agent controls one Actor through a full turn.
**Closed** 2026-09-11 · commit `0532cbc` (batched with T-040) · `agents/player.py`
(`ReferencePlayer`/`PlayerConfig`/`ActivationResult`/`load_player_prompt`/`build_openrouter_model`).
Bound to one Seat/game/Actor; per-player model; versioned neutral prompt (`player-v1.1`); only the
9 player-safe tools exposed; **fresh, bounded** context per activation (no replayed history);
persistent memory via Notes/server state; identity from the Seat token (INV-ACTOR-001); HTTP-only
(imports no `app` internals). Exit evidenced by
`test_one_player_controls_one_actor_through_a_full_turn`. 9 tests + 1 `@pytest.mark.llm`
(skips keyless) in `tests/test_player.py`. Suite 383 green (was 362).
Brief: `dev/archive/tasks/T-039-T-040-reference-player-lifecycle.md`.

### ✅ T-040 — Agent lifecycle loop
`WBS 3.4.4` · `agents.md §3, §4` · after T-039
`observe → decide whether input is required → invoke → allow tool use → wait/poll → repeat`.
**Done when:** an agent participates correctly across all phases without being told which
phase it is in.
**Closed** 2026-09-11 · commit `0532cbc` (batched with T-039) · `agents/lifecycle.py`
(`LifecycleRunner`/`CycleOutcome`/`ActivationLimitExceeded`). Python gates the model on the
server's `requires_input`/`available_actions` (no wasted "do I act?" call); re-observes after
every activation so it follows phase changes and recovers from stale/recoverable rejections
without local corruption; a minimal activation ceiling is the T-041 seam. Exit evidenced by
`test_no_external_phase_instruction_is_hardcoded` + the per-phase tests +
`test_lifecycle_follows_phase_changes_by_reobserving` +
`test_three_players_orchestrated_through_a_full_turn`. 12 tests in `tests/test_lifecycle.py`.
Suite 383 green. D-017 (pydantic-ai → `dev` group; order-independent boundary tests).
Brief: `dev/archive/tasks/T-039-T-040-reference-player-lifecycle.md`.

### ✅ T-041 — Agent guardrails and stall recovery
`WBS 3.5` · `agents.md §18` · `TRD §35` · after T-040
Maximum model iterations, tool calls and Messages; request timeout; text-length limits;
structured tool errors; one provider retry; on continued stall, log the failure and
auto-ready or auto-pass where legal.
**Done when:** a deliberately failing agent cannot hang a game, and the failure is recorded
rather than silently swallowed.
**Closed:** 2026-09-12 · **Commit:** `fd7194c` · **Tests:** no TEST-* IDs (exit-condition task);
`tests/test_guardrails.py` 11 pass — per-activation iteration/tool-call ceilings, text/size
validators + structured-error-visible-to-model, exactly-one-transient-retry / none-deterministic,
per-phase message cap across activations, stall→auto-ready (diplomacy) / auto-pass_con (Cons),
failed-arguer records-and-yields (no fabrication), and the three-player orchestration where a
deliberately failing agent cannot hang the game (turn completes, failures recorded). Guardrails in
`agents/player.py` (`GuardrailConfig` via PydanticAI `UsageLimits`; text/size validators; one
retry reusing T-038 classification — D-018), recovery in `agents/lifecycle.py` (`AgentFailure`
provenance, converted no-progress stall seam, legal auto-ready/auto-pass or record-and-yield —
D-019). Full suite 394 passed (was 383), no key; `app/` imports no agent framework.
**Brief:** `archive/tasks/T-041-agent-guardrails-stall-recovery.md`

### ✅ T-042 — AI Umpire
`WBS 3.6` · `agents.md §20–§27` · `prompts/umpire-v1.1.md` · after T-041
Fixed versioned adjudication prompt; context builder (scenario summary, turn/timescale,
active Effects, Argument, Pros, Cons, authorised hidden information); validated structured
response (`mode`, `probability`, `rationale`, success/failure Effects,
`supersedes_effect_ids`). Server performs the RNG and creates Effects. One retry; if still
unavailable, pause or fail the game — **never fabricate a result**.
**Done when:** real LLM adjudication drives a turn with server-owned outcomes, and an
Umpire outage produces a paused game rather than an invented one.
**Closed:** 2026-09-12 · **Commit:** `78b49f9` · **Tests:** no TEST-* IDs (exit-condition task);
`tests/test_ai_umpire.py` 26 offline + 1 `@pytest.mark.llm` (skips keyless) — reproduces
`TEST-ADJ-001…006` / `TEST-RNG-003` parity through the AI path, malformed responses refused by the
server with no partial write, exactly-one-transient-retry / none-deterministic, outage → paused
(no draw/record/Effect, `game_paused` emitted, no double-draw, API `503 adjudicator_unavailable`),
and `agents.umpire` imports no agent framework. `AIUmpire` (`agents/umpire.py`) on the T-038
`OpenRouterClient` (httpx, not PydanticAI — **D-020**); `UmpireUnavailable` in `app/umpire.py` →
`resolve_adjudication` pauses → `AdjudicatorUnavailable` → `503`. Spec defect **D-021** fixed in
`state-machine.md` (commit `1a7b950`). Full suite 420 passed (was 394), no key.
**Brief:** `archive/tasks/T-042-ai-umpire.md`

### ✅ T-043 — Admin endpoints and JSON game export
`WBS 3.7` · `api.md §14, §15.1` · `TRD §27` · after T-042
`uv run amaw export GAME_ID` and the admin export endpoint. Includes scenario/version, Actor
and model assignment, seed, Messages, Arguments, Cons, adjudications, RNG rolls, Effects,
Events, objectives, **private Notes with ownership and visibility metadata**, resource and
configuration hashes, agent failures and timestamps. Admin auth is separate from Seat tokens.
**Done when:** `TEST-NOTE-003` passes and a complete game is inspectable outside the app.
**Closed:** 2026-09-12 · **Commit:** `f0fb02b` · **Tests:** `TEST-NOTE-003` +
`TEST-AUTH-004` parity for §14/§15 routes; `tests/test_export.py` 14 pass (export completeness/
no-leak, endpoint==CLI, advance resolves-then-advances matching the service, read-only export,
`app.export`/`amaw_cli` import no agent framework); full suite 434 passed, no key. One framework-
free builder (`app/export.py`) feeds both `GET /admin/games/{id}/export` and `amaw export`;
unfiltered (never through `app.visibility`); §14 create/start/advance are thin `require_admin`
wrappers over `game_service`/`scenarios` (Seat token → 403). Deferred, not faked: resource
hashes/access → T-045/T-050, run manifest + model assignments → T-053, agent errors → T-053.
Decisions **D-022** (pause/end deferred), **D-023** (agent-error persistence → T-053),
**D-024** (admin advance resolves-then-advances with an injected Umpire; no `resolve` route),
**D-025** (`amaw` CLI outside `app/`).
**Brief:** `archive/tasks/T-043-admin-export.md`

### ✅ T-044 — AI smoke test
`WBS 3.8` · `testing.md §20` · after T-043
Three AI Actors, one fixed AI Umpire, six turns, zero human interventions. Marked
`@pytest.mark.llm`.
**Done when:** the smoke test completes without information leakage or state corruption.
**Closed:** 2026-09-12 · **Commit:** see merge of `task/T-044-ai-smoke-test` · **Tests:** no TEST-* IDs
(exit-condition task); `tests/test_ai_smoke.py` 5 offline + 1 `@pytest.mark.llm` — provider run 6
(game `db54f331`, `scenarios/northern-strait/`) `1 passed in 1:11:10`: 6 turns, 18 adjudications,
0 failures/retries/interventions, seat isolation and hidden-fact leak checks held. Six provider
runs were needed; runs 1–5 each exposed a defect fixed in-branch (I-004…I-026 except I-022 —
`BACKLOG_RESOLVED.md`; behavioural record `RUNS.md`). Prompts `player-v1.2`/`umpire-v1.2`
(D-027/D-028), Umpire hidden information (D-029), `agents.md` 0.2.1. Deviation: the passing run
uses the new live-play scenario, not the fixture (I-024). Full suite 468 passed (was 420), no key.
**Brief:** `archive/tasks/T-044-ai-smoke-test.md`

> ### ✅ GATE 2 CHECK — run `/gate-check 2`
> Three LLM agents and a fixed LLM Umpire complete six turns with no human intervention and
> a full JSON export. **This is the first version worth showing anyone.**

---

# Gate 3 — Scenario Knowledge Proven (MVP-2)

*Can agents seek information without the engine becoming domain-specific?*

### ✅ T-045 — Generic Resource model and hashing
`WBS 4.1` · `scenario-format §12, §13` · `INV-RES-001` · after T-044
Resource metadata: ID, name, open `type`, inline content or file path, visibility, visible
Actors, tags, free-form metadata. Declarations loaded from `scenario.yaml`. At game creation,
compute SHA-256 for `scenario.yaml` and every referenced Resource file and persist a manifest
for provenance. Reject duplicate IDs, missing files and invalid visibility references.
**Done when:** `TEST-SCN-003`, `TEST-SCN-004`, `TEST-SCN-005` pass.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-045 Generic Resource model and hashing`) ·
**Tests:** `TEST-SCN-003/004/005` plus focused security, snapshot, model and export coverage:
duplicate/invalid visibility; missing/absolute/traversing/escaping-symlink/directory sources;
exact file + inline bytes; canonical order; source mutation/deletion independence; cross-game ID
reuse + within-game uniqueness; Resource-stage atomic rollback; path-free admin provenance.
Focused sets 70 and 82 passed; full suite 484 passed, 4 LLM tests deselected, no key. One frozen
`LoadedScenario`/`LoadedResource` boundary feeds atomic creation; Resource identity is `(game_id,
id)`. Game stores exact manifest bytes, its SHA-256, the canonical Resource manifest and its hash.
Decisions **D-030…D-033**.
**Brief:** `archive/tasks/T-045-generic-resource-model.md`

### ✅ T-046 — Resource formats
`WBS 4.2` · `scenario-format §14` · after T-045
Markdown/text, YAML, JSON and CSV. PDF, PNG/JPEG and GeoJSON as opaque attachments only if
trivial. **No OCR, embeddings or semantic indexing** (TRD §41).
**Done when:** each required format loads and exposes safe text where text exists.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-046 Resource formats`) · **Tests:** required
`.md`/`.txt`/`.yaml`/`.yml`/`.json`/`.csv` matrix; strict UTF-8 and malformed structured-input
rejection; stable original text; opaque PDF/PNG/JPEG/GeoJSON metadata; signature, extension,
basename and 2 MiB/1,000,000-character bounds; immutable persistence; additive-schema migration;
canonical fixture unchanged. Focused suite 105 passed; full suite 513 passed, 4 LLM tests
deselected, no key. Decisions **D-034/D-035**.
**Brief:** `archive/tasks/T-046-resource-formats.md`

### ✅ T-047 — Resource search
`WBS 4.3` · `api.md §10.1` · `INV-RES-002, INV-RES-003` · after T-046
`GET /games/{game_id}/resources/search?q=` across names, tags and text content.
**Visibility filtering happens before matching** — hidden Resources must not affect result
counts, ordering or any returned metadata.
**Done when:** `TEST-RES-001` … `TEST-RES-004` pass.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-047/T-048 Resource search and read`) ·
**Tests:** `TEST-RES-001…004`; public/private and hidden-only discovery; full-response hidden-row
non-interference; permitted-field and prohibited-field matching; case-folding; opaque attachments;
fixed ranking, Resource-ID tie-break, 20-result limit and centered 160-character snippets; blank
query envelope; game-status/auth/cross-game/unknown-Actor boundaries. Batched with T-048 at the
shared visibility/API security boundary. Focused Resource suite 63 passed; expanded API/auth suite
82 passed; full suite 530 passed, 4 LLM tests deselected, no key. Decision **D-036**.
**Brief:** `archive/tasks/T-047-T-048-resource-search-read.md`

### ✅ T-048 — Resource read
`WBS 4.4` · `api.md §10.2` · `INV-RES-004` · after T-047
`GET /games/{game_id}/resources/{resource_id}`. Unauthorised reads are rejected without
leaking hidden metadata. Reads never mutate authoritative state.
**Done when:** an unauthorised read is indistinguishable from a nonexistent Resource.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-047/T-048 Resource search and read`) ·
**Tests:** all-Actor public and authorised-private reads; identical hidden/missing/other-game-only
`404 not_found`; explicit text/attachment projections; forbidden-field non-leakage; same-ID
cross-game isolation; unknown-Actor/auth/status boundaries; complete database snapshot unchanged
across repeated search/read requests. Batched with T-047. Full suite 530 passed, 4 LLM tests
deselected, no key. Decision **D-036**.
**Brief:** `archive/tasks/T-047-T-048-resource-search-read.md`

### ✅ T-049 — Resource tools
`WBS 4.5` · `agents.md §9, §10, §29` · after T-048
`search_resources(query)` and `read_resource(id)` added to the player toolset and prompt so
information-seeking becomes an agent decision rather than a scripted step.
**Done when:** an agent independently searches and reads before arguing.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-049 Resource tools`) ·
**Tests:** behavioural exit `test_agent_can_research_before_arguing` (search→read→argue in order,
each research call a real success, not an unknown-tool retry); tool-layer reachability +
hidden-Resource `404 not_found` non-leakage; `available_actions` now advertising
`search_resources`/`read_resource` in diplomacy/argument/cons and absent when not running;
player tool-grant set updated (offline smoke). No dedicated `TEST-*` ID — WBS "done when" is
behavioural (TEST-RES-001…004 were T-047/T-048; the knowledge acceptance test is T-051/§21).
Full suite 534 passed, 4 LLM deselected, no key; `app/` remains agent-framework-free. Decision
**D-037** (no new player prompt version — v1.2 already describes the tool category).
**Brief:** `archive/tasks/T-049-resource-tools.md`

### ✅ T-050 — Resource access logging
`WBS 4.6` · `TRD §29` · `benchmark-methodology §17` · after T-049
Record game, Actor, turn, operation, search query, resource read and timestamp.
**Done when:** a full game yields a queryable access log suitable for behavioural comparison.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-050 Resource access logging`) ·
**Tests:** no dedicated `TEST-*` ID (WBS "done when" is behavioural); `tests/test_resource_access_log.py`
(10 tests) — search/read each capture operation/query/resource_id/turn/actor_id/timestamp; repeated
search appends a distinct row; zero-result search still logs; denied read + blank query log nothing
(D-038); attribution to the Seat Actor (INV-ACTOR-001); rows surface in the admin export, ordered by
monotonic id; read-before-Argument reconstructable. Trap updated: `test_api_resources.py` snapshot
scopes "unchanged" to authoritative tables and asserts the log grows append-only. Append-only
`ResourceAccess` (`app/models.py`, TRD §29) written server-side in the two Resource endpoints
(`app/api.py`), surfaced in `app/export.py` (stub replaced). Full suite 544 passed, 4 LLM deselected,
no key; `app/` remains agent-framework-free. Decision **D-038**.
**Brief:** `archive/tasks/T-050-resource-access-logging.md`

### ✅ T-051 — Scenario knowledge acceptance test
`WBS 4.7` · `testing.md §21` · after T-050
A scenario with public background, private intelligence, structured data, at least one
map/image attachment, and information absent from Actor briefs.
**Done when:** different agents independently retrieve their authorised material and no
private Resource leaks.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-051 Scenario knowledge acceptance test`) ·
**Tests:** `tests/test_scenario_knowledge.py` — 7 offline + 1 `@pytest.mark.llm`. New fixture
`tests/fixtures/knowledge-scenario/` carries all six §21 elements (public background, two
per-Actor private intel Resources, a CSV, a valid PNG map/image attachment, and brief-absent
codeword facts `GREYPETREL`/`TIDEHOLD`). Security assertions are release-blocking and offline:
TEST-RES-002 (private intel reaches only its owner; guessed-ID read `404 not_found`-safe),
TEST-RES-003 (unauthorised codeword search `count == 0`), TEST-RES-004 (no hidden
id/name/tags/path/snippet/metadata leak via search/context/events), plus TEST-RES-001 for the
public Resources and the WBS done-when (each agent retrieves exactly its authorised set, every
ordered pair). §21 behaviour proven offline (FunctionModel agent searches → reads → argues from
the retrieved brief-absent fact) and live (`test_scenario_knowledge_live`, skipped keyless).
**D-041**: the §21 scenario is a test fixture, golden `deterministic-game` untouched; no
production-code gap, no `I-0NN` filed. Full suite 559 passed, 5 deselected, no key.
**Brief:** `archive/tasks/T-051-scenario-knowledge-test.md`

### T-052 — Consequence agent **[optional — skipped/deferred]**
`TRD §25` · `agents.md §28` · after T-051
Optional narrative consequence generation within the consequences phase.
**Done when:** it can be enabled and disabled without affecting authoritative state.
**Skipped:** 2026-09-12 · Optional; deferred beyond MVP Gate 3 and not required for the Resource
search/read criterion.
*Post-release note 2026-09-22: authoritative Consequence Management is intentionally a new task,
T-074 (I-040, D-086), under the Matrix Scenario Portability milestone. T-052 remains recorded as
the skipped consequence-agent proposal and is not retroactively redefined.*

> ### ✅ GATE 3 CHECK — run `/gate-check 3`
> Generic Resource search and read work with strict visibility and full access logging.

---

# Benchmark provenance — foundation built during MVP

*Run provenance (T-053) is the one piece of benchmark infrastructure completed during the
MVP, recorded here in place. The benchmark proving itself — runner, rotation, modes, outputs
and the acceptance run — is now a standalone post-MVP milestone: see "Post-MVP Milestone —
Benchmarking / Experimental Evaluation" below.*

### ✅ T-053 — Run metadata and immutable provenance
`WBS 5.1` · `TRD §28` · `benchmark-methodology §4, §12, §13` · after T-052
Per-agent: model requested and returned, provider, prompt version, token usage, tool calls,
invalid calls, stalls, cost, latency, completion status, model parameters, reasoning
configuration, framework version, tool schema version, player prompt hash.
Per-game: an **immutable** run configuration manifest — scenario version and content hash,
per-resource hashes, Umpire configuration and prompt hash, code version/commit, benchmark
mode, assignment ID, repetition index.
**Done when:** two games are fully distinguishable by provenance alone.
**Closed:** 2026-09-12 · **Commit:** this commit (`T-053 Run metadata and immutable provenance`) ·
**Tests:** no dedicated normative `TEST-*` ID; `tests/test_run_provenance.py` 4 tests cover
immutable/idempotent manifests and conflict refusal, two-game distinction, trusted scenario +
Resource hashes, append-only player/Umpire execution metadata, admin-advance Umpire capture,
role/phase runtime/call/token/cost aggregation, partial-cost honesty, credential-field refusal and
export integration. The offline autonomous six-turn test now proves automatic manifest/call
capture and returns/logs the human post-game summary. Full suite 548 passed, 4 LLM deselected, no
key. **D-039** records per-execution rows + derived summaries. Started standalone before T-051/Gate
3 by explicit user direction; T-052 remains optional.
**Brief:** `archive/tasks/T-053-run-metadata-provenance.md`

---

# Release

*The shippable MVP: Gates 0–3 plus the release package. Benchmarking is deferred to the
post-MVP milestone below; the benchmark-facing parts of T-064/T-066 land once that milestone
delivers.*

### ✅ T-064 — Operational documentation
`WBS 6.3` · after T-053
Installation, environment variables, authoring a scenario, starting a game, selecting models,
running benchmarks, reading exports, known limitations.
The superseded draft material formerly at `docs/amaw-operational-docs-draft/` was archived under
`dev/archive/docs/` by T-066 after the reconciled guides shipped in `guide/`.
**Closed:** 2026-09-13 · **Commit:** this commit (`T-064 Operational documentation`) ·
**Tests:** no dedicated normative `TEST-*` ID; `tests/test_operational_docs.py` 2 tests cover the
shipped guide set, quick-start/CLI commands, every environment variable, scenario-authoring
essentials, benchmark deferral, developer-guide delegation and all local Markdown links. CLI help
was checked for `amaw`, `amaw run` and `amaw export`; full suite 561 passed, 5 LLM deselected, no
key. **D-042** places non-normative shipped guidance in `guide/`; normative `docs/` is unchanged.
**Brief:** `archive/tasks/T-064-operational-documentation.md`

### ✅ T-065 — Polished example scenario
`WBS 6.4` · `scenario-format §18, §21` · after T-064
Three Actors, conflicting objectives, private information, meaningful diplomacy and
Arguments, generic Resources, six turns — so a new user understands AMAW without authoring
anything first.
**Closed:** 2026-09-14 · **Commit:** this commit (`T-065 Polished example scenario`) ·
**Tests:** `tests/test_orison_scenario.py` 3 passed; focused scenario/docs boundary 79 passed;
full offline suite 669 passed, 6 LLM deselected. Production loading, Resource materialisation,
seeded LOBBY creation, package completeness and newcomer run instructions are covered; manual
§21 review recorded in the brief. Swedish v4 was intentionally retired, so three stale
current-scenario tests were reconciled to v5 by explicit user confirmation.
**Brief:** `archive/tasks/T-065-polished-example-scenario.md`

### T-062 — After-action report **[optional — skipped/deferred]**
`WBS 5.8` · `TRD §34` · after T-043
Human-readable summary: timeline, turning points, strategies, agreements, betrayals,
successful and failed Arguments, notable outcomes.
**Done when:** an AAR is generated from an export with no manual editing.
**Skipped:** 2026-09-15 · Optional; explicit release sequencing proceeded directly to T-066.
The machine-readable export/summary ships without fabricating a narrative AAR.

### ✅ T-066 — Release package
`WBS 6.5` · after T-062
Source, README, example environment file, example scenario, automated tests, CLI, JSON
export and a benchmark output example.
**Closed:** 2026-09-15 · **Commit:** this commit (`T-066 Release package`) · **Tests:**
`tests/test_release_package.py` 3 tests inventory the source-checkout release, version/CLI
metadata, Orison quick start, export-v5 usage guidance and archived drafts; focused release,
documentation, scenario, export and CLI paths 31 passed. `uv sync --locked`, `uv build`, all
three CLI help surfaces and source-archive contents were verified; full offline suite 698 passed,
6 LLM deselected. Version 0.1.0 source/wheel artifacts built locally. The current WBS explicitly
defers automated benchmark orchestration and its output example to the post-MVP Benchmarking /
Experimental Evaluation milestone; none was fabricated for this package. The existing proprietary
license designation is normalized as `LicenseRef-Proprietary`; publishing/relicensing was not part
of this task.
**Brief:** `archive/tasks/T-066-release-package.md`

### ✅ T-071 — Clean public source release and boundary hardening
`testing.md §23` · `api.md §7.2` · `benchmark-methodology §4` · I-088 · I-094 · I-096 · after T-066
Build a deterministic, history-free public source tree for the owner's separate GitHub repository;
record the outstanding Gate 0/1 checks; and close the three public-facing boundary-hardening items
in their own tested sub-boundaries. The public tree excludes `dev/`, local/tool state, retained run
evidence and maintainer-only tests, but includes runtime code, contracts, guides, scenarios,
packaging, public CI and the product test suite.
**Done when:** two clean-source builds are byte-identical; a build installs from its lockfile,
runs its included offline suite, builds both distributions and exposes all CLI help surfaces;
Gate 0/1 records say PASS with actual output; TEST-I094-001, TEST-I088-001 and TEST-I096-001 pass;
the three backlog items move to resolved with their commits and evidence.
**Closed:** 2026-09-22 · **Commit:** `7592008` · **Tests:** red 6; focused 26 passed;
Gate 0 54 passed; Gate 1 7 passed; full maintainer suite 1251 passed / 7 LLM deselected; final
history-free 242-file public snapshot installed locked, passed 1238 / 7 LLM deselected, built
sdist+wheel and exposed all CLI help surfaces. D-083; I-088/I-094/I-096 closed. Ready tree:
`~/Projects/AMAW-public-ready`.
**Brief:** `archive/tasks/T-071-public-source-release-hardening.md`

---

# Post-release Milestone — Adjudication Reliability Experiment

*Does splitting scope/rules analysis from probability judgment materially improve adjudication
over the authority-and-causation-first single Umpire introduced by D-044/D-047? This is an
evaluation track, not a commitment to adopt a second mandatory LLM role. It measures model
reliability, not mechanical authority: no adjudicator design can give the server-enforced
semantic guarantee I-038 asks for (D-089). **Deferred and
unscheduled by D-056 (2026-09-16)**; the benchmarking milestone no longer waits on it. Ordered
when reopened: T-067 → T-068 → T-069. I-040 Consequence Management has been promoted to T-074
under the Matrix Scenario Portability milestone (D-086); I-039 reaction diplomacy remains
evidence-gated by T-072/T-075; neither depends on T-067–T-069. Critic escalation and a general
event-driven activation queue remain unscheduled Phase-2 work.*

### ✅ T-067 — Adjudication regression corpus, rubric and released baseline
`agents.md §23–§27` · `benchmark-methodology §9–§10` · D-044 · D-047 · after T-066
Create a versioned corpus of difficult adjudications, including the Swedish coalition overreach
that motivated D-044. For each case record authoritative input, independently resolvable claims,
allowed and prohibited branch Effects/supersessions, and a human scoring rubric. Define the
acceptance thresholds before running the candidate system, then record the released
`single_umpire` baseline for unsupported commitments, waived prerequisites, causal overreach,
invalid supersession, human-rated quality, failures, latency, tokens and cost.
**Done when:** the corpus, rubric, pre-committed thresholds and reproducible released-system
baseline are archived with prompt/model/configuration provenance.
**Closed:** 2026-09-27 · **Commits:** `aa6fe18` (corpus, rubric, thresholds, harness; locked
before any provider call), `c77ef41` (baseline run) · **Tests:** TEST-T067-001…005 19 passed;
TEST-T067-006 1 passed (provider); full suite 1548 passed / 9 deselected. Reopened by owner
request (D-056); D-110. `corpora/adjudication/v1/` (10 cases, `47a2eb15…`); run 20 in
`dev/runs/t067-adjudication-baseline/`: 50 calls, 0 failures, agent-rated and owner-adopted (D-111) semantic violation
rate 0.28 (`umpire-v1.3` 0.12, `umpire-v1.6` 0.44), invalid supersession 13/50, quality 4.18.
I-156, I-157 filed. T-068/T-069 remain deferred until the owner reopens them.
**Brief:** `archive/tasks/T-067-adjudication-corpus-baseline.md`

### T-068 — Feature-flagged two-stage adjudication prototype
`agents.md §21–§27` · `TRD §22–§24` · after T-067
Prototype `scope_then_umpire` outside `app/` behind the existing one-call `Umpire` protocol; retain
`single_umpire` as the default and keep the core's RNG, legality, persistence and Effect authority
unchanged. The typed Scope decision represents multiple independently resolvable claims,
inadmissibility reasons, supporting Effect IDs and potentially supersedable Effect IDs; downstream
Effects reference claims for auditability without claiming semantic proof. Specify whether the
Umpire may reject/request one bounded rescope, and define fail-closed timeout, retry and unusable-
output behaviour for both stages. Add deterministic `FakeScopeJudge` coverage, hidden-information
and prompt-injection tests, branch-independent/already-established claim tests, export support and
immutable provenance for the mode plus both models/prompts/calls.
**Done when:** both modes pass the offline adjudication/server regression suite, existing exports
remain readable, and no `app/` module imports the concrete adjudicator or an agent framework.

### T-069 — Two-stage adjudication evaluation and adoption decision
`benchmark-methodology §9–§10, §23` · after T-068
Run both fixed adjudication modes over the T-067 corpus and compare the pre-committed quality,
failure, disagreement, latency, token and cost measures. Treat the modes as separate experimental
conditions and do not tune the rubric or thresholds after seeing candidate results. Record one
decision: retain the single Umpire, adopt the composite for a new experimental condition, or revise
and repeat. Any adoption must identify the required immutable prompt/spec version, provenance and
migration work; it does not silently replace prior benchmark conditions. At this boundary, decide
whether I-039 and I-040 should be promoted before the full benchmark milestone.
**Done when:** an archived comparison report and `DECISIONS.md` entry state the evidence, verdict
and exact next tasks; an unproven prototype is not the default.

---

# Post-MVP Milestone — Benchmarking / Experimental Evaluation

*Can AMAW support meaningful comparative analysis of autonomous AI agents? Deferred until the
MVP (Gates 0–3 + Release) is shipped. Builds on the run-provenance foundation (T-053), already
complete. D-056 (2026-09-16) deferred T-067–T-069 and re-sequenced this milestone: T-054, T-058
and T-059 run as one declared batch first; then T-055 → T-056 → T-057 → T-060 → T-061 → T-063.
**Replanned 2026-09-19 (D-076):** the project runs one player model and is not doing comparative
multi-model benchmarking, so T-063 — and the Gate 4 exit check that requires its 3-model /
six-permutation / 30-attempt matrix — is **deferred, not completed**, and T-070 (filed only so
T-063 could run) is deferred with it. The milestone is complete through T-060, the
provider/batch smoke test; T-061 remains optional. Nothing below T-060 is in the current queue.*

### ✅ T-054 — Benchmark runner and CLI
`WBS 5.2` · `TRD §30` · `benchmark-methodology §3, §7, §8` · after T-053 (D-056; batched with T-058, T-059)
`uv run amaw benchmark SCENARIO --models ... --rotate --repetitions N`. `--repetitions N`
means N games **per generated model/role assignment**. Before executing, report assignment
count, repetitions per assignment and total games. Store `assignment_id` and
`repetition_index`; assign and store a seed per game.
**Done when:** a batch runs end to end without manual per-game setup.
**Closed:** 2026-09-16 · **Commit:** `eae72c6` · **Tests:** `tests/test_benchmark_runner.py` 18 passed (T-054-001…005, T-058-001…002, T-059-001…003) + `test_redact_secrets_is_the_shared_scrubber`; full offline suite 803 passed, 6 LLM deselected. Declared batch per D-056; T-055/T-056/T-057 not absorbed.
**Brief:** `archive/tasks/T-054-benchmark-runner.md`

### ✅ T-055 — Role rotation
`WBS 5.3` · `benchmark-methodology §6` · after T-054
Balanced cyclic assignments across models and Actor Seats, recorded explicitly, so
Actor-position advantage separates from model difference.
**Done when:** a 3×3 rotation produces the documented balanced assignment set.
**Closed:** 2026-09-16 · **Commit:** `bd4c0b1` · **Tests:** `tests/test_benchmark_controls.py` 24 passed (T-055-001…003); T-054 seed/identity tests unchanged and green; full offline suite 827 passed, 6 LLM deselected. Declared batch T-055 + T-056 + T-057 (D-057, D-058, D-059).
**Brief:** `archive/tasks/T-055-rotation-fixed-umpire-modes.md`

### ✅ T-056 — Fixed Umpire benchmark policy
`WBS 5.4` · `benchmark-methodology §9` · `agents.md §27` · `INV-ADJ-004` · after T-055
Umpire model, prompt version and adjudication settings are held constant across a batch and
recorded. Attempting to vary them in model-benchmark mode is an error.
**Done when:** the player model is provably the only varied factor in model-benchmark mode.
**Closed:** 2026-09-16 · **Commit:** `bd4c0b1` · **Tests:** `tests/test_benchmark_controls.py` 24 passed (T-056-001…003); T-054 seed/identity tests unchanged and green; full offline suite 827 passed, 6 LLM deselected. Declared batch T-055 + T-056 + T-057 (D-057, D-058, D-059).
**Brief:** `archive/tasks/T-055-rotation-fixed-umpire-modes.md`

### ✅ T-057 — Benchmark modes
`WBS 5.5` · `benchmark-methodology §5` · after T-056
Model benchmark (harness, prompt, tools and memory held constant; LLM varied) and agent
benchmark (prompts, frameworks and designs may vary). Mode is stored in the results.
**Done when:** every result set states its mode and the mode constrains what may vary.
**Closed:** 2026-09-16 · **Commit:** `bd4c0b1` · **Tests:** `tests/test_benchmark_controls.py` 24 passed (T-057-001…002); T-054 seed/identity tests unchanged and green; full offline suite 827 passed, 6 LLM deselected. Declared batch T-055 + T-056 + T-057 (D-057, D-058, D-059).
**Brief:** `archive/tasks/T-055-rotation-fixed-umpire-modes.md`

### ✅ T-058 — Raw benchmark outputs
`WBS 5.6` · `TRD §32` · `benchmark-methodology §14, §15, §25` · after T-057
`games`, `agents`, `arguments`, `messages`, `resource_access` tables as CSV or equivalent
JSON, carrying completed games, Arguments and success rate, Messages, Cons, resource searches
and reads, tool calls, invalid tool calls, stalls, tokens, cost and latency.
**Done when:** outputs load cleanly into an external analysis tool with no post-processing.
**Closed:** 2026-09-16 · **Commit:** `eae72c6` · **Tests:** `tests/test_benchmark_runner.py` 18 passed (T-054-001…005, T-058-001…002, T-059-001…003) + `test_redact_secrets_is_the_shared_scrubber`; full offline suite 803 passed, 6 LLM deselected. Declared batch per D-056; T-055/T-056/T-057 not absorbed.
**Brief:** `archive/tasks/T-054-benchmark-runner.md`

### ✅ T-059 — Failure continuation and reporting
`WBS 5.2.4` · `benchmark-methodology §21, §22` · after T-058
One failed game must not abort the batch. Failed runs are reported with their failure
category and retained debugging artefacts (`testing.md §24`) — never silently dropped, never
counted as completed.
**Done when:** a batch with an injected failure completes and reports the failure distinctly.
**Closed:** 2026-09-16 · **Commit:** `eae72c6` · **Tests:** `tests/test_benchmark_runner.py` 18 passed (T-054-001…005, T-058-001…002, T-059-001…003) + `test_redact_secrets_is_the_shared_scrubber`; full offline suite 803 passed, 6 LLM deselected. Declared batch per D-056; T-055/T-056/T-057 not absorbed.
**Brief:** `archive/tasks/T-054-benchmark-runner.md`

### ✅ T-060 — Benchmark batch smoke test
`testing.md §22` · after T-059
Small marked batch exercising rotation, repetition, provenance and outputs.
**Done when:** the smoke test passes and is runnable on a small model budget.
**Closed:** 2026-09-18 · **Commit:** `d03b734` · **Tests:** `tests/test_benchmark_smoke.py` — `test_t060_001_offline_batch_smoke_walks_the_section_22_checklist` passed (default suite; real CLI, cyclic rotation × 2 repetitions, injected failure, every §22 line); `test_t060_002_provider_batch_smoke` (`llm`) ran once — both games complete, every §22 line held, CLI exit 1 on the I-076 false `code_version` drift (I-076 closed 2026-09-18, `dc72c19`, D-067); closed on that attempt by decision, not re-run. Full offline suite 934 passed, 7 LLM deselected. Evidence `runs/t060-batch-smoke-20260918/`, `RUNS.md` run 10.
**Brief:** `archive/tasks/T-060-benchmark-batch-smoke.md`
*Note 2026-09-19: `test_t060_002_provider_batch_smoke` was subsequently observed green end to end
(`RUNS.md` run 11, `runs/t060-batch-smoke-20260919/`: 2 games complete, condition held constant,
provenance/export valid, `batch.json` integrity `ok`; run 10 exercised failure/recovery). This is
the provider/batch **smoke test** — the small marked batch exercising rotation, repetition,
provenance, outputs and provider execution. It does not stand in for T-063's comparative
acceptance matrix, which is deferred (D-076); the two purposes stay distinct.*

### T-061 — Objective scoring **[optional, recommended]**
`WBS 5.7` · `benchmark-methodology §20` · `prompts/evaluator-v1.1.md` · after T-060
One fixed evaluator model post-game producing a 0–4 objective score plus rationale, with
evaluator model and prompt version recorded.
**Done when:** scores appear in outputs with their evaluator provenance, and every score is
emitted with an explicit descriptive-only marker unless scoring anchors were sealed in the
analysis plan before execution (`benchmark-methodology §20`; a single LLM evaluator is one
measure, never ground truth — human calibration and inter-rater reliability are post-MVP).
*Note 2026-09-19 (external review): objectives and Effects are free text (I-038), so no
mechanical scoring path exists in the lean MVP; do not report T-061 scores comparatively.*

### T-070 — Batch resume for the benchmark runner **[deferred with T-063]**
`benchmark-methodology §7, §8, §21` · `WBS 5.1` · I-078 · I-084 · I-085 · after T-061 · before T-063
`amaw benchmark --resume-batch DIR`. T-063 is 30 attempts at the run 11 rate (73–79 min per
game, `RUNS.md`) — roughly 38 h serial before slower models, rate limits, provider incidents or
a machine restart — and `benchmark/runner.py` has no resume protocol (`require_empty_output_dir`).
I-078 already seals what a resume needs before game 1: the schedule, the expected condition,
per-attempt seeds/assignments, and (I-085) the environment fingerprint, all in `batch.json`.
Resume loads that sealed batch and the analysis plan it registered; verifies `code_version`,
`environment.sha256` and the expected condition against the current process and **refuses any
drift** (D-058/D-068 held-constant, same failure vocabulary); treats every recorded attempt —
completed *or failed* — as immutable; records an attempt whose Game was started but has no
record as failed/`interrupted` rather than re-running it (fixed denominator, §21); executes only
`not_run` attempts in their sealed positions; and rewrites the tables from the union. Serial
execution unchanged — resumability first, concurrency never in this step (D-056 ordering).
**Done when:** an offline batch killed after attempt k resumes to completion with identical
`batch.json` schedule/condition, `k` untouched attempt records, and tables reconciled with all
attempts; a resume under a changed `code_version` or lockfile is refused before any game; the
§22 smoke routine passes on a resumed batch. *Added 2026-09-19 from the external review of
run 11; filed as a planned step because T-063 cannot be run without it.*
**Deferred:** 2026-09-19 · D-076. Its only trigger was T-063's ~38 h serial batch; T-063 is
deferred, so this step is deferred with it. Reopens with T-063, or on its own only when a
single-model batch long enough to need resume is actually planned — not to fill the queue.

### T-063 — Benchmark acceptance run **[deferred — multi-model comparison is out of current scope]**
`WBS 5.9` · `benchmark-methodology §24` · `testing.md §23` · after T-061 · after T-070
3 models, 3 roles, all six model-role permutations, 5 repetitions per assignment, 30 fixed
scheduled attempts. Verify model/role metadata, seeds, deterministic interleaving, fixed
Umpire configuration, raw outputs, completion rate and failed-run retention; never silently
replace failures to reach a successful-run quota.
**Done when:** the full acceptance run completes and its outputs are archived.
*Unblocked 2026-09-18 by I-078 (`def0a8d`, D-068): the batch now declares its expected condition
and per-attempt seed/assignment before game 1 and verifies every manifest against them, so the
acceptance run's claim rests on a reconciled declaration rather than on the first game's manifest.*
**Deferred:** 2026-09-19 · D-076 · **not completed.** Deferred because multi-model comparative
benchmarking is outside the current project scope: the project runs one player model, and this
step's acceptance design (3 models × 3 roles, six permutations, 5 repetitions, 30 attempts) is a
comparative design. The 30-game batch was not run. T-060 (run 11) validates the batch execution
plumbing for the single-model scope; it does **not** satisfy this step's criteria and is not
recorded as doing so. The step is preserved as written so it can be reopened if multi-model
comparison becomes a real project goal. **On reopening:** re-derive the experimental design —
models, rotation and repetition count — from the actual research question; do not assume 30
attempts remains appropriate. Prerequisites still hold: T-070 (resume) and the I-091/I-092
condition changes must land before any sealed batch.

> ### GATE 4 CHECK — run `/gate-check 4` **[deferred with T-063, D-076]**
> 3 models × 3 roles, all six permutations × 5 repetitions, 30 retained attempts,
> machine-readable outputs with immutable run provenance and reported completion rate.
> *Deferred 2026-09-19: the criterion explicitly requires the multi-model matrix that T-063
> would produce; it is not reinterpreted onto single-model runs and no single-model result is a
> Gate 4 pass. Reopens with T-063.*

---

# Post-release Milestone — Matrix Scenario Portability

*Can AMAW run a broad class of strategic/political matrix-game scenarios without
scenario-specific engine code? This milestone tests and extends scenario expressiveness while
keeping the engine generic. Added 2026-09-22 (D-086). It is separate from comparative
benchmarking (T-054…T-063), objective scoring (T-061) and the adjudication-reliability
experiment (T-067…T-069). The target is not compatibility with every specialist matrix-game
subsystem; it is that scenarios built around Actors, objectives, private information,
negotiation, Arguments/Cons/reply, adjudication, persistent Effects and bounded Consequence
Management can be represented primarily as scenario data rather than Python changes. Ordered:
T-072 → T-073 → T-074 → T-075. Reaction diplomacy (I-039) and multiple Arguments per Actor
(I-109) are deliberately **not** queued: T-072/T-075 must first show a concrete scenario needs
them. Opened after I-112 closed; T-072 ✅, T-073 ✅, T-074 ✅ 2026-09-23; T-075 ✅ 2026-09-24 —
**milestone complete**. D-093 promoted no further capability.*

### ✅ T-072 — Matrix-game scenario portability audit
`scenario-format.md` · `state-machine.md` · `agents.md` · `TRD §41` · after T-071
Evaluate the current scenario/game model against several representative matrix-game
structures **before** adding engine functionality. Map at least: scenario/background brief;
Actors/roles; public and private objectives/information; maps and reference material; initial
world state; scenario-specific adjudication rules; actions / intended Effects / supporting
reasons; Cons and owner replies; persistent consequences; multiple actions or Argument
opportunities per turn; reaction diplomacy; numeric modifiers, tracks, counters and specialist
combat subsystems. Classify every requirement as (1) directly representable today,
(2) representable through scenario authoring with adaptation, (3) missing generic capability
worth adding, or (4) specialist subsystem intentionally outside AMAW's generic core — a
candidate for external-system integration (I-113) where mechanical execution is valuable. Use
structural descriptions or legally redistributable material only — no copyrighted scenario text
enters the repository for the audit. No production gameplay change in this task.
**Done when:** a versioned portability report (`dev/portability/` or the task archive) maps
several representative scenario structures onto AMAW, lists concrete incompatibilities, records
which backlog items (I-038, I-039, I-109, I-113, …) each supports or weakens, and states acceptance
criteria for T-073/T-074. No engine feature is added merely because another matrix-game
ruleset contains it.
**Closed:** 2026-09-23 · **Commit:** `331df0e` · **Tests:** no test IDs (audit only, no product
change); full offline suite 1280 passed / 7 LLM deselected. Report
`portability/T-072-portability-audit.md` v0.4: 49 mechanics, 19 A / 9 B / 14 C / 7 D; T-073/T-074
validated with audit acceptance criteria (§8.1/§8.2); I-109 evidence gate met; I-038 supports
D-089; I-113 fits identified, not triggered; T-075 → clean-room Nordic/Baltic total-defence crisis.
**Brief:** `archive/tasks/T-072-portability-audit.md`

### ✅ T-073 — First-class scenario adjudication rules
`scenario-format.md` · `agents.md §21–§27` · after T-072
Add an optional scenario-level `rules` declaration: scenario-specific rules the Umpire must
consider when adjudicating. Deliberately semantic, not executable — each rule is `id` + `text`.
Rules are immutable scenario input; validated and hashed with the scenario; snapshotted into
the Game/provenance boundary; supplied to the Umpire verbatim in its input contract; included
in administrative export/provenance; scenario-wide and public in v1. Private intelligence
remains private Resources/private information, never hidden rules. Rules do **not** execute
code, directly modify probability, mutate state, create Effects, bypass the authority →
prerequisite → deterministic-rule → residual-probability order, or introduce a rules engine.
Scenarios without `rules` are behaviourally unchanged. This amends `scenario-format.md` and
`agents.md §21` (Umpire input): write the `D-0NN`, change the docs in their own commit first.
**Done when:** a scenario can declare bounded rules; the Umpire receives exactly the
snapshotted rules; the change is provenance-visible and hash-affecting; existing scenarios and
the golden `deterministic-game` fixture are unchanged; deterministic tests prove rules cannot
mutate state or leak private Resource content.
**Closed 2026-09-23** · Commit `c853a76` (docs `2908bf7`, prompt `99b3fb0`; D-090) · TEST-T073-001…007
(16 passed) · full offline suite 1296 passed / 7 LLM deselected · rules are `{id, text}`, public,
≤32; they reach the Umpire as `scenario_rules` under the new `umpire-v1.5`; the export format is 14.
**Brief:** `archive/tasks/T-073-scenario-rules.md`

### ✅ T-074 — Bounded evidence-linked Consequence Management
`state-machine.md §21 Option B` · `agents.md §28` · I-040 · after T-073
Promote I-040 into a deliberate post-release gameplay condition. The spec already allows a
consequence step proposing 0–3 Effects that the server validates and applies; this task makes it
real, sealed and evidence-linked. Add a scenario/condition field `consequence_mode:
none | bounded_effects_v1`; `none` is the default and preserves the released loop exactly. Under
`bounded_effects_v1`: the step runs once at the end of a completed turn; no Actor receives
another decision opportunity; the resolver receives current-turn authoritative Events and active
Effects only; each proposed Effect must cite the authoritative evidence it derives from; at most
three Effects; the resolver owns and consumes no RNG; the server validates references, limits
and visibility before committing; invalid or unavailable resolver output commits nothing (fail
closed, never fabricate); condition, resolver prompt/model and execution are recorded in
provenance; replay/resume stay deterministic with respect to committed state. Framework-neutral
resolver protocol plus a deterministic fake resolver for engine tests; the concrete LLM resolver
lives outside `app/`, following the Umpire boundary (TRD §4). Amends `agents.md §28` (evidence
references) and `scenario-format.md` (the mode) — `D-0NN` plus doc commit first. Do **not**
combine with reaction diplomacy, two-stage adjudication, multiple Argument slots or
event-driven activation.
**Done when:** games complete offline with the mode on and off; `none` matches the released
state-machine behaviour test-for-test; the enabled path creates only bounded, evidence-linked
Effects; restart/resume/export/provenance work; bad resolver output creates no partial Effects;
one focused provider smoke shows a single end-of-turn consequence pass without changing the
released default.
**Closed 2026-09-23** · Commit `44b646d` (docs `3863497`/`cb4a213`, prompt `ccddae2`, smoke
`6abdc53`; D-091)
- **Tests.** TEST-T074-001…010: 45 offline tests pass. The full offline suite is 1341 passed,
  8 deselected. The `llm` provider smoke passed with `deepseek/deepseek-v4-flash-0731`
  (`RUNS.md` run 16).
- **What ships.**
  - `consequence_mode` is persisted and sealed. For `none` games the provenance is
    byte-identical.
  - One all-or-nothing pass per turn of 0–3 public Effects. Each Effect cites current-turn
    `argument_adjudicated` Events and/or active public Effects.
  - Invalid or unavailable output pauses the game. Resume never runs a pass twice.
  - The export format is 15.
  - `amaw benchmark` refuses the mode (I-114).

**Brief:** `archive/tasks/T-074-consequence-management.md`

### ✅ T-075 — Matrix portability acceptance scenario
`scenario-format.md` · T-072 · T-073 · T-074 · after T-074
Demonstrate that the additions solve scenario-authoring problems rather than add abstractions.
Adapt one legally redistributable matrix-game scenario, or build a clean-room scenario with the
same structural demands, with no scenario-specific Python. It must exercise: at least three
conflicting Actors; public and private information; scenario Resources; at least one
first-class scenario rule; negotiation; Argument → Cons → optional reply → adjudication;
persistent Effects; bounded Consequence Management; replayable/exportable provenance.
Document every source concept that needed adaptation and every concept left unsupported. At
this boundary explicitly reassess I-039 (reaction diplomacy), I-109 (one primary Argument per
Actor), numeric modifiers/tracks, event-driven activation and authority-policy work versus the
external-system connector (I-113, D-089) — and promote only what the acceptance work gives a
concrete scenario need for. Specialist deterministic mechanics default to the connector route,
not to generic AMAW state expansion.
**Done when:** the scenario runs to completion without scenario-specific engine code; its full
run exports and replays; the T-072 report is updated with actual evidence; every remaining
unsupported mechanic has an explicit deferred disposition in `BACKLOG.md`.
**Closed 2026-09-24** · Commits `7e0f91f` (package and tests), `52866ce` (run 17 and
reassessment); D-093
- **Tests.** TEST-T075-001…006 pass (6 tests). The full offline suite is 1357 passed,
  8 deselected.
- **Live run.** `RUNS.md` run 17: `skaro-crisis` completed 4 turns and 24 Arguments on DeepSeek
  v4 Flash.
  - 23 of 24 rationales cite a rule by `id`.
  - The consequence passes committed 3, 2, 2 and 0 Effects, all valid.
  - `amaw export` regenerates the export identically.
- **Records.** Portability audit v0.5 §12; BACKLOG I-115 (dispositions) and I-116 (scenario
  tuning).
- **Reassessment.** Nothing is promoted, and T-076 does not open.

**Brief:** `archive/tasks/T-075-portability-acceptance-scenario.md`

### ✅ T-080 — Round-robin Argument rounds (`primary_arguments_per_actor` 2–3)
`state-machine.md §17/§20/§24/§25` · `scenario-format.md §6` · `agents.md §4/§21` · `api.md §6.1` ·
I-109 · D-094 · after T-075
Build the first extra-action sequencing policy that T-072 H4 asked for, and only that one.
`game.primary_arguments_per_actor` stays the single canonical field. The value 1 keeps the
released loop exactly. A value of 2 or 3 runs that many **Argument rounds** per turn. Each round
walks `turn_order` once and gives every Actor its slot `r`, so `budget_slot == round`. It never
gives one Actor consecutive Arguments. The consequence pass runs once, after the last round.
Rounds are derived from persisted Arguments, so no new state is added. The derivation covers
restart and resume, events (`argument_round_started` for rounds 2 and later), the Actor and
Umpire contexts (`budget_slot`, present only when the budget is above 1), provenance (the
manifest's `scenario` section seals `argument_sequencing: round_robin_v1`), the runner's opportunity key and the
scripted driver. Typed slots use semantic `rules` that cite the slot. No slot-type schema is
added. **Not in this task:** conditional bonus actions, once-per-game or post-round bonus
actions, scarce or trigger resources, declining a slot, consecutive slots, and any change to a
player or Umpire prompt text. Those stay in I-109's remainder (D-094).
**Done when:**
- The loader accepts 1–3 and rejects anything else.
- A budget-2 game completes offline under both `consequence_mode` values, with round-robin order
  proven from Events.
- The budget-1 games and the golden fixture are unchanged, test for test.
- Restart in round 2 resumes deterministically.
- The export and provenance record the slots and the policy.

**Closed 2026-09-24** · Commit `4e1af77` (docs `ea818c2`, amendment `030ae5b`; D-094)
- **Tests.** TEST-T080-001…011 pass (20 tests), including the autonomous runner with fake
  models in both activation modes. The full offline suite is 1377 passed, 8 deselected.
- **What ships.**
  - Budgets 1–3.
  - `argument_round_started`.
  - The round is derived from persisted Arguments.
  - Budget-gated context, Umpire and packet fields.
  - The manifest seals `scenario.argument_sequencing`.
  - Budget 1 is unchanged, and the export format stays 15.
- **Not shipped.** Conditional, once-per-game and post-round bonus actions, and declining a slot,
  stay in I-109.

**Brief:** `archive/tasks/T-080-argument-rounds.md`

### ✅ T-081 — Scheduled scenario injects
`state-machine.md §15/§24` · `scenario-format.md §11.2/§19` · `api.md` export · D-095 · run 17 analysis · after T-080
Add optional `injects` to a scenario: authored Effects released unconditionally at the start of a
given turn (2…`max_turns`), after expiry and before Diplomacy. Each inject is validated like a
starting Effect, plus an id and a turn. It is snapshotted on the Game (`scenario_injects`),
hash-covered and hidden from Actors until released. Once released it is an ordinary Effect with
`source_inject_id`. The export records both, so the format becomes 16. **Not in this task:**
conditional injects, releasing Resources, an inject event type, and any `skaro-crisis` change
(T-083).
**Done when:**
- The loader validates injects.
- An inject is released exactly once, at its turn, with the correct visibility.
- A restart at the turn boundary does not re-release it.
- Actors cannot see unreleased injects through the context, Effects or events.
- The Umpire and the consequence pass see released injects as ordinary Effects.
- A scenario without injects is unchanged in state and events.

**Closed 2026-09-24** · Commit `90b67bb` (docs `bb428aa`; D-095)
- **Tests.** TEST-T081-001…006 pass (21 tests). The full offline suite is 1398 passed,
  8 deselected.
- **What ships.** Up to 32 unconditional injects, released at turn start after expiry, hidden
  until release. The export format is 16.

**Brief:** `archive/tasks/T-081-scheduled-injects.md`

### ✅ T-082 — Umpire Effect discipline (`umpire-v1.6`)
`agents.md §21/§22/§27/§31` · D-096 · run 17 analysis · after T-081
Add `umpire-v1.6`: v1.5 plus Effect discipline.
- At most 3 Effects per branch, enforced by the v1.6 structured-output schema
  (`umpire-output-v2`) and a retryable `effect_bound_exceeded` check in the Umpire client.
- Only real changes, and supersede to update.
- v1.6 becomes the default for scenarios with rules. v1.5 stays selectable, and rule-free
  defaults are unchanged.

**Not in this task:** changing the server's per-branch cap, other prompt conditions, or any
provider run (T-083).
**Done when:**
- v1.6 is packaged and identical to its docs copy.
- The schema bound and the retry behaviour are proven offline.
- The CLI defaults are proven.
- v1.3–v1.5 behave as before.

**Closed 2026-09-24** · Commit `c62ea60` (docs `48f7e76`; D-096)
- **Tests.** TEST-T082-001…005 pass (7 tests). The full offline suite is 1405 passed,
  8 deselected.
- **What ships.** v1.6 is the default for scenarios with rules. It bounds Effect lists at 3 at
  the AI boundary and records tool schema `umpire-output-v2`.

**Brief:** `archive/tasks/T-082-umpire-effect-discipline.md`

### ✅ T-083 — `skaro-crisis` 1.1.0: late-game pressure and a cheaper recommended run
`scenario-format.md §11.2/§20` · I-116 · D-095 · D-096 · run 17 analysis · after T-082
This is a content-only revision with no engine change.
- Add five unconditional scheduled injects: turn 3 (cyber intrusion at the harbour; Drevna's
  exercise extension; Aldmoor's parliamentary deadline) and turn 4 (a storm closing links; a
  Drevnian vessel anchored near the cable landing site).
- Bump the version to 1.1.0.
- Document the recommended run with bounded Diplomacy (3 rounds) and sampled Cons (K=2).
  `umpire-v1.6` is the default.
- Update AUTHOR_NOTES.

The budget stays 1. **Not in this task:** scenario-declared game-loop defaults (I-118), and the
provider validation run itself, which the owner authorises separately.
**Done when:**
- 1.1.0 loads.
- Offline play releases every inject at its turn with the right visibility and completes.
- The documented command parses to the intended conditions.
- The T-075 acceptance tests still pass.

**Closed 2026-09-24** · Commit `61e0bf3`
- **Tests.** TEST-T083-001…003 pass, and T-075 still passes. The full offline suite is 1408
  passed, 8 deselected.
- **What ships.** Five injects, the recommended cheaper command, I-118 filed, and I-116
  pending a 1.1.0 provider run.

**Brief:** `archive/tasks/T-083-skaro-crisis-1-1.md`

---

# Post-release Milestone — External Systems / Simulation Integration

*Can AMAW orchestrate strategic decision-making around external systems that own specialist
mechanics, without becoming a simulation engine itself? Added 2026-09-23 (D-089, I-113).
AMAW keeps authority over Actors, information, diplomacy, Arguments/Cons, adjudication and
narrative Effects; an external system is authoritative over its own domain state; adapters
(Python, HTTP, MCP) are never authoritative. Builds on TRD §43.3 and WBS §11.3. **Opens only
after T-075 and only if T-075 records a concrete scenario need.** *2026-09-24 (D-093): T-075
recorded no such need; the milestone stays unopened.* Only T-076 is queued; the
likely continuation — T-077 read-only connector prototype (`capabilities`/`observe` exposed as
Player tools against a tiny in-repo logistics simulator), T-078 authoritative command execution
(`execute` with permissions, idempotency, revisions, provenance, retry), T-079 synchronisation
and an external-system acceptance scenario — is sketched here and is **not** queued until T-076's
decision says so. No commercial-game integration is planned.*

### T-076 — External-system connector contract
`TRD §43.3` · `WBS §11.3` · I-113 · I-038 · after T-075
Design only — no production code. Define the transport-neutral connector contract
(`capabilities`, `observe`, `execute`, `events`) and its authority model: which state each side
owns, capability discovery, scenario-declared per-Actor permissions with an explicit mapping
layer, idempotency, external revision identity, provenance, failure/unavailability semantics
(fail closed), visibility of returned data and versioning. Settle three questions explicitly:
(1) where `execute` sits relative to Argument → Cons → adjudication → roll (Actor order tool,
server-side after the selected branch, or pre-adjudication prerequisite evidence); (2) how
external randomness reconciles with INV-RNG-001; (3) how connector and external-system identity
enter the sealed experimental condition. Amending the TRD §43.3 invariant needs its own
`D-0NN` and doc commit first.
**Done when:** the contract and the TRD amendment are committed with a `DECISIONS.md` entry that
states the answers to (1)–(3) and whether T-077…T-079 are queued, revised or dropped.

---

# Not in this plan

`TRD §41` and the archived original WBS §7
(`dev/archive/docs/original-vision/AMAW_WBS_v0.2.1.md`) list what the lean MVP explicitly
excludes; its §11 describes Phase-2 / MVP+ work. If a task starts pulling one of those in, stop and re-read
the task. Anything worth remembering goes in the Deferred section of `BACKLOG.md`.
