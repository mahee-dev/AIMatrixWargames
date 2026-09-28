# AMAW Domain Invariants and State Machine

**Version:** 0.9.0 (D-095: §15 step 5a releases the scenario injects scheduled for the turn as Effects)  
**Status:** Normative for Lean MVP  
**Aligned with:** `PRD.md`, `TRD.md`. Earlier versions of this contract are recorded in the decision log.

---

## 1. Purpose

This document defines the rules the AMAW server must enforce regardless of client, model, prompt or agent framework.

The LLM is never responsible for procedural correctness.

If this document conflicts with an agent prompt, this document wins.

---

# 2. External Game States

```text
LOBBY
DIPLOMACY
ARGUMENT
CONS
ADJUDICATION
CONSEQUENCES
COMPLETE
```

`APPLY_EFFECTS`, `TURN_COMPLETE`, Actor advancement and expiry processing are internal transition operations, not externally writable states.

---

# 3. Core Game Invariants

## INV-GAME-001 — Single authoritative server

Only the AMAW core may modify:

```text
turn
phase
status
active Actor
Argument status
adjudication result
RNG result
Effect lifecycle
game completion
```

## INV-GAME-002 — Valid turn

For a running game:

```text
1 <= turn <= max_turns
```

Before start, implementation may represent turn as `0`.

## INV-GAME-003 — Valid active Actor

During `argument`, `cons`, `reply` and `adjudication`:

- the current Argument cycle must be associated with exactly one active Actor;
- that Actor must exist in `turn_order`.

## INV-GAME-004 — Atomic transitions

Every authoritative state transition must occur transactionally.

A request must not leave a partially transitioned game.

## INV-GAME-005 — No double advancement

The same Argument, Actor cycle or turn must not advance more than once.

---

# 4. Actor Invariants

## INV-ACTOR-001 — Actor identity

Actor identity comes from the authenticated Seat.

Clients may not select another Actor by submitting an Actor ID.

## INV-ACTOR-002 — Private briefing isolation

Only the owning Actor and authorised administration may access:

```text
private_brief
objectives
private_information
Notes
private Resources
private Effects
private Messages
```

## INV-ACTOR-003 — One Seat per Actor in Lean MVP

Each Actor has one active Seat for the game.

Multi-seat teams are MVP+.

---

# 5. Message Invariants

## INV-MSG-001 — Immutable after send

A sent Message cannot be edited.

## INV-MSG-002 — Public visibility

If:

```text
public = true
```

then:

```text
recipients = []
```

and the Message is visible to all Actors.

## INV-MSG-003 — Private visibility

If:

```text
public = false
```

the Message is visible only to:

- sender;
- listed recipients.

## INV-MSG-004 — Valid recipients

Every recipient must be an Actor in the same game.

---

# 6. Argument Invariants

## INV-ARG-001 — Active Actor only

Only `active_actor_id` may submit the current primary Argument.

## INV-ARG-002 — Correct phase

Primary Arguments may be accepted only during:

```text
phase == argument
```

## INV-ARG-003 — Argument budget

For every:

```text
game + turn + Actor
```

the number of accepted primary Arguments must not exceed:

```text
primary_arguments_per_actor
```

Default:

```text
1
```

The default case should be enforceable with a database uniqueness strategy in addition to service validation.

`primary_arguments_per_actor` is 1, 2 or 3 (D-094). A budget of N > 1 is spent in N **Argument
rounds** (`round_robin_v1`, §17/§20): an Actor's Argument in round `r` has `budget_slot = r`, and
the unique `(game, turn, actor, budget_slot)` constraint is the database backstop. An Actor cannot
reach slot `r + 1` before every Actor in `turn_order` has resolved slot `r`. Every slot is
mandatory, as the single slot is at N = 1.

## INV-ARG-004 — Required structure

An accepted Argument requires:

```text
non-empty action
non-empty desired_effect
1–5 Pros by default
```

## INV-ARG-005 — One current Argument

At most one Argument may be open for Cons/adjudication in the current sequential MVP flow.

The current Argument's status is `open_for_cons` during `cons`, `awaiting_reply` during `reply`
(`visible_reply` only, §18.1), `adjudicating` during `adjudication` and `resolved` afterwards.

---

# 7. Con Invariants

## INV-CON-001 — Correct phase

Cons are accepted only during:

```text
phase == cons
```

## INV-CON-002 — No self-Con

The Actor that created the Argument cannot submit a Con against it.

## INV-CON-003 — Eligibility

Only Actors participating in the game and not owning the current Argument are eligible.

Under the released `open_cons` policy every such Actor is eligible. Under the experimental
`sampled_sealed` policy the eligible set is exactly the challenger set the server sealed on the
Argument at acceptance (§17); other opponents may neither Con nor pass.

## INV-CON-004 — Limit

Each eligible Actor may submit:

```text
0 .. max_cons_per_actor
```

Default:

```text
3
```

## INV-CON-005 — Pass closes participation

After an Actor passes, it may not submit another Con for that Argument.

## INV-CON-006 — Cons belong to current Argument

A Con submitted against a stale/non-current Argument must be rejected.

## INV-CON-007 — Cons are sealed until participation closes

While `phase == cons` no Actor can read another Actor's Con. Under the released `sealed_cons`
reply policy Cons are never player-visible: only the Umpire receives them. Under the experimental
`visible_reply` policy the server reveals the frozen set simultaneously to every current Actor
when Con participation closes (§18), never earlier and never partially. The revealed set is
rendered in the canonical order the Umpire receives — author `actor_id`, then `con_id` — never in
completion order: Cons are collected concurrently, and which model answered first is not
information the game may convey.

## INV-REPLY-001 — Owner only, once, in the reply phase

An owner reply is accepted only when:

```text
con_reply_mode == visible_reply
phase == reply
caller == owner of the current Argument
no ArgumentReply exists for that Argument
```

Exactly one `ArgumentReply` row may exist per Argument. An empty reply is the explicit pass and
counts as the reply. A replay under the same idempotency key with the same payload returns the
recorded reply; any other submission once the reply exists is rejected as already participated.

## INV-REPLY-002 — A reply answers Cons; it does not amend the Argument

A reply is `0 .. reply_max_points` points. Each point references one distinct `con_id` from the
frozen set of the current Argument and carries non-empty text within the Con-reason length limit.
A reply has no action, desired-effect or pro field and cannot change the Argument it answers.
Whether a reply is responsive is an Umpire judgment, not a server rule.

## INV-REPLY-003 — Reveal is player-visible communication, not projection

The frozen Cons (author and text) and the owner reply are visible to every current Actor of the
game from reveal onward. The server neither redacts them nor derives them from hidden state; a
voluntary disclosure inside a Con or reply changes the visibility of no Resource, Effect, brief or
private-information record. The hidden Umpire context (`relevant_hidden_information`) is unchanged
by this policy.

---

# 8. Effect Invariants

## INV-EFFECT-001 — Server authority

Only the server creates, deactivates or expires authoritative Effects.

## INV-EFFECT-002 — Umpire proposal only

An Umpire may propose Effect text and supersession targets.

It may not directly write to the Effect table.

## INV-EFFECT-003 — Persistence

An Effect remains active until:

- expiry;
- explicit deactivation;
- supersession;
- game completion where relevant.

## INV-EFFECT-004 — Historical retention

Deactivated/expired/superseded Effects are retained in history.

Do not delete them merely because they are no longer active.

## INV-EFFECT-005 — Visibility

A private Effect must never be returned to an unauthorized Actor.

---

# 9. Resource Invariants

## INV-RES-001 — Generic semantics

`Resource.type` is an open scenario-defined string.

The AMAW core must not hard-code resource semantics such as:

```text
military_capability
treaty
press
port
air_force
```

## INV-RES-002 — Visibility before search

Inaccessible Resources are removed from the candidate set before:

- search matching;
- ranking;
- snippet generation;
- result counting.

## INV-RES-003 — No hidden metadata leakage

Unauthorized Actors must not learn hidden:

```text
resource IDs
names
tags
paths
metadata
snippets
existence
```

## INV-RES-004 — Resource reads do not mutate world state

Reading a Resource is information access, not an authoritative game-world mutation.

---

# 10. Note Invariants

## INV-NOTE-001 — Actor isolation

Notes are scoped to:

```text
game_id + actor_id
```

## INV-NOTE-002 — No chain-of-thought requirement

Notes are explicit strategic memory written through tools.

The system does not require hidden model reasoning or chain-of-thought storage.

---

# 11. RNG Invariants

## INV-RNG-001 — Server owned

The server performs the random draw.

## INV-RNG-002 — Game seed

Every game has a recorded seed.

## INV-RNG-003 — Logged draw

Every probabilistic adjudication records at least:

```text
probability
roll
success/failure
```

The draw and increment of the game's RNG draw index occur in the same transaction as the
authoritative adjudication record. A restart must continue from the next draw, not replay an
already-used value.

## INV-RNG-004 — No deterministic-LLM claim

A reproducible server RNG does not imply deterministic model behaviour.

---

# 12. Adjudication Invariants

## INV-ADJ-001 — Structured result

The Umpire returns one of:

```text
automatic_success
automatic_failure
probabilistic
```

## INV-ADJ-002 — Probability constraints

If `mode == probabilistic`:

```text
0.0 <= probability <= 1.0
```

and probability must be present.

For automatic modes, probability should be absent/null.

## INV-ADJ-003 — Single authoritative resolution

An Argument may be authoritatively adjudicated only once.

## INV-ADJ-004 — Fixed benchmark Umpire

During a model benchmark, the Umpire model and Umpire prompt version must remain fixed across comparison runs.

---

# 13. Idempotency Invariants

## INV-IDEM-001

The same idempotency key + same semantic payload returns the prior result.

## INV-IDEM-002

The same idempotency key + different payload returns a conflict.

## INV-IDEM-003

Idempotency must protect against duplicate semantic actions, not merely duplicate HTTP rows.

---

# 14. State Transition Table

| Current state | Trigger / operation | Eligible caller | Guard | Next state |
|---|---|---|---|---|
| `lobby` | start | admin/runner | scenario valid, Seats configured; run manifest sealed when the Game was created with `require_run_manifest` (D-068) | `diplomacy` |
| `diplomacy` | send Message | Actor | game running | `diplomacy` |
| `diplomacy` | ready | Actor | Actor eligible | `diplomacy` |
| `diplomacy` | close | server/admin | all ready or timeout | `argument` |
| `argument` | submit Argument | active Actor | budget available | `cons` |
| `cons` | submit Con | eligible opponent | participation open | `cons` |
| `cons` | pass | eligible opponent | participation open | `cons` |
| `cons` | close | server/admin | all opponents done or timeout; `sealed_cons`, or no Con | `adjudication` |
| `cons` | close | server | all opponents done; `visible_reply` and ≥ 1 Con | `reply` |
| `reply` | submit reply | Argument owner | no reply yet | `adjudication` |
| `adjudication` | resolve | server | valid Umpire output | internal Effect application |
| `adjudication` | pause on outage | server | Umpire unavailable after its bounded attempts | `paused` status |
| internal | next Actor exists | server | current Argument resolved | `argument` |
| internal | no Actor remains in round `r`, `r < N` | server | current Argument resolved; `round_robin_v1` (D-094) | `argument`, first Actor in `turn_order` |
| internal | no Actor remains | server | all Actor cycles done; `none` | turn completion (§22) — Option A, no `consequences` phase is entered |
| internal | no Actor remains | server | all Actor cycles done; `bounded_effects_v1` | `consequences` |
| `consequences` | resolve | server | no record for this turn; valid resolver proposal | `consequences` (record + Effects committed) |
| `consequences` | pause on outage | server | resolver unavailable or proposal invalid | `paused` status |
| `consequences` | complete | server | the turn's consequence record exists | `diplomacy` or `complete` |
| any running state | pause | admin | — | `paused` status |
| paused | resume | admin | previous phase retained | previous phase |
| any running state | end | admin | — | `complete` |

`paused` is a `GameStatus`; phase may remain stored separately.

---

# 15. Turn Start

At the start of a turn the server:

1. when starting from `LOBBY`, sets `turn = 1`;
2. sets `phase = diplomacy`;
3. sets `active_actor_id = null`;
4. resets persisted diplomacy readiness and the completed Diplomacy-round counter;
5. expires Effects whose expiry condition is reached;
   1. (5a) releases every scenario inject scheduled for this turn (scenario-format §11.2,
      D-095). In declaration order it creates each inject's Effect with `created_turn = turn` and
      `source_inject_id = <inject id>`, and emits `effect_created{effect_id, source_inject_id}`.
      An inject is released at most once per game. The release uses no RNG and is unconditional;
      the server never evaluates inject text;
6. emits `phase_started`.

No Actor Argument budget is carried from the prior turn.

---

# 16. Diplomacy Phase

Allowed Actor operations:

```text
observe/context
read_messages
send_message
search_resources
read_resource
get_notes
save_note
ready
```

Not allowed:

```text
make_argument
make_con
adjudicate
modify Effect
advance phase
```

Default close condition:

```text
all Actors ready
OR phase timeout
OR admin force advance
```

If a ready Actor receives a new private Message addressed to it before diplomacy closes, the server must clear that Actor's ready state in the same transaction that creates the Message, before the close condition is evaluated.

Readiness is authoritative persisted state for the current turn; it must not exist only in a
runner process.

The normal polling runner may then activate the Actor again. No event-driven push subsystem is required. This readiness reset is deterministic and must be tested.

The released `open_diplomacy` policy remains the default. A game may instead persist the versioned
experimental `bounded_rounds` policy with a positive `diplomacy_rounds_per_turn`. One Diplomacy
round is one complete I-028 simultaneous sweep: freeze every eligible Actor's authoritative
activation context, execute the eligible activations with the configured concurrency, wait for all
work to settle, apply deferred readiness, then apply same-round private-Message recipient
reactivation. Only then does the server atomically increment the durable per-turn round counter.

If all Actors become ready, normal closure wins. Otherwise, completion of the configured final
round closes Diplomacy to Argument in the same authoritative server transaction. Every Message
and readiness mutation committed during that round remains committed and visible; a recipient
reactivated by a final-round private Message receives no additional Diplomacy activation. A failed
or unsettled sweep does not consume a completed-round slot. Reporting the same `(turn, round)` is
idempotent, and a new turn resets the counter to zero.

---

# 17. Argument Phase

At entry:

1. server selects next Actor in `turn_order`;
2. server sets `active_actor_id`;
3. server exposes `make_argument` only to that Actor.

**Argument rounds (D-094).** With `primary_arguments_per_actor` N = 1 the Argument stage is one
pass through `turn_order`, the released loop. With N = 2 or 3 (`round_robin_v1`) it is N passes, or
rounds. Round `r` gives every Actor, in `turn_order`, its slot `r`, and round `r + 1` starts only
after the last Actor's round-`r` Argument has been resolved (§20). One Actor therefore never makes
consecutive Arguments, and every opponent can Con, message and react between them.

The current round is derived from persisted Arguments and is never stored separately. In
`argument` it is the active Actor's accepted Arguments this turn plus one. From `cons` through
`adjudication` it is the current Argument's `budget_slot`. Restart and resume therefore need no
additional state.

Other Actors may still read authorised information and communicate if scenario policy allows.

When a valid Argument is accepted:

1. persist Argument;
2. emit `argument_submitted`;
3. initialize Con participation state;
4. set phase to `cons`;
5. emit `phase_started`.

Step 3 is where the persisted Con policy acts. Under `sampled_sealed` the server, in the same
transaction and before any opponent can be activated, selects exactly `con_challengers_per_argument`
challengers, persists them on the Argument and emits `con_challengers_selected`. Selection walks
the authoritative `turn_order` ring from a durable per-game cursor seeded at `seed mod N`, skips
the owner, takes the first K Actors and advances the cursor past the last one taken. It is pure
bookkeeping: no adjudication draw is consumed. An idempotent replay returns the recorded selection.

---

# 18. Cons Phase

Eligible participants:

```text
all Actors except Argument owner            (open_cons, the released default)
the sealed challenger set of the Argument   (sampled_sealed)
```

Each Actor may:

- submit 0–N Cons up to limit;
- then pass/complete participation.

Passing creates a persisted completion record for the current Argument and Actor. The record
is unique for that pair and prevents later Con submission after a restart or retry.

Close when:

```text
all eligible Actors have completed
OR timeout
OR admin force advance
```

Under `sampled_sealed` "all eligible Actors" is the sealed set; a non-selected opponent is never
awaited and receives no Con activation. The Umpire context is unchanged in shape and contains the
Cons actually committed; the condition is not disclosed to the Umpire.

Then:

1. freeze Con submission;
2. under `visible_reply`, when at least one Con exists: mark Argument `awaiting_reply`, emit
   `cons_revealed`, set phase to `reply` and emit `phase_started(reply)` — stop here (§18.1);
3. otherwise mark Argument `adjudicating`;
4. transition to `adjudication`.

Zero Cons never enter `reply`: the owner receives no activation and no reveal event is emitted.

## 18.1 Reply Phase (`visible_reply` only)

Eligible participant: the owner of the current Argument, exactly once. Every other Actor's
`available_actions` contain no phase action and `requires_input` is false. Messaging is not legal.
The current Argument's status is `awaiting_reply` for the whole phase: Cons are frozen (no Con or
pass is accepted) and adjudication has not begun.

From `cons_revealed` onward the server projects the frozen Cons (`con_id`, author, text) and, once
submitted, the reply to every current Actor (INV-REPLY-003).

The owner submits one reply of `0 .. reply_max_points` points (INV-REPLY-002). In one transaction
the server revalidates phase, current Argument, owner identity and absence of a prior reply
(INV-REPLY-001), then:

1. persist the `ArgumentReply`;
2. emit `owner_reply_submitted`;
3. mark Argument `adjudicating`;
4. set phase to `adjudication`;
5. emit `phase_started(adjudication)`.

A replay under the same idempotency key and payload returns the recorded reply; any other
submission once the reply exists is rejected as already participated (INV-REPLY-001). `reply` is ordinary durable phase state: after a restart the owner is simply
activated again and the unique `ArgumentReply` prevents a double reply. Reply timeouts and admin
force-advance of `reply` are not defined by this version (D-060); a stalled owner is a stall.

---

# 19. Adjudication Phase

Player state mutation is frozen.

The server builds an Umpire context containing only authorised relevant information.

The Umpire returns structured output.

For:

```text
automatic_success
```

server selects success branch.

For:

```text
automatic_failure
```

server selects failure branch.

For:

```text
probabilistic
```

server performs RNG.

Then:

1. store authoritative adjudication record;
2. emit `argument_adjudicated`;
3. apply selected Effects;
4. deactivate explicitly superseded Effects;
5. mark Argument resolved.

The Umpire cannot bypass these steps.

If the Umpire is unavailable — a model outage or unusable output that survives its bounded
attempts (at most four model calls per adjudication, each under a wall-clock deadline, TRD §35 /
D-063) — the server does **not** invent an
adjudication (TRD §35). It pauses the game: `status` becomes `paused`, the `adjudication` phase is
retained, and **no** RNG draw, adjudication record or Effect is created, so the state stays
consistent and restart-safe. The condition surfaces at the API as `503 adjudicator_unavailable`.
Resuming a paused game is an admin action (§14).

---

# 20. Actor Advancement

After adjudication:

If another Actor remains in `turn_order` for the current round:

```text
phase = argument
active_actor_id = next Actor
```

Otherwise, if the resolved Argument's round `r` is below `primary_arguments_per_actor` N
(`round_robin_v1`, D-094):

```text
emit argument_round_started {turn, round: r + 1}
phase = argument
active_actor_id = first Actor in turn_order
```

Otherwise the last round has resolved (at N = 1, the only round). Under `bounded_effects_v1`:

```text
phase = consequences
active_actor_id = null
```

Under `none` (the default) the turn completes directly (§21 Option A, §22), and no
`consequences` phase is stored or emitted.

---

# 21. Consequences Phase

A persisted game-loop condition, `consequence_mode`, chooses the option. It is resolved at game
creation and never toggled at runtime (D-091).

### Option A — disabled (`none`, the default)

Immediately complete the turn.

### Option B — one fixed consequence pass (`bounded_effects_v1`)

The phase is entered once per turn, after the last Actor's adjudication (§20). Player mutation is
frozen exactly as in `adjudication`: no Message, Argument, Con, reply or readiness is accepted, and
no Actor is offered a phase action or activated. Notes and Resource reads stay available, as in
every running phase, and they change no game state.

The server builds a public-only context (agents.md §28) and calls the resolver outside any write
transaction. The resolver may propose:

```text
0–3 Effects, each citing ≥ 1 authoritative evidence reference
```

In one transaction on re-read state the server:

1. requires a running `consequences` phase and no consequence record for this turn;
2. validates the whole proposal (INV-CONSEQ-002/003);
3. persists one consequence record for `(game, turn)`;
4. creates each Effect as public, non-expiring and non-superseding, with
   `source_consequence_turn = turn`;
5. emits `effect_created` for each Effect, then `consequences_resolved`.

It commits all of this or nothing. The phase stays `consequences`; a separate advancement completes
the turn (§22) and requires the record.

If the resolver is unavailable after its bounded attempts, or its proposal fails validation, the
server commits nothing. No record, no Effect and no event beyond the pause is written. It pauses
the game (`status = paused`, phase retained, `game_paused{reason: consequence_resolver_unavailable}`)
and never skips or fabricates the pass. Resuming re-runs the pass.

The resolver is not permitted to modify game state directly and draws no randomness.

## INV-CONSEQ-001 — One pass per turn

At most one consequence record exists per `(game, turn)`. Turn completion under
`bounded_effects_v1` requires it; a resumed game that already holds it advances without calling
the resolver again.

## INV-CONSEQ-002 — Bounded, evidence-linked proposal

A proposal has at most 3 Effects. Each Effect has a non-empty description and rationale, each at
most 4000 characters, and 1–8 distinct evidence references. Any violation rejects the whole
proposal.

## INV-CONSEQ-003 — Evidence must resolve and be public

A reference is either `{"kind": "event", "id": <int>}`, which must be an `argument_adjudicated`
Event of this game in the current turn, or `{"kind": "effect", "id": "<uuid>"}`, which must be an
Effect of this game that is active and public at commit. An unknown, foreign, other-turn, other-type,
inactive or private reference rejects the whole proposal.

## INV-CONSEQ-004 — No randomness, no Actor opportunity

The consequence pass leaves `rng_draw_index` unchanged. No Actor receives a decision opportunity
between the last adjudication and turn completion.

---

# 22. Turn Completion

If:

```text
turn < max_turns
```

then:

```text
turn += 1
phase = diplomacy
```

If:

```text
turn == max_turns
```

then:

```text
status = complete
phase = complete
active_actor_id = null
```

Emit:

```text
turn_completed
game_completed
```

as applicable.

---

# 23. Illegal Transition Examples

The following must fail:

```text
Actor B submits an Argument while Actor A is active.
Actor A submits a second primary Argument when budget is 1.
Actor A submits its round-2 Argument before every Actor has resolved its round-1 Argument.
Actor A submits a Con against its own Argument.
Actor B submits a Con after passing.
An Actor submits a Con after adjudication starts.
An Actor directly requests phase advancement.
The Umpire sends an Effect-writing database command.
A hidden Resource appears in Actor C's search count.
The same Argument is adjudicated twice.
```

---

# 24. Minimal Event Expectations

At minimum, a normal Argument cycle emits:

```text
phase_started(argument)
argument_submitted
phase_started(cons)
con_submitted / con_passed
[cons_revealed, phase_started(reply), owner_reply_submitted]   (visible_reply with ≥ 1 Con)
phase_started(adjudication)
argument_adjudicated
effect_created [0..N]
effect_deactivated [0..N]
```

At an Argument-round boundary under `round_robin_v1` (D-094), after the last Actor's round-`r`
cycle and before the first Actor's round-`r + 1` cycle:

```text
argument_round_started {turn, round}
phase_started(argument)
```

At turn boundary under `bounded_effects_v1` (§21 Option B), before `turn_completed`:

```text
phase_started(consequences)
effect_created [0..3]
consequences_resolved
```

At turn boundary:

```text
turn_completed
effect_deactivated [0..N]                     (expiry, §15.5)
effect_created [0..N]                         (scheduled injects, §15.5a, D-095)
phase_started(diplomacy)
```

At end:

```text
game_completed
```

---

# 25. Concurrency Rules

Lean MVP uses sequential Actor Arguments, so concurrency surface should remain small. Argument
rounds (D-094) stay sequential: one Argument is open at a time (INV-ARG-005).

State-changing operations must re-check:

```text
current phase
current active Actor
current Argument
current participation status
Argument budget
game status
```

inside the same transaction that writes the mutation.

Expected protected races include:

```text
two primary Arguments arriving nearly simultaneously
two phase-close operations
duplicate Con submission retry
duplicate owner-reply retry
two adjudication completion attempts
two consequence-pass commit attempts for one turn
two turn advancement attempts
```

Distributed locking is not required.

---

# 26. MVP+ State Extensions

Not part of this normative Lean MVP state machine:

```text
organisation hierarchies
conversation/channel state
approval workflows
event-driven Actor activation
external tool jobs
Secret Argument triggers
human facilitation workflows
parallel Arguments
simultaneous action resolution
```

Add these only through explicit future state-machine revisions.
