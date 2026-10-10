# AMAW — AI Matrix Wargames

<!-- SPDX-License-Identifier: MIT -->

AMAW lets language-model agents play **Matrix Games** against each other, and records every
game so that repeated runs can be compared. It is built to become a benchmark for models and
agent designs on negotiation and strategic reasoning.

A Matrix Game is a free-form wargame. On their turn, players say what their side does, why it
should work, and what should follow. The other players argue why it won't. An umpire weighs the
arguments and says how likely the action is to succeed, and a dice roll decides. There are no
unit counters or combat tables: the outcome depends on how good the argument is.

In AMAW every player (an **Actor**) is an LLM agent with its own private brief, objectives and
documents to search. The umpire is a fixed LLM. The **server, not any model, owns the rules,
who-sees-what, the dice and the game state**, so a model can argue for an outcome but never
decide one. Every game is exported as a complete machine-readable record, and seeded batches
rotate models across roles so that runs can be compared.

The umpire only rules on what an Actor can actually bring about. An Actor can bind itself, but
it cannot decide for another Actor: whether another player accepts a deal is that player's move,
never a dice roll. The dice are for what no player controls, such as markets, third parties or
whether a plan works in practice.

**Status:** version 0.1.0, pre-release. It plays complete games end to end and runs benchmark
batches. It is not on PyPI, and interfaces may still change. See
[current limitations](guide/operations.md#current-limitations).

## What happens in a game

Each turn has a Diplomacy phase and then one Argument from each Actor, in turn order:

1. **Diplomacy.** All Actors act at once. They read what they are allowed to see, search and read
   scenario documents, send public or private Messages, keep private notes, and declare
   themselves `ready`.
2. **Argument.** The active Actor states an action, the reasons it should work, and the result
   it wants.
3. **Cons.** Each other Actor may submit a counter-argument or pass. By default only the umpire
   sees them.
4. **Adjudication.** The umpire either declares an automatic success or failure, or gives a
   probability and writes out what happens on success and on failure. The server rolls and
   records the winning branch as **Effects**: facts that are now true in every Actor's world.

After the scenario's last turn (six in `orison-crisis` and `northern-strait`) the game is
complete. Scenarios can add
more Arguments per turn, scheduled events and scenario rules, and runs can switch on
experimental conditions. The [operational guide](guide/operations.md#run-a-game) covers these.

### An example exchange

This is abridged from turns 1 and 2 of a retained game of the bundled `northern-strait`
scenario, played with DeepSeek v4 Flash under the default umpire prompt, `umpire-v1.7`:

> **Arkania argues:** it formally offers Belvar a shared Strait Transit Council (decisions by
> consensus, a neutral secretariat, Arkania handling day-to-day administration) if Belvar stands
> its task group down. It wants Belvar to accept and Cestia to back the charter.
>
> **Belvar's Con:** Arkania keeping "administrative execution" is the single-state lead Belvar
> objects to under a new name, and standing down before the council's neutrality is proven gives
> away Belvar's only leverage.
>
> **Umpire:** automatic success, but narrowed. Arkania can make the offer; whether Belvar accepts
> and Cestia supports it is their decision, not the dice's. The Effect records only Arkania's
> formal offer.
>
> **A turn later, Belvar argues:** it signs the charter and asks the jointly agreed independent
> verification mission to certify that the secretariat really holds scheduling and fee
> authority. **Umpire:** probabilistic, **p = 0.65**. The signature stands in both branches;
> the roll decides only what the mission finds. **Server:** rolls 0.504, which succeeds: the
> council is certified neutral, and Belvar commits to a time-bound stand-down.

## Quick start

You need Python 3.12 or later, [uv](https://docs.astral.sh/uv/), and an
[OpenRouter](https://openrouter.ai/) API key.

```bash
git clone https://github.com/mahee-dev/AIMatrixWargames.git
cd AIMatrixWargames
uv sync --extra agents
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env`, then play the three-Actor Orison Crisis scenario:

```bash
uv run amaw run orison-crisis
```

By default every Actor and the umpire use `deepseek/deepseek-v4-flash-0731`. Change them with
`--player-model` and `--umpire-model`, or in `.env`.

**Expect a long run.** In retained three-Actor, six-turn games with DeepSeek v4 Flash, a game
took 70 to 120 minutes and about 3 million input and 0.4 million output tokens. What that costs
depends on the models you choose.

Every scenario in the checkout runs by name: `northern-strait`, `orison-crisis`,
`swedish-government` and `skaro-crisis` (see [`scenarios/`](scenarios/README.md)). An unknown
name lists the ones that work. A fifth scenario, `ai-2027`, is held back from the public
repository until the authors of AI 2027 have agreed to its publication.

To follow the game as it plays, add `--watch`: the terminal shows a live dashboard (turn and
phase, each Actor, the last ruling with the Umpire's probability and the server's roll, and every
Message, private ones included) while logs go to `data/logs/`. From another terminal,
`uv run amaw watch GAME_ID` attaches to a running game or replays a finished one (`--speed`,
`--step`); quitting the viewer never stops the run.

To follow games from a browser, run the relay ([`amaw-relay`](https://github.com/mahee-dev/amaw-relay),
FastAPI and PostgreSQL on your own server) and set `AMAW_RELAY_URL` and `AMAW_RELAY_TOKEN` in `.env`.
Then `--publish` pushes the game to the relay as it plays, and `uv run amaw publish GAME_ID`
backfills a finished game or follows a running one. Published games include every Actor's
private material, as the export does. A relay that is down never affects the run.

When it finishes, the command prints the game ID and writes the full record to
`data/exports/GAME_ID.json`, beside the database:

```bash
jq '.run_summary' data/exports/GAME_ID.json
uv run amaw export GAME_ID > game.json   # export again from the database at any time
```

The export is an administrative record and includes every Actor's objectives, private Messages and
notes, so handle it accordingly. To read a finished game as a turn-by-turn after-action report,
run `uv run amaw report GAME_ID` (or pass an export file); it writes
`data/exports/GAME_ID-aar/report.md`, which includes the same private material. Add `--card` for five
shareable story cards (`card-1.png` … `card-5.png` and a 25-second `card.mp4`, sized for X) after a
one-time `uv sync --extra share && uv run playwright install chromium`; add `--narrate` to have a
model write the story, checked against the export (`narration.json` can be edited and re-rendered).
Every card is labelled as an AI simulation. If a run is interrupted, `uv run amaw run --resume-game GAME_ID`
continues the same game (see [resuming a game](guide/operations.md#run-a-game)). To install
from a built wheel instead of a checkout, see
[installation](guide/operations.md#requirements-and-installation).

## Benchmarks

No comparative model results are published yet. The tooling to produce them ships:
`amaw benchmark` runs a batch: every model-to-role permutation, several seeded repetitions of
each, failed games kept rather than silently re-run, and raw CSV tables written at the end:

```bash
uv run amaw benchmark orison-crisis --benchmark-mode model \
  --models provider/a,provider/b,provider/c --rotate --repetitions 5 \
  --output-dir runs/my-batch
```

`--benchmark-mode` is required: `model` when the player model is what varies, `agent` when an
agent configuration is compared. The experimental design is in
[`docs/benchmark-methodology.md`](docs/benchmark-methodology.md). See
[Benchmarks](guide/operations.md#benchmarks) for what gets recorded.

## Guides

- [Field Manual](guide/manual.html): an illustrated tour of the rules of play, adjudication,
  settings and benchmark method (open it in a browser).
- [Operational guide](guide/README.md): configuration, running games, model selection, exports
  and limitations.
- [Scenario authoring](guide/scenario-authoring.md): package structure, validation, documents,
  visibility and a starter manifest.

## Repository map

| Path | What it is |
|---|---|
| `app/` | The FastAPI core: engine, storage, visibility, export. Never imports an agent framework. [`app/README.md`](app/README.md) is the module map. |
| `agents/` | The reference PydanticAI player, the umpire adapter and the autonomous runner. |
| `benchmark/` | Batch scheduling, sealed conditions and CSV outputs. |
| `observer/` | The read-only observer feed, the `amaw watch` terminal viewer and the `amaw publish` relay publisher (TRD §41.1). |
| `scenarios/` | Scenario packages; [`scenarios/README.md`](scenarios/README.md) says which to start with. |
| `amaw_resources/` | The prompts and supported scenarios packaged into the wheel. |
| `docs/` | The normative contracts (API, state machine, scenario format, agents, testing, benchmark methodology) and every versioned prompt. If a guide and a contract disagree, the contract wins. |
| `guide/` | How to run, configure, export and author. |
| `tests/` | The acceptance suite, keyed to the test IDs in `docs/testing.md`. Runs without an API key. |

## Development checks

```bash
uv sync
uv run pytest            # offline; no API key needed
uv run pytest -m llm     # provider-backed tests; requires OPENROUTER_API_KEY
```

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). [`SECURITY.md`](SECURITY.md) explains what exports
contain and how to report a vulnerability.

## Licence

MIT. See [`LICENSE`](LICENSE).
