# AMAW API Contract

**Version:** 0.4.10 (D-099: an omitted or `null` Diplomacy, Con or Con-reply field in `POST /admin/games` falls back to the scenario's `game.*` default, then the released default)  
**Status:** Normative for Lean MVP  
**Aligned with:** `PRD.md`, `TRD.md`. Earlier versions of this contract are recorded in the decision log.

---

## 1. Purpose

This document defines the external HTTP contract between the AMAW core server and autonomous Actor processes.

The API is intentionally small.

The server is authoritative for:

- game phase;
- active Actor;
- legal operations;
- Argument budget;
- visibility;
- adjudication result application;
- RNG;
- Effect creation/deactivation;
- turn advancement.

Clients request actions. Clients never directly mutate authoritative game state.

---

## 2. Protocol

### 2.1 Transport

MVP transport:

```text
HTTP/1.1 or HTTP/2
JSON request/response bodies
Bearer authentication
```

Polling is sufficient.

SSE and WebSockets are not required.

### 2.2 Content type

Requests containing JSON must send:

```http
Content-Type: application/json
```

Responses use:

```http
Content-Type: application/json
```

unless an endpoint explicitly returns a file/export.

### 2.3 Actor authentication

Actor endpoints require:

```http
Authorization: Bearer <seat-token>
```

The server resolves:

```text
seat-token
    ↓
Seat
    ↓
game_id + actor_id
```

The Actor ID in a request body is never trusted as identity.

### 2.4 Admin authentication

Administrative endpoints use a separate admin/facilitator token.

A Seat token must never authorize an admin endpoint.

---

## 3. Error envelope

All API errors should use a consistent shape:

```json
{
  "error": {
    "code": "wrong_phase",
    "message": "Arguments may only be submitted during the argument phase.",
    "details": {
      "current_phase": "diplomacy"
    }
  }
}
```

`details` is optional.

### 3.1 Core error codes

| HTTP | Code | Meaning |
|---:|---|---|
| 400 | `invalid_request` | Request is syntactically valid JSON but logically malformed |
| 401 | `invalid_token` | Missing/invalid Bearer token |
| 403 | `forbidden` | Authenticated Actor lacks access |
| 404 | `not_found` | Resource does not exist or is intentionally hidden |
| 409 | `wrong_phase` | Operation illegal in current phase |
| 409 | `not_active_actor` | Actor is not currently allowed to perform action |
| 409 | `argument_budget_exhausted` | Actor has used allowed primary Arguments |
| 409 | `already_participated` | Actor already passed/completed required participation |
| 409 | `idempotency_conflict` | Same idempotency key used with different payload |
| 409 | `state_conflict` | Operation lost a concurrency/state race |
| 422 | `validation_error` | Schema validation failed |
| 429 | `rate_limited` | Safety/runaway-agent protection |
| 500 | `internal_error` | Unexpected server error |
| 503 | `adjudicator_unavailable` | Umpire unavailable and game cannot continue |
| 503 | `consequence_resolver_unavailable` | Consequence resolver unavailable, or its proposal failed validation; the game is paused in `consequences` (D-091) |

For visibility-sensitive resources, prefer `404 not_found` over confirming that a hidden resource exists.

---

## 4. Idempotency

Mutating Actor endpoints must accept:

```http
Idempotency-Key: <opaque-client-generated-uuid>
```

Required for:

- send Message;
- submit Argument;
- submit Con;
- pass;
- ready;
- save Note.

### 4.1 Behaviour

If the same Actor submits:

```text
same endpoint
+ same idempotency key
+ same semantic payload
```

the server returns the original successful result.

If the key is reused with a different payload:

```http
409 idempotency_conflict
```

The server should scope keys at least by:

```text
game_id + seat_id + operation family
```

---

## 5. Common response fields

Where relevant, responses should include:

```json
{
  "game_id": "uuid",
  "turn": 2,
  "phase": "argument"
}
```

The exact internal database schema does not need to be exposed.

---

# 6. Actor Context

## 6.1 GET `/games/{game_id}/context`

Returns the Actor-specific game context.

### Request

```http
GET /games/82a971/context
Authorization: Bearer <seat-token>
```

### Response

```json
{
  "game_id": "82a971...",
  "turn": 2,
  "max_turns": 6,
  "phase": "argument",
  "status": "running",

  "active_actor_id": "arkania",

  "you": {
    "actor_id": "arkania",
    "name": "Republic of Arkania",
    "public_description": "A large regional power.",
    "private_brief": "You are the political leadership of Arkania...",
    "objectives": [
      "Increase influence over the Northern Strait.",
      "Prevent direct Belvar intervention."
    ],
    "private_information": [
      "Reserve mobilisation requires two turns."
    ]
  },

  "effects": [
    {
      "id": "uuid",
      "description": "Commercial shipping continues normally.",
      "visibility": "public",
      "created_turn": 0,
      "expires_turn": null,
      "active": true
    }
  ],

  "current_argument": null,

  "requires_input": true,

  "available_actions": [
    "send_message",
    "search_resources",
    "read_resource",
    "make_argument",
    "get_notes",
    "save_note"
  ]
}
```

### Requirements

The server must:

- filter all private information before response construction;
- expose only legal `available_actions`;
- never include another Actor's Notes or private information;
- never include hidden Resource metadata.

Clients should use `available_actions` rather than deriving legality solely from `phase`. Agent-facing action names should match the baseline tool contract; during Con participation these include `make_con` and `pass_con` as applicable, and during `reply` the Argument owner's actions include `reply_to_cons`.

Under the released `sealed_cons` Con-reply policy the response has exactly the shape above: no
Actor ever reads a Con. Under the experimental `visible_reply` policy (state-machine §18.1) the
projection is extended for **every** Actor from the moment Cons are revealed:

```json
{
  "current_argument": {
    "id": "uuid",
    "actor_id": "arkania",
    "action": "...",
    "desired_effect": "...",
    "pros": ["..."],
    "status": "awaiting_reply",
    "cons": [
      {"con_id": "uuid", "actor_id": "belvar", "reason": "Rail disruption will slow movement."}
    ],
    "owner_reply": null
  },
  "revealed_exchanges": [
    {
      "argument_id": "uuid",
      "actor_id": "cestia",
      "action": "...",
      "desired_effect": "...",
      "cons": [{"con_id": "uuid", "actor_id": "arkania", "reason": "..."}],
      "owner_reply": {"points": [{"con_id": "uuid", "text": "..."}]},
      "resolved": true
    }
  ]
}
```

`current_argument.cons` is the frozen set in canonical `(actor_id, con_id)` order — the order the
Umpire receives, never completion order — and is absent while `phase` is `cons` (INV-CON-007).
While `phase` is `reply` the Argument's `status` is `awaiting_reply`. `owner_reply` is `null` until the owner submits; an empty `points` list is
the recorded pass. `revealed_exchanges` lists the current turn's earlier Arguments that entered
`reply`, oldest first, and is empty at each turn start. Neither key is present under `sealed_cons`.

When the scenario's `primary_arguments_per_actor` is above 1 (round-robin Argument rounds,
state-machine §17/§20, D-094), and only then, the context adds:

```json
{
  "argument_budget": {"primary_arguments_per_actor": 2, "round": 2, "used": 1},
  "current_argument": {"id": "uuid", "budget_slot": 2}
}
```

- `round` is the current Argument round. It is `null` in `diplomacy`, and in `consequences` or
  after completion.
- `used` counts this Actor's accepted Arguments this turn.
- `current_argument.budget_slot` is the round in which the current Argument was made. The other
  `current_argument` keys are as above.

At budget 1 neither key is present, and the response is unchanged.

## 6.2 Incremental activation context (post-MVP)

`POST /games/{game_id}/activations` begins or resumes one Actor activation delivery. Identity
comes only from the Seat bearer token. The response combines the §6.1 context with:

```json
{
  "delivery_receipt": "opaque-value",
  "context_mode": "incremental-context-v1",
  "memory_version": "strategic-memory-v1",
  "messages_received": [],
  "strategic_memory": {
    "commitments_made": [],
    "commitments_or_offers_received": [],
    "outstanding_offers_and_negotiations": [],
    "red_lines_and_constraints": [],
    "uncertain_or_disputed_claims": [],
    "relationships_and_likely_coalitions": [],
    "strategic_assessment": "",
    "intended_next_action": ""
  }
}
```

The server creates at most one pending delivery per `game_id + actor_id`. Repeating this call
before acknowledgement returns the same receipt and the same bounded Message snapshot. Only
Messages visible under §7.1, sent by another Actor, and committed after the last acknowledged
boundary are included. The boundary itself is server-internal and must not expose hidden Message
or event counts.

`POST /games/{game_id}/activations/{delivery_receipt}/ack` accepts the complete structured
`strategic_memory`. It atomically replaces the Actor's one current memory state and advances the
acknowledged boundary through that delivery. An exact retry returns the original success; reuse
with different memory returns `409 idempotency_conflict`. A missing, foreign or stale receipt is
refused without changing memory or delivery progress.

Clients call acknowledgement only after the model activation returns and its selected legal tool
operations have completed. They do not acknowledge model/provider failure, timeout, guardrail
termination or lifecycle stall. The endpoint changes agent memory/delivery state only; it does
not change game phase, readiness, legality or `available_actions`.

---

# 7. Messages

## 7.1 GET `/games/{game_id}/messages`

Returns Messages visible to the current Actor.

Optional query parameters:

```text
after=<event_id>
```

Example response:

```json
{
  "messages": [
    {
      "id": "uuid",
      "turn": 1,
      "sender_actor_id": "belvar",
      "recipients": ["arkania"],
      "public": false,
      "text": "We are willing to discuss restraint.",
      "created_at": "2026-09-10T14:00:00Z"
    }
  ]
}
```

Private Messages are visible only to:

- sender;
- listed recipients.

## 7.2 POST `/games/{game_id}/messages`

### Request

```http
POST /games/{game_id}/messages
Authorization: Bearer <seat-token>
Idempotency-Key: 8f59f8da-...
Content-Type: application/json
```

Private Message:

```json
{
  "recipients": ["cestia"],
  "public": false,
  "text": "We are prepared to discuss guarantees."
}
```

Public Message:

```json
{
  "recipients": [],
  "public": true,
  "text": "Arkania announces that it remains committed to regional stability."
}
```

### Validation

- sender comes from Seat token;
- every private recipient must be a valid Actor in the game;
- public Messages must use an empty recipient list;
- private Messages must contain at least one recipient;
- message length must be within configured limit.

### Response

```json
{
  "message": {
    "id": "uuid",
    "sender_actor_id": "arkania",
    "recipients": ["cestia"],
    "public": false,
    "text": "We are prepared to discuss guarantees."
  }
}
```

---

# 8. Arguments

## 8.1 POST `/games/{game_id}/arguments`

Submits the active Actor's primary Matrix Argument.

### Request

```json
{
  "action": "Begin mobilisation of reserve forces.",
  "desired_effect": "Reserve formations begin deploying toward the frontier.",
  "pros": [
    "Mobilisation plans already exist.",
    "Rail capacity is currently available.",
    "Domestic support has increased."
  ]
}
```

### Validation

The server must verify:

```text
game is running
current phase == argument
authenticated Actor == active Actor
Actor has an unused Argument slot
action is non-empty
desired_effect is non-empty
1 <= len(pros) <= configured maximum
request size limits satisfied
```

Default:

```text
primary_arguments_per_actor = 1
```

With a budget above 1 the unused slot is the Actor's slot for the current Argument round
(D-094). The Actor can use it only while it is the active Actor of that round, and
`409 argument_budget_exhausted` still applies once every slot is used.

### Success response

```json
{
  "argument": {
    "id": "uuid",
    "turn": 2,
    "actor_id": "arkania",
    "action": "Begin mobilisation of reserve forces.",
    "desired_effect": "Reserve formations begin deploying toward the frontier.",
    "pros": [
      "Mobilisation plans already exist.",
      "Rail capacity is currently available.",
      "Domestic support has increased."
    ],
    "status": "open_for_cons"
  }
}
```

The server then transitions to the Cons phase for that Argument.

---

# 9. Cons

## 9.1 POST `/games/{game_id}/arguments/{argument_id}/cons`

### Request

```json
{
  "reason": "Rail capacity is reduced by ongoing industrial disruption."
}
```

### Validation

The server verifies:

- current phase is `cons`;
- supplied Argument is the current Argument;
- caller is not the Argument owner;
- caller is eligible to participate (under `sampled_sealed`, a sealed challenger of this Argument;
  a non-selected opponent receives `403 forbidden` here and on `/pass`);
- configured Con limit is not exceeded;
- caller has not passed;
- reason is non-empty and within length limit.

### Success response

```json
{
  "con": {
    "id": "uuid",
    "argument_id": "uuid",
    "actor_id": "belvar",
    "reason": "Rail capacity is reduced by ongoing industrial disruption."
  }
}
```

An Actor may submit more than one Con up to the configured maximum unless it has passed.

## 9.2 POST `/games/{game_id}/arguments/{argument_id}/pass`

Marks the current Actor's Con participation complete without another Con.

Request body may be empty:

```json
{}
```

Success:

```json
{
  "passed": true,
  "argument_id": "uuid",
  "actor_id": "cestia"
}
```

When all eligible Actors have completed participation, the server may advance to adjudication —
or, under `visible_reply` with at least one Con, to `reply` (state-machine §18).

## 9.3 POST `/games/{game_id}/arguments/{argument_id}/reply`

`visible_reply` only. The Argument owner's single answer to the revealed Cons.

### Request

```json
{
  "points": [
    {"con_id": "uuid", "text": "Rail disruption affects freight, not the passenger lines the plan uses."}
  ]
}
```

`points` may be empty: `{"points": []}` is the explicit pass and is the only way to decline.

### Validation

The server verifies, inside the transaction that persists the reply (INV-REPLY-001/002):

- the game's `con_reply_mode` is `visible_reply` (`409 wrong_phase` otherwise — the phase never
  exists under `sealed_cons`);
- current phase is `reply`;
- supplied Argument is the current Argument;
- caller is the Argument owner (`403 forbidden` for every other Actor);
- no reply exists for this Argument (`409 already_participated`);
- `0 ≤ len(points) ≤ reply_max_points`;
- every `con_id` belongs to the frozen Con set of this Argument and appears at most once;
- every `text` is non-empty and within the Con-reason length limit;
- no other field is accepted (`422 validation_error`): a reply cannot carry an action, desired
  effect or pro.

### Success response

```json
{
  "reply": {
    "argument_id": "uuid",
    "actor_id": "arkania",
    "points": [
      {"con_id": "uuid", "text": "Rail disruption affects freight, not the passenger lines the plan uses."}
    ]
  }
}
```

Acceptance closes the phase: the Argument moves from `awaiting_reply` to `adjudicating` and the
game to `adjudication` in the same transaction. A replay with the same `Idempotency-Key` and the
same payload returns the recorded reply (§4); any other request once the reply exists is
`409 already_participated`.

---

# 10. Resources

## 10.1 GET `/games/{game_id}/resources/search?q=...&contract=...`

Searches only resources visible to the current Actor.

`contract` is optional and defaults to the released `search-v2` deterministic token-coverage
ranking (D-087/D-088). `search-v1` selects the original whole-query substring ranking and remains
supported for reproducing runs recorded under it. No other value is valid. The selected contract
changes matching/ranking/snippet placement only; the response shape, result bound and security
rule are identical.

A client that means one specific contract states it rather than relying on this default; the
reference player always sends the contract its sealed condition names.

Example:

```http
GET /games/{game_id}/resources/search?q=Belvar%20fuel%20logistics&contract=search-v1
```

Response:

```json
{
  "query": "Belvar fuel logistics",
  "results": [
    {
      "id": "belvar-logistics",
      "name": "Belvar Logistics Overview",
      "type": "background",
      "tags": ["belvar", "logistics"],
      "snippet": "Belvar depends on two principal fuel import terminals..."
    }
  ]
}
```

### Security rule

Visibility filtering must occur **before**:

- matching;
- ranking;
- snippet generation;
- result counting.

Hidden resources must not leak through:

- resource IDs;
- names;
- tags;
- snippets;
- counts;
- path names;
- metadata.

## 10.2 GET `/games/{game_id}/resources/{resource_id}`

Returns an authorised Resource.

Example:

```json
{
  "resource": {
    "id": "regional-history",
    "name": "Regional History",
    "type": "background",
    "content": "The Northern Strait dispute began...",
    "tags": ["history", "politics"],
    "metadata": {
      "period": "1990-2026"
    }
  }
}
```

For file-backed attachments that are not parsed, the response may contain attachment metadata rather than inline content.

Unauthorized resources return `404 not_found`.

---

# 11. Notes

## 11.1 GET `/games/{game_id}/notes`

Returns only the current Actor's Notes.

```json
{
  "notes": [
    {
      "id": "uuid",
      "text": "Cestia may remain neutral if offered economic guarantees.",
      "created_at": "2026-09-10T14:02:00Z"
    }
  ]
}
```

## 11.2 POST `/games/{game_id}/notes`

Request:

```json
{
  "text": "Cestia may remain neutral if offered economic guarantees."
}
```

Notes are:

- private;
- game-scoped;
- Actor-scoped;
- not model chain-of-thought storage.

Notes remain the compatible append-only notebook surface for earlier agent conditions. The
`strategic-memory-v1` condition uses the single replacement state in §6.2 instead of adding a
strategic Note per activation.

---

# 12. Readiness

## 12.1 POST `/games/{game_id}/ready`

Signals that the Actor currently has no further useful action in a phase where readiness is legal.

Request:

```json
{}
```

Response:

```json
{
  "ready": true,
  "turn": 1,
  "phase": "diplomacy"
}
```

For Lean MVP, readiness is primarily used in diplomacy.

Phase advancement may occur when:

```text
all required Actors are ready
OR timeout occurs
OR admin forces advancement
```

If a ready Actor receives a new private Message before diplomacy closes, the server must clear that recipient's ready state before evaluating the diplomacy close condition. This is deterministic Lean MVP behaviour. The normal polling loop is sufficient to activate that Actor again; event-driven push delivery remains MVP+.

---

# 13. Events

## 13.1 GET `/games/{game_id}/events?after={event_id}`

Returns Actor-visible Events after the supplied Actor-view cursor.

Example:

```json
{
  "events": [
    {
      "id": 17,
      "turn": 2,
      "type": "argument_submitted",
      "actor_id": "arkania",
      "payload": {
        "argument_id": "uuid"
      },
      "created_at": "2026-09-10T14:03:00Z"
    }
  ],
  "last_event_id": 17
}
```

The Event endpoint must not become a side channel for hidden information.

For an Actor endpoint, `id` and `after` are stable one-based ordinals over the Events visible to
that authenticated Actor in that game. They are not database Event IDs. Hidden Events and Events
from other games do not consume an Actor-visible ordinal. A cursor is valid only for the same
Actor and game. Administrative export may retain authoritative database Event IDs.

Actor-visible events should either:

- contain only public/Actor-authorised payloads; or
- be filtered/redacted before return.

Unknown event types and events with missing, malformed or inaccessible referenced objects are
filtered out. Visibility fails closed.

Per event type (D-070):

- Public to every current Actor: `game_started`, `phase_started`, `actor_ready`,
  `argument_submitted`, `cons_revealed`, `argument_adjudicated`, `turn_completed`,
  `game_completed`, `game_paused`, `consequences_resolved` (D-091; emitted only under
  `bounded_effects_v1`, and it names only public consequence Effects), `argument_round_started`
  (D-094; emitted only when the Argument budget is above 1, carrying `turn` and `round`).
- Object-scoped: `message_sent` follows the Message's visibility; `effect_created` /
  `effect_deactivated` follow the Effect's.
- Never Actor-visible: `con_submitted` and `con_passed` — they name the opponent and the moment
  it completed, which is participation and completion order (state-machine INV-CON-007) under
  both reply policies, the author's own included. Under `visible_reply` the frozen set is
  disclosed once, at close, through `cons_revealed` (`con_ids` in canonical `(actor_id,
  con_id)` order) and `current_argument.cons`; under `sealed_cons` it never is. The
  authoritative log and the administrative export retain every event.
- Everything else (`con_challengers_selected`, `owner_reply_submitted`, …) is filtered until a
  policy admits it.

---

# 14. Administrative endpoints

Lean MVP may expose a minimal private admin surface:

```http
POST /admin/games
POST /admin/games/{game_id}/start
POST /admin/games/{game_id}/diplomacy-rounds/complete
POST /admin/games/{game_id}/pause
POST /admin/games/{game_id}/advance
POST /admin/games/{game_id}/end
GET  /admin/games/{game_id}/export
```

These endpoints are intended for:

- CLI orchestration;
- test harnesses;
- benchmark runner.

They are not a public user-management API.

`POST /admin/games` accepts `diplomacy_mode` (`open_diplomacy`, the default, or
`bounded_rounds`) and `diplomacy_rounds_per_turn` (a positive integer required only for bounded
mode). The runner reports a fully settled round to `.../diplomacy-rounds/complete` with its
authoritative `turn` and one-based `round_number`. This admin endpoint is idempotent for an already
completed round and delegates closure to the core state machine; it is not a client-authored phase
transition.

`POST /admin/games` also accepts `con_mode` (`open_cons`, the default, or `sampled_sealed`) and
`con_challengers_per_argument` (a positive integer, at most the number of opponents, required only
for sampled mode). The server seals the challengers itself at Argument acceptance; no endpoint lets
a client choose or reorder them.

`POST /admin/games` also accepts `con_reply_mode` (`sealed_cons`, the default, or
`visible_reply`) and `reply_max_points` (an integer from 1 to 3, required only for
`visible_reply`, rejected under `sealed_cons`). Both resolve at creation and are persisted; there
is no runtime toggle.

Each of these condition fields may be omitted or `null`, which means "not supplied". The server
then resolves the field in one order: the value in the body, then the scenario's `game.*`
default (scenario-format §6; D-099 for the Diplomacy and Con fields), then the released default
named above. A count follows its mode: an omitted count comes from the scenario only when the
resolved mode is the scenario's. `con_reply_mode` and `reply_max_points` have no scenario default.
The Game persists the resolved condition, never the unresolved request, and the pairing rules
apply to the resolved values. A client that sends none of these fields gets the released
condition for a scenario that declares no defaults.

`POST /admin/games` also accepts `consequence_mode` (`none` or `bounded_effects_v1`; D-091).
When it is omitted or `null`, the scenario's `game.consequence_mode` applies, and that defaults
to `none`. The value resolves at creation and is persisted with `consequence_policy_version`;
there is no runtime toggle. Under `bounded_effects_v1`, `POST .../advance` has one more
server-owned step:

- after the last Actor's adjudication, it returns `phase: consequences` with no active Actor;
- the next call runs the single consequence pass with the injected resolver (state-machine §21),
  then completes the turn;
- a game already holding the turn's committed consequence record is only advanced, and the pass
  is never re-run.

An unavailable resolver, or a proposal that fails server validation, pauses the game in
`consequences`, and `.../advance` answers `503 consequence_resolver_unavailable`. Nothing is
committed. Resuming re-runs the pass. Under `none` the endpoint behaves exactly as before.

`POST /admin/games` also accepts `require_run_manifest` (boolean, default `false`), persisted
and immutable after creation — no endpoint changes it. `POST /admin/games/{game_id}/run-manifest`
seals the Game's immutable run configuration manifest (TRD §28) and may only *create* one while
the Game is in `lobby`; once the Game has started, an exact retry of the sealed configuration
returns the existing record and anything else is a `409` conflict. `POST .../start` on a Game
created with `require_run_manifest: true` and no sealed manifest is a `409` state conflict. The
autonomous runner and benchmark runner always set the flag, so a benchmark's pre-committed
condition is a server-enforced fact rather than the runner's write order (D-068). The export's
`game` section reports the flag.

---

# 15. Export

## 15.1 GET `/admin/games/{game_id}/export`

Returns a complete machine-readable game record.

Minimum contents:

```text
scenario id/version
game configuration, including the snapshotted scenario rules (`game.scenario_rules`; an empty list when none are declared)
model/Actor assignments
seed
Messages
Arguments
Cons
adjudications
RNG rolls
Effects
Events
resource access records
Actor objectives
private Notes
visibility/ownership metadata
scenario and Resource content hashes
run configuration manifest
database identity: the schema version stamped on the database the record was read from and whether declared foreign keys were enforced on that connection (`database.schema_version`, `database.foreign_keys_enforced`)
Diplomacy policy, configured bound, completed rounds and closure reason by turn
Con policy, configured challenger count, per-Argument sealed challengers and opposition counts
Con-reply policy, configured reply bound and per-Argument owner replies (`argument_replies`)
Consequence policy and mode (`game.consequence_policy_version`, `game.consequence_mode`), one `consequence_records` entry per resolved (turn) pass with each accepted Effect's rationale and evidence references, and each Effect's origin (`source_argument_id`, `source_consequence_turn` or `source_inject_id`) — export format 15 (D-091)
Scheduled injects (`game.scenario_injects`, the creation-time snapshot including injects not yet released) — export format 16 (D-095)
current structured strategic memory and delivery metadata
agent errors
timestamps
```

This is an administrative/research export. It intentionally includes game-scoped private data, including private Messages, Actor objectives and explicit Notes, with ownership/visibility metadata retained. A Seat token must never authorize this endpoint. The export does not require or expose hidden model chain-of-thought.

The export is intended for analysis and authoritative game reconstruction, not deterministic replay of hosted model generation.

The whole record, its derived `run_summary` included, is read in one transaction: no section may count rows another section does not contain.

---

# 16. API invariants

The API implementation must preserve these invariants:

```text
API-INV-001
Actor identity comes from authentication, never request body.

API-INV-002
Hidden information is filtered before serialization.

API-INV-003
Only the active Actor may submit a primary Argument.

API-INV-004
An Actor cannot Con its own Argument.

API-INV-005
The Umpire cannot directly mutate Effects.

API-INV-006
The server performs all probabilistic RNG draws.

API-INV-007
Mutating Actor calls are idempotent.

API-INV-008
Clients cannot directly advance authoritative game state.

API-INV-009
available_actions is server-computed.

API-INV-010
Errors must not reveal hidden Resource existence.

API-INV-011
Incremental delivery and strategic memory are private and scoped by game_id + authenticated Actor.

API-INV-012
Only successful activation acknowledgement advances an Actor's delivery boundary.
```

---

# 17. Deferred API features

Not required for Lean MVP:

```text
WebSockets
SSE
human UI endpoints
conversation/channel APIs
organisation APIs
external MCP/tool gateway APIs
pending external jobs
Secret Arguments
human adjudication UI
full replay API
enterprise account management
OAuth / SSO
```

Add them only after MVP-3 proves the core experiment is useful.
