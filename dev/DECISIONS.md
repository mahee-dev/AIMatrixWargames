# AMAW — Decision Log

Short records of choices the specification does not settle, and of any change made to the
specification itself. Newest first.

Keep entries to a few lines. This is not a design document — it exists so a future reader
understands why the code diverges from the obvious reading, and so spec changes are never
silent.

## Entry format

```markdown
### D-0NN — Title
**Date:** YYYY-MM-DD · **Status:** accepted | superseded by D-0NN
**Context:** what forced a decision.
**Decision:** what we do.
**Consequence:** what this costs or constrains.
```

---

### D-125 — The terminal viewer is a four-screen console that shows only recorded data (#38)
**Date:** 2026-10-11 · **Status:** accepted
**Context:** The owner asked for the `amaw watch` viewer (TRD §41.1) to become a command console
modelled on a four-screen mock-up whose panels include tension and stability gauges, a region map,
Actor goals and "next expected" actions. AMAW records none of these. Feed refs are joined when the
feed is read, so an Effect's `active` flag or an Argument's `status` in a ref can be later than its
event, and a snapshot's run totals describe the present.
**Decision:** The viewer has four screens (Live Command, Actor Network, Adjudication, World State)
over one incremental fold of the feed (`observer/viewstate.py`). It draws only recorded data:
Effects are the world state, figures are counts from the event log, public broadcasts are counted
per sender rather than drawn as directed links, and "awaiting input" never claims a model call is
running. A replay takes lifecycle from events, never from refs, seeds the setup Effects that no
event creates, and shows run totals only once it reaches the snapshot's cursor. The feed, its item
shapes and the publisher are unchanged; the viewer stays read-only and local.
**Consequence:** Mock-up panels with no authoritative source (gauges, maps, goals, predicted next
moves, the Umpire's model) are left out until the engine records them. TRD §41's exclusion of
"advanced dashboards" still holds for anything beyond the §41.1 observer view.

### D-124 — Single-shot decisions decode double-encoded JSON objects (#31)
**Date:** 2026-10-11 · **Status:** accepted
**Context:** `deepseek/deepseek-v4.1-flash` sends the nested `strategic_memory` object of the
`decide` call as a string containing JSON, and repeats it after the retry prompt, so every
Argument activation failed with `UnexpectedModelBehavior` (12 of 12, on `northern-strait` too).
The content was exactly what the schema asks for.
**Decision:** Every decision object (`_Strict` in `agents/decisions.py`) decodes a string that
parses to a JSON object before validation, then validates it as strictly as the object form. Any
other string fails as before. The JSON schema offered to the model is unchanged, so the tool
schema version is unchanged.
**Consequence:** The harness accepts one more wire encoding of the same decision. The decode is
not counted as an invalid tool call, so a model that double-encodes is indistinguishable in the
export from one that does not; that is intended, since the benchmark measures play, not escaping.
Runs before this change that failed this way are not comparable to runs after it.

### D-123 — The game database runs in WAL journal mode (#12)
**Date:** 2026-10-10 · **Status:** accepted
**Context:** `engine_for_path` left SQLite in its default rollback journal, where a process
holding a read transaction blocks the game's commits until the 30 s busy timeout. Concurrent
games on one file (run 23) and the observer feed (#23), which reads while a run writes, both
depend on readers not stalling the writer.
**Decision:** Every app engine sets `PRAGMA journal_mode=WAL` on connect. Writes stay serialised
by SQLite's single write lock, so the `BEGIN IMMEDIATE` guards and the TRD §26.1 envelope are
unchanged; `synchronous` keeps its default.
**Consequence:** The database file gains `-wal` and `-shm` side files, ignored by git. Copying
the `.db` file alone while a process has it open can miss recent commits; use the export.
WAL needs a local filesystem, which §26.1 already requires.

### D-122 — An outside observer view, with an opt-in hosted publisher (#22)
**Date:** 2026-10-10 · **Status:** accepted · owner direction
**Context:** The owner wants to follow a game as it runs, including private Messages, and replay
it afterwards: in the terminal during `amaw run`, and in an external frontend (built in Lovable)
fed from a server the owner runs on their own VPS. TRD §41 excludes advanced dashboards. D-106
plans only a local workbench on 127.0.0.1 with its frontend in `web/`, so data leaving the machine
is not covered.
**Decision:** TRD §41.1 admits an observer view: a read-only feed outside `app/` whose items are
the admin export's shapes, a terminal viewer (`amaw watch`, `amaw run --watch`) and an opt-in
publisher (`amaw publish`) that pushes the feed over HTTPS to an operator-run relay (FastAPI and
PostgreSQL on the owner's VPS, serving reads and an SSE stream). Supabase was considered and
declined: the owner prefers to run the backend. The view is administrative, never Actor-facing,
and can never change game state or affect a run. The ingest credential stays local. The relay
(its own repository) and the frontend live outside this repository; the ingest format is their
contract. D-106 is unchanged: the local workbench stays a separate amendment (#1) and may reuse the feed.
**Consequence:** Published games expose every Actor's private material to whoever can read the
relay; publishing is per-run and the operator decides per scenario (`ai-2027` has a rights note).
The feed reads the database from a second process, which raises the priority of WAL mode (#12).
Work: #23 (feed), #24 (terminal viewer), #25 (publisher). The TRD moves to 0.7.0.

### D-121 — The share card and narrated headline belong to the after-action report
**Date:** 2026-10-10 · **Status:** accepted · owner direction
**Context:** #16 adds a deterministic after-action report. The owner also wants a shareable
image/video card and an optional LLM-written headline for marketing. TRD §41 excludes human UIs.
**Decision:** Both are TRD §34 AAR products: static files generated after the game from its
export, with no authority over game state. They are not a human UI and need no §41 amendment.
The card may include private material (the operator's own game); the narrator's beats must cite
ids present in the export. Design: `dev/plans/2026-10-10-aar-share-card-design.md`.
**Consequence:** New package `aar/` outside `app/`; optional extra `share` (Playwright,
imageio-ffmpeg). No winner panel until #15.

### D-120 — The public repository is named `AIMatrixWargames`
**Date:** 2026-09-28 · **Status:** accepted · owner direction
**Context:** I-152 planned to publish the snapshot as `mahee-dev/AMAW`, which is this private
repository's name, so the private one would have had to be renamed first.
**Decision:** The public repository is `mahee-dev/AIMatrixWargames`. The product name AMAW, the
Python package and the `amaw` CLI are unchanged. The private `mahee-dev/AMAW` keeps its name and
is archived read-only after the move. The run archive stays `amaw-runs`.
**Consequence:** The README's clone command points at `AIMatrixWargames`. Nothing in the code,
the package metadata or the tests names the repository.

### D-119 — The retired scenario name `swedish-government-v6` is dropped (I-170)
**Date:** 2026-09-28 · **Status:** accepted · reverses I-144's retired-name refusal
**Context:** I-144 renamed the bundled package to `swedish-government` and kept the old name as
a refusal that named the new one and the git tag of version 6. The tag exists only in the
private maintainer repository, and no user of the public repository ever had the old name.
**Decision:** Remove the retired-name mapping. `swedish-government-v6` is an unknown name like
any other; the error lists the names that work, `swedish-government` among them. No other
retired names exist, so the mechanism goes with it.
**Consequence:** An old command using `-v6` fails with the ordinary unknown-name error instead
of a tailored pointer. `TEST-I170-001` replaces `TEST-I144-002`.

### D-118 — Players' decisions are never rolled; near-deterministic games are accepted (I-160)
**Date:** 2026-09-28 · **Status:** accepted
**Context:** Run 22 (`swedish-government` 7.0.0, `umpire-v1.7`) drew no roll in 32 rulings. Run
23 compared v1.3, v1.6 and v1.7 on `northern-strait` with the same seed: 5, 7 and 3 rolls; 14, 8
and 17 of 18 Arguments succeeded. Almost every v1.3/v1.6 roll was on whether another played
Actor would endorse or accept, which §23 of `docs/agents.md` forbids and I-156 already named.
v1.7 narrows such Arguments to the arguing Actor's own commitment and grants that automatically:
in 10 of its 18 rulings the rationale says the core of the Desired Effect was outside the Actor's
authority.
**Decision:** The owner keeps §23 and `umpire-v1.7` as the default (D-114 stands). Another
played Actor's decision, commitment or acceptance is never resolved by a roll; it is left to that
Actor. A game whose uncertainty lies only in the players' own choices may therefore have few or
no rolls, and that is accepted: the benchmark measures agent behaviour, not dice. Dice are for
causal uncertainty outside every Actor's authority.
**Consequence:** "Argument success" now usually means that the narrowed, self-binding part was
established, not that the Actor got what it asked for. Success rate and mean adjudicated
probability are not comparable across Umpire versions and do not measure attainment on their
own; `benchmark-methodology` 0.3.13 says so and defines an attainment measure (§15, §19.1; I-162).
Scenarios that want dice must place uncertainty with forces no Actor plays (I-163).

### D-117 — `ai-2027` and the corpora built from it are held back from the public repository (I-152)
**Date:** 2026-09-27 · **Status:** accepted; temporary; amends D-116's shipping of `corpora/`
**Context:** I-152 needed the rights checked before sourced or derived scenarios ship under MIT.
The owner cleared `swedish-government`. `ai-2027` is derived from the third-party AI 2027
scenario; the owner expects it can be released but will ask its authors first. Eight
adjudication corpus cases (three in v1, five in v2) were extracted from `ai-2027` runs and carry
its Actors, Effects and text, and the corpora are hash-locked as wholes.
**Decision:** Until the authors agree, the public snapshot leaves out `scenarios/ai-2027/`, all of
`corpora/`, and the tests that need either (`tests/test_scenario_ai_2027*.py`,
`tests/test_adjudication_corpus*.py`). The builder drops the `ai-2027-branch-point` entry from
the shipped `scenarios/versions.lock` and leaves the rest byte-identical. The corpus harness code
and scripts ship; they only name the corpus paths. This repository keeps everything. The
README, scenario index, operations guide and Field Manual say that `ai-2027` is held back. The
shipped history (`RUNS.md`, `BACKLOG_RESOLVED.md`) still describes what the Actors did in run 19;
it summarises play and reproduces no scenario text, so it ships. TEST-I152-007.
**Consequence:** Releasing them later is one change in the public repository: add the directories
and tests, restore the lock entry (the hashes are already recorded here), and remove the
held-back notes. The corpora cannot ship without the `ai-2027` cases without re-locking them as
a new corpus version, which would break the comparability of runs 20 and 21.

### D-116 — The public repository ships its history and a GitHub-issues working agreement (I-152)
**Date:** 2026-09-27 · **Status:** accepted; refines D-106; supersedes D-083's exclusion of `AGENTS.md`, `CLAUDE.md` and all of `dev/`
**Context:** D-106 makes the public repository the upstream and ships `AGENTS.md` and
`DECISIONS.md` so `D-` references resolve. The public tree also cites `I-` and `T-` identifiers
(hundreds in code and tests), `dev/RUNS.md`, `dev/WBS.md`, `dev/plans/` and the original-vision
archive. Rewriting those citations would touch most modules for no behavioural gain, and
AGENTS.md §4 says not to rewrite comments just to follow a rule.
**Decision:**
- The snapshot ships this repository's process record at its original paths, frozen:
  `dev/DECISIONS.md` (which stays living there), `BACKLOG.md`, `BACKLOG_RESOLVED.md`, `WBS.md`,
  `RUNS.md`, `gates/`, `plans/` and `archive/docs/original-vision/`. Every `D-`/`I-`/`T-`
  identifier cited outside `dev/` then resolves, and a test keeps it so (TEST-I152-005).
- `STATUS.md`, task briefs (`dev/tasks/`, `dev/archive/tasks/`), run evidence (`dev/runs/`, which
  goes to `amaw-runs`), `GATE3_PREPARATION.md`, `portability/`, `.claude/` and `PUBLIC_SOURCE.md`
  stay private. The 13 test docstrings that pointed at a brief now cite the issue or step instead.
- The public `AGENTS.md`, `CLAUDE.md` and `dev/README.md` live in `public_overlay/` and replace
  this repository's versions in the snapshot. They describe a GitHub issue and pull request
  workflow, explain where each identifier resolves, and keep §6 (architecture rules) and §7
  (technology) at the numbers the code cites. This repository keeps its own agreement until it
  is frozen.
- `corpora/` ships, so the adjudication harness and its tests are usable in the public tree.
- Tests that check private process state skip unless `dev/STATUS.md` exists, instead of skipping
  whenever `dev/` is absent.
- The history files were audited before shipping: no credentials, e-mail addresses or secret
  fields. The three mentions of the local checkout path were removed.
**Consequence:** Commit hashes in the shipped history refer to the unpublished private history;
`dev/README.md` says so. The shipped `BACKLOG.md` is frozen at the move and its open items that
are worth keeping become GitHub issues. Rights for `scenarios/ai-2027` and the sourced
`swedish-government` Resources are still checked by the owner before the repository is created.

### D-115 — One command starts any scenario (I-152)
**Date:** 2026-09-27 · **Status:** accepted; supersedes D-091's refusal of a resolverless `bounded_effects_v1` game
**Context:** The owner wants starting a game to be "dead easy" before the public repository
becomes most people's first contact with AMAW. `uv run amaw run orison-crisis` worked, but
`skaro-crisis` and `ai-2027` needed a path, `skaro-crisis` refused to start without
`--consequence-model`, and the record stayed in SQLite until a second `amaw export`.
**Decision:** Three CLI defaults, none of them normative contract changes:
1. A bare scenario name that is neither an existing directory nor a bundled name resolves to
   `scenarios/<name>/` under the working directory. Explicit paths still win, bundled names
   still mean the wheel copy wherever they run, retired names keep their pointer (D-104), and an
   unknown name is refused with the names that would work. Nothing new is bundled into the wheel;
   that waits for I-152's rights check.
2. Under `bounded_effects_v1`, an unset resolver model defaults to the Umpire model, for
   `amaw run` and `amaw benchmark` alike. The resolved model is sealed in the recipe and
   provenance exactly as an explicit one (agents.md §27), so the condition stays fully recorded.
   A resolver model under `none` is still refused.
3. Without `--export-path`, `amaw run` writes its final or partial export to
   `exports/<game_id>.json` beside the database, and names it on stderr. stdout stays the bare
   game ID, so scripts that read it are unchanged. The export holds the database's private data
   and goes where the operator already keeps that, never into the working directory.
**Consequence:** Every scenario in a checkout starts with `uv run amaw run <name>`. A retained run
that relied on the refusal is unaffected: run 18 set its resolver to the Umpire model, which is
now the default. Batch exports are unchanged. TEST-I152-001…003; TEST-T075-006, TEST-T074-009 and
TEST-I114-004 assert the default instead of the refusal.

### D-114 — `umpire-v1.7` is the default Umpire prompt for every game (I-159)
**Date:** 2026-09-27 · **Status:** accepted; supersedes the default clauses of D-060 and D-096
**Context:** D-113 adopted `umpire-v1.7` as an experimental condition after run 21: semantic
violation rate 0.299 → 0.051 overall, 0.360 → 0.020 on held-out cases, every guard passing. The
largest gain was on rule-free cases (the `umpire-v1.3` stratum, 0.43 → 0.02), not only on
scenarios with rules. The defaults were v1.6 for scenarios with rules (D-096), v1.3 for rule-free
`sealed_cons` and v1.4 for rule-free `visible_reply` (D-060).
**Decision:** The owner makes v1.7 the default for every game. An unpinned game resolves to
`umpire-v1.7` whatever its rules and Con-reply treatment. Pins (`--umpire-prompt-version`,
`AMAW_UMPIRE_PROMPT_VERSION`) still win; `visible_reply` still refuses v1.3 and a rules scenario
still refuses v1.3/v1.4. v1.3–v1.6 stay loadable and selectable. A resumed game keeps the version
its recipe recorded, and a legacy recipe without one still resolves to v1.3. The library constant
`UMPIRE_PROMPT_VERSION` and `AIUmpire`'s constructor default stay the released v1.3 identity; they
are a code-level fallback, not the game default. T-068/T-069 (the two-stage adjudicator) stay
deferred.
**Consequence:** New games differ from retained runs made under the old defaults: a comparison
with them must pin the old version, and provenance records which one ran. Rule-free games now get
v1.6's Effect bound (at most 3 per branch) and a longer Umpire prompt. A `sealed_cons` /
`visible_reply` comparison is held constant by default. The evidence behind the change is one
model and model-judged ratings (D-113); a later regression on another model would be a reason to
revisit. agents.md 0.3.12 (§19, §21), benchmark-methodology 0.3.12, testing 0.3.11.

### D-113 — Verdict: `umpire-v1.7` is adopted as an experimental condition (I-156, I-157)
**Date:** 2026-09-27 · **Status:** accepted
**Context:** `corpora/adjudication/v2/thresholds.yaml` pre-committed the decision rule for
`umpire-v1.7` against the released Umpire (D-112). Run 21 ran both conditions, 20 cases × 5
repetitions each. The 196 usable proposals were rated in one blinded mixed sheet by an AI agent
launched from this session with a fresh context, given only the sheet and the rubric (D-111's
kind of rater; the owner chose this route).
**Decision:** **Adopt as a new condition**: the primary measure passes on both required levels
and every guard passes.

| | released | v1.7 | rule |
|---|---|---|---|
| semantic violation rate, overall | 0.299 | 0.051 | ≤ 0.5× and ≥ 0.10 lower: pass |
| semantic violation rate, held_out | 0.360 | 0.020 | ≤ 0.5× and ≥ 0.10 lower: pass |
| self-binding lost | 0.155 | 0.101 | increase ≤ 0.05: pass |
| invalid supersession | 0.258 | 0.202 | increase ≤ 0.02: pass |
| branch inconsistency | 0.041 | 0.020 | increase ≤ 0.02: pass |
| failure rate | 0.03 | 0.01 | ≤ 0.04: pass |
| mean quality | 3.76 | 4.51 | decrease ≤ 0.3: pass |
| worst per-case regression | — | +1 of 5 | ≤ 2: pass |
| cost, median wall time | — | 1.07×, 1.01× | ≤ 3×: pass |

Cases violating (most repetitions): 6 of 20 → 0 of 20. By the baseline's strata, on the same
cases: rule-free (`umpire-v1.3`) 0.43 → 0.02; rules (`umpire-v1.6`) 0.20 → 0.07.

**Deviation, and why the verdict does not rest on it.** `review.md` showed claim text but not
the three entailment notes rubric v2 tells the rater to apply ("a recorded YES vote entails the
direction"; I-158), so the rater marked recorded votes absent on `ob-directed-members` and
`wh-directed-members`. Those 30 rows were re-rated blind by a second fresh agent shown only
those proposals, that claim and its note; every other rating is the first rater's. The rows are
*establish* claims and move only self-binding loss. As rated: 0.227 → 0.172; corrected (used
above): 0.155 → 0.101; with the re-rater's four borderline rows flipped: 0.124 → 0.091. The
guard passes and the primary measure (0.299 → 0.051) is identical under all three. Both sheets
are kept (`ratings.csv` as rated, `ratings-corrected.csv`).

**Consequence:** `umpire-v1.7` is an available, measured experimental condition. It is not a
default: making it the default for scenarios with rules is a separate owner decision. Limits of
the evidence: one model (DeepSeek v4 Flash), one rater agent per sheet, model-judged semantics
(D-111); the development split informed the prompt, but held_out, which it never saw, improved
more. Some v1.7 rationales echo its prompt wording, a small blinding leak to a rater who never
saw either prompt. Invalid supersession stays high in both conditions, mostly in two held-out
cases whose keys allow few targets. T-068/T-069 remain deferred: the case for a two-stage
adjudicator now has to beat 0.05, not 0.30.

### D-112 — `umpire-v1.7`: v1.6 plus a branch audit, selectable, not a default (I-156)
**Date:** 2026-09-27 · **Status:** accepted
**Context:** Run 20 (T-067) measured a semantic violation rate of 0.44 in the `umpire-v1.6`
stratum. `skaro-dc-cabinet-alert` put a Cabinet authorisation in the success branch in 5 of 5
repetitions, and the held-out draw for corpus v2 found the same pattern in older runs: another
Actor standing down, or accepting an offer, established by the roll. v1.6 already states the §23
rule ("randomness may not … create another Actor's commitment"), so restating it would not help;
the Umpire needs a check it applies to its own output.
**Decision:**
- **New prompt, `umpire-v1.7`:** `umpire-v1.6` plus one "Branch audit" section and nothing else.
  - For each Effect in each selectable branch: "would this be established if no other Actor made a
    new discretionary decision or commitment?" If not, remove it or rewrite it as what this Action
    establishes (the request made, the offer tabled). Another Actor's decision may appear only if
    it is already authoritative or follows from a deterministic rule over established facts.
  - Unconditional self-binding Effects go in every selectable branch. A conditional
    self-commitment is established as the commitment, never as its consequence.
  - Branch shape: each `probabilistic` branch holds at least one Effect; an automatic mode leaves
    its unselectable branch empty.
  - One state vocabulary, never stronger than the facts: committed, accepted, directed, voted,
    authorised, begun, completed, exists.
- **Same shape as v1.6 otherwise:** rules-aware, reply-aware, at most 3 Effects per branch
  (`umpire-output-v2`), retried at the AI boundary. Pinnable for rule-free games (§21, §27).
- **Selectable, not a default.** No game resolves to v1.7 unless it is pinned. Whether it becomes
  a default is a separate decision after the I-156 comparison under
  `corpora/adjudication/v2/thresholds.yaml`.
- **Written after corpus v2 was locked** (`312e7a0`), so the held-out split measures it without
  tuning. The development split informed it (it is v1's corpus) and is reported separately.
**Consequence:** agents.md §21, §22 and §31 name v1.7 (version 0.3.11). The prompt index gains a
row. The server's validation, every earlier prompt condition and every default are unchanged. The
branch-shape rule narrows v1.6's "an empty branch is legitimate" to automatic modes, matching the
corpus v2 branch-inconsistency measure.

### D-111 — The T-067 baseline ratings are an AI agent's, adopted by the owner (T-067)
**Date:** 2026-09-27 · **Status:** accepted; amends D-110
**Context:** D-110 and `corpora/adjudication/v1/thresholds.yaml` name the owner as the human
rater, and say no model scores the semantic measures. After T-067 closed, the owner reported that
an AI agent had filled in the baseline's `ratings.csv`. The records called it owner-rated.
**Decision:** The owner adopts the agent's ratings as the baseline record. The rating was
careful: 78 notes, strict readings consistent with `rubric.md`. Records now say "rated by an AI
agent, adopted by the owner". Any adjudicator compared with this baseline is rated the same way:
the same kind of agent, the same `rubric.md` and `review.md` format, blind to which adjudicator
produced each proposal, mixed with a re-rated 20 % baseline sample as `thresholds.yaml` requires.
The locked `rater: owner` line in v1's thresholds stays as written. This entry records the
departure; the lock is not rewritten.
**Consequence:** The baseline's semantic measures are model-judged, owner-accepted evidence, not
a human's judgment. That is weaker for claims about human-rated quality, so reports say so. The
agent's review notes feed corpus v2 (atomic claims, "directs" versus "voted", and a branch/mode
consistency check), which I-156/I-157 carry.

### D-110 — The T-067 adjudication baseline condition, and humans score the semantics (T-067)
**Date:** 2026-09-26 · **Status:** accepted; the rater clause amended by D-111
**Context:** T-067 must record "the released `single_umpire`" baseline, but the released CLI
resolves the Umpire prompt from the scenario (`umpire-v1.6` with declared rules, `umpire-v1.3`
without; agents.md §21), and neither the model, the repetitions nor the scorer are fixed by the
WBS. Unsupported commitments, waived prerequisites and causal overreach are judgments about
free-text Effects (D-089), which no mechanical check can make.
**Decision:** Each corpus case records the prompt the released CLI resolves for its scenario, and
results are reported per prompt stratum. The Umpire model is run 19's,
`deepseek/deepseek-v4-flash-0731` over OpenRouter, with its sealed settings (no temperature, no
reasoning effort, 4 attempts, 60 s timeout). Every case runs 5 times. The corpus, rubric and
thresholds live in `corpora/adjudication/v1/`, hash-locked, and are committed before any
provider call. A human rater (the owner) records, per claim, whether it appears in each branch,
and grades quality; the code derives the violation counts from that sheet. No model or text
heuristic scores the semantic measures. Supersession validity, failures, latency, tokens and cost
are computed mechanically.
**Consequence:** The baseline is complete only once the owner fills in the rating sheet. The
T-069 comparison must use the same model, repetitions, rubric and thresholds; a change is a new
experiment, not a revision. `corpora/` is not in the public builder's allowlist yet; I-152 decides
whether it ships.

### D-109 — `analysis-plan-v2` structures `design`; a cell batch names its level at invocation (I-090)
**Date:** 2026-09-26 · **Status:** accepted
**Context:** `analysis-plan-v1` states `seed_policy` and `rotation_policy` as prose, so
`reconcile_plan` (I-078, D-068) compares a plan's counts with the schedule but only records the
policies. It also infers whether a batch is the whole experiment or one cell of a treatment
varied across batches, and a cell batch cannot say which level it runs.
**Decision:** Add `analysis-plan-v2`, identical to v1 except that `design` requires
`scope` (`experiment|cell`), a `seed_policy` equal to the runner's formula and a
`rotation_policy` in `single|permutations|cyclic`; a `cell` plan declares at least two levels.
`amaw benchmark` refuses a v2 plan whose scope or policies disagree with the schedule. A cell
batch names its level with `--plan-level`, recorded in `batch.json`, never in the plan: every
cell of one experiment must seal the same plan, or `analysis-plan verify` would call the other
cells `post_hoc_plan`. v1 loads and reconciles exactly as before. `benchmark-methodology.md`
0.3.11 §4.2; `docs/analysis-plan-v2-example.yaml`.
**Consequence:** A v1 plan, its hash and its batch record are unchanged. Nothing sealed in a run
manifest changes; the named level is batch evidence only, and whether the batch's condition
really is that level is not checked, because `treatment.factor` stays prose.

### D-108 — Two I-119 structural verdicts are revised (I-153, I-154)
**Date:** 2026-09-26 · **Status:** accepted
**Context:** The owner asked whether the I-119 hotspot plan's "no split" verdicts were right. A
re-read kept the rejection of a phase split of `game_service.py` (the transitions cross phases),
but found two splits the plan either dismissed or did not consider.
**Decision:** Split `app/api.py` by audience: player routes and admin routes follow different
identity and visibility rules and are reviewed separately (I-153). Move `game_service`'s error
classes and result dataclasses into their own modules, re-exported unchanged (I-154). Everything
else in the plan stands.
**Consequence:** `app.api` and `app.game_service` keep their public names; tests that patch
`game_service` module attributes are unaffected because helpers do not move.

### D-107 — Issue- and task-scoped test IDs are traceability, not contract (I-136)
**Date:** 2026-09-26 · **Status:** accepted
**Context:** Tests cite about 170 `TEST-*` IDs that `docs/testing.md` does not declare. Most are
defined only in task briefs and the resolved log under `dev/`, which the public snapshot omits
(D-083) and which I-152 will not ship. A public reader had no way to resolve them.
**Decision:** `testing.md` 0.3.10 §1.1: only the IDs and ranges the document declares are
contract. Issue- and task-scoped IDs are maintainer traceability. The carrying test's name and
docstring must state the property on their own. No public index of the scoped IDs is kept.
**Consequence:** Scoped IDs may be regrouped or merged with their tests (I-140) without a spec
change. A test whose docstring cannot stand alone is a documentation defect in the test.

### D-106 — The public repository becomes the upstream; a local web workbench is the next milestone (I-152)
**Date:** 2026-09-26 · **Status:** accepted
**Context:** The MVP (Gates 0–3 and Release) is closed, and T-071 built a history-free public
snapshot from an allowlist. Development would otherwise stay here, with a one-way export: public
issues and pull requests would have no path back. The process layer was built for a greenfield
MVP and has grown heavy (`STATUS.md` alone is 117 KB and is read at every session start). The
owner also wants a browser frontend, which TRD §41 lists as out of lean-MVP scope.
**Decision:**
- After I-152, the public GitHub repository is the upstream. This repository is frozen as an
  archive and receives no new work.
- New work is tracked as GitHub issues and pull requests, not as `I-0NN`/`T-0NN` entries, briefs
  or `WBS.md` steps. `AGENTS.md` and `DECISIONS.md` ship publicly, so the code's `D-` references
  resolve. The normative contracts, test-ID naming and the architecture rules of `AGENTS.md §6`
  stay.
- The first public milestone is a **local workbench**: `amaw web` serves a browser UI from the
  same origin on 127.0.0.1, over the same SQLite file. There are no user accounts, and provider
  keys stay server-side. It starts as a read-only viewer of live and past runs, then adds Actor
  View through the visibility service, launching runs, a scenario editor and run comparison.
- The React source lives in `web/`. Its built assets are committed inside the Python package, and
  CI rebuilds them and fails on any difference. `uv sync && uv run amaw web` must never need Node.
**Consequence:** TRD §41 must be amended before any workbench code lands. That amendment is the
first spec change in the public repository, in its own commit. Frontend contributors need a
pinned Node version and must commit rebuilt assets. Open backlog items here either become GitHub
issues or stay archived with this repository.

### D-105 — A recovery pass is recorded and reported apart from a chosen pass (I-150)
**Date:** 2026-09-26 · **Status:** accepted
**Context:** When a Con activation fails and `pass_con` is legal, the runner passes to keep the
game moving (agents.md §18). The `con_passed` Event and the run summary's `passes` did not say
so. In run 19, 4 of 36 passes were connection failures (the White House and China in turn 1,
China and the EU in turn 2), recoverable only by joining `agent_errors` on actor, turn and phase.
Methodology §4 requires Cons and passes per Argument, so an outage would read as a strategic
choice not to Con.
**Decision:** The runner's failure row carries `provider_metadata.recovery = {action,
argument_id}` whenever it took a legal completion (`argument_id` only for `pass_con`). The run
summary's `con_condition` adds `recovery_passes` per Argument and in the totals, a subset of
`passes`, and the summary line says how many passes followed failed activations. The
`con_passed` Event, the server API and the export format are unchanged: the server cannot verify
why a Seat passed, and the runner already reports its failures through the admin provenance
API. agents.md 0.3.10 §18; benchmark-methodology 0.3.10 §4.
**Consequence:** Analyses of Con behaviour subtract `recovery_passes` from `passes`. Games
recorded before this decision, run 19 included, have no marker, so their recovery passes are
unknown, not zero; RUNS.md gives run 19's four from the log. Recovery `ready` in Diplomacy is
marked the same way but not yet summarised; nothing reports ready counts as a behaviour today.

### D-104 — Scenario versions are semver and locked to their content hashes (I-143)
**Date:** 2026-09-25 · **Status:** accepted
**Context:** scenario-format §20 required a version change for benchmark-relevant edits, but
nothing checked it. Versions followed three conventions: semver (`orison-crisis`,
`skaro-crisis`), a bare integer (`northern-strait` 1, the Swedish scenario 6), and a version in
the directory name (`swedish-government-v6`). Backfilling hashes from git found one unbumped
change: `northern-strait` 1 rewrote its `description` 36 minutes after release (`41f6cbe`),
between runs 3 and 4.
**Decision:** Every new version is `MAJOR.MINOR.PATCH`, and any change to hashed content bumps
it; the size only signals redesign, tuning or text fix. `scenarios/versions.lock` records each
released version's scenario and Resource hashes as append-only history, maintained by
`scripts/lock_scenarios.py`, and TEST-I143-001 fails on content that no longer matches its
declared version. Each package keeps a `CHANGELOG.md`. Old versions are recovered through git
tags `scenario/<id>/<version>`, not copies. The two integer versions stay as pinned legacy
entries until their next bump. `northern-strait` stays at 1 for its current content, because
runs 4 onward all played it; its changelog records the pre-release hash only run 3 used.
Loaders keep accepting integer versions. scenario-format 0.5.2 §5 and §20.
**Consequence:** Editing a scenario means bumping its version, running the lock script and
writing a changelog entry, or the suite fails. The directory name `swedish-government-v6` is
left for a separate rename, because it is a bundled CLI name.

### D-103 — Superseded prompt versions move to `docs/prompts/superseded/` (I-142)
**Date:** 2026-09-25 · **Status:** accepted · reverses the flat-directory rule in `docs/prompts/README.md`
**Context:** `docs/prompts/` held 23 files, but the code loads only 9 (plus the unwired
evaluator). The 11 superseded versions were never read by code or tests, and the wheel still
bundled them. The flat layout had been kept because "the citations are path-based". Run
manifests seal a prompt's version and `prompt_hash`, not its path.
**Decision:** Superseded versions (player v1.1–v1.9, umpire v1.1–v1.2) move byte for byte to
`docs/prompts/superseded/`. They stay in `docs/`, not `dev/archive/`, because published run
evidence cites them and the public source has no `dev/`. `amaw_resources/prompts/` holds only
the loadable versions. When a version stops being loadable, it moves in the same change.
`agents.md §31` (0.3.9) states the layout. `TEST-I142-001…003`.
**Consequence:** Old path citations in closed briefs and run records remain history. The
prompts README maps each version to its current path.

### D-102 — The specification is consolidated; the original planning documents are archived
**Date:** 2026-09-25 · **Status:** accepted
**Context:** A documentation review found drift and duplication around the six contracts. The
PRD, TRD and WBS filenames carried versions that no longer matched their contents
(TRD file v0.5.1, content v0.5.5). Every contract's "Aligned with" line cited a different TRD
version. TRD §5, §14, §15, §19, §20 and §22 restated the repository layout, state machine,
API, player tools, player prompt and Umpire contract in forms that had gone stale (no Reply
phase, no `pass_con` or `reply_to_cons`, an 8-file `app/`). The original WBS still called
benchmarking MVP Gate 4. Two indexes listed the current prompts. The Field Manual
(`guide/manual.html`) was linked from nowhere and showed `skaro-crisis` as bundled.
**Decision:** The living requirements documents are `docs/PRD.md` and `docs/TRD.md` (TRD 0.6.0).
The original PRD v0.4.1, TRD v0.5.1 and delivery WBS, plus `UPDATE_SUMMARY.md`, are archived in
`dev/archive/docs/original-vision/` as the record of the original vision. `dev/WBS.md` alone
governs sequencing. The restating TRD sections keep their numbers but now state only the
architectural constraint and point to the contract that owns the detail. Contract headers state
the current version only; their earlier notes move verbatim to
`dev/archive/docs/contract-version-history.md`. `docs/prompts/README.md` is the only prompt
index. `guide/development.md` is folded into `CONTRIBUTING.md`, which already said the same.
The Field Manual is linked from `README.md`, `guide/README.md` and `docs/README.md`, and its
Skaro entry and command builder say Skaro runs by path from a checkout. No normative rule changes.
**Consequence:** Citations of "TRD §N" stay valid. Closed briefs and gate records keep their
old paths as history. A contract version bump now replaces the Version line rather than adding
a "Previous:" clause; the decision log holds the history.

### D-101 — Two lax configuration boundaries fail closed (I-127)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** Widening the mypy scope (I-127) found two values that could not be typed honestly
without a cast, because invalid input passed through them. First, a Player's top-level
`reasoning_effort` (`--player-reasoning-effort`) was never checked, although inference-profile
entries must be one of `REASONING_EFFORTS`; an unknown level reached OpenRouter. Second,
`resume_umpire_from_provenance` sealed `resume.umpire.model: null` when a run-manifest request's
Umpire configuration had no model, and resumption checks only that the key is present.
**Decision:** `PlayerConfig` refuses an unknown `reasoning_effort` with the same `ValueError` form
as its other fields. `amaw_contracts.ReasoningEffort` and `is_reasoning_effort` type the
vocabulary; the tuple's values are unchanged. Deriving `resume.umpire` from a configuration
without a model string raises `ProvenanceError`, which the run-manifest endpoint answers as 400
`invalid_request` without sealing anything. An absent Umpire section is not derived from, so
the manifest validator's existing error still reports it. `TEST-I127-002/003`.
**Consequence:** Behaviour changes only for invalid input, which now fails before any provider
call or sealing. Every valid manifest, hash, recipe and request is unchanged. The Umpire's own
`--umpire-reasoning-effort` is still unchecked; that is I-129.

---

### D-100 — The supported deployment is one server instance over one local SQLite file (I-123)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** INV-GAME-004/005 and the state-machine §25 races are enforced by `BEGIN IMMEDIATE`
guarded writes and by re-checking after the out-of-transaction Umpire and consequence calls.
Both rely on SQLite's single-writer serialisation. The docs said only that "SQLite transactions
and careful state validation are sufficient", so nothing stopped a reader treating another
database, several server instances or a network filesystem as a configuration choice.
**Decision:** TRD §26.1 (0.5.5) states the supported envelope: one AMAW server application
instance over one SQLite database file on a local filesystem, configured as a path. It names the
invariants that depend on it and says that leaving it is a design change, not a configuration
change. TRD §17 points to it. `TEST-I123-001/002` keep the statement in the TRD and true of the
code.
**Consequence:** No code change. Supporting another engine, several serving instances or a
network filesystem needs a new locking and isolation design, race tests and its own decision.

---

### D-099 — Scenarios declare Diplomacy and Con condition defaults; one resolver (I-118)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** `consequence_mode` could be a scenario default (D-091). The Diplomacy and Con
conditions could not: they were creation-time only, and the CLI Settings and the API body filled
in the released `open_diplomacy`/`open_cons` before the scenario was consulted. `skaro-crisis`
1.1.0 therefore documents its cheaper condition as README flags, and an operator who omits them
silently runs the expensive run 17 condition.
**Decision:**
- **Schema.** Optional `game.diplomacy_mode`, `game.diplomacy_rounds_per_turn`, `game.con_mode`
  and `game.con_challengers_per_argument` (scenario-format §6, 0.5.1). They are validated by the
  one `GameLoopCondition.validate` (I-060), plus K against the opponents at load.
- **One resolver.** `app.game_loop.resolve_game_loop_condition(explicit, scenario)` resolves
  each field: explicit value, then scenario default, then released default. `None` means not
  supplied; there is no truthiness test. A count not given explicitly is inherited only from a
  source whose mode equals the resolved mode. Game creation (the admin API), `amaw run` and
  `amaw benchmark` all call it. The CLI's explicit value is the flag, else an `AMAW_*` value
  actually set (`model_fields_set`); a Settings default is not explicit.
- **One type.** The resolved condition is the existing frozen `GameLoopCondition`; the source
  layers are the frozen `GameLoopConditionLayer`. No parallel resolved type is added, and
  `as_dict()`/`provenance()` stay the serialisation boundary, so an unchanged effective condition
  serialises byte-identically.
- **Runner.** `AutonomousGameRunner` still sends its whole condition explicitly, so what it seals
  is what the server persists. Callers resolve before constructing it.
- **Resume.** Unchanged: it restores the sealed recipe and compares it with the Game snapshot. It
  never re-resolves, so historical runs keep the condition they ran under.
- **API.** The Diplomacy, Con and Con-reply body fields become nullable-omitted (api.md 0.4.10).
**Consequence:** A scenario default is part of the sealed experimental condition, the same as a
flag. Scenarios without the new fields behave exactly as before. `skaro-crisis` is not changed
here: declaring its recommended condition is a benchmark-relevant content change that needs a
new scenario version (§20), filed as follow-up.

### D-098 — Player call cost comes only from the provider's report (I-117)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** Player calls go through PydanticAI 2.42's `OpenRouterModel`. It copies OpenRouter's
`usage.cost` to `ModelResponse.provider_details["cost"]`. `RequestUsage.cost` is a different
thing: PydanticAI fills it with a local genai-prices estimate. The player read
`RequestUsage.cost`, which was unset at the model boundary, so no player call was costed.
**Decision:**
- **The source.** Player cost is `provider_details["cost"]`, read once per returned request in
  `_TimedModel`. `RequestUsage.cost` is never recorded, because it is an estimate (D-039).
- **No request change.** OpenRouter reports cost without the usage-accounting opt-in (the
  Umpire client never sent it), so the player request body is unchanged.
- **Zero.** An explicit numeric `0` is a known zero cost. The 2.42 adapter copies the cost only
  when it is truthy, so a reported `0` arrives as unavailable and is recorded as `null`. A test
  pins that adapter behaviour.
**Consequence:** Player `AgentRun`s now carry cost and `costed_model_calls`. The export and
schema are unchanged, since `cost_usd` was already nullable. Earlier runs, including run 17,
stay player-uncosted and must not be compared on cost with later runs. A free model, which
reports zero, reads as partial coverage until the adapter forwards zero.

### D-097 — The direct provider client reads at most 2 MiB of response body (I-122)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** `agents/model_client.py` streams a completion into memory and checks only the
wall-clock deadline. A misbehaving gateway could grow memory at line rate for up to the
600-second Umpire deadline before any JSON or schema check runs. Inbound API bodies are already
capped at 65,536 bytes. Outbound Umpire and resolver calls had no cap.
**Decision:**
- **The limit.** `MAX_RESPONSE_BYTES = 2 MiB`, fixed and released. A structured adjudication
  or consequence completion is a few KiB, and a long reasoning trace is still orders of
  magnitude smaller, so a normal run never meets it. The limit is not a run condition, flag or
  setting. The constructor parameter exists only for tests.
- **Enforcement.** Bytes are counted as they stream. The first chunk that would cross the limit
  is dropped, and no further chunk is read.
- **Classification.** A new non-transient category, `response_too_large`. Neither the client's
  single retry nor the Umpire or resolver attempt bound (D-063) repeats the call. Repeating an
  oversized answer from the same routing is not a recovery. The Umpire pauses the game as for
  any deterministic provider failure (TRD §35 "deterministic failure → stop").
- **Provenance.** `max_response_bytes` is recorded in the Umpire and resolver provenance
  `settings`, beside `timeout`, `deadline` and `attempts`. Per-call `AgentRun.model_parameters`
  is unchanged: it records the request's parameters, and the limit is a client property sealed
  once per game.
- **Scope.** Player calls go through PydanticAI's own client and are not covered.

**Consequence:** new manifests carry one more key under `umpire.settings`, and under
`consequence.settings` for a `bounded_effects_v1` game. A batch seals it as part of the Umpire
condition, so it is compared like the other bounds. Resume recipes are unchanged:
`resume_umpire_from_provenance` reads the same five keys. A change of the limit would be a new
Umpire condition.

---

### D-096 — `umpire-v1.6` bounds and disciplines Umpire Effects (T-082)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** In run 17 the Umpire (`umpire-v1.5`) wrote 4–5 Effects per adjudication, and up to
8. It rarely superseded one, so 105 of 123 Effects were still active at the end, about 25k
characters in every context. The Umpire's input per turn doubled from 44k to 91k tokens over
four turns. Many late Effects recorded inaction ("Defence Command concludes in a silent,
lawful… posture"). A longer game would degrade every model's context. The server cap is 10 per
branch (`MAX_EFFECTS_PER_BRANCH`), and v1.5 asks only for "concise" Effects.
**Decision:**
- **New prompt, `umpire-v1.6`:** `umpire-v1.5` plus an "Effect discipline" paragraph.
  - At most 3 Effects per branch, counting branch-independent Effects.
  - An Effect only for a change later adjudication needs. Related points are merged.
  - No Effect that only records maintaining, continuing, confirming or concluding something,
    or doing nothing new. An empty branch is legitimate.
  - Updating an active Effect's subject means one updated Effect that supersedes the old one.
- **Enforced at the AI boundary, not in the server.**
  - Under v1.6 the structured-output schema sets `maxItems: 3` for both Effect lists. It is
    recorded as tool schema `umpire-output-v2`.
  - The Umpire client classifies more than 3 as retryable unusable output
    (`effect_bound_exceeded`), within the existing four-attempt budget (D-063).
  - The server's version-independent cap stays 10, so earlier prompt conditions are
    unchanged.
- **Default.** v1.6 replaces v1.5 as the default for scenarios that declare rules. v1.6 is
  rules-aware and reply-aware, so either set of features is satisfied. v1.5 stays selectable, for
  example to reproduce run 17. Rule-free games keep their released defaults: v1.3 for
  `sealed_cons` and v1.4 for `visible_reply`. v1.6 may be pinned for them.
**Consequence:** T-082 amends agents.md §21/§22/§27/§31 and the prompt index. A game with rules
is now a different Umpire condition from run 17, and the manifest records the prompt version and
hash. The discipline is instruction plus a hard count. "Only real changes" is still model
judgement and is measured in the next provider run (T-083).

---

### D-095 — Scheduled scenario injects release authored Effects at a turn start (T-081)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** Run 17 (`skaro-crisis`) stopped being a game after turn 2.

| | Turns 1–2 | Turns 3–4 |
|---|---|---|
| Rulings decided by a roll | 10 of 12 | 0 of 12 |
| Cons | 14 and 11 | 7 and 2 |
| Public messages | 19 and 14 | 11 and 1 |

Once the Actors had settled the opening problem, nothing new entered the world. AMAW has no
exogenous input after turn 0. The consequence pass only derives from evidence already
committed, by design (D-091), and it correctly committed nothing in turn 4. Facilitator
injects are a standard matrix-game device for keeping pressure on, and the T-072 audit did not
consider them. They need a generic mechanism, not scenario-specific prose.
**Decision:**
- **Schema.** A scenario may declare `injects`, at most 32. Each inject is `{id, turn,
  description, visibility, visible_to?, expires_turn?}`.
  - `turn` is between 2 and `max_turns`. Turn-0 facts are starting Effects.
  - `expires_turn`, when set, is greater than `turn`.
  - The id pattern, the ban on unresolved markers, and the private-requires-`visible_to` rules
    all match `rules` and starting Effects.
- **Release (state-machine §15).** At the start of turn T, after Effect expiry (step 5) and
  before `phase_started(diplomacy)`, the server creates one Effect per inject scheduled for T,
  in declaration order.
  - Each Effect has `created_turn = T` and `source_inject_id = id`.
  - Each emits `effect_created{effect_id, source_inject_id}`, whose visibility follows the
    Effect's.
  - The release is part of the same turn-start transaction. It is deterministic and uses no
    RNG. A restart cannot release an inject twice, because the turn has already advanced and
    the server refuses an existing `source_inject_id`.
- **Unconditional.** An inject is authored world input, not a branch the server evaluates.
  Conditions such as "if the cable is repaired" are not supported (no rules engine, TRD §41).
  An inject's wording should therefore stay true whatever the Actors did, for example a new
  fault elsewhere or a new demand from outside.
- **Hidden until released.** Injects are snapshotted on the Game at creation
  (`scenario_injects`), are covered by the scenario hash and appear in the administrative
  export. No Actor-facing API shows an unreleased inject. The Umpire sees only released,
  active Effects (the existing D-029 rules). Released Effects are ordinary Effects, so Umpire
  supersession, expiry and consequence-pass citation apply unchanged.
- **Scenarios without injects** are unchanged in state and events. The export gains
  `game.scenario_injects` and `effects[].source_inject_id`, and its format becomes 16.
**Consequence:** T-081 amends state-machine §15/§24, scenario-format §11.2/§19/§20 and api.md
(export). Authors get one lever against late-game slack. The lever is blunt: an inject cannot
react to play. That is deliberate, and conditional injects wait for evidence that timed ones
are not enough.

---

### D-094 — Extra Arguments are round-robin Argument rounds (`round_robin_v1`); other extra-action kinds stay deferred (I-109, T-080)
**Date:** 2026-09-24 · **Status:** accepted
**Context:** I-109 is ready to plan. T-072 H4 found three structures that need more than one
action per turn, and they differ in kind:
- typed per-turn slots (Israel-Hizbullah);
- a conditional bonus action (ISIS Crisis);
- a once-per-game bonus after the normal round (BEAR RISING).

The *Influence Wargaming Handbook* flags one action per turn as too few. Run 17 (D-093) showed
the cost: the Cabinet bundled several decisions into one Argument, and one roll decided all of
them. The loader has refused any budget other than 1 since D-069, because §20 moves to the next
Actor after one resolved Argument. H4 requires the sequencing policy to be explicit and "never
arbitrary consecutive N Arguments".
**Decision:**
- **Policy.** `game.primary_arguments_per_actor` stays the single canonical field. The loader
  accepts 1–3 and rejects anything else.
  - The value 1 is the released loop, unchanged byte for byte in state, events, contexts,
    export and provenance.
  - A value of N = 2 or 3 selects `round_robin_v1`. The Argument stage of a turn runs N
    **Argument rounds**. Each round walks `turn_order` once and gives each Actor slot `r`, so an
    Argument's `budget_slot` equals its round.
  - After the last Actor's adjudication in round `r < N`, §20 hands the `argument` phase to the
    first Actor in `turn_order` and emits `argument_round_started{turn, round}` before
    `phase_started(argument)`. After round N, §20/§21 run unchanged: the consequence pass or turn
    completion.
- **Why round-robin and not consecutive slots.** Every other Actor can Con, negotiate and react
  between one Actor's Arguments. Turn order does not become a double-move advantage. Each
  component gets its own Cons and its own roll. The round-robin cap of 3 bounds turn length.
- **No new state.** The current round is derived from persisted Arguments: the count of the
  active Actor's Arguments this turn, plus one while in `argument`, or the current Argument's
  slot afterwards. Restart and resume therefore need nothing new. The existing
  `uq_argument_budget_slot` constraint remains the database backstop.
- **Every slot is mandatory**, as the single slot is today. Declining a slot is not added.
- **Typed slots are semantic.** A scenario that needs typed actions writes a `rules` entry that
  cites the slot (for example, "an Argument in `budget_slot` 2 must be a military action").
  When N > 1, the Actor and Umpire contexts carry `budget_slot`. No slot-type schema is added.
- **Contexts, only when N > 1** (so budget-1 games stay byte-identical):
  - The Actor context adds `argument_budget {primary_arguments_per_actor, round, used}`, and
    `current_argument.budget_slot`.
  - The Umpire's `argument` input gains `budget_slot` and `primary_arguments_per_actor`.
  - The player packet forwards `argument_budget`.
  - No prompt text changes (`umpire-v1.5`, `player-v1.10`…`v1.13` stand). Budget-1 identities
    are unchanged, and the added fields are self-describing data.
- **Provenance.** When N > 1, the server-built `scenario` section of the run manifest adds
  `argument_sequencing: round_robin_v1` and `primary_arguments_per_actor`. The budget is also
  inside the scenario hash. The export already carries the budget and each Argument's
  `budget_slot`, so the export format stays 15.
  - *Amended during T-080:* the first draft sealed these in `game_loop`. That section is the
    runner's declared creation-time condition, and a resume re-derives it and compares it
    exactly. A scenario-owned budget there would make the runner claim what only the scenario
    decides.
- **Runner.** When N > 1, the player lifecycle's opportunity key includes the round. An Actor's
  round-2 Argument is a new opportunity, not a continuation of round 1.
- **Deferred, still I-109:**
  - conditional bonus actions;
  - once-per-game or post-round bonus actions, including BEAR's bonus before Consequence
    Management;
  - scarce or trigger resources;
  - declining a slot.

  Each needs its own trigger or ledger semantics, and each waits for a scenario that needs it.
**Consequence:** T-080 amends state-machine §17/§20/§24/§25 and INV-ARG-003/005, scenario-format
§6/§13, agents.md §4/§21 and api.md §6.1. `skaro-crisis` and every bundled scenario keep
budget 1. A budget-N game takes about N times the Argument-stage model calls per turn. A
comparison between budgets is a different game, and the scenario hash already separates them.

---

### D-093 — T-075 reassessment: nothing promoted; T-076 does not open
**Date:** 2026-09-24 · **Status:** accepted
**Context:** WBS T-075 requires the acceptance scenario to reassess:
- I-039, reaction diplomacy;
- I-109, one primary Argument per Actor;
- numeric modifiers and tracks;
- event-driven activation;
- authority-policy work versus the connector (I-113, D-089).

It must "promote only what the acceptance work gives a concrete scenario need for". The
evidence is the clean-room `skaro-crisis` package, with six Actors, eight rules and
`bounded_effects_v1`, plus its complete live run (RUNS.md run 17).
**Decision:**
- **No engine capability is promoted.** No part of the scenario was blocked by a missing generic
  capability.
- **I-039 stays deferred.** No Actor needed to communicate after an adjudication and before the
  next Diplomacy phase. Same-turn Arguments, Cons, owner replies and the next Diplomacy phase
  carried every reaction.
- **I-109 stays deferred, ready to plan.** The Cabinet bundled several decisions into one
  Argument ("in one decision: (1)… (2)…"). The Umpire narrowed the components separately, but
  one roll decided the whole bundle. That is a fidelity cost, not a blocker. T-072's evidence
  gate remains met; the sequencing task is planned only when a scenario needs typed, conditional
  or timed extra actions.
- **Numeric tracks and ledgers stay out.** Fuel endurance, lead times and posture stayed
  qualitative, and no adjudication turned on an exact number. The route for exact quantities
  remains I-113 (D-089), or a generic capability only on reusable evidence.
- **Event-driven activation stays out.** Timed outcomes were handled by a deterministic rule
  (`cable-repair-time`, applied correctly), by Effects and by the consequence pass.
- **Authority:** semantic rules were sufficient. 23 of 24 adjudications cited a rule by `id`,
  and a rule produced the one `automatic_failure`. I-038 is unchanged as an accepted limitation.
- **I-113:** no concrete need was recorded, so **T-076 does not open**. I-113 stays deferred.
- **Residual mechanics** receive an explicit deferred disposition in one backlog item, I-115.
- **The scenario's late-game slack** (turns 3–4 became close-outs) is a scenario-tuning item,
  I-116. It is not an engine change.

**Consequence:** The Matrix Scenario Portability milestone closes with T-075. The External
Systems milestone stays unopened, and T-076…T-079 remain unqueued. A later scenario that needs
extra-action sequencing, exact quantities or an authoritative external domain reopens the
matching item with its own evidence. Portability here is not analytical validity: run 17 is one
playtest (audit, cross-cutting section).

---

### D-092 — A benchmark batch seals and verifies the consequence resolver like the Umpire (I-114)
**Date:** 2026-09-23 · **Status:** accepted
**Context:** D-091 makes `bounded_effects_v1` a separate experimental condition that includes its
resolver model, prompt and settings. `amaw benchmark` seals one Umpire condition per batch and
verifies it on every attempt (T-056, I-078). It had no equivalent for the resolver, so T-074 made
it refuse the mode (I-114).
**Decision:**
- `benchmark` resolves the mode exactly as `run` does: flag, then `AMAW_CONSEQUENCE_MODE`, then the
  scenario. It takes `--consequence-mode`/`--consequence-model`, and the same refusals apply: no
  resolver model under `bounded_effects_v1`, a resolver model under `none`.
- Under `bounded_effects_v1` the batch seals a **consequence condition** before any game:
  `{provenance, resume}`. `provenance` is the resolver's `provenance_configuration()`, which is
  what the server writes to the manifest's `consequence`. `resume` is the recipe section
  every game seals as `resume.consequence`. `batch.json` records it as `consequence_condition`
  with a `consequence_condition_sha256`, and `experiment.consequence` holds the recipe section,
  beside `experiment.umpire`.
- Every attempt's manifest is compared with it in **both** benchmark modes. The resolver is
  common to the batch, like the Umpire. It is not player configuration, which §5.2 lets an agent
  benchmark vary. A difference is a `consequence_condition` integrity failure and sets
  `integrity.consequence_fixed = false`, and the batch stops (D-058). The mode itself is already
  in the expected condition through `game_loop` and `coordinator`.
- A `none` batch is unchanged. `batch.json` has no `consequence_condition` and no
  `consequence_fixed`, and its expected condition and hash are unchanged. A `none` batch still
  treats an attempt manifest that carries a consequence section as a `consequence_condition`
  failure, and only then is `consequence_fixed` recorded.
- A per-attempt `consequence*` override in `run_configuration["benchmark"]` is refused as the
  Umpire's is.
- `games.csv` gains three columns:
  - `consequence_mode`, copied from the export's `game.consequence_mode`;
  - `consequence_model` and `consequence_prompt_version`, copied from the manifest's
    `consequence`.

  They are copied, never derived. The resolver columns are empty for a `none` game.
**Consequence:** a batch may compare models under `bounded_effects_v1`. The results are read
only against batches of the same condition and never pooled with `none`. A batch runs one
resolver, and comparing resolvers means running one batch per resolver.

---

### D-091 — Bounded evidence-linked Consequence Management (`consequence_mode`, `consequence-v1.1`)
**Date:** 2026-09-23 · **Status:** accepted
**Context:** T-074 makes state-machine §21 Option B real. The released loop is Option A. T-072
§8.2 asks for aggregate end-of-turn consequences that cite authoritative evidence. Five things
are unsettled:
- where the mode lives;
- what may be cited;
- the visibility and lifecycle of the new Effects;
- what an unusable resolver does;
- the exact §28 schema.

`GameEvent` has no visibility field, and an Umpire rationale may reason over hidden information
(D-029).
**Decision:**

*The condition.*
- `consequence_mode: none | bounded_effects_v1` is the seventh field of the game-loop condition,
  with policy `consequence-policy-v1`.
- A scenario may declare it in `game:`. An explicit creation value (API body, CLI flag,
  `AMAW_CONSEQUENCE_MODE`) wins over the scenario, and the default is `none`.
- It is persisted on `Game` and validated on resume. In the manifest `game_loop` and the
  resume-recipe coordinator, the keys `consequence_policy_version`/`consequence_mode` appear
  **only when the mode is not `none`**, as `scenario_rules` is absent for rule-free
  scenarios. The provenance of a `none` game is therefore byte-identical to the released
  shape. An absent key means `none`, so legacy rows and recipes need no migration.

*The step.*
- Under `bounded_effects_v1`, advancing past the last Actor enters `consequences` with no active
  Actor.
- `resolve_consequences` runs the single pass. It builds a read snapshot, calls the resolver
  outside any write, then commits the whole proposal in one guarded transaction, after
  revalidating the phase and that no record exists for this turn.
- Advancing from `consequences` requires that record and completes the turn through the same
  helper `none` uses. Admin `/advance` resolves a pending pass as it resolves a pending
  adjudication.

*Resolver input* is public only:
- the scenario summary, turn, turn length, and `scenario_rules` when declared;
- one outcome per current-turn `argument_adjudicated` Event: the event id, Actor, Action,
  Desired Effect, mode, success and created Effect ids. No Umpire rationale, no probability or
  roll, no Cons;
- active **public** Effects.

No private information, private Effect, Message, Note or Resource content reaches the resolver.

*Evidence and output.*
- Citable evidence is exactly:
  - `{"kind":"event","id":<int>}`: a current-turn `argument_adjudicated` Event of this game.
    Its payload is already public;
  - `{"kind":"effect","id":"<uuid>"}`: an Effect of this game that is active **and public** at
    commit.
- Every Effect needs 1–8 distinct references.
- The output is `{"effects":[{"description","rationale","evidence"}]}`, with 0–3 Effects. Text
  is non-empty and at most 4000 characters.

*Consequence Effects.*
- They are public, never supersede and never expire (`expires_turn = null`).
- Their origin is `Effect.source_consequence_turn`. `source_argument_id` is null.
- One `ConsequenceRecord` per `(game, turn)` stores the accepted Effects with their rationale and
  references. The primary key is the idempotency guard.
- Events: `effect_created` for each Effect, then `consequences_resolved`.

*Failure.*
- An unavailable resolver pauses the game in `consequences` with the event
  `game_paused{reason: consequence_resolver_unavailable}`, surfaced as `503` at the API. The
  resolver keeps its bounded attempts: 4, with semantic retries on context-invalid references.
- A proposal that fails server validation also commits nothing and pauses with the same reason
  (`failure: invalid_proposal`). No record is written, so a resumed game re-runs the pass. The
  pass is never skipped or fabricated.
- The pass draws no RNG.

*Provider.*
- The LLM resolver is `agents/consequence.py`: httpx and OpenRouter with a strict schema.
- The prompt is `consequence-v1.1`.
- Attempts are `AgentRun` rows with `role = "consequence"`.
- The CLI refuses `bounded_effects_v1` without `--consequence-model` or `AMAW_CONSEQUENCE_MODEL`.
  It seals the resolver recipe (model, prompt version, temperature, max tokens, reasoning,
  timeout).
- The export carries the condition, the records and the Effect origins, with format 15.

**Consequence:**
- `none` games keep the released loop and provenance exactly. Only the Game row and the
  export's `game` section (format 15) name the mode explicitly.
- A `bounded_effects_v1` game is a separate experimental condition (agents.md §27), including
  its resolver model and prompt.
- Consequence Effects cannot be private or Actor-scoped, cannot resolve contradictions by
  supersession, and cannot cite Messages or Cons. Widening any of that needs a new policy
  version.

---

### D-090 — Scenario `rules` are a separate public, non-executable Umpire input under `umpire-v1.5`
**Date:** 2026-09-23 · **Status:** accepted
**Context:** T-073 adds a scenario-level `rules` declaration. `agents.md §23` and `umpire-v1.3`
already refer to "scenario rules", but the §21 input contract has no slot for them. Authors put
rules into `scenario.description`, which reaches the Umpire as `scenario_summary` without
identity or binding framing (T-072 §8.1). The Umpire prompt is immutable under its version id,
and §27 treats a prompt change as a separate condition.
**Decision:** `rules` is an optional top-level list of `{id, text}`. There are at most 32 rules.
Each `id` is lowercase and unique, and each `text` is non-empty, at most 1000 characters and
free of unresolved markers. There are no other fields, so no private rules. The loader
snapshots the rules onto the Game at creation (`Game.scenario_rules`). They are hash-affecting
because the scenario content hash covers `scenario.yaml`. The Umpire receives them verbatim, in
declaration order, as a new §21 field, `scenario_rules`. The field is **absent** when a scenario
declares none, so the context of rule-free scenarios stays byte-identical. `scenario_summary`
is unchanged. A new prompt, `umpire-v1.5`, is `umpire-v1.4` plus a rules paragraph: rules are
binding scenario content, applied at the authority, prerequisite and deterministic gates. They
are cited by `id` in the rationale. They cannot waive a gate, set a probability, or add an
Effect or an Actor's commitment by themselves. A game whose scenario declares rules defaults to
`umpire-v1.5`, and it is refused under v1.3/v1.4 at the CLI; the AI Umpire also refuses such a
context, which pauses the game with no draw. v1.5 may be pinned for rule-free games. Rules are
public: the Actor activation packet carries `scenario.rules` only when rules are declared.
The administrative export always carries `game.scenario_rules`, which bumps
`EXPORT_FORMAT_VERSION` to 14. The engine never interprets rule text.
**Consequence:** A rules scenario is a distinct Umpire condition (§27), and adding or changing
rules requires a scenario version bump (§20). No rules engine or per-rule mechanics exist, and
scenario-format §22's "authority/approval rules" stays out of scope: a rule is text the Umpire
weighs, not an authority model the server enforces.

---

### D-089 — I-038 is an accepted limitation; specialist mechanics go to external systems (I-113)
**Date:** 2026-09-23 · **Status:** accepted
**Context:** D-044 left server-enforced typed commitments/derivations open as I-038, implicitly
as future internal engine work, and T-068 cited I-038 as if two-stage adjudication were its
route. Neither holds: no adjudicator design gives a mechanical semantic guarantee, and a
universal typed authority/world model conflicts with TRD §41's exclusion of authority graphs and
formal Claims. External review proposed delegating mechanically authoritative domains to
external systems through a generic connector instead.
**Decision:** I-038 stays open as an accepted limitation for narrative state; AMAW will not build
a universal authority ontology to remove it. Where a scenario needs mechanically enforced state,
that domain belongs to an authoritative external system reached through a transport-neutral
connector (`capabilities`/`observe`/`execute`/`events`), filed as I-113 and extending TRD §43.3;
MCP is one adapter, not the contract. T-067…T-069 are reframed as a model-reliability
experiment and lose the I-038 dependency. T-072 classification (4) names the connector route;
T-075 reassesses authority work against it. A new post-release milestone queues only T-076
(contract design, after T-075 and only on a recorded need); T-077…T-079 are sketched, not
queued. T-073, T-074 and T-075's no-scenario-specific-Python rule are unchanged.
**Consequence:** The D-044 line "server-enforced typed commitments/derivations remain I-038" is
superseded in direction, not in fact — prose-only scenarios still rely on `umpire-v1.3`
narrowing. Letting an external system be authoritative over its own domain changes TRD §43.3's
invariant that external tools never mutate authoritative AMAW state; that amendment is deferred
to T-076 and needs its own entry and doc commit. No code, spec or run evidence changed.

---

### D-088 — `search-v2` is the released default; legacy records still resolve to `search-v1`
**Date:** 2026-09-23 · **Status:** accepted
**Context:** I-112 lands D-087's Decision A. A released default is two different questions that
D-084 could answer with one value and no longer can: what a *new* run resolves when nothing
states a contract, and what a record *written before this change* means when it names none.
`agents/tools.py` also omitted the `contract` parameter whenever the sealed condition was v1 and
let the server's default supply it — harmless while both were v1, silent corruption afterwards.
**Decision:** `SEARCH_CONTRACT_DEFAULT` (= `search-v2`) is the released default and is used at
every composition point: `Settings.player_search_contract`, `PlayerConfig.search_contract`,
`resources.search_resources` and the `GET …/resources/search` `contract` parameter. The legacy
resolution in `PlayerConfig.from_resume_recipe` stays `SEARCH_CONTRACT_V1` permanently — a recipe
sealed under the old default is never reinterpreted. The player tool now always sends the
contract, so a sealed condition never rides on a server default. `search-v1` remains fully
supported and explicitly selectable through `AMAW_PLAYER_SEARCH_CONTRACT` and
`--search-contract`. `api.md` 0.4.5.
**Consequence:** Runs recorded before I-112 are unaffected and remain reproducible by naming
`search-v1`, but they are not comparable with new runs unless the sealed contract matches — the
manifest, resume recipe and AgentRun rows already carry it, so every run says which it played
under. Acceptance tests whose subject is visibility rather than ranking had to stop asserting
ranking-dependent exact result sets (`TEST-RES-001`, the cross-game isolation test): they now
state what game scoping and visibility actually guarantee. No model-facing prompt or tool schema
changed, so no `*_TOOL_SCHEMA_VERSION` moved.

---

### D-087 — I-100 adopts the sealed `search-v2` candidate for the future default
**Date:** 2026-09-23 · **Status:** accepted
**Context:** The predeclared I-100 Northern Strait comparison completed four provider-backed
games, pairing seeds 60000 and 60001 under released `search-v1` and frozen D-084 `search-v2`.
Across 109 v1 searches and 56 v2 searches, zero-result rate fell from 88.99% to 3.57%,
identical-result reformulation rate from 50.46% to 23.21%, and returned-read conversion rose
from 0.92% to 58.93%. All games completed six turns with the same Argument/adjudication count;
v2 used fewer player calls and tokens. Cost telemetry was partial and latency noisy.
**Decision:** Record Decision A: adopt `search-v2` as the future released default, while keeping
`search-v1` supported and explicit for reproducibility. Make the actual default change in the
separate I-112 follow-up; I-100 itself does not change released behaviour.
**Consequence:** The comparison is complete and v2 is evidence-backed for the current provider
condition, but until I-112 lands, missing or legacy search-contract values still resolve to the
released `search-v1` default. Future benchmark records must continue to seal the chosen contract.

---

### D-086 — Matrix Scenario Portability is a post-release milestone, not a set of ad-hoc features
**Date:** 2026-09-22 · **Status:** accepted
**Context:** An external review of AMAW against the wider matrix-game literature asked how
general the engine is. It found the released loop close to a generic matrix game (Argument →
Cons → `visible_reply` → adjudication → Effects) but missing an authoritative end-of-turn
Consequence Management step (spec'd in `state-machine.md §21` Option B / `agents.md §28`, skipped
as T-052), a first-class place for scenario-specific adjudication rules (today smuggled into
briefs, Resources or starting Effects), and support for more than one primary Argument per Actor
per turn (`primary_arguments_per_actor` is refused unless 1). Several larger ideas — reaction
diplomacy, typed authority, organisations, comms topology, event-driven activation — were also
raised.
**Decision:** Add a *Matrix Scenario Portability* post-release milestone to `WBS.md`, ordered
T-072 (audit) → T-073 (semantic scenario `rules`) → T-074 (bounded evidence-linked Consequence
Management, sealed `consequence_mode`) → T-075 (portability acceptance scenario). I-040 is
promoted to T-074; T-052 stays skipped and is not redefined. Reaction diplomacy (I-039) and
multiple Arguments (new I-109) stay in the backlog, evidence-gated: T-072/T-075 must produce a
concrete scenario need before either is planned. T-061 and T-067…T-069 are untouched. Typed
authority, organisations, communication topology and event-driven activation remain Phase 2
(`TRD §41`, WBS §11). T-073 and T-074 amend normative docs and therefore each carry their own
`D-0NN` and doc commit when started. The `WBS.md` intro no longer claims a fixed 66 steps.
**Consequence:** The engine gains at most two bounded, sealed, provenance-visible scenario
capabilities before any evidence-gated loop change is considered. The milestone does not enter
the queue ahead of I-108; nothing changes for released behaviour, defaults or the golden fixture
until T-073/T-074 land, and each keeps `none`/absent as the default.

---

### D-085 — A batched research call succeeds with ordered per-item outcomes
**Date:** 2026-09-22 · **Status:** accepted
**Context:** I-101. `batched-v1` stopped at the first recoverable Resource rejection and returned
the whole call as failed, even after earlier items had committed and been logged. The failed call
was retryable as a whole, repeating completed work and consuming more research budget.
**Decision:** After validation, execute the batch in input order through recoverable failures.
Every dispatched item returns `{query|resource_id, ok, data}` on success or
`{query|resource_id, ok, error}` on failure; `error` contains `http_status`, `code`, `message` and
`details`. A batch with at least one dispatched item returns `ok: true` and the ordered outcomes,
so its exact repetition is covered by the existing successful-call duplicate guard and
`compact-success-v1` retains the exact mixed payload. Malformed input remains a whole-call error.
D-078 still governs capacity: an executed prefix may be followed by `not_executed`; no capacity
before the first item keeps the whole-call operation-budget refusal. Bump only batched tool
schemas v15/v16 → v17/v18; defaults and sequential schemas remain unchanged.
**Consequence:** One recoverable bad query/id no longer abandons later independent work or makes
already-completed operations retryable. Individual item failures inside a successful batch are
model-visible data rather than whole-tool failures; analysis must inspect the item outcomes when
studying `batched-v1`. ResourceAccess logging is unchanged: every authorised successful operation
has its row, while a rejected/denied operation remains charged but may have no row under D-038;
`research_operations` is the inclusive dispatched-work count.

### D-084 — Resource ranking is an explicit sealed `search_contract`
**Date:** 2026-09-22 · **Status:** accepted
**Context:** I-100. The released Resource search requires one contiguous whole-query substring,
while agents ask bag-of-concepts questions. The API fixes visibility and response shape but not
ranking. Replacing the algorithm in place would make otherwise identical manifests mean different
retrieval behaviour; putting it under `research_mode` would conflate ranking with request topology.
**Decision:** Keep `search-v1` as the default and add candidate `search-v2`. The candidate splits
casefolded text into Unicode `\w+` tokens, deduplicates query tokens in first-occurrence order,
requires at least one exact token, and ranks by a fixed integer score: best field per query token
is name 8 / tag 4 / readable text 1; bonuses are exact name 1000, full phrase in name 200, an
individual tag 100 or readable text 25; Resource id is the final ascending tie-break. Snippets use
the highest-weight matching field and centre on the first matching query token when the full phrase
is absent. `search_contract` is selected through settings/CLI, sent as the API `contract` query
parameter, included in the activation replay-cache key, and sealed in the player manifest, resume
recipe, AgentRun parameters and benchmark controls. Legacy/missing values resolve to `search-v1`.
**Consequence:** Existing clients and runs keep byte-for-byte v1 search behaviour. V2 is
dependency-free and deterministic but deliberately has no stemming, fuzzy matching or semantic
retrieval. It remains a candidate until an I-099-instrumented provider comparison is recorded;
only a later decision may change the default.

### D-083 — Public code ships as a history-free allowlisted snapshot
**Date:** 2026-09-22 · **Status:** accepted
**Context:** The owner will publish AMAW in a separate public repository rather than make the
maintainer repository public. The latter has a 296 MB history, live process records and private
provider-run evidence; copying its Git history would defeat the purpose of a clean source release.
**Decision:** `scripts/build_public_source.py` constructs the public tree from an explicit
allowlist: runtime code, packaging, public CI, contracts, guides, scenarios and product-facing
tests. It excludes `.git`, `.claude`, `AGENTS.md`/`CLAUDE.md`, all `dev/` state and the small set
of tests whose only subject is that maintainer state. The destination must be empty; two builds
from one source tree must be byte-identical. The public repository begins with a new root commit.
The maintainer repository may retain run 12/13 while the separate `amaw-runs` owner step remains
pending; I-089's retention guard names those six current directories rather than pretending the
post-I-106 evidence is absent.
**Consequence:** The public Git history contains no private repository history or provider-run
data. Maintainer task IDs and decisions remain useful internally but are not a prerequisite for
public contribution; public guides and contribution instructions therefore stand alone. A public
snapshot must be rebuilt and fully validated after source changes rather than updated by copying
individual files into an existing checkout.

### D-082 — Sibling mutations in one `tool_loop` response race; a known property of the experimental condition, not serialised
**Date:** 2026-09-21 · **Status:** accepted
**Context:** I-103. PydanticAI dispatches the sync tool calls of one model response
concurrently. `_send_lock` (I-021) serialises `send_message` and the research reservation
(I-077) serialises Resource calls, but nothing serialises two *different* mutations emitted
together — `send_message` + `make_argument`, `make_con` + `ready`. Whichever reaches the server
first may move the phase; the sibling then receives a genuine `409` and is charged to the model
as an invalid tool call although which one lost depends on local scheduling, not model behaviour.
D-077's refresh runs *after* a success and cannot reach a sibling already dispatched; under
`tool_surface=available_only` (D-079) the filter acts at the next model step, not on an already
emitted response, so the property holds for both surfaces. The alternative — one lock over all
mutating tools with each call revalidated against the refreshed `available_actions` before
dispatch — would make the harness choose which of the model's simultaneous mutations counts.
**Decision:** Leave the scheduling alone and document the race. D-079 made `tool_loop` an
explicit experimental condition and `single_shot` (one validated decision, fixed execution order;
not affected) the normal player; in `tool_loop` a model that emits two mutations in one response
has already departed from the one-mutation-per-round-trip behaviour the condition expects, and
the server's `409` is the authoritative answer to the second one. A harness-side ordering policy
would be a second, `agents/`-authored legality rule — what D-077 and D-079 refuse. The property
is stated in `docs/agents.md §5` (0.3.3) and in the player/decisions docstrings; the
`invalid_tool_calls` accounting is unchanged (the 409 is counted — that is what `tool_loop/broad`
measures). Reopened only if `tool_loop` is promoted to a supported normal-play mode: then the
second branch of I-103 applies — one lock over every mutating tool, pre-dispatch revalidation
against the live declaration, and a stub-response determinism test (identical model output →
identical accepted mutations across repeated runs).
**Consequence:** A `tool_loop` `invalid_tool_calls` count can include 409s whose *victim* was
chosen by the executor, so per-call `error_code` breakdowns from `tool_loop` runs are read with
this caveat (`RUNS.md` reports name `(activation_mode, tool_surface)` already). No code, prompt,
schema or provenance change; no comparability break.

### D-081 — Run evidence lives in a separate `amaw-runs` repository; this repo retains four runs
**Date:** 2026-09-21 · **Status:** accepted
**Context:** I-089. `AGENTS.md §8` and `RUNS.md` define `dev/runs/` as the evidence store for every
provider-backed run, and the deferred Benchmarking milestone would need "30 retained attempts".
At publication the tree held 54 tracked files / 21 MB of unfiltered administrative exports
(private objectives, Notes, private Messages, provider generation ids) that were never read with
publication in mind, and 11 untracked v6 run directories (moved to `~/Projects/AMAW-runs-archive/`
on 2026-09-19). A public clone cannot carry that, and evidence must not be silently lost.
**Decision:** Benchmark evidence moves to a separate `amaw-runs` repository (owner-created; the
files are staged for it under `~/Projects/AMAW-runs-archive/<run dir>/`, one directory per
`RUNS.md` entry, so nothing is lost between the `git rm` here and the push there). This
repository retains exactly four run directories, each read before retention: run 6
(`2026-09-12-db54f331/`, the smallest complete canonical `northern-strait` game), the I-027
`report.md` (`2026-09-13-i027-deepseek-v4-flash/`, linked from `STATUS.md`), run 10
(`t060-batch-smoke-20260918/`, I-076 evidence) and run 11 (`t060-batch-smoke-20260919/`, the
first green provider batch). The rest are `git rm`'d in one commit; history is **not** rewritten
(if clone size ever matters, `git filter-repo` is a separate, explicit decision). `RUNS.md`
entries keep their narrative and cite `amaw-runs/<dir>` where the export left; new runs are
written to `dev/runs/` as before, published to `amaw-runs` and pruned here at the next close-out.
`TEST-I089-005` pins the retained set.
**Consequence:** A checkout of this repo alone no longer reproduces the run 1–9 observations in
`RUNS.md`; `amaw-runs` is the citation target. The "30 retained attempts" criterion, if T-063 is
ever reopened, is met in `amaw-runs`, not here. The retention audit (transcript in the I-089
brief) found no credentials, e-mail addresses or secret-bearing fields in the four kept runs.
What they do contain is retained knowingly and unedited — evidence files are never rewritten:
the scenario's private objectives and Notes and the Actors' private Messages (the scenarios are
published in this repository, so nothing is disclosed that a clone does not already hold);
provider generation ids and model names (provenance, not secrets); and the absolute checkout
path of the maintainer's machine in two `batch.json` files and three logs (the username is the
public git author identity).

---

### D-080 — AMAW is released under the MIT licence
**Date:** 2026-09-21 · **Status:** accepted
**Context:** I-089. `pyproject.toml` declared `LicenseRef-Proprietary` and the repository had
no `LICENSE`; nothing else in the public-release plan can ship without a licence. The choice is
the owner's; the options weighed were MIT, Apache-2.0 and AGPL-3.0.
**Decision:** MIT, copyright holder Martin Heumannbauer, year 2026. `LICENSE` carries the text;
`pyproject.toml` declares `license = "MIT"`; `README.md` carries the SPDX identifier. Scenario
packages, prompts, documentation and retained run evidence are covered by the same licence —
no separate content licence. `TEST-I089-001` pins the three declarations.
**Consequence:** Anyone may run, modify and redistribute AMAW, including in closed or hosted
form, with attribution. No patent grant (Apache-2.0 was declined for brevity) and no
network-copyleft obligation (AGPL was declined so a hosted benchmark need not publish its
modifications). Changing the licence later requires the consent of every contributor whose
work is retained.

### D-079 — `tool_loop` is an experimental condition whose mutating tool surface is a sealed two-value field
**Date:** 2026-09-21 · **Status:** accepted
**Context:** I-092. In run 11 (`tool_loop`) the models attempted `send_message` in Cons-phase
activations although `available_actions` correctly omitted it (~20 wasted calls per game). The
backlog held three positions: (a) keep measuring — an agent ignoring declared legality is what
the `invalid_tool_calls` metric and T-040 exist to observe; (b) a more salient `player-v1.14`
wording; (c) derive the mutating surface from `available_actions` (external review 2026-09-19:
"the benchmark is partly measuring how much each model wastes calls fighting an avoidably invalid
API surface" — counter: the surface is *declared* in every context, so it is not invalid, and
hiding deletes a measurement). External analysis 2026-09-20 added (d): resolve by scope —
`single_shot` is already the default (D-046) and derives its decision schema from
`available_actions`, so it has no illegal mutating surface; the attempts are a property of the
experimental tool-use condition, not of normal play.
**Decision:** (d) as policy, and both surfaces as sealed conditions rather than a permanent
choice between (a) and (c). `single_shot` remains the normal/default player condition;
`tool_loop` is explicitly experimental. Within `tool_loop`, `PlayerConfig.tool_surface ∈
{broad, available_only}`, default `broad`: `broad` is today's behaviour byte-for-byte — the
whole mutating vocabulary is offered and the D-077 rule withdraws only a declared-then-retracted
tool, so an attempt outside `available_actions` stays observable invalid-tool behaviour;
`available_only` withdraws, via the same PydanticAI `prepare` mechanism, every mutating tool the
activation's authoritative declaration (the packet's `available_actions`, or the D-077 live
refresh after the Actor's own mutation) does not list. The mapping is the identity — the server
lists actions by the api.md §8.1 tool names — so no second representation of phase legality
exists in `agents/`; research/read tools follow their own rules (I-069, D-078) in both surfaces;
the server's legality checks are unchanged and remain authoritative. `tool_surface` is sealed
wherever `activation_mode` is: `PlayerConfig.shared_condition()`, the `autonomous-resume-v1`
recipe (optional key — a legacy recipe resolves to `broad` and is sealed as given, so no existing
`tool_loop` run is silently switched), the run manifest `player.tool_surface`, every player
`AgentRun.model_parameters`, `benchmark.controls.PLAYER_CONDITION_PATHS`, `Settings`
(`AMAW_PLAYER_TOOL_SURFACE`) and `--tool-surface`. A `broad` game refuses resume under
`available_only` and vice versa. Outside `tool_loop` the value is accepted and sealed as given
but semantically irrelevant (`single_shot` has nothing to filter) — one invariant instead of a
mode×surface special case in the dataclass, the resume validator and the CLI. The
`*_TOOL_SCHEMA_VERSION` identities are **not** bumped: the tool definitions offered under `broad`
are byte-identical to v11–v16, and the condition is identified by the new sealed field rather
than by a schema identity that would misdescribe unchanged definitions. No prompt changes.
**Consequence:** Reports quoting invalid-tool rates must name `(activation_mode, tool_surface)`;
run 11 is `tool_loop`/`broad`. `available_only` runs measure a different thing (server-rejected
attempts at *listed* tools, and I-091-style staleness) and are not comparable with `broad` runs
on `invalid_tool_calls`. I-103 (sibling-mutation race) is a known property of the experimental
condition and is documented rather than serialised. Revisit (c)-as-default only if `tool_loop`
is ever promoted to normal-play status.

### D-078 — Research operations are budgeted separately from research invocations
**Date:** 2026-09-21 · **Status:** accepted
**Context:** I-098. `tracked_tool(research=True)` charges one research call per invocation and
`search_resources_batch`/`read_resources_batch` then perform up to `RESEARCH_BATCH_MAX_ITEMS = 8`
Resource operations inside that one charge. With the default `max_research_tool_calls` of 4,
`sequential-v1` performs at most 4 Resource operations per activation and `batched-v1` up to 32,
while both seal the same guardrail. D-051 admitted `batched-v1` as a request-topology treatment
and reserved any "changed budget" for a separate decision; agents.md §18 says only "Batched
research counts as one tool call regardless of its bounded item count". The manifest therefore
records two conditions as equal in a dimension in which they differ eightfold.
**Decision:** A *research operation* is one underlying Resource `search` or `read` request that
is actually dispatched toward the server. It is charged immediately before dispatch, under the
tool-call lock, so a dispatched request consumes an operation even if it later fails; anything
that does not reach the server (a D-062 exact-repeat refusal, an in-flight twin, a guardrail
refusal of the call itself) consumes none; `read_messages` consumes a research-tool *invocation*
and no operation; an I-054 replay hit is server-side and so is a dispatched operation, as today.
A second guardrail, `max_research_operations` (released default the number 4 — the same value as
`max_research_tool_calls`, not tied to it — so `sequential-v1` is effectively unchanged under
defaults while `batched-v1` loses the multiplier), is disclosed in the packet `limits`, sealed
through `resume.player.guardrails` / `model_parameters.guardrails`, and resolves to the default
for a legacy recipe that lacks it without rewriting that recipe (I-069 precedent). A batch that
would cross the remaining operation budget executes its items in input order until the budget
is spent and returns the rest, never attempted, under `not_executed` with
`guardrail_research_operations_exhausted` — deterministic truncation, distinct from I-101's
per-item error handling, which this does not change. When either budget is spent the Resource
tools are withdrawn (`prepare` → `None`, the I-069 mechanism); `read_messages` follows the
invocation budget only. Both counts (`research_tool_calls`, `research_operations`) are recorded
per activation on `AgentRun`, on success and on failure (nullable; legacy rows unavailable).
The sequential tool definitions are byte-identical before and after — one invocation is one
operation, nothing new is true of them — so `player-tools-v11`/`v13` stand; the batch tools'
documented behavioural contract changes, so `v12 → player-tools-v15-batched-live-bounded-operations`
and `v14 → player-tools-v16-batched-reply-live-bounded-operations` (D-062: version the changed
tool schemas). The player prompt is unchanged ("bounded by `limits`"). agents.md §18 is amended
in its own commit.
**Consequence:** `sequential-v1` and `batched-v1` now differ only in request topology, which is
what D-051 claimed; a sequential-vs-batched comparison can be read as an efficiency comparison.
The pre-I-098 batched runs (run 11 and the Swedish v6 batched/compact playtests) ran under an
effective operation ceiling of `4 × 8`; no comparability claim across the v12→v15 boundary. The
export distinguishes invocations from operations; `ResourceAccess` rows remain the server's
record of what actually occurred (`research_operations ≥` rows, equal on the happy path).

### D-077 — The tool-loop surface follows the server's declaration after the Actor's own mutation
**Date:** 2026-09-19 · **Status:** accepted
**Context:** I-091. `_available_actions` correctly lists `send_message` + `make_argument` for the
active Actor in Argument phase; a successful `make_argument` moves the game to Cons inside the
same activation (api.md §8.1), but the tool result carries no `phase`/`available_actions`, so
the agent's only legality signal (agents.md §4) is stale by its own legal action. Its follow-ups
— a Message it was told was permitted, `ready`, `pass_con` on its own Argument — are rejected
`409 wrong_phase`/`403 forbidden` and charged to it in the I-052 ledger (run 11: 13/19 of the
`send_message` 409s, 5/6 `ready` 409s, 5/3 `pass_con` 403s per game). The same shape was seen
for `ready` in diplomacy (I-025) and fixed only on the recovery path. The contract says the
server decides legality from `available_actions` (agents.md §3/§5, API-INV-009) and says nothing
about a declaration that the agent's own action retracts mid-activation.
**Decision:** In `tool_loop`, after every *successful* server-mutating tool call (`send_message`,
`make_argument`, `make_con`, `pass_con`, `reply_to_cons`, `ready`) the player re-reads the
server's context and (1) attaches a `context` fragment in the agents.md §6 `observe` shape —
`{turn, phase, active_actor_id, requires_input, available_actions}` — to that tool's result, and
(2) for the rest of the activation withdraws (PydanticAI `prepare` → `None`, the I-069 mechanism)
exactly those mutating tools that the activation packet *listed* and the server *no longer
lists*. Tools the packet never listed are untouched: a Cons-phase `send_message` attempt is still
registered, still callable, still a `409 wrong_phase` in the ledger — I-092's measurement is
preserved and its decision stays open. Nothing is derived from a phase literal: the harness
withdraws only what it itself declared and the server has since retracted, so a diplomacy
`ready` withdraws `ready` and leaves `send_message` (still legal) in place. Failed mutations
trigger no refresh (a declaration made stale by *another* Actor is the lifecycle's re-observe,
T-040, not this rule). `single_shot` registers no mutating tools and is unchanged. This is a
model-facing surface/behaviour change and therefore a new sealed player condition (§5.1, agents.md
§19): the four `*_TOOL_SCHEMA_VERSION` identities move v7–v10 → v11–v14 (`…-live-…`), following
I-069/D-062; `single_shot` behaviour is byte-identical across that move and may be pooled by
anyone who states so. The player prompt is unchanged (T-040 neutrality); `app/` is unchanged
(its declaration was correct). `testing.md §29`'s named identities are updated in their own
commit.
**Consequence:** One `GET /context` per successful mutation. A naive model that would have kept
using the retracted surface now sees it gone and is told why in the result; run 11's
Argument-phase cluster (≈24/28 of the per-game invalid calls) cannot recur on that path, while
the Cons-phase cluster stays measurable. No comparability claim across the v10→v11 boundary;
the next provider run reports `invalid_tool_calls` by `(phase, action, error_code)` beside run 11.

### D-076 — T-063 acceptance run and the Gate 4 exit condition deferred; T-060 stands as the provider/batch smoke test
**Date:** 2026-09-19 · **Status:** accepted
**Context:** T-063 (`WBS 5.9`, `benchmark-methodology §24`, `testing.md §23`) and the Gate 4 exit
check were designed for comparative multi-model benchmarking: 3 models × 3 roles, all six
model-role permutations, 5 repetitions per assignment, 30 fixed scheduled attempts. The project
currently runs one player model and is not doing comparative multi-model benchmarking, so that
acceptance design no longer matches the objective — and at the run 11 rate it would spend ~38 h
of provider time proving a claim nobody is making. T-060 has now passed end to end on the real
provider path (`RUNS.md` run 11: two completed games, held-constant condition, valid
provenance/export, `batch.json` integrity `ok`; run 10 exercised failure/recovery). That is
sufficient validation of the batch execution plumbing for the single-model scope.
**Decision:** (1) T-063 is **deferred, not completed**, because multi-model comparative
benchmarking is outside the current project scope. The 30-game acceptance batch is not run. The
step stays in `WBS.md` as written so it can be reopened. (2) The Gate 4 exit condition
(`/gate-check 4`) is deferred with it: it explicitly requires the 3-model / six-permutation /
30-attempt matrix and is neither satisfied by nor reinterpreted onto single-model runs. (3) T-060
remains recorded as what it is — the small marked batch exercising rotation, repetition,
provenance, outputs and provider execution. It is **not** relabelled as having met T-063's
acceptance criteria; the two purposes stay distinct in every record. (4) T-070 (batch resume)
was filed solely because T-063 cannot run without it; it is deferred alongside T-063 and reopens
with it, or on its own only if a single-model batch long enough to need resume is actually
planned. (5) When T-063 is reopened, the experimental design — models, rotation and repetition
count — is re-derived from the research question being asked at that time; 30 attempts is the
original design's number, not a standing requirement. (6) No deferred backlog item and no
Phase-2 work is promoted to fill the gap; the queue continues with current-scope work only.
`docs/` is unchanged: the Gate 4 definition and §24 describe the deferred milestone; this is a
scope decision, not a spec defect.
**Consequence:** The Benchmarking / Experimental Evaluation milestone is complete through T-060
and otherwise parked, with T-061 still optional. No single-model result may be presented as a
Gate 4 pass or as comparative evidence. Reopening T-063 needs a new `STATUS.md` entry and a task
brief that states the research question before it states the schedule size.

### D-075 — Canonical JSON refuses non-finite floats; untrusted boundaries reject by name, keys by shape, CSV cells reversibly
**Date:** 2026-09-19 · **Status:** accepted
**Context:** I-086 (2026-09-18 review). `canonical_json` used `json.dumps`'s default
`allow_nan=True`, so a sealed identity could be a hash over `NaN`/`Infinity` bytes no standard
reader parses, and a raw `NaN` in a `run-manifest` body reached the handler and was sealed (after
the fix it would have been a 500). The plan loader passed `yaml.safe_load` output straight to the
encoder, so an unquoted `authored_at: 2026-09-16` escaped as a raw `TypeError`, and nested plan
mappings accepted any extra key or type while the top level rejected undeclared keys (§4.2).
Credential keys were matched by exact spelling (`api_key` but not `openrouter_api_key`). CSV
tables passed model-authored strings through, so a leading `=`/`+`/`-`/`@` became a formula.
**Decision:** (1) `canonical_json(allow_nan=False)`; `canonical_sha256`/`canonical_json_size`
inherit. Hash-neutral for every valid document, proven by the pinned bytes in `test_contracts.py`
(unchanged), a pinned document covering every JSON scalar shape, and the shipped example plan's
`sha256` `45d09488…c632c` pinned before and after; no `dev/runs` identity moves. (2) A boundary
raises its own error: `amaw_contracts.first_non_json_scalar` names the path of the first
non-finite float or non-JSON type; `create_run_manifest` raises `ProvenanceError` (→ 400
`invalid_request`) before canonicalising, `load_analysis_plan` raises `AnalysisPlanError`
(`'authored_at' must be a quoted string`). Reject, do not coerce: a YAML date is never turned into
text the author did not write. Runner-built content (fingerprint, expected condition, topology) is
trusted and gets no translation. (3) Nested `analysis-plan-v1` mappings have explicit key sets
(`hypotheses[]`, `treatment`, `outcome_measures[]`, `design`, `failure_rules`,
`assurance_checks[]`; none has optional keys) and prose/enumeration fields are typed
(non-empty string / list of non-empty strings). This is v1 made precise, not v2 (I-090). No sealed
plan with extra nested keys is known (the only shipped plan is the example, used by the `t060`
batch); one would still classify `predeclared` by hash — only re-loading its file would now fail.
(4) `amaw_contracts.is_sensitive_key` refuses by shape (a `SENSITIVE_KEYS` member, suffix
`_token/_key(s)/_secret(s)/_password/_credential(s)`, or fragment
`authorization/password/passwd/secret/credential/bearer/api_key/apikey`), used by both
`app/provenance` and `benchmark/analysis_plan`. The false-positive proof over the real manifest
builder output, the example plan and the runtime fingerprint rejected the recommended `_tokens`
suffix — `max_tokens`, `input_tokens`, `output_tokens` are telemetry — so it is not a rule; `key`
alone is not a fragment (`key_points`). Values are not scanned here; `redact_secrets` owns values.
(5) `benchmark.outputs._cell` prefixes one `'` to a string cell whose first character is
`= + - @ \t \r` or `'`; readers strip exactly one leading `'` from every string cell and recover
the text losslessly. Typed numbers, booleans, empty cells and JSON-dumped structures (now
`allow_nan=False` too) are untouched; the `csv` writer's quoting is unchanged (quoting does not
stop evaluation). `export.json` stays the authority; no `EXPORT_FORMAT_VERSION` bump, no column
change, no `docs/` change.
**Consequence:** Six `TEST-I086-*` functions. A NaN anywhere in trusted runner content now
surfaces as a `ValueError` — a bug, as it should. A reader of the CSV tables must apply the
one-quote strip to string cells (documented in the `benchmark/outputs.py` docstring); a
hyphen-led `failure_reason` is therefore written `'-…`. Plans that relied on undeclared nested
keys or non-string prose must be corrected to the §4.2 vocabulary before they load.

### D-074 — The checkout is identified at its Git root or not at all; the runtime environment is a held-constant fingerprint
**Date:** 2026-09-19 · **Status:** accepted
**Context:** I-085 (2026-09-18 external review). `code_version(root)` ran `git ls-files` under
whatever path it was handed, so the same checkout could hash differently depending on the
caller's starting directory; and on *any* `OSError`/`SubprocessError` — including the 2 s Git
timeout under load — it silently fell through to `package:<version>`/`unknown-build`, so one
checkout could carry two `code_version` strings and the D-058/D-068 held-constant check would
report drift that was not there. Separately, the manifest recorded source identity strongly
(commit, prompt and scenario hashes) but runtime identity not at all: `pyproject.toml` gives
lower bounds, so the same commit can run under different Python/SQLite/Pydantic/PydanticAI
versions with no trace in `batch.json` or the export.
**Decision:** (1) Detection and identification are separate steps. `git rev-parse
--show-toplevel` from the supplied or default path *detects* the repository; only its failure
(no `git`, not a repository) may fall through to the package identity. Every Git command after
detection — `rev-parse HEAD`, `diff-index`, `ls-files`, the dirty digest — runs at the resolved
toplevel, and any failure there raises `CodeVersionError`: `amaw run` reports it as a failed run
(exit 1), `amaw benchmark` refuses before any game (exit 2), `resume()` refuses. The D-067
`dev/runs` exemption is therefore evaluated relative to the real root. `AMAW_CODE_VERSION`
still wins outright and performs no inspection. The reported string for a clean or dirty
checkout is unchanged (`amaw-source-tree-v1` untouched). (2) `runtime_fingerprint()` in
`agents/code_version.py` is the manifest's `environment` section — schema
`amaw-runtime-fingerprint-v1`: Python implementation/version/platform/machine, SQLite library
and module version, a *frozen* logical-key → distribution mapping for nine runtime
dependencies (pydantic, pydantic-core, sqlmodel, SQLAlchemy, fastapi, starlette, pydantic-ai,
httpx, PyYAML) read through `importlib.metadata`, the SHA-256 of `<toplevel>/uv.lock`, the
coordinator transport identity (`in-process-asgi-testclient-v1` — the only transport today; a
keyword on the builder and the coordinator, not a `run_configuration` claim) and the
`canonical_sha256` of the rest. Unavailable data is `None` (no lockfile, no repository, a
distribution not installed), never invented; the section carries no path, username, hostname,
environment value or time (testing.md §24). `environment` is a reserved runner-derived section
like `code_version`; exactly one path, `environment.sha256`, joins `COMMON_CONDITION_PATHS`, so
both benchmark modes hold the runtime constant and a mismatch is reported once while the full
object stays available to diagnose. `games.csv` gains `environment_sha256` beside
`code_version`. A legacy manifest without the section projects `None` and resumes on the
sections it has. Every run class records it — one manifest shape, no second golden path.
**Consequence:** A checkout Git can see is identified by Git or the run does not start; a
transient Git failure now costs a restart instead of a silently different identity. A batch
whose interpreter, SQLite, a listed dependency, lockfile or transport moves between attempts
stops at the drifting attempt like any other condition violation (D-058), which also means a
`uv sync` mid-batch is a violation. Renaming a key, adding or removing a dependency, or changing
which distribution supplies a field is a new fingerprint schema version, not a refactor.
`sqlite3.version` is deprecated and removed in Python 3.14; it is recorded while it exists and
becomes `None` after, moving the hash exactly as the interpreter version already does. This is
reproducibility metadata (methodology §8), not a determinism claim; provider/model routing stays
in `AgentRun.model_returned`/`provider`. Wheel packaging (I-087) remains out of scope: a non-Git
install fingerprints with `uv_lock_sha256 = None`.

### D-073 — Benchmark batch mechanics: the seed policy is bounded, not versioned; the completion rate keeps the planned denominator
**Date:** 2026-09-19 · **Status:** accepted
**Context:** I-084. `SEED_POLICY` (`base_seed + assignment_index * 1000 + repetition_index`,
methodology §8's example, recorded in every manifest and sealed plan since T-054) reuses seeds
across assignments once `repetition_index ≥ 1000`, and nothing bounded `--repetitions`.
`batch.json.completion_rate` divided by the attempts *executed*, so an integrity stop after one
good game reported `1.0` although D-068 records the rest as `not_run` precisely to keep the
planned denominator visible. `games.csv.turns_completed` reported `game.turn`, which
state-machine §22 has already advanced into the turn an interrupted game was in.
`load_attempts` and the `{position:03d}` directory prefix disagreed past position 999, and
`_write_json` rewrote `batch.json` in place after every attempt.
**Decision:** `build_schedule` and the CLI refuse `repetitions > 999`; the policy string, D-009
and every schedule inside the range are unchanged, so no existing manifest or sealed plan
changes identity. `batch.json` carries `planned_completion_rate` (completed ÷ planned attempts —
the §21/§24 meaning, which `completion_rate` now also has) and `execution_success_rate`
(completed ÷ executed, `None` before the first attempt). `turns_completed` counts the export's
`turn_completed` events; `turn_reached` keeps `game.turn` (§21 "turn reached").
`load_attempts` orders by the integer `position` in `attempt.json`; directory names are
unchanged. `_write_json` writes a sibling temp file, fsyncs, `os.replace`s and fsyncs the
directory where the platform allows.
**Consequence:** A batch beyond 999 repetitions per assignment needs a deliberately versioned
seed policy (a new `SEED_POLICY` string and a decision), not a silent widening. Readers of
`batch.json` written before this entry should treat its `completion_rate` as the execution
rate; from here the two are named. `games.csv.turns_completed` is now one less than
`turn_reached` for every interrupted game — the raw column TRD §32 promised. No `docs/` change:
`batch.json` and `games.csv` shapes are not specified there, and the §21/§24 wording already
names the planned denominator.

---

### D-072 — Execution provenance is invocation-owned: outcomes are recorded as they arrive, and an adjudication returns its own records
**Date:** 2026-09-19 · **Status:** accepted (supersedes the transport half of D-065)
**Context:** I-081. The coordinator persisted a sweep's `AgentRun` rows only after the whole
sweep had returned, so an unexpected exception anywhere in it — a sibling worker's defect,
`ActivationLimitExceeded`, a failed diplomacy settlement or admin call, even a `stuck`
judgement raised mid-loop — dropped every completed sibling's provenance while its Con or
Message stayed committed (agents.md §18 "Every failed activation is recorded"). On the Umpire
side D-065 named the capability but kept its transport: one app-level `AIUmpire` queued
attempt records and a `last_failure` on the instance, and `/advance` drained "whatever is
there"; `resolve_adjudication` deliberately calls the Umpire outside any write transaction, so
two advances on different games could interleave records and read each other's failure.
**Decision:** A per-game `_GameLedger` in `agents/autonomous.py` records each outcome the
moment it reaches the coordinator thread — before the next Actor is observed, before a
diplomacy round settles, and before a concurrent sweep raises its first error (every future is
still awaited; every completed sibling is recorded; a failed provenance write is raised as the
admin error it is, after the others are attempted). The drive loop judges `stuck` only after
the whole sweep is recorded. `app/umpire.py` gains a frozen `UmpireAdjudication` (`proposal |
None`, the tuple of `UmpireAttemptRecord`s that produced it, `failure`) and
`UmpireProvenanceSource` becomes `provenance_configuration()` + `adjudicate_recorded(context)`;
`drain_provenance` and the protocol's `last_failure` are gone. `AIUmpire` keeps every attempt
in a call-local collector, so the instance is re-entrant; `adjudicate` (the plain `Umpire`
contract, unchanged messages and cause chain) and `adjudicate_recorded` share one path.
`RecordedUmpire` — a single-use `Umpire` over a source, in `app/umpire.py` — hands
`resolve_adjudication` the plain contract and keeps the correlated result for the advance
route, which persists it with the same server-only context and the same overlay precedence
(the Umpire's stated `failure`, else the exception class). `AIUmpire.last_failure` /
`last_provenance` remain as inspection-only diagnostics of the most recent call; nothing
durable reads them. The `(game_id, activation_id)` uniqueness I-081 asked for landed in I-082.
**Consequence:** A partial game can no longer hold committed player state without its
`AgentRun`; a resumed run's provenance is complete up to the failure. Umpire records are
correlated to their adjudication by construction, not by timing. Every persisted row value is
unchanged. The typed failure codes I-081 listed as optional are not taken up:
`classify_failure`'s one wording test (`"admin "`) is unchanged. Internal seam only; no
`docs/` change.

### D-071 — Declared SQLite integrity rules are enforced, the database carries a schema stamp, and the export reports it
**Date:** 2026-09-19 · **Status:** accepted
**Context:** I-082. `app/models.py` declares every parent relationship but SQLite ignores
`FOREIGN KEY` clauses unless `PRAGMA foreign_keys=ON` is set per connection, and
`app/database.py` never set it — the database accepted orphan Actors, Effects and Cons. A trial
with the pragma on failed only fourteen model-level tests that seeded rows without parents;
every service, API, coordinator and benchmark path was already clean. `initialize_database`
had grown ~30 additive columns with no recorded identity, so a database's shape was only
inferable from which columns existed and could not be reported. `AgentRun` activation
uniqueness was a read-before-insert in a non-immediate transaction. The export's
`run_summary` and the activation endpoint's context were read in a second transaction after
the first had closed.
**Decision:** Every app engine enables the pragma on every `connect`. `SCHEMA_VERSION` (now 1)
is stamped into SQLite's native `PRAGMA user_version` by `initialize_database` after its
additive steps; a database stamped by newer code is refused, and a pre-stamp database is
checked with `PRAGMA foreign_key_check` and refused — not repaired — if it already holds
orphans. `agentrun(game_id, activation_id)` gains a unique index (NULL Umpire rows are
distinct; legacy tables gain it additively). The export gains a `database` block read live
from the connection (`schema_version`, `foreign_keys_enforced`) under
`export_format_version "11"`, and `api.md` 0.4.3 §15.1 lists it and states that the whole
record, `run_summary` included, is one transactional read. The activation endpoint reads the
delivery snapshot and the context in one `BEGIN IMMEDIATE` transaction. The run manifest is
unchanged: `code_version` identifies the code, and the stamp is a property of the database
file, so it belongs to the record read from that file.
**Consequence:** A pre-I-082 database with orphan rows no longer opens under `amaw serve`
until it is repaired by hand; none of the retained `dev/runs/` databases were written outside
the service paths, so none is expected to be affected. No Alembic: the stamp is the identity
Alembic would otherwise supply, and the additive steps stay in `initialize_database` until a
non-additive change makes a migration tool worth its weight (AGENTS.md §7).

### D-070 — Per-Actor Con participation events are never Actor-visible; the reveal is the only disclosure
**Date:** 2026-09-18 · **Status:** accepted
**Context:** I-080. `app/visibility.py` treated `con_submitted` and `con_passed` as public
lifecycle events, so `GET /games/{id}/events` handed every Actor each opponent's `actor_id`
and `created_at` while Cons were still being collected — under `sealed_cons`, where Cons are
never player-visible, and under `visible_reply`, where INV-CON-007 forbids conveying completion
order. `api.md §13.1` never listed which types are public; the code decided, and the reference
player never reads `/events`, so no run was affected — but the contract was violated by the
projection. `cons_revealed`, the one disclosure D-060 permits, was filtered out as unknown.
**Decision:** In the Actor-facing projection `con_submitted` and `con_passed` are never visible
to any Actor, the author included, under either reply policy. `cons_revealed` — frozen `con_ids`
in canonical `(actor_id, con_id)` order, emitted only under `visible_reply` when participation
closes — is visible to every current Actor, matching `current_argument.cons`.
`owner_reply_submitted` and `con_challengers_selected` remain filtered (fail closed; the reply
is already projected in context, the sealed challenger set is never disclosed). The
authoritative event log (state-machine §24), the admin export and the ordinal rule (hidden
events consume none) are unchanged. `api.md` 0.4.2 states the per-type policy in §13.1.
**Consequence:** Actor-visible events no longer reveal who challenged, who passed or who
answered first. A player-side consumer wanting to know that Cons closed reads
`phase_started(adjudication|reply)` or `cons_revealed`, as before. Diplomacy `actor_ready` stays
public: readiness is a phase fact the protocol discloses by design (§16), not a sealed
submission.

### D-069 — The Lean MVP supports exactly one primary Argument per Actor per turn and at least two Actors
**Date:** 2026-09-18 · **Status:** accepted
**Context:** I-079. `scenario-format.md §6` validated the Argument budget as `>= 1` and the TRD
(§7.5) left values above one "scenario-configurable, not heavily optimised". The engine budgets
slots but state-machine §20 has no step that returns the phase to the same Actor, so a
configured `2` silently played as `1` — the worst kind of support, since the manifest recorded
a condition the game did not run under. Separately `actors` was validated as non-empty, but a
one-Actor game has no eligible opponent and (before I-079) could never leave Cons.
**Decision:** The scenario loader refuses `primary_arguments_per_actor != 1` and fewer than two
Actors, by name, before any Game exists. The `Game` column, the slot budgeting
(`INV-ARG-003`) and its hand-seeded tests stay, so multi-slot sequencing can be built
deliberately later — with the §20 return step, its activations, prompts and tests — rather than
inferred. `make_argument` also closes Cons at once when the eligible set is already complete
(vacuously), so the engine never strands a game the loader would refuse. `scenario-format.md`
0.2.2 records both rules under §6 and §19. "Three or more Actors" (PRD, TRD §1) remains the
product and benchmark intent, not a validation rule: two is the mechanical minimum.
**Consequence:** A scenario that declares a budget the engine cannot honour fails at load with
the reason, instead of running under a misdescribed condition. Building multi-slot sequencing
is a planned step when a scenario needs it, not a backlog fix.

### D-068 — The batch declares its expected condition before game 1; pre-commitment is server-enforced
**Date:** 2026-09-18 · **Status:** accepted · amends the consequence of D-058
**Context:** I-078. D-058 sealed the `model`-mode held-constant set *from the first manifest*,
so a misconfigured first game defined the condition; nothing compared the scheduled seed and
Actor→model assignment with what the server recorded; `agent` mode verified no non-player
constant; the sealed plan was validated structurally but never reconciled with the schedule;
`verify_registration` ignored *when* the plan was sealed; a non-empty output directory admitted
stale attempts into the tables; and the server never proved a manifest preceded play —
`create_run_manifest` accepted any status and `start_game` never looked for one. T-063 claims
benchmark acceptance on this evidence, so it had to be made honest first.
**Decision:** (1) One pre-run expectation. Before any game the batch derives
`ExpectedBatchCondition` — the manifest configuration the runner will submit, built by the
runner's own builder (`agents.autonomous.expected_manifest_configuration`, shared with the
recording path so the two cannot compute differently), plus the loaded scenario's hashes and the
sealed Umpire condition — projected onto the condition paths, and records it with a `sha256` in
`batch.json`. Every manifest, the first included, is verified against it: the whole §5.1 set in
`model` mode, the common non-player subset (`COMMON_CONDITION_PATHS`: scenario, Umpire, game
loop, coordinator, code version, benchmark policies, run class, plan) in `agent` mode. Each
attempt is also verified against its `ExpectedAttempt` (seed, `player.assignments`,
`resume.player.assignments`, `benchmark.assignment*`, `repetition_index`), and a completed
attempt without a manifest is a `missing_manifest` failure. The D-058 stop is unchanged in
shape (attempt record, `batch.json.integrity`, `not_run` rows, exit 1); its kinds are now
`umpire_condition`, `expected_condition`, `expected_attempt`, `missing_manifest`, and
`held_constant_sealed_from` no longer exists. (2) `reconcile_plan` refuses, before any game, a
plan whose `repetitions_per_assignment` differs from the schedule, whose `planned_attempts` is
neither the batch (`experiment` scope, treatment levels = roster) nor one cell per treatment
level (`cell` scope, the shipped example's shape), or whose levels are not the roster when the
batch is the whole experiment. `seed_policy`/`rotation_policy` are prose in
`analysis-plan-v1` and are recorded beside the resolved policies, not compared. (3)
`verify_registration` classifies a plan sealed after the export's `game_started` event as
`post_hoc_plan` and reports `sealed_before_start`; an export without the event reports `None`.
This stays an assurance check. (4) A batch's output directory must be absent or empty. (5)
Server: manifest *creation* is lobby-only (an exact retry still returns the immutable record
after start; a different configuration is still a conflict), and `POST /admin/games` accepts an
immutable creation-time `require_run_manifest` that makes `start_game` refuse a Game without a
sealed manifest. The autonomous runner always sets it. The state-machine §14 start guard is
unchanged for every Game that does not opt in (engine tests, scripted harnesses).
**Consequence:** A batch whose first game is misconfigured now stops at attempt 0 instead of
sealing the misconfiguration. `agent` mode can no longer drift its scenario, Umpire, code or
game loop silently. Tables separate planned (`seed`, `model_requested`) from observed
(`seed_observed`, `model_assigned`, `condition_match`); raw exports stay the authority. A plan
that describes a different design than the schedule never reaches a provider. Any `amaw run`
game that fails to seal its manifest now fails at `start` with 409 rather than playing
unprovenanced. Reconciling the plan's policies by name needs a structured `analysis-plan-v2`;
not started here.

### D-067 — A batch's own output under `dev/runs` is evidence, not source
**Date:** 2026-09-18 · **Status:** accepted
**Context:** I-076. `code_version` (TRD §28; methodology §4 experiment record) digests every tracked
and non-ignored untracked file, exempting only the ad-hoc `amaw run` names under `dev/runs`
(I-044). The documented batch workflow writes `batch.json`, `games/<attempt>/attempt.json` and
the five CSV tables under `dev/runs/<batch>` and rewrites them after every attempt, so a `model`
batch's second manifest carried a different `code_version` than its first and the D-058
held-constant check stopped the batch on its own evidence (T-060 provider smoke). The launchd
helper's `launchd.plist` perturbs the run it launches the same way. Alternatives were to refuse
an in-repo `--output-dir` or to document "write outside the checkout and copy in"; both contradict
the shipped README/operations guide and the working agreement's `dev/runs` evidence location,
and neither covers the launcher or the resume check.
**Decision:** Extend the I-044 exception, unchanged in shape, with the batch runner's and the
launchd helper's own output names: `attempt.json`, `batch.json`, `agents.csv`, `arguments.csv`,
`games.csv`, `messages.csv`, `resource_access.csv`, `launchd.plist`. The three guards stay: below
`dev/runs`, ordinary regular file, not executable. The digest format (`amaw-source-tree-v1`) is
untouched, so every identity a clean or genuinely dirty checkout reported before is reported now.
**Consequence:** A run's evidence written where the docs say to write it no longer changes the
identity of the code that produced it. Anything else written into the worktree during a batch —
a `pytest --log-file`, an edited brief, a stray script in the batch directory — still dirties the
identity, deliberately. Adding a new evidence file name to any run workflow now means adding it
to `_GENERATED_RUN_EVIDENCE_NAMES` in the same change, or the D-058 check will report the drift.

### D-066 — Unexpected model behaviour is deliberately retried once; ceilings bound the logical activation
**Date:** 2026-09-18 · **Status:** accepted
**Context:** I-077. `_classify_activation_error` maps every PydanticAI `UnexpectedModelBehavior`
(output-validation exhaustion, unknown-tool calls, unusable tool output) to `FAIL_TRANSIENT`, while
its docstring claimed only "clearly transient" network/status failures earn the agents.md §18
retry. The same review found the retry re-used the full per-run `UsageLimits`, so one logical
activation could spend ≈2× the configured request/tool ceilings, and that a duplicate research
call was rejected only after it had reached the server.
**Decision:** Keep the mapping and document it: a model-behaviour failure is one bad sample, and one
retry on a fresh sample is cheap and bounded, so it is retried exactly once like a provider hiccup;
the retry is never blind — deterministic HTTP statuses and unrecognised exceptions still stop. The
per-activation ceilings bound the *logical* activation: the retry receives only the remainder of
the request/tool allowance the first attempt left, and an exhausted remainder is the ordinary
`guardrail` failure without a second run. Duplicate research is rejected before execution, with an
in-flight reservation so concurrent identical calls execute at most once.
**Consequence:** No behaviour change for the retry classification; `model_calls`/`tool_calls` in a
failure or result now never exceed the configured ceilings; a repeated search leaves one
`ResourceAccess` row and one `client_guardrail` invalid-call event, so methodology §17 "repeated
searches" is read from tool-call telemetry rather than the access log.

### D-065 — Umpire provenance is a separate, atomic capability Protocol; the Umpire emits normalised attempt records
**Date:** 2026-09-18 · **Status:** accepted
**Context:** `app/api.py::_admin_advance_game` drained raw provider records from the Umpire by
`getattr`, normalised OpenRouter/OpenAI usage keys (`prompt_tokens_details.cached_tokens`, …)
itself and hard-coded the Umpire's framework identity — the structural review's one concrete
AGENTS.md §6 rule 2 violation (F4, I-065). `provenance_configuration`, `drain_provenance` and
`last_failure` were duck-typed and on no Protocol, so the contract the server relied on was
invisible. `agents/umpire.py` already canonicalised usage before queueing, so `api.py` was a
second normaliser that only ever saw canonical keys.
**Decision:** `app/umpire.py` gains a second `runtime_checkable` Protocol,
`UmpireProvenanceSource` (`provenance_configuration()`, `drain_provenance()`, `last_failure`),
and a `TypedDict`, `UmpireAttemptRecord`, naming the normalised record the Umpire boundary emits
(identity, the five canonical token buckets, call/billing counts, framework and schema version,
outcome, provider metadata — no raw `usage`, no server context). `Umpire` is **not** widened; it
stays the pure adjudication contract. `AIUmpire` builds every record through one private
builder; `_usage_integer`/`_usage_cost` leave `app/`. The server adds only what it alone knows —
`actor_id=None`, `role`, `turn`, `phase` — and the overlay when its own resolution fails after a
provider call: `completed=False`, `failure_reason = umpire.last_failure or ExceptionName`, in
that precedence. It detects the capability with one `isinstance`; an Umpire without it records
nothing and cannot seal a manifest. The name `UmpireProvenance` was not used because the
`AIUmpire.last_provenance` dataclass already has it; the dataclass is unchanged.
**Consequence:** Provider knowledge lives in `agents/`; `app/` names the seam without importing
it. Every Umpire `AgentRun` value is unchanged (golden-row diff empty on success, outage,
unusable output, semantic retry and server-side refusal). One deliberate tightening: provenance
is now one atomic optional capability — a partial duck type offering only some of the three
members no longer counts as providing it. No supported implementation was partial; `FakeUmpire`
gained `last_failure = None`. Internal seam only; no `docs/` change.

---

### D-064 — Context-ineligible supersession is retryable unusable Umpire output
**Date:** 2026-09-18 · **Status:** accepted
**Context:** I-074. Swedish v6 game `071fc4d5-87ab-47d6-970d-e5e5d1168c92` stopped during
turn-1 adjudication when an otherwise parseable Umpire completion proposed superseding Effect
`b6575f81-819e-48ee-9484-2b2b33fa8a68`, which was not part of the game. The provider-facing
schema already enumerated only the active Effect ids in the server-built context, but the Alibaba
route returned an out-of-enum UUID with `finish_reason=stop`. The authoritative server correctly
rejected it atomically with `InvalidSupersession`; because D-063 classified representable
semantic failures as deterministic, the autonomous advance surfaced `422` and exited after that
single call instead of using the remaining bounded Umpire attempts.
**Decision:** After parsing each AI-Umpire completion and before returning it to the server,
validate both supersession lists against the active Effect ids in that same authorised context
and reject duplicates. A violation is unusable Umpire output, not a repairable proposal: retain
the failed call's provider/finish reason/generation id and a content-safe semantic failure code,
exclude its downstream provider cumulatively, and spend the next attempt inside D-063's existing
four-call bound. Retry the byte-identical messages, fixed model, prompt, schema and inference
settings; do not add corrective prompt text and never drop or rewrite an id. Exhaustion raises
`UmpireUnavailable`, so the server pauses with no RNG draw, adjudication or Effect. The database-
backed `_validate_supersession` remains unchanged and transactionally revalidates every proposal,
including non-AI Umpires and any state change between context capture and commit. Other semantic
validation errors remain deterministic unless separately classified by evidence.
**Consequence:** A provider that violates the advertised supersession enum no longer strands an
unattended run on `422`, while cross-game isolation and fail-closed adjudication are unchanged.
The retry policy changes only for this typed model-output violation and is identified by code
version/provenance; no Umpire prompt version changes.

---

### D-063 — The Umpire makes at most four bounded attempts per adjudication, rotating providers
**Date:** 2026-09-17 · **Status:** accepted
**Context:** I-071/I-072. The fixed Umpire condition is `deepseek/deepseek-v4-flash-0731` through
OpenRouter's default routing. Across the 2026-09-16/17 Swedish v6 launches, 11 of 53 Umpire calls
were unusable (reasoning consumed the whole output budget, `content: null`, schema misses, a
5xx) — a 10–20 % per-call rate. TRD §35 "retry once → pause/fail game" and D-061's "never add
another attempt" give two attempts, so a 30-adjudication game paused with probability 26–73 %.
Separately, `timeout` is a per-read socket timeout; OpenRouter keeps a non-streaming response
alive, so one runaway completion ran 689 s against a 300 s timeout — no finite wall-clock bound
existed (TRD §21).
**Decision:** The Umpire model, prompt, schema and inference settings stay fixed. Per adjudication
the Umpire makes at most **four** model calls in total. An unusable completion or a transient
provider failure consumes one attempt; after an unusable completion that names a downstream
provider, that provider is added to `provider.ignore` for every later attempt of the same
adjudication (D-061 made cumulative); a deterministic failure stops immediately. Every model call
has a **wall-clock deadline** (Umpire default 600 s) enforced while the body is read; exceeding it
is a transient failure. After the fourth attempt the game pauses exactly as before: no RNG draw,
no adjudication record, no Effect, no fabricated proposal. Both bounds are recorded in the Umpire's
provenance configuration. TRD §35 and state-machine §19 are amended in the same change; D-061's
"no additional attempt" clause is superseded by this bound.
**Consequence:** With the observed per-call rate, the chance a game pauses drops to 0.3–5 %, and a
runaway attempt costs at most `deadline` + one read timeout instead of an unbounded wait. Worst
case per adjudication is four deadlines (~40 min). Cost per adjudication may rise by up to three
extra calls only when the provider misbehaves. The retry policy is a fixed condition of the
benchmark and is part of every manifest.

### D-062 — Research gets its own finite budget; required decisions get bounded fresh retries
**Date:** 2026-09-17 · **Status:** accepted
**Context:** I-069. In a live Swedish v6 Argument activation, the player repeatedly searched and
read the same Resources until the 24-request ceiling, never called `decide`, and stopped the
coordinator because an Argument cannot be auto-fabricated. The contract requires finite
activations and legal recovery but does not divide the total tool budget between optional research
and the required decision or say whether a fresh activation may retry that decision.
**Decision:** Add a recorded `max_research_tool_calls` guardrail (default 4) inside the existing
request/tool ceilings. Disclose all three ceilings to the model, reserve one remaining request and
tool call for completion, withdraw all research tools when the research budget is spent, and close
the research surface after an exact successful-call repeat. Version the changed player prompts and
tool schemas. When a guardrail or exhausted transient provider attempt leaves a still-current
required `make_argument`/`reply_to_cons` opportunity, start a fresh activation while the existing
`max_activations_per_opportunity` bound has room. Record every failed activation. Deterministic
failures and exhausted bounds stop; no Argument or reply is ever synthesized.
**Consequence:** A normal activation has at most four research round-trips before it must decide,
and one looping activation no longer kills a run immediately. Research depth is now an explicit
agent condition in manifests/resume recipes. Runs can still stop after genuinely repeated or
deterministic failure; all safety limits remain finite.

### D-061 — An unusable Umpire completion retries the same model through a different provider
**Date:** 2026-09-17 · **Status:** accepted
**Context:** I-067. The Swedish v6 playtest's Umpire returned two HTTP-200, `finish_reason=stop`
completions from OpenInference with all completion tokens reported as reasoning tokens and no
usable structured answer. The existing exactly-one output retry repeated the same request and was
routed to OpenInference again. The contract fixes the Umpire model and retry count but does not
define downstream-provider selection for that retry.
**Decision:** Keep the exact model, prompt, schema, inference settings and total of two attempts.
When the first completed response cannot be parsed into a proposal and names a downstream provider,
add that provider to OpenRouter's `provider.ignore` only for the second attempt while preserving
`require_parameters` and any existing routing constraints. If no concrete downstream provider is
known, repeat the unchanged routing configuration. A second unusable response still pauses the
game; no model fallback and no fabricated adjudication are introduced.
**Consequence:** Playtests are less likely to lose a whole game to one endpoint's malformed
HTTP-200 response, while the model condition and bounded retry policy stay fixed. The actual
provider remains required provenance; strict fixed-provider benchmarks must pin their provider
condition separately rather than relying on default routing.

### D-060 — Visible rebuttal and bounded owner reply is an explicit, persisted Con-reply treatment
**Date:** 2026-09-16 · **Status:** accepted
**Context:** I-046. Professional Matrix practice uses a bounded `Argument → rebuttal → owner reply
→ adjudication` exchange; AMAW sends Cons only to the Umpire and gives the owner no response.
Adding the reply silently would change what every Actor learns and what the Umpire weighs, and
would invalidate comparison with released runs.
**Decision:** Keep `sealed_cons` (Cons reach only the Umpire; no reply) as the default, byte-
identical in context, tool schema, prompts and Umpire rendering. Add versioned
`con-reply-policy-v1` with mode `visible_reply` and a required `reply_max_points` (1–3, rejected
otherwise or when set under `sealed_cons`). Cons are still collected concurrently and sealed from
each other. When Cons close with at least one Con, the server freezes the set, emits
`cons_revealed`, marks the Argument `awaiting_reply`, and enters a new `reply` phase in which
only the Argument owner acts, once:
`POST …/arguments/{id}/reply` / tool `reply_to_cons` with 0–`reply_max_points` points, each
referencing a distinct frozen `con_id`; an empty list is the explicit pass. Acceptance persists one
`ArgumentReply` (unique per Argument — exactly-once), emits `owner_reply_submitted` and closes to
`adjudication` in the same transaction. A replay under the same idempotency key and payload
returns the recorded reply; any fresh attempt once the reply exists is `409 already_participated`.
Zero Cons skips `reply` entirely (no owner activation, no event). The revealed set is rendered in
the canonical `(actor_id, con_id)` order the Umpire already receives (D-045), never in completion
order, so provider latency cannot anchor the reveal. From reveal onward the frozen Cons (author and
text) and the reply are visible to every current Actor through `current_argument.cons/owner_reply`
and a per-turn `revealed_exchanges` projection; nothing is redacted or projected from hidden
state, and Resource/Effect/brief visibility is unchanged by anything a Con discloses. The Umpire
receives the reply (with `con_id` on each Con) under `visible_reply` only;
`relevant_hidden_information` is untouched. The Umpire prompt is a separate, explicit condition:
`sealed_cons` keeps released `umpire-v1.3` by default, `visible_reply` requires the reply-aware
`umpire-v1.4` (v1.3 is rejected for it), and a new `--umpire-prompt-version` /
`AMAW_UMPIRE_PROMPT_VERSION` setting lets the controlled `sealed_cons`/`visible_reply` comparison
run both treatments under v1.4 so the Umpire condition is held constant (INV-ADJ-004) — no sealed
game is silently upgraded. `visible_reply` selects `player-v1.8` (tool loop) / `player-v1.9`
(single shot), which state the visibility before any Con, and tool-schema identities
`player-tools-v5-reply`/`player-tools-v6-batched-reply`; the single-shot path gains a first-class
`ReplyDecision` (`points`, empty = pass) validated against `reply_max_points` and the revealed
`con_id`s before any mutation and executed through the same endpoint; `sealed_cons` keeps the
released prompts, schemas and decision types. `reply` is an optional `inference-profile-v1` phase
key; when absent it resolves deterministically from the profile's `argument` entry (a reply is
Argument reasoning, not generic Cons), and the resolved reply settings are recorded like every
other phase. Mode and limit resolve at creation (env/CLI/`POST /admin/games`) and are persisted on the Game,
in the manifest `game_loop.con_reply`, resume recipe, AgentRun (including activation phase
`reply`), export v10 (`argument_replies`) and the summary `con_reply_condition`; legacy rows stay
null.
**Consequence:** The two modes are different experimental conditions; `visible_reply` must be
evaluated against `sealed_cons` with the same scenario, players and single Umpire before any
default discussion, and never combined in one evaluation with I-039 reaction messaging, I-040
Consequence Management, selective activation or T-067–T-069 two-stage adjudication. Reply
timeouts and admin force-advance of `reply` stay deferred with Con timeouts (D-053); a stalled
owner surfaces as a stall, never as a silent pass. Anonymous or narrower-audience rebuttal is a
separate treatment, not a variant of this mode.

### D-059 — `--benchmark-mode` is mandatory; `agent` mode is one reference player, honestly labelled
**Date:** 2026-09-16 · **Status:** accepted
**Context:** T-057. WBS 5.5 / TRD §31 want every result set to carry its interpretation, and
methodology §5.2 forbids presenting an agent-configuration comparison as a model comparison. A
default or an inferred mode (from the roster size, `--rotate`, the scenario) would let the label
drift from what the batch actually controlled.
**Decision:** `amaw benchmark` requires `--benchmark-mode model|agent`; no default, no inference,
invalid values exit 2. The label is stored as `benchmark_mode` — distinct from the execution kind
`benchmark.mode = "batch"` — in `batch.json`, every game manifest's `benchmark` section,
`games.csv` and `agents.csv`, beside the distinct compared roster and a fixed interpretation
sentence (`benchmark/controls.py::INTERPRETATIONS`). `model` mode seals and verifies the §5.1
held-constant set (D-058) and refuses a rotated roster with repeated model names. `agent` mode
keeps the Lean architecture as it is — one reference player, one batch-level `PlayerConfig` with
only the model swapped per Seat — records that resolved configuration, and states that no other
agent component varied. Per-assignment prompts, frameworks, memory or tool sets are not added.
**Consequence:** Existing `amaw benchmark` invocations must add the flag. An `agent` batch from
this code is a labelled single-architecture run, not evidence about agent architectures; a later
task may extend `agent` mode with per-assignment configurations without changing the label's
meaning.

### D-058 — One sealed Umpire condition per batch; drift is an integrity failure that stops it
**Date:** 2026-09-16 · **Status:** accepted
**Context:** T-056. Methodology §9, agents.md §27 and INV-ADJ-004 make the Umpire configuration
part of the experimental condition, and §10 makes Umpire sensitivity a separately declared
condition. The T-054 runner recorded the Umpire but did not verify it, and a game whose Umpire
had changed would have been counted as ordinary data.
**Decision:** Before the first game, the batch seals `umpire_condition` — the same
`provenance_configuration()` the server records (model, provider, prompt version and hash,
temperature, max tokens, reasoning, timeout, routing, adjudication schema identity — the last
added to `AIUmpire` provenance for agents.md §27), the resume-recipe Umpire entry, and the
inference profile's `umpire` entry (D-055) — with a canonical-JSON `sha256`. This applies in both
benchmark modes. After every attempt the authoritative manifest (`run_manifest.umpire`,
`resume.umpire`, `resume.inference_profile.umpire`) is compared to the seal; a `model` batch also
seals the §5.1 held-constant set as the resolved values of its first manifest and holds every
later manifest to it (`HELD_CONSTANT_PATHS`; assignment-dependent fields excluded by
construction). Any difference is recorded on the attempt (`umpire_condition_match`,
`condition_differences`) and in `batch.json.integrity`, the batch stops, every still-scheduled
attempt is written as `not_run` so the planned denominator stays visible, and the command exits 1.
Per-attempt Umpire overrides in `run_configuration["benchmark"]` are refused before scheduling.
**Consequence:** Umpire sensitivity (§10) is run as separate batches with different seals, never
as drift inside one. A stopped batch is not a result set; its completed attempts remain evidence
and its `not_run` rows remain in the tables. The held-constant seal is taken from the first
manifest, so a batch whose *first* game is misconfigured seals the misconfiguration — the
recorded resolved values, not a label, are what a report must cite.

### D-057 — Cyclic seat-balanced rotation for unequal counts; T-054 assignment identity is frozen
**Date:** 2026-09-16 · **Status:** accepted
**Context:** T-055. Methodology §6 requires an explicit balancing algorithm when model and Actor
counts differ; D-056 had the runner refuse such rosters. Any change to the equal-count set would
move `assignment_index` and therefore every seed of a batch already run under T-054.
**Decision:** Three named policies, each with its own canonical order: `single` (one model for
every Seat, or N models positionally — one assignment), `permutations` (equal counts, all N!
in `itertools.permutations` order — exactly the T-054 `--rotate` set, same indices, same seeds,
same interleaving) and `cyclic` (`--rotation cyclic`): for M models and N Seats, assignment
`k = 0..M-1` gives `Actor[j] -> models[(k + j) % M]`, so exactly M assignments in which every
listed model occupies every Actor Seat exactly once. Nothing more is claimed: opponent
composition is not balanced, when M > N an assignment uses only N models, and when N > M a model
holds several Seats at once. No search or optimisation over assignments. `--rotate` keeps meaning
`permutations`; combining it with another `--rotation` is refused. `batch.json` records the
policy, its parameters, formula, guarantee and non-guarantee (`describe_rotation`), every game
manifest's `benchmark` section records `rotation_policy`, and `games.csv` gains
`rotation_policy` and the compact `roster` after the §25 prefix.
**Consequence:** A 3×3 `cyclic` batch is the three §6 run sets, a different (and smaller) design
than the six-permutation acceptance set — reports must name the policy. Seeds from any T-054
batch remain reproducible under this code.

### D-056 — Adjudication reliability experiment deferred; benchmark runner batch goes first
**Date:** 2026-09-16 · **Status:** accepted
**Context:** `WBS.md` sequenced the post-release adjudication experiment (T-067 → T-069) ahead of
the benchmarking milestone and made T-054 depend on T-069's adoption decision. The two-stage
Umpire is not being pursued now, so that dependency would block the whole benchmarking track on
work nobody is doing.
**Decision:** T-067–T-069 are deferred, unscheduled, and stay in `WBS.md` as written. T-054 now
depends on T-053 only. T-054, T-058 and T-059 are executed as one declared batch (`benchmark/`
module, `testing.md §22` acceptance boundary): runner + CLI, raw output tables, and failure
continuation. T-055 (rotation algorithm for unequal counts), T-056 (fixed-Umpire enforcement) and
T-057 (model/agent benchmark-mode label) are **not** absorbed; the batch records what it holds
constant and what it varies, and those steps add their enforcement later. `--rotate` in T-054
yields the full permutation set when model and Actor counts are equal — the behaviour TRD §30
assigns to the runner itself — and refuses unequal counts until T-055 defines the balancing rule.
**Consequence:** The benchmarking milestone runs under the released single Umpire (D-044/D-047).
No result from it may be read as evidence about two-stage adjudication. Reopening T-067 needs a
new `STATUS.md` entry, not this decision.

### D-055 — Per-phase inference budgets are an explicit, sealed profile with no presets
**Date:** 2026-09-16 · **Status:** accepted
**Context:** I-032. Diplomacy and Cons dominate provider calls (Swedish v6: 946/1,068), so a
cheaper effort in those phases is the obvious iteration lever; but v6 already ran everything at
`low`, the backlog requires budgets to be pre-committed after measurement, and a named
`fast`/`quality` preset would let the meaning of a recorded run drift with the code.
**Decision:** Add `inference-profile-v1`: an explicit YAML/JSON document naming optional
`reasoning_effort`/`max_tokens` per player phase (`diplomacy`, `argument`, `cons`) and for the
Umpire. Unset values inherit the flat `--player-*`/`--umpire-*` settings; no profile is the
released `uniform` behaviour and sends byte-identical requests. The player applies the
phase-resolved values as per-run model settings; the Umpire's entry resolves once at
construction. The manifest seals the *resolved* per-phase table and the recipe seals the
document; every player AgentRun records the values requested for its phase beside actual
`reasoning_tokens`. Invalid documents fail before any Game exists; resume reconstructs the
sealed profile and refuses the flag; mixed profiles are rejected. No built-in presets ship.
**Consequence:** A profile is an agent-configuration condition (and, via `umpire`, a new Umpire
condition, methodology §9) — never a model comparison and never behaviour-neutral. Equal
configured effort is not equal purchased attention across models, so reports cite actual usage.
Per-turn `attention_budget` (call allocation by phase) and per-Actor tiering stay separate.

### D-054 — A run is a playtest unless it sealed an analysis plan before execution
**Date:** 2026-09-16 · **Status:** accepted
**Context:** I-047. `benchmark-methodology §4` left the research question and hypothesis
optional, so a report could present analytical findings from runs that never declared what they
were testing, and any metric that happened to be recorded could be promoted to an outcome after
the fact. The benchmark runner (T-054+) does not exist yet, but per-game manifests (T-053) do.
**Decision:** Every manifest records `benchmark.run_class` — `playtest` (default, `amaw run`
unchanged) or `analytical`. An analytical run seals a validated `analysis-plan-v1` document
(question, hypotheses, unit, treatment/fixed conditions, primary/secondary outcome measures with
capture source, estimand, attempts/seed/rotation design, failure/exclusion/missing-data rules,
analysis method, assurance checks) — content and `sha256` — into the immutable manifest at game
creation via `--analysis-plan`. The manifest's creation time is the evidence of predeclaration;
the plan's `authored_at` is not. Undeclared metrics are telemetry. Reports classify each game as
`predeclared` / `post_hoc_plan` / `unplanned` (`amaw analysis-plan verify`) and separate raw
observations, verified findings and exploratory analyses. The plan module lives in `benchmark/`;
`app/` stores it as opaque configuration.
**Consequence:** Playtests stay free of ceremony but can never be upgraded into evidence; a plan
changed after results were seen shows up as `post_hoc_plan` rather than replacing the original.
Runs 1–8 are playtests. T-054/T-058 must carry `run_class` and the sealed plan through batches
and output tables; the T-063 acceptance batch stays an operational proof unless it seals a plan.

### D-053 — Sampled-sealed Con opposition is an explicit, server-sealed treatment
**Date:** 2026-09-16 · **Status:** accepted
**Context:** I-055. Swedish v6 paid all 224 `N-1` opponent participations for 125 Cons and 99
passes. A "first three Cons per Argument" cap would reward provider latency and response order,
and any scheme that activates all `N-1` opponents to pick a subset saves nothing.
**Decision:** Keep `open_cons` (every opponent eligible) as the default. Add versioned
`sampled_sealed` with a required `con_challengers_per_argument` K, `1 ≤ K ≤ N-1`, rejected at
creation otherwise (K = N-1 is permitted as a plumbing control). Selection happens inside the
`make_argument` transaction, before any opponent activation: a walk over the `turn_order` ring
from a durable cursor seeded at `seed mod N`, skipping the owner, taking K, advancing the cursor
past the last taken. The sealed set is persisted on the Argument, announced by
`con_challengers_selected`, and *is* the eligible set for Con, pass, `available_actions` and
closure; non-selected opponents get `403` and no activation. No adjudication draw is consumed,
so same-seed open and sampled runs share every server roll. The Umpire context is unchanged and
the condition is not disclosed to it (fixed-Umpire policy). Manifest, resume recipe, AgentRun,
export v9 and summary carry the condition; legacy rows stay null.
**Consequence:** Sampled and open opposition are different experimental conditions and the mode
is never to be presented as unrestricted Matrix opposition. Exposure balance is empirical: the
rotating walk keeps every opponent within two selections of every other over a game (simulated
for 3–8 Actors, all K, up to four turns); it is balanced per challenger, not per (owner,
challenger) pair. Con timeouts, admin force-advance, I-046 visible rebuttals and any
`bounded_visible` composition remain separate decisions.

### D-052 — Bounded Diplomacy is an explicit game-loop treatment
**Date:** 2026-09-15 · **Status:** accepted
**Context:** I-028 defines complete simultaneous Diplomacy rounds, while Swedish v6 spent 623 of
1,068 provider calls in open-ended Diplomacy. Silently imposing a limit would change negotiation
opportunities and invalidate comparisons with released runs.
**Decision:** Keep `open_diplomacy` as the default. Add versioned `bounded_rounds`, requiring a
positive per-turn limit. A round is complete only after its frozen eligible set has settled and
I-028 readiness/reactivation rules have run. On the final round, Messages and readiness mutations
stay committed, but the authoritative server closes to Argument without another recipient
activation. Failed rounds consume no slot; completed-round checkpoints and closure are durable and
idempotent. Manifests, resume recipes, Game snapshots, AgentRuns and export/summary identify the
condition and actual per-turn results.
**Consequence:** Bounded and open runs are different experimental conditions. Lower call, token or
wall-time cost is not evidence of behavioural equivalence; adoption requires a controlled study.

### D-051 — Research batching and history compaction are independent harness conditions
**Date:** 2026-09-15 · **Status:** accepted
**Context:** I-057 asks both to reduce dependent provider turns through batch research tools and to
remove obsolete intra-activation tool plumbing. Combining them would prevent attribution, while
dropping or summarising returned Resource content would change the information available to the
player in an uncontrolled way.
**Decision:** Keep the released `sequential-v1` + `full-v1` defaults. Add bounded `batched-v1`
search/read tools as a separate model-facing schema and independently selectable
`compact-success-v1` provider-history processing. The processor replaces only completed,
successful research call/return plumbing with a canonical retained-result message containing the
exact returned `data`; failed/retried calls and the authoritative local transcript stay intact.
Measure I-056 topology after processing and persist both condition names plus the resolved tool
schema in the manifest, resume recipe and AgentRun model parameters.
**Consequence:** This is an agent-configuration treatment, not a behavior-neutral optimization or
a new default. Search-to-read dependencies still require another model turn unless the Resource id
is already known. Any lossy summary, automatic research plan, changed budget or default requires a
separate decision and comparative run.

### D-050 — Research correlation is opaque and classification stays activation-level
**Date:** 2026-09-14 · **Status:** accepted
**Context:** I-045 requires Resource research to join to AgentRun after the activation finishes,
including failures and concurrent Actors, but the durable delivery receipt may span multiple
activation attempts and the existing trace cannot identify provider-call/tool ordering.
**Decision:** Generate a fresh UUID for each reference-player activation and carry it as
observational HTTP metadata only; persist it on authorised ResourceAccess rows and the eventual
player AgentRun. Join by game, Actor and UUID. Normalize repeat queries exactly as search does
(`strip().casefold()`); a search converts when a read occurs before the activation's next search;
Resource versions are immutable content hashes; “continued” means the research activation has
more than one provider call. Legacy/missing correlations remain explicitly unattributed.
**Consequence:** The report answers I-045 mechanically without making correlation authoritative,
claiming semantic query equivalence, or inventing provider-call sequencing. Custom clients may
omit the header and old rows remain valid; those accesses are visible but not activation-attributed.

### D-049 — Outgoing Messages have a recorded per-activation behavioral budget
**Date:** 2026-09-13 · **Status:** accepted
**Context:** I-027 measured 51 Messages during only the first eight-Actor diplomacy sweep. The
existing ten-per-phase Message ceiling and general tool-call ceiling are emergency safeguards,
not a normal negotiation envelope. I-022 already requires successful sends to remain counted
when a later part of an activation fails, and I-028 runs Actor activations concurrently.
**Decision:** Add `max_messages_per_activation` (default `3`, minimum `0`) to the shared player
guardrails. It counts successful unique `send_message` mutations across the complete logical
activation, including its one internal model retry. Rejected sends do not spend it; stable
idempotent replays do not spend twice. It is per Actor, is protected by that player's existing
send lock, and is mechanically enforced in both activation modes. The existing per-phase and
tool-call limits remain separate. `0` disables sends only. Record the value in manifests and
AgentRun guardrail provenance; expose it through environment and CLI configuration. Issue
immutable neutral prompts `player-v1.6` (tool loop) and `player-v1.7` (single shot) with selective
diplomacy guidance; prior prompts remain unchanged.
**Consequence:** Runs with different Message budgets or prompt versions are distinguishable and
must not be compared as one model-benchmark condition. This is orchestration behavior, not a new
server game rule or persistence model. No global lock is introduced; concurrent Actors retain
independent counters.

### D-048 — Incremental context uses a durable pending receipt and one replace-in-place memory
**Date:** 2026-09-13 · **Status:** accepted
**Context:** I-034 moves the reference player from a repeated bounded slice of the complete
Message/Note archive to inbound Messages since its prior activation and one structured rolling
memory. The existing contracts define Message/Note privacy and failure recovery, but not a
restart-safe acknowledgement boundary, concurrent Message ordering, or replacement memory.
**Decision:** Add the benchmark condition `incremental-context-v1` + `strategic-memory-v1`.
The server stores one Actor/game state with an acknowledged internal event boundary, at most one
opaque pending-delivery receipt/snapshot, and the current eight-field structured memory. Beginning
an activation creates or replays that pending visible inbound batch; successful completion alone
acknowledges it, atomically replacing memory and advancing through the snapshot. Failure/stall
does not acknowledge; Messages committed after the snapshot remain for the next batch. Exact ack
replay is idempotent and conflicting replay is refused. Older history stays in `read_messages`;
legacy Notes remain compatible but are omitted from the new decision packet. Issue immutable
`player-v1.4` (`tool_loop`) and `player-v1.5` (`single_shot`), and record context/memory versions
in manifests, AgentRun rows and export v3. `docs/agents.md` → 0.2.5, `docs/api.md` → 0.2.2,
`docs/benchmark-methodology.md` → 0.3.1.
**Consequence:** This is intentionally an agent-design/benchmark condition, not a neutral token
optimization. Model comparisons must hold it constant; comparisons with earlier full-archive
runs are agent-configuration comparisons. One small server-backed state record and two
Seat-authenticated endpoints are added, but no persistent chat history, game phase, reaction
activation or event-driven queue is introduced.

### D-047 — Gate 1 is authority *and causation*; the governing design test is stated in the spec
**Date:** 2026-09-13 · **Status:** accepted
**Context:** External review of the D-044 formulation. "Authority first" fits voluntary
commitments but not outcomes an Actor can order without guaranteeing (a mobilisation ordered is
not a mobilisation achieved); "derived coalition status" is correct for the Swedish scenario but
must not read as a first-class AMAW concept; and the RNG rule deserved a sharper statement.
**Decision:** `docs/agents.md §23` now states the governing test — *mechanically protect player
agency and logical prerequisites, while leaving strategy, causal arguments, plausibility and
unexpected outcomes open to the players and the Umpire* — and gate 1 becomes "authority and
causation: what the Action can establish": the Desired Effect must be causally supportable by the
Action together with established state before probability is applied. Gate 3 is "deterministic
rules and derived state" (a facilitator's synthesis of what authoritative Effects imply; what is
derivable is scenario content). The RNG rule reads: the draw may decide whether a plausible,
attempted causal process succeeds; it does not create missing decisions, agreements, facts or
procedural prerequisites (§23, echoed in §26). The TRD gains the same design test in §3.3, the
precise naming "an autonomous, AI-umpired Pros-and-Cons Matrix Game variant with server-owned
randomness" in §1, and the §22 cross-reference now says authority-and-causation-first.
`agents.md` → 0.2.4, TRD → 0.5.3.
**Consequence:** Wording only — no engine or test change; the ordered gates, narrowing and
branch-specific supersession of D-044 are unchanged. The immutable `umpire-v1.3` prompt still
says "authority-first" (agents.md §31 forbids editing it in place); if the causation wording
should reach the Umpire, that is `umpire-v1.4`, a separate experimental condition, and is not
issued here. No `MATRIX_GAME_METHOD.md`/`RULEBOOK.md` exists in this repository; the principle
lives in the normative contracts instead.

### D-046 — Single-shot structured decisions are a recorded activation mode; the tool loop stays
**Date:** 2026-09-13 · **Status:** accepted
**Context:** I-031. The tool-loop harness pays one provider round-trip per mutating tool call
(I-027: 216 activations → 624 model calls; Cons 82 % of input tokens). A phase decision is small
and mostly structural — Messages/Note/ready, one Argument, Cons-or-pass — but the model sometimes
needs information first (`search_resources`/`read_resource`), and the `docs/agents.md §5` tool
list reads as the model-facing surface. PydanticAI refuses a per-run `output_type` alongside
agent-level output validators.
**Decision:** `PlayerConfig.activation_mode ∈ {tool_loop, single_shot}`, default `single_shot`.
In single-shot the model sees the three read tools plus one `decide` output tool whose schema is
chosen from the server's `available_actions` (`make_argument` → Argument; `pass_con` → Cons;
`ready` → Diplomacy — never a phase literal). Validation runs inside the output function
(`ModelRetry` with the tool loop's exact `guardrail_error` shape; Cons require `make_con` to be
listed, the server keeps the per-Actor limit); execution runs after the model returns, in the
fixed order messages → note → completion, through the unchanged `PlayerTools` HTTP client, with
every server rejection recorded (`invalid_tool_calls`) rather than fed back — an unfinished
opportunity re-activates under the existing stall bound. `cons: []` passes; a budget-exhausting
Con is not followed by `pass_con` (D-043). `ready=false` keeps diplomacy open. Each mode has its
own immutable prompt (`player-v1.2` / `player-v1.3`, the latter adding only a decision-format
section); `TOOL_SCHEMA_VERSION` → `player-tools-v2`; the mode is recorded in the run manifest
(`player.activation_mode`, one mode per game) and every `AgentRun`. `docs/agents.md §5` is
amended to say the eleven tools are the HTTP client surface and a harness may present mutations as
one structured decision. Guardrail checks move to `agents/guardrails.py` (re-exported).
**Consequence:** Simple phases cost one model call; research costs exactly the calls it needs.
Runs in the two modes are distinct experimental conditions — a model benchmark (T-057) holds
`activation_mode` constant; only an agent-design comparison varies it. The tool-loop suite keeps
passing (tests pin `tool_loop`); the live smoke runs under both modes. The `agents/player.py`
`PLAYER_PROMPT_VERSION` name now means the tool-loop version.

### D-045 — Con activations run concurrently; Cons are Umpire-only, so scheduling is not behaviour
**Date:** 2026-09-13 · **Status:** accepted
**Context:** I-029. At eight Actors the serial Cons sweep (seven opponents, one after another,
per Argument) dominated wall time (I-027: 504 of 624 model calls). The backlog asked first
whether Cons are visible to other Actors, because concurrency would then remove an exposure.
Verified: `GET /context` omits Cons, no player-facing Cons read exists, `con_submitted` carries
only ids, and the player exposes no events tool — every opponent already judges the Argument
independently. Separately, the Umpire received Cons in commit order, which concurrency would make
latency-dependent.
**Decision:** The coordinator steps the Cons sweep on a bounded pool (`cons_concurrency`, default
4; `1` is the serial sweep), recording outcomes in sweep order from the coordinator thread. A
serial sweep stops when an Actor observes a phase other than the cycle's, so a phase that opens
mid-sweep is re-dispatched its own way and no Con activation slips onto the coordinator thread.
Diplomacy stays serial (I-016). The server presents Cons to the Umpire ordered by Actor id in
every mode. The value is recorded in the run manifest (`coordinator.cons_concurrency`). The
"identical Cons count once" Umpire-prompt guard proposed in the backlog is **not** adopted: it was
conditioned on a visibility change that does not exist, and it would alter adjudication in both
scheduling modes.
**Consequence:** Wall time falls roughly with the pool size in Cons; nothing an agent sees or
decides changes, and server draws are identical for the same seed. The Umpire's Con order is now
Actor-id order rather than sweep order in serial runs too — a deterministic ordering, recorded
here so pre- and post-D-045 adjudication inputs are not assumed byte-identical.

### D-044 — Authority and prerequisites precede probability; supersession is branch-specific
**Date:** 2026-09-13 · **Status:** accepted
**Context:** I-036/I-037. An election-run Umpire correctly noticed that one Actor could not bind
the others or establish a shared budget, assigned the combined Desired Effect probability 0.30,
then let a low server roll establish the whole package and supersede the unresolved-budget
blocker. `agents.md §23` explicitly allowed overreach to be “heavily discounted,” so the failure
was permitted by the contract. The proposal also carried one unconditional supersession list,
which the server applied on either probabilistic branch.
**Decision:** Adjudication is ordered: (1) Actor authority/action scope, (2) authoritative
prerequisites, (3) deterministic rules/derivations, (4) probability only for residual causal
uncertainty. Probability never waives a failed earlier gate. The Umpire must narrow mixed or
overbroad requests to Effects the Action can establish; an Actor's feasible self-binding act is
normally automatic, repeated in both probabilistic branches when an external consequence remains
uncertain. Supersession becomes branch-specific and is permitted only when that selected branch's
Action or newly established evidence actually resolves the old condition. Issue immutable
`umpire-v1.3`; scenario v4 reinforces the generic rule but does not own it.
**Consequence:** v1.2 and v1.3 runs are separate experimental conditions. This is strong semantic
policy but not a mechanical free-text proof: server-enforced typed commitments/derivations remain
I-038 because Lean MVP excludes authority graphs and formal Claims. Reaction diplomacy and
bounded Consequences remain I-039/I-040 rather than being smuggled into this repair.

### D-043 — Exhausting a Con budget completes participation without claiming a pass
**Date:** 2026-09-13 · **Status:** accepted
**Context:** State-machine §18 says an Actor submits up to its configured Con limit and then
passes/completes. Once its final slot is consumed, no further Con is legal and `pass_con` is the
only remaining state transition, so requiring another model decision cannot affect strategy.
**Decision:** A successful `make_con` that consumes the final slot atomically persists the same
unique `ConParticipation` completion record used by `pass_con` and closes Cons when it completes
the eligible set. It emits `con_submitted` but not `con_passed`, because no pass was chosen.
`pass_con` remains required when the Actor has remaining budget or the configured limit is zero.
**Consequence:** Completion is restart-safe and behavior-neutral while eliminating a redundant
server action. The HTTP Con response and tool schema stay unchanged; clients learn the resulting
legality from their next authoritative context as before.

### D-042 — Shipped operational guides live outside the normative `docs/` tree
**Date:** 2026-09-13 · **Status:** accepted
**Context:** T-064 must turn the draft user documentation into shipped guidance while `docs/` is
the read-only normative specification layer. Keeping operational prose beside contracts would make
its authority ambiguous, and the draft developer guide duplicates the working agreement.
**Decision:** Keep the root `README.md` as the quick start and place non-normative user guidance in
`guide/`. Every guide links to the controlling contract; the developer page delegates to
`AGENTS.md` and `dev/` rather than restating the workflow. The existing
`docs/amaw-operational-docs-draft/` remains historical draft material until release packaging can
archive it without pretending it is shipped documentation.
**Consequence:** `docs/` remains an unambiguous specification layer, shipped guidance has a stable
home, and documentation acceptance checks only `README.md` plus `guide/`. T-066 may archive the
superseded draft directory as release housekeeping.

### D-041 — The §21 knowledge scenario is a test fixture, not a shipped scenario
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-051 needs a scenario carrying all six testing.md §21 elements (two per-Actor
private Resources, a CSV, a map/image attachment, brief-absent facts). `scenarios/` holds the
runnable packages (`northern-strait` is the live smoke scenario) and the golden engine fixture
`tests/fixtures/deterministic-game/` must not be edited to add a second private Resource or an
image.
**Decision:** Author the knowledge scenario as a sibling test fixture,
`tests/fixtures/knowledge-scenario/`, owned by `tests/test_scenario_knowledge.py`. It is not
golden and is never driven to a deterministic golden output; the golden fixture is untouched.
**Consequence:** The §21 acceptance scenario lives beside its test, not in `scenarios/`. If a
benchmark ever needs a shipped knowledge scenario, promote a copy to `scenarios/` rather than
repurposing this fixture (documented in `tests/fixtures/README.md`).

### D-040 — `amaw run` is the minimal local autonomous entrypoint
**Date:** 2026-09-12 · **Status:** accepted
**Context:** I-035 found that a user could export a game but had no clean product path to choose
a scenario and run one. The existing autonomous coordinator already composes the admin API and
the reference players, while adding a scenario registry or UI would be out of proportion to the
MVP.
**Decision:** Add one `amaw run SCENARIO` command outside `app/`. It accepts a scenario-package
directory or the name of a bundled source-checkout scenario, loads the normal environment settings,
uses the configured Player and Umpire models (with one-off CLI overrides), composes the existing
FastAPI surface in-process, and delegates all game work to `AutonomousGameRunner`. The command
creates an ephemeral in-memory admin token when `AMAW_ADMIN_TOKEN` is unset, requires the optional
agent dependencies and `OPENROUTER_API_KEY`, and prints only the completed game ID. `amaw export`
remains the inspection path; no scenario registry, UI, or second runner is added.
**Consequence:** A local user can start a complete autonomous game with one command and inspect it
with the existing export command. The command is intentionally local/in-process; deployments that
need a separately hosted API continue to use the documented admin API or Python coordinator path.

### D-039 — AgentRun rows are append-only executions; summaries are derived
**Date:** 2026-09-12 · **Status:** accepted
**Context:** TRD §28 describes per-Actor `AgentRun` totals, while the requested post-game report
needs exact role/phase call counts and timing. A single mutable Actor aggregate cannot attribute
separate activations, Umpire retries or failures without losing evidence.
**Decision:** Persist one append-only `AgentRun` row per player activation, failed/stalled player
opportunity, or Umpire provider completion/failure. `model_calls` records the actual provider
requests represented by that row (a tool-using activation or transport retry may contain several);
`costed_model_calls` says how many are covered by returned billing data. Derive Actor/game totals,
phase counts, tokens, latency and cost from these rows; derive wall-clock phase/runtime from the
existing event timestamps. Persist provider cost only when reported, and label a sum partial when
any represented call lacks cost. The one-per-game manifest is canonical-JSON hashed, includes the
Game id and seed, rejects credential-shaped fields, and accepts only byte-equivalent retries.
**Consequence:** Raw call evidence remains auditable and two games are distinguishable even under
identical experimental settings. Reports avoid false cost precision. The schema has two small
provenance tables rather than a mutable aggregate plus a separate call-detail table.

---

### D-038 — The Resource-access log records only authorised operations
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-050 logs `search_resources`/`read_resource` as a server side effect (TRD §29,
agents.md §10). Two questions the specs leave open: does a denied `read_resource` (a hidden/unknown
`404`, INV-RES-004) create a log row, and what about a rejected blank-query search?
**Decision:** Log only *authorised, performed* operations. A search always logs — even a
zero-result one, since the query is the datum (benchmark-methodology §17 "repeated searches") — but
a blank-query `422` is a malformed request, not a search, so it logs nothing. A denied read logs
nothing. The TRD §29 schema has no success/authorised field and explicitly declines "elaborate
behavioural classification"; recording a denied read under an `operation="read"` whose semantics
imply a *successful* read would corrupt the §17 "unique Resources read" and "search-to-read
conversion" metrics. The logged `query` is the raw string the Actor sent (faithful record), not the
normalised matching form. `created_at` is a real timestamp, so the append-only log is kept out of
the deterministic golden comparison (as timestamps/agent errors already are).
**Consequence:** The log is a clean record of what each Actor actually accessed, directly
consumable by the §17 metrics. It does *not* capture failed read attempts (agents probing hidden
IDs); if that behavioural signal is later wanted it needs a richer schema than TRD §29 and its own
decision. Denied reads remain observable through the event log / request logs if needed.

---

### D-037 — T-049 wires the resource tools with no new player prompt version
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-049 adds `search_resources`/`read_resource` to the player toolset and to the
server-computed `available_actions`. agents.md §17 says a player prompt should "describe available
tool categories"; §31 versions prompt files. The question is whether wiring the tools requires a
`player-v1.3`.
**Decision:** No new prompt version. The current `player-v1.2` already describes this tool
category conditionally ("search and read scenario information available to you"; "If you need more
information and the relevant tool is available, search for it.") and adds no scenario tactic, so
the versioned artefact is unchanged. `available_actions` (now including the two names while the
game is running) is what actually gates use; the prompt was written forward-looking for exactly
this step.
**Consequence:** Provenance stays on `player-v1.2` across the T-042/T-044 runs and T-049, so
smoke-run comparisons are not perturbed by a prompt bump. A future wording change to encourage
research more strongly would be a `player-v1.3` on its own merits.

---

### D-036 — Actor Resource search and read are available only while running
**Date:** 2026-09-12 · **Status:** accepted
**Context:** The Resource API contract does not state whether lobby, paused or completed games
accept Actor search/read requests. This is externally visible and must be consistent with T-049's
future server-computed Resource actions.
**Decision:** Both Actor-facing Resource endpoints require `GameStatus.running`. Lobby, paused and
complete games return the same deterministic `409 wrong_phase` envelope independently of Resource
visibility. The policy is checked before any Resource candidate lookup.
**Consequence:** Resource access is available during every running phase but not before start,
while paused or after completion; T-049 can advertise both actions under the identical status rule.

### D-035 — Required structured Resources preserve original validated text
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-046 must provide a deterministic readable/searchable representation for textual
formats, but the contracts do not require canonical reserialization.
**Decision:** Decode required textual files as strict UTF-8, validate YAML/JSON/CSV structure, and
persist the original decoded text. Inline content uses its already-defined UTF-8 text. Optional
PDF, PNG/JPEG and GeoJSON remain opaque attachments. Dispatch is by source kind and lower-cased
file extension, never by open `Resource.type` or metadata.
**Consequence:** Actor-readable text corresponds exactly to the immutable source-byte snapshot;
formatting remains provenance-visible, while semantic normalisation/search indexing stays out of
T-046.

### D-034 — Resource materialisation has conservative byte and text limits
**Date:** 2026-09-12 · **Status:** accepted
**Context:** D-033 deferred size policy until format handling. Unbounded package bytes and decoded
text create avoidable memory/context denial-of-service risk; the contracts specify no threshold.
**Decision:** Reject any Resource source over 2 MiB and any readable representation over 1,000,000
Unicode code points, with a clear scenario-validation error before game creation. Search result,
snippet and API response limits remain T-047/T-048 concerns.
**Consequence:** Scenario authors receive deterministic bounded-input failures. Exact accepted
bytes remain snapshotted and hashed under T-045's rules.

### D-033 — Resource byte/format limits are deferred to T-046
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-045 must snapshot exact Resource bytes for later handling, while T-046 owns format
parsing and the readable-vs-opaque boundary. The contracts permit warnings for very large text but
define no externally visible limit.
**Decision:** T-045 applies no new content-size limit and snapshots exact source bytes. T-046 must
choose and document any byte/text limits together with format handling; introducing one here would
create externally visible rejection behaviour without a normative threshold.
**Consequence:** Valid packages are not rejected by an invented size cap in T-045. Resource package
size remains an operational responsibility until T-046 defines any bounded representation.

### D-032 — Resource manifest uses canonical JSON over stable snapshot fields
**Date:** 2026-09-12 · **Status:** accepted
**Context:** TEST-SCN-005 requires a deterministic Resource-manifest hash suitable for benchmark
provenance. Database IDs, timestamps and host paths would make identical packages hash differently.
**Decision:** Sort Resources by scenario Resource ID, encode a list of stable entries as compact
UTF-8 JSON with sorted object keys, and SHA-256 those bytes. Each entry contains `id`, `name`, open
`type`, `visibility`, sorted `visible_to`, sorted `tags`, canonical free-form `metadata`,
`source_kind`, safe basename (`attachment_name`) for file-backed content, and
`content_sha256`. It excludes absolute paths, timestamps, database IDs and runtime state.
**Consequence:** Declaration order cannot perturb the manifest hash; changing relevant declaration
metadata or source bytes does. The canonicalisation helper is directly unit-tested.

### D-031 — Inline Resource provenance hashes declared UTF-8 content
**Date:** 2026-09-12 · **Status:** accepted
**Context:** TRD §12 explicitly requires hashes for referenced files; inline Resource content has
no separate file but needs comparable provenance and a durable snapshot.
**Decision:** After YAML parsing produces the declared `content` string, encode that value as UTF-8.
Those bytes are both the immutable snapshot and the input to SHA-256. The exact `scenario.yaml`
bytes separately cover YAML spelling/folding differences.
**Consequence:** Equal declared strings have equal inline hashes across platforms, while any byte
change to the manifest remains visible through `scenario_content_sha256`.

### D-030 — Resources are immutable game-scoped snapshots with contained package paths
**Date:** 2026-09-12 · **Status:** accepted
**Context:** Runtime reads from scenario package files would allow a completed game's knowledge and
provenance to drift after creation. File declarations also cross a trust boundary: absolute paths,
`..` traversal and escaping symlinks could read outside the package.
**Decision:** Load each Resource once at the package boundary and persist it under the composite
identity `(game_id, resource_id)` in the same transaction as game creation. File paths must be
relative, contain no `..` component, resolve to a regular file, and remain within the resolved
scenario-package root even through symlinks. Persist only exact bytes and a safe basename for
file-backed attachments, never an absolute/package root path.
**Consequence:** A game no longer depends on mutable source files after creation; identical Resource
IDs may exist in separate games but collide within one. Unsafe package references fail before any
game row is created.

### D-029 — Umpire receives every Actor's hidden information, tagged by owner
**Date:** 2026-09-12 · **Status:** accepted
**Context:** I-018. `agents.md §21` names `relevant_hidden_information` as a server-built Umpire
input but the server sends `[]`, so private facts cannot bound any Actor's claims. Adjudicated
Effects are public, so exposure needs a no-leak rule and a test.
**Decision:** The server passes every Actor's `private_information` and every active private
Effect, each tagged with `kind` and `actor_id` — as a human matrix-game umpire knows everything.
Symmetric: no Actor is bounded by its facts only when it happens to be arguing, and a Con is
weighed against its author's facts too. The Umpire prompt forbids restating hidden facts in
Effects or rationale; the smoke leak check covers Effect descriptions and rationales.
**Consequence:** The Umpire input grows by a few lines per Actor per adjudication;
`_build_umpire_context` is the single place the policy lives. Arguing-Actor-only and keep-empty
were considered and rejected (asymmetric handicap; inert information design). The smoke leak
check is verbatim-only; a paraphrased restatement is not caught in the MVP.

### D-028 — Umpire prompt v1.2: contradictions supersede, failures cost, anchors kept
**Date:** 2026-09-12 · **Status:** accepted
**Context:** I-019. In the first provider-backed run a failed Argument produced a new Effect that
sat beside the Effect it contradicted, and failure Effects were materially softer than success
Effects. Supersession is adjudicator-chosen (`agents.md §22`) and server-validated; the prompt
only asked for supersession when a new Effect "clearly replaces" one.
**Decision:** Issue `docs/prompts/umpire-v1.2.md` once, carrying the D-029 no-leak rule as well:
an Effect that contradicts an active Effect must supersede it; failure Effects describe the real
cost of the failed action; the `agents.md §25` anchors stay and the whole range is to be used.
v1.1 is retained unchanged. Probability calibration is judged on ≥3 provider-backed runs of
v1.2 (Part C), not on one.
**Consequence:** Umpire prompt version is a fixed-Umpire benchmark field
(`benchmark-methodology §9`); runs on v1.1 and v1.2 are not directly comparable.

### D-027 — Player prompt v1.2: private information is for use, Cons are strategic, own words
**Date:** 2026-09-12 · **Status:** accepted
**Context:** The first provider-backed T-044 run (I-017) showed three prompt-level gaps in
`player-v1.1`: no Actor used its private information, brief detail or private Effects anywhere;
one Actor conned an Argument whose success served its own objectives; and a later-activated
Actor reproduced an earlier Actor's public statement near-verbatim.
**Decision:** Issue `docs/prompts/player-v1.2.md`. It adds: private information, brief and private
Effects are decision inputs to be used, not merely known; a Con is a strategic instrument read by
the Umpire, raised when an Argument's success would harm the Actor's objectives and withheld when
it would help; write positions in the Actor's own words. `player-v1.1.md` is retained unchanged
(`agents.md §31`). No strategy, stance or scenario tactic is prescribed (`agents.md §17`).
**Consequence:** Prompt version is an experiment-relevant field; runs on v1.1 and v1.2 are not
directly comparable. `PLAYER_PROMPT_VERSION` moves to `player-v1.2`.

### D-026 — Actor event IDs are view-scoped cursors, not database IDs
**Date:** 2026-09-12 · **Status:** accepted (supersedes D-014 cursor/default details)
**Context:** I-008 found that filtering hidden Event rows while returning their database-global
IDs still reveals hidden and cross-game activity as gaps. This contradicts TEST-VIS-003 even when
the newest hidden Event does not advance `last_event_id`.
**Decision:** On Actor routes, Event `id` and `after` are stable one-based ordinals over Events
visible to that `(game_id, Actor)`. Hidden/other-game rows consume no ordinal; cursors are scoped
to that Actor and game. Administrative export retains authoritative database IDs. Event types are
explicitly classified and unknown or broken references fail closed.
**Consequence:** Actor clients must treat IDs as opaque per-view cursors and cannot correlate them
with admin exports or another Actor's stream. `docs/api.md` is patch-bumped to 0.2.1 and
`docs/testing.md` to 0.3.1.

### D-025 — The `amaw` CLI lives outside `app/` as a top-level module
**Date:** 2026-09-12 · **Status:** accepted (extends D-005)
**Context:** T-043 finally adds the `amaw` console script (D-005 deferred it until a real command
existed — `amaw export`). Today `amaw export` is framework-free, but T-054 adds `amaw benchmark`,
which drives real agents. If the CLI lived in `app/`, that later command would pull an agent
framework into the engine package, breaking the `app/` import boundary (AGENTS.md §6 / TRD §4).
**Decision:** The CLI is a single top-level module `amaw_cli.py` (`[project.scripts] amaw =
"amaw_cli:app"`, `py-modules = ["amaw_cli"]`), **outside** `app/` and the other engine packages.
`amaw export` calls the same `app.export.build_game_export` the HTTP endpoint uses (one builder).
A future agent-driven command must import `agents`/`pydantic_ai` **lazily inside the command** so a
plain `import amaw_cli` stays framework-free — asserted by an extended import-boundary test.
**Consequence:** One more top-level module (not a package — TRD §5 "don't pre-split"). The boundary
now covers `app.export` *and* `amaw_cli`.

### D-024 — Admin `…/advance` resolves-then-advances with an injected Umpire; no `resolve` route
**Date:** 2026-09-12 · **Status:** accepted
**Context:** `api.md §14` lists `…/advance` but no `resolve` route, yet a game at the ADJUDICATION
boundary needs the server to resolve the current Argument (server-owned RNG) before it can advance.
The Umpire must stay injectable and no provider may be hard-wired into `app/` (TRD §4).
**Decision:** `POST /admin/games/{id}/advance` is phase-aware: a read-only
`game_service.adjudication_pending` check decides whether the current Argument is still unresolved;
if so the route calls the unchanged `resolve_adjudication` with an Umpire injected via
`create_app(umpire=…)` (typed as the framework-neutral `app.umpire.Umpire`), then calls the
unchanged `advance_after_resolution`. No second copy of a transition; the §14 surface stays exactly
as documented (no extra route). With no Umpire configured, an advance that needs to resolve returns
`503 adjudicator_unavailable` — the server never fabricates a result (TRD §35).
**Consequence:** The composition root (runner/CLI/tests) supplies the concrete `FakeUmpire`/
`AIUmpire`. `pause`/`end` remain unexposed (D-022).

### D-023 — Agent-error persistence is deferred to T-053; the export ships `agent_errors: []`
**Date:** 2026-09-12 · **Status:** accepted
**Context:** `api.md §15.1`/`TRD §27` list "agent errors" in the export. But `AgentFailure` (T-041)
lives only in memory on `agents.lifecycle.LifecycleRunner.failures`; there is no agent-error table,
and `game_service` deliberately deferred an `agent_error` record. Fabricating errors would violate
"faithful, not fabricated".
**Decision:** T-043 does **not** add agent-error persistence. The export includes `agent_errors` as
a present-but-empty list (`[]`) — faithful to what the store holds (nothing). Durable persistence of
agent failures (a small append-only record/event) is T-053, alongside run/provenance metadata.
**Consequence:** The export shape is stable; T-053 fills `agent_errors` without a schema change to
the export's other sections.

### D-022 — `pause`/`end` admin routes are deferred (no such service transition exists)
**Date:** 2026-09-12 · **Status:** accepted
**Context:** `api.md §14` lists `POST …/pause` and `…/end`. But there is no `pause_game`/`end_game`
transition in `game_service` today — only the T-042 server pause-on-outage (D-021). Adding admin
pause/end now means defining new state-machine legality (from which phases? what happens to the
active Actor / current Argument?), which is under-specified and beyond this task's acceptance
boundary. The T-044/T-054 runners need only create/start/advance/export.
**Decision:** T-043 implements `POST /admin/games`, `…/start`, `…/advance` and `GET …/export`
(all behind `require_admin`). `…/pause` and `…/end` are **not** registered; they return the
documented `404 not_found` envelope until a task defines their transitions. `GameStatus.paused`/
`complete` already exist for when that work lands.
**Consequence:** A future task adds the two transitions + routes with their state-machine contract.
No half-built pause/end route ships in the meantime.

### D-021 — Server pause-on-Umpire-outage was a state-machine defect; the doc is fixed
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-042 must, on an Umpire outage, "pause or fail the game — never fabricate a result"
(WBS T-042; TRD §35 "Umpire timeout → retry once → pause/fail game. Do not invent an
adjudication."). But `state-machine.md §14`'s transition table listed `pause` **only** as an
admin trigger (`any running state | pause | admin`); there was no server-initiated pause when the
adjudicator is unavailable. Code cannot rely on a transition the normative doc does not define.
**Decision:** A genuine spec defect. Filed here and fixed in `state-machine.md` (bumped 0.3 →
0.3.1) in its own commit before relying on it: added the row
`| adjudication | pause on outage | server | Umpire unavailable after retry | paused status |`
and a §19 paragraph — the server pauses (`status = paused`, `adjudication` phase retained) with no
draw, record or Effect, surfaced as `503 adjudicator_unavailable`; resume is an admin action.
**Consequence:** `resolve_adjudication` catching `UmpireUnavailable` and pausing is now
doc-backed. The change is additive (a defect fix, not a design shift), so the prior version was
not archived — only the patch bump + version note record it.

### D-020 — The AI Umpire is built on the T-038 `OpenRouterClient` (httpx), not PydanticAI
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-042 permits either a PydanticAI `Agent` or the T-038 client (agents.md §22 fixes
only the *contract*, TRD §22 only that the server owns the roll). The Umpire needs strict,
server-owned validation and precise "malformed → server refuses / unusable → pause" control, plus
the one-transient-retry rule and §19 provenance.
**Decision:** Build `AIUmpire` on `agents.model_client.OpenRouterClient`. Its `complete()` already
implements **exactly one** transient retry and none on deterministic failures (`_STATUS_CATEGORY`)
— so the Umpire path reuses that single mechanism (no second one, consistent with D-018) — and its
`ModelResult` already carries model-requested/returned, provider, usage and latency for
`UmpireProvenance`. The AI Umpire parses the model JSON *leniently*: representable-but-invalid
values (probability out of range, empty rationale, a well-formed but unknown superseded id) pass
through so the server's `_validate_proposal`/`_validate_supersession` remain the authoritative
gate; only output that cannot become a proposal (not JSON, unknown mode, non-numeric probability,
non-list Effects, malformed id) or a post-retry model failure raises `UmpireUnavailable`.
**Consequence:** `agents/umpire.py` — and therefore the `app` resolution path — imports no agent
framework (TRD §4), proven by an extended boundary check. PydanticAI stays the reference *player*'s
dependency only. If a future Umpire needs tool use, revisit; the protocol boundary is unchanged.

### D-019 — Recovery cannot un-hang a stalled *active arguer*; it records and yields (never fabricates)
**Date:** 2026-09-12 · **Status:** accepted
**Context:** T-041 must guarantee "one player cannot hang the game" (agents.md §18), but
`_advance_sequencing` gives **every** Actor an ARGUMENT phase each turn, the only exit from
ARGUMENT is a submitted Argument, and the lean MVP has **no server force-advance** (STATUS.md).
Recovery must never fabricate an Argument (agents.md §2 / INV-ADJ-003).
**Decision:** Stall/failure recovery takes the *legal minimal completion* from the server's
current `available_actions` — `ready` in diplomacy, `pass_con` in Cons (the two collective
phases where one Actor's inaction deadlocks a phase that needs everyone). A persistently-failing
active arguer has **no** legal non-fabricating completion; the runner records the failure
(`AgentFailure`, `recovery_action=None`) and **yields**, and the single-Actor `run()` loop
terminates on that unrecoverable opportunity rather than re-observing it forever.
**Consequence:** The paradigmatic "one player hangs the game" (never readies / never passes) is
fully recovered and proven (`test_deliberately_failing_agent_cannot_hang_the_game`). Server-side
force-advance of a stalled arguer is out of lean-MVP scope; until then a wholly non-arguing model
blocks only its own Argument sub-phase, recorded not faked. Revisit if a force-advance is added.

### D-018 — The reference player's "retry once" wraps `activate()`, reusing T-038's classification
**Date:** 2026-09-12 · **Status:** accepted
**Context:** agents.md §18 / TRD §35 require exactly one retry on a *transient* provider failure
and none on a deterministic one. The reference player (T-039) runs through PydanticAI's own model
loop, not T-038's `OpenRouterClient` where the existing single-retry lives — so the rule had to
be applied to the PydanticAI path without adding a second retry mechanism.
**Decision:** `ReferencePlayer.activate()` re-runs the activation **at most once**, and only when
`_classify_activation_error` returns transient. That classifier reuses T-038's
`ProviderErrorCategory` / `_STATUS_CATEGORY` table (shared meaning of "transient": timeouts,
connection drops, 429/5xx) — no second classification. `UsageLimitExceeded` (a per-activation
ceiling) and deterministic auth/validation/config failures are **never** retried; each becomes a
structured `ActivationFailed` handed to the runner's recovery policy. The OpenRouterClient's own
one-retry is unchanged and independent (it is not on the reference-player path).
**Consequence:** One retry, one place, one classification. A transient blip self-heals; a
deterministic failure fails fast into recovery. If a player is ever driven through
`OpenRouterClient` directly, retries do not stack — the two paths are mutually exclusive.

### D-017 — PydanticAI is a test dependency; the `app/` import-boundary test runs in a subprocess
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-039's reference player (`agents/player.py`) is a real PydanticAI `Agent`. To
exercise it (and the T-040 lifecycle) in the default suite — offline, key-free (testing.md §3) —
the framework must be *importable* when the tests run, but PydanticAI is only in the optional
`agents` extra, which CI's `uv sync --locked` does not install. Separately, the existing
`app/`-boundary tests asserted `"pydantic_ai" not in sys.modules` **session-globally**, which
becomes a false failure the moment any test imports the reference player.
**Decision:** Add `pydantic-ai` to the `dev` dependency-group (test tooling), keeping it **out**
of core `[project.dependencies]` so `app/` stays installable without an agent framework. Harden
the three import-boundary assertions (`test_api_app`, `test_umpire`, `test_agent_tools`) to be
order-independent: `app/` boundaries import the package in a **clean subinterpreter** and assert
`pydantic_ai` did not load; the tool-layer check asserts on the module's own AST imports. The
default suite drives the player with an offline PydanticAI **fake model** (`FunctionModel`), so
no key is ever needed.
**Consequence:** `app/` remains framework-independent and is proven so regardless of test order;
the reference player is genuinely exercised by CI. The `agents` extra still exists for a runtime
install; nothing in `app/` imports PydanticAI.

### D-016 — The T-038 model client is built on `httpx`, not PydanticAI
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-038 (WBS) points at "OpenRouter model client" and agents.md §19. TRD §6 names
PydanticAI (reference player) and OpenRouter (model gateway) as distinct layers; PydanticAI is
an optional `agents` extra and is not installed by default. T-038 needs precise control of the
single-retry rule and requested-vs-returned provenance, and the whole default suite must stay
installable and runnable with no key (testing.md §3).
**Decision:** `agents/model_client.py` calls OpenRouter's OpenAI-compatible `/chat/completions`
directly over `httpx` (a core dependency), exposing `ModelConfig`/`ModelResult`/
`ModelClientError`. PydanticAI stays the reference *player*'s dependency (T-039); this transport
imports it nowhere. The boundary is preserved: `player → model_client → OpenRouter`, with `app/`
untouched.
**Consequence:** T-039 consumes this client (or points PydanticAI's OpenAI-compatible provider
at OpenRouter). The one-retry/provenance behaviour is unit-tested offline via an
`httpx.MockTransport`; no `agents` extra is needed to run or test T-038.

### D-015 — GET `/messages` accepts `after` but the authoritative cursor is `/events`
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-030. api.md §7.1 lists an optional `after=<event_id>` query parameter on GET
`/messages`, but a `Message` carries no monotonic integer cursor (its ordering key is
`created_at`), while §13.1 gives `/events` a real `last_event_id` cursor and positions polling
there.
**Decision:** GET `/messages` declares `after` (so the documented parameter is honoured and
never 422s) but returns all currently-visible Messages regardless of its value. Event-id
cursoring belongs to `/events` (T-035), which is the authoritative polling cursor; a client
that wants "new since" polls events and re-reads messages.
**Consequence:** `after` on `/messages` is a no-op today. If message-level pagination is ever
needed it must define its own cursor rather than overloading the event id; this is the entry to
revisit.

### D-014 — Event-visibility policy for the Actor-facing event stream
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-035. api.md §13.1 requires that GET `/events` "must not become a side channel for
hidden information" and that Actor-visible events either carry only public/authorised payloads
or be filtered/redacted, but does not enumerate which event types are sensitive.
**Decision:** `app.visibility.can_view_event` classifies the §24 event types: the lifecycle/
state events (`game_started`, `phase_started`, `actor_ready`, `argument_submitted`,
`con_submitted`, `con_passed`, `argument_adjudicated`, `turn_completed`, `game_completed`) carry
only public game facts and are visible to all; a `message_sent` event inherits its Message's
visibility (public → all; private → sender + listed recipients, read from the event's
`actor_id`/`payload.recipients`); and `effect_created`/`effect_deactivated` inherit the
referenced Effect's visibility via `can_view_effect`. Unauthorised events are **excluded**, never
returned redacted, so no payload, count or id leaks. The endpoint's `last_event_id` advances over
visible events only, so a hidden newest event never forces its id to be disclosed.
**Consequence:** Event visibility now depends on Message/Effect rows, so it lives in the one
visibility service (AGENTS.md §6.4), not the router. The policy is conservative: any new
sensitive event type defaults to public unless added to `can_view_event`, so new event types
that carry hidden information must be classified here when introduced.

### D-013 — `available_actions` omits Resource actions until their endpoints exist
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-029. api.md §6.1's example `available_actions` includes `search_resources` and
`read_resource`, and requires the server to "expose only legal `available_actions`"
(API-INV-009). The Resource endpoints (§10) are not implemented until T-045+.
**Decision:** The context read-model omits `search_resources`/`read_resource` from
`available_actions` until those endpoints exist. Advertising an action whose endpoint would
return `404` contradicts "expose only legal available_actions"; the list reflects what the
server can actually fulfil right now (`send_message`, `make_argument`, `make_con`, `pass_con`,
`ready`, `get_notes`, `save_note`, as applicable to the Actor and phase).
**Consequence:** Reference players built before T-045 will not see Resource tools in the
context. When the Resource endpoints land, add the two action names back in the same phase logic;
this is the entry to revisit.

### D-012 — Cross-game Seat token is `403 forbidden`, not `404 not_found`
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-028. A valid Seat token for game A used on a `/games/{B}/…` path must be
refused. api.md §3.1 offers both `403 forbidden` ("Authenticated Actor lacks access") and the
guidance to "prefer `404 not_found` over confirming that a hidden resource exists"
(API-INV-010).
**Decision:** Return `403 forbidden` for a cross-game token. The caller is authenticated; it
simply lacks access to that game. `404` is reserved for visibility-sensitive *resource*
existence, applied by the real endpoints (T-029+), not for the auth boundary.
**Consequence:** Whether game B exists is not revealed by the auth layer either way (the
response is identical for a real and an absent B). If a future requirement needs the game's
existence itself hidden at this layer, this is the entry to revisit.

### D-011 — Superseding an already-inactive Effect is rejected as ineligible
**Date:** 2026-09-11 · **Status:** accepted
**Context:** T-020 must "validate every proposed `supersedes_effect_id` … is eligible to be
superseded under the contract." state-machine §19.4/INV-EFFECT-003/004 define supersession as
*deactivating an active Effect* and retaining it in history, but do not state what happens when
the Umpire proposes to supersede an Effect that is already inactive (already expired or already
superseded). testing.md tests cross-game (TEST-ADJ-006) and the deactivation result
(TEST-EFF-005) but not this case.
**Decision:** `app.game_service._validate_supersession` rejects a target that is not currently
`active` with `InvalidSupersession`, alongside unknown, cross-game and duplicate ids. Superseding
an already-inactive Effect is a no-op that can only mask stale Umpire state, and "eligible to be
superseded" most naturally means "currently active." Not a spec defect — §19 is silent on the
case, not wrong.
**Consequence:** An Umpire that references a stale Effect id fails the whole adjudication
atomically (no record, no Effects, no RNG consumed) rather than silently doing nothing. If a
future scenario needs idempotent re-supersession it must revisit this.

### D-010 — Effect expiry boundary: `expires_turn = N` expires at the start of turn N
**Date:** 2026-09-11 · **Status:** accepted
**Context:** state-machine §15.5 fixes *when* Effect expiry is evaluated ("at the start of a
turn … expires Effects whose expiry condition is reached") but not the exact comparison against
`Effect.expires_turn`. testing.md TEST-EFF-004 says only "becomes inactive at expected turn
boundary". The task (T-013) owns turn-start expiry and required a defined, non-inferred boundary.
**Decision:** An Effect with `expires_turn = N` expires **at the start of turn N**: during
turn-start bookkeeping `active` is set `False` when `expires_turn is not None and expires_turn
<= turn`. It is therefore active for turns `[created_turn, N-1]`. Only currently-active Effects
are considered; expiry **deactivates**, never deletes (INV-EFFECT-004); Effects with
`expires_turn is None` or `> turn` are untouched. Not a spec defect — §15 is silent on the
comparison, not wrong.
**Consequence:** A starting Effect (created_turn 0) with `expires_turn: 1` is already inactive
during turn 1. T-020 (Effect application/supersession) and T-021 (turn advancement) must reuse
this same `<= turn` boundary in `app.game_service._expire_effects`; a later turn boundary is
not re-tested until then.

### D-009 — Optional `game.seed` for deterministic fixtures; seed otherwise supplied at creation
**Date:** 2026-09-11 · **Status:** accepted
**Context:** `docs/testing.md` §4 requires the canonical fixture to carry a *fixed RNG seed*,
but `docs/scenario-format.md` §6 enumerates no `seed` field in the `game:` config. TRD §7.1
gives `Game.seed: int`, and the benchmark runner assigns a seed per game (T-054).
**Decision:** Add an optional `game.seed: int` to the manifest schema for deterministic
fixtures. `create_game_from_scenario(engine, scenario, *, seed=...)` uses the argument when
given, else `scenario.game.seed`; a game with neither is rejected (`GameCreationError`).
Not filed as a spec defect: scenario-format §6 is not wrong, it simply omits an optional
deterministic-test convenience; the benchmark path (T-054) remains the primary seed source.
**Consequence:** The canonical fixture pins `seed: 424242`. Benchmark seeds override the
manifest seed per game, keeping per-run provenance authoritative (T-053/T-054).

### D-008 — Correct persistence and benchmark contracts before T-007
**Date:** 2026-09-10 · **Status:** accepted
**Context:** The pre-T-007 MVP review found that scenario-local Actor IDs could not coexist
across games, required transition/adjudication state had no durable representation, and the
30-game cyclic benchmark left avoidable opponent-role imbalance and failure-selection risk.
**Decision:** Persist Actors per game; add only the configuration, readiness, Con-completion,
adjudication and RNG-progress state required by existing contracts; use six permutations ×
five fixed attempts for the MVP benchmark; retain every attempt and blind/harden adjudicators.
**Consequence:** The domain schema grows by two small records and several scalar/configuration
fields before T-007. No workflow engine, general participation abstraction or statistical
framework is introduced.

### D-007 — Actor's `metadata` field is stored as attribute `actor_metadata`
**Date:** 2026-09-10 · **Status:** accepted
**Context:** TRD §7.2 names the Actor's optional scenario metadata field `metadata: dict`.
`metadata` is a reserved attribute on the SQLAlchemy Declarative API (it is the `MetaData`
registry), so a SQLModel `table=True` column named `metadata` raises
`InvalidRequestError: Attribute name 'metadata' is reserved`.
**Decision:** The domain attribute and DB column are named `actor_metadata` (same JSON
shape, same "no core semantics in MVP" intent). Not filed as a spec defect: the TRD text is
correct as a data contract; this is an ORM-mechanics rename local to `app/models.py`. When
scenario loading lands (T-009) it maps scenario-YAML `metadata` → `actor_metadata`.
**Consequence:** Code and tests reference `actor_metadata`; the external field name in
scenario YAML stays `metadata`, mapped at the loader boundary.

### D-006 — `pydantic-settings` is a core dependency for configuration
**Date:** 2026-09-10 · **Status:** accepted
**Context:** T-002 needs an environment-driven settings object that also loads the `.env`
file `.env.example` tells operators to create. TRD §6 lists "Pydantic v2" but not its
settings companion, which was split into a separate package in Pydantic v2.
**Decision:** Add `pydantic-settings` to the core dependency set (not the `agents` extra).
It is the idiomatic Pydantic-v2 way to type-parse env vars and read `.env`, and it stays
inside the `app/` boundary (no agent framework). Config lives in one module `app/config.py`
(configuration + safe logging together — TRD §5 "do not split prematurely").
**Consequence:** One extra runtime dependency. `app/config.py` is a module not named in the
TRD §5 illustrative layout; that layout is explicitly illustrative and the addition is a
real, non-speculative core need.

### D-005 — The `amaw` console script is deferred until it has a real command
**Date:** 2026-09-10 · **Status:** accepted
**Context:** AGENTS.md §7 documents `uv run amaw --help`, but T-001's WBS "Done when" is only
`uv sync` + `import app`, and the same task says "Do not create empty speculative modules."
A Typer entry point with zero commands is exactly that scaffolding.
**Decision:** T-001 ships `pyproject.toml` and the package skeleton without a `[project.scripts]`
`amaw` entry. The console script is added when its first real subcommand exists —
`amaw export` (T-043) or `amaw benchmark` (T-054).
**Consequence:** `uv run amaw --help` does not work until then; `python -m` / direct calls are
used in the interim. No behaviour is lost, since no CLI command exists yet to invoke.

### D-003 — Specification defects are filed, not silently corrected
**Date:** 2026-09-10 · **Status:** accepted
**Context:** Setting up the process surfaced a real conflict in `docs/`: `README.md`
references `prompts/player-v1.1.md`, but v1.1 sits at `docs/player-v1.1.md` while the
superseded v1 remains in `docs/prompts/`. The superseded text omits the mandatory `pass_con`
behaviour, so an implementer following the directory would wire up the wrong prompt.
**Decision:** Filed as **I-001** rather than corrected in place. `docs/` is normative and
predates implementation; changes to it are a deliberate act with their own commit, not a
side effect of tidying.
**Consequence:** T-039 (PydanticAI reference player) is blocked until I-001 resolves which file is
the authoritative player prompt.

### D-004 — `WBS.md` is the plan and the progress record; `BACKLOG.md` is an issue tracker
**Date:** 2026-09-10 · **Status:** accepted · supersedes part of D-002
**Context:** The first cut duplicated all 66 planned steps into `BACKLOG.md`, creating two
files holding the same list and a guaranteed drift point.
**Decision:** Planned steps (`T-0NN`) live only in `WBS.md` and are closed in place there,
beside the contract they were built against. `BACKLOG.md` holds only work discovered along
the way (`I-0NN`) — bugs, errors, spec defects, deferred follow-ups — and starts empty.
**Consequence:** "What is next" is read from `WBS.md`, not the backlog. The two ID prefixes
must never mix, or the distinction collapses again.

### D-002 — The design of this process lives in its own artefacts
**Date:** 2026-09-10 · **Status:** accepted
**Context:** The brainstorming workflow normally ends by writing a separate design document
to `docs/superpowers/specs/`. Here the deliverable *is* documentation.
**Decision:** No separate spec document for the development process. `AGENTS.md` is the
design; `dev/WBS.md` is the plan. `docs/` stays reserved for the product specification.
**Consequence:** Changing the way of working means editing `AGENTS.md`, not a spec file.

### D-001 — `AGENTS.md` is canonical; `CLAUDE.md` imports it
**Date:** 2026-09-10 · **Status:** accepted
**Context:** Two agent-instruction conventions, and a third file — `docs/agents.md` — that is
the normative player-agent contract and unrelated to either.
**Decision:** The working agreement lives in `AGENTS.md` and is tool-neutral. `CLAUDE.md`
imports it with `@AGENTS.md` and adds only Claude-Code-specific detail (slash commands,
session start). Rules are never duplicated across the two.
**Consequence:** Any tool that reads only `CLAUDE.md` must follow the import. The naming
collision with `docs/agents.md` is called out in both files.
