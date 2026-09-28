# AMAW — Resolved Backlog

Closed items from [`BACKLOG.md`](BACKLOG.md), newest first — bugs fixed, errors corrected,
spec defects settled.

**Completed development steps are not here.** A `T-0NN` step is closed in place in
[`WBS.md`](WBS.md), next to the contract it was built against.

## Entry format

```markdown
### I-0NN — One-line summary
**Closed:** YYYY-MM-DD · **Commit:** `abc1234`
**Resolution:** what actually fixed it. If a decision was involved, reference it (`D-0NN`).
```

---

### I-170 — The retired scenario name pointed at a tag only the private repository has
**Closed:** 2026-09-28 · **Commit:** `d02a024` · **Decision:** D-119 (owner direction)
**Resolution:** `RETIRED_BUNDLED_SCENARIOS` removed; `swedish-government-v6` is an ordinary
unknown name and the error lists `swedish-government`. `TEST-I170-001` replaces `TEST-I144-002`.
Filed and closed in one step at the owner's request; no open backlog entry.

### I-169 — The public snapshot could not yet carry development on its own
**Closed:** 2026-09-28 · **Commit:** `703a3f5` · **Decision:** D-116, D-117
**Resolution:** the builder holds back itself, its tests and the corpus scripts, adds `dev/runs/`
to the public `.gitignore`, and prints one `scenario/<id>/<version>` tag per shipped package for
the public root commit. The public `RUNS.md` is living; `AGENTS.md` and the authoring guide say
where pre-public scenario versions live. `TEST-I128-001`/`TEST-I161-001` read fixture baselines,
so they run publicly. Issue drafts 11–14 added. `TEST-I169-001…004`.

### I-168 — User-facing docs contradicted D-118 and carried process identifiers
**Closed:** 2026-09-28 · **Commit:** `d8932b2` · **Decision:** D-118, D-076
**Resolution:** the README example is now run 23's `umpire-v1.7` game: Arkania's offer narrowed
to its own commitment, then a roll on an independent verification mission, not on a player's
acceptance. The README says no comparative model results are published yet. `README.md`,
`guide/` and the scenario READMEs state decisions in words instead of `D-`/`I-`/`T-` numbers.
`TEST-I168-001/002`. Found on the way: I-164 corrected from one case to two.

### I-161 — Open Diplomacy in `swedish-government` ends only through the no-progress ceiling
**Closed:** 2026-09-28 · **Commit:** the I-161 I-163 commit · **Decision:** owner direction, D-099
**Resolution:** `swedish-government` 7.1.0 declares `diplomacy_mode: bounded_rounds` with 3
rounds per turn, the setting `skaro-crisis` validated in run 18; a flag or `AMAW_*` value still
overrides. `TEST-I161-001` checks that 7.1.0 differs from 7.0.0 only by version, this condition
and the I-163 rule, and that a bare run resolves it. Not yet played: the check run is separate.

### I-163 — The authoring guide does not say where dice can still act
**Closed:** 2026-09-28 · **Commit:** the I-161 I-163 commit · **Decision:** D-118
**Resolution:** `guide/scenario-authoring.md` gains "Give the dice something to decide": players'
decisions are never rolled, so put the uncertainty dice should decide with forces no Actor plays
and name it in a rule. Applied in `swedish-government` 7.1.0 as rule `intern-foerankring`:
joining a government, a budget or cooperation agreement, or breaking a public red line needs the
party's own board or Riksdag group; the Umpire may roll that approval, the leadership's own
commitment stands either way, and other played parties' decisions are never rolled.
`regeringsregler.md` §12/§11 and AUTHOR_NOTES criteria 8–9 match. `TEST-I163-001`.

### I-162 — Argument success rate and mean probability do not measure attainment
**Closed:** 2026-09-28 · **Commits:** the I-162 spec commit, the I-162 close commit · **Decision:** D-118
**Resolution:** docs only. `benchmark-methodology` 0.3.13: §15 says success rate is comparable only
within one Umpire condition; new §19.1 explains why a `success` under §23 often establishes only
the Actor's own commitment, requires the adjudication mode distribution beside every success
figure, and defines Desired-Effect attainment (full / partial / none), rated after the game by a
fixed, blinded evaluator; §27 reports both; §29 adds "Model X achieves more because its Arguments
succeed more often" to the overstated claims. An attainment rater is not built; that belongs with
T-061.

### I-167 — Committing new run evidence broke two release tests
**Closed:** 2026-09-28 · **Commit:** the I-167 commit
**Resolution:** found while closing I-162: the run 22 and run 23 commits (`b06690d`, `d887c8a`)
left `master` red. `TEST-I089-005` pins the tracked run directories, so the four new ones are
added to `RETAINED_RUN_DIRS`. `TEST-I157-002` failed because `select_heldout_cases.py` globbed
every export in `dev/runs/` today, while `selection.yaml` defines the pool as the exports tracked
when the rule was written; the pool (226 eligible Arguments) grew to 288 and redrew the held-out
split. The script now reads a frozen `POOL_EXPORTS` list from commit `312e7a0`; the locked
`selection.yaml` is unchanged. Adding a run to `dev/runs/` now needs only the retained-set line.

### I-160 — `umpire-v1.7` rarely rolls and rarely fails an Argument
**Closed:** 2026-09-28 · **Commit:** the D-118 commit · **Decision:** D-118
**Resolution:** not a prompt defect. Run 23 (`northern-strait`, v1.3/v1.6/v1.7, one seed) showed
that almost every v1.3/v1.6 roll was on another played Actor's acceptance, which §23 forbids
(I-156). v1.7 narrows such Arguments to the Actor's own commitment and grants it, so it rolls
less and succeeds more. The owner kept §23 and v1.7 (D-118); run 22's zero rolls follow from a
scenario with no non-played uncertainty. The consequences are filed as I-162 (success rate is not
an attainment measure), I-163 (authoring rule on where dice act) and I-164 (one residual
acceptance roll under v1.7). Evidence: `dev/runs/i160-northern-strait-umpire-v1{3,6,7}-*/`,
RUNS.md runs 22–23.

### I-159 — Make `umpire-v1.7` the default Umpire prompt for every game
**Closed:** 2026-09-27 · **Commits:** `66fc5f6` (spec), the I-159 close commit · **Decision:** D-114
**Resolution:** the CLI resolves every unpinned game to `umpire-v1.7`, with or without rules and
under either Con-reply treatment. Pins still win; `visible_reply` still refuses v1.3 and a rules
scenario v1.3/v1.4; resumed games keep their recorded version and legacy recipes still resolve to
v1.3. `TEST-I159-001…003`; tests for the superseded defaults updated.

### I-156 — The released `umpire-v1.6` lets probability establish another Actor's decision
**Closed:** 2026-09-27 · **Commits:** `6ba814c`, `bdab905`, `82dd63d` · **Decision:** D-112, D-113
**Resolution:** a new immutable prompt, `umpire-v1.7`: v1.6 plus a branch audit ("would this be
established if no other Actor made a new discretionary decision or commitment?"). Run 21
judged it against the released Umpire on corpus v2 under pre-committed thresholds: semantic
violation rate 0.299 → 0.051 overall and 0.360 → 0.020 on the held-out split, every guard
passing. Adopted as a selectable experimental condition; the released defaults are unchanged.

### I-157 — Corpus-v1 claim texts narrower than the Effects they are meant to catch
**Closed:** 2026-09-27 · **Commits:** `64e7830`, `312e7a0`, `ecc767b`
**Resolution:** `adjudication-corpus-v2`: v1's cases as a development split with atomic claims in
one state vocabulary, a held-out split drawn by a rule and seed committed first, a mechanical
branch-inconsistency measure, thresholds v2, and a harness with bounded concurrency, resume
and a blinded mixed sheet. v1 and run 20 are unchanged. The rating review's omission of claim
notes is filed as I-158.

### I-090 — Structured `analysis-plan-v2` design block so seed and rotation policies reconcile by name
**Closed:** 2026-09-26 · **Commit:** `9ebe4ef` · **Decision:** D-109
**Resolution:** `analysis-plan-v2` is v1 with a structured `design`: `scope` (`experiment|cell`),
`seed_policy` equal to the runner's formula, `rotation_policy` in `single|permutations|cyclic`;
a cell plan declares at least two levels. `reconcile_plan` refuses a v2 plan whose scope or
policies disagree with the schedule. A v2 cell batch names its level with `amaw benchmark
--plan-level`, recorded in `batch.json` and never in the plan, so every cell seals the same plan.
v1 plans, the v1 example's hash and the v1 batch record are unchanged. Spec in its own commit
(`82b67b4`): `benchmark-methodology.md` 0.3.11 §4.2, `docs/analysis-plan-v2-example.yaml`.
TEST-I090-001…003 (19 tests); `uv run pytest`: 1529 passed, 8 deselected; mypy clean.
Follow-up: I-155 (the named level is not checked against the batch's condition).

### I-129 — `--umpire-reasoning-effort` was not checked against OpenRouter's effort levels
**Closed:** 2026-09-26 · **Commit:** `486690b` · **Decision:** D-101
**Resolution:** The Umpire's effort fails closed as the Player's does. `AIUmpire` refuses a level
outside `REASONING_EFFORTS` at composition, which covers resumed recipes too. `amaw run` and
`amaw benchmark` refuse the flag with exit 2 before any runner or Game exists, even when a
profile overrides it (TEST-I129-001/002). Valid levels and `None` are unchanged. The consequence
resolver's effort is always `None`, so it needs no guard. `uv run pytest`: 1510 passed,
8 deselected; mypy clean.

### I-134 — Test-suite review (umbrella for I-135…I-140)
**Closed:** 2026-09-26 · **Commit:** `07a4354`
**Resolution:** All six items resolved: I-136…I-140 on 2026-09-26 (D-107) and I-135 below. The
suite is organised by behaviour (no issue- or task-named test file), every declared `TEST-*` ID
has a named test, the public snapshot runs every test, and the full run takes 4:44 instead of
9:18. The review record stays at `dev/plans/2026-09-24-test-suite-review.md`; its file:line
references predate the I-140 renames.

### I-135 — Bookkeeping tests played full six-turn games; the suite took 9 min
**Closed:** 2026-09-26 · **Commit:** `07a4354`
**Resolution:** Bookkeeping tests play a session-derived short game (the canonical fixture with
`max_turns: 2`, `tests/benchmark_helpers.derive_short_fixture`; the canonical fixture is
untouched) through one shared `offline_batch` harness and one `invoke_benchmark`, replacing three
copies of each. §18/§19 play the fixture once per module. `test_i028_002` no longer waits out a
10 s barrier timeout. A registered `slow` marker covers the 41 heaviest tests. Measured: 1506
passed in 558.5 s before, 1508 passed in 284.3 s after; `-m "not slow and not llm"` runs 1457
tests in 2:24. `TEST-T054-003` and `TEST-T060-001` stay real six-turn batches. Brief:
`dev/archive/tasks/I-135-test-speed.md`.

### I-153 — `app/api.py` split into player-facing and admin route modules
**Closed:** 2026-09-26 · **Commit:** `6f637b0`
**Resolution:** The Actor-facing routes are in `app/api_player.py` and the admin orchestration
and export routes in `app/api_admin.py`. `app/api.py` keeps the error envelope, the
authentication dependencies, exception handlers, request logging and `create_app`. The route
table is identical. `TEST-I153-001` keeps `app.export`, `app.summary` and `app.api_admin` out of
the player module. The source checks of I-065 and I-081 now scan every `app/api*.py` module (D-108).
Brief: `dev/archive/tasks/I-153-I-154-api-and-service-split.md`.

### I-154 — `game_service` domain errors and result values extracted
**Closed:** 2026-09-26 · **Commit:** `bea01f0`
**Resolution:** The 28 domain errors are in `app/game_errors.py`, and the 12 frozen result
dataclasses are in the new `app/game_results.py`. `app.game_service` re-exports every name.
Helpers and operations stay, so the phase transitions are still in one file (D-108). Brief as I-153.

### I-140 — Issue- and task-named test files regrouped by behaviour
**Closed:** 2026-09-26 · **Commit:** `264ba01`
**Resolution:** The 68 `test_iNNN_*`/`test_tNNN_*` files were renamed with `git mv` to behaviour
names that group by prefix (`test_scenario_*`, `test_cons_*`, `test_research_*`, `test_player_*`,
`test_model_*`, `test_provenance_*`, `test_benchmark_*`, `test_api_*`, `test_release_*`, …). They
were not merged, because per-file helpers would collide for no navigation gain.
`test_review_remediation.py` was split into its behaviours' files. No test was renamed. Brief:
`dev/archive/tasks/I-136-I-140-test-suite-cleanup.md`.

### I-138 — Duplicate tests and copied helpers
**Closed:** 2026-09-26 · **Commit:** `3eb9178`
**Resolution:**
- Two subsumed tests deleted, with their IDs and assertion moved into the survivors.
- `test_architecture_boundaries.py` replaces 11 import-boundary checks. The framework checks keep
  the clean-interpreter subprocess form (D-017), and the named IDs are parametrisation IDs.
- `tests/golden.py` holds the §18 golden outcomes, which §18 and §19 both call.
- Shared helpers: `api_helpers.FIXTURE_DIR`/`REPO_ROOT`/`bearer`, `db_helpers`, `cli_helpers` and
  `benchmark_helpers.read_tables`.
- The per-file batch harnesses are left to I-135.

Brief as I-140.

### I-137 — Prose-pinning document tests; whole-file snapshot exclusions
**Closed:** 2026-09-26 · **Commit:** `9306f17`
**Resolution:**
- Prose asserts were dropped and the contract halves kept (env vars, commands, module index,
  links, TRD §26.1 heading and D-100).
- `test_i103_*` and the ledger-only checks were dropped.
- The `dev/` readers skip without `dev/`.
- The two §25 column checks are now one check: every documented column is written.
- `scripts/build_public_source.py` no longer excludes test files. The snapshot runs every test:
  1501 passed, 4 skipped.

Brief as I-140.

### I-136 — Test-ID traceability gaps
**Closed:** 2026-09-26 · **Commit:** `04739b2` (spec `5a46f63`, D-107)
**Resolution:**
- New tests: `TEST-I053-008` (the bounded condition from `AMAW_DIPLOMACY_*` to the manifest,
  AgentRuns, rebuilt export and summary) and `TEST-I053-012` (a raising Diplomacy activation
  charges no round, serial and concurrent).
- `TEST-ADJ-007/008/009` are separate tests over every shipped Umpire prompt.
- `TEST-RNG-003`: an Umpire output carrying `roll`/`success` resolves exactly as one without.
- `TEST-I055-007b` was renamed to its test's own ID. `TEST-I118-004…010` are defined in the
  archived I-118 brief.
- `testing.md` 0.3.10 §1.1 (D-107): issue- and task-scoped IDs are traceability, not contract.

Brief as I-140.

### I-139 — Vacuous, cwd-dependent and tombstone tests
**Closed:** 2026-09-26 · **Commit:** `ad34f99`
**Resolution:**
- The always-true `hasattr` assert is now `request_count == 2`.
- Seven cwd-relative paths are anchored on `api_helpers.REPO_ROOT`. The full suite now passes
  from `tests/` (1505).
- Tombstones and the docstring-length test were dropped. `i063_002` pins public names and import
  direction.
- The Con reload test proves the pass: no Con row, and a second pass is refused.

Brief as I-140.

### I-149 — `ai-2027` 1.0.0 converged too easily
**Closed:** 2026-09-26 · **Commit:** `e76077a`
**Resolution:** `ai-2027-branch-point` 1.1.0 adds rule `agreements-take-time` (a treaty moves one
stage per turn; entry into force needs working verification) and three injects: `unreproducible-results`
(turn 2, public; a finding only an independent test can settle), `copy-separation-plan` (turn 2,
private to Agent-4) and `board-freeze-demand` (turn 3, private to OpenBrain; oppose any training
freeze in public). Owner's choice: tune now and validate 1.1.0 with the next run, as run 18 did
for `skaro-crisis` 1.1.0. TEST-I149-001; full suite 1499 passed. Brief:
`dev/archive/tasks/I-149-ai-2027-tuning.md`.

### I-148 — A console log under `dev/runs/<dir>/` changed `code_version` and blocked `--resume-game`
**Closed:** 2026-09-26 · **No code change** (operator error)
**Resolution:** `agents/code_version.py` already excludes `stdout.log`, the name
`guide/operations.md` "Run a game" documents for console output and `amaw_unattended.py` writes.
Runs 18 and 19 were launched by hand with `stdout.txt` instead. The exclusion list is exact on
purpose (an arbitrary file under `dev/runs` stays part of the source identity), so it is not
widened for a non-standard name. Launch with `>"$RUN_DIR/stdout.log" 2>&1`.

### I-150 — A failed Con activation's pass was indistinguishable from a chosen pass
**Closed:** 2026-09-26 · **Commit:** `013807f` (spec `94528d3`, D-105)
**Resolution:** The runner's failure row records `provider_metadata.recovery = {action,
argument_id}`, and the run summary's `con_condition` reports `recovery_passes` per Argument and in
the totals, with "(N after failed activations)" on the summary line. The `con_passed` Event, API
and export format are unchanged. agents.md 0.3.10 §18, benchmark-methodology 0.3.10 §4. Games
before D-105, run 19 included, carry no marker: their recovery passes are unknown, not zero.
TEST-I150-001/002; full suite 1498 passed. Brief: `dev/archive/tasks/I-150-recovery-passes.md`.

### I-147 — `ai-2027` declares no `game.seed`, so its README command fails
**Closed:** 2026-09-26 · **Commit:** `bbba7f2`
**Resolution:** `ai-2027-branch-point` 1.0.1 declares `game.seed: 20271025`, the seed run 19
used, so `uv run amaw run scenarios/ai-2027` creates a game. Relocked, CHANGELOG entry, local tag
`scenario/ai-2027-branch-point/1.0.1`. TEST-I147-001; full suite 1496 passed. Brief:
`dev/archive/tasks/I-147-ai-2027-seed.md`.

### I-146 — Add an AI 2027 scenario package
**Closed:** 2026-09-25 · **Commit:** `79bd74f`
**Resolution:** New package `scenarios/ai-2027` (`ai-2027-branch-point` 1.0.0), forking from
AI 2027 at its October 2027 Oversight Committee decision on Agent-4.
- **Game.** Six Actors (safety team, OpenBrain leadership, White House, Agent-4, China, EU),
  four one-week turns, bounded Diplomacy and sampled Cons.
- **Rules.** Eight authority gates: a Committee decision needs six of ten votes, Agent-4 acts
  only through its access, alignment is never established by assertion, and weapons, pathogen
  and cyber detail stays strategic.
- **Content.** Six injects that stay true in any state, two of them private deadlines. Five
  Resources, each citing ai-2027.com. The Committee's 5+5 membership is a labelled scenario
  assumption. The published endings are in `AUTHOR_NOTES.md` only.
- **Draft.** Replaces the owner-supplied `ai-2027-five-actor-v1` (Atlas and Tianji in Nov
  2027) and keeps its authority rules.
- **Not done:** bundling into the wheel (runs by path), a provider run, an anonymised twin.
- **Tests.** TEST-I146-001…004; 1495 passed, 8 deselected; mypy clean.
- **Brief:** `dev/archive/tasks/I-146-ai-2027-scenario.md`.

### I-145 — The Swedish scenario predates the official 2026 result
**Closed:** 2026-09-25 · **Commit:** `a6b4a83`
**Resolution:** `svensk-regeringsbildning-2026` 7.0.0 is frozen at 25 Sep 2026 12:00 CEST.
- **State.** It uses the official seats (S 99, V 30; v6 had a pre-final 100/29) and the real
  starting state: a caretaker government since 17 Sep and Andersson's assignment since 18 Sep.
  It adds a five-rule kernel.
- **Resources.** Public positions are split into before and after the election. Three new
  Resources cite dated sources: institutional status with the 12 Nov budget clock, election
  integrity with Borlänge and the IT fault kept apart and each claim marked, and policy fault
  lines. The private incentives are unchanged, and no brief names the integrity case.
- **Verification.** The external review was checked against sources first. MP's "no red
  lines" was narrowed, the police-surveillance finding was added, and unverifiable dates were
  dropped.
- **Side effect.** Rules make `umpire-v1.6` the default Umpire prompt for this scenario.
- **Not done, candidates only:** a deterministic package validator, a replay variant frozen on
  19 Sep with injects, a cyclic turn-order treatment, and an anonymised twin.
- **Tests.** TEST-I145-001…004; 1491 passed, 8 deselected; mypy clean.
- **Brief:** `dev/archive/tasks/I-145-swedish-scenario-7.md`.

### I-144 — The Swedish scenario's directory still carries a version number
**Closed:** 2026-09-25 · **Commit:** `655f886`
**Resolution:** The package is now `scenarios/swedish-government`, and so is its bundled copy
and CLI name. The content is unchanged, so version 6 keeps its locked hashes, and only the
`directory` in `versions.lock` moved. The old name is not an alias: `RETIRED_BUNDLED_SCENARIOS`
makes `swedish-government-v6` fail with the new name and the `scenario/svensk-regeringsbildning-2026/6`
tag, because I-145 moves the package to 7.0.0.
- **Tests.** TEST-I144-001/002; 1487 passed, 8 deselected; mypy clean.
- **Brief:** `dev/archive/tasks/I-144-swedish-government-rename.md`.

### I-143 — Scenario versions are not enforced and follow three conventions
**Closed:** 2026-09-25 · **Commit:** `a0a26bc` (spec `27ce210`, `c79aef5`)
**Resolution:** D-104. New scenario versions are `MAJOR.MINOR.PATCH`, and any hashed change
bumps the version (scenario-format 0.5.2 §5/§20).
- **Lock.** `scenarios/versions.lock` records each released version's scenario and Resource
  hashes as append-only history, backfilled from git (6 versions). `scripts/lock_scenarios.py`
  appends new versions and refuses to rewrite a locked one.
- **Changelogs and tags.** Each package has a `CHANGELOG.md`. Local annotated tags
  `scenario/<id>/<version>` mark every version's release commit; they are not pushed.
- **Found.** `northern-strait` 1 changed its `description` after release (`41f6cbe`). It stays
  at 1 for the content runs 4 onward played, and its changelog records the hash only run 3
  used.
- **Legacy.** `northern-strait` 1 and the Swedish scenario 6 are pinned as integer versions
  until their next bump.
- **Tests.** TEST-I143-001…004. **Evidence.** 1485 passed, 8 deselected; mypy clean.
- **Deviation.** Changelogs are `CHANGELOG.md`, not `AUTHOR_NOTES.md` sections. **Follow-up.**
  I-144 (rename `swedish-government-v6`).
- **Brief:** `dev/archive/tasks/I-143-scenario-versioning.md`.

### I-142 — Superseded prompts sit beside the current ones and ship in the wheel
**Closed:** 2026-09-25 · **Commit:** `7aa7ade` (spec `dbc5d80`)
**Resolution:** The 11 prompt versions the code can no longer load (player v1.1–v1.9, Umpire
v1.1–v1.2) moved byte for byte to `docs/prompts/superseded/`. They are no longer bundled:
`amaw_resources/prompts/` holds the 9 loadable versions. `agents.md` 0.3.9 §31 states the
layout, and D-103 reverses the flat-directory rule. Manifests seal version and hash, so no
recorded run changes meaning.
- **Tests.** TEST-I142-001 pins the moved files' SHA-256s and the top-level set; TEST-I142-002
  checks that the bundle is exactly the loadable set; TEST-I142-003 checks the index paths.
- **Evidence.** 1481 passed, 8 deselected; mypy clean.
- **Brief:** `dev/archive/tasks/I-142-superseded-prompts.md`.

### I-128 — `skaro-crisis` does not declare its recommended condition
**Closed:** 2026-09-25 · **Commit:** `879059a`
**Resolution:** `skaro-crisis` 1.2.0 declares `diplomacy_mode: bounded_rounds` with 3 rounds
and `con_mode: sampled_sealed` with 2 challengers in `game` (D-099). This is the condition run
18 validated on 1.1.0. Nothing else changed. The version bump follows scenario-format §20. The
README's recommended command drops the four flags and explains how to reproduce run 17's open
condition.
- **Tests.** TEST-I128-001 checks that the manifest equals the last 1.1.0 manifest apart from the
  version and the four fields. TEST-I128-002 checks that a bare run resolves the declared
  condition and that flags override it.
- **Adjusted tests.** The T-075 offline play and TEST-I120-004/006 pass the released open
  condition explicitly. The I-120 fixtures stay byte-identical.
- **Evidence.** 1478 passed, 8 deselected; mypy clean.
- **Brief:** `dev/archive/tasks/I-128-skaro-declared-condition.md`.

### I-141 — The authoring guide lacks design lessons from playtests
**Closed:** 2026-09-25 · **Commit:** `f169794`
**Resolution:** Filed and closed after run 18. `guide/scenario-authoring.md` explained the
schema, but not what makes a scenario play well. Lessons that had been recorded only in
`dev/RUNS.md` are now in a "Design lessons from playtests" section:
- conflicting objectives;
- a second act through injects;
- Desired Effects sized to one turn;
- rules as authority gates;
- qualitative quantities;
- a wall-time budget with declared bounded conditions;
- quality criteria written into `AUTHOR_NOTES.md` before the first run.
The guide cites no `dev/` paths, so the public snapshot stays self-contained. The `RUNS.md`
Patterns section gains the injects lesson. Docs only; `docs/scenario-format.md` is unchanged. The
owner waived the test run for this docs change.

### I-116 — `skaro-crisis` loses pressure after turn 2
**Closed:** 2026-09-25 · **Commit:** `231b86c` · **Decision:** D-095 (injects)
**Resolution:** `skaro-crisis` 1.1.0 (T-083) added five scheduled injects for turns 3–4. Run 18
validated them and met AUTHOR_NOTES criterion 6.
- **Evidence.** In turns 3–4, 9 of 12 adjudications were probabilistic, against none in run 17,
  and 3 of them failed their rolls. The Arguments in those turns act on the harbour intrusion,
  Aldmoor's deadline, the storm and the anchored vessel. Drevna argued a contested framing play
  in every turn, and the turn 4 consequence pass committed 3 Effects.
- **Caveat.** Run 18 also changed the Umpire prompt (v1.6) and the Diplomacy and Con conditions.
  It is a playtest, so the late contest is not attributed to the injects alone.
- **Also confirmed.** I-117's player cost in a full export: 307 of 308 player calls are costed.
- **Record:** `dev/RUNS.md` run 18; `dev/runs/i116-skaro-crisis-20260924/`.

### I-133 — Public source snapshot cannot install
**Closed:** 2026-09-24 · **Commit:** `c4d1f38`
**Resolution:** Found while checking the release. The snapshot from `scripts/build_public_source.py`
(D-083) left out `amaw_resources/`, so `uv sync --locked` and `uv build` failed in the public tree.
- The allowlist now includes `amaw_resources/`. TEST-I133-001 checks every package and module
  that `pyproject.toml` builds is in the snapshot.
- TEST-I133-002 checks relative Markdown links resolve inside the snapshot. It caught the I-132
  README linking `dev/README.md`, and the `dev/`, `AGENTS.md` and `CLAUDE.md` rows were removed.
- `test_i123_001` reads `dev/DECISIONS.md` and now skips when `dev/` is absent.
- `PUBLIC_SOURCE.md` makes the root commit before the tests: without a `HEAD`, the code-version
  provenance check fails 88 runner tests.
- Snapshot (331 files, root commit): `uv sync --locked`, `uv build`, all three `--help` surfaces
  and `uv run pytest`: 1462 passed, 1 skipped, 8 deselected. Maintainer `uv run pytest`: 1476
  passed, 8 deselected.
- **Not changed.** `scenarios/skaro-crisis/AUTHOR_NOTES.md` cites `dev/RUNS.md` in prose, which
  the public tree lacks. `~/Projects/AMAW-public-ready` is the stale 2026-09-22 build.

### I-132 — Root README does not explain AMAW in its first minute
**Closed:** 2026-09-24 · **Commit:** `3196d60`
**Resolution:** The README was restructured before the repository goes public.
- It opens with what a Matrix Game is, why AMAW exists and a status line. The turn walkthrough
  follows, with an abridged real exchange from a retained `northern-strait` game (run 11).
- The quick start names the default model and the observed run time and token volume (runs 10
  and 11).
- Resume and wheel details moved out, since `guide/operations.md` already covers both.
- The repository map adds `dev/`, `AGENTS.md` and `CLAUDE.md`.
- README text only. `uv run pytest`: 1474 passed, 8 deselected.

### I-131 — Scenario and fixture READMEs lag their packages
**Closed:** 2026-09-24 · **Commit:** `9386367`
**Resolution:** A second pass over every README after I-130.
- `orison-crisis` names its declared `in_world_turn_length`.
- `swedish-government-v6` no longer asks for a CSV that is already packaged, and it explains the
  leftover `VALRESULTAT_REQUIRED.md`.
- The scenario index and the archive README cover `skaro-crisis`.
- `tests/fixtures/README.md` replaces a long-finished "deferred" list. `docs/README.md` dates its
  prompt list after `umpire-v1.6`.
- README text only, so no scenario hash changes. `uv run pytest`: 1474 passed, 8 deselected.
- **Not changed.** `VALRESULTAT_REQUIRED.md` itself is still in both the source package and the
  wheel copy. `dev/runs/` tracks 11 run directories, but D-081 retains four.

### I-130 — User-facing guides lag the engine
**Closed:** 2026-09-24 · **Commit:** `8097d2f` (index `3e92e1e`)
**Resolution:** Found by a documentation review against the code. The README and `guide/` had
not kept up with D-094, D-095, D-096 and D-099. The normative specs were already current.
- **Broken commands.** The benchmark examples in `README.md` and `guide/operations.md` now pass
  the required `--benchmark-mode`.
- **Wrong defaults.** `umpire-v1.6` is named as the rules default in `operations.md` and in the
  `docs/prompts/README.md` intro. The current player prompt versions replace `v1.3`/`v1.2`.
- **Stale rules.** `primary_arguments_per_actor` accepts 1–3. The limitations no longer say
  Consequence Management is disabled, and the README game walkthrough covers injects, Argument
  rounds, `visible_reply` and the opt-in Consequences.
- **Gaps.** `operations.md` lists `AMAW_PLAYER_TOOL_SURFACE` and `AMAW_MAX_RESEARCH_OPERATIONS`
  and states the D-099 precedence. `scenario-authoring.md` gains a section on rules, injects and
  declared conditions. `.env.example` had already been fixed in `fc4f51f`.
- Docs only. `uv run pytest`: 1474 passed, 8 deselected.

### I-127 — The mypy scope covers the six modules next to the I-120 hand-offs
**Closed:** 2026-09-24 · **Commit:** `32725c5` · **Decision:** D-101
**Resolution:**
- **Scope.** `[tool.mypy] files` adds `agents/consequence.py`, `app/api.py`,
  `benchmark/controls.py`, `agents/autonomous.py`, `agents/player.py` and `app/provenance.py`,
  one commit each, and the suite passed after every commit. That is 12 checked files.
  TEST-I127-001 keeps the six listed. TEST-I120-001 now asserts that mypy succeeds, not a file
  count.
- **HTTP boundary.** `agents/tools.py` defines one structural `HTTPClient` Protocol (headers,
  json, params, timeout) with an `HTTPResponse` Protocol. `PlayerTools` and
  `AutonomousGameRunner` both depend on it, and `httpx.Client` and `TestClient` both satisfy
  it. The response is structural because Starlette 1.6's `TestClient` returns an
  `httpx2.Response`. The `amaw_cli.py` `type: ignore` is gone.
- **Two lax boundaries fail closed (D-101).**
  - An unknown top-level Player `reasoning_effort` is refused (TEST-I127-002).
  - Deriving `resume.umpire` from an Umpire configuration without a model answers 400 and seals
    nothing (TEST-I127-003).
- **Everything else is narrowing.** No `Any`, `cast` or `type: ignore` was added to discharge an
  error. Valid manifests, hashes, recipes and requests are unchanged.
- **Follow-up.** I-129: the Umpire's `--umpire-reasoning-effort` has the same gap.

### I-123 — The supported deployment envelope is implicit
**Closed:** 2026-09-24 · **Commit:** `0c533d6` (docs `c1b2744`) · **Decision:** D-100
**Resolution:**
- **Statement.** TRD 0.5.5 §26.1: the supported deployment is one AMAW server application
  instance over one SQLite database file on a local filesystem, configured as a path. It names
  what rests on it: `BEGIN IMMEDIATE` guarded writes (INV-GAME-004/005, INV-ADJ-003, draw order
  under INV-RNG-001, TEST-RACE-001…003), the re-check after out-of-transaction Umpire and
  consequence calls, and the per-instance search cache. Another engine, several serving
  instances or a network filesystem is a design change, not a configuration change. §17 points
  to it.
- **Accuracy from the code.** Configuration is a file path, not a SQLAlchemy URL. Game creation
  and access-log appends use ordinary transactions (no guard); §26.1 says so.
- **No code change.** TEST-I123-001/002 (4 tests) keep the statement in the TRD and true of the
  engine; suite 1471 passed, 8 deselected; mypy clean.
- Brief: `dev/archive/tasks/I-123-deployment-envelope.md`.

### I-118 — Scenarios declare their recommended Diplomacy and Con conditions
**Closed:** 2026-09-24 · **Commit:** `c68e7ed` (docs `05a1896`) · **Decision:** D-099
**Resolution:**
- **Schema.** Optional `game.diplomacy_mode`, `diplomacy_rounds_per_turn`, `con_mode` and
  `con_challengers_per_argument` (scenario-format 0.5.1), checked by the one
  `GameLoopCondition.validate` plus K against the opponents.
- **One resolver.** `app.game_loop.resolve_game_loop_condition(explicit, scenario)`: explicit,
  then scenario, then released; `None` = not supplied; a count follows its mode. Used by game
  creation (admin API), `amaw run` and `amaw benchmark`. The CLI treats only a flag or an
  `AMAW_*` value actually set as explicit.
- **Typing (I-120 rule).** `GameLoopCondition` stays the resolved frozen type;
  `GameLoopConditionLayer` is the source layer. mypy scope gains `app/game_loop.py` and
  `app/scenarios.py`.
- **Unchanged:** the runner still sends its whole condition explicitly; resume restores and
  compares the sealed condition and never re-resolves; I-120 golden hand-offs byte-identical.
- **Evidence.** TEST-I118-001…010 (26 tests) pass; suite 1467 passed, 8 deselected.
- **Follow-up.** I-128 (`skaro-crisis` 1.2.0 declaring its condition).
- Brief: `dev/archive/tasks/I-118-scenario-condition-defaults.md`.

### I-117 — Player model calls record no cost
**Closed:** 2026-09-24 · **Commit:** `f6f0a76` · **Decision:** D-098
**Resolution:** PydanticAI 2.42's OpenRouter adapter reports OpenRouter's `usage.cost` on
`ModelResponse.provider_details["cost"]`. `RequestUsage.cost` is only a local genai-prices
estimate. The player read that estimate, which was unset, so player rows were `cost_usd = null`
(not `0.0` as first reported) with no cost captured.
- **The fix.** `_TimedModel` now reads `provider_details["cost"]` for each returned request.
- **No request change.** OpenRouter reports cost without an opt-in.
- **Zero and missing.** An explicit zero is a known cost. A missing cost stays `null`. The 2.42
  adapter drops a reported `0`, and that is pinned by a test.
- **Tests.** 11 tests in `tests/test_i117_player_cost.py`. They cover known, unknown, explicit
  zero, multiple and partial costs, the retry and failure paths, and the combined summary. Two
  of them go through the real adapter over a mocked transport.
- **Evidence.** 1441 passed, 8 deselected. One live OpenRouter request recorded
  `cost_usd = 2.376e-05` with `billed_requests = 1`.
- **Confirmed 2026-09-25 (run 18).** 307 of 308 player calls are costed in a full export; the
  one gap is a request OpenRouter reported no usage for.
- **Brief:** `dev/archive/tasks/I-117-player-cost.md`.

### I-119 — Second structural pass over the four largest modules
**Closed:** 2026-09-24 · **Commit:** plan `fdc64c5`; splits `a188a1e`, `84ae0b7`, `2930486`
**Resolution:** The hotspot plan (`dev/plans/2026-09-24-structural-hotspot-plan.md`) names each
module's responsibilities and gives a verdict.
- **Landed:** three approved splits, each its own item: I-124, I-125 and I-126.
- **Rejected, with reasons:**
  - a phase split of `game_service.py`, because its transitions cross phases, so the state
    machine would scatter across files;
  - decomposing `Player._activate`, because the failure-count key order reaches persisted
    `AgentRun`s;
  - splitting `api.py`'s route containers, because they are lists of short closures.
- **Evidence.** The suite is unchanged apart from new tests: 1430 passed, 8 deselected (1408 before, plus 22 new tests). The import-boundary test
  is green. CLI help and player tool definitions are byte-identical. No schema, API, export,
  prompt or tool change.

### I-124 — Player tool-surface policy moved into `ToolSurface`
**Closed:** 2026-09-24 · **Commit:** `a188a1e`
**Resolution:** `agents/tool_surface.py` owns exposure and per-call accounting, with its
per-activation state under one lock:
- exposure: the D-077/D-079 mutating hook and the I-069/D-078 research hook;
- per-call accounting: I-077 reservation, the D-062 closure, the budget refusal and D-078
  operation charging.

The tool bodies stay closures in `ReferencePlayer._build_agent`. Every model-facing definition
is byte-identical across all 16 surface combinations. TEST-I124-001…003 pass. The player,
guardrail and tool-surface suites pass unchanged (229 + 28).

### I-125 — One typed condition resolution in `amaw_cli.py`
**Closed:** 2026-09-24 · **Commit:** `84ae0b7`
**Resolution:**
- `run` and `benchmark` both resolve a new game's condition through `_resolve_new_condition`,
  in the same order, so the first refusal is unchanged.
- Resume uses `_resolve_sealed_condition`.
- Both return a frozen `ResolvedCondition`, and `ConditionFlags` carries the shared flags.
- Flags, defaults, help, error text, exit codes and the validation order are unchanged.
- The `run`/`benchmark` hand-offs are byte-identical to the pre-change commit (TEST-I120-003…006,
  which also pass on `c80613e`).

### I-120 — Condition and recipe hand-offs are typed and checked
**Closed:** 2026-09-24 · **Commit:** `84ae0b7`
**Resolution:**
- **Types.** One `TypedDict` per sealed section in `amaw_contracts.py`: `ResumeRecipe`,
  `ResumePlayerRecipe`, `UmpireRecipe`, `ConsequenceRecipe`, `ResumeCoordinatorRecipe`,
  `RunRuntime` and `RunConfiguration`. `RESUME_*_FIELDS` is derived from them with its released
  values.
- **Value objects in the CLI:** frozen dataclasses (I-125).
- **Left as mappings:** provider payloads, exports, the `benchmark` section and the
  `experiment` record.
- **Checker.** mypy (dev group, `[tool.mypy]`). It is scoped to `amaw_contracts.py`,
  `amaw_cli.py`, `agents/model_client.py` and `agents/tool_surface.py`, and TEST-I120-001 runs
  it inside the suite.
- **Serialisation unchanged.** Golden fixtures under `tests/fixtures/i120/` were captured from
  `c80613e`, and the insertion order of the recipe sections is asserted.
- **Follow-up.** Widening the scope is I-127.

### I-122 — Umpire and consequence provider responses bounded at 2 MiB
**Closed:** 2026-09-24 · **Commit:** `c80613e` · **Decision:** D-097
**Resolution:**
- **The limit.** `agents/model_client.MAX_RESPONSE_BYTES = 2 MiB`, counted while streaming. The
  first chunk over the limit is dropped, and nothing more is read.
- **The failure.** The new category `response_too_large` is non-transient. There is no client
  retry and no D-063 attempt, so the Umpire pauses the game as for any deterministic provider
  failure.
- **Provenance.** Recorded as `settings.max_response_bytes` in the Umpire and resolver
  provenance, beside `timeout`, `deadline` and `attempts`.
- **Unchanged:** resume recipes, `AgentRun.model_parameters`, timeouts, parsing and player calls.
- **Evidence.** TEST-I122-001…009 pass (below, at and over the limit, no retry at any layer,
  transient retry unchanged, provenance).

### I-126 — Advance-route attempt recording and the `request._body` replay as named helpers
**Closed:** 2026-09-24 · **Commit:** `2930486`
**Resolution:**
- `_record_resolution_attempts` serves both the Umpire and the consequence branches of
  `POST /admin/games/{id}/advance`, with the same overlay rule.
- `_replay_consumed_body` is the only assignment to Starlette's private `request._body`.
- `MAX_REQUEST_BODY_BYTES = 65536` replaces three literals, and the error text is the same.
- TEST-I126-001…003 pass. The provenance, I-081, I-088 and API suites pass unchanged (98).

---

### I-104 — Bounded / cursor-based `read_resource` — closed as not warranted
**Closed:** 2026-09-24 · **Commit:** milestone triage (`dev/gates/portability-milestone-triage.md`)
**Resolution:** No change. The item's own condition was that it is justified "only if I-099
telemetry shows agents receiving the right Resource id and not reading it, rather than
reformulating after poor results". The telemetry shows the reverse:
- the run 11 pattern came from poor search results;
- I-100 (run 15): `search-v2` moved returned-read conversion from 0.92% to 58.93%;
- I-112 made `search-v2` the released default;
- run 17 under v2 made 131 searches and 97 reads.

It reopens only if a scenario with very large Resources shows reads being avoided.

### I-114 — Benchmark batches seal and verify the consequence resolver
**Closed:** 2026-09-23 · **Commit:** `cd30e10` (docs `e8af975`) · **Decision:** D-092
**Resolution:** `amaw benchmark` accepts `bounded_effects_v1`. It resolves the mode as `amaw run`
does and has the same refusals.

- **Seal.** The batch seals a consequence condition `{provenance, resume}` beside the Umpire's.
  `batch.json` gets `consequence_condition` and its sha256, and `experiment.consequence` holds
  the recipe section.
- **Verify.** Every attempt's manifest is verified against it in both benchmark modes. Drift is a
  `consequence_condition` integrity failure and stops the batch, and the rest is recorded
  `not_run`. Per-attempt `consequence*` overrides are refused.
- **Tables.** `games.csv` gains `consequence_mode`, `consequence_model` and
  `consequence_prompt_version`.
- **`none` batches** record exactly what they did before.
- **Evidence.**
  - TEST-I114-001…004: 10 tests pass.
  - The full offline suite: 1351 passed, 8 deselected.
  - Methodology 0.3.9, with §9.1 the fixed consequence resolver.

---

### I-040 — Bounded evidence-linked end-of-turn Consequence Management
**Closed:** 2026-09-23 · **Commit:** `44b646d` (docs `3863497`, prompt `ccddae2`) · **Decision:** D-091
**Resolution:** T-074 implemented state-machine §21 Option B as the opt-in `consequence_mode:
bounded_effects_v1`. `none` remains the default and is the released loop.

- **When it runs.** Once per completed turn, in a `consequences` phase with no active Actor and
  no Actor action.
- **Resolver and input.** A framework-neutral resolver (`app/consequences.py`) receives public
  material only.
- **Proposal and commit.** It proposes 0–3 public Effects, each citing current-turn
  `argument_adjudicated` Events or active public Effects. The server validates and commits the
  proposal all or nothing: a `ConsequenceRecord` per (game, turn) plus
  `Effect.source_consequence_turn`.
- **Failure.** Invalid or unavailable output commits nothing and pauses the game with
  `consequence_resolver_unavailable`.
- **RNG and resume.** The pass draws no RNG. Resume never runs a pass twice.
- **Provenance.** Attempts are recorded as `role = consequence` AgentRuns, with the condition and
  resolver sealed in the manifest. The export format is 15.
- **Evidence.** TEST-T074-001…010 (45 tests) and the T-072 §8.2 criteria are satisfied. The
  provider smoke passed (`RUNS.md` run 16). The benchmark harness support was split out as I-114.

---

### I-112 — `search-v2` becomes the released default search contract
**Closed:** 2026-09-23 · **Commit:** `c1961e9` (docs `d5f2d29`) · **Decision:** D-088
**Resolution:** One named `SEARCH_CONTRACT_DEFAULT` (= `search-v2`) is now what a new run
resolves when nothing states a contract — `Settings.player_search_contract`,
`PlayerConfig.search_contract`, `resources.search_resources` and the HTTP `contract` parameter —
executing D-087 Decision A. The legacy resolution is deliberately a different value:
`PlayerConfig.from_resume_recipe` keeps `search-v1`, so a recipe sealed under the old default is
never reinterpreted (D-084). `agents/tools.py` had omitted `contract` whenever the sealed
condition was v1 and relied on the server default; that omission would have silently upgraded a
sealed `search-v1` player, and `TEST-I112-002` reproduces the regression against the intermediate
state. `search-v1` stays fully supported through `AMAW_PLAYER_SEARCH_CONTRACT` and
`--search-contract`; `api.md` 0.4.5, the operator guide, `.env.example` and the CLI help say so.
Two acceptance tests whose subjects are visibility and game scoping stopped asserting
ranking-dependent exact result sets, and `TEST-I083-003`'s legacy recipe now names the released
default of its own time. No prompt, tool schema, ranking algorithm or run evidence changed. Six
`TEST-I112-*` functions red first; full offline suite: 1280 passed, 7 LLM deselected. No provider
run — the evidence is I-100 Run 15. Brief: `dev/archive/tasks/I-112-search-v2-default.md`.

---

### I-111 — The retained-run allowlist lagged behind runs 14 and 15
**Closed:** 2026-09-23 · **Commit:** `f049fe0`
**Resolution:** `TEST-I089-005`'s `RETAINED_RUN_DIRS` still named the six directories current at
T-071, so the four added since — run 14 (`t060-batch-smoke-20260922-current/`, I-110's evidence)
and run 15's `i100-search-contract-20260922/` report plus its `-v1`/`-v2` raw arms — failed the
exact-set guard. All four are now named with the reason each is kept, under D-083's standing
policy that this repository keeps current evidence while the `amaw-runs` owner step is pending,
and the set comment states that adding a directory *is* the retention decision. D-081's
read-before-retention audit was performed on the four (25 tracked files, 7.4 MB): no OpenRouter
key, `api_key`/`bearer` assignment or e-mail address; what they hold is the already-accepted
categories — unfiltered exports of the published `northern-strait` scenario, provider generation
ids and the absolute checkout path — and D-083's public snapshot excludes all of `dev/`. No file
under `dev/runs/` was added, modified or removed and no retention rule changed. Full offline
suite: 1274 passed, 7 LLM deselected — the last known deterministic failure is gone. Brief:
`dev/archive/tasks/I-111-retained-run-allowlist.md`.

---

### I-100 — Candidate `search-v2` provider comparison
**Closed:** 2026-09-23 · **Evidence:** `dev/runs/i100-search-contract-20260922/` plus the raw
`dev/runs/i100-search-contract-20260922-v1/` and `-v2/` directories · **Decision:** A

The predeclared Northern Strait comparison ran four completed games: paired seeds 60000 and
60001 under released `search-v1` and frozen D-084 `search-v2`, with the v2 block first. The I-099
report measured 109 v1 searches versus 56 v2 searches; zero-result rate fell from 88.99% to
3.57%, identical-result reformulation rate from 50.46% to 23.21%, and returned-read conversion
rose from 0.92% to 58.93%. All games completed six turns with 36 Arguments and 36 adjudications;
v2 used fewer player calls and tokens, while partial provider cost was similar and latency was
noisy. Both batch integrity records are green and all raw attempts/faults are retained. Adopt
v2 as the future default in a separate follow-up (I-112); keep v1 as an explicit reproducibility
condition until that change lands. No source default was changed in I-100.

---

### I-108 — Batched search ignored the sealed search contract
**Closed:** 2026-09-22 · **Commit:** `87a0bcf`
**Resolution:** Every item dispatched by `search_resources_batch` now passes
`self._config.search_contract` to `PlayerTools.search_resources`, exactly like the sequential
closure. Behavioral `TEST-I108-001` configures non-default `search-v2`, invokes one sequential
search and a three-item batch through the model-facing tools, and records all four underlying
calls as `search-v2`; red-first evidence observed `search-v1` on the batch. Focused test: 1 passed;
broader agent/search regressions: 63 passed. The immediate audit found no additional search
dispatch omission. Search algorithms, defaults, schemas, manifests, public tool behavior,
ordering and concurrency are unchanged. The full run passed 1273 tests and exposed one unrelated
pre-existing retained-run allowlist failure, filed separately as I-111. Brief:
`dev/archive/tasks/I-108-batched-search-contract.md`.

### I-110 — Mixed terminal Umpire failures lost their per-attempt diagnosis
**Closed:** 2026-09-22 · **Commit:** `0e9d048`
**Resolution:** The admin adjudication boundary now applies a server-owned failure overlay only
when the Umpire returned a usable proposal and engine resolution then failed. When the Umpire
exhausts its bounded calls without a proposal, every already-normalised attempt record is
persisted unchanged, retaining mixed transient and unusable-output reasons in order. The new
API-level `TEST-I110-001` reproduces run 14's transient/unusable/transient/unusable sequence,
proves the game still returns 503 and pauses, and checks all four exact reasons. Existing
homogeneous-outage and post-proposal overlay behavior remains green. Full offline suite: 1273
passed, 7 LLM deselected. No retry, provider, adjudication, prompt, schema or gameplay condition
changed. Brief: `dev/archive/tasks/I-110-mixed-umpire-failure-provenance.md`.

### I-087 — Package runtime prompts and bundled scenarios for wheel installation
**Closed:** 2026-09-22 · **Commit:** `ec5b152`
**Resolution:** Added the importable `amaw_resources` namespace and one `importlib.resources`
boundary for prompt text, bundled scenario materialisation and explicit-path-first resolution.
The wheel now carries all 13 player and four Umpire prompt versions plus `scenario.yaml` and
referenced Resource files for `orison-crisis`, `northern-strait` and `swedish-government-v6`;
archived scenarios, evaluator material, `docs/` and `dev/` remain outside wheel runtime data.
`run`/`benchmark` keep packaged scenario material alive for the Click command lifetime, while an
explicit external directory always wins and an invalid explicit path never falls back by name.
Four red-first `TEST-I087-*` checks inspect the wheel, install it outside the checkout, load both
prompt families, create Orison to the same pre-provider hashes/state, preserve external paths and
compare every packaged byte. Built sdist + wheel, inspected 87 wheel entries, and installed the
wheel with `[agents]` into a clean Python 3.14 environment; full offline suite: 1272 passed,
7 LLM deselected. No prompt, scenario, Resource, manifest or provenance hash changed. Brief:
`dev/archive/tasks/I-087-package-runtime-assets.md`.

### I-101 — Batched research abandoned later items and made completed work retryable
**Closed:** 2026-09-22 · **Commit:** `af16495` · **Decision:** D-085 · **Spec:** `2ba655c`
**Resolution:** `batched-v1` now dispatches its validated prefix in input order through
recoverable Resource failures and returns one self-contained outcome per item: success carries
`data`; failure carries `http_status`, `code`, `message` and `details`. A batch with any dispatched
item is a successful tool call, so an exact repeat receives the existing successful-call duplicate
guard without adding ResourceAccess rows or operation charges, and `compact-success-v1` retains
the exact mixed payload. D-078 remains intact: every dispatch is charged first, a capacity-crossing
suffix stays under `not_executed`, and zero capacity retains the whole-call refusal; malformed
input still fails before dispatch. D-038 remains intact for rejected reads (charged operation,
no access row). Only batched schema identities moved v15/v16 → v17/v18; sequential v11/v13,
defaults, budgets, server/API and persistence are unchanged. Four changed behavior/schema checks
were red first; all six final I-101 tests pass. Focused regression set 100 passed; full offline
suite 1268 passed, 7 LLM
deselected. I-108 was discovered and filed separately. Brief:
`dev/archive/tasks/I-101-batch-item-outcomes.md`.

### I-064 — Neutral `app/game_queries.py` shared by `game_service` and `context`
**Closed:** 2026-09-22 · **Commit:** `fddf853` · **Decision:** —
**Resolution:** Added one neutral read-only query layer for current Arguments by lifecycle
status, accepted Arguments in the game/turn/Actor budget scope, submitted Cons, game-local Actor
ids, sealed challenger eligibility and canonical revealed-Cons ordering. `game_service` retains
every exact-cardinality/fail-closed wrapper and its existing error text while `context` now reads
through the same functions and no longer imports the mutation service. The duplicate Actor-id,
Diplomacy-phase and non-idempotent transaction wrappers were removed; `GameServiceError` and
`GameNotFound` moved to the neutral `game_errors` boundary and remain re-exported by
`game_service`. `TEST-I064-001…003` were red first; full offline suite: 1254 passed, 7 LLM
deselected. No API shape, game rule, normative doc or decision changed. Brief:
`dev/archive/tasks/I-064-game-queries.md`.

### I-107 — CI contract tests depended on terminal rendering and a stale retained-run inventory
**Closed:** 2026-09-22 · **Commit:** `d4614de`
**Resolution:** `TEST-T057-002` now gives the benchmark command every required input except
`--benchmark-mode`, inspects the Typer signature to prove that option is declared required, and
still proves parsing exits 2 before schedule construction; it no longer depends on Rich's
terminal-width-dependent abbreviation of option labels. T-071 separately reconciled
`TEST-I089-005` with all six deliberately retained run directories. The two exact regressions
passed locally on Python 3.12 and 3.14; both complete affected test files passed (44 tests).
GitHub Actions run `35671719853` reproduced exactly these failures on both interpreters; replacement
run `35676695257` passed in full on Python 3.12 and 3.14. No CLI behaviour, retained evidence,
application code or normative contract changed. Brief:
`dev/archive/tasks/I-107-ci-contract-test-stability.md`.

### I-096 — CLI engine ownership was fragmented before the disposal boundary
**Closed:** 2026-09-22 · **Commit:** `7592008`
**Resolution:** Both `run` and `benchmark` now establish `try/finally` ownership immediately
after `engine_for_path`; condition construction, resume parsing, Umpire/player configuration,
composition and execution all sit inside that boundary. The validation helpers no longer dispose
engines they do not own. `TEST-I096-001` injects an unexpected condition-construction failure in
both commands and proves each engine is disposed. Full offline suite: 1251 passed, 7 LLM
deselected.

### I-094 — Resume silently ignored an explicit stream-game-details option
**Closed:** 2026-09-22 · **Commit:** `7592008`
**Resolution:** The dual stream flag is tri-state for `run`: omission inherits the sealed first
process setting, while explicit `--stream-game-details` or `--no-stream-game-details` controls the
current invocation and logs a difference. The immutable manifest runtime record is not rewritten.
`TEST-I094-001` covers omission, explicit enable and explicit disable. Full offline suite: 1251
passed, 7 LLM deselected.

### I-088 — Request-body limit was enforced only after buffering the whole body
**Closed:** 2026-09-22 · **Commit:** `7592008`
**Resolution:** Mutating requests are consumed one ASGI chunk at a time; only bytes within the
65,536-byte bound are retained, the crossing chunk returns the existing 413 response immediately,
and no later chunk or endpoint is reached. Accepted bodies are cached for Starlette's downstream
replay. The Content-Length fast path and malformed-header 400 remain. `TEST-I088-001` covers both
the crossing-chunk stop and exact-limit replay. Full offline suite: 1251 passed, 7 LLM deselected.

### I-106 — Player usage could violate the inclusive-parent invariant and abort `AgentRun` persistence
**Closed:** 2026-09-21 · **Commit:** `fa09df3`
**Resolution:** Every returned player request is normalised before activation aggregation using
I-066's existing conservative rule: canonical input is at least either cache subset and canonical
output is at least the reasoning subset; parents are raised with `max`, never summed, so an
already-inclusive provider cannot be double-counted. Corrected requests retain
`usage_normalization` with provider-reported and recorded parents plus the observed subsets;
consistent request records are unchanged. `TEST-I106-001/002` reproduce T-060 run 12's shape
through a completed activation and a framework-rejected logical attempt plus retry, then persist
and export the resulting `AgentRun` without a 400. Focused accounting/provenance/offline-smoke
suite: 73 passed; full offline suite: 1246 passed, 7 LLM deselected. Run 12's two failed attempts
and integrity-valid batch are retained under `dev/runs/t060-batch-smoke-20260921/` and recorded in
`RUNS.md`. No server invariant, schema, prompt, tool condition, gameplay or normative docs changed;
no decision or follow-up issue. Brief:
`dev/archive/tasks/I-106-normalize-player-inclusive-usage.md`.

### I-103 — `tool_loop` claimed "one mutating tool per provider round-trip" but only `send_message` was serialised; sibling mutations race
**Closed:** 2026-09-21 · **Commit:** `0ebcd46` (spec/decision `23c8f2f`) · **Decision:** D-082
**Resolution:** The documented-property branch, as D-079 dictated. D-082 records that the tool
calls of one `tool_loop` response are dispatched concurrently, that only `send_message` (I-021)
and the Resource tools (I-077) are serialised, that two sibling mutations race with the loser's
genuine `409` counted as an invalid tool call, that this holds under both `tool_surface` values
(`available_only` filters at the next model step, not within an emitted response), and why the
scheduling is left alone: `tool_loop` is an explicit experimental condition and a harness-side
ordering policy would be a second, `agents/`-authored legality rule (D-077, D-079). Reopens only
if `tool_loop` is promoted to a supported normal mode (then: one lock over all mutating tools,
pre-dispatch revalidation, stub-response determinism test). `docs/agents.md` 0.3.3 §5 states the
property; the three docstrings that asserted the one-tool-per-round-trip rule as a guarantee
(`agents/player.py` module + `PlayerConfig`, `agents/decisions.py`) now state it as the
expectation the loop does not enforce — comment lines only, no behavioural change. `single_shot`
unaffected; no prompt, schema or provenance change. `TEST-I103-001` (2 doc-guard functions, red
first); full offline suite 1244 passed, 7 LLM deselected. Brief:
`dev/archive/tasks/I-103-tool-loop-sibling-mutation-race-note.md`.

### I-105 — `games.csv` aggregated `invalid_tool_calls` per game without naming the activation mode or tool surface
**Closed:** 2026-09-21 · **Commit:** `0933813` (doc `6db976d`)
**Resolution:** `benchmark/outputs.py` `games.csv` gains `player_activation_mode` and
`player_tool_surface` directly after `player_prompt_hash`, copied from the manifest's sealed
player block (D-046, D-079) so a game-level `invalid_tool_calls` total reads against its
condition without opening the manifest. Copied, never derived: a manifest that predates D-079
projects an empty surface — run 11 re-projected shows `tool_loop` / `''` beside 54 and 49
invalid calls (D-079 records those runs were `broad` byte-for-byte). `agents.csv`, the manifest
and `PLAYER_CONDITION_PATHS` unchanged; retained CSVs not regenerated.
`benchmark-methodology.md` 0.3.8 (§25, own commit). `TEST-I105-001…003` (4 functions, red
first); full offline suite 1242 passed, 7 LLM deselected. Brief:
`dev/archive/tasks/I-105-games-csv-activation-mode-tool-surface.md`.

### I-102 — `agents.csv` conflated the attempt outcome with per-actor activation faults and blanked `agent_framework_version` after a failed first run
**Closed:** 2026-09-21 · **Commit:** `4415181` (doc `e6c69e7`)
**Resolution:** `benchmark/outputs.py` now keeps the two facts apart on every `agents.csv` row.
`completed` / `failure_reason` are the methodology §21 *attempt* outcome: `failure_reason` is
filled only when `attempt.actor_affected` is this Actor and is never taken from the Actor's last
failed `AgentRun` (an Umpire/server failure leaves every Actor row's `failure_reason` empty —
`games.csv` carries it). A fault the coordinator recovered from — stall, ceiling hit, exhausted
transient — stays data under the existing `failed_activations` count and the new
`last_activation_failure_reason` column. `agent_framework_version` is read from the manifest's
sealed `player` block, falling back to `_distinct` over the Actor's runs without a manifest, so
the failure-path `AgentRun`'s deliberate `None` (T-053, unchanged) can no longer blank a completed
attempt's version. Run 11's two Belvar rows re-projected from the retained export now read
`completed=true`, empty `failure_reason`, the fault text under the new column and `2.42.0` in
both games (previously the fault text sat under `failure_reason` and one version cell was
blank). Retained CSVs under `dev/runs/` were not regenerated (export.json is the authority).
`benchmark-methodology.md` 0.3.7 (§25 column list and attribution note, own commit).
`TEST-I102-001…004` (8 functions, red first); full offline suite 1238 passed, 7 LLM deselected.
No decision needed; no follow-up filed. Brief:
`dev/archive/tasks/I-102-agents-csv-attempt-vs-activation-faults.md`.

### I-089 — Make the repository publishable as open source without moving code
**Closed:** 2026-09-21 · **Commit:** `9b3aceb` (spec/decisions `79c7eca`, gate `c84757b`) · **Decisions:** D-080, D-081
**Resolution:** MIT licence (D-080): `LICENSE`, `pyproject.toml` `license = "MIT"`, SPDX line in
`README.md`. Run evidence moves to a separate `amaw-runs` repository (D-081): `dev/runs/` pruned
from 14 tracked directories to the four retained ones (run 6, the I-027 report, the T-060 batch
smokes), each read for credentials/e-mails/secret fields before retention (none; the checkout
path and provider generation ids remain, unedited); the ten removed directories were copied and
file-counted into `~/Projects/AMAW-runs-archive/` before `git rm`; history not rewritten;
`AGENTS.md §8` and `RUNS.md` repointed. `docs/README.md` prompt index rewritten to name every
file and mark what `agents/` loads (it had named v1.2/v1.3 current since D-027/D-044), plus a
"Read in this order" list; new `docs/prompts/README.md`, `scenarios/README.md`,
`scenarios/northern-strait/README.md`, `dev/README.md`, `app/README.md`, `CONTRIBUTING.md`,
`SECURITY.md`; `README.md` gains a turn walkthrough, repository map and Contributing/Licence
sections and no longer sends readers to `dev/STATUS.md`. No package moves, no `docs/`
reorganisation, no change to `app/*.py`, `agents/`, `benchmark/`, prompts, fixtures, hashes,
export shapes or API bodies. Readiness gate recorded in `dev/gates/release-readiness.md`: fresh
clone → `uv sync` → import/tests/`--help`, three questions answered from non-`dev/` files; the
plan's `amaw run orison-crisis` step was **not executed** (no provider budget) — run 11's
retained export stood in, stated in the record. `TEST-I089-001…005` red first; T-066's licence
pin updated to MIT. Full offline suite 1230 passed, 7 LLM deselected. Brief:
`dev/archive/tasks/I-089-public-release-readiness.md`.

### I-092 — Cons-phase `send_message` attempts against a correct `available_actions`: steer, hide or keep measuring
**Closed:** 2026-09-21 · **Commit:** `5579f28` (spec `447f3e1`) · **Decision:** D-079
**Resolution:** Option (d) as policy, plus a sealed two-value condition instead of a permanent
choice between measuring and hiding. `single_shot` remains the normal/default player condition;
`tool_loop` is explicitly experimental and carries `PlayerConfig.tool_surface ∈ {broad,
available_only}`, default `broad` — today's behaviour byte-for-byte, so the Cons-phase attempts
stay observable invalid-tool behaviour and run 11 (`tool_loop`/`broad`) keeps its meaning.
`available_only` withdraws, through the I-091 `prepare` hook, every mutating tool the activation's
authoritative declaration (packet `available_actions`, or the D-077 live refresh) does not list;
the mapping is the identity with api.md §8.1 tool names, so `agents/` still infers no legality
and the server's checks are unchanged. Research tools and `single_shot` are unchanged; no
tool-schema version bump (definitions identical; the sealed field is the identity). Sealed in
`shared_condition()`, the resume recipe (optional key — legacy → `broad`, sealed as given), the
manifest `player.tool_surface`, every player `AgentRun.model_parameters`,
`PLAYER_CONDITION_PATHS`, `AMAW_PLAYER_TOOL_SURFACE` and `--tool-surface`; a `broad` game refuses
resume under `available_only` and vice versa. Outside `tool_loop` the value is accepted and sealed
as given (one invariant, no mode×surface special case). No prompt change (`player-v1.14` not
issued). `docs/agents.md` 0.3.2 (§5, §19); `guide/operations.md` documents the flag.
`TEST-I092-001…009` (13 functions; 005/006 red first, 004/007/008 compatibility guards); full
offline suite 1225 passed, 7 LLM deselected. Follow-ups: I-105 filed (`games.csv` convenience
columns); I-103 annotated (document the sibling race as a property of the experimental
condition, both surfaces). Brief: `dev/archive/tasks/I-092-tool-loop-tool-surface-condition.md`.

### I-099 — `ResourceAccess` recorded that a search happened but not what it returned
**Closed:** 2026-09-21 · **Commit:** `c499ad3` (doc `582a17c`) · **Decision:** — (observability inside TRD §29; methodology 0.3.6)
**Resolution:** A search row now carries `result_count`, `result_resource_ids` in returned order
and `result_set_sha256 = sha256(canonical_json(ids))`, taken from the already visibility-filtered
list the response is built from (api.md §10.1 by construction; no snippet, name, tag or text
copied). The digest is an ordered-result-list identity, not a set identity. Reads and legacy rows
are NULL (unavailable, never "zero results"); an I-054 replay is server-side, so it is a dispatched
operation and, as before, its own row with the identical ordered ids. Additive migration, export
format 13, `resource_access.csv` +3 columns. The research report adds `zero_result_searches`,
`distinct_result_sets`, `reformulations_after_identical_set` (a new query — not an exact repeat of
any earlier one — whose digest equals the immediately preceding search's),
`search_returned_read_conversions` (every read of a Resource a preceding same-activation search
returned) with `read_ranks` (rank in the most recent preceding list containing it) and
`searches_without_result_metadata`; the sequence-only `search_to_read_conversions` is kept beside
them. `TEST-I099-001…004` (6 functions); full offline suite 1212 passed, 7 LLM deselected. Gate
for I-100 now open. Brief: `dev/archive/tasks/I-098-I-099-research-operations-and-result-identity.md`.

### I-098 — `batched-v1` granted up to 8× the Resource operations of `sequential-v1` under the same guardrail
**Closed:** 2026-09-21 · **Commit:** `b699c0f` (spec `b43c69d`) · **Decision:** D-078
**Resolution:** A second guardrail, `max_research_operations` (released default the number 4),
charges every Resource search/read dispatched toward the server — sequential call or batched item
— immediately before dispatch under the tool-call lock: a dispatched request stays charged if it
fails; a client-side refusal (D-062 exact repeat, sibling beyond the budget) consumes none;
`read_messages` charges an invocation only. A batch crossing the remaining budget executes in
input order up to it and returns the rest, never attempted, under `not_executed` with
`guardrail_research_operations_exhausted` (deterministic truncation; an attempted item that fails
keeps the I-101 fail-fast shape). Resource tools are withdrawn when either budget is spent. Sealed
and disclosed through `GuardrailConfig`, `AMAW_MAX_RESEARCH_OPERATIONS`, `--max-research-operations`,
the resume recipe (legacy recipes resolve to 4 and stay sealed as given) and `limits`. Batched
schema identities v12→v15 / v14→v16; the sequential definitions are byte-identical and v11/v13
stand (pinned in the test). `AgentRun.research_tool_calls` / `research_operations` (nullable,
migrated, validated, export format 12). agents.md 0.3.1 (§18/§19), testing.md 0.3.8 (§29).
`TEST-I098-001…005` (17 functions); full offline suite 1206 passed at the commit, 1212 at close-out.
The two research modes now differ only in request topology, as D-051 claimed; no comparability
claim across v12→v15. Brief: `dev/archive/tasks/I-098-I-099-research-operations-and-result-identity.md`.

### I-097 — `--export-path` could overwrite the SQLite database; `amaw run` export written non-atomically
**Closed:** 2026-09-21 · **Commit:** `f846b4a` · **Decision:** — (D-073 rule extended to the single-run export)
**Resolution:** `amaw run` refuses an `--export-path` that resolves to `settings.database_path`
or one of SQLite's owned siblings (`-journal`, `-wal`, `-shm`, appended to the resolved filename)
with `error:` + exit 2, evaluated before `engine_for_path` on both the create and `--resume-game`
paths; other files beside the database stay allowed. The I-084 writer moved unchanged from
`benchmark/runner.py` to `app/export.py` as `write_json_atomic`; the runner, the final export and
the best-effort partial export all write through it (encoding unchanged). `TEST-I097-001` (9
cases) and `TEST-I097-002` (torn write, encoding pin, spies on both `run` paths); `TEST-I084-005`
repointed. Full offline suite 1183 passed, 7 LLM deselected. I-096 (engine ownership in the same
span) untouched. Brief: `dev/archive/tasks/I-093-I-095-I-097-boundary-hardening.md`.

### I-095 — `is_sensitive_key` false negatives: bare `token` and camelCase names passed
**Closed:** 2026-09-21 · **Commit:** `74db7a4` · **Decision:** — (D-075 (4) extended, none of it reversed)
**Resolution:** Names are normalised by word boundary before the shape rules: camelCase split at
lower/digit→upper and at an acronym's last capital, every non-alphanumeric run collapsed to one
`_`, lower-cased, stripped; bare `token` joins `SENSITIVE_KEYS`. `token`, `Token`, `accessToken`,
`authToken`, `apiToken`, `privateKey`, `HTTPToken`, `seat.token` are now refused; `maxTokens` →
`max_tokens` stays telemetry (`_tokens` still not a suffix) and `keyPoints` stays ordinary (`key`
still not a fragment). `TEST-I086-005` extended (27 positive / 20 negative names); the
real-content false-positive proof over the manifest builder, example plan and runtime fingerprint
passes unchanged. Values remain `redact_secrets`' job. Brief:
`dev/archive/tasks/I-093-I-095-I-097-boundary-hardening.md`.

### I-093 — `first_non_json_scalar` never validated mapping keys; recursive alias was a raw `RecursionError`
**Closed:** 2026-09-21 · **Commit:** `23d3236` · **Decision:** — (D-075 (2) extended to keys and cycles)
**Resolution:** The walker returns `NonJsonContent(path, value, reason)` with reason
`scalar | key | cycle`. A non-`str` mapping key is reported against the mapping's path and never
coerced, so `{1: …}` and `{"1": …}` no longer seal to one identity and `sort_keys` no longer
raises a raw `TypeError`; a container that is its own ancestor is reported as a cycle where it
recurs (ancestor set, not a global visited set — a reused non-recursive YAML anchor still loads).
`canonical_json` bytes unchanged (pinned tests green). `load_analysis_plan` names the path
(`'design' has a non-string mapping key 7 (int); quote it`, `'design[0]' is a circular structure`);
the bare-list `&a [*a]` document keeps the root-shape error first. `create_run_manifest` raises
`ProvenanceError` for both and now runs the JSON walk before the sensitive-key walk (the latter
has no cycle guard). `TEST-I093-001…003`, 20 functions. Brief:
`dev/archive/tasks/I-093-I-095-I-097-boundary-hardening.md`.

### I-091 — Player context goes stale after the Actor's own phase-advancing action
**Closed:** 2026-09-19 · **Commit:** `8bf01ee` (spec `4a44b08`) · **Decision:** D-077
**Resolution:** In `tool_loop`, after every successful server-mutating tool call the player
re-reads `GET /context`, attaches an agents.md §6 fragment (`turn, phase, active_actor_id,
requires_input, available_actions`) to the tool result, and withdraws — via PydanticAI `prepare`,
the I-069 mechanism — exactly those mutating tools the activation packet declared and the server
no longer lists. Tools the packet never declared are untouched, so the Cons-phase `send_message`
attempts (I-092) are still offered, tried and logged. The server computes the list (API-INV-009);
nothing infers a phase; no `app/` change; no prompt change (T-040 neutrality); `single_shot`
unchanged. The four `*_TOOL_SCHEMA_VERSION` identities move v7–v10 → v11–v14 (`…-live-…`), a
new sealed player condition; `testing.md` §29 names them (0.3.7). Refinement of the filed
acceptance: `ready`/`pass_con` after `make_argument` were never declared in the Argument-phase
packet, so they are informed, not hidden. Eight `TEST-I091-*` functions; full offline suite
1127 passed, 7 LLM deselected. Provider follow-up deferred to the next run: report
`invalid_tool_calls` by `(phase, action, error_code)` beside run 11's, no comparability claim
across v10→v11.

### I-086 — Tighten canonical serialization and untrusted export boundaries
**Closed:** 2026-09-19 · **Commit:** `e716b0c` · **Decision:** D-075
**Resolution:** `amaw_contracts.canonical_json` passes `allow_nan=False` (hash-neutral: the pinned
bytes and the example plan's `45d09488…c632c` are unchanged). `first_non_json_scalar` names the
path of the first non-finite float or non-JSON type, so `create_run_manifest` answers 400
`invalid_request` for a raw `NaN` body (previously sealed as `NaN`) and `load_analysis_plan`
raises `AnalysisPlanError` for an unquoted YAML date/timestamp or `.nan` — reject, not coerce.
Nested `analysis-plan-v1` mappings have explicit key sets and typed prose/enumeration fields,
errors by path; the shipped example and `VALID_PLAN` validate unchanged and still reconcile.
`is_sensitive_key` refuses credential-shaped keys by shape at both boundaries (the false-positive
proof over real manifest content rejected a `_tokens` suffix: `max_tokens` is telemetry).
`benchmark.outputs._cell` prefixes one `'` to strings starting with `= + - @ TAB CR '`
(reversible: strip exactly one) and dumps structures with `allow_nan=False`; `export.json` stays
the authority. No export version, column, docs or game-semantics change. Six `TEST-I086-*` IDs
(18 functions); full offline suite 1119 passed, 7 LLM deselected.

### I-085 — Resolve the real Git root in `code_version` and record a runtime-environment fingerprint
**Closed:** 2026-09-19 · **Commit:** `db7d35a` · **Decision:** D-074
**Resolution:** `code_version` detects the repository with `git rev-parse --show-toplevel` and
runs every later Git step at that toplevel, so the identity no longer depends on the caller's
path and the D-067 `dev/runs` exemption is evaluated against the real root. Detection failure
(no `git`, not a repository) keeps the `package:`/`unknown-build` fallback; any Git failure
after detection raises `CodeVersionError` — `amaw run` reports a failed run (exit 1), `amaw
benchmark` refuses before any game (exit 2), `resume()` refuses — so one checkout can never
carry two identities. The reported string for a clean or dirty checkout is unchanged.
`runtime_fingerprint()` (`amaw-runtime-fingerprint-v1`: Python, SQLite, nine frozen runtime
dependencies via `importlib.metadata`, `uv.lock` sha256, transport
`in-process-asgi-testclient-v1`, canonical sha256; `None` for anything unavailable, no
paths/usernames/env values) is the reserved manifest section `environment`, built by the one
builder and therefore sealed before game 1; `environment.sha256` joins
`COMMON_CONDITION_PATHS` (both benchmark modes); `games.csv` gains `environment_sha256`; a
legacy manifest projects `None`. No `docs/` change. `TEST-I085-001…007` (10 functions) in
`tests/test_i085_runtime_identity.py`; full offline suite 1046 passed, 7 LLM deselected.
Brief: `dev/archive/tasks/I-085-runtime-identity.md`; execution prompt
`dev/archive/tasks/I-085-runtime-identity.PROMPT.md`.

### I-084 — Harden benchmark runner/output mechanics at large counts and interrupted runs
**Closed:** 2026-09-19 · **Commit:** `9b57f46` · **Decision:** D-073
**Resolution:** Five local fixes on the `benchmark/` boundary. `build_schedule` and
`amaw benchmark --repetitions` refuse more than 999 repetitions per assignment — the
collision-free range of the recorded seed policy — with `SEED_POLICY`, `game_seed` and every
in-range schedule unchanged, so no existing manifest or sealed plan changes identity.
`load_attempts` orders by the integer `position` in `attempt.json`, not the directory name.
`batch.json.completion_rate` keeps the planned denominator (methodology §21, D-068) and is
joined by `planned_completion_rate` and `execution_success_rate` (`None` before the first
attempt). `games.csv.turns_completed` counts `turn_completed` events while `turn_reached`
keeps `game.turn`. `_write_json` writes a sibling temp file, fsyncs, `os.replace`s and fsyncs
the directory, so a kill mid-write cannot truncate `batch.json`/`attempt.json`/`export.json`.
No `docs/` change. `TEST-I084-001…005` (8 functions) in
`tests/test_i084_benchmark_output_mechanics.py`; full offline suite 1035 passed, 7 LLM
deselected. Brief: `dev/archive/tasks/I-084-benchmark-output-mechanics.md`.

### I-081 — Make execution provenance invocation-owned and durable across sibling failure
**Closed:** 2026-09-19 · **Commit:** `82292e0` · **Decision:** D-072
**Resolution:** The coordinator persists every outcome the moment it reaches its thread —
serial sweep before observing the next Actor, diplomacy round before settlement, concurrent
sweep as each future resolves in input order, the first error raised only after every
completed sibling is durable — so an unexpected sibling failure, `ActivationLimitExceeded`, a
refused settlement or a failed admin call can no longer leave committed Con/Message state
without its `AgentRun`; the `stuck` judgement moves after the whole sweep is recorded.
`app/umpire.py` gains the frozen `UmpireAdjudication` and `UmpireProvenanceSource` becomes
`provenance_configuration()` + `adjudicate_recorded()`; `RecordedUmpire` is the single-use
adapter `/advance` hands `resolve_adjudication`; `AIUmpire` keeps attempts in a call-local
collector (no queue, no drain), so concurrent adjudications on the one app-level Umpire cannot
interleave records. Row values and the D-065 overlay precedence unchanged. The
`(game_id, activation_id)` uniqueness it asked for had landed in I-082. The optional typed
failure codes were not taken up. `TEST-I081-001…006` (7 functions) in
`tests/test_i081_invocation_owned_provenance.py`; full offline suite 1027 passed, 7 LLM
deselected. Brief: `dev/archive/tasks/I-081-invocation-owned-provenance.md`.

### I-082 — Turn declared SQLite integrity rules into enforced database guarantees
**Closed:** 2026-09-19 · **Commit:** `65a2b80` (spec `55e3af9`, merge `6712c42`) · **Decision:** D-071
**Resolution:** One persistence-integrity pass, no rewrite. Every app engine sets
`PRAGMA foreign_keys=ON` on each connection, so the parent relationships `app/models.py` declares
are enforced (a trial had shown only fourteen model-level tests seeding orphans; they now seed
parents). `SCHEMA_VERSION = 1` is stamped into SQLite's `PRAGMA user_version` by
`initialize_database` after its additive steps; a newer stamp is refused and a pre-stamp database
with existing orphans (`PRAGMA foreign_key_check`) is refused, not repaired. `agentrun(game_id,
activation_id)` has a unique index (NULL Umpire rows distinct; legacy tables gain it additively),
closing the read-before-insert race. The export carries a live-read `database` block
(`schema_version`, `foreign_keys_enforced`) under `export_format_version "11"`, and its
`run_summary` is now built in the export's own transaction; `POST /activations` reads the
delivery snapshot and the context in one `BEGIN IMMEDIATE` transaction. `api.md` 0.4.3 §15.1.
Six `TEST-I082-*` functions, each red first; full offline suite 1020 passed, 7 LLM deselected.
The real `data/amaw.db` was verified clean on a copy. Brief:
`dev/archive/tasks/I-082-persistence-integrity.md`.

### I-079 — Close Argument/Cons edge-state holes without adding unused game-loop scope
**Closed:** 2026-09-18 · **Commit:** `5082c03` (spec `7f02095`, guide `2b3df35`) · **Decision:** D-069
**Resolution:** Three local corrections, no new game-loop scope. The scenario loader refuses
`primary_arguments_per_actor != 1` (the engine has no §20 step returning the phase to the same
Actor, so any other budget silently played as 1) and fewer than two Actors (no eligible opponent);
`Game` columns, slot budgeting and the hand-seeded slot tests stay so multi-slot sequencing can be
built deliberately later. `make_argument` closes Cons in the same transaction when the eligible
set is already (vacuously) complete, so the engine never strands a one-Actor game. The single-shot
executor no longer passes when every requested `make_con` was rejected: the opportunity stays
open for the lifecycle's bounded reactivation and stall handling (agents.md §18 — the harness never
fabricates the decision); partly accepted decisions complete as before. `scenario-format.md`
0.2.2 (§6, §19). Eight `TEST-I079-*` functions, the lifecycle one proven against the pre-fix
executor (72 vs 36 Cons activations). Brief:
`dev/archive/tasks/I-079-I-080-cons-edge-states-and-event-sealing.md`.

### I-080 — Do not leak sealed Con participation or completion order through `/events`
**Closed:** 2026-09-18 · **Commit:** `c2ec01b` (spec `d12bc47`) · **Decision:** D-070
**Resolution:** `con_submitted` and `con_passed` left the Actor-visible event set — they carry
the opponent's `actor_id` and `created_at`, i.e. participation and completion order, which
INV-CON-007 forbids under both reply policies — and are hidden from every Actor, the author
included. `cons_revealed` (frozen `con_ids` in canonical `(actor_id, con_id)` order, emitted only
under `visible_reply` at close) became Actor-visible, matching `current_argument.cons`;
`owner_reply_submitted` and `con_challengers_selected` stay filtered. The authoritative log,
admin export and the ordinal rule are unchanged. `api.md` 0.4.2 §13.1 now states the per-type
policy it had never listed. Three `TEST-I080-*` functions (the leak reproduced red first;
cestia-first completion order asserted invisible). Full offline suite for the batch: 1014
passed, 7 LLM deselected. Brief: `dev/archive/tasks/I-079-I-080-cons-edge-states-and-event-sealing.md`.

### I-083 — Replace drifting execution-condition field lists with canonical validation/projections
**Closed:** 2026-09-18 · **Commit:** `ffb6189`
**Resolution:** One projection per boundary instead of four hand-kept lists.
`PlayerConfig.shared_condition()` (every field but `model`, from the dataclass fields) is what
`AutonomousGameRunner._manifest_configuration` now compares across the roster, naming the
differing fields — a roster differing in temperature, timeout, reasoning effort or any guardrail
is refused rather than sealed under the first player's settings (§5.1 / agents.md §19).
`_manifest_configuration_for` refuses a `run_configuration` carrying the runner-derived sections
(`player`, `code_version`, `coordinator`, `game_loop`) and checks a supplied `resume` recipe
semantically against the players, coordinator and game-loop condition, so legacy recipes still
seal byte-for-byte (I-061) while one describing other players is refused. The
`autonomous-resume-v1` vocabulary lives in `amaw_contracts` (required and optional key sets for
player, Umpire, coordinator and guardrails); `build_resume_recipe` writes from it and
`app/resumption.py` validates from it, type-checks the optional keys when present and checks the
Diplomacy condition against the `Game` snapshot like Con and Con-reply.
`InferenceProfile.from_document` validates structure before honouring the reserved `uniform`
name. No manifest content, CLI, export or `app/` boundary change; no decision needed. Twenty-seven
`TEST-I083-*` functions; full offline suite 1003 passed, 7 LLM deselected. Brief:
`dev/archive/tasks/I-083-canonical-condition-projections.md`.

### I-078 — Reconcile declared, scheduled, executed and exported benchmark conditions
**Closed:** 2026-09-18 · **Commit:** `def0a8d` (spec `52c6c7e`) · **Decision:** D-068
**Resolution:** One pre-run expectation instead of more pairwise checks. `benchmark/controls.py`
gained `ExpectedBatchCondition` — the manifest configuration the runner will submit, built by
the runner's own builder (`agents.autonomous.expected_manifest_configuration`, shared with the
recording path), plus the loaded scenario's hashes and the sealed Umpire condition, projected
onto `HELD_CONSTANT_PATHS` and recorded with a `sha256` in `batch.json` before game 1 — and
`ExpectedAttempt` (seed, `player.assignments`, `resume.player.assignments`,
`benchmark.assignment*`, `repetition_index`) per scheduled game. `_verify_attempt` holds every
manifest, attempt 0 included, to the whole §5.1 set in `model` mode and to
`COMMON_CONDITION_PATHS` in `agent` mode, plus its expected attempt; a completed attempt without
a manifest is `missing_manifest`. `held_constant_sealed_from` is gone; the D-058 stop keeps its
shape with kinds `umpire_condition`/`expected_condition`/`expected_attempt`/`missing_manifest`.
`reconcile_plan` refuses, before any game, a plan whose `repetitions_per_assignment`,
`planned_attempts` (whole experiment, or one cell per treatment level — the shipped example's
shape) or `treatment.levels` disagree with the schedule; the prose policies are recorded, not
compared (I-090). `verify_registration` reports `sealed_before_start` and classifies a plan
sealed after `game_started` as `post_hoc_plan`. `run_batch` and the CLI refuse an output
directory that is not absent or empty. `games.csv` gained `seed_observed`/`condition_match`,
`agents.csv` `model_assigned`. Server: run-manifest *creation* is lobby-only (exact retry
preserved, different configuration still a conflict); `POST /admin/games` accepts an immutable
`require_run_manifest` (additive `game` column) and `start_game` refuses a flagged Game without
a sealed manifest; the autonomous runner always sets it; the §14 guard is unchanged for Games
that do not opt in. `state-machine.md` 0.6.2, `api.md` 0.4.1, `guide/operations.md` Benchmarks
rewritten. Tests `TEST-I078-001…008` (21 functions) across `tests/test_benchmark_controls.py`,
`tests/test_benchmark_runner.py`, `tests/test_i047_analysis_plan.py`,
`tests/test_i078_precommitment.py`; full offline suite 976 passed, 7 LLM deselected. T-063 is
unblocked.

### I-076 — Benchmark batch output written inside the worktree drifts `code_version` between attempts
**Closed:** 2026-09-18 · **Commit:** `dc72c19` · **Decision:** D-067
**Resolution:** Option (a). `agents/code_version.py::_GENERATED_RUN_EVIDENCE_NAMES` now also
exempts the `amaw benchmark` batch layout — `batch.json`, `games/<attempt>/attempt.json`,
`games.csv`, `agents.csv`, `arguments.csv`, `messages.csv`, `resource_access.csv` — and the launchd
helper's `launchd.plist`, under the unchanged I-044 guards: below `dev/runs`, ordinary regular
file, not executable. A batch written to the documented `dev/runs/<batch>` location therefore
reports the same `code_version` on every attempt and the D-058 held-constant check no longer stops
a `model` batch on its own evidence; the resume check (`agents/autonomous.py`) and an unattended
run's identity benefit the same way. The digest format is untouched, so no previously reported
identity changes. Refusing an in-repo `--output-dir` (b) or documenting "write outside the
checkout" (c) were rejected: both contradict the shipped README/operations guide and the
`dev/runs` evidence convention, and neither covers the launcher or the resume path. Correction to
the filed entry: the launcher writes only `launchd.plist`; there is no `pid` file. Guards:
`tests/test_code_provenance.py` `test_i076_001` (batch layout stable across the runner's
second-attempt writes), `test_i076_002` (launchd run directory reports exact HEAD),
`test_i076_003` (the exception is not widened — same names outside `dev/runs`, executable copies
and arbitrary files in the batch directory still dirty the identity). Red: 001/002 failed on the
`-dirty-` suffix. Green: 8 passed in the file; full offline suite 947 passed, 7 LLM deselected. No
provider run; `test_t060_002_provider_batch_smoke` still awaits its green end-to-end record.
Evidence: `dev/archive/tasks/I-076-batch-output-code-version.md`.

### I-077 — Enforce one activation-wide request, tool and research budget
**Closed:** 2026-09-18 · **Commit:** `15bf912` · **Decision:** D-066
**Resolution:** `agents/player.py` `tracked_tool` now decides under `_tool_call_lock` *before*
the tool runs: a key already in `_successful_research_calls` returns `guardrail_research_duplicate`
and closes the research surface (D-062 exact-repeat closure); a key in the new
`_inflight_research_calls` reservation is rejected without closing it (PydanticAI runs one
response's sync tools concurrently, so a check alone let two identical calls both reach the
server); otherwise the key is reserved, executed outside the lock, released in `finally`, and
remembered only on success. A duplicate therefore never reaches the server — one `ResourceAccess`
row — while still counting as a tool/research call and a `client_guardrail` invalid-call event.
Before the transient retry the remaining request/tool allowance is computed from
`_TimedModel.metrics()[0]` and `_tool_calls_this_activation` (both already span the two attempts);
either `< 1` fails the activation `guardrail` without a second `run_sync`, otherwise the retry gets
exactly the remainder. Ceiling-failure counts come from one `_ceiling_counts` helper.
`GuardrailConfig.__post_init__` mirrors `Settings`' bounds. `_classify_activation_error`'s
docstring now states that `UnexpectedModelBehavior` is deliberately retried once (behaviour
unchanged). Guards: `tests/test_guardrails.py` `test_i077_001a/b` (sequential and concurrent
duplicate research → one row), `test_i077_002a–d` (retry remainder at both boundaries for
requests and tools, provenance totals), `test_i077_003` (config bounds). `test_i050_usage_survives…`
and `test_i056_003…` had pinned four requests across two attempts under `max_iterations=2` — the
defect — and now use `max_iterations=4` with every assertion unchanged. No prompt/tool-schema bump;
no model-visible text changed. Full offline suite: 944 passed, 7 deselected. Brief:
`dev/archive/tasks/I-077-activation-wide-budget.md`.

### I-075 — Post-I-063 dead-code scan leftovers
**Closed:** 2026-09-18 · **Commit:** `363599f` · **Decision:** —
**Resolution:** The four items the 2026-09-18 reassessment filed
(`dev/plans/2026-09-18-post-i063-reassessment.md §1`), taken before I-064 because it is pure
housekeeping with no shared surface. `agents/tools.py::TOOL_SCHEMA_VERSION = "player-tools-v3"`
deleted — zero references; no shipped doc cites the symbol or the string as a live value (the
string survives only as literal test-row data and in historical `dev/` process notes and run
exports); the live identity is `agents/player.py` `*_TOOL_SCHEMA_VERSION` via
`ReferencePlayer.tool_schema_version`, and the I-034 toolset comment now says so instead of
claiming manifests pin the deleted constant. Unused `import time` dropped from
`agents/umpire.py`. The 19 ruff F401/F841 findings in 14 `tests/` modules resolved: 17 import
removals by safe autofix (diff reviewed), the two F841s by hand (`test_guardrails.py` never-read
`seen` removed; `test_i043_con_capacity.py` keeps the `start_to_cons()` call and drops the
binding). `dev/tasks/I-022-I-027-throughput-accounting.md` moved to `dev/archive/tasks/`.
`uvx ruff check --select F` over source and tests: clean; ruff stays evidence, not a dependency
or a test. Guards: `tests/test_i075_dead_code_leftovers.py` (2 tests; the brief guard counts only
`### I-0NN — …` resolution headings so prose mentions of open issues do not count). No manifest
hash, export shape, API body, column, prompt or test assertion changed; `docs/` untouched. Full
offline suite: 933 passed, 6 deselected, 2 warnings. Brief:
`dev/archive/tasks/I-075-dead-code-leftovers.md`.

### I-065 — Move Umpire provenance normalisation out of `app/api.py`; make the provenance capability an explicit protocol
**Closed:** 2026-09-18 · **Commit:** `3f61d74` · **Decision:** D-065
**Resolution:** `app/umpire.py` now names the seam: `UmpireProvenanceSource`, a second
`runtime_checkable` Protocol (`provenance_configuration()`, `drain_provenance()`,
`last_failure`) beside an unchanged `Umpire`, and `UmpireAttemptRecord`, the TypedDict of the
normalised attempt record the Umpire boundary emits (identity, five canonical token buckets,
call/billing counts, framework and schema version, outcome, provider metadata — no raw `usage`,
no server context). `AIUmpire` builds every record through one `_attempt_record` builder
replacing the outage/success literals; `_usage_cost` moved beside `_normalize_inclusive_usage`,
the single normaliser (the player path's `_usage_integer` reads PydanticAI objects and is not
dict-reusable, so the *rule* is shared, not the code). `app/api.py` lost `_usage_integer`,
`_usage_cost` and `importlib.metadata`; its three `getattr` probes are one `isinstance` each; the
advance route adds only `actor_id=None`, `role`, `turn`, `phase` and the unchanged overlay
(`completed=False`, `failure_reason = umpire.last_failure or ExceptionName`). `FakeUmpire`
gained `last_failure = None`. Named `…Source` because `UmpireProvenance` is the existing
dataclass. Deliberate tightening (D-065): provenance is one atomic optional capability. Golden
Umpire `AgentRun` rows identical before/after on success, outage, unusable output, semantic
retry and server-side refusal (8 rows, empty diff excluding ids/timestamps/measured latency).
Guarded by `tests/test_i065_umpire_provenance_protocol.py` (4 tests). No manifest hash, export
shape, API body or column changed; `docs/` untouched. Full offline suite: 931 passed,
6 deselected, 2 warnings. Brief: `dev/archive/tasks/I-065-umpire-provenance-protocol.md`.

### I-063 — Extract `agents/inference_profile.py` and `agents/code_version.py`; delete dead helpers
**Closed:** 2026-09-18 · **Commit:** `23403d2`
**Resolution:** `agents/inference_profile.py` now owns `InferenceProfile`, `InferenceProfileError`,
`load_inference_profile`, `INFERENCE_PROFILE_UNIFORM(_NAME)` and their private helpers, moved
verbatim out of `agents/player.py` (1863 → 1693 lines), which imports only what `PlayerConfig`
needs. No compatibility re-exports (decided at task-start, same reasoning as I-062; the entry's
"with re-exports" wording was amended): `amaw_cli.py` and four test modules re-pointed,
`app/resumption.py` docstring path updated. `agents/code_version.py` exposes one name,
`code_version(root=None)`; the worktree-hashing helpers and `_GENERATED_RUN_EVIDENCE_NAMES` are
private there, moved from `AutonomousGameRunner` static methods (`autonomous.py` 1229 → 1085
lines); the string was verified identical to `HEAD`'s implementation on the same dirty worktree,
`AMAW_CODE_VERSION` precedence and fallbacks unchanged, `tests/test_code_provenance.py` (i044 ×5)
re-pointed. Deleted zero-reference `app/models.py::DiplomacyMode` and
`agents/tools.py::PlayerToolContext`, `PlayerTools.for_base_url`, `_DEFAULT_BASE_URL` after
confirming none is exported by a package `__init__` or named in `README`/`guide/`/`docs/`.
Guarded by `tests/test_i063_agent_module_extraction.py` (4 tests). No manifest hash, export
shape, API body or column changed. Full offline suite: 927 passed, 6 deselected, 2 warnings.
Brief: `dev/archive/tasks/I-063-agent-module-extraction.md`.

### I-062 — Split derived reports out of `app/provenance.py`; rewrite ledger docstrings
**Closed:** 2026-09-18 · **Commit:** `b625d37`
**Resolution:** `app/summary.py` now owns the derived reports (`build_game_summary`,
`format_game_summary`, `build_resource_research_report`, the Con/Con-reply condition summaries
and helpers, `PHASES`), moved byte-identically; `app/provenance.py` is recording and validation
only (1019 → 418 lines) and never imports `summary`. No compatibility re-exports: `api.py`,
`export.py` and six test modules were re-pointed (`benchmark/outputs.py`, named in the entry,
never imported `provenance`). The T-0NN ledger docstrings of `game_service.py` (72 → 14 lines),
`api.py`, `amaw_cli.py` and `provenance.py` now state responsibility and owned invariants in
≤15 lines; inline citations at rule sites untouched. Guarded by
`tests/test_i062_summary_split.py` (3 tests). No manifest hash, export shape, API body or
column changed. Full offline suite: 923 passed, 6 deselected, 2 warnings. Brief:
`dev/archive/tasks/I-062-provenance-summary-split.md`.

### I-074 — Retry context-ineligible Umpire supersession output within D-063
**Closed:** 2026-09-18 · **Commit:** `4a095d9` · **Decision:** D-064
**Resolution:** A parsed AI-Umpire proposal is now checked against the active Effect ids in its
exact authorised context before it can reach mutation. Unknown/foreign/inactive or duplicate
supersession references are retained as typed semantic-output failures with provider, finish
reason, generation id and offending ids; the emitting downstream provider is cumulatively
excluded and the same model/prompt/schema/inference request retries inside D-063's existing
four-call bound. Exhaustion raises `UmpireUnavailable` and takes the established atomic pause path.
The database-backed `_validate_supersession` remains unchanged and authoritative; arbitrary
semantic `422`s are not retried and no proposal is repaired. Focused affected suite: 80 passed,
1 deselected; full offline suite: 920 passed, 6 deselected, 2 warnings. No provider call, prompt/
schema change or game launch was made. Brief:
`dev/archive/tasks/I-074-retry-invalid-supersession.md`.

### I-061 — One autonomous-session composition path; one resume-recipe builder
**Closed:** 2026-09-17 · **Commit:** `f9c35ac`
**Resolution:** Structural cleanup C2 (review F1/F3/F5), behaviour-preserving. `amaw run` and
`amaw benchmark` share ten private composition helpers and each reads parse → resolve the
condition → compose and drive → write. `agents.autonomous.build_resume_recipe` is the only
`autonomous-resume-v1` builder and `PlayerConfig.from_resume_recipe` its inverse (byte-identical
round trip; legacy recipes resolve to the released defaults); the CLI's duplicate builder and
28-local unpacking are gone. `RESUME_OVERRIDE_FLAGS` is the explicit named override-refusal set,
guarded by `test_i061_001` against drift from `run`'s condition flags. `_agent_run_body` carries
the shared AgentRun fields for `_record_activation`/`_record_failure`; the failure-side
`agent_framework_version: None` is preserved. No manifest content, export shape, flag, error text
or exit code changed except that a sealed recipe failing `PlayerConfig` validation on resume now
reports "not resumable" (exit 1). `app/` untouched; import boundary green. Full offline suite
914 passed, 6 LLM deselected. Brief: `dev/archive/tasks/I-061-cli-composition-path.md`.

### I-071 / I-072 / I-073 — Bounded Umpire attempts, per-call deadline, generation id
**Closed:** 2026-09-17 · **Commit:** `f3d7ef0` (decision and doc amendments: `67f2a21`)
**Resolution:** D-063. `AIUmpire.adjudicate` owns the whole outage budget: at most
`UMPIRE_MAX_ATTEMPTS = 4` model calls per adjudication, a transient provider failure or an
unusable completion each consuming one, the downstream provider behind an unusable completion
excluded for every later attempt (D-061 made cumulative), deterministic failures stopping at once,
pause after the last with nothing fabricated. `OpenRouterClient.complete` takes `max_attempts`
(the Umpire passes 1; the default 2 is unchanged for other callers) and `ModelConfig.deadline`:
the body is streamed and abandoned as a transient failure once the wall clock passes it (Umpire
default `UMPIRE_CALL_DEADLINE_SECONDS = 600`), since httpx's per-read timeout never fires on a
kept-alive response. `attempts` and `deadline` are in the Umpire's provenance configuration and
`deadline` in every call row; completed rows carry OpenRouter's `generation_id`. TRD §35 (0.5.4)
and state-machine §19 (0.6.1) amended. Tests `test_i071_001…005`, `test_i072_001…003`,
`test_i073_001`; four legacy two-attempt pins updated.

### I-070 — Null-content Umpire completion bypasses the D-061 provider-excluding retry
**Closed:** 2026-09-17 · **Commit:** `222d5f3`
**Resolution:** `agents/model_client.py` `_parse` now returns a completed choice whose
`message.content` is `null` as an empty completion instead of raising a transport-level
`transient` error. The Umpire's existing unusable-output retry (D-061) therefore handles it exactly
as it handles `""`: one retry, same model and settings, failing downstream provider added to
`provider.ignore`, `finish_reason` and provider kept in `last_failure`/provenance, two attempts in
total, pause on the second failure. Genuinely malformed 200 envelopes (no choice, no message,
non-string content) stay transient (I-009/I-023). Found in run
`swedish-government-v6-b3-k2-batched-compact-fixed-20260917-161254`, where both attempts went to
OpenInference (`gen-1789656628-2x6llSJ2uUen3deJus2p`, `gen-1789657317-fM5Q6qNbXPbcc715beLi`).
Tests: `test_i070_001…003`.

### I-069 — Bound player research loops and retry required decisions without fabrication
**Closed:** 2026-09-17 · **Commit:** `222d5f3`
**Resolution:** D-062 adds a separately configured, immutable
`max_research_tool_calls` guardrail (default 4) inside the existing total request/tool ceilings.
Every activation packet now states all three budgets. Research tool descriptions state remaining
capacity; all research tools are withdrawn when the subset is spent, an exact successful call is
repeated, or one request/tool call must be reserved for the required decision. The new
`player-v1.10`…`v1.13` prompts explicitly require batching, retained-result reuse and a timely
decision, and the changed surfaces have new tool-schema identities. A still-current required
Argument/Reply that fails for a guardrail or exhausted transient attempt now receives another
fresh activation while the existing finite per-opportunity bound has room; deterministic or
repeated failures still stop, and no decision is fabricated. The CLI/env setting, manifest,
resume recipe and AgentRun guardrail provenance retain the research bound. Focused suite: 160
passed, 1 LLM test deselected; full offline suite: 899 passed, 6 LLM tests deselected. No provider
call, scenario change or game launch was made. Brief:
`dev/archive/tasks/I-069-bounded-research-recovery.md`.

### I-068 — Align the provider adjudication schema with authoritative branch limits
**Closed:** 2026-09-17 · **Commit:** `222d5f3`
**Resolution:** The ten-item Effect/supersession branch limit now lives at the framework-neutral
Umpire boundary and is consumed by both the authoritative engine validator and the strict JSON
schema sent to providers. All four arrays advertise `maxItems: 10`; supersession arrays also
advertise `uniqueItems: true`, while their per-adjudication enum may still contain every eligible
active Effect. The server continues to revalidate size, uniqueness and eligibility inside the
transaction and fail closed. Regression coverage reproduces the failed game's 31-Effect context
and proves oversized proposals create no adjudication, draw no RNG and mutate no Effects. Focused
suite: 60 passed, 1 LLM test deselected; full offline suite: 894 passed, 6 LLM tests deselected.
No provider call or game launch was made. Brief:
`dev/archive/tasks/I-068-umpire-schema-branch-bounds.md`.

### I-067 — Make unattended runs provider-resilient and genuinely one-shot
**Closed:** 2026-09-17 · **Commit:** `222d5f3`
**Resolution:** D-061 keeps the exact Umpire model, prompt, schema, inference settings and bounded
two-attempt policy, but the second attempt now adds the first unusable response's concrete
downstream provider to OpenRouter `provider.ignore` while preserving `require_parameters`. This
prevents a nominal HTTP-200 reasoning-only/malformed endpoint response from being retried through
that same endpoint; a second unusable result still pauses without fabrication. The source release
now also ships `amaw-unattended`, a macOS helper that creates a fresh run directory, retains an
explicit launchd plist, and bootstraps exactly one `uv run amaw run` with `RunAtLoad=true` and
`KeepAlive=false`, no persisted credential, and combined stdout/stderr. The operations guide uses
the current Swedish v6 package, fresh seed/run names, warns against `launchctl submit`, and keeps
`nohup` as the ordinary POSIX path. Affected suite: 61 passed, 2 deselected; full offline suite:
891 passed, 6 LLM deselected; sdist contains the helper and guide. Brief:
`dev/archive/tasks/I-067-unattended-run-resilience.md`.

### I-066 — Make Umpire failure provenance non-fatal
**Closed:** 2026-09-17 · **Commit:** `222d5f3`
**Resolution:** The Swedish v6 overnight playtest exposed two independent Umpire-provenance
failures. Routed provider usage can report a reasoning/cache subset larger than its nominal parent;
the Umpire's provider boundary now records the parent as the conservative `max(parent, subsets)`,
retains every subset, and annotates the adjustment in `provider_metadata.usage_normalization`
instead of aborting after the authoritative adjudication. The outage path now extracts
`completed` and `failure_reason` unconditionally before composing the AgentRun, eliminating the
short-circuit duplicate-keyword `TypeError`; a missing Umpire failure string falls back to the
resolution exception class. Two API-level regression tests reproduce both overnight failures.
Malformed Umpire output still follows the normative bounded policy: retry once, pause, return
`503 adjudicator_unavailable`, retain the attempts as incomplete Umpire AgentRuns, and create no
RNG draw or adjudication. Focused affected suite: 66 passed, 2 deselected; full offline suite:
887 passed, 6 LLM deselected. Brief: `dev/archive/tasks/I-066-umpire-failure-provenance.md`.

### I-060 — Introduce `GameLoopCondition` and shared canonical-JSON/contract helpers
**Closed:** 2026-09-16 · **Commit:** `224ddca`
**Resolution:** Cleanup C1 of the structural review (`dev/plans/2026-09-16-structural-cleanup-review.md`
F2/F9/F10). The six-field Diplomacy / Con / Con-reply condition is one frozen `GameLoopCondition`
in `app/game_loop.py` (`validate()`, `from_mapping()`, `as_dict()`, `provenance()`, policy-version
and mode constants). Its api.md §14 pairing rules were written five times, not three — `Settings`
and the `AutonomousGameRunner` constructor had copies too — and all five now delegate; scenario
creation keeps only the K ≤ opponents check; the runner holds one condition object (create body,
manifest `game_loop`, both AgentRun `model_parameters` blocks, recipe fallback and resume comparison
read from it) and the CLI reads a recipe through `from_mapping`. `amaw_contracts.py` gained
`canonical_json`, `canonical_sha256`, `SENSITIVE_KEYS` and the `inference-profile-v1` vocabulary,
replacing six canonical-JSON and two sensitive-key copies; `agents/player.py` re-exports the
vocabulary and `app/resumption.py` reads it without importing the agent layer.
`game_service._semantic_hash` deliberately keeps its own `default=str` encoder (a `make_con`
payload carries a `UUID`; the shared encoder raised). No wire, persisted, manifest-hash, error-class
or exit-code change. New `tests/test_game_loop.py` (20) and `tests/test_contracts.py` (5); full
offline suite 885 passed, 6 LLM deselected. Brief: `dev/archive/tasks/I-060-game-loop-condition.md`.

### I-059 — `docs/analysis-plan-example.yaml` is missing from the source distribution
**Closed:** 2026-09-16 · **Commit:** `222d5f3`
**Resolution:** `MANIFEST.in` shipped `docs/` as `*.md` only, so the sdist carried
`benchmark-methodology.md §4.2` and `guide/operations.md` — both of which name
`docs/analysis-plan-example.yaml` as the complete example plan — without the file. The `docs`
include is now `*.md *.yaml`, matching the `guide` include widened in I-032. Guarded by
`test_i059_docs_example_yaml_is_in_the_sdist` (file present in the checkout and the manifest's
`docs` include covers `*.yaml`); confirmed against a real `uv build --sdist` archive, which now
lists `amaw-0.1.0/docs/analysis-plan-example.yaml`. Packaging only — no engine, docs or CLI change.

### I-046 — Add a configurable visible-rebuttal and bounded Argument-owner reply protocol
**Closed:** 2026-09-16 · **Commit:** this commit (spec: `0c3b246`)
**Resolution:** D-060 keeps released `sealed_cons` (Cons reach only the Umpire, no reply) as the
default — byte-identical context packet, tool schema, prompts and Umpire rendering — and adds the
persisted, versioned `visible_reply` treatment with a required `reply_max_points` (1–3). Cons are
still collected concurrently and sealed from each other; when they close with at least one Con the
server marks the Argument `awaiting_reply`, emits `cons_revealed` and opens a `reply` phase in which
only the owner acts, once: `POST …/reply` / `reply_to_cons` / single-shot `ReplyDecision` with
0–N points each naming a distinct revealed `con_id` (empty = pass), persisted as one `ArgumentReply`
and closed to `adjudication` in the same transaction; same-key replay returns the record, any fresh
attempt is `409 already_participated`; zero Cons skip the phase. The reveal is rendered in the
canonical `(actor_id, con_id)` Umpire order, never completion order, and stays visible to every
Actor for the turn (`current_argument.cons/owner_reply`, `revealed_exchanges`); nothing hidden is
projected. The Umpire receives `owner_reply` (with `con_id`s) only under `visible_reply`; the Umpire
prompt is an explicit condition (`sealed_cons` keeps `umpire-v1.3`, `visible_reply` requires
`umpire-v1.4`, `--umpire-prompt-version` pins it for the A/B), and `AIUmpire` now actually loads the
prompt of its declared version. `visible_reply` selects `player-v1.8`/`player-v1.9` and the
`player-tools-v5-reply`/`v6-batched-reply` schema identities; `reply` is an optional inference phase
resolving from `argument`. Env/CLI/admin creation, Game, manifest `game_loop.con_reply`, resume
recipe, AgentRun, export v10 (`argument_replies`) and the summary `con_reply_condition` carry the
condition; legacy rows stay null. Evidence: `dev/archive/tasks/I-046-visible-reply.md`;
TEST-I046-001…015 (32 tests) and the full offline suite passed (859 passed, 6 LLM deselected). No
provider run, `sealed_cons` vs `visible_reply` evaluation or default change was made; reply
timeouts, admin force-advance of `reply` and anonymous rebuttal remain out of scope.

### I-032 — `run_profile` (fast/quality) with per-phase inference budget
**Closed:** 2026-09-16 · **Commit:** `b1f6007`
**Resolution:** D-055 adds a declared `inference-profile-v1` document — optional
`reasoning_effort`/`max_tokens` per player phase (`diplomacy`, `argument`, `cons`) and for the
Umpire — selected by `--inference-profile`/`AMAW_INFERENCE_PROFILE`. Unset values inherit the
flat `--player-*`/`--umpire-*` settings; no profile is the released `uniform` behaviour with
byte-identical requests. The player applies the phase-resolved values as per-run model settings,
the Umpire resolves its entry once, the manifest seals the *resolved* per-phase table and the
recipe the document, and every player AgentRun (success and failure) records the requested
phase values beside actual `reasoning_tokens`. Invalid documents fail before any Game exists,
resume reconstructs the sealed profile and refuses the flag, mixed profiles are rejected. No
built-in `fast`/`quality` presets — budgets are written per experiment
(`guide/inference-profile-example.yaml`). Evidence: `dev/archive/tasks/I-032-inference-profile.md`;
8 focused acceptance tests, 112 affected tests and the full offline suite passed (784 passed,
6 LLM deselected). No provider run, behavioural-equivalence or cost claim was made; per-turn
`attention_budget` and per-Actor tiering remain open design.

### I-058 — `test_i033_005` asserts a send order that concurrent tool execution never promised
**Closed:** 2026-09-16 · **Commit:** `222d5f3`
**Resolution:** CI failed with `['B', 'A', 'C'] != ['A', 'B', 'C']`. PydanticAI runs the two
`send_message` calls of one model response on separate AnyIO worker threads; the I-021 lock makes
the budget check atomic but leaves commit order to the scheduler, so A/B may persist in either
order and either of C/D may take the last slot. macOS scheduling favours the first-submitted
thread, which is why the test was stable locally and flaky on the Linux runner. Reproduced
deterministically by stalling A's and C's threads (`['B', 'A', 'D']`). The test now asserts what
the contract actually guarantees — both pre-retry sends survive the retry, exactly one more fits
the remaining budget — instead of a thread-scheduling order. Engine and player code are unchanged;
whether sends within one response *should* be serialised (`sequential=True`) is a separate
design question, not taken here.

### I-047 — Add a predeclared analysis and assurance plan for analytical runs
**Closed:** 2026-09-16 · **Commit:** `222d5f3`
**Resolution:** D-054 classifies every run as `playtest` (the unchanged `amaw run` default) or
`analytical`, recorded in the immutable T-053 manifest's `benchmark` block. An analytical run seals
a validated `analysis-plan-v1` document — research question, hypotheses, experimental unit,
treatment and fixed conditions, primary/secondary outcome measures with capture source, estimand,
attempts/seed/rotation design, failure/exclusion/missing-data rules, analysis method, assurance
checks — with its content and `sha256` into that manifest before any Game exists
(`amaw run --analysis-plan`), so the manifest's creation time evidences predeclaration; a resumed
game inherits the sealed plan and refuses a different one. Undeclared metrics are telemetry
(`classify_measure`). `amaw analysis-plan verify` classifies exports as `predeclared`,
`post_hoc_plan` or `unplanned`; `benchmark-methodology.md` v0.3.4 §4.1–§4.2 and the §27 template
(classification, raw observations, verified findings, exploratory analyses, bias/validity/
confidence/generalisation limits) make the separation normative. The module lives in `benchmark/`;
`app/` is unchanged. Evidence: `dev/archive/tasks/I-047-analysis-plan.md`; `TEST-I047-001…009`
and the full offline suite passed (776 passed, 6 LLM deselected). Runs 1–8 are recorded as
playtests; T-054/T-058 carry the classification through batches and output tables.

### I-055 — Design a bounded total-Cons experimental condition
**Closed:** 2026-09-16 · **Commit:** `222d5f3`
**Resolution:** D-053 keeps released `open_cons` (every opponent eligible) as the default and adds
the persisted, versioned `sampled_sealed` treatment with a required challenger count `1 ≤ K ≤ N-1`,
rejected at creation otherwise. The server seals exactly K challengers inside the Argument-acceptance
transaction — before any opponent activation, so latency and response order cannot bias who
opposes — by walking the `turn_order` ring from a durable cursor seeded at `seed mod N`, skipping the
owner; no adjudication draw is consumed. The sealed set is the eligible set for Con, pass,
`available_actions` and closure; non-selected opponents get `403` and no activation. The Umpire
context is unchanged and the condition is undisclosed to it. Game/Argument snapshots, manifest, resume
recipe, AgentRun parameters, export v9 and the summary `con_condition` carry the condition; legacy
rows stay null. Evidence: `dev/archive/tasks/I-055-sampled-cons.md`; sealing, schedule determinism
and balance, eligibility/403, closure, replay, restart, resume, configuration, provenance and runner
regressions plus the full offline suite passed (745 passed, 6 LLM deselected). No provider run or
behavioural-equivalence claim was made. I-046 rebuttals and any `bounded_visible` composition remain
open.

### I-053 — Add a fixed diplomacy-round experimental condition
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** D-052 keeps released `open_diplomacy` as the default and adds the persisted,
versioned `bounded_rounds` treatment with a required positive per-turn limit. The I-028 coordinator
checkpoints a round only after all activations and deferred readiness settle; failures consume no
slot. The server transaction durably increments/replays `(turn, round)`, closes exactly once at the
bound, and resets progress next turn. Final-round Messages remain committed, while reactivated
recipients receive no extra Diplomacy activation. Game snapshots, actor context, immutable
manifests/resume recipes, AgentRun parameters and export-v8 summaries expose the configured policy,
actual rounds, activations, calls, tokens, wall time, Messages and closure reason. Additive migration
leaves historical policy fields null rather than fabricating bounded provenance. Evidence:
`dev/archive/tasks/I-053-bounded-diplomacy.md`; serial/concurrent, message-race, failure, replay,
restart, configuration and provenance regressions plus the full offline suite passed (733 passed,
6 LLM deselected). No provider run or behavioural-equivalence claim was made.

### I-057 — Evaluate batched research and intra-activation context compaction
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** D-051 keeps the released `sequential-v1` + `full-v1` condition as default and
adds two independently selectable agent-harness treatments. `batched-v1` adds bounded ordered
batch tools for independent searches and already-known Resource reads while still using the same
Seat-bound HTTP methods, visibility service, ResourceAccess rows and I-045 activation id.
`compact-success-v1` processes only the provider-visible copy of intra-activation history: a
completed successful research call/return pair becomes a canonical retained-result message with
the exact returned data; failures, retries and the authoritative transcript used by I-052 remain
unchanged. I-056 measures the processed provider-bound request. Manifests, resume recipes,
AgentRun model parameters and tool-schema version pin both conditions; CLI/env flags expose them
and mixed player conditions fail closed. Evidence:
`dev/archive/tasks/I-057-batched-research-context-compaction.md`; 5 focused acceptance tests, 88
affected tests and the full offline suite passed (721 passed, 6 LLM deselected). No provider run
or strategic-behaviour equivalence claim was made, and no treatment became the default.

### I-056 — Profile per-request token topology and cacheability
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** Every reference-player provider request now records a versioned, content-free
canonical-JSON logical-request measurement at the PydanticAI wrapper boundary. Component bytes
cover prompts, schemas, public/private packet regions, Effects, incremental Messages, memory,
Argument/Cons context, Resource results and prior tool plumbing, with an envelope residual that
reconciles exactly. Adjacent growth and common-prefix length use an ephemeral activation-keyed HMAC
digest; no content, raw hash or key persists. Per-request provider input/cache usage reconciles to
I-050 AgentRun totals, including returned responses around framework rejection, provider failure
and logical retry. Export format 7, additive legacy migration, strict validation and summaries
distinguish measured/unmeasured calls. The guide explicitly says this is neither provider wire size,
tokenizer attribution nor proof of a cache hit. No prompt/schema/history/caching/behavior changed.
Evidence: `dev/archive/tasks/I-056-request-token-topology.md`; 7 focused acceptance tests, 108
affected tests and the full offline suite passed (716 passed, 6 LLM deselected); no provider run.

### I-054 — Stop mechanically repeated Resource-search loops
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** The Resource endpoint now replays an immutable result projection when the same
trim-and-casefold-normalised query is repeated for the same Game, Actor and correlated activation.
The application-local LRU is bounded and single-flight, never serves an uncorrelated request, and
cannot share results across visibility contexts, processes or app instances. Every logical search
still makes its HTTP call, keeps its own trimmed query echo, appends its own ordered ResourceAccess
row and remains present in I-045/I-052 sequence telemetry. Thus only duplicate authorised
visibility/search evaluation is avoided; no tool call, provider iteration, rejection, prompt,
research budget or model behaviour is suppressed or changed. Evidence:
`dev/archive/tasks/I-054-resource-search-replay-cache.md`; 5 focused acceptance tests, 117 affected
tests and the full offline suite passed (709 passed, 6 LLM deselected); no provider run.

### I-052 — Split and explain the aggregate `invalid_tool_calls` metric
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** New player activations retain a versioned, content-free event ledger behind the
compatible `invalid_tool_calls` total. It separates callable-tool failures, decision validation
retries and post-validation executed-action rejections; records safe action, error code/status,
event order and whether another model request follows; and derives category/reason counts in the
run summary. Export format 6 and the operations guide explicitly distinguish validation retries
from HTTP game-tool failures. Persistence rejects malformed or non-reconciling ledgers, while an
additive SQLite migration leaves historical aggregates as explicitly unclassified `{}` rather
than fabricating detail. No retry policy, prompt, tool schema or gameplay changed. Evidence:
`dev/archive/tasks/I-052-invalid-tool-call-telemetry.md`; 6 focused acceptance tests, 76 affected
tests and the full offline suite passed (704 passed, 6 LLM deselected); no provider run.

### I-051 — Strategic-memory acknowledgement limit disagrees with player validation
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** Server and player now share one framework-neutral 12,000-byte canonical JSON
ceiling. Single-shot output validation rejects oversized replacement memory before executing any
bundled Message/completion action, while the tool loop returns the same specific correctable
guardrail error before staging. The server names the actual/allowed byte size independently from
receipt conflicts, and acknowledgement remains phase-independent so a valid pending receipt
succeeds after concurrent phase closure. The old mismatch interval, both activation modes,
exactly-once bundled mutation and phase-closure paths are covered. Evidence:
`dev/archive/tasks/I-051-strategic-memory-limit.md`; 4 focused regressions, 63 affected tests and
the full offline suite passed (698 passed, 6 LLM deselected); no provider run.

### I-050 — Player AgentRuns lose usage from failed/retried model requests
**Closed:** 2026-09-15 · **Commit:** `222d5f3`
**Resolution:** `_TimedModel` now captures every returned PydanticAI request usage record at the
provider boundary and accumulates it across model/tool iterations, framework output rejection and
AMAW's one logical retry. Successful and failed player AgentRuns persist inclusive input/output,
reasoning, cache-read and cache-write aggregates; request-by-request usage remains available in
provider metadata for exact reconciliation. Umpire OpenRouter rows normalize the same raw fields.
Export v5 and derived summaries expose the breakdown, legacy SQLite databases gain additive
zero-default columns, and historical values are not fabricated. Evidence:
`dev/archive/tasks/I-050-token-accounting.md`; 4 focused provider-free reconciliations and the full
offline suite passed (691 passed, 6 LLM deselected); no provider run.

### I-045 — Attribute repeated Resource research to player activations
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** D-050. Every reference-player activation now owns an opaque UUID scoped across
its complete model/tool loop and cleared on exit. Authorized Resource searches/reads and the
eventual successful or failed AgentRun persist that same identifier; the report joins by game,
Actor and activation so concurrent activity cannot cross-attribute. The T-053 summary/export v4
now reports per-activation searches/reads, exact trim+casefold repeats, immutable Resource hashes,
search-to-read conversion, no-read searches, provider calls and activation-level continuation,
while legacy/orphaned accesses remain explicitly unattributed. Additive migration preserves old
rows. No prompt, memory, visibility/retrieval or research-limit behavior changed. Evidence:
`dev/archive/tasks/I-045-resource-activation-correlation.md`; 4 focused acceptance tests and the
full offline suite passed (687 passed, 6 LLM deselected); no provider run.

### I-048 — Resume an existing autonomous game after runner/process interruption
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** `amaw run --resume-game GAME_ID` now reconstructs the reference-player/Umpire
condition from a complete credential-free `autonomous-resume-v1` recipe in the immutable run
manifest, rejects legacy/incompatible provenance and condition-changing flags, rotates all Seat
hashes atomically through an admin-only path, and drives the existing Game by re-observing its
authoritative state. Raw replacement tokens are never persisted; old credentials are invalidated
together. Pending strategic-memory delivery, idempotency history, AgentRun evidence, RNG state and
the manifest remain intact. Offline interruption tests cover committed Actor work and a committed
adjudication/advance immediately before process loss, then complete the same one Game with exactly
18 Arguments. Evidence: `dev/archive/tasks/I-048-resume-autonomous-game.md`; 15 focused tests and
the full 683-test offline suite passed, 6 LLM tests deselected, no provider run.

### I-044 — Code provenance conflates source changes with generated untracked artefacts
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** The autonomous run manifest now reports exact HEAD for a clean checkout or one
containing only recognized, non-executable generated evidence under `dev/runs/`. Tracked changes
and every other untracked file produce `<commit>-dirty-<sha256>`, derived deterministically from
the Git index and current path bytes/modes. The narrow evidence exception cannot hide arbitrary
files or executable evidence-shaped files. `AMAW_CODE_VERSION` and non-Git package fallbacks are
unchanged. Evidence in `dev/archive/tasks/I-044-code-provenance.md`: 5 focused regressions and the
full 674-test offline suite passed; 6 LLM tests deselected and no provider run made.

### I-049 — Swedish v5 seat distribution disagrees with its acceptance test
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** The v5 resource's authoritative 349-seat distribution is now reflected in
`tests/test_election_scenario.py` (`S=100`, `M=70`, `SD=62`, `C=25`, `V=29`, `KD=22`, `MP=22`,
`L=19`). This verification cleanup is separate from the I-043 Con-capacity orchestration fix.

### I-043 — Single-shot Cons decisions can exceed the remaining Con budget
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** The server-computed Actor context now reports `con_slots_remaining` during an
eligible Cons opportunity, and the reference player's packet carries it in `limits`. Single-shot
validation rejects an oversized `ConsDecision` before any mutation with a structured guardrail
error. The existing transactional server budget remains unchanged; a one-slot opportunity now
issues at most one `make_con` request. The validation contract now requires the capacity whenever
`make_con` is allowed, rejects missing capacity even for an explicit pass, and accepts `None` only
for non-Con direct use. Evidence in
`dev/archive/tasks/I-043-con-capacity.md`: the review
hardening adds fail-closed missing-capacity validation, single-snapshot Con counting, one
model-facing capacity location, and activation coverage; focused I-043/affected regressions
passed, and 663 passed with the three unrelated deleted-v4 tests deselected. No provider run.

### I-042 — Concurrent player clients outlive their per-activation event loops
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** Every Actor now owns one persistent single-thread worker and asyncio event loop
for the complete autonomous run, across concurrent Diplomacy, serial Argument and concurrent Cons
activations. The configured concurrency ceilings remain enforced across Actor workers. Teardown
closes each owned OpenRouter client on its own loop before closing that loop and joining the worker,
including failure paths. The offline regression reproduces the previous cross-phase loop change,
proves loop identity and same-loop client closure, and verifies no worker threads remain. Evidence
in `dev/archive/tasks/I-042-actor-event-loop-affinity.md`: focused 20 passed; full suite 661 passed,
6 LLM deselected; no provider run.

### I-041 — Make long autonomous runs diagnosable and recoverable
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** `amaw run` now exposes provider/model parameters and all finite player/runtime
ceilings, supports a timestamped secret-redacted file log, streams event/resource-access/AgentRun
provenance plus the immutable run manifest on request, and writes a final or best-effort partial
administrative export. The operational guide includes a reproducible unattended command. The
intentional latest Swedish mandate allocation is published as scenario v5 while the already-run
v4 Resource bytes remain unchanged. Evidence in
`dev/archive/tasks/I-041-unattended-run-ops.md`: focused 23 passed; full suite 660 passed,
6 LLM deselected; no provider run was made and raw trial logs were not committed.

### I-033 — Tighter per-activation diplomacy Message budget
**Closed:** 2026-09-14 · **Commit:** `222d5f3`
**Resolution:** D-049. The shared player guardrails now enforce a non-negative
`max_messages_per_activation` (default `3`) across the complete logical activation and its
internal model retry. Only successful unique Message mutations spend it; rejected sends do not,
and stable idempotent replay does not double-charge. Single-shot rejects an over-budget decision
through `ModelRetry` before any mutation; tool-loop returns a structured exhaustion error and may
continue other legal actions. The independent per-phase emergency ceiling remains enforced across
activations. `AMAW_MAX_MESSAGES_PER_ACTIVATION` / `--max-messages-per-activation`, manifest and
AgentRun provenance, packet remaining capacity, immutable `player-v1.6`/`player-v1.7`, and
real-thread per-Actor isolation are covered. Evidence in
`dev/archive/tasks/I-033-message-budget.md`: focused 111 passed; full suite 657 passed,
6 LLM deselected; no provider run.

---

### I-028 — Run diplomacy-phase activations concurrently
**Closed:** 2026-09-13 · **Commit:** `222d5f3`
**Resolution:** Diplomacy now executes as explicit isolated rounds. The coordinator captures every
Actor's authoritative context before starting work, pins each eligible Actor's incremental
delivery and visible-Message read, runs only eligible Actors through a bounded pool
(`diplomacy_concurrency`, default 4; environment/CLI configurable), waits for all workers, and
handles outcomes/provenance in rotated sweep order. Readiness is deferred until all round Message
writes settle; a ready decision is suppressed for a same-round private recipient so only that
recipient is reactivated in the next round. This prevents both ready-before-Message and
Message-before-ready closure races while every mutation still goes through the server. Real-thread
tests retain T-023 idempotency and T-024 single-close behavior. Evidence in
`dev/archive/tasks/I-028-diplomacy-concurrency.md`: focused 65 passed; full suite 647 passed,
6 LLM deselected; no provider run.

---

### I-027 — Run the T-053 throughput report on an 8-Actor dry run before optimizing
**Closed:** 2026-09-13 · **Commit:** `222d5f3`
**Resolution:** Ran the Swedish v4 scenario with `deepseek/deepseek-v4-flash-0731` for both
recorded player activation modes at `cons_concurrency=4`. `single_shot` completed the initial
eight-Actor diplomacy sweep with 8 activations, 34 persisted model calls, 354,866 input tokens,
89,933 output tokens, 86 tool calls and 1,562,984 ms provider wait; the run then stalled in
diplomacy before Argument/Cons/Umpire work. The matching `tool_loop` attempt made 4 provider
requests but completed no activation before bounded manual termination. Provider cost was not
returned and remains unavailable. The full evidence and caveats are in
`dev/runs/2026-09-13-i027-deepseek-v4-flash/report.md`.

---

### I-034 — Incremental diplomacy context + structured rolling strategic memory
**Closed:** 2026-09-13 · **Commits:** `this commit` (code), `2190842` (contract)
**Resolution:** D-048. One server-backed `ActorStrategicState` per game/Actor now owns the
structured current memory and a durable acknowledged/pending GameEvent boundary. A pending batch
is replayed after failure or restart; successful model and tool execution atomically acknowledges
the opaque receipt, advances the boundary and replaces memory. Activation packets contain stable
scenario/Actor context, authoritative current state and only new visible inbound Messages; older
history remains available through `read_messages`. Both activation modes use immutable
`player-v1.4` / `player-v1.5` prompts and `player-tools-v3`. Run manifests, AgentRun rows and
export v3 identify `incremental-context-v1` / `strategic-memory-v1`. Evidence in
`dev/archive/tasks/I-034-incremental-strategic-memory.md`: focused 15 passed; full suite 634
passed, 6 LLM deselected; no provider run.

### I-031 — Single-shot structured phase decisions with a tool-loop fallback
**Closed:** 2026-09-13 · **Commits:** `598461d` (code), `31bc7bf` (agents.md 0.2.3)
**Resolution:** D-046. `PlayerConfig.activation_mode` (`single_shot` default, `tool_loop`
kept). Single-shot runs the agent with a per-activation `ToolOutput` whose schema follows
`available_actions`; the three read tools stay callable (research first only when needed); the
decision is validated in the output function against the §18 guardrails (`ModelRetry`, same
error shape) and executed messages → note → completion through the unchanged HTTP client, server
rejections recorded. New `agents/decisions.py`, `agents/guardrails.py`, immutable
`player-v1.3`, `player-tools-v2`, mode in manifest and every AgentRun, one mode per game,
`AMAW_PLAYER_ACTIVATION_MODE` / `--activation-mode`. Evidence in
`dev/archive/tasks/I-031-single-shot-decisions.md`: 27 new tests; full suite 619 passed, 6 LLM
deselected (live smoke parametrised over both modes, not run — no key).

### I-029 — Run Con activations concurrently for one Argument
**Closed:** 2026-09-13 · **Commit:** `6472b43`
**Resolution:** D-045. Verified first that Cons are Umpire-only (no player-facing Cons read;
`con_submitted` carries ids only), so concurrency is behaviour-neutral and the proposed Umpire
echo-guard was dropped. `AutonomousGameRunner` now steps the Cons sweep on a bounded pool
(`cons_concurrency`, default 4, `AMAW_CONS_CONCURRENCY` / `--cons-concurrency`; `1` = serial),
recording outcomes in sweep order from the coordinator thread; a serial sweep stops when the phase
flips so no Con activation slips onto the coordinator thread; the Umpire receives Cons in Actor-id
order; the bound is recorded in the manifest (`coordinator.cons_concurrency`). Evidence in
`dev/archive/tasks/I-029-concurrent-cons.md`: focused 49 passed; full suite 591 passed, 5 LLM
deselected.

### I-037 — Swedish scenario v3 contains an authoritative placeholder and compresses settlement into two vague turns
**Closed:** 2026-09-13 · **Commits:** `2cbdaf2`, `d0ba24c`
**Resolution:** D-044. Published `svensk-regeringsbildning-2026-v4`: a concrete fictional
post-resignation/speaker-consultation start state, four 48-hour turns, explicit derived-package,
credible-budget and opposition rules, and decision-relevant fictional leverage for C, KD and L.
The generic loader now rejects `TODO`/`TBD`/`FIXME`/`FYLL_I...`/`<placeholder>` markers in
authoritative starting Effects (TEST-SCN-006). Focused 11 passed; full suite 581 passed, 5 LLM
deselected.

### I-036 — Probability can waive missing Actor authority or prerequisites
**Closed:** 2026-09-13 · **Commits:** `2cbdaf2`, `d0ba24c`
**Resolution:** D-044 revised the normative contract to authority → prerequisites → deterministic
rules → residual probability and issued immutable `umpire-v1.3`. Overbroad mixed requests must be
narrowed; self-binding Effects survive external uncertainty in both branches; unsupported Actor
commitments cannot become a low-probability jackpot. Supersession is now mechanically
branch-specific, so an unselected or failed branch cannot deactivate a blocker. TEST-ADJ-007…009;
focused 11 passed; full suite 581 passed, 5 LLM deselected. A server-enforced semantic guarantee
for free-text Effects remains honestly open as I-038.

### I-030 — Auto-complete Con participation when an Actor's Con budget is exhausted
**Closed:** 2026-09-13 · **Commit:** `222d5f3`
**Resolution:** A successful final-slot `make_con` now persists `ConParticipation` and closes the
Cons phase when it completes the eligible set, all in the same guarded transaction. It emits
`con_submitted` followed by `phase_started(adjudication)` when applicable, never the false claim
`con_passed`. Explicit `pass_con` remains for zero-Cons and non-exhausted participation. D-043;
full suite 570 passed, 5 LLM deselected.

### I-022 — Messages sent before an activation fails are not counted against the phase cap
**Closed:** 2026-09-13 · **Commit:** `222d5f3`
**Resolution:** `ActivationFailed` now carries the number of successfully committed Messages and
`LifecycleRunner` charges them to the activation's original `(turn, phase)` before recovery. A
regression sends once, exceeds the model-iteration ceiling, reopens diplomacy and proves the next
activation receives zero remaining Message budget.

### I-035 — No user-facing way to choose a scenario and start/run an autonomous game
**Closed:** 2026-09-12 · **Commit:** `0fe7a47`
**Resolution:** Added `amaw run SCENARIO`, which accepts a package path or bundled scenario name,
composes the existing API and `AutonomousGameRunner` with the configured Player/Umpire models,
creates and completes the game, and prints its game ID. `amaw export GAME_ID` remains the
inspection path. Decision recorded as D-040; the root README now contains the minimal runbook.

### I-026 — The AI Umpire does not retry an unusable completion
**Closed:** 2026-09-12 · **Commit:** `1c8ecd7`
**Resolution:** `AIUmpire.adjudicate` makes one fresh attempt on an unusable completion before pausing; smoke budget raised.

### I-025 — Failure recovery judged from the pre-activation snapshot stalls a game that has moved on
**Closed:** 2026-09-12 · **Commit:** `41f6cbe`
**Resolution:** Recovery re-observes, compares opportunity keys, and reports an explicit `CycleOutcome.stuck`; reproduced offline first.

### I-024 — Fixture objectives are mutually satisfiable, so autonomous games converge
**Closed:** 2026-09-12 · **Commit:** `40add5b`, `41f6cbe`
**Resolution:** `scenarios/northern-strait/` (triangular objectives). Runs 3, 5 and 6 produced contested play with side deals, deadlock and a negotiated settlement; the fixture is unchanged. T-065 starts from this package.

### I-023 — Malformed 2xx provider bodies are non-transient on the Umpire path but transient on the player path
**Closed:** 2026-09-12 · **Commit:** `2b94cbc`
**Resolution:** 2xx parse failures are `TRANSIENT` with the single retry; `AIUmpire.last_failure` names the shape. The retry fired and behaved correctly in run 5.

### I-021 — Per-phase Message cap is not atomic under concurrent tool execution
**Closed:** 2026-09-12 · **Commit:** `b873c12`
**Resolution:** `send_message` holds a lock across check→send→count; failed 5/5 pre-fix, green after.

### I-020 — The smoke run's export and activation provenance are not written to disk
**Closed:** 2026-09-12 · **Commit:** `f86c932`, `49b7a46`, `2b94cbc`
**Resolution:** `AMAW_SMOKE_EXPORT_DIR` receives export/activations/failures/run.json; a failed run keeps its export via `AutonomousGameRunner.last_game_id`.

### I-019 — Umpire prompt v1.1 lacks contradiction-supersession and failure-cost guidance; probability distribution is not summarised
**Closed:** 2026-09-12 · **Commit:** `bfae4b3`, `6cb0630`
**Resolution:** D-028 `umpire-v1.2`. Calibration judged on 36 adjudications across runs 3, 5, 6: probabilities 0.10–0.80, all three modes used (`RUNS.md`).

### I-018 — Umpire never receives hidden information
**Closed:** 2026-09-12 · **Commit:** `0b9e83f`, `ed15010`, `fe7069a`
**Resolution:** D-029: every Actor's private facts and active private Effects reach the Umpire tagged by owner; `relevant_effects` carry visibility; leak check over Effects, rationales and both branches; `agents.md` 0.2.1.

### I-017 — Player prompt v1.1 frames Cons as impartial critique and never says private information is for use
**Closed:** 2026-09-12 · **Commit:** `8b69ae3`, `26e4fc5`
**Resolution:** D-027 `player-v1.2`: private inputs are for use, Cons are strategic, own words. Behaviour change confirmed in runs 3–6 (`RUNS.md`).

### I-016 — Autonomous sweep order is fixed, anchoring later Actors on earlier same-phase text
**Closed:** 2026-09-12 · **Commit:** `e6b63eb`
**Resolution:** `sweep_order` rotates the coordinator sweep start by turn and logs it; mitigation, not elimination (see `RUNS.md`).

### I-015 — Decision packet does not state the remaining per-phase Message budget
**Closed:** 2026-09-12 · **Commit:** `5c7707a`
**Resolution:** Packet `limits` block with `messages_remaining_this_phase` and a framing sentence; tests in `test_player.py`/`test_guardrails.py`.

### I-014 — Recent Message and Note selection relies on undefined database order
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** `visible_messages`/`visible_notes` order by `(created_at, id)`. Named test `test_i014_visible_messages_and_notes_are_chronological`.

### I-013 — The Umpire receives a scenario ID instead of scenario context
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** Immutable scenario version + summary snapshotted on the Game and passed to the Umpire; `test_i013_scenario_context_is_snapshotted`.

### I-012 — Authoritative API and Umpire output size limits are missing
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** Server-side text/list limits at the service boundary; `test_i012_service_text_and_umpire_limits`.

### I-011 — Logging redaction omits the configured admin token
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** Admin token registered with the redacting filter; `test_i011_admin_token_is_redacted`.

### I-010 — Event authorization fails open for unknown or broken event references
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** Event visibility denies by default; `test_i010_unknown_and_broken_events_fail_closed`.

### I-009 — Malformed successful provider responses bypass safe Umpire outage handling
**Closed:** 2026-09-12 · **Commit:** `42e6dee`, `2b94cbc`
**Resolution:** Every response-parse failure is a `ModelClientError`; with I-023 the four 2xx-parse failures are transient (one retry) and then reach the pause path. `test_i009_malformed_success_response_is_normalized`.

### I-008 — Actor-visible event cursors leak hidden and cross-game activity
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** D-026: Actor-visible event ids are per-view ordinals; `test_i008_hidden_event_creates_no_actor_cursor_gap`.

### I-007 — Retrying a player activation can repeat committed tool side effects
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** Every player mutation carries a stable per-opportunity idempotency key; a retried activation replays rather than duplicates. Named test `test_i007_retry_after_a_committed_send_does_not_duplicate_it`.

### I-006 — Fresh application databases are never initialized
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** `create_app` initialises a fresh database and applies additive migrations at startup; `test_i006_*`.

### I-005 — Guarded SQLite mutations can lose concurrent updates
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** Guarded mutations serialise the read/write boundary; `test_i005_simultaneous_ready_updates_are_not_lost` drives real threads.

### I-004 — PydanticAI/OpenAI transport performs hidden provider retries beyond AMAW's
  one-retry policy
**Closed:** 2026-09-12 · **Commit:** `42e6dee`
**Resolution:** `build_openrouter_model` constructs the OpenAI client with `max_retries=0`; AMAW's single transient retry is the only one. Named test `test_i004_openrouter_sdk_retries_are_disabled`.

### I-003 — Pre-T-007 persistence and benchmark-review corrections
**Closed:** 2026-09-10 · **Commits:** `981eab6`, `9d0bc96`
**Resolution:** D-008 corrected the normative persistence/benchmark contracts; the schema now
scopes Actors per game, persists execution and adjudication state, enforces the key uniqueness
invariants and restores UUID list types. Locked CI verifies Python 3.12 and 3.14; 32 tests pass.

### I-002 — TRD architecture diagram alignment
**Closed:** 2026-09-10 · **Commit:** `981eab6`
**Resolution:** Realigned PydanticAI and OpenRouter beneath their architecture branches while
updating TRD v0.5.2.

### I-001 — Player-prompt version and location conflict
**Closed:** 2026-09-10 · **Commit:** `981eab6`
**Resolution:** Moved player v1.1 to `docs/prompts/`, archived superseded player v1, and updated
the prompt index. Umpire/evaluator v1.1 were versioned and their v1 prompts archived too.
