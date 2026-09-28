# AMAW Test and Acceptance Specification

**Version:** 0.3.11 (D-114: the I-046 acceptance states the new default Umpire prompt)  
**Status:** Normative for Lean MVP  
**Aligned with:** `PRD.md`, `TRD.md`. Earlier versions of this contract are recorded in the decision log.

---

## 1. Purpose

AMAW must be correct **before** it is interesting.

The engine should be tested primarily without LLMs.

A passing AI demo does not compensate for broken visibility, duplicated actions or incorrect state transitions.

### 1.1 Test IDs

The IDs headed in this document (`TEST-ARG-008`) and the ranges it declares (`TEST-I053-001`…
`TEST-I053-012`, §26–§29) are the release-blocking contract. Tests also cite issue- and
task-scoped IDs that this document does not declare, such as `TEST-I092-003` or
`TEST-T080-001`. Those are maintainer traceability, not part of this contract. The carrying
test's name and docstring state the property it proves, so a reader needs no other record to
understand it.

---

# 2. Test Layers

AMAW uses four test layers:

```text
1. Unit tests
2. Deterministic engine integration tests
3. API integration tests
4. AI smoke / benchmark tests
```

Only layer 4 depends on external LLM providers.

---

# 3. Test Environment

Use:

```text
pytest
temporary SQLite database
deterministic scenario fixtures
FakeUmpire
ScriptedPlayers
fixed RNG seeds
```

Tests must not depend on execution order.

External-provider tests should be separately marked, for example:

```python
@pytest.mark.llm
```

Default CI should be able to run without an OpenRouter key.

---

# 4. Canonical Test Scenario

Create one small deterministic fixture:

```text
tests/fixtures/deterministic-game/
```

Requirements:

```text
3 Actors
6 turns
1 primary Argument per Actor/turn
private Messages
private Resources
public Resources
starting Effects
fixed turn order
known FakeUmpire results
fixed RNG seed
```

This fixture is the authoritative engine integration scenario.

Do not casually edit it without updating expected outputs.

---

# 5. Unit Test Catalogue

## TEST-SCN-001 — Valid scenario loads

Given:

```text
valid scenario.yaml
```

When:

```text
scenario loader runs
```

Then:

```text
scenario accepted
Actors created correctly
turn order preserved
starting Effects loaded
```

## TEST-SCN-002 — Unknown Actor in turn order rejected

Given:

```yaml
turn_order: [actor_a, actor_missing]
```

Then scenario creation fails before game start.

## TEST-SCN-003 — Duplicate Resource ID rejected

Duplicate Resource IDs must fail validation.

## TEST-SCN-004 — Missing Resource file rejected

A referenced file that does not exist causes scenario validation failure.

## TEST-SCN-005 — Scenario and Resource hashes recorded

Game creation records SHA-256 for `scenario.yaml` and every referenced Resource file in the immutable run/resource manifest. Recreating a game from unchanged bytes produces the same hashes; changing any referenced content changes the corresponding hash.

## TEST-SCN-006 — Authoritative starting Effects contain no unresolved markers

Reject a scenario whose starting Effect contains a case-insensitive standalone authoring marker
such as `TODO`, `TBD`, `FIXME`, `FYLL_I...` or `<placeholder>`. Resources and explanatory prose
may discuss authoring markers; this check protects authoritative initial state.

---

# 6. Authentication Tests

## TEST-AUTH-001 — Valid Seat token resolves Actor

Token maps to exactly one game + Actor.

## TEST-AUTH-002 — Invalid token rejected

Expected:

```http
401 invalid_token
```

## TEST-AUTH-003 — Request Actor ID cannot impersonate another Actor

If Actor A includes:

```json
{"actor_id": "actor_b"}
```

identity remains Actor A or request is rejected.

## TEST-AUTH-004 — Seat token cannot call admin endpoint

Expected:

```http
403 forbidden
```

---

# 7. Message Visibility Tests

## TEST-VIS-001 — Public Message visible to all

Given Actor A sends public Message.

Then A, B and C can read it.

## TEST-VIS-002 — Private Message visible to sender and recipient

Given A sends private Message to B.

Then:

```text
A sees it
B sees it
C does not
```

## TEST-VIS-003 — Private Message does not leak indirectly

When C reads:

```text
messages
events
context
counts
```

the Message must not appear through any payload/snippet/count that reveals its existence.

Actor-facing Event IDs/cursors must also reveal no gap for that Message. Interleaved hidden Events
and Events from another game do not advance the Actor-visible cursor. The visible cursor remains
stable as later Events append.

---

# 8. Resource Visibility Tests

## TEST-RES-001 — Public Resource searchable by all

All Actors can discover/read it.

## TEST-RES-002 — Private Resource visible only to authorised Actor

Given Resource visible only to A:

```text
A search → may return it
A read → succeeds
B search → cannot reveal it
B direct read by guessed ID → not_found/forbidden-safe
```

## TEST-RES-003 — Hidden Resource does not affect result count

If B searches a term matching only A's hidden Resource:

```text
result count == 0
```

Do not return:

```text
1 hidden result
```

or equivalent.

## TEST-RES-004 — Hidden metadata does not leak

No hidden:

```text
ID
name
tags
path
snippet
metadata
```

is exposed.

---

# 9. Note Visibility Tests

## TEST-NOTE-001

A Note created by Actor A is visible to A.

## TEST-NOTE-002

The Note is invisible to B and C.

## TEST-NOTE-003 — Administrative export includes Notes

The administrative game export includes Actor Notes with Actor ownership and visibility metadata. Notes remain inaccessible through another Actor's Seat-authenticated endpoints. The export must not claim or require hidden model chain-of-thought.

---

# 10. Argument Tests

## TEST-ARG-001 — Active Actor can submit

Given:

```text
phase = argument
active Actor = A
```

A valid Argument succeeds.

## TEST-ARG-002 — Wrong Actor rejected

B attempts primary Argument while A active.

Expected:

```http
409 not_active_actor
```

## TEST-ARG-003 — Wrong phase rejected

A attempts Argument during diplomacy.

Expected:

```http
409 wrong_phase
```

## TEST-ARG-004 — Empty action rejected

Expected validation failure.

## TEST-ARG-005 — Empty desired Effect rejected

Expected validation failure.

## TEST-ARG-006 — Zero Pros rejected

Default requires at least one Pro.

## TEST-ARG-007 — Too many Pros rejected

Default maximum:

```text
5
```

## TEST-ARG-008 — Argument budget enforced

With budget `1`:

A's second accepted primary Argument in same turn must fail.

---

# 11. Con Tests

## TEST-CON-001 — Opponent Con accepted

Current Argument belongs to A.

B submits valid Con.

Success.

## TEST-CON-002 — Self-Con rejected

A attempts Con against own Argument.

Reject.

## TEST-CON-003 — Con limit enforced

With max `3`, fourth Con by same Actor is rejected.

## TEST-CON-004 — Pass closes participation

After B passes, B cannot submit another Con.

## TEST-CON-005 — Stale Argument rejected

Con against a previously resolved Argument is rejected.

---

# 12. Phase Transition Tests

## TEST-PHASE-001 — Game starts in diplomacy

After admin start:

```text
status = running
phase = diplomacy
turn = 1
```

## TEST-PHASE-002 — Diplomacy closes

When all Actors ready:

```text
phase → argument
active_actor_id → first turn-order Actor
```

## TEST-PHASE-003 — Private Message clears diplomacy readiness

Given Actor B is ready during diplomacy and Actor A successfully sends a new private Message addressed to B before phase closure:

```text
B.ready → false
phase remains diplomacy
```

The readiness reset occurs before evaluating whether all Actors are ready.

## TEST-PHASE-004 — Argument opens Cons

Accepted Argument:

```text
phase → cons
```

## TEST-PHASE-005 — Cons completion opens adjudication

When all eligible Actors pass/complete:

```text
phase → adjudication
```

## TEST-PHASE-006 — Adjudication advances Actor

After Actor A resolution:

```text
next Actor becomes active
phase → argument
```

## TEST-PHASE-007 — Final Actor advances turn

After final Actor resolution/consequence step:

```text
turn += 1
phase → diplomacy
```

## TEST-PHASE-008 — Final turn completes game

After turn `max_turns`:

```text
status = complete
phase = complete
```

---

# 13. Effect Tests

## TEST-EFF-001 — Success Effect created

FakeUmpire success branch creates expected Effect.

## TEST-EFF-002 — Failure Effect created

Failure branch creates expected failure Effect.

## TEST-EFF-003 — Effect persists

An active Effect from turn 1 remains available in turn 2 unless expired/deactivated.

## TEST-EFF-004 — Effect expiry

Effect with configured expiry becomes inactive at expected turn boundary.

## TEST-EFF-005 — Supersession deactivates target

New Effect with:

```text
supersedes=[old_effect]
```

results in:

```text
new active
old inactive
old retained historically
```

---

# 14. RNG Tests

## TEST-RNG-001 — Same seed produces same server draws

Given identical server RNG operation sequence:

```text
seed 12345
```

produces same draws.

## TEST-RNG-002 — RNG recorded

Probabilistic adjudication persists:

```text
probability
roll
result
```

## TEST-RNG-003 — Umpire does not choose roll

Fake/real Umpire output cannot inject authoritative roll.

---

# 15. Adjudication Tests

## TEST-ADJ-001 — Automatic success

Success branch applied without RNG draw.

## TEST-ADJ-002 — Automatic failure

Failure branch applied without RNG draw.

## TEST-ADJ-003 — Probabilistic mode requires probability

Missing probability fails schema validation.

## TEST-ADJ-004 — Probability bounds

Reject:

```text
-0.1
1.1
```

## TEST-ADJ-005 — Double adjudication prevented

Two completion attempts for same Argument produce only one authoritative resolution.

## TEST-ADJ-006 — Invalid superseded Effect rejected

Umpire cannot supersede an Effect that does not belong to the game.

## TEST-ADJ-007 — Missing authority or prerequisites are not probabilistic

Given an Actor asserts another Actor's unestablished commitment or an outcome whose required
authoritative prerequisite is absent, the Umpire policy must omit that unsupported component from
both Effect branches. It must not assign the component a low probability that a lucky roll can
turn into authoritative state.

## TEST-ADJ-008 — Self-binding survives external uncertainty

Given an Action contains both a feasible self-binding declaration and an uncertain external
consequence, the Umpire policy must establish the declaration independently: for a probabilistic
proposal it appears identically in success and failure branches. The external consequence may be
branch-dependent.

## TEST-ADJ-009 — Supersession is causal and branch-specific

Only the selected branch's supersession list is applied. A branch may supersede an active
contradictory Effect only when its Action or newly established evidence resolves the earlier
condition; merely asserting the opposite does not qualify.

---

# 16. Idempotency Tests

## TEST-IDEM-001 — Duplicate Message request

Same idempotency key + same payload creates one Message and returns same result.

## TEST-IDEM-002 — Duplicate Argument request

Same key + same payload creates one Argument.

## TEST-IDEM-003 — Key conflict

Same key + different payload returns:

```http
409 idempotency_conflict
```

## TEST-IDEM-004 — Duplicate ready

Repeated same readiness request does not corrupt transition counts.

---

# 17. Concurrency Tests

## TEST-RACE-001 — Simultaneous Arguments

Two near-simultaneous valid-looking primary Argument requests from same active Actor:

```text
exactly one accepted
```

## TEST-RACE-002 — Double phase close

Two phase-close attempts do not advance twice.

## TEST-RACE-003 — Double adjudication callback

Only one adjudication is authoritative.

SQLite transactions/state revalidation are sufficient.

---

# 18. Deterministic Six-Turn Integration Test

Fixture:

```text
ScriptedPlayer A
ScriptedPlayer B
ScriptedPlayer C
FakeUmpire
seed = fixed
max_turns = 6
```

Scripted players must:

- send at least one Message;
- submit one primary Argument per turn;
- submit/pass Cons;
- use at least one private information element.

Expected assertions:

```text
game completes
turn == 6
18 primary Arguments accepted
no Argument budget violations
all expected adjudications exist
RNG sequence matches fixture
final active Effects match fixture
private data never leaked
event sequence structurally valid
```

This is the primary release gate for MVP-0.

---

# 19. API Integration Test

Run FastAPI against temporary database.

Test full flow using HTTP:

```text
create game
authenticate Seats
start
send Messages
ready
verify private-message readiness reset
submit Arguments
submit Cons
pass Con participation
adjudicate through FakeUmpire
complete turn
```

Assert HTTP status codes and response shapes.

---

# 20. AI Smoke Test

Marked external/provider test.

Configuration:

```text
3 autonomous LLM Actor agents
1 fixed LLM Umpire
6 turns
1 fictional scenario
```

Success criteria:

```text
game reaches complete
no human intervention
no server exception
no privacy violation detected
all Actors produce at least one valid Argument
agent failures are recorded
```

Do not require a strategically good game for this test.

This proves autonomous execution.

---

# 21. Scenario Knowledge Acceptance Test

Scenario contains:

```text
public background
private Resource for A
private Resource for B
CSV Resource
map/image attachment
critical information absent from initial brief
```

At least one AI Actor must successfully:

```text
search_resources
read_resource
use retrieved information in later behaviour
```

Security assertions remain release-blocking.

---

# 22. Benchmark Batch Smoke Test

Use low-cost/test models or mocked agents where possible.

Run:

```text
multiple games
role rotation
multiple seeds
configured repetitions per assignment
```

Verify:

```text
failed run does not abort batch
AgentRun metadata recorded
run configuration manifest recorded
scenario/Resource/prompt hashes recorded
role assignment recorded
assignment_id and repetition_index recorded
total games == assignment_count * repetitions_per_assignment
seed recorded
CSV/JSON outputs produced
fixed Umpire metadata constant
```

---

# 23. Acceptance Gates

## Gate 0 — Foundation

Pass when:

```text
fresh checkout installs
pytest runs
scenario fixture loads
temporary database works
```

## Gate 1 — Engine Proven

Pass when deterministic six-turn integration test passes.

No external model required.

## Gate 2 — Autonomous Game Proven

Pass when three LLM agents complete six turns without human intervention.

Before proceeding to the paid acceptance batch, manually inspect 3–5 autonomous games using
a recorded checklist: pursuit of private objectives, communication affecting later actions,
non-repetitive Arguments, coherent Effects/adjudications, and acceptable stall/invalid-call
rates. This is an MVP product-value gate, not a claim of scientific validation.

## Gate 3 — Scenario Knowledge Proven

Pass when generic Resource search/read works with strict visibility.

## Gate 4 — Benchmark Proven

Pass when at least:

```text
3 models
3 roles
all 6 model-role permutations
5 repetitions per assignment
30 scheduled attempts, all retained
completion rate reported (90% recommended release target)
machine-readable outputs with immutable run provenance
```

are produced.

---

# 24. Test Data Retention

Persist failed AI run artifacts useful for debugging:

```text
game export
agent error
provider error category
last legal context
tool error
model metadata
```

Do not persist API keys or bearer tokens.

---

# 25. Non-Goals of the Test Suite

Lean MVP does not need tests for:

```text
enterprise SSO
distributed workers
PostgreSQL failover
Kubernetes
WebSockets
MCP
human UI
organisation hierarchies
event-driven activation
Secret Arguments
full replay
vector search
```

Add tests when those features become actual scope.

---

# 26. I-053 Fixed Diplomacy-Round Condition

Offline tests must prove `open_diplomacy` compatibility; deterministic one- and N-round bounded
closure; natural all-ready closure before the bound; final-round private-Message persistence with
no extra round; exactly-once concurrent phase transition; per-turn counter reset; explicit invalid
configuration failures; immutable manifest, AgentRun, export and summary provenance; additive
legacy schema compatibility; restart-safe completed-round progress; and no completed-round charge
for a failed/unsettled activation (`TEST-I053-001`…`TEST-I053-012`).

---

# 27. I-055 Sampled-Sealed Con Opposition Condition

Offline tests must prove `open_cons` compatibility (no challenger list persisted, every opponent
awaited); sealing of exactly K challengers inside the Argument-acceptance transaction with
`con_challengers_selected` between `argument_submitted` and `phase_started(cons)`; a
seed-deterministic rotating schedule whose exposure spread stays within two selections over a
game; non-selected opponents rejected on `/cons` and `/pass` with neither action listed and
`requires_input` false; closure on the sealed set only; no adjudication draw consumed and an
idempotent replay returning the sealed selection; a restart-safe cursor and resume that continues
the schedule and refuses a differing condition; explicit invalid-configuration failures including
K above the opponent count; configuration, manifest, resume recipe, AgentRun, export-v9 and
summary provenance with additive legacy schema compatibility; and an autonomous runner that spends
no Con activation on a non-selected opponent (`TEST-I055-001`…`TEST-I055-010`).

---

# 28. I-047 Predeclared Analysis Plan for Analytical Runs

Offline tests must prove that a valid `analysis-plan-v1` document loads with a canonical,
key-order-independent `sha256`; that every required declaration (research question, hypotheses
with id and statement, experimental unit, treatment factor and levels, fixed conditions, outcome
measures with at least one primary, estimand, design attempts/repetitions/seed/rotation, failure
rules including a present exclusion list, analysis method, assurance checks, authored date) is
rejected by name when missing or empty; that undeclared and credential-shaped keys are rejected
and an undeclared metric classifies as telemetry; that `amaw run` without a plan seals
`run_class = playtest` with no plan and otherwise unchanged provenance; that a plan is sealed with
its content and hash into the immutable manifest whose creation precedes every recorded
activation and round-trips through the export; that an invalid plan, `--run-class analytical`
without a plan, a plan with `--run-class playtest`, and a plan with `--resume-game` fail before
any game or database exists; that resume reconstructs the sealed plan and refuses a different
one; that registration verification classifies exports as `predeclared`, `post_hoc_plan` or
`unplanned` and reports `all_predeclared`, with the CLI helper exposing validation and
verification; and that legacy manifests and exports read as playtests
(`TEST-I047-001`…`TEST-I047-009`).

---

# 29. I-046 Visible-Rebuttal and Owner-Reply Condition

Offline tests must prove `sealed_cons` compatibility (no `reply` phase, no reply row, no new
context or Umpire-context key, `TEST-CON-001…005` and `TEST-PHASE-004…006` unchanged); that under
`visible_reply` Cons closing with at least one Con emits `cons_revealed` then
`phase_started(reply)` and marks the Argument `awaiting_reply`, while a close with zero Cons goes
straight to `adjudication` with no reveal event and no owner activation; that no Actor can read
another's Con while `phase == cons` and every current Actor reads the identical frozen set
afterwards in canonical `(actor_id, con_id)` order even when Con completion timing is deliberately
reversed (INV-CON-007); that only the owner has `reply_to_cons` listed with
`requires_input` true while every other Actor gets `403 forbidden` on `/reply` and no phase action;
that cardinality above `reply_max_points`, an unknown or duplicate `con_id`, empty text, over-long
text and any action/desired-effect/pro field are rejected with nothing persisted; that an empty
reply and a non-empty reply each persist exactly one `ArgumentReply`, emit
`owner_reply_submitted` and close to `adjudication` in one transaction, that a same-key
same-payload replay returns the recorded reply while any fresh attempt is
`409 already_participated`; that a fresh
engine on the same database in `reply` re-activates the same owner and cannot double-reply; that
the Umpire context carries `con_id` per Con and `owner_reply` only under `visible_reply` and is
byte-identical under `sealed_cons`, with `relevant_hidden_information` unchanged in both; that
`revealed_exchanges` shows every current Actor this turn's earlier exchanges with their reply and
is empty at the next turn start; that configuration (env, CLI, `POST /admin/games`), manifest
`game_loop.con_reply`, resume recipe (refusing a differing condition), AgentRun activation phase
`reply`, export-v10 `argument_replies` and the summary `con_reply_condition` round-trip with
additive legacy schema compatibility; that `visible_reply` selects the `player-v1.12`/`player-v1.13`
prompts and the `player-tools-v13-reply-live-bounded-research`/
`player-tools-v18-batched-reply-live-bounded-operations-item-outcomes` schema identities (v11/v13 per I-091 /
D-077: the tool-loop surface follows the server's refreshed declaration after the Actor's own
successful mutation; the batched surfaces v17/v18 per I-101 / D-085: ordered per-item outcomes,
continuation through recoverable failures, with D-078's separately budgeted operations) while
`sealed_cons` requests stay byte-identical, with `visible_reply` rejecting `umpire-v1.3` and
both treatments defaulting to `umpire-v1.7` (D-114) unless `--umpire-prompt-version` pins
another version, and an absent `reply`
inference-profile entry resolving from `argument` and being recorded; that the autonomous runner
spends exactly one activation on the owner in `reply` and none on any other Actor in **both**
`single_shot` (a `ReplyDecision` validated before mutation) and `tool_loop`; that
`visible_reply` composes with `sampled_sealed` — only the K sealed challengers can Con and the
revealed set is exactly their committed Cons; that the `visible_reply` player prompts tell a
challenger before submission that Con text and authorship become visible while the sealed prompts
are unchanged; and that a private Resource or fact behind one challenger that its Con does not
disclose is absent from every other Actor's post-reveal context and search
(`TEST-I046-001`…`TEST-I046-015`).
