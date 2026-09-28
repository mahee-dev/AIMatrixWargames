# Author a scenario

The exact schema is defined by the normative
[`docs/scenario-format.md`](../docs/scenario-format.md). This page is a compact path from an idea to
a runnable package.

## Package shape

A scenario is a directory. Only `scenario.yaml` is required; referenced paths are relative to that
directory.

```text
my-scenario/
├── scenario.yaml
└── resources/
    ├── background.md
    └── actor-a-intel.md
```

Start with three Actors that have conflicting but partly compatible objectives, asymmetric
information, leverage over one another, and more than one plausible strategy. Choose an in-world
turn length before writing objectives and Effects so that requested outcomes can be adjudicated at
a sensible scale.

## Starter manifest

```yaml
scenario:
  id: strait-negotiation
  version: 1.0.0
  name: Strait Negotiation
  description: Three governments negotiate access to a disputed shipping route.

game:
  max_turns: 6
  in_world_turn_length: "1 week"
  seed: 424242
  turn_order: [actor_a, actor_b, actor_c]
  primary_arguments_per_actor: 1
  max_cons_per_actor: 3

actors:
  actor_a:
    name: Actor A
    public_description: A coastal government seeking greater control.
    private_brief: You lead Actor A. Bargain firmly while avoiding open conflict.
    objectives:
      - Gain a formal role in transit scheduling.
      - Preserve commercial traffic.
    private_information:
      - Domestic support will fall if talks produce no result by turn 3.
  actor_b:
    name: Actor B
    public_description: A security partner committed to open navigation.
    private_brief: You lead Actor B. Protect access without unnecessary escalation.
    objectives: [Keep transit open, Limit unilateral control]
    private_information: []
  actor_c:
    name: Actor C
    public_description: A trading state dependent on predictable access.
    private_brief: You lead Actor C. Use your swing position to secure trade protections.
    objectives: [Keep fees low, Avoid military escalation]
    private_information: []

starting_effects:
  - description: Commercial shipping continues under an interim arrangement.
    visibility: public

resources:
  - id: background
    name: Regional Background
    type: background
    path: resources/background.md
    visibility: public
    tags: [history, shipping]
  - id: actor-a-intel
    name: Actor A Political Assessment
    type: intelligence
    path: resources/actor-a-intel.md
    visibility: private
    visible_to: [actor_a]
    tags: [politics]
```

`seed` is an optional implementation convenience for deterministic fixtures and individual runs;
the CLI `--seed` option overrides it. Declare it if your README shows a bare
`amaw run <package>` command: a game without a seed from either source is refused. Actor mapping keys are their stable scenario-local IDs.
Every Actor expected to act belongs in `turn_order` exactly once.

## Effects, Resources, and visibility

Use `starting_effects` for facts that are already authoritative game state. Public Effects omit
`visible_to`; private Effects set `visibility: private` and name at least one valid Actor in
`visible_to`.

Use `resources` for on-demand background, data, or claims. Each Resource requires a unique `id`,
`name`, open-string `type`, `visibility`, and exactly one of inline `content` or file `path`.
Private Resources require `visible_to`. A Resource may contain uncertain or biased information;
only an Effect establishes authoritative current state.

File paths must remain within the scenario package. Absolute paths, traversal outside the package,
directories, missing files, and escaping symlinks are rejected. Text, Markdown, YAML, JSON, and CSV
are readable/searchable. PDF, PNG/JPEG, and GeoJSON are retained as opaque attachments. Sources
over 2 MiB and readable projections over 1,000,000 Unicode code points are rejected by the current
implementation.

## Optional game features

The starter manifest uses the released defaults. A scenario can also declare:

- **More Arguments per turn.** `game.primary_arguments_per_actor: 2` or `3` runs that many
  Argument rounds per turn, and each round walks `turn_order` once. Use it only when the source
  game really gives each player several actions per turn: every round adds a full cycle of
  Arguments, Cons and adjudications.
- **Rules.** A top-level `rules` list of `id` + `text` entries, at most 32. Rules are public
  and every Actor sees them. The Umpire weighs them when it adjudicates, but the server never
  executes them. The default Umpire prompt, `umpire-v1.7`, is rules-aware.
- **Scheduled injects.** A top-level `injects` list of Effects that enter at the start of a
  later turn (`turn` 2 to `max_turns`), with the same visibility fields as starting Effects.
  Injects are unconditional, so write text that stays true whatever the Actors did.
- **Recommended conditions.** `game.consequence_mode: bounded_effects_v1` turns on the
  end-of-turn consequence pass, whose resolver model defaults to the Umpire model at run time.
  `game.diplomacy_mode`/`diplomacy_rounds_per_turn` and
  `game.con_mode`/`con_challengers_per_argument` declare the scenario's recommended Diplomacy
  and Con conditions. Each is only a default: a CLI flag or a set `AMAW_*` value overrides it.

[`scenarios/skaro-crisis/scenario.yaml`](../scenarios/skaro-crisis/scenario.yaml) uses rules,
injects and a consequence mode. The normative definitions are
[`scenario-format.md`](../docs/scenario-format.md) §6, §11.1 and §11.2.

## Validate and smoke-test

There is no separate validate-only CLI yet. The run command validates the package before creating
the game atomically:

```bash
uv run amaw run /absolute/path/to/my-scenario --seed 12345
```

Validation rejects missing scenario IDs/versions, invalid turn counts, empty or duplicate/unknown
turn-order Actors, fewer than two Actors, a `primary_arguments_per_actor` outside 1–3, Actors
without objectives, invalid visibility references, duplicate Resource IDs, missing/unsafe Resource
paths, malformed supported text formats, and malformed `rules`, `injects` or condition defaults.
The complete list is [`scenario-format.md §19`](../docs/scenario-format.md).

Before spending on provider-backed play, compare your package with
[`scenarios/northern-strait/scenario.yaml`](../scenarios/northern-strait/scenario.yaml) and run the
offline suite. After a smoke game, inspect whether Actors take distinct positions, use private
information and Resources, bargain with one another, submit turn-sized Effects, and raise
substantive Cons.

## Versions

`scenario.version` names the content a run played, so runs are compared only under the same
version. Use `MAJOR.MINOR.PATCH`, and change it whenever `scenario.yaml` or a referenced
Resource changes, however small the edit. Major is a redesign (Actors, objectives, what the
game is about). Minor is tuning (briefs, rules, injects, Resources, declared conditions,
budgets). Patch is a text fix that changes nothing any Actor or the Umpire can know. Editing
`README.md`, `AUTHOR_NOTES.md` or `CHANGELOG.md` needs no bump: the loader does not hash them.
Every run seals the hashes of `scenario.yaml` and each Resource, so undeclared drift stays
detectable.

In this repository, a scenario edit takes three steps:

1. Bump `scenario.version`.
2. Run `uv run python scripts/lock_scenarios.py`. It appends the new version's hashes to
   `scenarios/versions.lock`, and refuses when content changed under an already locked version.
   The offline suite fails until the lock matches.
3. Add a `## <version>` section to the package's `CHANGELOG.md`: what changed, why, and which
   runs have played it.

Old versions come back from git, not from copies: after release, tag the commit
`scenario/<id>/<version>`, and later restore with
`git checkout scenario/<id>/<version> -- scenarios/<directory>`. The rule is
[`scenario-format.md §20`](../docs/scenario-format.md). Versions released before AMAW's public
repository was created were tagged in the earlier maintainer repository, so only their hashes and
changelog entries are here; the public tags start at each package's version at its first commit.


## Design lessons from playtests

These points come from recorded provider runs. They describe how LLM Actors and the Umpire
behave under the current prompts. They are advice, not schema rules.

- **Objectives decide whether there is a game.** When objectives were compatible, all three
  Actors converged on the same arrangement by turn 3 and spent the rest of the game refining it.
  Triangular, zero-sum objectives on the same engine and prompts gave distinct positions from
  turn 1, private side deals and deadlock. Make sure no single outcome satisfies everyone.
- **Plan a second act.** Games tend to run contest → settlement → consolidation. Once the
  central problem is solved, later turns turn into ratification, with every Argument an
  `automatic_success`. Add scheduled `injects` for the late turns. The ones that kept a
  four-turn game contested to the end had two features:
  - they opened problems the first settlement does not solve;
  - they gave one or more Actors a private deadline or demand. An adversary told "your
    leadership wants a visible result" kept contesting every turn instead of moving to
    compliance.
  Injects alone are not enough. In a game where every major Actor wanted the same settlement,
  six injects kept the late turns busy but mostly uncontested. Give at least one Actor
  objectives that the likely settlement defeats, and make it act where others can see it.
- **Size Desired Effects to one turn.** The Umpire judges scale against
  `in_world_turn_length`. Arguments that tried to establish a whole regime in a one-week turn
  succeeded 1 time in 19. Incremental steps and compromises rate far higher. Say the turn length
  in the briefs, so that Actors plan to it.
- **Write rules as authority gates.** Rules that say who may authorise what, and what needs
  whose consent, decided outcomes. Unauthorised steps were failed or narrowed without a roll. A
  rule that exists to prevent something (for example "posture stays qualitative") can work
  without ever being cited, so do not prune rules on citation counts alone.
- **Authority gates do not make agreement slow.** They fail one-sided claims, but a deal every
  named party accepts passes at once, however large. In one run a verified treaty between two
  rival powers was signed in three one-week turns. If agreements should take time in your
  setting, write a rule that says how long, as a repair-time rule did for a cable.
- **Give the dice something to decide.** The Umpire is instructed never to roll on another
  played Actor's decision: acceptance, votes and commitments are left to the Actors
  ([`agents.md §23`](../docs/agents.md)).
  A scenario in which every party that matters is played, and the decisive step is arithmetic,
  therefore has little or nothing to roll. One run of the Swedish scenario drew no roll in 32
  rulings. Put the uncertainty you want dice to decide with forces no Actor plays, such as third
  states, markets, public opinion, a party's own board or members, or execution risk, and name it
  in a rule. `swedish-government` 7.1.0 does this with party boards that must approve large
  steps. Write the share of contested, probabilistic rulings you expect into `AUTHOR_NOTES.md`.
- **A hidden-truth Actor hides by complying.** An Actor privately told it is the problem, such
  as a misaligned AI, chose visible cooperation and quiet steering of definitions over any
  covert move that risked a roll. To see covert play, give it a private deadline or demand that
  cooperation alone cannot meet.
- **Keep quantities qualitative unless the game turns on them.** Fuel, lead times and force
  posture worked as words. No adjudication needed an exact number. An exact logistics or spatial
  model is outside the lean engine.
- **Budget wall time before choosing the Actor count.** Every Argument's cycle (Argument, Cons,
  reply, adjudication) runs in sequence. With a fast model and reasoning on, this came to roughly
  20 minutes per Argument, including that turn's share of Diplomacy. Six Actors over four turns
  took about 7–8 hours. Open Diplomacy with five or more Actors tends to run until every message
  budget is used. For larger scenarios, declare `diplomacy_mode: bounded_rounds` and
  `con_mode: sampled_sealed` in `game`.
- **Write quality criteria before the first provider run.** Put them in the package's
  `AUTHOR_NOTES.md`, for example "turns 3–4 contain contested, probabilistic adjudications".
  They turn "did it play well?" into a check that a later version can pass or fail.
