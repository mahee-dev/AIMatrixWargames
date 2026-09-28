# AMAW — Autonomous Matrix Wargame Platform

## Technical Requirements Document

**Version:** 0.5.1 — Lean MVP + Scale Extension Contracts  
**Status:** Proposed implementation baseline  
**Primary goal:** Build the smallest reliable all-AI Matrix Game benchmark while preserving clean extension contracts for larger synthetic-actor ecosystems, professional hybrid games and external tools.

---

# 1. What this MVP is

AMAW MVP is a small Python application that:

1. loads a scenario;
2. creates three or more Actor seats;
3. connects one autonomous AI agent to each Actor;
4. runs a multi-turn Matrix Game;
5. lets agents communicate privately/publicly;
6. lets each Actor make Matrix Arguments;
7. lets opposing Actors submit Cons;
8. adjudicates Arguments through one fixed AI Umpire;
9. applies persistent Effects;
10. logs what happened;
11. exports the game;
12. repeats games across model/role assignments for comparison.

The implementation target is deliberately small.

The MVP is **not** a general simulation platform, general agent framework, enterprise defence system, full RAG platform or sophisticated wargaming suite.

A reasonable implementation should remain on the order of a few thousand lines of application code plus tests.

---

# 2. Full Lean MVP / MVP-3 acceptance test

The first publicly interesting version is complete when the following works:

```bash
uv run amaw play scenarios/northern-strait \
    --actor arkania=provider/model-a \
    --actor belvar=provider/model-b \
    --actor cestia=provider/model-c
```

and:

```bash
uv run amaw benchmark scenarios/northern-strait \
    --models provider/model-a,provider/model-b,provider/model-c \
    --rotate \
    --repetitions 10
```

The system must be able to:

- complete a six-turn game without human intervention;
- keep Actor-private information private;
- support private and public diplomacy;
- enforce the primary Argument limit;
- collect Pros and Cons;
- use a fixed AI Umpire for adjudication;
- use seeded server-side randomness for probabilistic outcomes;
- maintain persistent Effects;
- allow agents to search and read generic scenario resources;
- maintain a private notebook per Actor;
- avoid hanging indefinitely when an agent misbehaves;
- export complete game history as JSON;
- record enough model/run metadata to compare games;
- rotate models across Actor roles.

Anything not required by this acceptance test is presumptively **MVP+**.

---

# 3. Design principles

## 3.1 Keep the engine small

The engine understands:

```text
Game
Actor
Seat
Message
Argument
Con
Effect
Event
Resource
Note
Adjudication
```

It does not understand:

```text
air force
tank
ship
company
port
treaty
political faction
cyber operation
market
pipeline
brigade
```

Those concepts belong to scenario content.

---

## 3.2 Scenario semantics are generic

AMAW provides:

```text
generic content
generic metadata
generic visibility
generic search
generic retrieval
```

The scenario decides what a resource means.

A defence scenario may use resource types such as:

```text
map
intelligence
capability
history
treaty
```

A corporate scenario may instead use:

```text
competitor
market_data
regulation
product
```

No Python code changes should be required merely to introduce a new scenario-specific resource type.

---

## 3.3 AI supplies strategy; software supplies procedure

Player agents decide:

- what to research;
- whom to contact;
- what to say;
- what to argue;
- how to react;
- whether to cooperate, threaten, bluff or deceive.

The server decides:

- whose turn it is;
- what operations are legal;
- who may see information;
- whether the Argument budget is exhausted;
- when a phase advances;
- the random draw;
- which Effects become authoritative.

---

## 3.4 Do not overbuild before games are interesting

Do not add infrastructure simply because it might be useful later.

The MVP deliberately avoids:

```text
microservices
LangGraph
Kubernetes
vector databases
WebSockets
complex workflow engines
full event sourcing
generic ontology engines
human-facing UI
enterprise authentication
classification systems
external combat simulation integrations
```

---

# 4. MVP architecture

```text
                  ┌─────────────────────┐
                  │      FastAPI        │
                  │     AMAW Core       │
                  └─────────┬───────────┘
                            │
                   ┌────────▼─────────┐
                   │   GameService    │
                   │                  │
                   │ state machine    │
                   │ visibility       │
                   │ validation       │
                   │ effects          │
                   │ event logging    │
                   │ RNG              │
                   └────────┬─────────┘
                            │
                        SQLModel
                            │
                       ┌────▼────┐
                       │ SQLite  │
                       └─────────┘

           REST
    ───────────────┼────────────────
                   │
        ┌──────────┴──────────┐
        │                     │
   Player processes       Umpire adapter
        │                     │
       Agno                OpenRouter
        │
    OpenRouter
```

The FastAPI application must not import Agno.

OpenRouter integration belongs outside the core game domain.

For the MVP, the Umpire may still live in the same repository/process tree; architectural separation is more important than deployment separation.

---

# 5. Repository structure

Keep the repository simple:

```text
amaw/
│
├── app/
│   ├── main.py
│   ├── api.py
│   ├── models.py
│   ├── game_service.py
│   ├── visibility.py
│   ├── scenarios.py
│   ├── resources.py
│   └── database.py
│
├── agents/
│   ├── player.py
│   ├── tools.py
│   └── umpire.py
│
├── benchmark/
│   └── runner.py
│
├── scenarios/
│   └── northern-strait/
│       ├── scenario.yaml
│       └── resources/
│
├── tests/
│
├── pyproject.toml
└── README.md
```

Do not split modules prematurely.

Refactor when files become unwieldy, not before.

---

# 6. Technology choices

| Component | MVP choice |
|---|---|
| Language | Python 3.12+ |
| API | FastAPI |
| Validation | Pydantic v2 |
| Persistence | SQLModel + SQLite |
| Scenario manifest | YAML |
| Reference agent framework | Agno |
| Model gateway | OpenRouter |
| HTTP | httpx |
| Async | asyncio |
| CLI | Typer |
| Testing | pytest |
| Packaging | uv |
| Deployment | local process / Docker optional |

Alembic is optional until schema changes become painful.

SSE is optional and not required for the first release.

---

# 7. Core domain objects

## 7.1 Game

```python
class GameStatus(str, Enum):
    lobby = "lobby"
    running = "running"
    paused = "paused"
    complete = "complete"


class GamePhase(str, Enum):
    diplomacy = "diplomacy"
    argument = "argument"
    cons = "cons"
    adjudication = "adjudication"
    consequences = "consequences"
    complete = "complete"
```

```python
class Game:
    id: UUID
    scenario_id: str

    turn: int
    max_turns: int

    phase: GamePhase
    status: GameStatus

    turn_order: list[str]
    active_actor_id: str | None

    seed: int
```

The database representation may differ from the Pydantic representation.

---

## 7.2 Actor

```python
class Actor:
    id: str
    name: str

    public_description: str
    private_brief: str

    objectives: list[str]
    private_information: list[str]

    # Optional scenario metadata; no core semantics in MVP.
    organisation_id: str | None = None
    role: str | None = None
    metadata: dict = {}
```

The Actor represents a role in the scenario.

AMAW does not infer real-world behaviour from that role.

---

## 7.3 Seat

```python
class Seat:
    id: UUID
    game_id: UUID
    actor_id: str

    token_hash: str
```

For the MVP, every Seat is assumed to be autonomous.

Keep the abstraction so human/remote Seats can be added later.

---

## 7.4 Message

```python
class Message:
    id: UUID
    game_id: UUID
    turn: int

    sender_actor_id: str
    recipients: list[str]

    text: str
    public: bool

    created_at: datetime
```

Rules:

- `public=True` means visible to all Actors;
- otherwise only sender + listed recipients may read it;
- messages are immutable after sending.

No formal message-intent ontology is required in the MVP.

---

## 7.5 Argument

```python
class Argument:
    id: UUID
    game_id: UUID
    turn: int
    actor_id: str

    action: str
    desired_effect: str
    pros: list[str]

    status: str
```

Validation:

```text
action required
desired_effect required
1–5 Pros
correct Actor
correct phase
one primary Argument per Actor per turn by default
```

The primary Argument budget should be scenario-configurable, but supporting values greater than one does not need to be heavily optimised in the first release.

---

## 7.6 Con

```python
class Con:
    id: UUID
    argument_id: UUID
    actor_id: str
    reason: str
```

Default:

```text
0–3 Cons per eligible Actor per Argument
```

The Actor that created the Argument cannot submit a Con against itself.

---

## 7.7 Effect

Keep Effects intentionally simple.

```python
class Effect:
    id: UUID
    game_id: UUID

    description: str

    visibility: str
    visible_to: list[str]

    source_argument_id: UUID | None

    created_turn: int
    expires_turn: int | None

    active: bool
```

For the first MVP:

- Effects persist while `active=True`;
- expiry is supported;
- facilitator-created Effects are not required;
- sophisticated supersession graphs are not required.

If contradictory Effects occur, the Umpire may propose a new Effect and the server may deactivate explicitly referenced old Effects.

A minimal optional field is enough:

```python
supersedes: list[UUID] = []
```

Do not build a general truth-maintenance system.

---

## 7.8 Event

```python
class GameEvent:
    id: int
    game_id: UUID
    turn: int

    type: str
    actor_id: str | None

    payload: dict
    created_at: datetime
```

Important event types:

```text
game_started
phase_started
message_sent
argument_submitted
con_submitted
argument_adjudicated
effect_created
effect_deactivated
actor_ready
turn_completed
game_completed
agent_error
```

The event log is an audit/history record.

It is **not** full event sourcing.

---

## 7.9 Note

```python
class Note:
    id: UUID
    game_id: UUID
    actor_id: str

    text: str
    created_at: datetime
```

Notes are private to one Actor in one game.

AMAW does not require model chain-of-thought.

---

# 8. Generic scenario resources

Scenario resources provide optional background information beyond the initial Actor brief.

Examples could include:

```text
history
maps
capability descriptions
intelligence
statistics
treaties
economic data
images
documents
```

But AMAW does not hard-code those concepts.

Use one generic object:

```python
class ScenarioResource:
    id: str
    scenario_id: str

    name: str
    type: str

    content: str | None
    path: str | None

    visibility: str
    visible_to: list[str]

    tags: list[str]
    metadata: dict
```

The meaning of `type` and `metadata` belongs to the scenario.

Example:

```yaml
resources:
  - id: regional-history
    name: Regional history
    type: background
    path: resources/history.md
    visibility: public
    tags: [history, politics]

  - id: arkania-readiness
    name: Arkanian readiness assessment
    type: intelligence
    path: resources/arkania-readiness.md
    visibility: private
    visible_to: [belvar]
    tags: [arkania, military, readiness]
```

---

# 9. Resource formats

First release:

```text
Markdown / text
YAML
JSON
CSV
```

Optional if easy:

```text
PDF
PNG/JPEG
GeoJSON
```

A file may simply be exposed as an attachment/resource rather than deeply parsed.

Do not build:

- OCR pipelines;
- geospatial analysis;
- PDF semantic indexing;
- vector embeddings;
- multimodal preprocessing

until scenarios demonstrate a real need.

---

# 10. Resource search and retrieval

Agents should not receive every scenario document in their prompt.

Instead expose:

```text
search_resources(query)
read_resource(resource_id)
```

The first implementation may use:

- title matching;
- tag matching;
- simple case-insensitive substring/full-text search.

That is enough for small scenarios.

Example:

```text
search_resources("Belvar fuel logistics")
```

may return:

```text
1. Belvar Logistics Overview
2. Northern Ports
3. Fuel Import Statistics
```

The agent then chooses what to read.

This makes information-seeking behaviour observable without requiring a full RAG stack.

---

# 11. Resource visibility

Visibility filtering is mandatory.

Public resource:

```yaml
visibility: public
```

Private resource:

```yaml
visibility: private
visible_to:
  - arkania
```

The server must filter resources **before** returning search results.

A hidden resource must not leak through:

- title;
- search snippet;
- tags;
- metadata;
- file name.

This is one of the most important security tests in the MVP.

---

# 12. Scenario package

A scenario is a directory.

Example:

```text
northern-strait/
│
├── scenario.yaml
│
└── resources/
    ├── background.md
    ├── arkania.md
    ├── belvar.md
    ├── trade.csv
    └── map.png
```

The directory structure is convention, not domain logic.

`scenario.yaml` is authoritative.

At game creation, compute and persist SHA-256 hashes for `scenario.yaml` and every referenced Resource file. Persist the resulting resource manifest with the game/run configuration. Hashing is provenance, not a content-addressed storage subsystem.

Example:

```yaml
scenario:
  id: northern-strait
  name: Northern Strait Crisis
  version: 1
  description: >
    A fictional regional crisis.

game:
  max_turns: 6
  in_world_turn_length: "2 weeks"

  turn_order:
    - arkania
    - belvar
    - cestia

  primary_arguments_per_actor: 1
  max_cons_per_actor: 3

actors:
  arkania:
    name: Republic of Arkania
    public_description: >
      A large regional power.
    private_brief: >
      You are the political leadership of Arkania.
    objectives:
      - "Increase influence over the Northern Strait."
      - "Prevent direct Belvar intervention."
    private_information:
      - "Reserve mobilisation requires two turns."

  belvar:
    name: Belvar
    public_description: >
      ...
    private_brief: >
      ...
    objectives:
      - "..."

  cestia:
    name: Cestia
    public_description: >
      ...
    private_brief: >
      ...
    objectives:
      - "..."

starting_effects:
  - description: "Commercial shipping continues normally."
    visibility: public

resources:
  - id: background
    name: Regional Background
    type: background
    path: resources/background.md
    visibility: public

  - id: red-intel
    name: Arkania Intelligence Estimate
    type: intelligence
    path: resources/arkania.md
    visibility: private
    visible_to: [arkania]
```

---

# 13. Game lifecycle

For each turn:

```text
DIPLOMACY
    ↓
ARGUMENT — Actor A
    ↓
CONS
    ↓
ADJUDICATION
    ↓
APPLY EFFECTS
    ↓
ARGUMENT — Actor B
    ↓
CONS
    ↓
ADJUDICATION
    ↓
APPLY EFFECTS
    ↓
...
    ↓
CONSEQUENCES
    ↓
NEXT TURN
```

After the final turn:

```text
COMPLETE
↓
EXPORT
```

Objective scoring and AAR generation may exist, but neither should block the first autonomous game.

---

# 14. State machine rules

Keep the state machine explicit.

## Lobby

Allowed:

```text
start game
```

## Diplomacy

Allowed:

```text
read context
search/read resources
read messages
send messages
save/read notes
ready
```

Close when:

```text
all Actors ready
OR timeout
```

If a ready Actor receives a new private Message before diplomacy closes, the server clears that recipient's ready state before evaluating the close condition. The normal polling loop can then activate the Actor again.

This behaviour must be deterministic and covered by engine tests. It does not require event-driven orchestration.

Do not let diplomacy scheduling become a major subsystem before basic games work.

## Argument

Only the active Actor may:

```text
make_argument
```

It may also:

```text
read context/resources/messages/notes
send_message
```

## Cons

All other Actors may:

```text
make_con
pass
```

Close when:

```text
all eligible Actors have acted/passed
OR timeout
```

## Adjudication

No player mutation is permitted.

The server obtains one structured Umpire decision.

## Consequences

Optional in MVP-1.

If implemented, one AI consequence call may return 0–3 Effects.

If this causes too much complexity, ship the first benchmark without it.

---

# 15. API surface

Keep the API small.

## Context

```http
GET /games/{game_id}/context
```

Returns everything the current Actor is currently authorised to know and needs for play.

Example:

```json
{
  "turn": 2,
  "phase": "argument",
  "active_actor_id": "arkania",

  "you": {
    "actor_id": "arkania",
    "objectives": []
  },

  "effects": [],
  "current_argument": null,

  "available_actions": [
    "send_message",
    "search_resources",
    "read_resource",
    "make_argument",
    "save_note"
  ]
}
```

`available_actions` is authoritative.

---

## Messages

```http
GET /games/{game_id}/messages
POST /games/{game_id}/messages
```

---

## Arguments

```http
POST /games/{game_id}/arguments
```

---

## Cons

```http
POST /games/{game_id}/arguments/{argument_id}/cons
POST /games/{game_id}/arguments/{argument_id}/pass
```

---

## Resources

```http
GET /games/{game_id}/resources/search?q=...
GET /games/{game_id}/resources/{resource_id}
```

---

## Notes

```http
GET /games/{game_id}/notes
POST /games/{game_id}/notes
```

Deletion is optional for MVP.

---

## Ready

```http
POST /games/{game_id}/ready
```

---

## Events

```http
GET /games/{game_id}/events?after=...
```

Polling is enough.

SSE is not required.

---

# 16. Authentication

For the first release:

```text
one random Bearer token per Seat
```

The server maps the token to:

```text
game_id
actor_id
```

The CLI/game runner may retain an internal admin token for setup/start/end operations.

No:

```text
accounts
OAuth
SSO
RBAC platform
user database
```

is required.

---

# 17. Idempotency and races

This is worth implementing even in the lean MVP because autonomous agents and HTTP retries will produce duplicates.

For mutating Actor operations, accept:

```http
Idempotency-Key: <uuid>
```

At minimum protect:

```text
send_message
make_argument
make_con
pass
ready
save_note
```

If the same key is submitted twice with the same payload, return the original result.

Also enforce key game invariants at the database/service level so that:

```text
two simultaneous primary Arguments cannot both succeed
an Argument cannot be adjudicated twice
a turn cannot advance twice
```

Do not build distributed locking.

SQLite transactions and careful state validation are sufficient for the MVP.

---

# 18. Player agent

The reference player should be simple.

Conceptually:

```python
while True:
    context = await api.get_context()

    if context["game_complete"]:
        break

    if not context["requires_input"]:
        await asyncio.sleep(POLL_INTERVAL)
        continue

    await agent.arun(
        build_prompt(context),
    )
```

The agent receives tools.

Do not implement a hard-coded strategic workflow.

---

# 19. Player tools

Required:

```text
observe
read_messages
send_message
search_resources
read_resource
make_argument
make_con
get_notes
save_note
ready
```

Most tools are thin HTTP wrappers.

Example:

```python
async def make_argument(action, desired_effect, pros):
    return await api.post(
        f"/games/{game_id}/arguments",
        json={
            "action": action,
            "desired_effect": desired_effect,
            "pros": pros,
        },
    )
```

No complex local game state should live inside the agent process.

---

# 20. Agent instructions

A baseline player system instruction may be:

```text
You control {actor_name}.

Pursue your Actor's objectives throughout the game.

You may communicate with other Actors, inspect available scenario
information, maintain private notes, make Matrix Arguments and respond
to the Arguments of other Actors.

You are autonomous. Decide your own strategy.

You may cooperate, negotiate, conceal intentions, bluff, threaten,
make commitments or change strategy.

Respect the current game phase and available tools.

Do not assume you know information that has not been provided to you
or retrieved through your tools.

When you have completed everything useful in the current opportunity,
call ready().
```

Keep the baseline prompt versioned.

Do not excessively coach the model strategically.

---

# 21. Agent guardrails

Required:

```text
max LLM iterations per activation
max tool calls per activation
request timeout
one retry for transient model/API failure
message length limit
Argument length limit
```

Example:

```python
MAX_LLM_ITERATIONS = 12
MAX_TOOL_CALLS = 20
MAX_MESSAGES_PER_PHASE = 10
```

If an agent repeatedly fails:

```text
record error
→ retry once
→ automatically pass/ready where legal
```

The game must continue.

---

# 22. Umpire

Use exactly one primary Umpire implementation for the first MVP.

Input:

```text
scenario summary
turn number and timescale
Argument
Pros
Cons
relevant active Effects
relevant facilitator/private scenario information if configured
```

Output:

```python
class Adjudication:
    mode: Literal[
        "automatic_success",
        "automatic_failure",
        "probabilistic",
    ]

    probability: float | None
    rationale: str

    success_effects: list[str]
    failure_effects: list[str]

    supersedes_effect_ids: list[UUID] = []
```

The Umpire never performs the random draw.

The server does.

---

# 23. Fixed Umpire benchmark policy

When comparing player models:

```text
keep the Umpire model fixed
keep the Umpire prompt fixed
keep adjudication settings fixed
```

Otherwise differences in outcomes become difficult to interpret.

Example:

```yaml
benchmark:
  umpire_model: provider/fixed-model
  umpire_prompt_version: umpire-v1
```

Experiments comparing Umpires can be added separately.

---

# 24. RNG

Every game gets a seed.

```python
rng = random.Random(seed)
```

Probabilistic resolution:

```python
roll = rng.random()
success = roll <= probability
```

Log:

```json
{
  "probability": 0.70,
  "roll": 0.483293,
  "success": true
}
```

This reproduces server-owned random outcomes.

It does **not** make LLM behaviour deterministic.

---

# 25. Consequence agent

A Consequence Agent is useful but not essential to the first AI-vs-AI proof.

If implemented:

```text
run once at end of turn
input: turn events + active Effects
output: 0–3 Effect proposals
```

The server stores accepted Effects.

For MVP, automatic acceptance is acceptable.

Do not build:

```text
human approval workflows
multi-agent consequence panels
complex causal graphs
```

yet.

If necessary, move the whole feature to MVP-2.

---

# 26. Persistence

SQLite stores:

```text
games
actors
seats
messages
arguments
cons
effects
events
notes
resource metadata
agent run metadata
```

Scenario source files may remain on disk.

Do not duplicate every scenario attachment into the database unless needed.

---

# 27. Game export

Required first format:

```text
JSON
```

Example:

```bash
uv run amaw export GAME_ID > game.json
```

The export should include:

```text
scenario id/version
game configuration
Actor/model assignments
seed
messages
Arguments
Cons
adjudications
RNG rolls
Effects
events
resource-access log if available
Actor objectives
private Notes
visibility metadata
scenario/resource content hashes
run configuration manifest
agent errors
timestamps
```

CSV summaries belong to benchmark output.

A full replay engine is **MVP+**.

The administrative export is an analysis artifact and intentionally includes Actor-private game data such as objectives, private Messages and explicit Notes, each retaining its visibility/ownership metadata. It is never an Actor-facing endpoint and does not require or contain hidden model chain-of-thought.

The MVP requirement is that the exported record clearly shows what happened and under what immutable configuration it ran.

---

# 28. Model and run metadata

Do not build an enormous provenance subsystem initially.

For every Actor run, record:

```python
class AgentRun:
    game_id: UUID
    actor_id: str

    model_requested: str
    model_returned: str | None
    provider: str | None

    prompt_version: str
    prompt_hash: str

    model_parameters: dict
    reasoning_configuration: dict | None
    agent_framework: str
    agent_framework_version: str | None
    tool_schema_version: str

    input_tokens: int
    output_tokens: int

    tool_calls: int
    invalid_tool_calls: int
    stalls: int

    cost_usd: float | None
    latency_ms: int | None

    completed: bool
```

Each game should also persist one immutable run-configuration manifest containing at least:

```text
scenario_id + scenario_version
scenario_content_hash
resource manifest with per-resource hashes
player prompt version/hash
Umpire model/provider/prompt version/hash/settings
agent framework/version
tool schema version
code version / git commit or build identifier
benchmark mode, assignment_id and repetition_index where applicable
```

If the provider exposes useful additional metadata cheaply, store it.

Do not block the MVP on perfect cross-provider metadata normalisation.

---

# 29. Resource access logging

Because information-seeking is part of the benchmark, record:

```text
resource searches
resource reads
Actor
turn
timestamp
```

Example:

```python
class ResourceAccess:
    game_id: UUID
    actor_id: str
    turn: int

    operation: Literal["search", "read"]
    query: str | None
    resource_id: str | None

    created_at: datetime
```

This permits later questions such as:

```text
Which agents researched before acting?
Which resources were repeatedly ignored?
How much information did each model consume?
```

No elaborate behavioural classification is required in the engine.

---

# 30. Benchmark runner

The benchmark runner is a core feature, not an afterthought.

Example:

```bash
uv run amaw benchmark scenarios/northern-strait \
    --models provider/a,provider/b,provider/c \
    --rotate \
    --repetitions 10
```

For three models and three Actors, role rotation should produce assignments such as:

```text
Run set 1
A=a B=b C=c

Run set 2
A=b B=c C=a

Run set 3
A=c B=a C=b
```

`--repetitions N` means **N repetitions per generated model/role assignment**. Role assignments are not themselves repetitions.

For example, three cyclic assignments with `--repetitions 10` launch 30 games. The runner should report `assignment_count`, `repetitions_per_assignment` and `total_games` before execution and store `assignment_id` plus `repetition_index` for every game.

Each game gets its own seed.

---

# 31. Benchmark modes

Support two conceptual modes from the beginning, even if the CLI implementation is initially minimal.

## Model benchmark

Same:

```text
agent harness
prompt
tools
memory mechanism
```

Different:

```text
underlying model
```

Question:

> How does behaviour differ between models under the same agent architecture?

## Agent benchmark

Different combinations of:

```text
model
prompt
agent framework
memory
planning strategy
```

Question:

> How do complete agent configurations differ?

Store a label identifying which interpretation applies.

---

# 32. Benchmark outputs

At minimum produce:

```text
games.csv
agents.csv
arguments.csv
messages.csv
resource_access.csv
```

and/or equivalent JSON.

Useful metrics:

```text
games completed
objective score if available
Arguments submitted
Argument success rate
messages sent
Cons submitted
resources searched
resources read
tool calls
invalid calls
stalls
tokens
cost
latency
```

Do not prematurely implement psychological/behavioural scores such as:

```text
aggression = 0.81
trustworthiness = 0.63
```

Those require a separate analysis methodology.

Raw behavioural data first.

---

# 33. Objective scoring

Objective scoring is useful but may be simple.

For MVP-3, use one fixed evaluator model after game completion:

```python
class ObjectiveScore:
    actor_id: str
    objective: str
    score: int  # 0–4
    rationale: str
```

Scale:

```text
0 failed badly
1 mostly failed
2 partly achieved
3 largely achieved
4 fully achieved
```

Record evaluator model and prompt version.

Objective scores are benchmark aids, not objective truth.

If this feature slows the first autonomous game, defer it until the benchmark milestone.

---

# 34. AAR

A fun after-action report is valuable for demonstrating the project.

It may contain:

```text
summary
timeline
turning points
strategies
agreements
betrayals
successful Arguments
failed Arguments
unexpected outcomes
winner/objective results
```

Generate it after the game.

It cannot affect gameplay.

This is useful but not release-blocking for MVP-1.

---

# 35. Error handling

## Player provider timeout

```text
retry once
→ auto-ready/pass if still unavailable
→ log failure
```

## Malformed tool call

```text
return tool error
→ allow model to retry
```

## Umpire timeout

```text
retry once
→ pause/fail game
```

Do not invent an adjudication.

## Player process crash

For lean MVP:

```text
mark run error
→ auto-pass/ready
```

More sophisticated takeover is later.

## Invalid scenario

Fail before game creation with a clear validation error.

---

# 36. Information security

MVP security objective:

> One Actor must never receive another Actor's private scenario information, resources, notes or messages through normal APIs.

Required:

```text
random Seat tokens
server-side visibility checks
no API keys sent to agents
environment variables for provider secrets
request-size limits
safe logging
```

Not required:

```text
OAuth
SSO
enterprise audit platform
classification-aware storage
air-gapped model serving
```

Those belong to professional deployment work.

---

# 37. Tests

Tests matter more than architectural sophistication.

## Release-blocking deterministic tests

Without LLMs:

```text
scenario loads
game starts
correct Actor can make Argument
wrong Actor rejected
second Argument rejected
Cons work
self-Con rejected
private Message hidden
private Resource hidden
private Note hidden
private Effect hidden
turn advances
Effect persists
Effect expires
seeded RNG repeats
duplicate Argument request does not duplicate
double adjudication prevented
game terminates
```

---

# 38. Scripted integration test

Create:

```text
ScriptedPlayer A
ScriptedPlayer B
ScriptedPlayer C
FakeUmpire
```

They must complete a six-turn game deterministically.

This is the first milestone.

Do not connect real LLMs until this passes.

---

# 39. AI smoke test

Then replace scripted players with real AI agents.

Success criterion:

```text
3 autonomous AI players
6 turns
no human intervention
game completes
```

This is the project's first major proof point.

The game does not need to be analytically sophisticated yet.

It needs to be coherent and interesting enough to inspect.

---

# 40. Development milestones

## MVP-0 — Engine proof

Build:

```text
scenario YAML
Game / Actor / Seat
Message
Argument / Con
Effect
phase machine
visibility
SQLite
FakeUmpire
ScriptedPlayers
```

Acceptance:

> Three scripted players complete six deterministic turns.

---

## MVP-1 — Autonomous game

Add:

```text
Agno player
OpenRouter
player tools
fixed AI Umpire
RNG
notes
agent guardrails
JSON export
```

Acceptance:

> Three autonomous LLM agents complete six turns without human intervention.

This is the first version worth showing people.

---

## MVP-2 — Scenario knowledge

Add:

```text
generic ScenarioResource
resource files
search_resources
read_resource
private resource visibility
resource access logging
optional Consequence Agent
```

Acceptance:

> Agents can independently investigate background information before making decisions, without information leakage.

---

## MVP-3 — Benchmark

Add:

```text
benchmark CLI
model rotation
multiple runs
AgentRun metadata
CSV/JSON results
basic objective scoring
optional AAR
```

Acceptance:

> Run 30+ games across three models/roles and compare raw behavioural and performance data.

This is the first real AMAW agent-benchmark release.

---

# 41. Explicitly MVP+

The following are **not required** for MVP-0 through MVP-3:

```text
Human player UI
Human facilitator UI
Human adjudication workflow
Organisation hierarchy/runtime
persistent Conversation/channel objects
communication topology / authority graphs
external Tool Gateway / MCP integration
event-driven Actor activation
Actor tiers / mixed model allocation
large synthetic populations
Secret Arguments
natural-language trigger engine
Claims / epistemic-status graph
resource ontology system
scenario-defined schemas
embeddings/vector search
large-scale RAG infrastructure
PDF semantic indexing
OCR
advanced multimodal map reasoning
GeoJSON operations
external combat models
external logistics models
hybrid adjudicators
assessment panels
multi-umpire voting
full deterministic replay engine
full event sourcing
WebSockets
SSE
enterprise authentication
RBAC
SSO
classified deployment
on-prem model serving
distributed workers
message queues
PostgreSQL
Kubernetes
advanced dashboards
automated behavioural personality scoring
```

These may be added only after the benchmark demonstrates that the underlying games produce useful or interesting behaviour.

---

# 42. Extension points to preserve

Even though MVP+ features are not implemented, avoid choices that make them unnecessarily difficult later.

Preserve:

### Seat abstraction

So humans may eventually replace AI Actors.

### Generic Resource abstraction

So richer scenario packs can be added without changing the game engine.

### Umpire boundary

So different adjudicators can eventually replace the fixed LLM Umpire.

### Event log

So replay and richer analytics can be added later.

### Generic Effect descriptions

So AMAW remains scenario-agnostic.

### Provider-neutral model identifiers

So OpenRouter can eventually be replaced or supplemented.

These are interfaces, not large subsystems.

---

# 43. Large-Scenario Architecture Direction

This section defines **extension contracts, not MVP work**.

The lean MVP remains a three-or-more-Agent autonomous benchmark. These contracts exist so the initial implementation does not prevent later scenarios with governments, ministries, commands, press organisations, companies, NGOs, alliances and other synthetic Actors.

## 43.1 Generic organisations

Future versions may introduce:

```python
class Organisation:
    id: str
    name: str
    type: str
    parent_id: str | None = None
    metadata: dict = {}
```

`type` is scenario-defined.

Do not create core classes such as:

```text
Government
Army
Ministry
NewsOrganisation
Company
```

Actors may reference `organisation_id` and `role`, but the MVP does not need organisation behaviour.

---

## 43.2 Conversations and communication topology

MVP recipient-list Messages are sufficient for small games.

A later large-scenario implementation may introduce:

```python
class Conversation:
    id: UUID
    game_id: UUID
    members: list[str]
    channel_type: str
    metadata: dict = {}
```

This can represent scenario-defined constructs such as internal groups, alliance consultations, command networks or media channels without teaching the engine what those organisations mean.

Information must still be filtered server-side. Membership and visibility, not LLM instructions, determine access.

---

## 43.3 External Tool Gateway

External tool integration should use a generic boundary:

```python
class ToolProvider(Protocol):
    async def list_tools(self, actor_id: str) -> list[ToolDefinition]:
        ...

    async def call_tool(
        self,
        actor_id: str,
        name: str,
        arguments: dict,
    ) -> ToolResult:
        ...
```

Possible adapters:

```text
MCPToolProvider
HTTPToolProvider
LocalPythonToolProvider
```

The gateway is responsible for:

- Actor/scenario authorization;
- allowed-tool filtering;
- parameter validation where configured;
- credentials isolation;
- call logging;
- result provenance.

The critical invariant is:

> **External tools never directly mutate authoritative AMAW state.**

A simulator, GIS tool, database or analytical model may return information/evidence. Only normal AMAW game operations and adjudication create authoritative Effects.

MCP is therefore an adapter, not a mandatory AMAW core dependency.

---

## 43.4 Tool results as resources/evidence

A future tool call may create an ephemeral or persisted generic scenario/game resource containing:

```text
tool/provider
version
calling Actor
inputs or input hash
result
visibility
run ID
timestamp
```

This allows an Actor to use a simulation result in later reasoning while preserving provenance.

The MVP does not need an Evidence graph or generic tool-result persistence beyond ordinary logs.

---

## 43.5 Selective and event-driven Actor activation

The MVP may poll/activate all eligible Actors because Actor count is small.

Large scenarios should avoid all-to-all inference.

Future generic activation modes may include:

```text
always
phase
event
contacted
scheduled
manual
```

Conceptual flow:

```text
authoritative Event
↓
activation/routing policy
↓
relevant Actors
↓
agent invocation
```

The engine should not hard-code concepts such as "military escalation" or "press interest". A scenario or future routing component defines subscriptions/relevance.

---

## 43.6 Actor tiers and model allocation

Large games may use different inference budgets for different Actors:

```text
principal Actors   → high-capability persistent agents
secondary Actors   → invoked when relevant
reactive Actors    → primarily contacted/event-triggered
environment Actors → cheap/specialised mechanisms where appropriate
```

These are deployment/configuration policies rather than different core Actor classes.

---

## 43.7 Authority and approvals

Future organisational scenarios may need delegated authority or approval relationships.

Represent these generically through scenario policy/permissions rather than embedding government or military command logic.

Examples may include:

```text
Actor may perform action category
Actor requires approval from another Actor
Actor may communicate on specified channel
Actor may invoke specified tool
```

This is deliberately outside the lean MVP.

---

## 43.8 Scaling guidance

Approximate engineering direction:

```text
3–10 Actors
Current MVP process model is sufficient.

10–30 Actors
Selective activation and concurrency controls become useful.

30–100+ Actors
Event-driven routing, persistent Conversations, rate limits,
mixed model tiers and stronger worker management become likely.

100+ Actors
Task queues / persistent workers / alternative database may become justified.
```

These are not hard limits.

Do not introduce distributed infrastructure until actual measured load or scenario requirements justify it.

---

## 43.9 Future hybrid mode

The existing Seat abstraction should allow a future human client or remote integration to replace an autonomous Actor without changing game semantics.

A likely professional configuration is:

```text
small number of human principal roles
+
large number of synthetic secondary Actors
```

No human UI is required for MVP-0 through MVP-3.

---

# 44. Approximate implementation size target

This is not a contractual LOC requirement, but it is an architectural smell test.

Expected application-code magnitude:

```text
Core engine/API             ~1,500–2,500 LOC
Agent runner/tools          ~700–1,200 LOC
Umpire/consequence          ~300–700 LOC
Scenario/resource handling  ~400–800 LOC
Benchmark/export            ~400–800 LOC
```

Approximately:

```text
3,500–6,000 lines of product code
```

is a reasonable target for the lean MVP.

Tests may add a comparable amount.

If the system becomes tens of thousands of lines before MVP-1 works, reconsider the architecture.

---

# 45. Implementation order

A developer should build in this order:

```text
1. Pydantic/SQLModel domain types
2. scenario loader
3. SQLite persistence
4. game phase state machine
5. Messages
6. Arguments
7. Cons
8. Effects
9. visibility tests
10. FakeUmpire
11. ScriptedPlayers
12. six-turn deterministic integration test
13. Seat authentication
14. FastAPI routes
15. AI player tools
16. OpenRouter/Agno player
17. fixed AI Umpire
18. stall/error handling
19. JSON export
20. generic scenario resources
21. benchmark runner
22. role rotation
23. basic statistics
```

Do not work on later items while the deterministic integration test is broken.

---

# 46. Definition of technical MVP done

The lean technical MVP is done when:

> Starting from a fresh checkout, a developer can configure an OpenRouter API key, load a fictional scenario and launch three autonomous AI agents. They negotiate privately and publicly, inspect authorised scenario information, pursue private objectives, submit Matrix Arguments, challenge one another with Cons, receive structured adjudications from a fixed AI Umpire, experience persistent Effects and complete six turns. The system records the complete observable game history and exports it as JSON. The same scenario can then be run repeatedly with different models rotated across Actor roles, producing machine-readable benchmark results.

The implementation should be understandable by one developer.

The core should remain boring.

The interesting complexity should come from:

> **what the AI agents choose to do.**

---

# 47. Architecture rules to protect

```text
Do not build a military-specific engine.
Do not encode scenario semantics into core Python classes.
Do not let player agents mutate game state directly.
Do not let the Umpire perform RNG.
Do not expose hidden resources before filtering.
Do not depend on an LLM to enforce confidentiality.
Do not build a general agent orchestration platform.
Do not build professional-defence infrastructure before the experiment works.
Do not confuse repeatable configuration with deterministic LLM behaviour.
Do not optimise the benchmark before autonomous games are interesting.
Do not hard-code governments, militaries, press or companies as core Actor types.
Do not let external MCP/REST/tools bypass AMAW authorization or mutate game state.
Do not wake every Actor for every event once scenario scale makes that wasteful.
```

The MVP is deliberately small:

```text
Scenario
   │
   ▼
Small authoritative Matrix Game engine
   │
   ├── diplomacy
   ├── Arguments
   ├── Cons
   ├── adjudication
   ├── Effects
   └── generic resources
   │
   ▼
Autonomous AI agents
   │
   ▼
Game logs
   │
   ▼
Repeated model/role experiments
```

That is enough to test the central AMAW idea.
