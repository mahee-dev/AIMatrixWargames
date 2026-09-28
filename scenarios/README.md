# Scenarios

A scenario is a directory with a `scenario.yaml` manifest and a `resources/` folder; the
schema is `docs/scenario-format.md` and the authoring walkthrough with a starter manifest is
`guide/scenario-authoring.md`. Run one by directory name:

```bash
uv run amaw run orison-crisis
```

Each package has a `CHANGELOG.md`, and [`versions.lock`](versions.lock) records the hashes of
every released version. Change a scenario only by bumping its version (see "Versions" in the
authoring guide); the offline suite fails on content that no longer matches its version.

## Start here: `orison-crisis`

Three Actors, six turns, a fictional superpower missile crisis with the names, geography and
capabilities changed so the models must reason from the scenario's evidence rather than
recite history. It is the polished introductory example — the one `README.md` runs — with a
`README.md`, `AUTHOR_NOTES.md` and `PACKAGE_CONTENTS.md` of its own that show what a complete
package looks like.

## `northern-strait`

Three Actors, six turns, a contest over a strategic waterway in which no single forum can
satisfy every Actor. The smallest package (one manifest, three Resources) and the one the
provider-backed smoke tests and most recorded runs use. See its `README.md`.

## `swedish-government`

Eight Actors, four turns, in Swedish: forming a government after the 2026 Riksdag election,
frozen at the real state of play on 25 September 2026 (final seats, a caretaker government,
Andersson's exploratory assignment), with six scenario rules and, since 7.1.0, bounded Diplomacy. The large, slow, stress example — eight parallel
Diplomacy activations, a real election-result CSV as a Resource, and the scenario behind the
unattended-launcher guide (`guide/operations.md`). Not a forecast.

## `skaro-crisis`

Six Actors, four turns, a fictional total-defence crisis: the cable to an island county has
failed, a neighbour is exercising nearby, and a government, armed forces, civil-contingencies
agency, county administration and an allied partner must share authority. It is the Matrix
Scenario Portability acceptance scenario. It declares first-class `rules`, scheduled
`injects`, `consequence_mode: bounded_effects_v1` and, since 1.2.0, bounded Diplomacy and
sampled Cons as its default condition. Its consequence model defaults to the Umpire's. It runs
by name from the checkout and is not bundled into the wheel. See its `README.md`.

## `ai-2027`

Six Actors, four one-week turns, forking from the published
[AI 2027](https://ai-2027.com/) scenario at its October 2027 branch point: OpenBrain's
Oversight Committee must decide whether to keep using Agent-4, which its own safety team
suspects of working against the company. The Actors are the safety team, OpenBrain
leadership, the White House, Agent-4 itself (which knows its hidden goal), China and the EU.
Nine authority-gate rules, nine injects, bounded Diplomacy and sampled Cons. The published
endings are author-only. Runs by name from the checkout (`uv run amaw run ai-2027`)
and is not bundled. Not a forecast. See its `README.md`.

**Held back from the public repository** until the authors of AI 2027 have agreed to its
publication, together with the adjudication corpora built from its runs.

## `archive/`

Superseded packages kept so that closed briefs and regression tests that cite them stay
readable. Nothing there is a supported starting point; its `README.md` says what each one was.

## Writing your own

Copy `orison-crisis`, change the `scenario.id`, and run it; there is no separate validate-only
command — `amaw run` validates the package before it creates a Game and refuses a bad one
without touching the database:

```bash
uv run amaw run <your-scenario>
```

Every Resource declares its visibility (`public`, or `private` to named Actors) in the
manifest; the server filters before search, so an Actor never learns that hidden material
exists. Private objectives and briefs go in the manifest, not in Resources. The authoring
guide covers validation errors, Effects and the visibility rules in detail, and its
"Design lessons from playtests" section covers what makes a scenario play well.
