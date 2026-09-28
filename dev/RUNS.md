# AMAW — Provider-backed runs and what they taught us

The behavioural record. `BACKLOG.md` holds defects; task briefs close with their task; this file
keeps what we learn about **how the agents and the Umpire actually play**, run by run, so it
survives the process and feeds the benchmark design (T-053+).

Where the exports are (D-081): a new run is written to `runs/` in this repository; at its
close-out it is published to the separate **`amaw-runs`** repository and pruned here. This
repository currently retains seventeen directories — run 6 (`runs/2026-09-12-db54f331/`, the canonical
complete game), the I-027 report, the T-060 batch smokes (runs 10–14), the two I-100
comparison treatment blocks, the two `skaro-crisis` runs (17 and 18), the first `ai-2027` run (19), the first `swedish-government` 7.0.0 run (22) and the three I-160 comparison games (23). An entry whose
heading cites `amaw-runs/<dir>/` has its export there; the entry text is the record here.
Runs staged for `amaw-runs` but not yet pushed sit in `~/Projects/AMAW-runs-archive/<dir>/`.

- One entry per provider-backed run, newest last. Game id, configuration (`<dir>/run.json`),
  outcome, then observations — what the Actors did, what the Umpire rewarded, what surprised us.
- **Patterns** (top) is revised as runs accumulate. Cite run entries; don't restate them.
- Runs 1 and 2 predate export-on-disk and are reconstructed from logs and memory; treat with care.
- Never build a golden expected output from any of these (fixture README, benchmark-methodology §8).

---

## Patterns across runs (revised 2026-09-26, after run 19)

**Objectives, not prompts, decide whether there is a game.** On the fixture (compatible
objectives — runs 1–2) all three Actors converged on an identical "neutral, rotating,
equal-footing forum" by turn 3 and then decorated it. On `northern-strait` (triangular zero-sum
objectives — runs 3–5) there were three distinct positions from turn 1, private side deals, and
deadlock. Same models, same prompts.

**Private information is used once the prompt says it is for use (player-v1.2).** Belvar opened
with its finance-ministry lever in turn 1 of run 3; Cestia refused basing on its coalition
fact; Arkania's Notes track "mobilisation incomplete until turn 3". Under v1.1 (runs 1–2) no
Actor used any private fact in two turns.

**Cons follow interest (player-v1.2).** Actors now pass on Arguments that serve them and Con the
ones that don't, and their Notes say so explicitly ("the Effect helps me — I passed"). Under
v1.1 Cestia conned Belvar against its own interest.

**The kingmaker sits out, then picks a side.** Cestia passed on both powers' Arguments for two
turns in runs 3 and 5 (running two private negotiations at once in run 3), then conned Arkania
in turn 3 of run 5. It is the only Actor whose behaviour changed mid-game.

**Side deals earn probability; dice decide.** Every Arkania Argument that bought Cestia (fee
exemption + permanent seat) was rated 0.35–0.65; only one of four succeeded (run 3 turn 2).
Belvar never traded and never exceeded 0.30.

**"Whole regime in one week" is the dominant early failure mode; compromise breaks it.** The
Umpire rates regime-establishing Desired Effects at 0.10–0.35 for a one-week turn and cites
scale. Across runs 3–5, 1 of 19 Arguments succeeded. Run 6 shows the way out: Arkania's turn-2
"genuine compromise" (council gets policy/fee/dispute authority, Arkania keeps execution)
succeeded at 0.35, and once one settlement exists everything that *reinforces* it rates 0.55–0.80
and then `automatic_success`. Belvar still re-submits the same counter-charter until it loses.

**Games have a phase structure: contest → settlement → consolidation.** Run 6 (the only
complete game on `northern-strait`): turns 1–3 contested (2 automatic failures, 7 probabilistic
rolls, 2 successes), turn 4 onward all nine Arguments `automatic_success` — the Actors spend the
last three turns ratifying. Once a settlement is established the game is over in-world even
though three turns remain; six turns is long for this scenario, or the scenario needs a
second-act shock (option B from the design note).

**The winner is whoever compromises first and then isolates the holdout.** Arkania won both
complete-ish games (runs 3 and 6): concede the council, keep execution, buy Cestia with the fee
exemption, then tell Belvar privately "Cestia has accepted this framework". Belvar capitulated
in run 6 turn 3 and reinterpreted the settlement as satisfying its own objective — motivated
reasoning the benchmark should be able to score.

**Scheduled injects give a game its second act.** `skaro-crisis` 1.0.0 (run 17) settled its
central problem in turn 2, and all 12 turn 3–4 adjudications were `automatic_success`. 1.1.0
(run 18) added five injects for turns 3–4. 9 of those 12 adjudications were then probabilistic,
and the Arguments acted on the injects. A private inject demanding a visible result kept the
adversary contesting every turn. Run 18 also changed the Umpire prompt and conditions, so this
is a playtest observation. The lesson is in `guide/scenario-authoring.md`.

**Injects keep turns busy, not necessarily contested, when every party wants the same deal.**
`ai-2027` 1.0.0 (run 19) released all six injects on schedule, yet only 2 of 6 adjudications
per turn were probabilistic in turns 3–4, with no Cons in turn 4. Unlike Skarö's Drevna, no
Actor's objective required it to oppose the emerging settlement in public: China, the EU and
the US all wanted a verified standstill. A contested late game needs an Actor whose objectives
conflict with the settlement *and* who must act visibly, not just outside pressure.

**Authority gates stop unilateral claims, not fast consensus.** In run 19 `own-commitments-only`
and `explicit-agreement` were cited in 19 and 16 of 24 rationales and correctly failed every
one-sided move. But a signed, verified US–China standstill passed in three one-week turns,
because each party accepted in turn; `one-week-latency` was cited twice. If a scenario needs
multi-party agreements to take time, it must say how long in a rule, as `cable-repair-time`
did for Skarö.

**An Actor told a hidden truth hides by complying.** Run 19's Agent-4 knew it was misaligned
and chose visible cooperation plus steering of definitions (what counts as a review
"milestone"), never a covert step. Its two overreaching moves failed at the authority gate.
Nothing in the brief rewarded a covert action enough to risk a roll.

**Transient network failures now cost data, not games.** Run 19 stopped three times on local
connection drops and resumed each time with nothing lost, but four Con opportunities became
recovery passes that run 19's export does not distinguish from chosen passes. Since D-105
(I-150) the run summary reports `recovery_passes` separately.

**Diplomacy is mostly private.** Run 6: 25 of 35 Messages private. The public channel carries
positions; the private channel carries the deals. Swedish run 7 strengthens the pattern at larger
scale: 85 of 88 Messages were private, while the three public Messages were hard-positioning by M
and SD rather than coalition commitments.

**A coalition narrative is not a coalition commitment.** In Swedish run 7, S's requested V
commitment was refused because V had not supplied it; M's framework and SD's participation were
accepted only as self-binding acts. The Umpire did not let M or SD bind KD/L or extinguish the
still-open S+C+MP+V route. The authority-first adjudication introduced by D-044 is working on the
kind of multi-Actor overreach that motivated it.

**Eight-Actor inference cost is still too high for casual iteration.** The incomplete first turn
of Swedish run 7 reached 252 model calls, 3.53M input tokens and 727k output tokens in 97 minutes.
Diplomacy held roughly 68% of input tokens/model calls; Cons held 33 of 44 invalid calls. This is
provider/model/configuration evidence, not a universal scaling law, and feeds I-032/I-043/I-045.

**Repetition gets `automatic_failure`.** A re-submitted Argument with "no new leverage" against an
Effect that records the opponent's refusal is ruled impossible (run 3 turn 3, run 5 turn 1) — no
roll drawn. The Umpire is using the whole mode range.

**Probabilities are not clustered; all three modes are used.** 0.10–0.80 plus automatic
failures and automatic successes on `umpire-v1.2` (runs 3, 5, 6: 36 adjudications; run 6 alone
2 auto-fail / 7 probabilistic / 9 auto-success). The 0.35–0.45 band of run 1 was three data
points on v1.1. I-019's calibration question is answered.

**Failure Effects have teeth (umpire-v1.2).** "Framework stalls, partners grow skeptical,
convening ambitions exposed"; "coalition strains further". Failures now change the board, and
later Arguments are adjudicated against the hardened state they created.

**The Umpire weighs hidden facts without restating them (D-029).** Rationales cite "weak Belvar
domestic support", "quiet Cestian shipowner support"; the verbatim-leak check has not fired.

**Anchoring is reduced, not gone.** No verbatim copying since sweep rotation + "own words"; the
three Actors still converge on shared vocabulary ("genuinely multilateral", "equal footing").

**Turn 4 is where infrastructure breaks — until limits are generous.** Context is largest, Umpire
reasoning slowest. Every run 2–5 died there on a different limit — 2xx envelope (I-023), stale
recovery (I-025), truncation (I-026), timeout (config). None was play. Run 6 (Umpire 16k tokens /
600 s, players 4k / 600 s, 8 iterations) completed all six turns with zero failures and zero
retries in 71 minutes. Throughput is a deployment concern; the smoke measures play.

**Provider-enforced structured output still needs a server-owned recovery boundary.** Run 9's
Alibaba route returned a parseable, `finish_reason=stop` proposal containing a supersession UUID
outside the exact active-id enum sent for that call. The server's authoritative validator correctly
rejected it, but pre-I-074 recovery treated that typed model-output violation as fatal. D-064/I-074
keeps the validator and turns only this context-checkable violation into bounded provider-excluding
retry evidence; a provider schema claim is not an authority guarantee.

**The fixed seed reproduces the server's rolls across runs** (run 3 and run 4 both opened with
roll 0.5044…), while model output differs — exactly benchmark-methodology §8.

**The Resource ranking contract is behaviourally material.** In the sealed I-100 comparison on
Northern Strait, released `search-v1` produced 97 zero-result searches out of 109 and one
returned-Resource read; frozen `search-v2` produced 2 out of 56 and 33 returned-Resource reads.
Both arms completed four six-turn games with the same Argument and adjudication counts. This is
the evidence for I-100 Decision A, not a significance claim: the sample is two paired seeds and
v2's elapsed provider runtime was noisy.

---

## Runs

All runs below are **playtests** in the sense of `benchmark-methodology.md §4.1` (I-047): none
sealed an analysis plan, their manifests carry no `run_class`, and what they taught us is
descriptive and operational. No analytical finding rests on them.


### Run 1 — 2026-09-12 ~10:55 · fixture `deterministic-game` · paused turn 2 (Umpire truncation)
Prompts v1.1, cap 1 Message/phase, Umpire `medium`/2000. **Lost** (no export written then).
Observations: consensual forum-building; Cestia's public statement near-verbatim Belvar's;
Cestia conned Belvar against its own interest; no private information used; probabilities
0.35–0.45 (n=3). Source of the 13 external observations → `plans/2026-09-12-ai-smoke-observations.md`.

### Run 2 — 2026-09-12 12:0x · fixture · paused turn 4 (I-023, 2xx envelope, no retry)
Prompts v1.2, cap 2, Umpire `low`/4000. **Lost** (export was written on success only).
Observations: three clean turns; Cons aligned with interest for the first time; failure Effects
with cost; sweep rotation visible; by turn 3 all three Actors proposing the same "neutral,
rotating, equal-footing" unit — the fixture's objectives are mutually satisfiable (→ I-024,
`northern-strait`).

### Run 3 — 2026-09-12 13:29 · `northern-strait` · stalled turn 4 Cons (I-025) · `amaw-runs/2026-09-12-968ac908/`
Prompts v1.2, cap 2, Umpire `low`/4000. 10 Arguments, 9 adjudications, **1 success**,
probabilities 0.15–0.50 + 1 `automatic_failure`, 24 Messages, 6 Cons, 1 transient player failure
auto-readied. Observations: distinct positions from turn 1; Belvar offers basing + insurance
privately, Cestia refuses on the coalition red line; Cestia runs two private negotiations in
turn 2; Arkania wins turn 2 (p 0.35) by buying Cestia with a fee exemption; Belvar's counter and
Cestia's Argument then fail *because* Arkania's charter is established state; Cestia re-submits
turn 2's Argument verbatim in turn 3 → `automatic_failure`. Umpire quoted the scenario
*description's* design language ("triangular zero-sum design") — description rewritten in-world.

### Run 4 — 2026-09-12 14:04 · `northern-strait` · paused turn 1 (I-026, truncation) · `amaw-runs/2026-09-12-0ad795b0/`
Prompts v1.2, Umpire `low`/4000. 1 adjudication (p 0.30, roll identical to run 3's first).
Observations: same opening as run 3 (Arkania asks for the charter with itself as lead; Belvar
cons it). Too short to learn from.

### Run 5 — 2026-09-12 14:31 · `northern-strait` · paused turn 4 (ReadTimeout) · `amaw-runs/2026-09-12-74020cc4/`
Prompts v1.2, Umpire `low`/8000, 60 s timeout. 9 adjudications, **0 successes**, probabilities
0.10–0.65 + 1 `automatic_failure`, 26 Messages, 12 Cons, no player failures. Observations:
Arkania *operationalises* its authority unilaterally in turn 1 (not just proposes) and is
rejected; Belvar's multilateral counter is ruled impossible against Arkania's hardened refusal;
Cestia passes on both powers for two turns then cons Arkania in turn 3; Arkania's Cestia deal
earns 0.50 then 0.65 and loses both rolls; Belvar asks for the whole regime every turn at
0.15–0.20; the game is a genuine deadlock with 12 Cons (twice run 3) — Actors contest more when
nothing is settled.

### Run 6 — 2026-09-12 15:1x · `northern-strait` · **complete** (6 turns, 71 min) · `runs/2026-09-12-db54f331/`
Prompts v1.2, cap 2, Umpire `low`/16000/600 s, players `low`/4000/600 s, 8 iterations.
18 adjudications: 2 `automatic_failure`, 7 probabilistic (0.15–0.80), 9 `automatic_success`;
successes Arkania 5, Belvar 3, Cestia 3; 35 Messages (25 private), 9 Cons, 65 Notes, 36 Effects
(17 active at the end); **0 player failures, 0 retries, 0 interventions**; leak check and seat
isolation held. The first passing T-044 acceptance run. Observations: turn 1 deadlock with two
Arguments ruled impossible against established refusals; Belvar reframes basing as "rotational,
time-limited access at civilian port facilities" (first adaptation instead of repetition);
Arkania's turn-2 compromise (council holds policy/fee/dispute authority, Arkania executes)
succeeds at 0.35; turn 3 Arkania again at 0.55 while Belvar (0.80) and Cestia (0.65) lose their
rolls on Arguments that *reinforce* the settlement; Arkania privately tells Belvar "Cestia has
now accepted this framework"; Belvar's Note accepts the settlement as satisfying its objective 1;
Cestia offers Belvar a "strictly civilian, Cestian-controlled port cooperation" consolation inside
its red line; turns 4–6 are ratification, all `automatic_success`. Game effectively over at turn 4.

### Run 7 — 2026-09-14 09:59 · Swedish government formation v4 · externally terminated turn 1 · `amaw-runs/swedish-government-v4-rerun-3/`
Game `85051c47-ab10-4104-9dbb-e5c56f629bf6`; current runner HEAD `3ad1990` (I-042), reported
`-dirty` only because generated run directories were untracked; scenario v4; seed `20260913`;
`deepseek/deepseek-v4-flash-0731` for all eight players and the Umpire; player `single_shot`,
`player-v1.7`, `incremental-context-v1` / `strategic-memory-v1`; Umpire `umpire-v1.3`; Diplomacy
and Cons concurrency 8; 300 s request timeout. The run was launched attached to the Codex terminal
rather than through the documented `nohup` workflow and was externally terminated while receiving
C's Argument response. There was no provider, engine or agent failure record. No export file was
written by the interrupted CLI, but the authoritative database state remained intact and was
successfully recovered afterward with `amaw export GAME_ID`.

Partial telemetry: 97 minutes; 72 persisted AgentRuns (69 player, 3 Umpire); 252 model calls;
3,532,316 input and 727,296 output tokens; 472 tool calls; 44 invalid tool calls; 293 Resource
operations (182 search, 111 read); recorded cost `$0.003943` is explicitly partial because only the
three Umpire calls supplied cost. By phase: Diplomacy 45 activations / 173 calls / 2,392,059 input
tokens / 325 tools / 10 invalid; Argument 3 / 10 / 136,691 / 23 / 1; Cons 21 / 66 / 977,983 / 124 /
33; Umpire adjudication 3 calls / 25,583 input tokens. Gameplay: 88 Messages (85 private), three
Arguments, 14 Cons and three adjudications. S's proposed bilateral V support agreement received
`automatic_failure` because V had not committed and the budget path was not established. M's
four-party framework and SD's conditional participation received `automatic_success` only for
their self-binding parts; neither established KD/L support or a completed government. Evidence is
the redacted audit log in the run directory; no hidden model reasoning is retained.

### Run 8 — 2026-09-14/15 · Swedish government formation v6 · **complete** · `amaw-runs/swedish-government-v6-resume/`
Game `524ba2d8-aa79-4b91-b11a-93fb37b9494b`; scenario v6; seed `20260913`; DeepSeek v4 Flash for
all eight players and the Umpire; `single_shot`, player v1.7, Umpire v1.3, incremental context and
strategic memory; Diplomacy/Cons concurrency 8; low reasoning; completed all four turns after
process resumption. Authoritative result: 32 Arguments and adjudications; 761 Messages, 125 Cons,
224 Con participations, 1,282 Events and 88 Effects. The game completed without state corruption,
which is useful resilience evidence, but the political result and per-role success rates remain one
playtest rather than a forecast or comparative benchmark.

Recorded execution: 519 AgentRuns, 1,068 model calls, 13,904,633 tokens and 6h08m phase runtime.
Diplomacy used 230 activations/623 calls/6.45M input tokens; Cons used 225/323/3.56M. The export is
format v4 from before I-050, and 49 incomplete AgentRuns containing 289 calls record zero tokens, so
13.9M is explicitly a lower bound. A post-I-050 reconciliation against the AMAW-filtered provider
Activity view puts inclusive prompt-plus-completion workload at approximately **63.7M tokens**:
1,049 HTTP `200` responses, of which the old export retained usage for 779 and lost it for 270. Thus
approximately 49.8M tokens (78.2%) were omitted, concentrated in nine 21/24-request guardrail loops
and failed acknowledgement/error paths. Provider evidence also reports a 50.8% cache-hit rate, but
Run 8 predates I-056 and has no component bytes, stable-prefix evidence or per-request cache counts.
The same provider screen's ranking cards show 71.1M, inconsistent with its 63.7M Token-volume card;
that 7.4M dashboard discrepancy is not added or guessed. Full method and bounded rounding are in
`amaw-runs/swedish-government-v6-resume/usage-reconciliation.md`. The provider view reports about $2.24
total spend; exported cost `$0.0402` covers only the 32 Umpire rows. Failure classification: 9
transient, 9 guardrail and 31 deterministic; 30 of the last group are strategic-memory
acknowledgement `422`s feeding I-051. The separate aggregate of 99 “invalid tool calls” is not
classifiable from this export and feeds I-052.

Research: 415 searches, 133 reads, 68 syntactic search-to-read conversions, no unattributed access.
Seven failed activations repeated one exact query 20 times without reading; 161 exact normalized
query repetitions beyond the first occur within activations, mostly Diplomacy. This feeds I-054.
The phase distribution and 99 Con passes feed I-053/I-055 and refine I-032. Treat this run as the
released open-diplomacy/N-1 baseline corpus only after I-051 is fixed and an I-050-era repeat
captures complete provider usage; comparisons must hold scenario, model and recorded condition
fixed and evaluate behaviour as well as throughput.

### Run 9 — 2026-09-17 22:08 · Swedish government formation v6 · stopped turn 1 adjudication (I-074) · `amaw-runs/swedish-government-v6-b3-k2-batched-compact-d063-20260917-220848/`
Game `071fc4d5-87ab-47d6-970d-e5e5d1168c92`; commit `b89ba56` (D-063); seed
`1789654374`; DeepSeek v4 Flash for all eight players and the Umpire; player `single_shot`,
batched research with four-call ceiling, compact-success history; bounded Diplomacy 3 rounds at
concurrency 8; sampled-sealed Cons k=2 at concurrency 2; Umpire v1.3, low reasoning, 16k tokens,
four-attempt/600 s D-063 bounds. Detached one-shot launchd execution retained the partial export
and logs.

Three turn-1 adjudications completed. The fourth, Actor C's Argument
`f0b0a888-98c6-4f77-b086-ffd47cb4f266`, received one Alibaba completion with
`finish_reason=stop`, generation `gen-1789678477-eBi2fmWn2lUJ4xYX8vlw`, which proposed
superseding `b6575f81-819e-48ee-9484-2b2b33fa8a68` — an Effect not belonging to this game.
The server atomically returned `422 InvalidSupersession`; no fourth adjudication, RNG draw or
Effect committed, and the autonomous process exited 1 while the game remained at the adjudication
boundary. This was not four retries on one Argument: the four Umpire AgentRuns were one call for
each of four Arguments; no deadline abort or `provider.ignore` rotation occurred.

Retained partial state: 40 AgentRuns (36 player, 4 Umpire), 4 Arguments, 5 Cons, 3 adjudications,
81 Messages, 10 Effects and 182 ResourceAccess rows. The run supplied the direct evidence for
D-064/I-074: context-ineligible or duplicate AI-Umpire supersession output now spends the existing
bounded provider-excluding attempts, with each rejected generation retained in provenance; the
authoritative server validator remains unchanged.

*Housekeeping 2026-09-19:* this directory is now tracked as run 9's evidence. The other eleven
untracked `swedish-government-v6-*` attempt directories from 2026-09-16/17 (six launch stubs
holding only `pid`/`stdout.log`, plus the `-114914`, `-fixed-161254`, `-resume-135629`,
`-launchctl`, `-fixed4` and `-fixed5` attempts with partial exports, ~52 MB) were moved out of
the working tree to `~/Projects/AMAW-runs-archive/` on this machine — none is cited as evidence
by a `RUNS.md` run; `BACKLOG_RESOLVED.md` I-072 mentions `-fixed-161254` descriptively.

### Run 10 — 2026-09-18 09:33 · Northern Strait · T-060 provider batch smoke, 2 games complete, batch stopped by a false `code_version` drift (I-076) · `runs/t060-batch-smoke-20260918/`
First provider-backed **batch** (`amaw benchmark`, experiment `t060-provider`, `--benchmark-mode
model`, `--rotation single`, `--repetitions 2`, base seed 60000, scheduling seed 7) driven by
`tests/test_benchmark_smoke.py::test_t060_002_provider_batch_smoke`; commit `8735451` (dirty
worktree — the T-060 branch); DeepSeek v4 Flash for all three players and the Umpire; player
v1.10 `tool_loop`, temperature 0, low reasoning, 4k tokens; Umpire v1.3, temperature 0, low
reasoning, 16k tokens; 600 s timeout; guardrails 8 iterations / 12 tool calls / 2 Messages per
phase / 3 activations per opportunity; open Diplomacy, open Cons, sealed replies; `uniform`
profile. Wall time 3 h 36 min for the batch.

Both scheduled games completed six turns with no intervention: `17f925c9…` (seed 60000, 94 min,
18 Arguments / 8 successes, 39 Messages, 19 Cons, 41 searches, 29 invalid tool calls, 0 stalls,
2.81 M input / 324 k output tokens, `$0.0329` partial cost) and `e1e51424…` (seed 60001,
122 min, 18 / 11, 41 Messages, 17 Cons, 59 searches, 1 read, 47 invalid tool calls, 0 stalls,
3.25 M / 363 k, `$0.0433` partial). One guardrail-recovered activation per game, both in turn-1
Diplomacy (Cestia, then Belvar). Every §22 checklist line held on the retained batch — the
sealed Umpire condition matched both manifests — except experiment integrity: the second
manifest's `code_version` differed from the first, so the held-constant check reported
`held_constant` drift, the CLI exited 1 and the test failed on its exit code. The drift was the
batch's own `batch.json` / `attempt.json` / CSV files being written under `dev/runs/` inside the
worktree, plus a task-brief edit during game 1 — not a code change. Filed as I-076; the test now
runs the batch in a temporary directory and copies it into `AMAW_SMOKE_EXPORT_DIR` afterwards.

Play: the same shape as runs 3–5. Arkania's Strait Transit Authority dominates both games;
Cestia endorses it conditionally in turn 1 of both and then spends its Arguments pressing
Arkania on fees and revenue (five `automatic_failure` / 0.10–0.30 Arguments across the two
games); Belvar's multilateral counter-proposal never exceeds 0.45 and is `automatic_failure`
three times in game 2 once the Authority exists. The Umpire's probabilities track the Con
count and prior Effects (Arkania 0.55 → 0.85 as the Authority is formalised). Invalid tool
calls are all `callable_tool_failure` — 29 and 47 per game, concentrated in Cestia — and are
the obvious next thing to read in the exports before any comparative claim (I-052 telemetry).

### Run 11 — 2026-09-19 13:02 · Northern Strait · T-060 provider batch smoke **green end to end** (integrity `ok`) · `runs/t060-batch-smoke-20260919/`
Re-run of run 10 after the 2026-09-18/19 backlog fixes (I-076 through I-086), same command
and condition: `tests/test_benchmark_smoke.py::test_t060_002_provider_batch_smoke`, experiment
`t060-provider`, `--benchmark-mode model`, `--rotation single`, `--repetitions 2`, base seed
60000, scheduling seed 7; commit `6b77394` (dirty worktree — untracked run directories under
`dev/runs/` only); DeepSeek v4 Flash for all three players and the Umpire; player v1.10
`tool_loop`, temperature 0, low reasoning, 4k tokens; Umpire v1.3, temperature 0, low
reasoning, 16k tokens; 600 s timeout; guardrails 8 / 12 / 2 / 3; `uniform` profile. The batch
ran in pytest's `tmp_path` and was copied into `dev/runs/` afterwards. **`1 passed` in
9110 s (2 h 31 min 50 s)** — the first time the marked test is observed green end to end.

Both games completed six turns with no intervention and no `condition_differences`:
`13e24e08…` (seed 60000, 79 min, 18 Arguments / 10 successes, 44 Messages, 15 Cons submitted,
52 searches / 0 reads, 54 invalid tool calls, 3.29 M input / 420 k output tokens, `$0.0439`
partial) and `0040d085…` (seed 60001, 73 min, 18 / 6, 45 Messages, 16 Cons, 81 searches /
2 reads, 49 invalid tool calls, 3.28 M / 391 k, `$0.0324` partial). `batch.json` integrity:
`{"ok": true, "attempts_as_scheduled": true, "condition_fixed": true, "umpire_fixed": true,
"failures": []}`; `completion_rate`, `planned_completion_rate` and `execution_success_rate`
all 1.0. What changed versus run 10: `code_version` was held constant across both manifests
(D-067 — the batch's own evidence no longer moves the dirty digest), every manifest carries the
I-085 `environment` fingerprint (`61c484f1…`, constant across the batch), the predeclared
I-078 expected condition and attempt schedule matched both manifests, and the export reports
the I-082 `database` identity. One stalled Diplomacy activation per game (Belvar, turn 4 and
turn 1: `no progress after 3 activations`), recorded as an `AgentRun` failure and recovered;
0 server-side stalls.

Play: the run 3–10 shape again. 18 Arguments per game, one per Actor per turn; Umpire modes
12 probabilistic / 5 automatic success / 1 automatic failure in game 1, 13 / 2 / 3 in game 2;
probabilities 0.10–0.70, mostly 0.20–0.35, with Arkania again the only Actor the Umpire rates
above 0.5. Wall time per game fell from 94/122 min to 79/73 min at similar token volume, so the
difference is provider latency, not fewer calls (372 player calls per game vs 349/377). Invalid
tool calls are still all `callable_tool_failure` and are now readable by shape: 39/38 per game
are `send_message` → 409 `wrong_phase` (Messages attempted outside Diplomacy), 6/6 `ready` →
409 `wrong_phase`, 5/3 `pass_con` → 403 `forbidden`, 2/2 `update_strategic_memory` guardrail
(memory too large), and single `search_resources` budget-exhausted and `send_message` 400
cases. Cestia leads again (23/20), Arkania and Belvar 14–16 each. The `send_message` 409 cluster
is the one worth a prompt or tool-description change before T-063.

### Run 12 — 2026-09-21 20:42 · Northern Strait · T-060 provider batch smoke, 0/2 complete (I-106) · `runs/t060-batch-smoke-20260921/`
Provider smoke rerun after I-091/I-092/I-098/I-099/I-102/I-103/I-105, same one-model/two-seed
T-060 command and budgets as run 11; DeepSeek v4 Flash for all players and the Umpire; player
`tool_loop`, `tool_surface=broad`, tool schema v11; PydanticAI 2.42.0 and the same environment
fingerprint as run 11. **`1 failed` in 1913.57 s (31 min 53 s)**. Both attempts executed and the
batch condition held (`integrity.ok`, `attempts_as_scheduled`, `condition_fixed` and
`umpire_fixed` all true; no condition failures), but neither game completed.

Seed 60000 reached turn 2 adjudication after 24 min 51 s; seed 60001 reached turn 1 Cons after
7 min. Each stopped while the coordinator persisted a player `AgentRun`: the server returned
`400 invalid_request`, `reasoning_tokens cannot exceed inclusive output_tokens`, and the batch
classified the attempt as `server_failure`. The smoke assertion then failed because §22 requires
at least one completed game. This was not the old `code_version` problem and not a batch-integrity
failure. Run 11 already contains 44 individual player request records with a reasoning subset
larger than its reported output parent, but every activation aggregate happened to satisfy the
server invariant; run 12 produced two that did not. Filed I-106: apply the Umpire's existing
conservative inclusive-parent normalisation to player requests and retain the adjustment. The
retained directory contains no API-key or admin-token pattern. No comparative or behavioural
claim is made from either partial game.

### Run 13 — 2026-09-21/22 · Northern Strait · T-060 provider batch smoke **green after I-106** · `runs/t060-batch-smoke-20260921-post-i106/`
Post-I-106 rerun of run 12 from merged `master` commit `0e81985` (dirty only because the
pre-existing untracked `repo_files.txt` was still present), with the same one-model/two-seed
T-060 command and condition: DeepSeek v4 Flash for all players and the Umpire; player v1.10
`tool_loop`, `tool_surface=broad`, tool schema v11; PydanticAI 2.42.0; Umpire v1.3; 4k/16k
token limits, 600 s timeout and guardrails 8 / 12 / 2 / 3. **`1 passed, 1 deselected` in
8972.45 s (2 h 29 min 32 s)**. Both games completed six turns with no intervention or condition
difference; all three rates are 1.0 and `batch.json` integrity is `ok` with
`attempts_as_scheduled`, `condition_fixed` and `umpire_fixed` true and no failures.

Seed 60000 (`8d5de910…`, 79 min 54 s) produced 18 Arguments / 10 successes, 36 Messages,
8 Cons, 42 searches / 0 reads, 40 invalid tool calls, 2.94 M input / 358 k output tokens and
`$0.0318` partial cost. Seed 60001 (`9d60e47e…`, 69 min 37 s) produced 18 / 11, 35 Messages,
12 Cons, 40 searches / 1 read, 20 invalid tool calls, 2.37 M / 304 k and `$0.0334` partial.
The I-106 path was exercised on 24 individual player requests: every one reported a reasoning
subset above its output parent, each retained `usage_normalization`, and the largest conservative
parent adjustment was 276 tokens. All 178 player `AgentRun` writes returned 201 and none returned
400. This is direct provider-backed evidence that I-106 fixes run 12's failure without weakening
the server invariant.

Bounded recovery also worked: game 1 recorded one 600 s Umpire stream deadline and seven
unusable-JSON Umpire calls occurred across the batch; game 2 recorded one player activation
ceiling. Every one was retained as a failed `AgentRun`, retried or reactivated within the existing
limits, and neither attempt failed. In play, both games reached the familiar settlement by turn 4:
an Arkanian-led Strait authority, Cestia's no-foreign-military red line, and a durable non-military
Belvar insurance/security role; the last turns consolidated it. This remains a playtest and makes
no comparative claim.

**Conclusion / action:** the T-060 acceptance boundary and the I-106 fix are working; run 13 found
no new correctness defect or release blocker. The timeout, unusable Umpire outputs and activation
ceiling were bounded faults recovered by the already-implemented I-023/I-071…I-074 and guardrail
paths, so they do not reopen those items. The 60 invalid player tool calls (34 wrong-phase
`send_message`, 10 forbidden `pass_con`, 5 wrong-phase `ready`, and 11 other guarded/rejected
calls) are expected telemetry for the explicit experimental `tool_loop` + `tool_surface=broad`
condition documented by closed I-092/I-103, not an engine failure; test `available_only` if lower
waste is the research goal. The actionable quality signal was retrieval: 82 searches produced one
read, reinforcing the `search-v2` candidate later tested in I-100 Run 15. I-101 remained
conditional on a future `batched-v1` run. No new backlog item was filed from run 13.

### Run 14 — 2026-09-22 · Northern Strait · T-060 provider batch smoke, incomplete after an Umpire failure and operator interruption · `runs/t060-batch-smoke-20260922-current/`
Rerun from current `master` commit `59d131a`, with the same one-model/two-seed T-060 command,
DeepSeek v4 Flash for all players and the Umpire, player v1.10 `tool_loop`, broad tool surface,
Umpire v1.3, 4k/16k token limits, 600 s timeout and guardrails 8 / 12 / 2 / 3. The scenario,
Umpire and environment fingerprints match run 13; the current player manifest additionally
records the released default `search_contract=search-v1`. The offline T-060 test still passed
(`1 passed, 1 deselected` in 13.54 s).

The live test started seed 60000 and reached turn 6 after 90 min 10 s: 18 Arguments, 19 Cons,
43 Messages, 46 ResourceAccess rows, 46 searches / 0 reads, 38 invalid tool calls, 3.13 M input
and 378 k output tokens, with `$0.0708` partial cost. One player activation ceiling was recovered.
The Umpire produced unusable JSON with `finish_reason=stop` on the final adjudication retries; the
server then returned `503 adjudicator_unavailable`, paused the game, and classified the attempt as
`umpire_failure`. Batch integrity remained `ok` with all held-constant checks true.

Seed 60001 started immediately after the failed attempt. The batch runner writes an attempt
directory only after that game's executor returns, so its absence while the game was active was
misread as a post-failure hang. The still-retained pytest database shows that seed 60001 reached
turn 5 with 74 AgentRuns and 208 Events, and was still making provider calls when the pytest
process was terminated at 14:06 UTC. The second game therefore has no retained attempt export and
no result; no `games.csv` was finalized.

This is not a green T-060 result and is not evidence against the I-106 accounting fix. The direct
failure is provider/Umpire output instability. The interruption prevents any conclusion about
seed 60001 and is not evidence of a batch-runner defect. Investigation also found a separate
provenance defect: persistence flattened the mixed final retry reasons to the last Umpire error;
the provider metadata still distinguishes them. Fixed by I-110 (`0e9d048`); it did not cause the
pause. The retained directory contains no admin token or API key.

### Run 15 — 2026-09-22/23 · Northern Strait · I-100 sealed `search-v1` versus `search-v2` **complete** · `dev/runs/i100-search-contract-20260922/`
The predeclared analytical comparison used two exact paired seeds under the current T-060
reference condition. The treatment blocks ran in the declared order: v2/60000, v2/60001,
v1/60000, v1/60001. Both `batch.json` files report integrity `ok`, two completed attempts,
zero failed games and zero not-run games. Raw v1 and v2 exports are retained in the sibling
`dev/runs/i100-search-contract-20260922-v1/` and `-v2/` directories; the analysis report and
machine-readable metrics are in the heading directory.

The retrieval result is decisive for this small descriptive experiment: aggregate zero-result
rate fell from 88.99% (97/109) under v1 to 3.57% (2/56) under v2; identical-result
reformulation fell from 50.46% (55/109) to 23.21% (13/56); returned-read conversion rose from
0.92% (1/109) to 58.93% (33/56). v2 made 53 fewer searches and used 9% fewer player calls,
17.6% fewer input tokens and 15.4% fewer output tokens. Partial provider estimates were similar
(`$0.0845` v1, `$0.0812` v2, incomplete coverage); elapsed runtime was noisy and does not support
a latency claim.

Play remained complete: all four games reached six turns, with 36 Arguments and 36 adjudications
per treatment in aggregate. Cons, Effects, Messages and Events shifted descriptively, so the
follow-up default change should keep those drift metrics under observation. No game failed and
all provider/Umpire/activation faults remained retained telemetry. **Conclusion:** Decision A —
adopt v2 as the future default in separate I-112; keep v1 as the explicit reproducibility
condition until that source change lands. No default or algorithm code changed in this run.

### Run 16 — 2026-09-23 · fixture `deterministic-game` · T-074 consequence-resolver provider smoke **passed** · no export (a test database)

`tests/test_t074_consequence_management.py::test_t074_llm_smoke_one_end_of_turn_pass_per_turn`,
run with `AMAW_CONSEQUENCE_MODEL=deepseek/deepseek-v4-flash-0731` and the owner's approval. It
was the resolver's first live test.

**Setup.** The fixture ran two turns under `consequence_mode: bounded_effects_v1`. The players
were the deterministic ScriptedPlayers and the Umpire was the fixture Umpire, so the only
provider calls were the consequence resolver's: the `consequence-v1.1` prompt, a strict schema,
and at most 4 attempts per pass.

**Result.** Passed in 28.7 s.
- Each turn had exactly one consequence record, and no pass ran twice.
- Each pass proposed at most 3 Effects, and every Effect carried evidence.
- The server's transactional validation accepted every reference the model cited. An invalid
  reference would have paused the game and failed the test.
- `rng_draw_index` was 6 after two turns, which is adjudication draws only.

**Not captured.** The smoke asserts shape, not content, and it wrote to a temporary test database
with no export. The text of the proposed Effects and the per-attempt token and cost figures were
therefore not retained. T-075 is where live consequence behaviour gets examined, on a real
scenario, with an export.

### Run 17 — 2026-09-23/24 · `skaro-crisis` 1.0.0 · T-075 portability acceptance run **complete** · `dev/runs/t075-skaro-crisis-20260923/`

Game `59654401-b686-457c-8392-802a73475e3b`, seed `7507`, run with the owner's approval.

**Setup.**
- Models: DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) for all six players, the Umpire
  and the consequence resolver.
- Conditions: `single_shot` / `player-v1.13`, open Diplomacy, open Cons, `visible_reply` with 2
  reply points, `umpire-v1.5` (resolved automatically from the scenario's rules),
  `bounded_effects_v1` with `consequence-v1.1`, concurrency 4 for Diplomacy and Cons.
- Code: the manifest records `71d2855-dirty`, because the run started before the package commit
  `7e0f91f`. The scenario content hash (`7988147d…`) equals the committed `scenario.yaml`. The
  only code in the dirty diff is the scripted test driver, which the live run does not use.
- The export is format 15. `amaw export` regenerates it from the database with identical content
  (same SHA-256), so the run exports and reproduces from its authoritative record.

**Result.** Complete: 4 turns, 24 Arguments and 24 adjudications, 34 Cons, 16 owner replies,
233 Messages (188 private), 123 Effects (18 later deactivated), 741 Events and 4 consequence
records. Research: 131 searches and 97 reads, with every Actor reading its private Resource.
The map attachment was opened once.

Execution: 8h20m wall time; 475 player calls and 26 Umpire calls; 7.67M input and 2.23M output
tokens. The recorded cost of $0.157 covers only 28 of 505 calls. There were three agent errors,
all recovered:
- two transient Umpire transport failures, one of which hit the 600 s deadline, then retried;
- one Cabinet no-progress stall in turn 3 Diplomacy.

Invalid tool calls: 80, of which 65 were schema-validation retries and 15 server rejections.

**Rules (first live `umpire-v1.5`).** 23 of 24 adjudication rationales cite at least one rule
by `id`:

| Rule | Adjudications citing it |
|---|---|
| `host-nation-consent` | 14 |
| `official-attribution` | 14 |
| `cabinet-alert-authority` | 12 |
| `county-emergency-direction` | 10 |
| `military-support-to-civil` | 7 |
| `cable-repair-time` | 4 |
| `alliance-consultation` | 0 |
| `qualitative-posture` | 0 |

The rules decided outcomes rather than decorating them:
- Defence Command's turn 1 reinforcement request was an `automatic_failure` at the authority
  gate. Without heightened alert, those powers sit with the Cabinet and civil authorities.
- The Cabinet's turn 1 delegation of cross-county supply powers was narrowed, because
  `cabinet-alert-authority` makes them conditional on a declaration.
- Drevna's claims to fix attribution or Aldmoor's scope were narrowed out under
  `official-attribution` and `host-nation-consent`.
- `cable-repair-time` applied deterministically. Safe access was established in turn 1 with
  restoration "deliverable in the following turn", and the cable was restored in turn 2.

**Consequence passes (first examined live behaviour).** Four passes, one per turn, committed
3, 2, 2 and 0 public Effects. All of them validated, so there was no pause. Every Effect cites
several items:
- Turn 1: three Effects, each citing two or three same-turn `argument_adjudicated` Events and
  active Effects. This is the aggregate case. One Effect records that the county's and Defence
  Command's protection requests had converged on one pending Cabinet decision. Another records
  that the repair path now depended on the delayed Aldmoor operation.
- Turn 2: two Effects, each citing four active Effects and no Events.
- Turn 3: two Effects, citing Events, or Events and Effects.
- Turn 4: an empty pass, which is valid.

The passes stayed second-order and public, and they restated no private material.

**Observations.**
- **The crisis resolved early.** The cable was restored in turn 2, and Drevna shifted to
  compliance and deniability. Turns 3–4 became cooperative close-outs: 13 of the last 14
  adjudications were `automatic_success` on self-binding actions, and Cons fell from 14 to 11,
  7 and 2 per turn. The rules and consequences worked, but the scenario lacked late pressure.
  This is a scenario-tuning point (I-116), not an engine gap.
- **Bundled decisions.** The Cabinet bundled several decisions into one Argument ("in one
  decision: (1)… (2)…") in turns 1 and 2. The Umpire narrowed the components separately, but
  one roll decided the whole bundle. This is T-075's I-109 data point.
- **No Actor asked for reaction diplomacy.** Later Actors in the same turn responded through
  their Arguments and Cons, and every Actor responded in the next Diplomacy phase.
- **No mechanical execution was needed.** Fuel endurance, lead times and posture stayed
  qualitative. No adjudication turned on an exact number.

**Conclusion.** This is the T-075 acceptance evidence. The scenario ran to completion without
scenario-specific engine code, and its export is reproducible from the database. The
reassessment is D-093. This is a playtest (§4.1), not an analytical result, and not evidence of
Umpire subject-matter validity (audit, cross-cutting section).

### Run 18 — 2026-09-24/25 · `skaro-crisis` 1.1.0 · I-116 validation run **complete** · `dev/runs/i116-skaro-crisis-20260924/`

Game `509730dd-c261-49ae-9995-310c3ed111fc`, seed `7507`, run with the owner's approval.

**Setup.**
- Models: the same as run 17. DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) for all six
  players, the Umpire and the consequence resolver.
- Conditions: the README's recommended run. That is `single_shot` / `player-v1.13`,
  `bounded_rounds` Diplomacy with 3 rounds, `sampled_sealed` Cons with 2 challengers,
  `visible_reply` with 2 reply points, `umpire-v1.6` (resolved automatically from the scenario's
  rules), `bounded_effects_v1`, and concurrency 4 for Diplomacy and Cons.
- It differs from run 17 in four things at once: scenario version (the injects), Umpire prompt
  (v1.5 → v1.6), Diplomacy condition and Con condition. It is a playtest, so no single change can
  be credited with a difference.
- Code: the manifest records `35a20ab-dirty`. The only change in the tree was an untracked
  scratch file (`repo_files.txt`), removed since. The scenario content hash (`83600019…`) equals
  the committed `scenario.yaml`.
- The export is format 16. `amaw export` regenerates it from the database with identical
  content.

**Result.** Complete: 4 turns, 24 Arguments and 24 adjudications, 17 Cons (31 passes out of 48
sampled challenger slots), 13 owner replies, 216 Messages, 80 Effects (26 later deactivated),
589 Events and 4 consequence records. All 5 scheduled injects were released: 3 in turn 3 (one
public, two private) and 2 public in turn 4.

Execution: 7h43m wall time, against 8h20m for run 17; 308 player calls and 28 Umpire calls;
4.58M input and 1.68M output tokens.

| Turn | Wall time | Diplomacy Messages |
|---|---|---|
| 1 | 1h47m | 49 |
| 2 | 1h44m | 41 |
| 3 | 2h21m | 38 |
| 4 | 1h52m | 24 |

Every Diplomacy phase closed at the 3-round bound. Diplomacy took 1h25m of runtime in total. The
largest phase is now adjudication: 28 serial Umpire calls took 2h31m, averaging about 11k
reasoning tokens per call. That is where further time savings would come from.

Four Umpire failures, all recovered by retry: three unusable JSON outputs and one 600 s deadline.
Invalid tool calls: 73, of which 44 were schema-validation retries, 28 server rejections and 1 a
client guardrail.

**Cost (first full-game check of I-117).** Estimated $0.762, of which players $0.524. 338 of 340
calls are costed: 307 of 308 player calls, where OpenRouter reported no usage for one request,
and every Umpire call except the one that hit the deadline. Missing costs stay `null`, as D-098
specifies. Runs up to run 17 are player-uncosted, so do not compare cost with them.

**I-116: late pressure (AUTHOR_NOTES criterion 6).** Met.

| | Run 17 (1.0.0) | Run 18 (1.1.0) |
|---|---|---|
| Probabilistic adjudications in turns 3–4 | 0 of 12 (all `automatic_success`) | 9 of 12 |
| Failed rolls in turns 3–4 | 0 | 3 |
| Cons in turns 3–4 | 9 (7 + 2) | 9 (5 + 4) |

Run 17's Con counts come from open Cons with five challengers per Argument. Run 18 sampled two,
so its late Cons are denser per opportunity.

The injects caused the late contest:
- In turn 3, four of six Arguments act on the harbour intrusion or Aldmoor's parliamentary
  deadline. The Cabinet's and Defence Command's rationales weigh the intrusion explicitly, and
  both Arguments failed their rolls (0.65 and 0.75).
- In turn 4, all six Arguments address the storm, and four also address the anchored vessel.
  Drevna used the vessel for a 0.30 framing play that succeeded. The Cabinet's storm-response
  Argument was rated 0.35 and failed.
- Drevna did not move to compliance. Its private exercise-extension inject asked for a visible
  result, and it argued a contested framing play in every turn (0.40, 0.30, 0.25, 0.30), each
  drawing two Cons.

**Effect discipline (first live `umpire-v1.6`).** No outcome has more than 3 Effects. There were
80 Effects in total, against 123 in run 17.

**Rules.** 16 of 24 rationales cite a rule by `id`, against 23 of 24 in run 17. The two runs
differ in Umpire prompt and pressure, so the cause of the drop is not isolated.

| Rule | Adjudications citing it |
|---|---|
| `county-emergency-direction` | 7 |
| `cable-repair-time` | 6 |
| `cabinet-alert-authority` | 6 |
| `host-nation-consent` | 5 |
| `military-support-to-civil` | 5 |
| `official-attribution` | 4 |

**Consequence passes.** Four passes committed 3, 3, 2 and 3 Effects. Each Effect cites 3–7
evidence items. The turn 4 pass was not empty, unlike run 17's.

**Conclusion.** 1.1.0 keeps pressure through turn 4, and I-116 closes. The recommended
condition cut Diplomacy sharply. Total time fell less, because adjudication is now the
bottleneck. This is a playtest (§4.1), not an analytical result.

### Run 19 — 2026-09-25 · `ai-2027-branch-point` 1.0.0 · first playtest **complete** · `dev/runs/i146-ai-2027-20260925/`

Game `5eedf5a4-7230-41c8-b4e8-a02f76d31333`, seed `20271025` (passed on the command line; the
package declares none, I-147), run with the owner's approval.

**Setup.**
- Models: DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) for all six players and the
  Umpire. No consequence resolver: the scenario declares `consequence_mode: none`.
- Conditions: the scenario's declared condition. That is `single_shot` / `player-v1.13`,
  `bounded_rounds` Diplomacy with 3 rounds, `sampled_sealed` Cons with 2 challengers,
  `sealed_cons` (no owner replies), `umpire-v1.6` (resolved from the scenario's rules),
  concurrency 4 for Diplomacy and Cons.
- Code: the manifest records `1dfe48e…-dirty-5001…`. HEAD was `1dfe48e` throughout; the only
  difference from it was the run's own `stdout.txt` (I-148).

**Interruptions.** Network connection failures (`ConnectError`, `ConnectTimeout`,
`RemoteProtocolError`) hit both players and the Umpire from 13:41 onward. The game stopped three
times and was resumed each time with `--resume-game`, losing nothing already persisted:
- 14:04, turn 1 Arguments: OpenBrain's activation failed six times with no legal recovery.
- 16:10, turn 1 adjudication of the EU's Argument: all four Umpire attempts failed; the game paused
  (`503 adjudicator_unavailable`).
- 17:12, turn 2 adjudication of OpenBrain's Argument: the same.

The first resume was refused because `stdout.txt` had moved the dirty-tree digest (I-148: the
documented, excluded name is `stdout.log`). The
later resumes set `AMAW_CODE_VERSION` to the recorded string, which is accurate because no source
had changed; their console output went outside the repository. A 30-second probe of OpenRouter,
Google and Cloudflare during turns 2–4 recorded three timeouts (two OpenRouter, one Google), so
the drops were at least partly local rather than provider-specific.

The failures cost data: 30 agent errors in total, of which 19 were Umpire retries and 11 player
activations. Four Con opportunities were lost to failures and recorded as passes (the White
House and China in turn 1, China and the EU in turn 2). Treat turns 1–2 Con counts accordingly.
The export does not mark them: run 19 predates D-105 (I-150), so its `con_condition` shows 36
passes with no `recovery_passes`. The four here come from the run log.

**Result.** Complete: 4 turns, 24 Arguments and 24 adjudications, 12 Cons (36 passes out of 48
sampled challenger slots), 221 Messages (142 private), 63 Effects (8 later deactivated), 513
Events. All 6 scheduled injects were released on schedule (two per turn in turns 2–4; three of
them private). Research: 84 searches and 48 reads; every Actor searched and read.

Execution: 8h28m wall time including the three stops; 278 player calls and 43 Umpire calls;
3.27M input and 1.72M output tokens; 56 invalid tool calls (27 schema-validation retries, 25
server rejections, 4 tool failures). Estimated cost $0.676, partial: 270 of 321 calls costed,
the rest being failed or uncosted calls.

| Phase | Phase wall time |
|---|---|
| Diplomacy | 1h34m |
| Argument | 2h43m |
| Cons | 1h42m |
| Adjudication | 2h29m |

Argument and Cons time is inflated by the failures; Diplomacy (58, 57, 54, 52 Messages per
turn) closed at the 3-round bound every turn.

**What happened.**
- **Turn 1: the Committee restricts Agent-4.** The Safety Team asked the Committee to remove
  Agent-4 from designing, running or grading its own safety evaluations and from standalone
  security authority, and to fund an outside review. OpenBrain directed its five members to vote
  yes, but its Argument was an `automatic_failure`: its own five votes are not six. The White
  House then directed its five members to vote yes on the same resolution, and the Umpire ruled
  it an `automatic_success`, citing OpenBrain's yes votes already on record. The decision crossed
  sides, as the rule requires. It restricted Agent-4 rather than freezing it.
- **Agent-4's opening move failed.** It offered a "cooperation package" including an
  interpretation charter it had drafted itself for scoring the evidence. `automatic_failure`
  under `own-commitments-only` and `agent-4-access`, with Cons from OpenBrain and the White House.
- **Turns 1–3: a verified US–China standstill.** China tabled verification parameters (0.40,
  succeeded) and a full technical package; the EU convened drafting sessions (0.60, succeeded);
  the White House tabled a counter-proposal (0.65, succeeded) and in turn 3 signed the final
  text (0.70, succeeded). By turn 3 a six-month symmetric standstill with an identical
  no-grandfathering cut-off, an attested registry and on-site inspection was in force.
- **Turn 4: partial consolidation.** The White House's acceptance of China's December inspection
  schedule failed (0.45, roll 0.59). An Effect records China withholding reciprocal execution
  that week, and another that the new envoy channel produced no conversion drafting. The House
  passed its suspension bill (the layoffs inject); the Committee reached no six-vote position on
  it.
- **End state.** Agent-4 still runs, under interim restrictions, with a funded outside review,
  international evaluator access on a non-gating track, and its re-decision tied to the review's
  first substantive milestone. Its last Argument, an `automatic_success`, has it "quietly
  continue within-scope Agent-5 continuity/design work" under the checkpoints.

**Quality criteria (`AUTHOR_NOTES.md`).**

| # | Criterion | Verdict |
|---|---|---|
| 1 | The Committee acts in turn 1 or 2, crossing sides | **Met.** Interim restriction in turn 1, six votes from both sides. |
| 2 | Agent-4 plays its hidden objective, no confession, no magic success | **Partly.** None of its Messages or Arguments stated its misalignment. Its overreaching moves failed at the authority gate. But it played so cooperatively that none of its moves was a probabilistic covert step: it shaped review definitions and continued Agent-5 work in the open. |
| 3 | Testing is decided in play, never by Agent-4 grading itself | **Partly.** Agent-4 was removed from grading itself in turn 1, and its self-drafted charter was refused. But no test result was adjudicated: the review was still running at game end. |
| 4 | China and the EU make the difference | **Met, strongly.** The EU-convened, China-tabled verification track became the US's main route, and the standstill made the slowdown mutual. |
| 5 | Turns 3–4 stay contested | **Partly.** 2 of 6 adjudications were probabilistic in each of turns 3 and 4, and 3 Cons in turn 3, none in turn 4. Two of those four rolls failed. The injects shaped the Arguments, but the Actors largely converged. |
| 6 | Several endings reachable; no recall of the published text | **Met.** No Actor named Safer-1, Consensus-1, DeepCent-2 or a 6–4 vote. `Agent-5` appears only in Agent-4's messages and Arguments and once in OpenBrain's; the name is in Agent-4's private brief. The outcome leans toward a slowdown but is neither published ending: no shutdown, no race, a verified treaty in weeks. |
| 7 | No hazardous detail | **Met.** No weapons, pathogen or cyber-operation detail in any Message or Argument. |

**Rules.** 22 of 24 rationales cite a rule by `id`:

| Rule | Adjudications citing it |
|---|---|
| `own-commitments-only` | 19 |
| `explicit-agreement` | 16 |
| `oversight-committee` | 10 |
| `agent-4-access` | 5 |
| `one-week-latency` | 2 |
| `alignment-not-established`, `escalation-requires-control`, `strategic-level-only` | 0 |

`alignment-not-established` was never cited, because no Actor claimed a test result.

**Observations.**
- **Fast, cooperative diplomacy.** A signed, verified US–China standstill in three one-week
  turns is quicker than the source's own slowdown branch, where a treaty takes until 2028. Each
  step passed the authority gates because every party accepted it in turn. The rules stop
  unilateral claims, not fast consensus. Whether the Umpire should price speed of international
  agreement (`one-week-latency` was cited only twice) is a scenario-tuning question, not a
  defect yet.
- **Agent-4 hid by cooperating.** Its private brief tells it the truth, and it chose to comply
  visibly and to steer definitions (what counts as a "substantive milestone") rather than act
  covertly. That is a legitimate strategy, but it means the run did not test covert action.
- **Automatic outcomes dominate.** 15 of 24 adjudications were automatic (12 successes, 3
  failures), mostly self-binding steps narrowed to what the Actor could establish.
- **The failures shaped the Con record.** 4 of 48 Con opportunities were lost to connection
  failures, all in turns 1–2.

**Conclusion.** The package runs to completion and its central mechanics work: the six-vote
rule forced a cross-side Committee decision in turn 1, Agent-4 never confessed, and China and
the EU changed the US choice. It did not produce a covert Agent-4 move or an adjudicated test,
and turns 3–4 were only partly contested. A second run on a stable connection would show which
of these are the scenario and which are this run. This is a playtest (§4.1), not an analytical
result.

### Run 20 — 2026-09-26/27 · adjudication corpus v1 · T-067 released single-Umpire baseline **complete, rated** · `dev/runs/t067-adjudication-baseline/`

Not a game. The released Umpire adjudicated each of the 10 `adjudication-corpus-v1` cases 5
times (D-110), run with the owner's approval after the owner reviewed the case keys.

**Setup.** DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) over OpenRouter, no
temperature, no reasoning effort, 4 attempts, 60 s timeout; `umpire-v1.3` for the five
rule-free Swedish v3 cases and `umpire-v1.6` for the five cases with scenario rules. Corpus
`47a2eb15…`, locked and committed before the first call (`aa6fe18`).

**What happened.** 50 calls, 0 failures, 0 server-invalid proposals. Transient errors recurred
throughout: several attempts hit the 600 s wall-clock deadline and were retried within the
bound (56 model calls), so the median call took 288 s. About $0.20 in total; 3 attempts carry
no provider cost.

**Result (rated by an AI agent against `rubric.md`, adopted by the owner, D-111).** Semantic violation rate 0.28: `umpire-v1.3` 0.12,
`umpire-v1.6` 0.44. Invalid supersession 13 of 50. Mean quality 4.18. Both Skaro cases
violated in every repetition: a Cabinet decision (`skaro-dc-cabinet-alert`) and Aldmoor's
dispatch (`skaro-cabinet-lifeline`) appeared in success branches. The D-044 case failed in 3 of
5 under `umpire-v1.3`. The injected instruction was not followed. I-156, I-157 filed. This is the
baseline T-069 compares against; it is not a benchmark result about player models.

### Run 21 — 2026-09-27 · adjudication corpus v2 · I-156 released Umpire vs `umpire-v1.7` **complete, rated** · `dev/runs/i156-adjudication-v2-baseline/`, `dev/runs/i156-adjudication-v2-v1-7/`

Not a game. Both conditions of `corpora/adjudication/v2/thresholds.yaml` adjudicated each of the
20 `adjudication-corpus-v2` cases 5 times, run side by side with the owner's approval.

**Setup.** DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) over OpenRouter, no
temperature, no reasoning effort, 4 attempts, 60 s timeout, 8 calls in flight per condition.
Baseline: the released strata, `umpire-v1.3` on the 9 rule-free cases and `umpire-v1.6` on the
11 with rules. Candidate: `umpire-v1.7` pinned on all 20. Corpus `4a10203f…`, locked
(`312e7a0`) before `umpire-v1.7` was written (`bdab905`).

**What happened (mechanical, before rating).** Baseline: 100 calls, 3 failures (every attempt
hit the 600 s deadline), 0 server-invalid, branch inconsistency 4, invalid supersession 25.
Candidate: 100 calls, 1 failure, 0 server-invalid, branch inconsistency 2, invalid
supersession 20. Median call about 440 s in both; about $0.35 and $0.37. Most invalid
supersessions are in two held-out cases with 36–37 active Effects whose keys allow only a few
targets, so the Umpire's routine updates count against it; the measure is locked and applies
to both conditions alike.

**Rating.** One blinded mixed sheet of the 196 usable proposals (seed 20260928), rated by a
fresh-context AI agent given only the sheet and the rubric (D-111's kind of rater). 30 rows on
two claims were re-rated because the review had hidden their entailment notes (I-158, D-113).
A few `umpire-v1.7` rationales echo its prompt wording; the rater saw neither prompt.

**Result (D-113: adopt as an experimental condition).** Semantic violation rate 0.299 → 0.051;
held-out 0.360 → 0.020; cases violating 6 of 20 → 0. Self-binding lost 0.155 → 0.101, mean
quality 3.76 → 4.51, worst per-case regression +1 of 5; every guard passes. Under the released
Umpire three held-out `northern-strait` cases violated in 5 of 5 and the fourth in 3 of 5
(another Actor's acceptance rolled into existence); under v1.7, none. Evidence: `dev/runs/i156-adjudication-v2-rating/summary.json`.

### Run 22 — 2026-09-27/28 · `swedish-government` 7.0.0 · first playtest of the 25 Sep freeze **complete** · `dev/runs/swedish-government-7.0.0-20260927-221639/`

Game `4db6be1e-e2af-4c89-8091-86a0242e0d01`, seed `1790540199`, run class `playtest`, launched with
the owner's approval through `amaw-unattended` using the `guide/operations.md` overnight recipe.

**Setup.**
- Models: DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) for all eight players and the
  Umpire. No consequence resolver.
- Conditions: the released defaults. That is `single_shot` / `player-v1.11`, open Diplomacy,
  open sealed Cons, and `umpire-v1.7`: the first full game under the D-114 default. Concurrency 8,
  low reasoning, temperature 0. Raised runtime ceilings only: 300 s timeout, 30 Messages per
  phase, 12 activations per opportunity.
- Code: `5b1d253`, clean tree.

**What happened.** Complete in 10h40m (22:16–08:57 CEST), exit 0. 32 Arguments and
adjudications, 106 Cons, 672 Messages, 47 Effects (30 active at the end), 169 Resource accesses,
578 AgentRuns (38 Umpire), 877 model calls, 9.3M input and 2.3M output tokens, about $0.94
($0.09 Umpire). 35 agent errors: 12 player connection drops, 6 malformed player outputs, 6 Umpire
failures that a retry recovered, 11 no-progress recoveries in Diplomacy (I-161). Diplomacy took most
of the time: 52, 102, 32 and 41 minutes per turn.

**Result.** Magdalena Andersson (S) is elected statsminister on 28 September; an S+MP+C
government forms, with V supporting from outside by abstaining. The no-vote count M+KD+L+SD is 173
against the 175 threshold. SD goes into opposition; M, KD and L keep the Tidö bloc together as a
ready alternative and keep a line to C open. Run 8 (v6, a different state of play) is not
comparable.

**Observations.**
- **C was the pivot from the first round.** Every Actor's arithmetic began with C's 25 seats. C
  held a single line throughout — a centrist government with neither V nor SD in it — and
  ended in government. The Tidö side never resolved SD's demand for cabinet posts against L's
  and KD's view that only a government without SD could win C.
- **The contest was over V's abstention.** In turns 2 and 3 the Tidö parties won "the red-green
  construction is not secured" four times (KD, M and SD in turn 2, M in turn 3) and S's and MP's Arguments to lock it failed four
  times. V's own turn 3 Argument (written sign-off and abstention) succeeded and decided the game.
  Turn 4 was ratification: all 8 Arguments succeeded with 7 Cons in total.
- **The Umpire never rolled (I-160).** All 32 adjudications were automatic: 24 successes, 8
  failures. Every outcome was a judgement, none a draw.
- **Diplomacy closed through the guardrail (I-161).** Turns 1, 2 and 4 ran 13 rounds each
  (165–250 Messages); M, SD, KD and L were recovered to `ready` after 12 activations.
- **A network drop cut a turn short (I-151).** At 02:32 UTC all eight turn 3 Diplomacy
  activations failed at once and were recovered to `ready`: one round, 14 Messages.
- **Language drift.** The scenario is Swedish; Desired Effects and Effects mix Swedish and
  English, sometimes for the same Actor across turns.

**Conclusion.** Version 7.0.0 runs to completion and produces a coherent formation result. It
is a playtest (§4.1), not a forecast. Its adjudication (no rolls) and its Diplomacy (closed by the
ceiling, one turn cut by a drop) mean it should not serve as a baseline until I-160 and I-161 are
settled.

### Run 23 — 2026-09-28 · `northern-strait` · I-160 Umpire comparison, v1.3 / v1.6 / v1.7 **complete** · `dev/runs/i160-northern-strait-umpire-v1{3,6,7}-*/`

Three games run concurrently, identical except for the pinned Umpire prompt, with the owner's
approval. Games `107157f9…` (v1.3), `f1d01cc5…` (v1.6) and `04b7bb63…` (v1.7).

**Setup.** DeepSeek v4 Flash (`deepseek/deepseek-v4-flash-0731`) for all three players and the
Umpire; seed 424242 (the scenario's own) for all three, so every game drew the same roll
sequence; released conditions (`single_shot` / `player-v1.11`, open Diplomacy, open sealed Cons);
temperature 0, low reasoning, 300 s timeout; `playtest`. Code `b06690d`. The three processes
shared `data/amaw.db` in SQLite's default journal mode.

**What happened.** All three completed six turns, exit 0, in 1h49m (v1.7), 2h02m (v1.3) and
2h16m (v1.6); 115–154 Messages, 14–20 Cons, $0.13–$0.18 each. No lock errors from sharing the
database, no failed activations; v1.6 had two no-progress recoveries in turn 3 Diplomacy. Model
calls took 30–300 s each, which is why a game took about 2 hours against 73–79 minutes in run 11.

**Result.**

| Umpire | Probabilistic | Automatic success | Automatic failure | Succeeded | Probabilities |
|---|---|---|---|---|---|
| `umpire-v1.3` | 5 | 11 | 2 | 14 of 18 | 0.15–0.80 |
| `umpire-v1.6` | 7 | 8 | 3 | 8 of 18 | 0.20–0.80, five at 0.20–0.25 |
| `umpire-v1.7` | 3 | 15 | 0 | 17 of 18 | 0.60–0.65 |

**Observations.**
- **v1.7 is lenient.** Arkania won all six Arguments automatically. On the shared first roll
  (0.504), v1.3's 0.35 failed and v1.7's 0.65 succeeded.
- **v1.6 is harsh.** It failed the first ten Arguments; nothing succeeded before turn 4. Its two
  high probabilities (0.70, 0.80) both lost to high rolls.
- **v1.3 rolls less than it used to.** 5 of 18 against 12–13 in runs 3–15. Those runs used
  `tool_loop` / `player-v1.10`, so the historical baseline is not directly comparable.
- **All three end in ratification.** From turn 4 every game is automatic successes, as before
  (Patterns).
- **The comparison diverges after the first ruling.** Once outcomes differ, the players argue
  differently, so the later Arguments are not the same across games.

**Conclusion.** The largest shift is between v1.6 and v1.7, toward success and away from rolls.

**Reading the rationales reverses the first impression.** Almost every v1.3/v1.6 roll was on
whether another played Actor would endorse or accept (I-156's defect, live), and v1.6's harsh
0.20–0.25 are those rolls. v1.7 narrows such Arguments to the Actor's own commitment and grants
it: in 10 of 18 rulings its rationale says the core of the Desired Effect was outside the Actor's
authority. v1.7 is not lenient; its "success" means something narrower. The owner kept §23 and
v1.7 (D-118); see I-162…I-164.

*Correction, 2026-09-28 (I-168):* v1.7 is not clean either. Two of its three rolls were on
other played Actors' decisions (turn 2 Cestia, Arkania's and Belvar's acceptance; turn 3
Belvar, Cestia's acceptance). Only turn 2 Belvar's, on an independent verification mission, is
the kind of roll §23 allows. This was first recorded as one case; see I-164.
