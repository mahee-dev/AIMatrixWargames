# Install, configure, run, and export AMAW

## Requirements and installation

AMAW requires Python 3.12 or later and [uv](https://docs.astral.sh/uv/). From a source checkout:

```bash
uv sync --extra agents
uv run amaw --help
```

The `agents` extra installs the PydanticAI reference-player dependency required by autonomous
games. Core engine and export work can be installed with `uv sync` alone. The normal test suite is
offline and does not require provider credentials:

```bash
uv run pytest
```

AMAW is not currently published on PyPI. A locally built or downloaded wheel can be installed
without retaining the source checkout; it includes the versioned runtime prompts and the three
supported bundled scenarios (`orison-crisis`, `northern-strait`, and
`swedish-government`):

```bash
uv build
uv venv /tmp/amaw-wheel-env
uv pip install --python /tmp/amaw-wheel-env/bin/python 'dist/amaw-0.1.0-py3-none-any.whl[agents]'
cd /tmp
/tmp/amaw-wheel-env/bin/amaw --help
/tmp/amaw-wheel-env/bin/amaw run orison-crisis
```

The `agents` extra is required for autonomous `run` and `benchmark` commands. Installed-wheel
execution resolves bundled names from package data; an explicit custom scenario directory still
takes priority and is loaded directly. A name that is not bundled resolves to `scenarios/<name>/`
under the working directory, so from a checkout every scenario runs by name, including
`skaro-crisis`. An unknown name is refused with the list of names that work.

## Configuration

Copy the example and edit the local file; `.env` is ignored by Git.

```bash
cp .env.example .env
```

| Variable | Required | Default / purpose |
|---|---:|---|
| `OPENROUTER_API_KEY` | For autonomous runs | No default. Provider credential used by Players and the Umpire. |
| `AMAW_PLAYER_MODEL` | No | `deepseek/deepseek-v4-flash-0731`; OpenRouter identifier used for every Actor unless overridden. |
| `AMAW_UMPIRE_MODEL` | No | `deepseek/deepseek-v4-flash-0731`; fixed adjudicator model unless overridden. |
| `AMAW_ADMIN_TOKEN` | Hosted admin API only | No default; when unset, admin HTTP routes are closed. The local `amaw run` command generates an in-memory token for its own in-process API. |
| `AMAW_DATABASE_PATH` | No | `./data/amaw.db`; SQLite file shared by `run` and `export`. |
| `AMAW_LOG_LEVEL` | No | `INFO`; one of `DEBUG`, `INFO`, `WARNING`, or `ERROR`. |
| `AMAW_LOG_FILE` | No | Unset; optional timestamped, secret-redacted operational log file. |
| `AMAW_REQUEST_TIMEOUT_SECONDS` | No | `60`; timeout for outbound model requests. |
| `AMAW_PLAYER_ACTIVATION_MODE` | No | `single_shot`; how the reference player drives one activation — `single_shot` (one structured decision per activation, research tools still callable) or `tool_loop` (one mutating tool call per model round-trip). |
| `AMAW_PLAYER_TOOL_SURFACE` | No | `broad`; the mutating tools offered under `tool_loop`. `available_only` offers only the tools the server currently lists. No effect under `single_shot`; see `--tool-surface` below. |
| `AMAW_PLAYER_RESEARCH_MODE` | No | `sequential-v1`; `batched-v1` additionally exposes bounded batch search/read tools. This is an agent-configuration treatment. |
| `AMAW_PLAYER_SEARCH_CONTRACT` | No | `search-v2`; the released Resource matching/ranking contract (token coverage). `search-v1` is the original whole-query substring ranking, kept for reproducing runs recorded under it. Independent of the research topology above. |
| `AMAW_PLAYER_RESEARCH_HISTORY_MODE` | No | `full-v1`; `compact-success-v1` removes completed successful research plumbing from later requests while retaining exact returned data. |
| `AMAW_DIPLOMACY_CONCURRENCY` | No | `4`; how many independent diplomacy activations may run in one round. |
| `AMAW_DIPLOMACY_MODE` | No | `open_diplomacy`; released open-ended behaviour. `bounded_rounds` is a distinct experimental game-loop condition. |
| `AMAW_DIPLOMACY_ROUNDS_PER_TURN` | Bounded mode only | Positive completed Diplomacy-round limit; unset for open Diplomacy. |
| `AMAW_CONS_CONCURRENCY` | No | `4`; how many opponents of one Argument are activated at once during Cons. `1` runs them one after another. |
| `AMAW_CON_MODE` | No | `open_cons`; released unrestricted opposition (every opponent may Con). `sampled_sealed` is a distinct experimental condition in which the server seals a fixed number of challengers per Argument. |
| `AMAW_CON_CHALLENGERS_PER_ARGUMENT` | Sampled mode only | Positive challenger count per Argument, at most the number of opponents; unset for open Cons. |
| `AMAW_CON_REPLY_MODE` | No | `sealed_cons`; released behaviour (Cons reach only the Umpire, no owner reply). `visible_reply` is a distinct experimental condition: Cons are revealed to every Actor when they close and the owner replies once before adjudication. |
| `AMAW_REPLY_MAX_POINTS` | Visible-reply mode only | Reply bound, 1–3 points; unset for sealed Cons. |
| `AMAW_UMPIRE_PROMPT_VERSION` | No | Unset: `umpire-v1.7` for every game (rules-aware, reply-aware, Effect discipline and a branch audit so the roll never decides another Actor's choice). `visible_reply` refuses v1.3; a scenario with `rules` refuses v1.3 and v1.4. `umpire-v1.3`–`umpire-v1.6` stay selectable to reproduce earlier runs; a comparison with a run made under an old default must pin that version. |
| `AMAW_CONSEQUENCE_MODE` | No | Unset: the scenario's `game.consequence_mode`, which defaults to `none` (the released loop). `bounded_effects_v1` is a distinct experimental condition: one evidence-linked end-of-turn consequence pass per turn. `amaw benchmark` seals and verifies the resolver on every attempt. |
| `AMAW_CONSEQUENCE_MODEL` | `bounded_effects_v1` only | OpenRouter model for the consequence resolver. Defaults to the Umpire model under `bounded_effects_v1`; refused under `none`. |
| `AMAW_MAX_LLM_ITERATIONS` | No | `12`; per-activation model iteration ceiling. |
| `AMAW_MAX_TOOL_CALLS` | No | `20`; per-activation tool-call ceiling. |
| `AMAW_MAX_RESEARCH_TOOL_CALLS` | No | `4`; optional research-call subset. Research tools are withdrawn when spent, on an exact successful repeat, or when completion capacity must be reserved. |
| `AMAW_MAX_RESEARCH_OPERATIONS` | No | `4`; Resource searches and reads per activation, however many research calls carry them (a batch call counts each operation). |
| `AMAW_MAX_MESSAGES_PER_ACTIVATION` | No | `3`; normal outgoing-Message budget for one activation. |
| `AMAW_MAX_MESSAGES_PER_PHASE` | No | `10`; per-Actor emergency Message ceiling for one phase. |
| `AMAW_MAX_ACTIVATIONS_PER_OPPORTUNITY` | No | `6`; no-progress recovery ceiling for one Actor opportunity. |

A game resolves each experimental condition — the Diplomacy, Con and consequence modes with their
counts — in one order: the CLI flag, then an `AMAW_*` value that is set, then the scenario's
`game.*` default, then the released default
([`scenario-format.md §6`](../docs/scenario-format.md)). A value set in `.env` therefore overrides every scenario's declared condition, which is
why `.env.example` leaves those lines commented out.

Never commit `.env`. Provider keys, admin tokens, and Seat bearer tokens are secrets. AMAW applies
log redaction, but operators should still avoid putting credentials in commands, scenario files,
or exported artefacts.

## Run a game

Pass either a bundled scenario name or a path to a directory containing `scenario.yaml`:

```bash
uv run amaw run orison-crisis
uv run amaw run scenarios/orison-crisis
uv run amaw run /absolute/path/to/my-scenario
```

The local runner validates and snapshots the package, creates the game, starts it, and drives all
Actors plus the Umpire until the game completes or the safety ceiling/failure policy stops it. On
success, stdout contains the game ID. State is durable in `AMAW_DATABASE_PATH`.

Useful one-run overrides are:

```bash
uv run amaw run orison-crisis \
  --player-model provider/player-model \
  --umpire-model provider/umpire-model \
  --seed 12345 \
  --max-cycles 1000 \
  --cons-concurrency 4 \
  --activation-mode single_shot
```

Open Diplomacy remains the default. A fixed-round experiment must be declared explicitly:

```bash
uv run amaw run orison-crisis \
  --diplomacy-mode bounded_rounds \
  --diplomacy-rounds-per-turn 3
```

The bound counts complete simultaneous Diplomacy rounds, not Actor activations. Final-round
Messages remain in the game record, but the explicit condition closes before another Diplomacy
activation. Do not mix open and bounded runs as though they measured the same condition.

Unrestricted Con opposition likewise remains the default. A sampled-opposition experiment must be
declared explicitly:

```bash
uv run amaw run orison-crisis \
  --con-mode sampled_sealed \
  --con-challengers-per-argument 2
```

When an Argument is accepted, the server seals exactly that many challengers from a rotating
seat-order schedule (seeded start, balanced exposure across the game) before any opponent is
activated. Only sealed challengers receive a Con activation or may pass; the phase closes when
they have completed. The Umpire sees the Cons that were actually submitted and is not told the
condition. This is sampled opposition, not unrestricted Matrix opposition; do not compare it with
open runs as though they measured the same condition.

Sealed Cons likewise remain the default: a Con is read only by the Umpire and the Argument owner
never answers it. The visible-rebuttal experiment must be declared explicitly:

```bash
uv run amaw run orison-crisis \
  --con-reply-mode visible_reply \
  --reply-max-points 3
```

Cons are still collected concurrently and sealed from each other. When they close with at least
one Con, the server reveals the frozen set — text and author, in canonical order, never in
completion order — to every Actor and opens a `reply` phase in which only the owner acts, once:
`reply_to_cons` with at most the bound's points, each answering one revealed Con by `con_id`, or
an empty list as the explicit pass. The reply cannot change the Action or Desired Effect; the
Umpire receives it and judges whether it is responsive. Zero Cons skip the phase. The exchange
stays visible to every Actor for the rest of the turn (`revealed_exchanges`). The players run the
`player-v1.12`/`player-v1.13` prompts, which state the visibility before any Con is submitted, and
the Umpire's default, `umpire-v1.7`, is reply-aware. Both treatments default to it, so a
`sealed_cons` vs `visible_reply` comparison shares one Umpire condition unless you pin a
different version; pin the same one on both treatments. Nothing hidden is projected into a Con or reply: a
voluntary disclosure changes no Resource, Effect or brief visibility.

Consequence Management is off by default (`none`): the turn completes as soon as the last
Argument is adjudicated. The bounded end-of-turn pass must be declared, either in the scenario's
`game.consequence_mode` or explicitly. The resolver model defaults to the Umpire model;
`--consequence-model` or `AMAW_CONSEQUENCE_MODEL` chooses another. Either way it is sealed in the
run's recipe:

```bash
uv run amaw run orison-crisis --consequence-mode bounded_effects_v1
```

After the last adjudication of each turn the game enters a `consequences` phase in which no Actor
acts. The resolver runs the `consequence-v1.1` prompt. It receives only public material: this
turn's adjudicated outcomes, the active public Effects, the scenario summary and any declared
rules. It proposes 0–3 public Effects, each citing the outcomes or Effects it follows from. The
server validates every reference and commits all of them or none, and it draws no randomness. An
unusable resolver pauses the game in `consequences`, and resuming with `--resume-game` re-runs
the pass. The export records each pass in `consequence_records`. Treat this as a separate
condition from `none` and never pool the two. `amaw benchmark` takes the same two flags. A batch
under `bounded_effects_v1` seals the resolver's model, prompt and settings in `batch.json`
(`consequence_condition`). Each attempt is verified against them like the Umpire, and
`games.csv` names the condition in `consequence_mode`, `consequence_model` and
`consequence_prompt_version`.

Every run is a **playtest** unless it declares an analysis plan. A run whose report will present
findings must seal a predeclared `analysis-plan-v1` or `-v2` document (research question, hypotheses,
experimental unit, treatment and fixed conditions, outcome measures, estimand, attempts/seed/
rotation design, failure and exclusion rules, analysis method, assurance checks — see
[`benchmark-methodology.md §4.2`](../docs/benchmark-methodology.md); a complete example is
[`docs/analysis-plan-example.yaml`](../docs/analysis-plan-example.yaml)) into its immutable
run manifest before execution. For a benchmark batch, prefer `analysis-plan-v2`
([`docs/analysis-plan-v2-example.yaml`](../docs/analysis-plan-v2-example.yaml)): its `design`
states the scope, seed policy and rotation policy by value, so `amaw benchmark` can check them
(see *Benchmarks*).

```bash
uv run amaw analysis-plan validate docs/analysis-plan-example.yaml
uv run amaw run orison-crisis --analysis-plan docs/analysis-plan-example.yaml --export-path run/export.json
uv run amaw analysis-plan verify docs/analysis-plan-example.yaml run/export.json
```

`validate` prints the plan id and its `sha256`; `run` refuses an invalid plan before any game is
created; `verify` classifies each export as `predeclared`, `post_hoc_plan` or `unplanned` against
the plan and exits non-zero unless every game sealed exactly that plan. A resumed game inherits
its sealed plan; do not pass `--analysis-plan` with `--resume-game`. Metrics that a run records
but the plan does not declare as outcomes are descriptive telemetry, not findings.

For a long unattended run, use a separate run directory and raise only the runtime ceilings. This
keeps the scenario's actual rules (`max_turns`, Argument budgets and Con limits) unchanged while
giving model calls enough time and room to recover. The command below also writes the complete
administrative export on success or best-effort on failure, and streams gameplay/provenance records
to a timestamped redacted log:

```bash
RUN_DIR="runs/swedish-government-overnight"
SEED="$(date +%s)"
mkdir -p "$RUN_DIR"
nohup uv run amaw run scenarios/swedish-government \
  --player-model deepseek/deepseek-v4-flash-0731 \
  --umpire-model deepseek/deepseek-v4-flash-0731 \
  --seed "$SEED" \
  --request-timeout-seconds 300 \
  --max-cycles 10000 \
  --diplomacy-concurrency 8 \
  --cons-concurrency 8 \
  --activation-mode single_shot \
  --player-temperature 0 \
  --player-reasoning-effort low \
  --player-max-tokens 8000 \
  --umpire-temperature 0 \
  --umpire-reasoning-effort low \
  --umpire-max-tokens 16000 \
  --max-llm-iterations 24 \
  --max-tool-calls 40 \
  --max-messages-per-activation 5 \
  --max-messages-per-phase 30 \
  --max-activations-per-opportunity 12 \
  --log-level DEBUG \
  --log-file "$RUN_DIR/run.log" \
  --stream-game-details \
  --export-path "$RUN_DIR/export.json" \
  >"$RUN_DIR/stdout.log" 2>&1 &
echo $! > "$RUN_DIR/pid"
```

The `nohup` form is the ordinary POSIX workflow. In a macOS managed terminal whose process group
is reaped when the terminal task ends, use the shipped one-shot launchd helper instead. The helper
creates the run directory itself, retains the exact `launchd.plist`, persists no credentials, and
sets `RunAtLoad=true` with `KeepAlive=false`, so a failed command cannot silently create a second
Game:

```bash
STAMP="$(date +%Y%m%d-%H%M%S)"
SEED="$(date +%s)"
RUN_DIR="runs/swedish-government-overnight-$STAMP"
LABEL="com.amaw.swedish-government-overnight-$STAMP"
uv run amaw-unattended --run-dir "$RUN_DIR" --label "$LABEL" -- \
  scenarios/swedish-government \
  --player-model deepseek/deepseek-v4-flash-0731 \
  --umpire-model deepseek/deepseek-v4-flash-0731 \
  --seed "$SEED" \
  --request-timeout-seconds 300 \
  --max-cycles 10000 \
  --diplomacy-concurrency 8 \
  --cons-concurrency 8 \
  --activation-mode single_shot \
  --player-temperature 0 \
  --player-reasoning-effort low \
  --player-max-tokens 8000 \
  --umpire-temperature 0 \
  --umpire-reasoning-effort low \
  --umpire-max-tokens 16000 \
  --max-llm-iterations 24 \
  --max-tool-calls 40 \
  --max-messages-per-activation 5 \
  --max-messages-per-phase 30 \
  --max-activations-per-opportunity 12 \
  --log-level DEBUG \
  --log-file "$RUN_DIR/run.log" \
  --stream-game-details \
  --export-path "$RUN_DIR/export.json" \
  --run-class playtest
```

The OpenRouter key must be available through the normal repository `.env`; it is deliberately not
copied into the plist. Inspect with `launchctl print "gui/$(id -u)/$LABEL"` and stop with
`launchctl bootout "gui/$(id -u)/$LABEL"`. Do **not** use `launchctl submit` for a one-game run:
its failure-relaunch behaviour can execute `amaw run` again after a non-zero exit. A completed or
failed one-shot job remains loaded but inactive until it is booted out; it is never restarted.

The log includes timestamps, phase/turn transitions, tool calls, failures/recovery, gameplay
records, event history, resource-access activity and model-call metadata. It deliberately excludes
credentials, hidden chain-of-thought, static private Actor briefs and Resource contents; the
administrative `export.json` is the complete sensitive research record. Inspect the process and
tail the log with `cat "$RUN_DIR/pid"`, `ps -p "$(cat "$RUN_DIR/pid")"`, and `tail -f "$RUN_DIR/run.log"`.

If the process ends after the Game is created, recover its ID from stdout/logs or the partial
export and resume against the same `AMAW_DATABASE_PATH`:

```bash
uv run amaw run --resume-game GAME_ID \
  --log-file "$RUN_DIR/run.log" \
  --export-path "$RUN_DIR/export.json"
```

The original model assignments, prompt versions, sampling settings, tool/context/memory modes,
guardrails, request timeout, concurrency, and coordinator ceiling come only from the immutable run
manifest. Resume therefore rejects all flags that would change those fields. It also requires the
same code identity and current prompt/tool versions; an older manifest without the complete
`autonomous-resume-v1` recipe is refused instead of guessed. The external OpenRouter credential may
be supplied afresh.

Before any player is rebuilt, the admin-only resume path replaces every Seat hash in one
transaction and returns new raw credentials to the new coordinator. Tokens held by the interrupted
process immediately stop authenticating. The coordinator then re-observes the existing Game; it
does not create/start another Game or replay completed actions. Pending incremental-memory delivery,
idempotency records, AgentRun history, RNG position, Effects and the manifest remain unchanged. An
in-flight provider request itself is not recovered: authoritative server state decides whether that
opportunity is still pending.

Use an OpenRouter model identifier. `--player-model` currently applies one model to every Actor;
`--umpire-model` selects the single fixed Umpire. The CLI options override the corresponding
environment settings only for that run. `--seed` overrides the scenario seed and controls
server-side adjudication draws; it does not make hosted model generations deterministic.

`--activation-mode` selects the player harness. In `single_shot` (the default) each activation
asks the model for one structured decision — Messages, a Note and `ready`; the primary Argument;
or Cons/pass — which AMAW validates against the guardrails and carries out in a fixed order; the
model calls the read tools (`read_messages`, `search_resources`, `read_resource`) first only when
it needs information. In `tool_loop` the model calls one mutating tool per model round-trip, as
the first autonomous runs did. The two modes use different immutable prompt versions
(`player-v1.11` / `player-v1.10`; `player-v1.13` / `player-v1.12` under `visible_reply`) and are recorded in the run manifest (`player.activation_mode`) and
in every agent record, so runs made under different harnesses are never mistaken for one
condition. A model benchmark holds the mode constant; only an agent-design comparison varies it.

`--tool-surface` applies to `tool_loop` only, which is an experimental tool-use
condition — `single_shot` is normal play. `broad` (the default) offers the model the whole mutating
vocabulary, so an attempt outside `available_actions` is measured as invalid-tool behaviour;
`available_only` offers, at each activation, only the mutating tools the server currently lists.
The server's legality checks are the same either way. The value is sealed in the run manifest
(`player.tool_surface`), the resume recipe and every agent record; a game cannot be resumed under
the other surface, and a report quoting invalid-tool rates must name both the activation mode
and the tool surface. Under `single_shot` the flag is recorded as given and has no effect.

`--search-contract` selects the Resource matching/ranking contract independently of the research
topology. The released default is `search-v2`; `search-v1` reproduces runs recorded before that
change. The contract is sealed in the run manifest, the resume recipe and every player AgentRun,
so a run always says which ranking it played under — a legacy record that names none was played
under `search-v1` and is never reinterpreted.

`--research-mode` and `--research-history-mode` select two independent research treatments.
`batched-v1` adds `search_resources_batch` and `read_resources_batch` for up to eight independent
queries or already-known Resource ids per call; dependent search→select→read remains sequential.
`compact-success-v1` replaces only completed successful research call/return plumbing in later
provider requests with a canonical message containing the exact returned data. Failures and the
full local transcript remain intact. Both settings and the resolved tool schema are recorded in
the run manifest, resume recipe and player AgentRun records. Keep `sequential-v1` + `full-v1` for
the released baseline and vary these only as a declared agent-configuration experiment.

`--inference-profile PATH` (or `AMAW_INFERENCE_PROFILE`) declares a per-phase inference
budget. The document is `inference-profile-v1` YAML/JSON — a `name`, optional `phases` entries
for `diplomacy`, `argument` and `cons`, and an optional `umpire` entry, each carrying
`reasoning_effort` (`none`…`xhigh`) and/or `max_tokens`. Anything a phase leaves unset inherits the
flat `--player-reasoning-effort`/`--player-max-tokens` (or `--umpire-*`) value, so without a
profile every request is exactly the released `uniform` behaviour. The *resolved* per-phase table
is sealed into the run manifest (`player.inference_profile`) and resume recipe, and every player
agent record carries the phase-resolved `reasoning_configuration`, `max_tokens` and profile name
beside the actual `reasoning_tokens` it consumed. An invalid document fails before any Game
exists; `--resume-game` reconstructs the sealed profile and refuses the flag. There are no
built-in `fast`/`quality` presets — write the budgets down per experiment
(`guide/inference-profile-example.yaml`). Equal configured effort is not equal purchased
attention across models: report actual usage, and treat a profile change as a new
agent-configuration (and, through its `umpire` entry, Umpire) condition, never as a model
comparison.

`--cons-concurrency` bounds how many Actors challenge the current Argument at the same time. Cons
are read only by the Umpire — no Actor ever sees another Actor's Con — so this changes wall-clock
time, not what any agent decides; the Umpire always receives the Cons in Actor-id order. Diplomacy
uses isolated rounds with a bounded pool; each Actor's context is captured before that round's
workers start. Both values are recorded in the run manifest (`coordinator.*_concurrency`).

When comparing player models manually, hold the scenario content/version, Player prompt and
toolset, Umpire model/prompt/settings, seed policy, and runtime configuration fixed. Record actual
returned models as well as requested models. AMAW results describe synthetic agents under those
conditions; they are not predictions about real organisations.

## Export and inspect a game

`amaw run` writes each game's export when it ends, or a best-effort partial export when it
fails: to `--export-path` if given, otherwise to `exports/GAME_ID.json` beside the database
(`data/exports/` by default). It names the file on stderr; stdout stays the bare game ID. To
export again from the SQLite database configured by `AMAW_DATABASE_PATH`:

```bash
uv run amaw export GAME_ID > game.json
```

The JSON has `export_format_version: "16"` and includes the identity of the database it was read
from (`database.schema_version`, `database.foreign_keys_enforced`), the authoritative game
configuration and state (including the snapshotted scenario rules, `game.scenario_rules`, and scheduled injects, `game.scenario_injects`), Actors, Messages, Arguments, Cons, owner replies (`argument_replies`, empty for sealed
games), adjudications and RNG rolls, Effects (including inactive
history, each with its origin: `source_argument_id`, `source_consequence_turn` or `source_inject_id`), the
Consequence condition (`game.consequence_mode`) and one `consequence_records` entry per
committed end-of-turn consequence pass (empty unless the game ran `bounded_effects_v1`), Events, Notes, Resource-access records, scenario/Resource hashes, the immutable run
manifest, model assignments, per-execution agent records/errors, and a derived run summary. A
failed or interrupted run may still have useful durable state and provenance if its game ID was
created.

Each AgentRun and the derived summary retain inclusive `input_tokens` and `output_tokens`, plus
their `reasoning_tokens`, `cache_read_tokens`, and `cache_write_tokens` subsets. Do not add those
subsets to the parent totals. Player provider metadata also retains the per-request usage records,
so one logical activation can be reconciled request by request even when framework validation or
AMAW's bounded retry prevents completion.

Player AgentRuns keep `invalid_tool_calls` as the compatible aggregate and, for newly recorded
activations, add `invalid_tool_call_breakdown`. Its versioned, content-free event ledger separates
callable-tool failures, structured-decision validation retries, and rejected actions executed after
validation. Category/reason counts are derived in the run summary; `{}` means an older aggregate
whose classification is unavailable. Validation retries are not necessarily failed HTTP game-tool
calls.

New player AgentRuns also include versioned, content-free `request_topology`. Its component byte
counts measure an AMAW canonical-JSON logical request projection at PydanticAI's model-wrapper
boundary; they reconcile exactly to that measured representation, but are not provider HTTP-wire
bytes or tokenizer counts because provider adapters may rewrite roles, schemas, and whitespace.
Request growth and common-prefix lengths describe structural overlap only. Prefix digests use an
ephemeral per-activation HMAC key that is never persisted, so an exported digest cannot be used as
a raw content-hash oracle. Provider-reported `cache_read_tokens` and `cache_write_tokens` remain
separate observations and are not inferred from prefix overlap. `tokenizer: null` means no
identified tokenizer-derived component attribution is claimed. `{}` means legacy/unavailable;
Umpire composition is not measured by this player-boundary schema.

This is an **unfiltered administrative/research export**. It intentionally contains private Actor
briefs, objectives and information, private Messages, private Effects, and private Notes with
ownership/visibility metadata. It contains no Seat token, token hash, provider credential, admin
token, or hidden chain-of-thought. Store and share it as sensitive research data.

Useful first checks with `jq` include:

```bash
jq '.game | {id, scenario_id, turn, phase, status, seed}' game.json
jq '.run_summary' game.json
jq '.agent_runs[] | {role, actor_id, phase, completed, failure_reason}' game.json
jq '.effects[] | {description, active, visibility}' game.json
jq '.resource_access' game.json
```

The export supports inspection and authoritative game reconstruction. It cannot replay the exact
hosted model generations.

## Benchmarks

`amaw benchmark` schedules and runs a batch of autonomous games without per-game setup
(TRD §30; `benchmark-methodology.md` §7–§8, §21, §25):

```bash
uv run amaw benchmark orison-crisis \
    --benchmark-mode model \
    --models provider/a,provider/b,provider/c \
    --rotate \
    --repetitions 5 \
    --output-dir runs/my-batch
```

`--models` names one model for every Seat, or exactly one model per Actor in turn order;
`--rotate` (equal counts only) generates every model-to-role permutation and
`--rotation cyclic` balances unequal counts. `--benchmark-mode model|agent` is required and
labels how the batch may be read. `--repetitions N` means N games **per assignment**, never N
games in total. The complete schedule — assignments,
`repetition_index`, one seed per game from the recorded policy
`base_seed + assignment_index * 1000 + repetition_index`, and a deterministic interleaving from a
recorded `--scheduling-seed` — is generated and reported (`assignment_count`,
`repetitions_per_assignment`, `total_games`) before any game exists. The scheduling seed changes
only the execution order; it never changes a game's seed. Every `amaw run` condition flag
(Umpire model, Diplomacy/Con modes, activation mode, guardrails, inference profile,
`--analysis-plan`) applies to the whole batch and is recorded.

Games run serially. **One failed game never aborts the batch**: the attempt is recorded with
`completed=false`, a failure category (`agent_stall`, `provider_timeout`, `invalid_tool_loop`,
`umpire_failure`, `server_failure`, `game_incompletion`), a redacted one-line reason, the turn
reached and the Actor affected, and its partial export is retained. The planned number of
attempts is the denominator; nothing is silently replaced or topped up.

**The experiment declared is the experiment that ran.** Before the first game the batch writes
its complete expected condition into `batch.json` (`expected_condition`, with a `sha256`): the
scenario hashes, the resolved player and Umpire configuration (and, under
`bounded_effects_v1`, the sealed consequence resolver), game-loop and coordinator settings, code version, mode, rotation and seed policies, run class and sealed plan. Every
attempt's authoritative run manifest — the first included — is compared with that declaration
and with its own scheduled seed and Actor→model assignment; a completed attempt without a
manifest is itself a finding. A `model` batch is held to the whole `benchmark-methodology.md`
§5.1 set, an `agent` batch to everything but the player configuration (§5.2). Any difference is
an experiment-integrity failure: it is recorded on the attempt and in `batch.json.integrity`,
the batch stops, every still-scheduled attempt is written as `not_run` so the planned
denominator stays visible, and the command exits 1. A different Umpire, scenario or player
condition is a different experiment and needs its own batch. With `--analysis-plan`, the plan's
`design` (repetitions, planned attempts, treatment levels) must describe the schedule or the
command exits 2 before any game exists. An `analysis-plan-v2` plan is also held to its declared
`scope`, `rotation_policy` (`--rotation`) and `seed_policy`. A v2 plan with `scope: cell` runs
one batch per treatment level, and each batch names its level with `--plan-level LEVEL`; every
cell seals the same plan, so `analysis-plan verify` accepts the exports of all of them. The level
is recorded in `batch.json` under `experiment.analysis_plan.plan_level`.

`--output-dir` must be absent or empty: a batch owns its evidence directory and never writes
into one that already has contents (there is no batch resume). The server also refuses to
start an autonomous game whose run manifest has not been sealed, and refuses to seal a manifest
once a game has left the lobby, so pre-commitment is a server-enforced fact.

The output directory contains:

```text
batch.json                      experiment record, full schedule, every attempt's outcome
games/<attempt>/attempt.json    identity + outcome of one scheduled attempt
games/<attempt>/export.json     that game's administrative export (partial for failures;
                                a minimal stub when no game was created)
games.csv agents.csv arguments.csv messages.csv resource_access.csv
```

The five CSV tables are a pure projection over the per-attempt exports and load into any
analysis tool without post-processing; the `benchmark-methodology.md §25` columns lead each
table and raw WBS 5.6 metrics follow. Planned and observed values are separate columns:
`games.csv` carries the scheduled `seed` and the manifest's `seed_observed` plus
`condition_match`; `agents.csv` carries the scheduled `model_requested`, the manifest's
`model_assigned` and the provider's `model_returned`. The raw exports remain the authority. The
tables contain private game material (Argument text, queries) and are administrative data. No
behavioural or psychological score is computed.

Not yet provided: objective scoring of whether Actors met their objectives, parallel game execution, and resuming an
interrupted batch (resume individual games with `amaw run --resume-game`, into a new output
directory). Do not present findings from playtests: an analytical batch seals its analysis plan
first (`--analysis-plan`, see *Run a game*). The normative experimental controls, run
classification and interpretation rules are in
[`benchmark-methodology.md`](../docs/benchmark-methodology.md).

## Current limitations

- The supplied autonomous entrypoint is a local, in-process CLI composition; AMAW does not ship a
  human UI or a production deployment recipe.
- SQLite is the only persistence backend.
- End-of-turn Consequence Management is off by default: after the final adjudication the engine
  goes directly to the next turn or completion. `bounded_effects_v1` is an opt-in experimental
  condition (see *Run a game*).
- Benchmark batches run serially; objective scoring and batch resume are deferred (see
  *Benchmarks*).
- PDF, PNG/JPEG, and GeoJSON Resources are opaque attachments; there is no OCR, semantic index,
  vector search, or large-document ingestion pipeline.
- The scenario format has fixed turn order and no scenario-defined early termination, custom
  communication topology, external MCP tools, or custom schemas. Scenario `rules` are public text
  the Umpire weighs; the server does not execute them.
- Hosted LLM output and provider availability are not deterministic. The server RNG is seeded and
  recorded, but identical seeds do not guarantee identical games.
- Provider cost fields are reported only when the provider supplies them; summaries label partial
  or unavailable cost coverage.

For known engineering follow-ups and release status, see the public repository's GitHub issues
and releases.
