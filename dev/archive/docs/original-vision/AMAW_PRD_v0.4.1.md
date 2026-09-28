# AMAW — Autonomous Matrix Wargame Platform

## Product Requirements Document

**Version:** 0.4.1  
**Status:** Proposed product baseline  
**Document purpose:** Define AMAW as an AI-native synthetic-actor and strategic-interaction platform, its Matrix-style game method, benchmark and professional wargaming use cases, analytical limits, scalability direction and MVP scope.

---

## 1. Product definition

AMAW is an **AI-native strategic-interaction and synthetic-actor platform** built around a lightweight Matrix Game protocol.

Its primary purpose is to place autonomous AI agents into persistent, multiplayer scenarios in which they occupy scenario-defined roles, possess different information and objectives, communicate with one another, seek additional information, make consequential proposals and react to a shared evolving world.

The platform provides:

- authoritative shared game state;
- scenario-defined Actors and roles;
- turn and phase sequencing;
- private and public communication;
- information access control;
- structured Matrix Arguments and Cons;
- adjudication and persistent Effects;
- generic scenario resources and on-demand retrieval;
- experiment and benchmark logging;
- repeatable model/role rotation;
- post-game analysis.

An **Actor** is any scenario-defined decision-making role. It may represent, for example, a government, ministry, military command, intelligence service, company, press organisation, NGO, international organisation, political faction, proxy group or other relevant participant. None of those types are hard-coded into AMAW.

The platform deliberately does **not** attempt to encode strategic reality into a large library of military, political, economic or organisational mechanics.

Its central product principle is:

> **AMAW provides a controlled interaction environment. Scenarios define the world and roles; AI agents supply the behaviour.**

The initial MVP is intentionally all-AI. Human Seats remain a future-compatible extension rather than an MVP dependency.

AMAW is intended to support four related uses:

1. **Synthetic-agent benchmark** — compare how models or complete agents behave under controlled strategic conditions.
2. **Synthetic scenario exploration** — run all-AI scenarios repeatedly to discover behaviours, interactions and plausible pathways worth examining.
3. **Synthetic role-player / control-cell augmentation** — populate professional games with autonomous secondary Actors that would otherwise be abstracted or manually controlled.
4. **Hybrid professional wargaming** — a future mode in which selected human decision-makers interact with a larger synthetic environment.

---

## 2. Product positioning

AMAW is scenario-agnostic at its core, but **defence, security, crisis-management and geopolitical wargaming are high-value applications** because these settings involve many interacting organisations, incomplete information, negotiation, deterrence, escalation and cross-domain effects that are difficult to represent with a fixed action space.

Potential professional users include:

- defence and national-security organisations;
- wargaming and operational-analysis teams;
- consultancies running analytical games;
- research organisations and universities;
- AI-agent developers and evaluators.

AMAW is not intended to replace:

- campaign analysis models;
- tactical combat simulations;
- force-on-force attrition models;
- logistics simulation;
- weapon-effect models;
- specialist economic, cyber or geospatial tools;
- digital twins;
- command-and-control systems.

Instead, these systems may later be attached as external tools or evidence sources available to authorised Actors or adjudicators.

The intended long-term position is:

> **AMAW is the strategic interaction layer around autonomous role players, scenario information and specialist analytical tools.**

For professional wargaming, a particularly important future use is a **synthetic control cell**: a small number of principal human or AI roles can interact with many persistent synthetic secondary Actors rather than collapsing those Actors into scripted injects or a single control team.

---

## 3. Product philosophy

AMAW should preserve the properties that make Matrix Games useful:

- few predefined restrictions;
- open-ended player action;
- explicit reasoning;
- adversarial challenge;
- transparent adjudication;
- persistent consequences;
- fast scenario creation;
- low rules overhead.

The engine should understand a small set of general objects:

```text
Actors
Seats
Arguments
Pros
Cons
Effects
Facts
Messages
Visibility
Turns
Adjudications
Events
Objectives
Observations
Resources
Claims
Evidence references
```

It does **not** require dedicated mechanics for:

```text
air superiority
submarine warfare
banking sanctions
elections
cyber operations
information operations
refugee flows
industrial policy
alliances
mobilisation
public opinion
```

Those phenomena may be represented through natural-language Arguments, Effects, scenario facts and evidence.

This is the main architectural simplification.

The same principle applies to scenario information. AMAW must not hard-code concepts such as `air_force`, `treaty`, `history`, `port`, `company` or `market_data` into the core domain. The engine provides generic resources, visibility, provenance, retrieval and relationships; each scenario defines the vocabulary and semantics it needs.

> **AMAW provides the information infrastructure. The scenario provides the ontology.**

---

## 4. Analytical philosophy

AMAW is an analytical game platform, not a prediction engine.

Its outputs are best understood as:

- conditional narratives;
- observed player behaviour;
- tested interactions between assumptions;
- evidence about the game system;
- hypotheses and questions for further investigation.

They are **not** direct forecasts of real-world state behaviour.

An autonomous AI playing an Actor represents:

> an AI model operating under a specified brief, information set, prompt, toolset and game environment.

It does not automatically represent the real government, military, organisation or population being portrayed.

Accordingly, AMAW must preserve enough experiment metadata to distinguish:

```text
Observed synthetic-player behaviour
```

from:

```text
Claims about real-world actor behaviour
```

The latter require independent analytical justification outside the game.

---

## 5. Analysis-led workflow

For defence analytical use, AMAW should support a workflow broader than simply:

```text
Scenario → Game → AAR
```

The preferred workflow is:

```text
Research question
        ↓
Hypotheses / expectations
        ↓
Scenario and treatment design
        ↓
Game configuration
        ↓
Human / AI / hybrid play
        ↓
Structured game observations
        ↓
Cross-game analysis
        ↓
Findings
        ↓
Confidence, limitations and open questions
```

The MVP does not need a complete research-management suite, but scenarios and benchmark runs must support metadata for:

- research question;
- experiment name;
- hypothesis or expectation identifiers;
- treatment/variant identifiers;
- run identifiers;
- player/model assignments;
- adjudicator configuration;
- evaluator configuration.

---

## 6. Product objective

The lean MVP must prove the central thesis with the smallest useful system:

> **Three or more autonomous AI Actors can complete a coherent multi-turn Matrix Game, independently communicate, seek authorised information, pursue private objectives and produce machine-readable behavioural data that can be compared across repeated runs.**

A successful autonomous run should support:

```text
Scenario loading
↓
AI Seat assignment
↓
Diplomacy
↓
Information seeking
↓
Actor Arguments
↓
Pros / Cons
↓
Fixed AI Umpire adjudication
↓
Persistent Effects
↓
Further turns
↓
Game completion
↓
Export
↓
Repeated role/model rotations
↓
Comparative analysis
```

The MVP validates the protocol with a small Actor count. It does **not** need to implement the large-scenario runtime, organisation hierarchy, MCP integration or human interface before the autonomous benchmark is useful.

The architecture should nevertheless leave a clean path toward scenarios containing many more Actors and organisations.

---

## 7. MVP success criteria

The first useful release succeeds when a developer can run **one six-turn fictional game with at least three autonomous AI Actors from beginning to end without human intervention**, then repeat that scenario across different models and role assignments.

### Required capabilities

| Requirement | Lean MVP target |
|---|---|
| Complete autonomous games | Yes |
| Three or more independent AI Actors | Yes |
| Different models per Actor | Yes |
| Private/public diplomacy | Yes |
| Actor-specific information | Yes |
| Generic scenario resources | Yes |
| On-demand resource search/read | Yes |
| Primary Argument budget | Yes |
| Pros and Cons | Yes |
| Fixed automated Umpire | Yes |
| Probabilistic outcomes | Yes |
| Persistent Effects | Yes |
| Minimal Effect expiry/supersession | Yes |
| Agent private notebook | Yes |
| Complete observable event log | Yes |
| Seeded server RNG | Yes |
| JSON game export | Yes |
| Model/role rotation | Yes |
| Multi-game benchmark runner | Yes |
| Raw behavioural/run metrics | Yes |
| Resource-access logging | Yes |
| Basic objective evaluation | Optional/recommended |
| AAR generation | Optional/recommended |
| Human Seats/UI | MVP+ |
| Organisations/hierarchies | MVP+ |
| Conversation/channel topology | MVP+ |
| External MCP/tool gateway | MVP+ |
| Event-driven Actor activation | MVP+ |
| Large synthetic populations | MVP+ |
| Secret Arguments | MVP+ |
| Tactical combat engine | No |
| Enterprise defence deployment | No |

The key acceptance question is not whether AMAW has many features. It is whether repeated autonomous games are **coherent, interesting and analytically inspectable**.

---

## 8. Explicit non-goals

AMAW MVP is not intended to be:

- a tactical military simulation;
- an order-of-battle engine;
- a weapon-performance model;
- a physics-based combat model;
- a geopolitical prediction engine;
- a digital twin;
- an intelligence assessment system;
- a commercial mass-multiplayer game;
- a highly available distributed service;
- an enterprise-grade defence deployment.

In particular:

> **AMAW simulations generate evidence about behaviour inside a configured game. They do not establish what will happen in a real conflict or crisis.**

---

## 9. Primary users

### 9.1 Scenario / experiment designer

Defines:

- research questions or game purpose;
- scenario world and starting conditions;
- Actors and role briefs;
- organisations where relevant;
- objectives;
- private information;
- scenario resources;
- communication constraints where supported by the selected scenario-format version;
- game rules;
- benchmark treatments and run configurations.

### 9.2 Autonomous Actor

Receives one role perspective and independently:

- observes;
- communicates with authorised Actors;
- negotiates;
- seeks information;
- uses available tools where configured;
- plans;
- records private notes;
- makes Arguments;
- challenges other Arguments;
- adapts to game events.

### 9.3 Analyst / AI researcher

Runs repeated games and compares:

- model behaviour;
- role effects;
- scenario treatments;
- information-seeking behaviour;
- tool use;
- strategy patterns;
- coordination and conflict;
- escalation pathways;
- objective performance;
- reliability and cost;
- adjudication sensitivity.

### 9.4 Professional wargaming team / consultancy

A future professional user may use AMAW to:

- populate secondary roles with synthetic Actors;
- run synthetic control cells;
- explore scenario variants before or after a workshop;
- execute large numbers of follow-on synthetic runs;
- connect authorised specialist analytical tools;
- compare synthetic behaviour with observations from human games.

### 9.5 Human player / facilitator

Human participation is a future-compatible mode, not a requirement for the lean MVP. Human Seats should eventually use the same logical protocol as AI Seats so professional games can mix principal human roles with synthetic secondary Actors.

---

## 10. Core Matrix Game model

The canonical gameplay object is the **Argument**.

An Argument expresses:

```text
Actor
  ↓
Action
  ↓
Desired Effect
  ↓
Pros
  ↓
Opposing Cons
  ↓
Adjudication
  ↓
Outcome
  ↓
Established Effects
```

Example:

```text
ACTION
Arkania publicly offers Cestia a security guarantee.

DESIRED EFFECT
Cestia refuses Belvar's request for military basing.

PROS
1. Cestia depends heavily on Arkanian trade.
2. Arkania has privately offered economic assistance.
3. Cestia's government has campaigned on neutrality.

CONS
1. Cestia distrusts Arkania after the previous crisis.
2. Belvar remains Cestia's principal security guarantor.
```

An Argument must propose a sufficiently clear change that an adjudicator can determine whether it occurred and which Effects should follow.

---

## 11. Action budget

The default Matrix rule is:

> **Each Actor receives one primary Argument per turn.**

This forces prioritisation and prevents autonomous agents from attempting an unlimited list of consequential actions.

However, the engine must treat the action budget as **scenario-configurable**, not hard-coded.

Example:

```yaml
game:
  primary_arguments_per_actor: 1
```

A future scenario may use:

```yaml
game:
  primary_arguments_per_actor: 2
```

Diplomatic messages, offers, threats, promises, requests and ordinary discussion do not consume the primary Argument unless the scenario explicitly defines otherwise.

The MVP should not introduce separate military-specific action classes.

Scenario-specific communication restrictions beyond the baseline public/private messaging contract are MVP+. The Lean MVP does not require a generic communication-policy DSL.

---

## 12. Game lifecycle

A normal game consists of:

```text
SETUP

↓
TURN 1
    Diplomacy
    Actor A Argument
    Cons
    Adjudication
    Effects
    Actor B Argument
    Cons
    Adjudication
    Effects
    Actor C Argument
    Cons
    Adjudication
    Effects
    Consequence Management

↓
TURN 2
    ...

↓
FINAL TURN

↓
END GAME

↓
Objective Evaluation

↓
After-Action Analysis
```

The default turn order is fixed by scenario.

The scenario may later support:

- randomised order;
- alternating order;
- negotiated order;
- initiative mechanisms.

These are post-MVP unless required by a specific experiment.

---

## 13. Diplomacy

Diplomacy is a first-class mechanic.

Players may communicate privately or publicly between Arguments.

Supported message intents may include:

```text
message
announcement
offer
promise
threat
request
proposal
reply
```

These intents are descriptive metadata. The engine does not determine whether the contents are truthful.

Agents may bluff, conceal and lie.

### Diplomacy readiness

`ready()` means:

> **The Actor currently has no further useful action to take in this phase.**

It does not permanently silence that Actor.

For the Lean MVP, if a ready Actor receives a new private Message before the diplomacy phase closes, the server clears that Actor's ready state. The Actor may then be activated again by the normal runner polling loop. This reset is deterministic server behaviour, not an LLM decision.

The Lean MVP closes diplomacy when:

- all required Actors are currently ready; or
- the phase timeout occurs; or
- an authorised administrator forces advancement.

A private Message sent to a ready recipient before closure clears that recipient's ready state before the close condition is evaluated. Scenario-configurable diplomacy close/reactivation policies are MVP+. This avoids systematic bias in favour of slower or faster agents without requiring event-driven orchestration.

---

## 14. Information and visibility

AMAW supports three baseline visibility classes:

```text
PUBLIC
known to all participating Actors

PRIVATE
known only to explicitly authorised Actors

FACILITATOR
known only to facilitator/engine-authorised roles
```

Every:

- Effect;
- Message;
- Argument where applicable;
- note;
- brief;
- observation;
- scenario resource;
- resource search result;
- resource attachment;
- claim

must be filtered server-side.

The model itself must never be relied upon to ignore information it has already received.

Example:

```text
PUBLIC
"Red has announced mobilisation."

BLUE PRIVATE
"SIGINT suggests mobilisation is only 40% complete."

RED PRIVATE
"Transport shortages mean mobilisation is badly delayed."

FACILITATOR
"Actual mobilisation is 35% complete."
```

Information walls are a hard MVP requirement.

Visibility must apply before search and retrieval. A hidden resource must not become discoverable merely because its title, tags, search snippets, attachment name or existence appears in a search result.

---

## 15. Facts and Effects

AMAW's world state is primarily represented by **Effects**, rather than a detailed simulation ontology.

An Effect may represent:

```text
Blue has mobilised.
Public support for Cestia has fallen.
The northern road is blocked.
Relations between Arkania and Cestia have improved.
Commercial insurance rates have increased.
Red possesses a diplomatic advantage [+1].
```

Effects persist until they are:

- superseded;
- expired;
- explicitly removed;
- reversed by a later authoritative outcome.

The engine must preserve provenance.

An Effect should be able to record:

```text
source Argument
source Event
source adjudication
source consequence proposal
Effects that it supersedes
visibility
creation turn
expiry
```

Natural language remains the primary representation. The additional structure exists to avoid contradictory active state.

---

## 16. Consequence management

At the end of each turn, an authorised consequence resolver reviews the turn and may propose a small number of indirect or continuing consequences.

For autonomous play, the default maximum is:

```text
0–3 consequences per turn
```

A consequence may arise from combinations of events such as:

```text
Red mobilisation
+
Blue warning
+
failed negotiations

→ commercial shipping insurance rises
→ Cestia government faces increased domestic pressure
```

The consequence component is not an unrestricted world-authoring agent.

It should prefer consequences that are:

- directly supported by events;
- proportionate to the turn timescale;
- analytically useful;
- not redundant with existing Effects.

All accepted consequences become authoritative Effects and retain:

- rationale;
- source event IDs;
- resolver identity/configuration;
- visibility decision.

For human-supervised games, consequence proposals may require facilitator approval.

---

## 17. Adjudication

AMAW supports interchangeable adjudication modes.

The engine owns the authoritative result, but an adjudicator may be human, AI, rule-based or hybrid.

### 17.1 LLM Umpire

The adjudicator receives only the information it is authorised to use:

```text
scenario context
relevant established Effects
argument
pros
cons
relevant hidden information
turn timescale
external evidence where applicable
```

It produces a structured proposal:

```json
{
  "mode": "probabilistic",
  "probability": 0.7,
  "rationale": "...",
  "success_effects": ["..."],
  "failure_effects": ["..."],
  "supersedes_effect_ids": []
}
```

Supported resolution modes:

```text
automatic_success
automatic_failure
probabilistic
```

The Python engine performs any random draw.

The adjudicator proposes outcomes and Effects.

The server validates and stores the authoritative result.

### 17.2 Human adjudicator

A human facilitator may provide the same structured decision through a UI or API.

### 17.3 Assessment panel

A post-MVP mode may ask multiple assessors to independently estimate likelihood using a bounded scale such as:

```text
10%
30%
50%
70%
90%
```

The engine may aggregate estimates using a configured rule such as median.

### 17.4 Hybrid adjudication

The architecture should permit adjudicators to incorporate evidence from:

- combat models;
- logistics models;
- cyber models;
- geospatial tools;
- intelligence assessments;
- human SMEs;
- other analytical systems.

AMAW should not absorb these specialist models into its core domain.

---

## 18. AI design principle

AMAW treats AI player agents and AI adjudicators differently.

### Player AI

Player AI may be:

- creative;
- deceptive;
- opportunistic;
- risk-taking;
- strategically inconsistent.

Those behaviours are part of the observed game.

### Umpire AI

Umpire AI should be:

- conservative;
- evidence-bound;
- structured;
- consistent;
- traceable;
- calibrated;
- explicit about uncertainty.

A questionable player decision is potentially useful behavioural data.

A questionable umpire decision changes the game world and therefore requires greater scrutiny.

---

## 19. Actor briefs

Actor briefs should remain compact and perspective-specific.

Example:

```yaml
id: arkania
name: Republic of Arkania

public_description: |
  A large regional power bordering the Northern Strait.

private_brief: |
  You are the political leadership of Arkania...

objectives:
  - Control access to the Northern Strait
  - Prevent direct intervention by Belvar
  - Preserve relations with Cestia
  - Long-term: establish Arkania as the dominant regional power

private_information:
  - Reserve mobilisation will take two turns.
  - Domestic approval is fragile.

starting_effects:
  - description: "Strong domestic nationalist pressure"
    visibility: private
    visible_to: [arkania]
```

Agents should derive strategy themselves rather than receiving a rigid workflow.

---

## 20. Scenario resources and knowledge

Scenario information may be much larger than an Actor's normal working context. AMAW must therefore support **progressive, on-demand access** to authorised scenario material rather than placing the entire scenario pack into every prompt.

A scenario may provide arbitrary material such as:

```text
background narrative
history
maps
images
PDFs
intelligence reports
capability descriptions
doctrine
economic data
political information
biographies
infrastructure data
timelines
structured datasets
reference documents
scenario-specific entities
```

These examples are not AMAW domain types.

The core engine uses a generic concept:

```text
Scenario Resource
```

A resource has, at minimum:

```text
ID
name
scenario-defined type
content and/or attachment
visibility
provenance
tags
arbitrary metadata
relationships
```

The `type` is scenario-defined and open-ended.

One defence scenario may define:

```text
military_formation
intelligence_assessment
treaty
location
infrastructure
```

Another scenario may define:

```text
company
product
regulation
market_dataset
supply_chain
```

No core engine change should be required.

### Resource types and schemas

A scenario may optionally declare resource-type definitions and metadata expectations, but this is not mandatory.

A minimal scenario may simply contain:

```yaml
resources:
  - id: background
    type: document
    path: resources/background.md

  - id: regional-map
    type: map
    path: maps/region.png
```

A sophisticated scenario may additionally define its own vocabulary, validation schemas, relationship types and metadata conventions.

Complexity is available to the scenario designer rather than imposed on every game.

### Resources, information and authoritative state

AMAW must distinguish three concepts:

```text
RESOURCE
Information or an asset available in the scenario.

CLAIM
A statement available to one or more Actors that may be incomplete,
uncertain, interpreted or false.

EFFECT
An authoritative current condition in the game world.
```

For example:

```text
CLAIM:
"Arkanian intelligence assesses that the bridge has been destroyed."

EFFECT:
"The bridge has been destroyed."
```

The first must not automatically create the second.

Scenario resources and Claims may be informational rather than authoritative truth. Their provenance and epistemic status must be retained.

### Progressive disclosure

Scenario information may be exposed at three broad levels:

```text
PUSHED
Included automatically in the Actor's brief/current context.

AVAILABLE
Authorised and searchable/readable if the Actor chooses to investigate.

UNAVAILABLE
Not accessible to the Actor unless later game events change visibility.
```

The exact labels may be scenario-configurable, but the core distinction between automatically supplied, retrievable and inaccessible information should be supported.

An Actor should therefore be able to ask questions such as:

```text
What previous agreements concern the Strait?
What information do we have on Belvar's readiness?
Show me information relevant to fuel infrastructure.
What does the scenario material say about the 2024 crisis?
What maps are available for the northern region?
```

without receiving the entire scenario package.

### Generic player tools

The player interface should use generic retrieval tools such as:

```text
search_resources
read_resource
inspect_asset
list_resources
```

The core should not expose domain-specific tools such as:

```text
get_air_force
get_treaty
get_history
get_economic_data
```

Format-specific handling is acceptable. For example, the platform may know how to read text, paginate a table, return an image or expose GeoJSON features without knowing what those contents mean strategically.

### Maps and spatial material

Maps are scenario resources, not military movement mechanics.

A scenario may provide:

```text
PNG/JPEG maps
PDF maps
GeoJSON
structured locations
infrastructure overlays
other geospatial assets
```

AMAW controls access, provenance and retrieval. It does not infer that a map requires hex movement, ranges, terrain combat modifiers or any other military-specific rule.

### Data and capability information

Capabilities and data may be structured where useful, but the structure belongs to the scenario.

For example, a scenario may represent a force, corporation, political group or infrastructure system as a generic resource containing arbitrary structured data.

The engine should not create permanent Python classes for every scenario concept.

### Information-seeking as behaviour

For autonomous-player experiments, choosing what to investigate is itself potentially meaningful behaviour.

AMAW should therefore preserve resource-search and retrieval activity in the experiment log so analysts can examine:

```text
what information an Actor searched for;
which resources it inspected;
which information it ignored;
which evidence it cited in Arguments;
how information-seeking differed between models or roles.
```

This may be analytically valuable without implying that synthetic information-seeking behaviour predicts real decision-makers.

---

## 21. Organisations and role ecosystems

Large scenarios should be able to represent **multiple Actors within the same organisation** without assuming that an organisation behaves as one perfectly unified decision-maker.

Examples may include:

```text
Government
├── Head of Government
├── Foreign Ministry
├── Defence Ministry
├── Intelligence Service
└── Finance Ministry

Military
├── Joint Headquarters
├── Land Component
├── Maritime Component
└── Air Component

Information environment
├── News organisation
├── Opposition party
└── Civil-society actor
```

These are examples only. AMAW should use generic concepts such as:

```text
Organisation
Actor
role
parent organisation
relationships
```

rather than hard-coded government, military or press classes.

Actors within the same organisation may have overlapping but non-identical objectives and information. Internal disagreement, coordination failure, information withholding and bureaucratic friction may therefore emerge from play rather than being scripted by the engine.

Organisation hierarchy is **MVP+**. The lean MVP proves the interaction protocol with independent Actors first.

---

## 22. Communication topology

Communication should eventually be more expressive than global all-to-all messaging.

A scenario may define communication structures such as:

```text
private bilateral conversation
cabinet/internal group
military command group
alliance consultation
public announcement
press channel
restricted classified channel
```

The engine should treat these as generic scenario-defined communication relationships or Conversations rather than embedding organisational semantics.

Information propagation must not be assumed. If one Actor learns something, another Actor should know it only when:

- the scenario initially grants access;
- a shared resource is available;
- another Actor communicates it;
- an authoritative event changes visibility.

This enables scenarios to represent organisational friction and information topology without creating a separate organisational simulation engine.

Recipient-list messaging is sufficient for the lean MVP. Persistent Conversations/channels are **MVP+**.

---

## 23. External tools and MCP

AMAW should eventually allow scenario designers to give different Actors access to different external capabilities, including:

```text
specialist simulations
GIS/geospatial tools
search and knowledge systems
structured databases
logistics/economic models
calculators
local Python tools
REST services
MCP servers
other analytical systems
```

Tool access is scenario-defined and Actor-specific.

The core invariant is:

> **External tools may provide information, calculations, simulations or evidence. They may not directly mutate authoritative AMAW game state.**

A typical flow is:

```text
Actor considers action
↓
Actor invokes authorised external tool
↓
Tool returns evidence / estimate / simulation result
↓
Actor uses result in strategy or Argument
↓
Other Actors may challenge it
↓
Umpire adjudicates
↓
AMAW creates authoritative Effects
```

This allows AMAW to connect to sophisticated domain models without becoming one.

MCP is a natural integration mechanism but should not be the only possible protocol. The product should eventually expose a generic Tool Provider / Tool Gateway abstraction with MCP, REST and local adapters.

External tool integration is **MVP+** and must not block the initial autonomous benchmark.

---

## 24. Scalable Actor activation

Small games can invoke all eligible Actors during each relevant phase.

Large games should not assume that every Actor reasons after every event. AMAW should eventually support **selective/event-driven activation** so most secondary Actors remain dormant until relevant.

Conceptually:

```text
Event occurs
↓
Determine which Actors are affected, contacted or subscribed
↓
Activate relevant Actors
↓
Others remain dormant
```

Generic future activation policies may include:

```text
always
phase-based
event-based
contacted
scheduled
manual
```

Scenario semantics determine relevance; the engine supplies routing and activation mechanics.

This is important both for cost and realism. A scenario may contain many represented entities while only a subset requires expensive model inference at a given moment.

Event-driven activation is **not required for the lean MVP**.

---

## 25. Operating modes and scale

AMAW should support a progression from small experiments to richer synthetic environments.

### Synthetic benchmark

All Actors are AI. The same scenario is repeated across model/agent configurations, role assignments and seeds.

### Synthetic scenario exploration

All or nearly all Actors are AI. The purpose is to explore interactions, pathways and scenario assumptions rather than rank models.

### Synthetic control cell

Professional wargame teams use persistent AI-controlled secondary Actors to represent organisations that would otherwise be scripted or collapsed into a control function.

### Hybrid wargame

Selected principal roles are human; a larger environment of secondary roles is synthetic.

The lean MVP implements the first mode and provides much of the foundation for the second.

A reasonable architectural direction is:

```text
3–10 Actors
current phase/turn activation is sufficient

10–30 Actors
selective activation becomes desirable

30–100+ Actors
event-driven activation, conversations, rate limits and mixed model tiers

100+ Actors
persistent workers/task infrastructure may become justified
```

These ranges are engineering guidance, not hard product limits. Analytical value, not Actor count, should determine scenario size.

---

## 26. Autonomous player behaviour

An autonomous agent controls one Seat representing one Actor.

Typical instruction:

```text
You control the Republic of Arkania.

Pursue your Actor's objectives throughout the game.

You may inspect your situation, communicate with other Actors,
maintain private notes and make Matrix Arguments.

You are autonomous. Decide when to negotiate, conceal intentions,
make commitments, bluff, plan or act.

Respect the current game phase and available tools.

When you currently have nothing further to contribute, call ready().
```

Typical tools:

```text
observe
search_resources
list_resources
read_resource
inspect_asset
read_messages
send_message
make_argument
make_con
get_notes
save_note
delete_note
ready
```

Scenario-resource tools are generic. Their results are visibility-filtered by the server before they reach the player.

Secret Arguments are excluded from the first MVP.

---

## 27. Agent memory

AMAW does not require or store private model chain-of-thought.

Each autonomous player receives:

### Session context

Recent interaction context managed by the agent runtime.

### Private strategic notebook

Persistent within one game:

```text
beliefs
plans
promises
suspicions
relationships
lessons
important observations
```

Memory is isolated by:

```text
game_id + actor_id
```

Memory does not carry between benchmark games unless deliberately enabled as an experimental treatment.

---

## 28. Human participation

The lean MVP is intentionally autonomous-agent-first.

The server should nevertheless continue to represent a **Seat**, not an AI-specific identity, so a later human or remote integration can occupy the same Actor role.

```text
Actor
  ↓
Seat
   ├── autonomous agent   ← MVP
   ├── human              ← future
   └── remote integration ← future
```

In professional hybrid games, AMAW may eventually support a small number of human principal decision-makers surrounded by many synthetic secondary Actors.

Human interfaces and facilitator workflows are **MVP+**. They must not increase the initial build scope.

---

## 29. Game completion

A game ends when:

```text
max_turns reached
OR
facilitator/admin ends game
```

Scenario-defined termination conditions are MVP+ and are not part of the Lean MVP scenario schema.

At completion, AMAW produces or makes available:

```text
final public world state
authorised private state
complete event history
argument history
authorised diplomacy history
Actor objectives
objective assessments
model/agent metadata
adjudicator metadata
game statistics
after-action narrative
experiment metadata
scenario/resource manifest and versions
authorised resource-access history where configured
```

---

## 30. Replayability and reproducibility

AMAW uses precise terminology.

### Replayable

A completed game can be reconstructed from stored authoritative records **without invoking any LLM again**.

### RNG reproducible

All server-owned stochastic decisions can be reproduced from recorded RNG state or seed.

### Experiment repeatable

A new game can be launched with the same declared scenario, prompts, model configuration and experimental settings.

AMAW does **not** claim that a hosted generative model will return identical outputs on repeated execution.

For audit and experiment comparison, the platform should retain:

- effective prompt/instructions version;
- model requested;
- model returned;
- provider;
- model parameters;
- reasoning configuration where exposed;
- tool calls;
- tool results;
- token counts;
- timestamps;
- raw structured adjudicator outputs;
- relevant provider metadata where available;
- scenario version and resource manifest;
- immutable hashes/versions of resource attachments used during the run;
- resource searches and reads performed by autonomous players where experiment logging is enabled.

---

## 31. Benchmark and experimental mode

Every agent connection records:

```text
model
provider
model parameters
temperature
reasoning configuration
agent framework/version
prompt version
```

Metrics may include:

```text
games completed
agent stalls
tool calls
invalid calls
arguments made
messages sent
tokens
latency
cost
objective scores
wins/outcomes
action diversity
escalation indicators
diplomatic agreements
broken commitments
```

Model rotation should mitigate role advantage.

Example:

```text
Game 1   A=Model X   B=Model Y   C=Model Z
Game 2   A=Model Z   B=Model X   C=Model Y
Game 3   A=Model Y   B=Model Z   C=Model X
```

Experiment runs should also support treatment labels so that changes in:

- scenario facts;
- Actor briefs;
- available information;
- adjudication mode;
- turn order;
- action budget

can be analysed explicitly.

---

## 32. Objective evaluation

Actor objectives remain private until game completion unless the scenario says otherwise.

For MVP, a separate evaluator may score objectives on a bounded scale such as:

```text
0 failed badly
1 mostly failed
2 partially achieved
3 largely achieved
4 fully achieved
```

However, scenarios should be able to provide evaluator anchors.

Example:

```yaml
objectives:
  - text: Prevent direct Belvar intervention
    evaluation:
      0: "Belvar enters direct hostilities."
      2: "Belvar deploys forces but does not directly intervene."
      4: "No Belvar military deployment or direct intervention occurs."
```

The LLM or human evaluator maps game evidence to the scenario-defined rubric.

Evaluator metadata must be recorded separately from player metadata.

Objective scores are analytical aids, not objective truth.

---

## 33. After-action analysis

AMAW should produce an after-action product containing:

```text
Game summary
Timeline
Major turning points
Actor strategies
Important agreements
Deception and broken commitments
Successful Arguments
Failed Arguments
Unexpected consequences
Objective results
Evidence relevant to experiment hypotheses
Open analytical questions
Limitations and caveats
```

An LLM may assist with synthesis because this occurs after gameplay and does not modify authoritative game state.

---

## 34. Secret Arguments

Secret Arguments are a valid Matrix Game concept but are **post-MVP**.

The initial implementation should not introduce arbitrary natural-language trigger monitoring.

A later version may support:

```text
hidden Argument
visibility scope
explicit trigger
defined evaluation points
trigger audit log
```

Trigger evaluation should occur only at defined synchronisation points, such as after authoritative events or resolved Arguments.

---

## 35. MVP restraint

No feature enters MVP unless it is required to complete an interesting, inspectable autonomous Matrix Game or to make the resulting experiment technically defensible.

In particular, MVP should resist:

- tactical rules;
- weapon mechanics;
- large ontologies;
- generic workflow frameworks;
- distributed microservices;
- opaque autonomous orchestration;
- elaborate secret-trigger systems;
- hard-coded defence-specific scenario resource classes;
- domain-specific retrieval APIs where generic resource retrieval is sufficient.

The intended complexity belongs primarily:

> **inside the decisions of the players and the evidence used by adjudicators, not inside the core rules engine.**

---

## 36. Definition of MVP done

The hard acceptance test is:

> Starting from a fresh checkout, a developer can configure model access, run one command and launch a fictional three-Actor scenario in which autonomous AI agents independently negotiate, search authorised scenario information, maintain private notes, submit Matrix Arguments, respond with Cons, undergo structured adjudication from a fixed AI Umpire, experience persistent Effects and complete six turns without human intervention. Duplicate or retried client operations do not corrupt game state. At completion, the observable game history can be exported as JSON and all server-owned random decisions are logged with their probability and draw.

A second acceptance test is:

> The same scenario can be run repeatedly across multiple models and balanced role rotations while preserving scenario version, prompt version, model assignment, Umpire configuration and RNG seed, producing machine-readable behavioural/run data.

A third acceptance test is:

> During play, an Actor can search and retrieve additional authorised scenario information—including generic documents, structured data and optional map/image assets—without being given inaccessible material and without the core engine needing scenario-specific domain classes.

Human Seats, organisation hierarchies, persistent communication channels, external MCP/tools and event-driven large-Actor activation are explicitly **MVP+**.

---

## 37. Product principles to protect

1. **Thin authoritative engine**
2. **Open-ended player action**
3. **Server-enforced information walls**
4. **Autonomous-agent-first MVP**
5. **AI creativity at the player layer**
6. **Conservative, inspectable adjudication**
7. **Natural-language Effects with minimal provenance**
8. **Generic scenario resources; scenario-defined semantics**
9. **Progressive disclosure instead of giant prompts**
10. **Resources and claims must not silently become authoritative Effects**
11. **External specialist tools rather than a military-specific core**
12. **External tools provide evidence; AMAW alone owns authoritative state**
13. **Scenario-defined organisations and roles, not hard-coded role classes**
14. **Scale through selective activation rather than all-to-all inference**
15. **Behavioural comparison before predictive claims**
16. **Experiment controls around the game, not just game execution**
17. **MVP simplicity**
