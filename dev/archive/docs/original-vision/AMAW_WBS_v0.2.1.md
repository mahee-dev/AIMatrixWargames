# AMAW — Work Breakdown Structure

**Version:** 0.2.2
**Basis:** AMAW TRD v0.5.2 — Lean MVP + Scale Extension Contracts
**Purpose:** Define the work required to deliver the first usable AMAW autonomous-agent wargaming and benchmarking MVP.

---

# 1. Project Foundation

## 1.1 Repository and Development Environment

### 1.1.1 Repository setup
- Create Python project structure.
- Configure `pyproject.toml`.
- Configure `uv`.
- Add `.gitignore`.
- Add `.env.example`.
- Add baseline README.

**Deliverable:** Developer can clone repository and install dependencies.

**Done when:**
```bash
uv sync
```
completes successfully from a fresh checkout.

### 1.1.2 Runtime configuration
- Define environment-variable configuration.
- Add OpenRouter API key configuration.
- Add database path configuration.
- Add basic logging configuration.

**Deliverable:** Central application configuration.

### 1.1.3 Test harness
- Configure pytest.
- Add temporary SQLite test database support.
- Add fixtures for games, scenarios and Actors.

**Deliverable:** Automated test suite can run locally.

**Done when:**
```bash
uv run pytest
```
runs successfully.

---

# 2. MVP-0 — Authoritative Game Engine

**Objective:** Three scripted players complete a deterministic six-turn Matrix Game without any LLM dependency.

---

## 2.1 Core Domain Model

### 2.1.1 Game
Implement:
- Game ID.
- scenario ID.
- turn.
- maximum turns.
- phase.
- status.
- turn order.
- active Actor.
- RNG seed.

### 2.1.2 Actor
Implement:
- Actor ID.
- name.
- public description.
- private brief.
- objectives.
- private starting information.

### 2.1.3 Seat
Implement:
- Seat ID.
- game ID.
- Actor ID.
- bearer-token hash.

### 2.1.4 Message
Implement:
- sender.
- recipients.
- public/private flag.
- message text.
- turn.
- timestamp.

### 2.1.5 Argument
Implement:
- Actor.
- action.
- desired Effect.
- Pros.
- status.
- turn.

### 2.1.6 Con
Implement:
- Argument association.
- submitting Actor.
- reason.

### 2.1.7 Effect
Implement:
- description.
- visibility.
- visible Actors.
- source Argument.
- creation turn.
- optional expiry.
- active flag.
- minimal `supersedes` relationship.

### 2.1.8 Event
Implement immutable event record containing:
- game.
- turn.
- event type.
- Actor.
- payload.
- timestamp.

### 2.1.9 Note
Implement:
- game.
- Actor.
- text.
- timestamp.

**Deliverable:** Complete minimal domain model.

---

## 2.2 Persistence

### 2.2.1 SQLite setup
- Configure SQLModel engine.
- Create tables.
- Implement database session lifecycle.

### 2.2.2 Persistence operations
Implement create/read/update operations required by GameService for:
- games;
- Actors;
- Seats;
- Messages;
- Arguments;
- Cons;
- Effects;
- Events;
- Notes.

### 2.2.3 Transactions
Ensure state-changing game operations are performed transactionally.

**Deliverable:** Game survives process-level application operations without relying on in-memory state.

---

## 2.3 Scenario Loader

### 2.3.1 Scenario manifest schema
Implement YAML validation for:
- scenario metadata;
- game configuration;
- Actor definitions;
- turn order;
- starting Effects.

### 2.3.2 Scenario validation
Reject:
- duplicate Actor IDs;
- unknown Actors in turn order;
- invalid visibility;
- invalid turn counts;
- malformed starting Effects.

### 2.3.3 Game creation from scenario
Create:
- Game;
- Actors;
- Seats;
- starting Effects.

**Deliverable:** A valid scenario directory can create a playable game.

---

## 2.4 Game State Machine

### 2.4.1 Game start
Implement:
```text
LOBBY → DIPLOMACY
```

### 2.4.2 Diplomacy phase
Implement:
- messages;
- readiness;
- phase completion.

### 2.4.3 Argument phase
Implement:
- active Actor enforcement;
- primary Argument submission;
- Argument budget enforcement.

### 2.4.4 Cons phase
Implement:
- eligible opposing Actors;
- Con submission;
- pass;
- maximum Cons.

### 2.4.5 Adjudication phase
Implement:
- freeze player state-changing operations;
- request adjudication;
- accept structured result.

### 2.4.6 Effect application
Implement:
- successful/failure Effects;
- optional Effect supersession;
- Effect expiry.

### 2.4.7 Actor sequencing
Advance:
```text
Actor A → Actor B → Actor C
```

### 2.4.8 Turn sequencing
Advance:
```text
TURN N → TURN N+1
```

### 2.4.9 Game completion
Complete game when:
```text
turn == max_turns
```

**Deliverable:** Complete deterministic game lifecycle.

---

## 2.5 Visibility and Information Walls

### 2.5.1 Message visibility
Ensure private Messages are visible only to:
- sender;
- recipients.

### 2.5.2 Actor information
Ensure private Actor briefs/objectives/information are available only to the associated Actor.

### 2.5.3 Effect visibility
Filter public/private Effects by Actor.

### 2.5.4 Note visibility
Ensure Notes are accessible only to their Actor.

### 2.5.5 Visibility service
Centralise visibility checks rather than implementing endpoint-specific filtering.

**Deliverable:** No normal player API can expose another Actor's private state.

---

## 2.6 Idempotency and State Integrity

### 2.6.1 Idempotency keys
Support idempotency for:
- Message submission;
- Argument submission;
- Con submission;
- pass;
- ready;
- Note save.

### 2.6.2 Duplicate prevention
Prevent:
- duplicate primary Arguments;
- double adjudication;
- double phase advancement;
- duplicate Cons caused by retries.

### 2.6.3 State revalidation
Validate phase and Actor eligibility inside state-changing transactions.

**Deliverable:** Duplicate/retried client operations do not corrupt a game.

---

## 2.7 RNG

### 2.7.1 Game RNG
Initialise deterministic game RNG from scenario/run seed.

### 2.7.2 Probabilistic adjudication
Implement:
```python
roll = rng.random()
success = roll <= probability
```

### 2.7.3 RNG logging
Record:
- probability;
- roll;
- result.

**Deliverable:** Server-owned random outcomes are reproducible.

---

## 2.8 Fake Umpire

### 2.8.1 Umpire interface
Define minimal structured adjudication interface.

### 2.8.2 Fake implementation
Implement deterministic adjudicator for tests.

### 2.8.3 Effect proposals
Support:
- automatic success;
- automatic failure;
- probabilistic result;
- success Effects;
- failure Effects.

**Deliverable:** Game engine can complete without any real model provider.

---

## 2.9 Scripted Players

### 2.9.1 Scripted Actor behaviour
Create deterministic scripted players for at least three Actors.

### 2.9.2 Phase participation
Players must:
- send Messages;
- submit Arguments;
- submit/pass Cons;
- signal ready.

### 2.9.3 Six-turn integration game
Run all scripted players through six turns.

**MVP-0 exit criterion:**

> Three scripted Actors and FakeUmpire complete a six-turn game deterministically with correct visibility, sequencing and Effects.

---

# 3. MVP-1 — Autonomous AI Game

**Objective:** Replace scripted Actors and FakeUmpire with autonomous LLM-backed agents.

---

## 3.1 FastAPI Interface

### 3.1.1 Game context endpoint
Implement:
```http
GET /games/{game_id}/context
```

Return:
- current turn;
- phase;
- active Actor;
- Actor-specific context;
- visible Effects;
- available actions.

### 3.1.2 Message endpoints
Implement:
```http
GET  /games/{game_id}/messages
POST /games/{game_id}/messages
```

### 3.1.3 Argument endpoint
Implement:
```http
POST /games/{game_id}/arguments
```

### 3.1.4 Con endpoints
Implement:
```http
POST /games/{game_id}/arguments/{argument_id}/cons
POST /games/{game_id}/arguments/{argument_id}/pass
```

### 3.1.5 Note endpoints
Implement:
```http
GET  /games/{game_id}/notes
POST /games/{game_id}/notes
```

### 3.1.6 Ready endpoint
Implement:
```http
POST /games/{game_id}/ready
```

### 3.1.7 Event polling
Implement:
```http
GET /games/{game_id}/events?after={event_id}
```

**Deliverable:** Full autonomous-player protocol over REST.

---

## 3.2 Seat Authentication

### 3.2.1 Seat token generation
Generate cryptographically random token for each Seat.

### 3.2.2 Token hashing/storage
Store hashes, not raw tokens.

### 3.2.3 Actor resolution
Resolve:
```text
bearer token → game → Actor
```

### 3.2.4 Endpoint enforcement
Actor identity must come from token, not request body.

**Deliverable:** Each agent can act only as its assigned Actor.

---

## 3.3 Agent Tool Layer

Implement thin HTTP tools:

### 3.3.1 `observe`
Fetch current context.

### 3.3.2 `read_messages`
Read visible Messages.

### 3.3.3 `send_message`
Send public/private Message.

### 3.3.4 `make_argument`
Submit primary Matrix Argument.

### 3.3.5 `make_con`
Challenge active Argument.

### 3.3.6 `pass_con`
Close the Actor's participation in the current Con phase without submitting another Con.

### 3.3.7 `get_notes`
Read private notebook.

### 3.3.8 `save_note`
Write private notebook entry.

### 3.3.9 `ready`
Signal diplomacy readiness or completion where readiness is explicitly legal.

**Deliverable:** Autonomous player can perform every required game action through tools.

---

## 3.4 Reference AI Player

### 3.4.1 PydanticAI integration
Create reference PydanticAI player.

### 3.4.2 OpenRouter model integration
Allow model ID to be configured per Actor.

### 3.4.3 Baseline player prompt
Create and version neutral player instructions.

### 3.4.4 Agent lifecycle
Implement:
```text
observe
→ determine whether input is required
→ invoke agent
→ allow tool use
→ wait/poll
→ repeat
```

### 3.4.5 Actor isolation
Each player receives only its own:
- brief;
- objectives;
- private information;
- Messages;
- Notes;
- visible Effects.

**Deliverable:** One autonomous AI can control an Actor.

---

## 3.5 Agent Guardrails

### 3.5.1 Limits
Implement:
- maximum model iterations;
- maximum tool calls;
- maximum Messages;
- request timeout;
- text-length limits.

### 3.5.2 Tool errors
Return structured errors to agent where recoverable.

### 3.5.3 Provider retry
Retry transient model failure once.

### 3.5.4 Stall recovery
If still stalled:
- log failure;
- auto-ready/pass where legal.

**Deliverable:** One failing agent cannot permanently hang a game.

---

## 3.6 AI Umpire

### 3.6.1 Umpire prompt
Create fixed, versioned adjudication prompt.

### 3.6.2 Adjudication context builder
Provide:
- scenario summary;
- turn/timescale;
- active Effects;
- Argument;
- Pros;
- Cons;
- authorised hidden information.

### 3.6.3 Structured response
Validate:
- mode;
- probability;
- rationale;
- success Effects;
- failure Effects;
- optional superseded Effect IDs.

### 3.6.4 Server resolution
Server:
- performs RNG;
- chooses success/failure branch;
- creates authoritative Effects.

### 3.6.5 Failure handling
Retry Umpire once.
If unavailable:
- pause/fail game;
- never fabricate result.

**Deliverable:** Real LLM-backed adjudication with server-owned outcomes.

---

## 3.7 JSON Game Export

### 3.7.1 Export command
Implement:
```bash
uv run amaw export GAME_ID
```

### 3.7.2 Export contents
Include:
- scenario/version;
- Actor/model assignment;
- seed;
- Messages;
- Arguments;
- Cons;
- adjudications;
- RNG rolls;
- Effects;
- Events;
- Actor objectives;
- private Notes with ownership/visibility metadata;
- scenario/resource hashes and run configuration where available;
- agent failures;
- timestamps.

**Deliverable:** Complete observable game can be inspected outside the application.

---

## 3.8 AI Smoke Test

Run:
```text
3 AI Actors
1 fixed AI Umpire
6 turns
0 human interventions
```

**MVP-1 exit criterion:**

> Three autonomous LLM agents complete a coherent six-turn game without information leakage or server-state corruption.

This is the first version worth demonstrating externally.

---

# 4. MVP-2 — Generic Scenario Knowledge

**Objective:** Allow agents to choose what additional scenario information to investigate.

---

## 4.1 Generic Scenario Resource Model

### 4.1.1 Resource metadata
Implement:
- ID;
- name;
- open `type`;
- inline content or file path;
- visibility;
- visible Actors;
- tags;
- free-form metadata.

### 4.1.2 Scenario loading
Load resource declarations from `scenario.yaml`.

At game creation, compute SHA-256 hashes for `scenario.yaml` and each referenced Resource file and persist a resource manifest for benchmark provenance.

### 4.1.3 Resource validation
Reject:
- duplicate IDs;
- missing files;
- invalid Actor visibility references.

**Deliverable:** Scenario designers can attach arbitrary resource types without core-code changes.

---

## 4.2 Initial Resource Formats

Required:
- Markdown/text;
- YAML;
- JSON;
- CSV.

Optional if trivial:
- PDF attachment;
- PNG/JPEG attachment;
- GeoJSON attachment.

No OCR, embeddings or semantic indexing.

---

## 4.3 Resource Search

### 4.3.1 Search operation
Implement simple search across:
- resource names;
- tags;
- text content where available.

### 4.3.2 Visibility-first filtering
Filter inaccessible resources before matching/returning results.

### 4.3.3 Search result
Return:
- ID;
- name;
- type;
- short safe snippet;
- tags.

**Deliverable:** Agent can discover relevant scenario information.

---

## 4.4 Resource Reading

### 4.4.1 Read endpoint
Implement:
```http
GET /games/{game_id}/resources/{resource_id}
```

### 4.4.2 `read_resource` tool
Expose to autonomous agent.

### 4.4.3 Access control
Reject inaccessible resources without leaking hidden metadata.

**Deliverable:** Agent can retrieve authorised information on demand.

---

## 4.5 Resource Search Tool

Implement:
```text
search_resources(query)
```

Integrate into player prompt/toolset.

**Deliverable:** Information seeking becomes an autonomous agent decision.

---

## 4.6 Resource Access Logging

Record:
- game;
- Actor;
- turn;
- operation;
- search query;
- resource read;
- timestamp.

**Deliverable:** Later analysis can compare agent information-seeking behaviour.

---

## 4.7 Scenario Knowledge Test

Create a scenario containing:
- public background;
- private intelligence;
- structured data;
- at least one map/image attachment;
- information not present in Actor briefs.

Verify different agents can independently retrieve it.

**MVP-2 exit criterion:**

> Agents can search and inspect additional scenario information during play without private-resource leakage.

---

# 5. MVP-3 — Agent Benchmark

**Objective:** Run the same strategic environment repeatedly and compare models/agent configurations.

---

## 5.1 Agent Run Metadata

Record:
- game ID;
- Actor ID;
- model requested;
- model returned where available;
- provider;
- prompt version;
- token usage;
- tool calls;
- invalid calls;
- stalls;
- cost where available;
- latency where available;
- completion status;
- model parameters;
- reasoning configuration where exposed;
- agent framework/version;
- tool schema version;
- player prompt hash/version.

Persist an immutable game-level run configuration manifest containing scenario/version/content hash, per-resource hashes, Umpire configuration and prompt hash/version, code version/commit or build identifier, benchmark mode, assignment ID and repetition index.

**Deliverable:** Each AI participant and game has comparable, auditable run metadata.

---

## 5.2 Benchmark CLI

Implement:
```bash
uv run amaw benchmark SCENARIO \
    --models ... \
    --rotate \
    --repetitions N
```

### 5.2.1 Assignment repetition
Interpret `--repetitions N` as N games per generated model/role assignment. Before execution, report assignment count, repetitions per assignment and resulting total games. Store `assignment_id` and `repetition_index` for each game.

### 5.2.2 Model assignment
Assign models to Actor Seats.

### 5.2.3 Seeds
Assign/store seed per game.

### 5.2.4 Failure continuation
One failed game must not abort the complete benchmark batch.

**Deliverable:** Batch execution without manual setup.

---

## 5.3 Role Rotation

For three models and three Actors, generate balanced assignments.

Example:
```text
A=X B=Y C=Z
A=Y B=Z C=X
A=Z B=X C=Y
```

Record role assignment explicitly.

**Deliverable:** Actor-position advantage can be separated from model differences.

---

## 5.4 Fixed Umpire Benchmark Control

Benchmark configuration must hold constant:
- Umpire model;
- Umpire prompt version;
- adjudication settings.

**Deliverable:** Player model remains the primary independent variable in model-benchmark mode.

---

## 5.5 Benchmark Modes

### 5.5.1 Model benchmark
Hold constant:
- harness;
- prompt;
- tools;
- memory design.

Vary:
- underlying LLM.

### 5.5.2 Agent benchmark
Allow different:
- models;
- prompts;
- frameworks;
- memory/planning designs.

Store benchmark mode in results.

**Deliverable:** Results have a clear experimental interpretation.

---

## 5.6 Raw Benchmark Outputs

Produce machine-readable outputs such as:

```text
games.csv
agents.csv
arguments.csv
messages.csv
resource_access.csv
```

or equivalent JSON.

Minimum metrics:
- completed games;
- Arguments;
- Argument success;
- Messages;
- Cons;
- resource searches;
- resource reads;
- tool calls;
- invalid tool calls;
- stalls;
- tokens;
- cost;
- latency.

**Deliverable:** Benchmark results can be analysed in external tools.

---

## 5.7 Basic Objective Scoring

Optional but recommended for MVP-3.

### 5.7.1 Fixed evaluator
Use one fixed evaluator model after game completion.

### 5.7.2 Score
Produce:
```text
0–4 objective score
rationale
```

### 5.7.3 Evaluator metadata
Record:
- evaluator model;
- evaluator prompt version.

**Deliverable:** Basic performance comparison in addition to behavioural logs.

---

## 5.8 After-Action Report

Optional but recommended for demonstration.

Generate:
- game summary;
- timeline;
- turning points;
- strategies;
- agreements;
- betrayals;
- successful/failed Arguments;
- notable outcomes.

**Deliverable:** Human-readable demonstration output.

---

## 5.9 Benchmark Acceptance Run

Run at least:
```text
3 models
3 roles
all 6 model-role permutations
5 repetitions per assignment
30 scheduled attempts, all retained
completion rate reported
```

Verify:
- model/role metadata;
- seed metadata;
- fixed Umpire configuration;
- raw outputs;
- failed-run reporting.
- deterministic interleaving / schedule metadata.

**MVP-3 exit criterion:**

> AMAW can repeatedly run strategic AI-agent games and produce comparable machine-readable behavioural and performance data across models and role assignments.

---

# 6. Quality Assurance and Release

## 6.1 Release-Blocking Unit Tests

Implement tests for:
- scenario validation;
- correct Actor action;
- wrong Actor rejection;
- Argument budget;
- Con limits;
- self-Con rejection;
- private Message visibility;
- private Resource visibility;
- private Note visibility;
- private Effect visibility;
- phase sequencing;
- Effect persistence;
- Effect expiry;
- seeded RNG;
- idempotent retries;
- double adjudication prevention;
- game termination.

---

## 6.2 Integration Tests

Maintain:
- scripted six-turn game;
- FakeUmpire deterministic game;
- API-level game test;
- AI smoke test;
- resource visibility test;
- benchmark batch smoke test.

---

## 6.3 Operational Documentation

Document:
- installation;
- environment variables;
- creating a scenario;
- starting a game;
- selecting models;
- running benchmarks;
- reading exports;
- known limitations.

---

## 6.4 Example Scenario

Provide one polished fictional scenario demonstrating:
- three Actors;
- conflicting objectives;
- private information;
- diplomacy;
- meaningful Arguments;
- generic scenario resources;
- six-turn structure.

**Deliverable:** New user can understand AMAW without writing a scenario first.

---

## 6.5 Release Package

Release should contain:
```text
source code
README
example environment file
example scenario
automated tests
CLI
JSON export
benchmark output example
```

---

# 7. Explicitly Outside MVP

The following are excluded from the **lean MVP delivery gates**. Some are intentionally decomposed in Section 11 as post-MVP work:

```text
human player UI
human facilitator UI
Organisation hierarchy/runtime
persistent Conversations/channels
communication topology and authority graphs
external Tool Gateway / MCP adapters
event-driven Actor activation
Actor tiers / mixed model allocation
large synthetic populations
enterprise authentication
SSO / RBAC
classified deployment
on-prem inference
distributed workers
message queues
PostgreSQL migration
Kubernetes
vector database
embedding-based RAG
OCR
semantic PDF indexing
advanced multimodal processing
map/geospatial game mechanics
military-specific domain classes
combat simulation
logistics simulation
Secret Arguments
trigger engine
formal epistemic/Claims graph
hybrid adjudication
multi-Umpire panels
external analytical models
full deterministic replay engine
full event sourcing
automated personality labels
advanced analytics dashboards
```

These belong to MVP+ and must not block Gate 0–4.

---

# 8. WBS Delivery Gates

## Gate 0 — Foundation Ready

Required:
```text
repository
configuration
SQLite
pytest
scenario schema
```

Decision:
> Can development proceed reproducibly from a fresh checkout?

---

## Gate 1 — Engine Proven

Required:
```text
MVP-0 complete
3 ScriptedPlayers
FakeUmpire
6 deterministic turns
visibility tests pass
```

Decision:
> Is the game engine correct without relying on AI?

---

## Gate 2 — Autonomous Game Proven

Required:
```text
MVP-1 complete
3 LLM agents
fixed LLM Umpire
6 turns
JSON export
no human intervention
```

Decision:
> Do autonomous games actually work and produce interesting behaviour?

---

## Gate 3 — Scenario Knowledge Proven

Required:
```text
MVP-2 complete
generic resources
search/read tools
private resource filtering
resource access logs
```

Decision:
> Can agents independently seek relevant information without the engine becoming domain-specific?

---

## Gate 4 — Benchmark Proven

Required:
```text
MVP-3 complete
role rotations
multiple models
30+ games
machine-readable outputs
```

Decision:
> Can AMAW support meaningful comparative analysis of autonomous AI agents?

---

# 9. Critical Path

The development critical path is:

```text
Repository / tests
        ↓
Domain model
        ↓
Persistence
        ↓
Scenario loader
        ↓
State machine
        ↓
Visibility
        ↓
FakeUmpire
        ↓
Scripted six-turn game
        ↓
FastAPI / auth
        ↓
Agent tools
        ↓
LLM player
        ↓
LLM Umpire
        ↓
Autonomous six-turn game
        ↓
Scenario resources
        ↓
Benchmark runner
        ↓
Role rotation / outputs
```

Do not begin sophisticated benchmark analytics until the autonomous six-turn game is reliable.

Do not add advanced scenario-resource infrastructure until simple `search_resources` and `read_resource` prove insufficient.

---

# 10. Final MVP Deliverable

The completed MVP is:

> A small Python application that loads a generic strategic scenario, runs three or more autonomous AI Actors through a Matrix Game using private/public diplomacy, Arguments, Cons, AI adjudication and persistent Effects, gives agents on-demand access to authorised scenario resources, records their observable behaviour, and repeatedly runs the same scenario across different models and role assignments to produce machine-readable comparative results.

The software should remain intentionally small.

The benchmark value should come from the behaviour of the agents, not from complexity in the engine.
---

# 11. Phase 2 / MVP+ — Large Synthetic Actor Ecosystems

**Status:** Deferred until MVP-3 demonstrates useful autonomous games and benchmark outputs.

**Purpose:** Define the next work packages without adding them to the lean MVP critical path.

## 11.1 Generic Organisation Model

Implement only when scenarios require multiple role Actors inside shared organisations.

Work packages:
- generic `Organisation` object;
- optional parent/child organisation relationships;
- Actor `organisation_id` and scenario-defined `role`;
- organisation-level visibility/policy where justified;
- scenario validation.

**Deliverable:** A scenario can represent multiple independent Actors within one organisation without hard-coded government/military classes.

---

## 11.2 Conversations and Communication Topology

Work packages:
- persistent Conversation/channel object;
- membership and visibility enforcement;
- group/internal communications;
- scenario-defined channel types;
- optional information-routing rules.

**Deliverable:** Large scenarios can model structured communication networks rather than relying only on arbitrary recipient lists.

---

## 11.3 External Tool Gateway

Work packages:
- generic `ToolProvider` interface;
- per-Actor/scenario tool authorization;
- MCP adapter;
- REST adapter where useful;
- local Python adapter where useful;
- credential isolation;
- tool-call logging/provenance;
- tool-result handling.

**Hard rule:** External tools provide information/evidence and never directly mutate authoritative AMAW state.

**Deliverable:** Scenario designers can attach simulations, GIS, databases and analytical tools without changing the core game engine.

---

## 11.4 Tool Results as Scenario/Game Resources

Work packages:
- optional persisted tool-result resource;
- visibility scope;
- calling Actor;
- tool/version/run metadata;
- input/output provenance;
- ability for agent/Umpire to reference result.

**Deliverable:** External analysis can be inspected and cited later in the game.

---

## 11.5 Event-Driven Actor Activation

Work packages:
- generic activation-policy model;
- Event-to-Actor routing;
- contacted-Actor activation;
- event/phase subscriptions;
- dormant Actor handling;
- activation logging.

**Deliverable:** Large scenarios do not invoke every model after every event.

---

## 11.6 Actor Tiers and Model Allocation

Work packages:
- per-Actor model configuration;
- inference budget/priority;
- principal/secondary/reactive operating policies;
- cheap-model allocation for lower-priority roles;
- benchmark metadata for mixed model tiers.

**Deliverable:** Large synthetic worlds can remain economically and computationally practical.

---

## 11.7 Generic Authority / Approval Policies

Work packages:
- scenario-defined permission categories;
- optional approval requirement;
- authorised approver Actors;
- approval event/state tracking;
- no domain-specific command semantics in core.

**Deliverable:** Organisational decision processes can be represented when a scenario needs them.

---

## 11.8 Hybrid Human + Synthetic Mode

Only after autonomous mode is mature.

Work packages:
- human Seat client/API workflow;
- handoff between AI/human Seat implementations;
- facilitator visibility/control;
- principal-human + synthetic-secondary configuration.

**Deliverable:** Consultancies or defence teams can use human principal roles alongside synthetic control-cell Actors.

---

## 11.9 Large-Scenario Runtime

Trigger this work only from measured need.

Possible work packages:
- concurrency/rate-limit management;
- persistent workers;
- job/task queue;
- asynchronous external-tool jobs;
- database migration from SQLite if required;
- recovery/restart semantics;
- cost/latency monitoring.

**Deliverable:** AMAW can run larger synthetic populations reliably without turning the lean core into distributed infrastructure prematurely.

---

## 11.10 Phase-2 Entry Gate

Begin this phase only when all of the following are true:

```text
MVP-3 benchmark works reliably
repeated games produce interesting/usable behaviour
at least one target scenario genuinely needs > small Actor count
at least one external tool/integration has a concrete user case
```

Decision:

> Which scaling feature solves a demonstrated scenario problem first?

Do not implement the entire Phase-2 list as one programme.
