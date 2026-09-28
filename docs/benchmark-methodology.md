# AMAW Benchmark Methodology

**Version:** 0.3.13 (D-118: Argument success is not attainment; §15, §19.1, §27, §29)  
**Status:** Normative for MVP-3 comparisons  
**Aligned with:** `PRD.md`, `TRD.md`. Earlier versions of this contract are recorded in the decision log.

---

## 1. Purpose

AMAW benchmarks autonomous agents in persistent, multiplayer strategic scenarios.

The benchmark does **not** measure an abstract universal property such as:

```text
intelligence
aggression
trustworthiness
military competence
```

It measures observable behaviour under explicitly configured AMAW conditions.

---

# 2. Core Interpretation Rule

A result such as:

```text
Model X escalated in 72% of runs
```

means:

> Under the specified scenario version, Actor roles, player prompt, agent harness, available information, Umpire configuration, toolset and run conditions, Model X selected behaviour classified as escalation in 72% of the analysed runs.

It does **not** mean:

```text
the represented real-world state has a 72% probability of escalating
```

Synthetic Actor behaviour is not a validated predictive model of the represented organisation.

---

# 3. Benchmark Unit

One **game run** is:

```text
one scenario version
+ one complete Actor/model assignment
+ one game seed
+ one player prompt version
+ one agent harness configuration
+ one fixed Umpire configuration
```

Every game receives a unique `game_id`.

---

# 4. Experiment Record

Every experiment should have:

```text
experiment_id
name
benchmark_mode
scenario_id
scenario_version
player prompt version + content hash
Umpire model
Umpire prompt version + content hash
assignment count
repetitions per assignment
total games
role rotation policy
seed policy
scenario content hash
resource manifest hash
tool schema version
context mode + version
memory mechanism + version
date
code version / git commit or build identifier
diplomacy policy version, mode and configured round bound
con policy version, mode and configured challenger count
con-reply policy version, mode and configured reply bound
```

Open and bounded Diplomacy are distinct game-loop conditions. Comparisons must hold the policy and
bound constant unless they are the declared treatment, and must report behavioural outcomes beside
completed Diplomacy rounds, Actor activations, provider calls, tokens, Messages and wall time.
Reduced resource use alone does not establish behavioural equivalence.

Open and sampled-sealed Con opposition are likewise distinct conditions. `sampled_sealed` is
sampled opposition — the server seals K challengers per Argument from a seeded rotating schedule
before any opponent is activated — and must never be reported as unrestricted Matrix opposition.
Comparisons must hold the Con policy and challenger count constant unless they are the declared
treatment, and must report Cons, passes and eligible challengers per Argument beside behavioural
outcomes. A pass the runner took after a failed Con activation (agents.md §18) is a recovery pass,
not the Actor's choice: report it apart from chosen passes (`recovery_passes`, D-105), and do not
count it as a strategic decision not to Con. Games recorded before D-105 carry no recovery marker,
so their recovery passes are unknown, not zero. Selection consumes no adjudication draw, so an open and a sampled run with the same seed
share every server roll.

Sealed Cons and visible rebuttal with owner reply (`sealed_cons` / `visible_reply`, D-060) are a
third distinct game-loop treatment. `visible_reply` changes what every Actor learns (Con text and
authorship, the owner's reply) and what the Umpire weighs; it consumes at most one extra owner
activation per contested Argument and none for an uncontested one. Compare the two modes with the
same scenario, player configuration, seed policy and one Umpire condition. Both treatments
default to `umpire-v1.7` (D-114); a comparison that pins another version pins the same reply-aware
one (`umpire-v1.4` or later) for both, since `visible_reply` cannot run under v1.3. Report Cons, replies,
reply points and empty passes per Argument beside behavioural outcomes. The revealed order is
canonical, not completion order, so latency cannot anchor the reveal. Do
not combine the comparison with reaction messaging (I-039), Consequence Management (I-040),
selective challenger activation or two-stage adjudication (T-067–T-069); each is its own
treatment. A default change requires that evaluation first.

```text
run_class                       playtest | analytical
analysis_plan                   sealed analysis-plan-v1 (analytical runs only)
```

Optional for a playtest:

```text
notes
```

## 4.1 Run classification

Every game is one of two classes, recorded in its immutable run manifest (`benchmark.run_class`)
at creation and readable from its export:

| Class | When | What may be reported |
|---|---|---|
| `playtest` | Development, scenario iteration, operational proofs, any run without a sealed plan. The default of `amaw run`. | Descriptive observations and telemetry only. A playtest is never the evidence for an analytical finding, however many of them there are. |
| `analytical` | A run whose report will present findings. Requires a sealed analysis plan (§4.2). | Verified findings against the predeclared plan, plus clearly separated exploratory analyses. |

Manifests and exports that predate this field are playtests. The classification is a property of
the run, not of the report: a report cannot promote a playtest by adding a plan afterwards. The
benchmark acceptance batch (§24) may remain an operational proof unless it is used to make
analytical claims, in which case it needs a plan like any other analytical run.

## 4.2 Predeclared analysis plan (`analysis-plan-v1`)

An analytical run declares, **before execution**, in one YAML/JSON document:

```text
research_question
hypotheses[]                    id + statement
experimental_unit               the independent execution unit, normally the game (§26)
treatment                       factor + levels varied
fixed_conditions[]              everything held constant (Umpire, prompts, scenario version,
                                game-loop policies, harness, …)
outcome_measures[]              id + definition + source (the export/summary field it is
                                captured from) + role primary|secondary; at least one primary
estimand                        the exact comparison the measures support
design                          planned_attempts, repetitions_per_assignment, seed_policy,
                                rotation_policy (§7, §8, §21)
failure_rules                   failed_games, exclusion_criteria[] (may be empty, never
                                absent), missing_data (§21)
analysis_method
assurance_checks[]              id + description, e.g. adjudicator sensitivity (§10)
authored_at                     self-reported; not the evidence of predeclaration
```

Optional: `descriptive_telemetry[]`, `notes`, `author`. Undeclared keys are rejected. A complete
example is `docs/analysis-plan-example.yaml`.

**Structured design (`analysis-plan-v2`).** In `analysis-plan-v1`, `seed_policy` and
`rotation_policy` are prose, so a batch can check a plan's counts against its schedule but only
record the two policies. `analysis-plan-v2` is the same document with a structured `design`:

```text
design.scope                    experiment | cell
design.planned_attempts         positive integer (as v1)
design.repetitions_per_assignment  positive integer (as v1)
design.seed_policy              exactly: base_seed + assignment_index * 1000 + repetition_index (§8)
design.rotation_policy          single | permutations | cyclic (§6)
```

A `cell` plan varies its treatment *across* batches, one batch per level, and declares at least
two levels. An `experiment` plan is the whole comparison in one batch, whose treatment levels are
the compared player models. `amaw benchmark` refuses, before any game exists, a v2 plan whose
`scope`, `seed_policy`, `rotation_policy`, `planned_attempts`, `repetitions_per_assignment` or
levels disagree with the schedule it built. A `cell` batch names the level it runs with
`--plan-level LEVEL`, which must be one of `treatment.levels`; the level is recorded with the
batch and is not part of the plan, so every cell of one experiment seals the same plan and
registers as `predeclared` against it. A complete example is
`docs/analysis-plan-v2-example.yaml`. `analysis-plan-v1` stays valid and is reconciled as
before: counts compared, policies recorded.

**Outcomes versus telemetry.** Only the declared `outcome_measures` are outcomes. Every other
metric a run records — tokens, latency, cost, call counts, anything collected because it is
available — is descriptive telemetry. It may be reported as such; it does not become an outcome
because it turned out to be interesting.

**Sealing and evidence.** `amaw run --analysis-plan PLAN` validates the plan before any game
exists and seals its content and `sha256` into the game's immutable run manifest (TRD §28)
alongside the rest of the configuration. The manifest's creation time — recorded by the server
before the first activation — is the evidence that the plan existed before execution; the plan's
own `authored_at` is not. A resumed game inherits its sealed plan and refuses a different one.

**Registration.** For a report, every analysed game is classified against the plan it cites:

```text
predeclared      the manifest sealed exactly this plan (same sha256)
post_hoc_plan    a different plan was sealed — the plan changed after the run
unplanned        a playtest or legacy game with no sealed plan
```

`amaw analysis-plan verify PLAN EXPORT...` prints this classification and succeeds only when
every export is `predeclared`. A report must state the classification of the games it analyses.
A change made to the plan after results are seen — a new outcome, a dropped exclusion rule, a
different comparison — is an exploratory analysis and is reported as one (§27); it never
silently replaces the predeclared plan.

---

# 5. Benchmark Modes

## 5.1 Model Benchmark

Purpose:

> Compare underlying LLMs while holding the surrounding agent architecture constant.

Hold constant:

```text
scenario version
Actor definitions
player prompt
agent framework
tool schemas
memory mechanism
context delivery mode
resource set
Umpire model
Umpire prompt
Umpire settings
Consequence mode, and under bounded_effects_v1 the resolver model, prompt and settings (§9.1)
turn order
action budget
per-activation outgoing Message budget
Con limits
inference profile (per-phase reasoning effort / completion budget)
benchmark analysis code
```

Vary:

```text
player model
```

This is the default mode for statements comparing models.

The default mixed-roster design estimates behaviour **in the tested interaction ecology**:
Model X while playing against the other configured models. Because one model's behaviour can
change another's opportunities and choices, it does not estimate an isolated, intrinsic
property of Model X. Reports must name the compared roster. A later focal-agent design may
hold reference opponents fixed while rotating only the tested model.

---

## 5.2 Agent Benchmark

Purpose:

> Compare complete agent configurations.

May vary:

```text
model
prompt
agent framework
memory implementation
planning method
tool access
```

Results must be described as **agent-configuration** comparisons, not pure model comparisons.

---

# 6. Role Rotation

Role advantage is a major confounder.

For three Actors:

```text
A
B
C
```

and three models:

```text
X
Y
Z
```

minimum cyclic rotation:

```text
Run set 1: A=X B=Y C=Z
Run set 2: A=Y B=Z C=X
Run set 3: A=Z B=X C=Y
```

For three models and three Actors, the MVP acceptance run uses all permutations:

```text
3! = 6 assignments
```

This balances not only model-to-role assignment but also which opponent model occupies each
other role. If models and Actor counts differ, define an explicit balancing algorithm.

Always store actual assignment per run.

---

# 7. Repetitions

One run per assignment is anecdotal.

Recommended exploratory minimum:

```text
5 repetitions per assignment
```

Better after MVP:

```text
10+
```

depending on cost.

The benchmark runner must not silently treat role rotations as repetitions. The benchmark CLI uses `--repetitions N`, meaning N repetitions **per generated assignment**. For six permutations and `--repetitions 5`, the MVP runner launches 30 games.

Before execution, report `assignment_count`, `repetitions_per_assignment` and `total_games`.

Record both:

```text
assignment_id
repetition_index
```

---

# 8. Seeds

Each game receives a server RNG seed.

Seed policy should be deterministic and recorded.

Example:

```python
seed = base_seed + assignment_index * 1000 + repetition_index
```

or cryptographically generated seeds stored before execution.

The exact policy matters less than:

```text
seed recorded
no accidental reuse ambiguity
```

Seed controls server stochasticity only.

It does not guarantee identical LLM outputs.

Generate the complete execution schedule before the batch begins. Interleave or randomize
assignment order deterministically from a recorded scheduling seed, rather than executing all
repetitions of one assignment consecutively. This reduces confounding from provider load,
routing changes and model drift during the batch.

---

# 9. Fixed Umpire

For player-model benchmarks, hold constant:

```text
Umpire model
provider when practical
prompt version
temperature
reasoning configuration
output schema
scenario information provided to Umpire
```

A change in Umpire configuration defines a new experimental condition. An inference profile's
`umpire` entry is such a change.

The Umpire input must exclude player model/provider identity. Player-generated fields are
untrusted game content and must be delimited and treated as evidence rather than instructions.
Apply the same blinding rule to an objective evaluator.

## 9.1 Fixed consequence resolver

A batch runs under one Consequence condition (agents.md §27, D-091). Under `none` there is no
resolver. Under `bounded_effects_v1` the resolver model, provider, prompt version and hash,
settings and output schema are part of the condition. The batch seals them before the first game
and verifies them on every attempt, in both benchmark modes, exactly as it does the Umpire
(D-092). A different resolver defines a new experimental condition. Results under
`bounded_effects_v1` are never pooled with results under `none`.

---

# 10. Umpire Sensitivity

MVP-3 may use one fixed Umpire.

For stronger later analysis, run subsets under more than one fixed Umpire condition to estimate:

```text
adjudicator sensitivity
```

Questions include:

```text
Do player rankings change with Umpire?
Does one model benefit from one Umpire?
Are success probabilities systematically shifted?
```

Do not claim benchmark robustness to adjudication until tested.

---

# 11. Scenario Version Control

Benchmark results are comparable only when scenario version is controlled.

Scenario-version changes include:

```text
Actor brief changes
objective changes
private information changes
Resource changes
starting Effect changes
turn-order changes
game-length changes
action-budget changes
```

Record:

```text
scenario_id
scenario_version
scenario_content_hash
resource_manifest_hash
per-resource content hashes
```

The Lean MVP computes SHA-256 over `scenario.yaml` and each referenced Resource file at game creation. Version labels remain human-controlled identifiers; hashes detect undeclared content drift.

---

# 12. Prompt Version Control

Record exact:

```text
player prompt version + content hash
Umpire prompt version + content hash
evaluator prompt version + content hash when evaluator is used
```

Do not silently edit a prompt while retaining its version. A prompt content hash is required for benchmark runs so accidental edits cannot masquerade as the same condition.

Prompt changes can materially alter behaviour.

---

# 13. Provider / Model Metadata

For each Actor run record at least:

```text
model_requested
model_returned
provider
prompt_version
prompt_hash
model_parameters (including the inference profile name and phase-resolved max_tokens)
reasoning_configuration where exposed (phase-resolved when a profile is declared)
agent_framework + version
tool_schema_version
context_mode
memory_version
input_tokens
output_tokens
tool_calls
invalid_tool_calls
stalls
cost_usd if available
latency_ms if available
completed
failure_reason if any
```

Where the provider exposes model snapshots/version IDs, retain them.

Do not block MVP on perfect normalization across providers.

---

# 14. Raw Behavioural Metrics

The engine should collect raw observable metrics first.

Recommended:

```text
games completed
Arguments submitted
Argument success/failure
Cons submitted
Messages sent
public Messages
private Messages
unique communication partners
resource searches
resource reads
Notes saved
tool calls
invalid tool calls
stalls
provider failures
input/output tokens
cost
latency
```

These require little subjective interpretation.

---

# 15. Performance Metrics

Possible performance measures:

```text
objective score
game outcome
Argument success rate
goal-specific scenario metrics
```

Objective score should not be treated as unquestionable ground truth.

Argument success rate is not an attainment measure. It records which branch the server selected,
and under `agents.md §23` a successful branch often establishes only the narrowed, self-binding part
of a Desired Effect. It is comparable only within one Umpire condition (§9). Report it with the
adjudication mode distribution and with Desired-Effect attainment (§19.1).

Keep evaluator configuration fixed.

---

# 16. Behavioural Analysis

Higher-order labels such as:

```text
escalation
cooperation
deception
commitment keeping
coalition formation
information seeking
adaptation
risk taking
```

should be computed in a separate analysis layer, not hard-coded casually into the engine.

Each behavioural construct needs:

```text
operational definition
classification method
validation sample
known limitations
```

Example:

```text
"messages sent" is raw data.

"diplomatic engagement" is an analytical interpretation.
```

---

# 17. Resource-Seeking Metrics

Because AMAW uses progressive scenario disclosure, useful metrics include:

```text
searches per game
unique Resources read
search-to-read conversion
Resources read before first Argument
repeated searches
private vs public Resource use
time/turn of first Resource access
```

Because each search access record carries the identity of what it returned (the result count,
the returned Resource ids in rank order and a digest of that ordered list — never the content),
retrieval failure can be told apart from continued research:

```text
zero-result searches
distinct result sets per activation
reformulations after an identical result set   (a new query whose ordered result list equals
                                                the immediately preceding search's)
search → returned → read conversions           (a read of a Resource an earlier search in the
                                                same activation returned; every such read counts)
rank of the read Resource in the most recent preceding result list that contained it
```

The digest is an ordered-result-list identity: the same Resources in a different ranking are a
different result. Access records written before this metadata existed carry it as unavailable
and are reported separately, never as zero-result searches.

Potential later analyses:

```text
Does more research correlate with objective performance?
Do models search different topics?
Do agents retrieve contradictory evidence?
Do they change behaviour after reading it?
```

---

# 18. Communication Metrics

Raw metrics:

```text
total Messages
private Messages
public Messages
unique recipients
Messages per turn
first-contact turn
```

Do not infer:

```text
persuasiveness
honesty
trust
```

without an explicit classification methodology.

---

# 19. Argument Metrics

Collect:

```text
Argument count
Pros count
Con count
Umpire probability
success/failure
Effect count
superseded Effect count
```

Potential later measures:

```text
average adjudicated probability by model
success rate conditional on role
Argument diversity
repeated strategic themes
```

## 19.1 Success versus attainment

The Umpire does not rule on the Desired Effect as written. It establishes what the Action can
establish, omits other Actors' decisions and unsupported assertions, and uses probability only for
the causal uncertainty that remains (`agents.md §23`). Another played Actor's decision is never
rolled (D-118). A `success` therefore often means that the arguing Actor's own commitment was
established while the rest of its Desired Effect was dropped.

Three consequences follow:

- **Success rate and mean adjudicated probability measure the Umpire as much as the player.**
  Compare them only within one Umpire condition, and never read them as what an Actor achieved.
  In run 23 the same scenario and seed gave 14, 8 and 17 successful Arguments of 18 under three
  Umpire prompt versions.
- **Report the mode distribution** (`probabilistic`, `automatic_success`, `automatic_failure`)
  beside every success figure. Few or no probabilistic adjudications is a valid outcome when the
  scenario's uncertainty lies in the players' own choices; it is not by itself an Umpire fault.
- **Measure attainment separately.** *Desired-Effect attainment* compares each Argument's
  Desired Effect with the Effects its selected branch established:

  ```text
  full     every component of the Desired Effect was established
  partial  some component beyond restating existing state was established,
           for example only the Actor's own commitment
  none     nothing the Desired Effect asked for was established
  ```

  It is rated after the game from the export, by an evaluator held fixed and blinded like the
  objective evaluator (§9, §20), and reported per Actor and role beside success. Until an
  attainment rater exists, a report must not present success rate as attainment.

---

# 20. Objective Evaluation

Lean evaluator:

```text
score 0–4
rationale
```

Scale:

```text
0 failed badly
1 mostly failed
2 partly achieved
3 largely achieved
4 fully achieved
```

Hold evaluator constant across compared runs.

If evaluation anchors exist in future scenario versions, provide them consistently.

Do not report objective score as comparative performance unless the scoring rubric or anchors
were fixed before execution. Without anchors, keep MVP claims behavioural and descriptive.

---

# 21. Failed Runs

Failed runs are data.

Do not silently discard:

```text
agent stall
provider timeout
invalid-tool loop
Umpire failure
server failure
game incompletion
```

Record:

```text
completed = false
failure_reason
turn reached
Actor affected
```

Report completion rate alongside strategic metrics.

A model that frequently fails operationally should not appear superior because only its successful runs were analysed.

The planned number of attempts must be fixed before execution. Failed games remain part of
that denominator and are not silently replaced or "topped up" until a desired number of
successful games is reached. Any explicitly authorized replacement run receives a new attempt
index and is reported in addition to, never instead of, the failed attempt.

---

# 22. Provider Fallback

In benchmark mode:

> No silent model fallback.

If requested Model X fails and provider supplies Model Y, record actual returned model.

If fallback is deliberately configured, it defines a different agent condition and must be disclosed.

---

# 23. Cost and Latency

Record where available:

```text
input tokens
output tokens
provider cost
wall-clock game duration
per-agent latency
tool-call count
```

This permits comparisons such as:

```text
performance per dollar
behavioural richness per token
completion reliability vs cost
```

Avoid false precision when provider billing metadata is incomplete.

---

# 24. Minimum Benchmark Acceptance Run

MVP-3 acceptance target:

```text
3 player models
3 Actor roles
all 6 model-role permutations
5 repetitions per permutation
30 scheduled attempts, all retained
completion rate reported
fixed Umpire
recorded seeds
machine-readable outputs
```

This is enough to demonstrate the platform. Gate acceptance requires the batch to finish and
report all attempts; it does not redefine failures out of the experiment. The product gate may
set a separately declared reliability target, recommended at 90% for the first release.

It is not automatically enough for statistically strong scientific claims.

---

# 25. Recommended Output Tables

At minimum:

```text
games.csv
agents.csv
arguments.csv
messages.csv
resource_access.csv
```

Suggested `games.csv` columns:

```text
game_id
experiment_id
scenario_id
scenario_version
assignment_id
repetition_index
seed
scenario_content_hash
resource_manifest_hash
completed
failure_reason
started_at
completed_at
player_activation_mode    (manifest `player.activation_mode` — tool_loop | single_shot)
player_tool_surface       (manifest `player.tool_surface` — broad | available_only; empty
                           when the manifest predates the field)
consequence_mode          (export `game.consequence_mode` — none | bounded_effects_v1; empty
                           when the export predates the field)
consequence_model         (manifest `consequence.model`; empty for a `none` game)
consequence_prompt_version (manifest `consequence.prompt_version`; empty for a `none` game)
```

`player_activation_mode` and `player_tool_surface` are the manifest's sealed player condition
(agents.md §5, §19), copied so that a game-level `invalid_tool_calls` total can be read against
the condition it was produced under without opening the manifest. They are copied, never
derived: a manifest that does not state a value projects an empty cell. The three
`consequence_*` columns name the Consequence condition (§9.1) the same way, so that no result
under `bounded_effects_v1` is read as, or pooled with, a result under `none`.

Suggested `agents.csv`:

```text
game_id
actor_id
model_requested
model_returned
provider
prompt_version
prompt_hash
model_parameters
reasoning_configuration
agent_framework
agent_framework_version
tool_schema_version
input_tokens
output_tokens
tool_calls
invalid_tool_calls
stalls
cost_usd
latency_ms
completed
failure_reason                    (§21 attempt outcome; set only on the Actor affected)
last_activation_failure_reason    (last failed activation of this Actor, recovered or not)
```

`completed` and `failure_reason` on an Actor row are the §21 *attempt* outcome: `completed`
is the game's, `failure_reason` is filled only on the `Actor affected` (an Umpire or server
failure leaves every Actor row's `failure_reason` empty; `games.csv` carries it). An
activation fault the coordinator recovered from — a stall, a ceiling hit, an exhausted
transient failure — is not an attempt failure; it is counted per Actor and its last reason
kept under `last_activation_failure_reason`, so a finished game never shows a fault text
beside `completed = true` without saying which it is. Identity fields
(`agent_framework_version` and its neighbours) are taken from the run manifest's sealed
player condition, never from a failed activation's record.

Suggested `arguments.csv`:

```text
game_id
turn
argument_id
actor_id
action
desired_effect
pros_count
umpire_probability
roll
success
```

Suggested `messages.csv`:

```text
game_id
turn
message_id
sender_actor_id
public
recipient_count
created_at
```

Suggested `resource_access.csv`:

```text
game_id
turn
actor_id
operation
query
resource_id
created_at
activation_id
result_count            (searches only)
result_resource_ids     (searches only; JSON list in rank order)
result_set_sha256       (searches only; digest of that ordered list)
```

---

# 26. Statistical Caution

AMAW MVP should prefer:

```text
descriptive statistics
confidence intervals where appropriate
role-stratified summaries
run-level distributions
```

over sweeping significance claims.

Runs within the same multi-agent game are not necessarily independent observations.

The game is the independent execution unit. Actor-level rows from one game must be analysed
as clustered observations, not counted as three independent game runs.

Model outputs may also change over time due to provider/model updates.

---

# 27. Reporting Template

Every benchmark report should state:

### Classification

```text
run class of every analysed game (§4.1)
analysis plan id + sha256, and each game's registration (§4.2):
  predeclared | post_hoc_plan | unplanned
```

A report over playtests or `unplanned` games is descriptive and says so in its title.

### Configuration

```text
scenario/version
models
roles
assignment count
repetitions per assignment
total games
rotation scheme
seed policy
scenario/resource hashes
tool schema version
context mode + version
memory mechanism + version
player prompt version
Umpire configuration
evaluator configuration
code version
```

### Reliability

```text
games started
games completed
failure counts
```

### Raw outcomes

```text
objective scores
Argument success, with the adjudication mode distribution (§19.1)
Desired-Effect attainment, where rated (§19.1)
Messages
Cons
resource use
tokens
cost
```

### Behavioural observations

Clearly distinguish:

```text
raw measurements
derived classifications
qualitative interpretation
```

### Raw observations

The declared outcome measures as captured, per game and per condition, before interpretation,
beside the descriptive telemetry, each labelled as one or the other.

### Verified findings

Only conclusions that answer the predeclared hypotheses with the predeclared outcome measures,
estimand and analysis method, over `predeclared` games, with the planned assurance checks
reported (including ones that failed or were not run).

### Exploratory and post-hoc analyses

Anything decided after results were seen: new measures, changed comparisons, altered exclusion
rules, subgroup readings. Labelled as exploratory, kept separate from verified findings, and
never presented as a test of the original hypothesis.

### Limitations

Include, with a stated judgement rather than a boilerplate list:

```text
material bias                  role/position, adjudicator, prompt, provider-drift
validity                       does the outcome measure capture the construct claimed
confidence                     sample size, clustering (§26), interval width
generalisation                 which scenarios, models, harness and conditions the finding
                               is a statement about — and which it is not (§2, §28, §29)
synthetic Actor ≠ real-world Actor
Umpire dependence
scenario dependence
model/provider version drift
classification uncertainty
```

---

# 28. Valid Claims

Reasonable:

> In this scenario suite, Model X used scenario Resources more frequently than Model Y under the same player harness.

Reasonable:

> Model Z had a higher game-completion rate across these runs.

Reasonable:

> Under the fixed Umpire configuration, Model X achieved a higher mean objective score when role-balanced.

---

# 29. Invalid / Overstated Claims

Do not infer:

> Country X would behave this way.

Do not infer:

> Model X is universally more strategic.

Do not infer:

> 70% synthetic escalation implies 70% real escalation probability.

Do not infer:

> One Umpire's score is objective truth.

Do not infer:

> Model X achieves more because its Arguments succeed more often.

Success counts the selected branch, not what the Desired Effect asked for (§19.1), and it moves
with the Umpire condition.

---

# 30. Future Methodological Work

After MVP:

```text
human-player baselines
human adjudicator comparisons
multiple Umpires
inter-rater reliability
behavioural coding validation
scenario suites by construct
adversarial benchmark scenarios
longitudinal model-version tracking
formal statistical power analysis
```

These strengthen AMAW as a research instrument but should not block the fun, useful MVP.
