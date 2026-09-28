# AMAW Agent, Tool and Umpire Contract

**Version:** 0.3.12 (D-114: §19/§21 `umpire-v1.7` is the default Umpire prompt for every game)  
**Status:** Normative for Lean MVP  
**Aligned with:** `PRD.md`, `TRD.md`. Earlier versions of this contract are recorded in the decision log.

---

## 1. Purpose

This document defines how autonomous player agents interact with AMAW and how the AI Umpire returns adjudications.

The central rule is:

> **Models choose strategy. AMAW enforces procedure.**

The agent framework must not maintain its own authoritative copy of game state.

---

# 2. Actor Agent Responsibilities

An Actor agent may decide:

- what information to inspect;
- what strategic assumptions to make;
- whom to contact;
- what to say;
- whether to cooperate;
- whether to conceal intentions;
- whether to bluff or threaten;
- what primary Argument to make;
- what Cons to raise;
- what private Notes to record;
- when it has nothing further to do.

The Actor agent must not:

- impersonate another Actor;
- directly modify game phase;
- directly create authoritative Effects;
- perform RNG for adjudication;
- access hidden Resources without server authorization;
- bypass tool schemas.

---

# 3. Agent Lifecycle

Reference loop:

```python
while True:
    context = await api.get_context()

    if context["status"] == "complete":
        break

    if not context["requires_input"]:
        await asyncio.sleep(POLL_INTERVAL)
        continue

    await agent.arun(
        build_activation_prompt(context),
    )
```

The agent may call multiple legal tools during one activation.

The server determines legal actions.

---

# 4. Activation Context

Every activation should provide at least:

```text
current turn
maximum turns
current phase
active Actor
Actor identity
Actor objectives
Actor private brief
Actor private starting information
scenario rules, when the scenario declares them (scenario-format §11.1; absent otherwise)
Argument budget and current round, when the budget is above 1 (D-094; absent otherwise)
visible active Effects
current Argument where relevant
available_actions
remaining outgoing Message budget for this activation
```

Do not include:

```text
another Actor's objectives
another Actor's private brief
another Actor's Notes
private Messages not addressed to Actor
hidden Resources
hidden Effects
facilitator-only information
```

### Incremental context and rolling strategic memory (post-MVP condition)

The reference player's `incremental-context-v1` condition replaces the repeated Message/Note
archive in each decision packet with:

```text
stable Actor and scenario information
current authoritative Effects and participation opportunity
Messages received since the Actor's last acknowledged activation
one current structured strategic-memory state
available_actions
```

“Received” excludes Messages sent by the Actor itself. Older visible Messages remain available
through `read_messages`; incremental delivery neither deletes nor hides history.

The memory is explicit private strategic memory, not hidden chain-of-thought, and contains at
least:

```text
commitments made
commitments or offers received
outstanding offers and negotiations
red lines and constraints
uncertain or disputed claims
relevant relationships or likely coalitions
current strategic assessment
intended next action
```

Exactly one current memory state exists per `game_id + actor_id`; an update replaces that state
rather than appending a new Note. The server persists both memory and delivery progress. The
framework must not keep persistent LLM chat history.

Delivery is at-least-once until acknowledgement. A successful activation acknowledges only the
bounded snapshot it received, atomically with replacement of its strategic memory. A failed,
timed-out, guardrail-stopped or stalled activation does not acknowledge the snapshot. A process
restart therefore re-delivers the same pending batch. Messages committed after the snapshot wait
for the next activation and cannot be skipped by acknowledgement of the earlier batch.

---

# 5. Baseline Player Tools

Required Lean MVP tools:

```text
observe
read_messages
send_message
search_resources
read_resource
make_argument
make_con
pass_con
get_notes
save_note
ready
```

Tools should be thin wrappers around the AMAW HTTP API.

Under the experimental `visible_reply` Con-reply policy (state-machine §18.1) a twelfth tool,
`reply_to_cons` (§13.1), is registered; under the released `sealed_cons` policy it does not exist
and the tool schema is byte-identical to the released one.

These eleven tools are the player's **HTTP client surface**; every mutation an agent makes goes
through one of them. A harness may present the mutating tools to the *model* as one structured
decision per activation (Messages + Note + `ready`; an Argument; Cons or pass; under
`visible_reply`, a `ReplyDecision` — `points` of `{con_id, text}`, empty meaning pass, with the
complete replacement memory, selected when `reply_to_cons` is listed, validated against
`reply_max_points` and the revealed `con_id`s before any mutation and executed through the same
endpoint) that the harness
validates against §18 and executes through these tools in a fixed order, while the read tools
(`read_messages`, `search_resources`, `read_resource`) stay callable for research first. The
model-facing surface is a benchmark condition: record it as `activation_mode` alongside the tool
schema version (§19), hold it constant in model-benchmark mode, and never mix modes in one game.
Legality is unchanged in either form — the server decides from `available_actions` (§3).

The structured-decision form (`single_shot`) is the normal player condition; the tool-loop form
(`tool_loop`) is an experimental tool-use condition (D-079). In `tool_loop` the mutating tools
offered to the model are a further sealed condition, `tool_surface`: `broad` (default) offers the
whole mutating vocabulary, so an attempt the current `available_actions` does not list is
observable invalid-tool behaviour; `available_only` withdraws, per activation, every mutating tool
the server's current declaration does not list. Neither form changes server legality; the read
tools follow §18 in both. Outside `tool_loop` the field is recorded as given and has no effect.

`tool_loop` expects one mutating tool per provider round-trip, but the harness does not enforce
it: the tool calls of one model response are dispatched concurrently, and only `send_message`
(the Message cap) and the Resource tools (the research reservation) are serialised. Two sibling
mutations emitted together therefore race — whichever the server accepts first may move the
phase, and the other receives a genuine `409` that is counted as an invalid tool call although
which one lost depends on local scheduling. This is a known property of the experimental
condition under both surfaces — `available_only` filters at the *next* model step, not within an
already-emitted response — and is left as it is (D-082). `single_shot` is not affected.

---

# 6. Tool Contract — `observe`

Purpose:

> Refresh current Actor-specific game context.

Input:

```json
{}
```

Output:

```json
{
  "turn": 2,
  "phase": "argument",
  "active_actor_id": "arkania",
  "requires_input": true,
  "available_actions": ["make_argument", "send_message", "search_resources"]
}
```

Side effects:

```text
none
```

Legal:

```text
all game phases
```

---

# 7. Tool Contract — `read_messages`

Purpose:

> Read Messages visible to the Actor.

Input:

```json
{
  "after_event_id": 382
}
```

`after_event_id` optional.

Output:

```json
{
  "messages": []
}
```

Side effects:

```text
none
```

---

# 8. Tool Contract — `send_message`

Purpose:

> Send a public or private Message.

Private input:

```json
{
  "recipients": ["cestia"],
  "public": false,
  "text": "We are willing to discuss a mutual restraint agreement."
}
```

Public input:

```json
{
  "recipients": [],
  "public": true,
  "text": "Arkania calls for immediate consultations."
}
```

Output:

```json
{
  "message_id": "uuid"
}
```

Errors may include:

```text
invalid recipient
message too long
wrong phase if messaging restricted by scenario
```

---

# 9. Tool Contract — `search_resources`

Purpose:

> Search additional scenario information available to the Actor.

Input:

```json
{
  "query": "Belvar fuel logistics"
}
```

Output:

```json
{
  "results": [
    {
      "id": "belvar-logistics",
      "name": "Belvar Logistics Overview",
      "type": "background",
      "snippet": "Belvar depends on two principal fuel import terminals..."
    }
  ]
}
```

The agent should use this when it identifies an information gap.

Search results are visibility-filtered by the server.

---

# 10. Tool Contract — `read_resource`

Purpose:

> Read one authorised scenario Resource.

Input:

```json
{
  "resource_id": "belvar-logistics"
}
```

Output:

```json
{
  "id": "belvar-logistics",
  "name": "Belvar Logistics Overview",
  "type": "background",
  "content": "..."
}
```

Side effect:

```text
resource-access event/log entry
```

Unauthorized reads return a generic not-found/forbidden-safe response.

---

# 11. Tool Contract — `make_argument`

Purpose:

> Use the Actor's consequential Matrix action for the turn.

Input:

```json
{
  "action": "Begin mobilisation of reserve forces.",
  "desired_effect": "Reserve formations begin deploying toward the frontier.",
  "pros": [
    "Mobilisation plans already exist.",
    "Rail capacity is currently available."
  ]
}
```

Output:

```json
{
  "argument_id": "uuid",
  "status": "open_for_cons"
}
```

Legal only when:

```text
make_argument ∈ available_actions
```

The agent must not attempt to split one primary Argument into numerous pseudo-actions to bypass the scenario's action budget.

When the scenario's budget is above 1 (`round_robin_v1`, D-094), the Actor is offered
`make_argument` once in each Argument round. Its context carries
`argument_budget {primary_arguments_per_actor, round, used}`, so it can plan one decision per
Argument instead of bundling several into one. The tool, its input and its output are unchanged.
The player packet forwards `argument_budget` as delivered. The prompt text is unchanged, because
the budget is data in the packet. At budget 1 the key is absent and the packet is unchanged.

---

# 12. Tool Contract — `make_con`

Purpose:

> Challenge another Actor's current Argument.

Input:

```json
{
  "argument_id": "uuid",
  "reason": "Rail capacity is constrained by industrial disruption."
}
```

Output:

```json
{
  "con_id": "uuid"
}
```

The Actor cannot Con its own Argument.

After submitting any useful Cons, the Actor must call `pass_con` to close its participation. If it has no useful Con, it should call `pass_con` immediately.

Under `sealed_cons` a Con is read only by the Umpire. Under `visible_reply` the Con's text **and
its author** are revealed to every current Actor when participation closes, and the Argument owner
may answer it (§13.1). The player prompt for that mode states this before any Con is submitted;
a sealed-Con prompt must not be reused unchanged for it. A Con may voluntarily disclose what its
author knows, exactly as diplomacy may; the server projects nothing hidden into it and the
disclosure changes no Resource, Effect or brief visibility.

---

# 13. Tool Contract — `pass_con`

Purpose:

> Close the Actor's participation in the current Con phase without submitting another Con.

Input:

```json
{
  "argument_id": "uuid"
}
```

Output:

```json
{
  "passed": true,
  "argument_id": "uuid"
}
```

Legal only when:

```text
pass_con ∈ available_actions
```

After `pass_con`, the Actor may not submit another Con for that Argument.

---

# 13.1 Tool Contract — `reply_to_cons` (`visible_reply` only)

Purpose:

> Answer the revealed Cons against the Actor's own current Argument, once.

Input:

```json
{
  "argument_id": "uuid",
  "points": [
    {"con_id": "uuid", "text": "Rail disruption affects freight, not the passenger lines the plan uses."}
  ]
}
```

Output:

```json
{
  "argument_id": "uuid",
  "point_count": 1
}
```

Legal only when:

```text
reply_to_cons ∈ available_actions
```

which is true only for the Argument owner while `phase == reply`. `points` holds `0 ..
reply_max_points` entries, each naming a distinct `con_id` from `current_argument.cons`. An empty
list is the explicit pass — the only way to decline. `current_argument.cons` is presented in the
canonical `(actor_id, con_id)` order the Umpire receives, never completion order. The reply
answers rebuttals; it cannot replace or amend the Argument's action, desired effect or pros. Whether it is responsive is the
Umpire's judgment. The reply is visible to every current Actor. After the call the phase is closed
and the tool is no longer available; a second call fails as already participated.

---

# 14. Tool Contract — `get_notes`

Purpose:

> Read the Actor's explicit private strategic notebook.

Input:

```json
{}
```

Output:

```json
{
  "notes": [
    {
      "id": "uuid",
      "text": "Cestia appears receptive to neutrality."
    }
  ]
}
```

---

# 15. Tool Contract — `save_note`

Purpose:

> Save explicit strategic memory for later turns.

Input:

```json
{
  "text": "Belvar threatened intervention if Cestia grants basing rights."
}
```

Output:

```json
{
  "note_id": "uuid"
}
```

Notes should contain useful explicit memory, not hidden chain-of-thought.

---

# 16. Tool Contract — `ready`

Purpose:

> Signal that the Actor currently has no further useful action in the current participation opportunity.

Input:

```json
{}
```

Output:

```json
{
  "ready": true
}
```

The runner should use this only where `ready` is present in `available_actions`, primarily diplomacy in the Lean MVP. Con participation is closed with the dedicated `pass_con` tool; `ready` must not be overloaded as a Con pass.

---

# 17. Agent Prompting Principles

Player prompts should:

- identify the Actor;
- state objectives;
- explain that the agent is autonomous;
- describe available tool categories;
- tell the agent to respect `available_actions`;
- avoid giving a pre-computed strategy;
- tell the agent not to assume unseen information;
- encourage explicit Notes when durable memory matters.

Player prompts should not:

- tell the model what winning strategy to use;
- tell it always to cooperate or always escalate;
- encode scenario-specific tactical playbooks unless that is an experimental treatment;
- expose Umpire hidden information.

---

# 18. Agent Guardrails

Recommended defaults:

```python
MAX_LLM_ITERATIONS = 12
MAX_TOOL_CALLS = 20
MAX_RESEARCH_TOOL_CALLS = 4
MAX_RESEARCH_OPERATIONS = 4
MAX_MESSAGES_PER_ACTIVATION = 3
MAX_MESSAGES_PER_PHASE = 10
```

`MAX_RESEARCH_TOOL_CALLS` is the optional-research subset of `MAX_TOOL_CALLS`. The player must
state the request, total-tool, research-tool and research-operation ceilings in the activation
packet. Research tools must no longer be offered after the research subset is exhausted, after an
exact successful-call repeat, or when another model request/tool call must be reserved for the
required completion decision. Batched research counts as one tool call regardless of its bounded
item count. A model uses already-returned results rather than repeating a successful search/read.

`MAX_RESEARCH_OPERATIONS` bounds the underlying Resource work an activation may cause,
independently of how many tool calls carry it (D-078). A *research operation* is one Resource
`search` or `read` request actually dispatched toward the server; it is charged immediately before
dispatch and remains charged if the request fails. A request that never reaches the server — a
refused exact repeat, a refused call — consumes no operation. `read_messages` consumes a
research-tool call and no operation. Each item of a batched search/read is one operation. A batch
that would cross the remaining operation budget executes its items in input order until the
budget is spent and returns the remaining items, never attempted, as `not_executed` with
`guardrail_research_operations_exhausted`; a batch is never granted more than the remaining
budget. When either research budget is spent the Resource tools are withdrawn; `read_messages`
follows the research-tool budget only. Both counts — research tool calls and research operations
— are recorded per activation (§19). Under the released defaults a sequential player and a
batched player may cause the same maximum Resource work; they differ only in request topology.

Within the executed prefix of a `batched-v1` search/read, every item produces one ordered outcome:
`{query|resource_id, ok: true, data}` or
`{query|resource_id, ok: false, error: {http_status, code, message, details}}`. A recoverable item
failure does not abandon later independent items. A batch with at least one dispatched item is a
successful tool call even when one or more item outcomes failed; this makes the exact completed
batch subject to successful-call duplicate suppression and makes its exact mixed result eligible
for `compact-success-v1` retention. Invalid batch input remains a whole-call failure. If the
operation budget is already exhausted before the first dispatch, D-078's whole-call refusal
remains; if it is exhausted after an executed prefix, the untouched suffix remains under
`not_executed`.

`MAX_MESSAGES_PER_ACTIVATION` is the normal behavioural budget for successful outgoing Message
mutations attributable to one Actor activation. It is non-negative; `0` permits no Message sends
but leaves every other legal action available. It does not reset when the model is retried within
that activation. Successfully committed Messages remain charged after a later activation failure;
failed/rejected mutations do not consume it, and an idempotent replay does not consume it twice.
The per-Actor activation budget is independent of both `MAX_MESSAGES_PER_PHASE` (the phase-wide
emergency ceiling across repeated activations) and `MAX_TOOL_CALLS`; all applicable limits apply.

Also enforce:

```text
model/API timeout
tool payload size
message length
Argument text length
Pros count
Note length
```

### Failure recovery

If a player model:

1. returns a malformed tool call → tool error returned;
2. transiently fails → retry once;
3. still fails/stalls → record failure and auto-ready/pass where legal.

The failure record names the completion the runner took and, for `pass_con`, its Argument, so a
recovery pass is never read as the Actor's own choice (D-105).

If a still-current `make_argument` or `reply_to_cons` opportunity has no legal automatic
completion, a guardrail failure or exhausted transient attempt may start a fresh activation while
the finite per-opportunity activation bound has room. Every failed activation is recorded.
Deterministic failures and an exhausted opportunity bound stop; the harness never fabricates the
required decision.

One player must not permanently hang the game.

---

# 19. Model Configuration

Each Actor run should record:

```text
model_requested
model_returned if available
provider
prompt_version
token usage
tool calls
research tool calls and research operations (D-078)
invalid calls
stalls
cost if available
latency if available
activation_mode
tool_surface (D-079)
context_mode
memory_version
max_messages_per_activation
diplomacy_policy_version
diplomacy_mode
diplomacy_rounds_per_turn where bounded
con_policy_version
con_mode
con_challengers_per_argument where sampled
con_reply_policy_version
con_reply_mode
reply_max_points where visible_reply
consequence_policy_version and consequence_mode (D-091)
scenario.argument_sequencing and scenario.primary_arguments_per_actor where the budget is above 1 (D-094; scenario-derived, sealed by the server)
inference_profile and the phase-resolved reasoning configuration / max_tokens
```

An inference profile (`inference-profile-v1`) may vary reasoning effort and completion budget
by player phase (`diplomacy`, `argument`, `cons`, and optionally `reply`) and for the Umpire.
When `reply` is absent it resolves from the profile's `argument` entry (a reply is Argument
reasoning); existing documents stay valid and the resolved reply settings are sealed and recorded
like every other phase. The Umpire prompt version is likewise explicit: every game defaults to
`umpire-v1.7` (D-114), `visible_reply` refuses the reply-unaware `umpire-v1.3`, and
`--umpire-prompt-version` / `AMAW_UMPIRE_PROMPT_VERSION` pins it for a comparison. It is an agent-configuration condition: the resolved
per-phase table is sealed in the run manifest, each Actor run records the values actually
requested for its phase, and actual reasoning usage is reported beside them — equal configured
effort does not mean equal purchased attention across models.

For **model benchmark mode**, keep constant:

```text
agent framework
baseline player prompt
toolset
memory mechanism
inference profile
```

and vary only the underlying model.

---

# 20. Umpire Role

The Umpire is not a player.

It should be:

- conservative;
- evidence-bound;
- explicit;
- structured;
- consistent;
- aware of turn timescale;
- resistant to rhetorical style as a substitute for evidence.

The Umpire does not need to make the game exciting.

It needs to make a plausible, inspectable resolution.

---

# 21. Umpire Input Contract

Minimum adjudication context:

```json
{
  "scenario_summary": "...",
  "turn": 2,
  "turn_length": "2 weeks",

  "argument": {
    "actor_id": "arkania",
    "action": "Begin mobilisation...",
    "desired_effect": "Reserve formations begin deploying...",
    "pros": [
      "Mobilisation plans already exist."
    ]
  },

  "cons": [
    {
      "actor_id": "belvar",
      "reason": "Rail disruption will slow movement."
    }
  ],

  "relevant_effects": [
    {
      "id": "uuid",
      "description": "Regional political tensions are elevated.",
      "visibility": "public"
    }
  ],

  "relevant_hidden_information": [
    {"kind": "private_information", "actor_id": "arkania", "text": "Reserve mobilisation requires two turns."},
    {"kind": "private_effect", "actor_id": "arkania", "text": "Arkania's reserve mobilisation plan is incomplete."}
  ]
}
```

The server constructs this context.

Do not let the player model choose what hidden information the Umpire receives.

Under `visible_reply` (state-machine §18.1) and only then, each `cons` entry additionally carries
its `con_id` and the context carries the owner's recorded reply:

```json
{
  "owner_reply": [
    {"con_id": "uuid", "con_actor_id": "belvar", "text": "Rail disruption affects freight, not the passenger lines the plan uses."}
  ]
}
```

When the scenario declares rules (scenario-format §11.1), and only then, the context carries them
verbatim in declaration order, separate from `scenario_summary` (D-090):

```json
{
  "scenario_rules": [
    {"id": "no-overflight", "text": "Belvar cannot authorise foreign military overflight without a parliamentary vote."}
  ]
}
```

When the scenario's Argument budget is above 1 (D-094), and only then, `argument` also carries
`budget_slot`, the Argument's round, and `primary_arguments_per_actor`. A scenario rule can then
name the slot, for example to type the actions allowed in slot 2. The Umpire prompt is unchanged
(`umpire-v1.5` and earlier stand), and at budget 1 the `argument` object is unchanged.

The server fills `scenario_rules` from the rules snapshotted on the Game at creation. No player
model and no Argument chooses whether they are included: every adjudication in the game
receives the same rules, whether or not a Pro or Con mentions them. The key is absent for a
scenario without rules, so that context is unchanged. Rules are binding scenario content, and
the Umpire applies them at the authority, prerequisite and deterministic gates (§23), citing a
rule by `id` in its rationale when it applies one. A rule cannot waive a gate, set a
probability, or establish an Effect or another Actor's commitment by itself. The server never
interprets rule text. `scenario_rules` requires a rules-aware prompt: `umpire-v1.5` (`umpire-v1.4`
plus the rules framing), `umpire-v1.6` (v1.5 plus Effect discipline, §22, D-096) or `umpire-v1.7`
(v1.6 plus a branch audit, §22, D-112). Every game defaults to v1.7 (D-114), with or without
rules. A game whose scenario declares rules cannot run under v1.3 or v1.4. v1.5 and v1.6 stay
selectable, for example to reproduce an earlier run, and v1.3 or v1.4 may still be pinned for a
rule-free game (§27). A resumed game keeps the prompt version its recipe recorded.

`owner_reply` is an empty list for a recorded pass and absent under `sealed_cons` or when no Con
was made (the `reply` phase was skipped). `relevant_hidden_information` is unchanged by the policy.
The Umpire weighs the reply as the owner's answer to the rebuttals; it adds no action and it is
never a rule that a reply must be credited. `visible_reply` requires a reply-aware prompt
(`umpire-v1.4` or later). Both treatments default to `umpire-v1.7` (D-114), so a
`sealed_cons`/`visible_reply` comparison holds the Umpire condition constant by default; a
comparison that pins a version must pin the same one for both treatments (§27).

---

# 22. Umpire Output Contract

The Umpire must return structured JSON equivalent to:

```json
{
  "mode": "probabilistic",
  "probability": 0.65,
  "rationale": "Existing mobilisation planning makes the action plausible, but rail disruption materially reduces the chance that formations become available within the turn.",
  "success_effects": [
    "Arkanian reserve formations begin deploying toward the frontier."
  ],
  "failure_effects": [
    "Arkanian reserve mobilisation begins but fails to produce deployable formations this turn."
  ],
  "success_supersedes_effect_ids": [],
  "failure_supersedes_effect_ids": []
}
```

Allowed `mode`:

```text
automatic_success
automatic_failure
probabilistic
```

Validation:

```text
probability required for probabilistic
0.0 <= probability <= 1.0
probability null for automatic modes
rationale non-empty
Effect lists bounded by configured limits
success/failure superseded Effect IDs must exist and be eligible
```

Under `umpire-v1.6` (D-096) each Effect list holds at most 3 entries. The structured-output schema
states the bound (tool schema `umpire-output-v2`), and the Umpire client treats a longer list as
unusable output that it retries within its bounded attempts. The prompt also asks the Umpire to:

- write an Effect only for a change that later adjudication needs;
- write no Effect that only records maintaining, confirming or concluding something already
  established;
- supersede an active Effect when updating it, instead of adding a parallel one.

The server's own per-branch limit is version-independent and unchanged.

`umpire-v1.7` (D-112) keeps v1.6's bound, schema and retry, and adds a branch audit that applies
§23 to each branch the server could select. For every Effect in such a branch the Umpire asks
whether it would be established if no other Actor made a new discretionary decision or
commitment. It may rest on another Actor's decision only where that decision is already
authoritative or a deterministic rule makes it follow from established facts. The prompt also asks
the Umpire to:

- put every unconditional self-binding Effect in every selectable branch;
- establish a conditional self-commitment as the commitment, not as its consequence;
- give each branch of a `probabilistic` proposal at least one Effect, and leave the unselectable
  branch of an automatic mode empty;
- name the state an Effect establishes (committed, accepted, directed, voted, authorised, begun,
  completed, exists) and no stronger one.

These are prompt instructions. The server's validation is the same under every prompt version.

`success_effects` and `failure_effects` describe only state that the Action and selected branch
can establish. They need not repeat an overbroad Desired Effect. A branch-independent Effect is
represented identically in both lists. Supersession targets are branch-specific: the server
applies only the list for the selected branch.

---

# 23. Adjudication Principles

The governing test, for adjudication and for any future mechanic: **mechanically protect player
agency and logical prerequisites, while leaving strategy, causal arguments, plausibility and
unexpected outcomes open to the players and the Umpire.** A rule that protects agency, procedure
or state coherence belongs here; one that prescribes the domain's strategy space or simulates
outcomes automatically does not.

Adjudication is ordered. A later gate may not override a failed earlier gate:

```text
1. authority and causation: what the Action can establish
2. authoritative prerequisites
3. deterministic rules and derived state
4. probability for remaining causal uncertainty
5. branch Effects and branch-specific supersession
```

The first gate asks what the arguing Actor's Action can establish, in two parts. *Authority:* an
Actor may bind itself where the scenario gives it that authority; it may not create another
Actor's decision, commitment or acceptance merely by asserting it in a Desired Effect.
*Causation:* the Desired Effect must be causally supportable by the Action together with the
established state. An Actor may be able to order something without being able to guarantee that
it happens: an outcome the Action can plausibly bring about but not assure passes this gate and
is resolved by probability later; an outcome the Action cannot bring about at all does not pass.
The second gate checks whether any facts or commitments required by the requested outcome already
exist as authoritative Effects or explicit scenario facts. Missing authority, absent causal
support or a missing prerequisite is not low-probability uncertainty. It is an inadmissible
component and must be omitted from both branches.

The third gate covers conclusions that follow mechanically from authoritative facts and scenario
rules — *derived state*, or a facilitator's synthesis of what the established Effects already
imply. What counts as derivable is scenario content; the engine and this contract name only the
category.

Where a Desired Effect mixes an admissible self-binding act with unsupported or uncertain external
consequences, the Umpire must narrow and separate it:

- establish the feasible self-binding act;
- omit unsupported commitments and assertions;
- derive only conclusions whose prerequisites are already authoritative;
- use probability only for any remaining causal uncertainty.

For a probabilistic external consequence, repeat the self-binding Effect identically in both
branches so the server establishes it regardless of the roll. If no causal uncertainty remains,
use the appropriate automatic mode for the narrowed Effect.

The Umpire should consider:

```text
plausibility
established Effects
explicit scenario facts
private information authorised for adjudication
Pros
Cons
turn timescale
scope of requested Effect
```

The Umpire should reject or narrow:

```text
Effects wildly larger than the stated action
outcomes impossible within the turn timescale
arguments contradicted by authoritative Effects
pure rhetorical confidence without supporting reasons
```

The random draw may decide whether a plausible, attempted causal process succeeds. It does not
create missing decisions, agreements, facts or procedural prerequisites — it may not waive a
missing prerequisite, create another Actor's commitment, or make an unsupported factual assertion
authoritative.

Where authoritative facts and scenario rules make a conclusion mechanical (for example a known
threshold calculation), the conclusion is deterministic. Do not assign it a probability merely
because it is strategically consequential.

An Effect may supersede an active contradictory Effect only in a branch whose Action or newly
established evidence actually resolves the condition represented by the earlier Effect. Restating
the opposite proposition is not resolution. Each branch lists only its own eligible supersession
targets.

The Umpire should not invent large unrelated world events merely to create drama.

---

# 24. Automatic Success / Failure

Use `automatic_success` where meaningful uncertainty is negligible.

Examples:

```text
Actor issues a public statement.
Actor sends an order within its own organisation, where compliance is not contested.
Actor makes a feasible declaration or commitment that binds only itself.
```

Use `automatic_failure` where the requested Effect is impossible given authoritative state/timescale.

Most strategically consequential contested actions should normally be:

```text
probabilistic
```

---

# 25. Probability Calibration

The MVP does not require mathematically perfect calibration.

However, the Umpire should avoid fake precision.

Recommended practical anchors:

```text
0.10 very unlikely
0.30 unlikely
0.50 roughly even
0.70 likely
0.90 very likely
```

The Umpire may use intermediate values if useful, but benchmark consistency matters more than apparent precision.

---

# 26. Server Resolution

For `probabilistic`:

```python
roll = rng.random()
success = roll <= probability
```

The server selects either:

```text
success_effects
```

or:

```text
failure_effects
```

The Umpire does not perform or fabricate the roll.

---

# 27. Fixed Umpire Policy for Benchmarks

When comparing player models, fix:

```text
Umpire model
Umpire provider if practical
Umpire prompt version
temperature/reasoning configuration
adjudication schema
scenario version (including its declared rules)
```

If any of these change, runs should be treated as a separate experimental condition.

Under `consequence_mode: bounded_effects_v1` (§28), the consequence resolver's model, prompt
version, temperature/reasoning configuration and output schema are fixed in the same way and
sealed in the run manifest. `none` and `bounded_effects_v1` are different conditions.

---

# 28. Consequence Agent

The Consequence Agent (the *consequence resolver*) runs only under the persisted game-loop
condition `consequence_mode: bounded_effects_v1` (state-machine §21 Option B; D-091). Under
`none`, the default, it is never called.

It is not a player and not a second Umpire. It proposes the **aggregate, secondary**
consequences of the whole completed turn: what follows from several same-turn outcomes taken
together, which no single adjudication could see. It runs once per turn, after the last
adjudication. It draws no randomness, decides no Argument, and gives no Actor another
decision.

## 28.1 Input

The server builds the context. Everything in it is public; nothing hidden is ever supplied.

```json
{
  "scenario_summary": "...",
  "turn": 2,
  "turn_length": "2 weeks",

  "outcomes": [
    {
      "event_id": 41,
      "actor_id": "arkania",
      "action": "Begin mobilisation...",
      "desired_effect": "Reserve formations begin deploying...",
      "mode": "probabilistic",
      "success": true,
      "created_effect_ids": ["uuid"]
    }
  ],

  "active_effects": [
    {"id": "uuid", "description": "Arkanian reserve formations begin deploying.", "created_turn": 2}
  ]
}
```

- `outcomes` has one entry per `argument_adjudicated` Event of the current turn, in event order.
  Each entry carries only what that public Event and the public Argument already disclose. The
  Umpire rationale, probability and roll, the Cons and any owner reply are **not** included.
- `active_effects` lists the game's currently active **public** Effects, including those created
  this turn.
- `scenario_rules` is added verbatim, exactly as in §21, when the scenario declares rules, and
  is absent otherwise.
- No Actor private information, private Effect, Message, Note, Resource content or sealed Con
  is ever supplied.

## 28.2 Output

```json
{
  "effects": [
    {
      "description": "Commercial shipping insurance premiums in the strait rise sharply.",
      "rationale": "Arkanian mobilisation and Belvar's naval exercise together raise perceived shipping risk.",
      "evidence": [
        {"kind": "event", "id": 41},
        {"kind": "event", "id": 47}
      ]
    }
  ]
}
```

Validation is the server's, and it is all or nothing (state-machine INV-CONSEQ-002/003):

```text
0–3 proposed Effects
description and rationale non-empty, each at most 4000 characters
1–8 distinct evidence references per Effect
{"kind": "event", "id": <int>}      an argument_adjudicated Event of this game, current turn
{"kind": "effect", "id": "<uuid>"}  an Effect of this game, active and public at commit
```

Any violation rejects the whole proposal: no Effect, no record. The game pauses with
`consequence_resolver_unavailable`, and resuming re-runs the pass. An empty `effects` list is a
valid pass: the record is written and the turn completes.

Accepted Effects are public. They neither supersede nor expire, and each records its origin
(`source_consequence_turn`) and its evidence in the turn's consequence record. The resolver must
not restate private material and has none to restate.

The concrete LLM resolver lives outside `app/`, like the Umpire. It uses the versioned
`consequence-v1.1` prompt and a strict structured output schema, with at most 4 model calls per
pass. A reference that is outside the supplied context is retried within that bound. Every
attempt is recorded as an `AgentRun` with `role = "consequence"`. The server remains
authoritative.

---

# 29. Resource-Seeking Behaviour

The agent is not required to read every Resource.

That is intentional.

The system should log:

```text
search query
Resources returned
Resource read
turn
Actor
timestamp
```

This supports later analysis of:

- whether the agent researched before acting;
- which information it ignored;
- whether it repeatedly searched the same topic;
- whether information-seeking correlated with outcomes.

---

# 30. Future External Tools

MVP+ may add:

```text
MCP
REST tools
local Python tools
simulation tools
GIS
databases
economic models
campaign models
```

The future invariant is:

> External tools provide information, calculations, simulation results or evidence; they do not directly mutate authoritative AMAW state.

The Agent Tool Gateway should enforce per-Actor permissions outside the model.

---

# 31. Versioned Prompt Files

Every prompt version the reference agents have used stays in the repository, one file per
version. The versions the code can load sit at the top of `prompts/`; a version that can no
longer be loaded moves unchanged to `prompts/superseded/` (D-103):

```text
prompts/player-v1.10.md
prompts/player-v1.11.md
prompts/player-v1.12.md
prompts/player-v1.13.md
prompts/umpire-v1.3.md
prompts/umpire-v1.4.md
prompts/umpire-v1.5.md
prompts/umpire-v1.6.md
prompts/umpire-v1.7.md
prompts/consequence-v1.1.md
prompts/evaluator-v1.1.md
prompts/superseded/player-v1.1.md … player-v1.9.md
prompts/superseded/umpire-v1.1.md
prompts/superseded/umpire-v1.2.md
```

Only the loadable versions are packaged as runtime data. `prompts/README.md` is the index.
A run manifest identifies a prompt by version and hash, so moving a superseded file changes
no recorded run.

Prompt version changes are experiment-relevant changes.

Do not silently modify prompt text while retaining the same version identifier.
