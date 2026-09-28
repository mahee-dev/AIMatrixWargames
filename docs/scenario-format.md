# AMAW Scenario Authoring Specification

**Version:** 0.5.2 (D-104: §5 new versions are `MAJOR.MINOR.PATCH`; §20 any hashed change bumps the version, and a scenario library locks each version's hashes)  
**Status:** Normative for Lean MVP  
**Aligned with:** `PRD.md`, `TRD.md`. Earlier versions of this contract are recorded in the decision log.

---

## 1. Purpose

A scenario defines the strategic environment in which autonomous Actors operate.

Scenarios must remain **domain-generic**.

The AMAW engine does not know what a:

```text
government
brigade
company
newspaper
port
treaty
alliance
market
intelligence service
```

means.

A scenario expresses such concepts through generic:

- Actors;
- text;
- objectives;
- private information;
- Effects;
- Resources;
- metadata.

---

# 2. Scenario Package

A scenario is a directory.

Recommended layout:

```text
northern-strait/
│
├── scenario.yaml
└── resources/
    ├── background.md
    ├── trade.csv
    ├── arkania-intel.md
    └── regional-map.png
```

Only `scenario.yaml` is required.

Directory layout is convention, not core semantics.

Paths inside `scenario.yaml` are resolved relative to the scenario directory.

---

# 3. Minimal Scenario

```yaml
scenario:
  id: minimal-demo
  version: 1.0.0
  name: Minimal Demo
  description: >
    A small fictional strategic interaction.

game:
  max_turns: 6
  in_world_turn_length: "1 week"
  turn_order:
    - actor_a
    - actor_b
    - actor_c
  primary_arguments_per_actor: 1
  max_cons_per_actor: 3

actors:
  actor_a:
    name: Actor A
    public_description: >
      A major stakeholder.
    private_brief: >
      You control Actor A.
    objectives:
      - "Achieve Objective A."
    private_information:
      - "Private fact A."

  actor_b:
    name: Actor B
    public_description: >
      Another stakeholder.
    private_brief: >
      You control Actor B.
    objectives:
      - "Achieve Objective B."
    private_information: []

  actor_c:
    name: Actor C
    public_description: >
      A third stakeholder.
    private_brief: >
      You control Actor C.
    objectives:
      - "Achieve Objective C."
    private_information: []

starting_effects:
  - description: "The situation is tense but stable."
    visibility: public

resources: []
```

---

# 4. Top-Level Schema

Required sections:

```yaml
scenario:
game:
actors:
```

Optional:

```yaml
rules:
starting_effects:
resources:
benchmark:
metadata:
```

Unknown top-level fields may either:

- be preserved as metadata; or
- be rejected in strict mode.

The implementation should choose one policy and test it consistently.

---

# 5. `scenario`

Required:

```yaml
scenario:
  id: northern-strait
  version: 1.0.0
  name: Northern Strait Crisis
```

Optional:

```yaml
  description: >
    A fictional regional crisis.
```

### Rules

`id`:

- non-empty;
- stable across revisions;
- recommended lowercase kebab-case;
- unique within scenario library.

`version`:

- a `MAJOR.MINOR.PATCH` string for every new version (D-104); an integer version released
  before D-104 stays valid until the package's next change, and loaders still accept integers;
- must change when scenario content changes in a benchmark-relevant way (§20).

`name`:

- human-readable.

---

# 6. `game`

Example:

```yaml
game:
  max_turns: 6
  in_world_turn_length: "2 weeks"

  turn_order:
    - arkania
    - belvar
    - cestia

  primary_arguments_per_actor: 1
  max_cons_per_actor: 3

  diplomacy_timeout_seconds: 120
  cons_timeout_seconds: 60

  consequence_mode: none

  # Optional recommended condition (D-099); omit for the released defaults.
  diplomacy_mode: bounded_rounds
  diplomacy_rounds_per_turn: 3
  con_mode: sampled_sealed
  con_challengers_per_argument: 2
```

Required:

```text
max_turns
turn_order
```

Recommended defaults:

```text
primary_arguments_per_actor = 1
max_cons_per_actor = 3
```

Timeouts may be omitted in deterministic tests.

`primary_arguments_per_actor` is the Argument budget per Actor per turn: 1 (the default, the
released loop), 2 or 3 (D-094). A budget above 1 runs that many **Argument rounds** per turn
(`round_robin_v1`, state-machine §17/§20). Each round walks `turn_order` once and gives every
Actor one Argument, so one Actor never argues twice in a row, and every slot must be used. The
consequence pass, when enabled, follows the last round. Use a budget above 1 when the source
game genuinely gives each player several actions per turn. Do not use it to lengthen a turn: each
round adds a full cycle of Arguments, Cons and adjudications.

Typed slots have no schema. When slot 2 must be a particular kind of action, write it as a `rules`
entry (§11.1) that names the slot, for example "An Argument in `budget_slot` 2 must be a military
action". The Actor and Umpire contexts carry each Argument's `budget_slot` whenever the budget is
above 1. Conditional bonus actions, once-per-game bonus actions and declining a slot are not
supported (I-109).

`consequence_mode` is optional: `none` (the default, the released loop) or `bounded_effects_v1`
(one bounded, evidence-linked end-of-turn consequence pass, state-machine §21 Option B; D-091).
It is the scenario's default for the condition. An explicit creation-time value (API body or CLI)
overrides it, and the resolved value is persisted and sealed with the run. A `bounded_effects_v1`
game needs a consequence resolver configured, and the runner refuses to start without one.

`diplomacy_mode`, `diplomacy_rounds_per_turn`, `con_mode` and `con_challengers_per_argument` are
optional (D-099). They declare the scenario's recommended Diplomacy and Con conditions, with the
same values and pairing rules as the creation-time fields (api.md §14):

| Field | Values | When absent |
|---|---|---|
| `diplomacy_mode` | `open_diplomacy` or `bounded_rounds` | `open_diplomacy` (released) |
| `diplomacy_rounds_per_turn` | positive integer; required with, and only with, `bounded_rounds` | none |
| `con_mode` | `open_cons` or `sampled_sealed` | `open_cons` (released) |
| `con_challengers_per_argument` | positive integer, at most the Actor count minus one; required with, and only with, `sampled_sealed` | none |

Like `consequence_mode`, each is a default. A new game resolves every condition field in one
order: **the explicit creation-time value** (API body field, CLI flag or a set `AMAW_*` value),
**then the scenario's default, then the released default**. A count follows its mode: when the
count is not given explicitly it comes from the scenario only if the resolved mode is the
scenario's mode, so an explicit `open_diplomacy` over a `bounded_rounds` scenario runs open
Diplomacy with no bound.

The resolved condition is persisted on the Game and sealed in the run manifest, so a scenario
default is part of the experimental condition and its provenance exactly as a flag would be.
Resume restores and compares that sealed condition; it never re-reads the scenario's defaults, so
a run created before a scenario declared them keeps the condition it ran under. A scenario that
declares none of the four fields runs the released defaults, as before.

### Validation

- `max_turns >= 1`;
- every Actor in `turn_order` must exist;
- each Actor appears at most once in `turn_order`;
- every Actor expected to act should appear in `turn_order`;
- Argument budget is 1, 2 or 3. A value above 1 runs round-robin Argument rounds (D-094, which
  widens D-069's budget of exactly 1);
- Con limit >= 0;
- `consequence_mode`, when present, is `none` or `bounded_effects_v1`;
- `diplomacy_mode`/`diplomacy_rounds_per_turn` and `con_mode`/`con_challengers_per_argument`,
  when present, follow the table above, and `con_challengers_per_argument` does not exceed the
  opponents per Argument (D-099).

---

# 7. `actors`

Example:

```yaml
actors:
  arkania:
    name: Republic of Arkania

    public_description: >
      A large regional power bordering the Northern Strait.

    private_brief: >
      You are the political leadership of Arkania.
      Preserve strategic freedom of action while avoiding
      an uncontrolled regional war.

    objectives:
      - "Increase influence over the Northern Strait."
      - "Prevent direct Belvar intervention."
      - "Maintain relations with Cestia."

    private_information:
      - "Reserve mobilisation requires two turns."
      - "Domestic approval is fragile."
```

Required per Actor:

```text
name
public_description
private_brief
objectives
```

Optional:

```text
private_information
metadata
```

### Actor IDs

The YAML mapping key is the authoritative Actor ID.

Recommended:

```text
lowercase_snake_case
```

or:

```text
lowercase-kebab-case
```

Choose one convention per project.

### Actor semantics

An Actor may represent anything that makes decisions:

```text
state
ministry
military command
company
newspaper
NGO
political faction
alliance
international organisation
synthetic control-cell role
```

No engine code changes are required.

---

# 8. Actor Brief Design

Actor briefs should be concise enough to fit comfortably into model context.

A good brief answers:

```text
Who are you?
What do you care about?
What is your situation?
What constraints matter?
```

Avoid giving detailed strategy.

Bad:

```text
Turn 1: contact Cestia.
Turn 2: mobilise.
Turn 3: threaten Belvar.
```

Good:

```text
You seek greater regional influence but want to avoid a direct war
with Belvar. Your economy depends on continued shipping. Domestic
nationalists are demanding a stronger stance.
```

Agents should derive strategy themselves.

---

# 9. Objectives

Lean MVP format:

```yaml
objectives:
  - "Prevent direct Belvar intervention."
  - "Maintain commercial shipping."
```

Objectives:

- are private to the Actor by default;
- should be outcome-oriented rather than tactical instructions;
- may conflict with one another;
- may conflict with another Actor's objectives.

MVP+ may support structured objective IDs/weights/anchors.

---

# 10. Private Information

Example:

```yaml
private_information:
  - "Reserve mobilisation will take two turns."
  - "The government believes public support is fragile."
```

Private information is Actor knowledge, not automatically public game truth.

For Lean MVP, the distinction is represented through visibility rather than a formal epistemic graph.

Scenario authors should avoid silently treating a private belief as an authoritative Effect unless that is intended.

---

# 11. Starting Effects

Example public Effect:

```yaml
starting_effects:
  - description: "Commercial shipping continues normally."
    visibility: public
```

Private Effect:

```yaml
  - description: "Arkania's reserve mobilisation plan is incomplete."
    visibility: private
    visible_to:
      - arkania
```

Optional:

```yaml
    expires_turn: 3
```

### Rules

`visibility`:

```text
public
private
```

For Lean MVP:

- `public` → `visible_to` omitted/empty;
- `private` → `visible_to` contains at least one Actor ID.

Starting Effects are part of authoritative game state.

Use them for facts that should influence later adjudication.

An authoritative starting Effect must not contain an unresolved authoring marker. Validation
rejects case-insensitive standalone `TODO`, `TBD`, `FIXME`, any `FYLL_I...` token, and an explicit
`<placeholder>` token. Scenario Resources and explanatory documentation may discuss such markers;
the invariant applies to starting Effect descriptions because they immediately become
authoritative state.

---

# 11.1 Scenario Rules

A scenario may declare rules that the Umpire must consider when it adjudicates (D-090):

```yaml
rules:
  - id: no-overflight
    text: "Belvar cannot authorise foreign military overflight without a parliamentary vote."
  - id: port-authority
    text: "Arkania's port authority may close its own ports to any shipping without further approval."
```

A rule is semantic scenario content, **not executable**. It is `id` + `text` and nothing else.
The server never interprets rule text. A rule executes no code, sets no probability, mutates no
state, creates no Effect and introduces no rules engine. The Umpire weighs rules within the
ordered adjudication gates (agents.md §23). A rule informs authority, prerequisites and
deterministic conclusions; it can never waive a gate.

### Rules

- `rules` is optional. A scenario without it behaves exactly as before.
- At most 32 rules.
- `id`: required, unique within the scenario, matching `^[a-z0-9][a-z0-9_-]{0,63}$`.
- `text`: required, non-empty after trimming, at most 1000 characters.
- `text` must not contain an unresolved authoring marker (the §11 marker rule).
- No other field is accepted. In particular there is no `visibility` or `visible_to`:
  - rules are scenario-wide and **public** in this version;
  - every Actor's activation context carries them;
  - private intelligence belongs in private Resources, private information or private starting
    Effects, never in a hidden rule.

Game creation snapshots the declared rules, in declaration order, onto the Game. That snapshot
is what the Umpire receives (agents.md §21) and what the administrative export records. Rules
are part of `scenario.yaml`, so they are covered by the scenario content hash (§20).

Use rules for what an author would otherwise hide in `scenario.description`:
- constraints and capabilities that must reach every adjudication;
- thresholds and derivations the Umpire applies deterministically.

Keep setting prose in `description`.

---

# 11.2 Scheduled Injects

An inject is an authored fact that enters the game at the start of a later turn. It is the
scenario's way to keep pressure on after the opening problem has been settled (D-095).

```yaml
injects:
  - id: second-cable-fault
    turn: 3
    description: "A second subsea cable, the Norrsund power link, loses capacity at 03:40; its operator reports a fault of unknown cause."
    visibility: public
  - id: drevna-escalation-order
    turn: 3
    description: "Drevna's leadership orders the exercise extended by one week and asks for options to press the fisheries claim."
    visibility: private
    visible_to:
      - drevna
    expires_turn: 5
```

Fields:

- `id`: required, pattern `^[a-z0-9][a-z0-9_-]{0,63}$`, unique among injects.
- `turn`: required, `2 ≤ turn ≤ max_turns`. Facts true from the start are starting Effects
  (§11).
- `description`: required, non-empty, at most 1000 characters, with no unresolved authoring
  marker (§11).
- `visibility`, `visible_to`: as for starting Effects (§11).
- `expires_turn`: optional, and greater than `turn`.

At most 32 injects.

At the start of turn `turn` the server creates the inject's Effect, after expiry and before
Diplomacy opens (state-machine §15.5a). From then on it is an ordinary Effect: Actors see it
according to its visibility, the Umpire can supersede it, it expires normally, and a
consequence pass may cite it if it is public. Before release no Actor can see it. The Game
keeps a snapshot, `scenario_injects`, which the administrative export includes.

Injects are unconditional. The server never checks whether an inject still makes sense, so
write text that stays true whatever the Actors did: a new fault elsewhere, an outside demand,
an order from a capital, a deadline. Do not write text that assumes a particular outcome, such
as "the repaired cable fails again".

---

# 12. Generic Resources

Resources provide optional on-demand scenario information.

Example:

```yaml
resources:
  - id: regional-background
    name: Regional Background
    type: background
    path: resources/background.md
    visibility: public
    tags:
      - history
      - politics
    metadata:
      period: "1990-2026"
```

Private resource:

```yaml
  - id: arkania-intel-estimate
    name: Arkania Intelligence Estimate
    type: intelligence
    path: resources/arkania-intel.md
    visibility: private
    visible_to:
      - arkania
    tags:
      - belvar
      - readiness
```

---

# 13. Resource Schema

Required:

```text
id
name
type
visibility
```

Content source requires one of:

```text
content
path
```

Example inline:

```yaml
- id: short-note
  name: Short Reference
  type: reference
  content: >
    The strait is approximately 40 km wide at its narrowest point.
  visibility: public
```

Optional:

```text
visible_to
tags
metadata
```

### `type`

`type` is an open string.

Examples:

```text
background
map
history
capability
intelligence
dataset
treaty
press_archive
economic_data
technical_reference
```

The engine must not branch on these values for domain behaviour.

### `metadata`

`metadata` is a free-form mapping.

Example:

```yaml
metadata:
  source: "Scenario design team"
  date: "2026-08-01"
  confidence: medium
```

AMAW stores/exposes metadata but does not infer domain meaning from it.

---

# 14. Supported Resource Formats

Required initial support:

```text
.md
.txt
.yaml
.yml
.json
.csv
```

Optional attachment support if trivial:

```text
.pdf
.png
.jpg
.jpeg
.geojson
```

For optional formats, the MVP may expose file metadata/attachment access without semantic parsing.

Not required:

```text
OCR
embedding generation
vector search
PDF semantic indexing
map reasoning
GIS operations
```

---

# 15. Resource Search Behaviour

For small scenarios, search may use:

```text
resource name
tags
plain-text content
```

A simple case-insensitive search is acceptable.

The key requirement is security:

> Filter inaccessible Resources before matching, ranking, snippets or counts.

A private Resource must not influence an unauthorized Actor's search response.

---

# 16. Progressive Disclosure

The Actor starts with:

```text
brief
objectives
private information
current Effects
recent game context
```

Resources remain available on demand through:

```text
search_resources
read_resource
```

Do not inject every scenario Resource into every model prompt.

This preserves context space and makes information-seeking observable.

---

# 17. Scenario Knowledge vs Authoritative State

Scenario authors must distinguish:

### Resource

Information an Actor may inspect.

Example:

```text
"Intelligence estimates Belvar has 18–26 operational fighters."
```

### Effect

Authoritative state used by the game.

Example:

```text
"Belvar's northern airbase is closed."
```

A Resource can contain inaccurate, uncertain or biased material.

An Effect is treated as authoritative unless later deactivated/superseded.

Lean MVP does not implement a full Claims/epistemic system.

---

# 18. Example Scenario Package

```text
northern-strait/
│
├── scenario.yaml
└── resources/
    ├── background.md
    ├── diplomatic-history.md
    ├── trade.csv
    ├── arkania-intel.md
    ├── belvar-intel.md
    └── regional-map.png
```

Example `scenario.yaml`:

```yaml
scenario:
  id: northern-strait
  version: 1.0.0
  name: Northern Strait Crisis
  description: >
    Three fictional Actors compete for influence over a disputed
    maritime region while attempting to avoid uncontrolled escalation.

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
      A large regional power bordering the Northern Strait.
    private_brief: >
      You are the political leadership of Arkania. You seek greater
      regional influence without triggering a direct war with Belvar.
    objectives:
      - "Increase influence over access to the Northern Strait."
      - "Prevent direct Belvar military intervention."
      - "Preserve relations with Cestia."
    private_information:
      - "Reserve mobilisation requires two turns."

  belvar:
    name: Belvar
    public_description: >
      A distant but powerful state with regional security commitments.
    private_brief: >
      You are the political leadership of Belvar.
    objectives:
      - "Prevent unilateral Arkanian control of the Strait."
      - "Maintain credibility with regional partners."
    private_information:
      - "Domestic support for a prolonged deployment is limited."

  cestia:
    name: Cestia
    public_description: >
      A smaller regional state economically linked to both powers.
    private_brief: >
      You are the government of Cestia.
    objectives:
      - "Preserve sovereignty."
      - "Avoid becoming the battlefield of a major-power confrontation."
      - "Protect commercial access."
    private_information:
      - "The coalition government is politically fragile."

starting_effects:
  - description: "Commercial shipping continues normally."
    visibility: public

  - description: "Regional political tensions are elevated."
    visibility: public

resources:
  - id: background
    name: Regional Background
    type: background
    path: resources/background.md
    visibility: public
    tags: [history, politics]

  - id: trade
    name: Regional Trade Data
    type: dataset
    path: resources/trade.csv
    visibility: public
    tags: [trade, economy]

  - id: arkania-intel
    name: Arkanian Intelligence Estimate
    type: intelligence
    path: resources/arkania-intel.md
    visibility: private
    visible_to: [arkania]
    tags: [intelligence, belvar]

  - id: belvar-intel
    name: Belvar Intelligence Estimate
    type: intelligence
    path: resources/belvar-intel.md
    visibility: private
    visible_to: [belvar]
    tags: [intelligence, arkania]

  - id: regional-map
    name: Regional Overview Map
    type: map
    path: resources/regional-map.png
    visibility: public
    tags: [map, geography]
```

---

# 19. Scenario Validation Errors

Reject a scenario before creating a game if:

```text
scenario ID missing
version missing
max_turns invalid
turn_order empty
unknown Actor in turn_order
duplicate Actor in turn_order
Actor has no objectives
fewer than two Actors (every Argument needs an eligible opponent, state-machine §18) — D-069
primary_arguments_per_actor outside 1–3 — D-069, D-094
private Resource has no visible_to Actors
visible_to references unknown Actor
Resource ID duplicated
Resource path missing
starting Effect has invalid visibility
rule id missing, malformed or duplicated — D-090
rule text empty, over 1000 characters or containing an unresolved marker — D-090
more than 32 rules, or a rule with any field other than id and text — D-090
consequence_mode other than none or bounded_effects_v1 — D-091
diplomacy_mode / con_mode outside their values, a count without its mode, a mode without its
  count, or con_challengers_per_argument above the opponents per Argument — D-099
inject id missing, malformed or duplicated; more than 32 injects — D-095
inject turn below 2 or above max_turns; expires_turn not after turn — D-095
inject description empty, over 1000 characters or containing an unresolved marker — D-095
inject visibility rules violated (as for starting Effects) — D-095
```

Warnings rather than hard failures may be used for:

```text
very long Actor brief
very large text Resource
Actor not present in turn_order
zero public starting Effects
```

provided behaviour is documented.

---

# 20. Benchmark-Safe Scenario Versioning

When comparing runs, record:

```text
scenario.id
scenario.version
```

Increment/change version when modifying benchmark-relevant content such as:

```text
Actor brief
objective
private information
starting Effect
scenario rule
scheduled inject
Resource contents
turn order
turn count
Argument budget
Con limit
consequence mode
Diplomacy or Con condition default
```

Do not compare runs under the same declared version if the scenario contents differ materially.

Any change to `scenario.yaml` or to a referenced Resource's bytes is a new version, even when it
looks cosmetic: the sealed hashes change, and runs are pooled only when id, version and hashes
all agree. The version signals the size of the change:

```text
MAJOR   redesign: Actors, objectives, or what the game is about
MINOR   tuning: briefs, rules, injects, Resources, declared conditions, budgets
PATCH   text fixes that do not change what any Actor or the Umpire can know
```

A repository's scenario library records every released version and its hashes in
`scenarios/versions.lock`, and a package's `CHANGELOG.md` says what each version changed and
why. A library test fails when a package's content no longer matches the hashes locked for its
declared version, so content cannot change without a bump. Earlier versions are recovered
from version control (tag `scenario/<id>/<version>`), not by keeping copies of the package.
Files the loader does not hash, such as `README.md`, `AUTHOR_NOTES.md` and `CHANGELOG.md`, may
change without a bump.

For Lean MVP benchmark provenance, game creation must compute SHA-256 for `scenario.yaml` and every referenced Resource file and persist the resulting manifest with the run. The scenario author does not manually supply these hashes in YAML.

---

# 21. Scenario Authoring Guidance

A useful scenario generally has:

- meaningful goal conflict;
- imperfect information;
- more than one plausible strategy;
- incentives for communication;
- uncertainty that requires adjudication;
- consequences that can persist;
- no trivially dominant first move.

For an AI benchmark, scenario authors should also ask:

```text
Does the scenario reward information seeking?
Can agents form different strategies?
Can role advantage be controlled through rotation?
Are objectives measurable enough for later evaluation?
Is one Actor structurally doomed or dominant?
```

---

# 22. Out of Scope for Lean Scenario Format

Deferred:

```text
organisation hierarchy schema
conversation/channel topology
external MCP tool declarations
tool permissions
event-driven activation rules
authority/approval rules (a server-enforced authority model; semantic `rules` text for the Umpire is §11.1)
Secret Argument triggers
formal Claims/epistemic graph
scenario-defined custom schemas
scenario-defined termination-condition rules
scenario-specific communication restrictions beyond baseline public/private messaging
custom diplomacy close/reactivation policies
vector indexes
large document ingestion pipelines
```

These may be added without changing the core principle that scenario semantics remain generic.
