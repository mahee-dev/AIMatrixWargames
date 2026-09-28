# Structural Hotspot Plan — 2026-09-24

> The plan I-119 asks for before any structural change. Each approved split is filed as its own
> backlog item: **I-124**, **I-125** and **I-126**. I-120 (typed hand-offs) rides on I-125
> because it touches the same resolution path. I-119 is the umbrella and closes when those three
> have landed.

**Goal:** name the concrete responsibilities inside the four largest modules. Split only where a
boundary already exists and the result is easier to trace. Line counts are evidence, not a
reason.

**Method:** read-through of `agents/player.py`, `amaw_cli.py`, `app/game_service.py` and
`app/api.py` at `841eded`. Function lengths measured with `ast`. For each candidate: where the
split runs, the protecting tests, and the invariants that must not move. Baselines captured
before any edit (CLI help, the runner and batch hand-offs as canonical JSON, and every player
tool definition across all 16 surface combinations) are compared again after each split.

**Invariants for every split below.** No behaviour change:
- `app/` stays free of agent-framework imports. The subprocess import-boundary test in
  `test_release_package.py` stays green.
- No API body, export, manifest key, resume recipe, prompt, tool name or tool schema changes.
- No persisted column changes.
- Import direction: `agents → app`, and `amaw_cli → agents/app/benchmark`. Never `app → agents`.

---

## 1. `agents/player.py` (1997 lines) — **split warranted → I-124**

**Hotspot.** `ReferencePlayer._build_agent` (`agents/player.py:1021`, about 340 lines).

**Current responsibilities.** `_build_agent` does three different jobs:
1. **Exposure policy.** `prepare_mutating_tool` withdraws a mutating tool under D-077/D-079
   using the server's declaration. `prepare_research_tool` hides research once the I-069 call
   budget or the D-078 operation budget is spent, and appends the remaining-budget sentence to
   each description.
2. **Per-call accounting.** `tracked_tool`/`counted` counts every call. It also reserves research
   calls under the lock (I-077), closes on an exact repeat (D-062), refuses over budget, and
   re-declares the live surface after the Actor's own successful mutation.
3. **Tool bodies.** The 12 closures that validate guardrails and call `PlayerTools`.

The state that jobs 1 and 2 share lives on the player: `_tool_call_lock`, the call, research and
operation counters, the in-flight and successful call keys, `_research_exhausted`,
`_declared_actions` and `_live_actions` (`:885–:909`). That state is reset in `_activate`
(`:1771–:1781`) and read by five other methods (`_remaining_research_operations` `:1397`,
`_charge_research_operation` `:1404`, `_refresh_live_surface` `:1554`, `_ceiling_counts` `:1727`,
`_research_counts` `:1747`).

**Why it is hard to change.** The locking rules (I-021, I-069, I-077, I-091, I-092, D-078) are
spread across nested closures, `__init__` and `_activate`. A reviewer has to hold all of them
at once to see that a counter is only ever touched under `_tool_call_lock`. There is no unit
that owns "which tools the model sees, and how each call is admitted".

**Boundary.** A new `agents/tool_surface.py`:
- **`ToolSurface`**, one per player, owns jobs 1 and 2 and the locked state behind them:
  - `reset()` and `declare()` at the start of an activation;
  - `prepare_mutating_tool` and `prepare_research_tool`;
  - `tracked()`, the call-counting and research-reservation wrapper;
  - `charge_research_operation()`, `remaining_research_operations()` and `research_counts()`;
  - `record_live_actions()`;
  - `exposed_tool_names()`.
- The tool-name vocabularies move with it.

The **tool bodies stay in `ReferencePlayer._build_agent`** as closures. PydanticAI derives each
tool's name, description and JSON schema from the function's `__name__`, docstring and
signature. Keeping the closures unchanged is what keeps every tool definition byte-identical.
`ReferencePlayer` keeps the lifecycle, the Message budget (`_send_lock` and its counters, which
are a separate lock by I-021) and packet building.

This is one collaborator for a concern that already exists, not a tool framework. No tool gains
access to persistence. Tools still reach the game only through `PlayerTools`.

**`_activate` (`:1755`, about 235 lines) — not split.** It reads top to bottom as one logical
activation:
1. reset;
2. packet;
3. two-attempt model loop;
4. single-shot execution;
5. acknowledgement.

The only candidate is the three near-duplicate `counts={...}` literals. Their key order reaches
persisted `AgentRun` failure records, so a shared builder risks changing bytes for no gain in
clarity. The counter reset moves into `ToolSurface.reset()` as a side effect of I-124. Nothing
else changes.

**Protecting tests.**
- `test_player.py`, `test_guardrails.py` (budgets, duplicates, concurrency).
- `test_i092_tool_surface.py`, `test_i091_stale_context.py`, `test_i046_visible_reply.py`.
- `test_i057_batched_research_context_compaction.py`,
  `test_i098_i099_research_operations_and_result_identity.py`, `test_i101_batch_item_outcomes.py`.
- `test_i103_tool_loop_sibling_race_documented.py`, `test_autonomous_concurrency.py`,
  `test_single_shot.py`, `test_lifecycle.py`, `test_i052_invalid_tool_call_telemetry.py`.
- The tool-definition baseline across 16 combinations: activation mode × research mode ×
  reply mode × tool surface.

**Invariants.**
- Tool names, descriptions and schemas are unchanged, and so is `tool_schema_version`.
- Legality stays server-declared (API-INV-009).
- Every counter is read and written only under the one lock.
- The accounting is unchanged:
  - the transient retry shares the activation-wide ceilings (I-077);
  - an operation is charged before dispatch (D-078);
  - an in-flight twin is rejected without closing the surface.

---

## 2. `amaw_cli.py` (1644 lines) — **split warranted → I-125 (with I-120)**

**Hotspots.** `benchmark()` (`amaw_cli.py:1209`, 432 lines) and `run()` (`:890`, 316 lines).

**Current responsibilities.** Each command parses flags, then resolves a new game's condition,
then composes and drives, then reports. Resolving the condition is the same eight steps in both
commands: `run` at `:1049–:1113` and `benchmark` at `:1433–:1500`.
1. The inference profile.
2. `max_cycles`, `cons_concurrency` and `diplomacy_concurrency` with their fallbacks.
3. `_game_loop_condition`.
4. `_consequence_recipe`.
5. `_umpire_recipe` with `_umpire_prompt_version`.
6. `_player_config`.
7. `build_resume_recipe`.
8. `_run_runtime`.

`run --resume-game` resolves the same values from the sealed recipe instead (`:1013–:1048`) and
leaves them in 12 locals. The recipes travel as `dict[str, Any]`.

**Why it is hard to change.** A condition change has to be made twice, in two long functions
whose local variable names the rest of the command reuses implicitly. There is no object that
says "this is the condition this invocation will run". The resume path and the fresh path fill
the same locals by different routes, so it is not visible that they produce the same things.

**Boundary.** This is the I-096 `RunSpec` direction, limited to fields that are stable today:
- **`ConditionFlags`** (frozen dataclass): the condition-bearing flag values the two commands
  share, exactly as Typer parsed them.
- **`ResolvedCondition`** (frozen dataclass): what both commands hand to execution. That is the
  profile, `PlayerConfig`, `GameLoopCondition`, the Umpire, consequence and resume recipes, and
  the three coordinator values.
- **`_resolve_new_condition(settings, flags, …)`** is the one resolution for a new game, used by
  both commands in the same order. That order fixes which of several invalid flags is reported
  first.
- **`_resolve_sealed_condition(recipe)`** is the resume path's inverse.

`run()` and `benchmark()` then read as parse, resolve, execute, report. Typer flags, defaults,
help text, error text, exit codes and the order of validation are unchanged. Validation still
happens before any database or provider side effect (I-047). `RESUME_OVERRIDE_FLAGS` stays the
explicit set.

**Not introduced.** No `BenchmarkSpec`. The batch-only values are the schedule, roster, mode and
output directory. They are used in one place, and the benchmark layer already owns them as
`Schedule` and `BatchControls`. No session or composition object either: compose and drive stay
in each command, because they genuinely differ (one game against a batch).

**Protecting tests.**
- `test_cli_run.py`, `test_i061_cli_composition.py` (including the `RESUME_OVERRIDE_FLAGS` drift
  guard), `test_i096_cli_engine_ownership.py`.
- `test_i032_inference_profile.py`, `test_i047_analysis_plan.py`, `test_benchmark_runner.py`,
  `test_benchmark_controls.py`.
- `test_i114_benchmark_consequence_condition.py`, `test_t074_consequence_management.py`,
  `test_t073_scenario_rules.py`, `test_i083_condition_projections.py`,
  `test_release_package.py`.
- The hand-off baseline: 13 `run` and 4 `benchmark` invocations, including every refusal.

**Invariants.**
- Every recipe, `run_configuration`, `experiment` record and sealed condition is identical.
- Manifest hashes and resume comparisons are unchanged.
- The CLI help output is unchanged.

---

## 3. `app/game_service.py` (2606 lines) — **not split**

**Candidates evaluated.** A phase-oriented split into Diplomacy (`:839`, `:868`, `:1034`,
`:1152`), Arguments and Cons (`:1211`, `:1364`, `:1439`, `:1544`), adjudication (`:1688`–`:2112`)
and consequence and turn sequencing (`:2118`–`:2596`), over a shared transaction, idempotency
and event module (`_run_idempotent` `:504`, `_emit_event` `:578`, the guards at `:606`–`:704`).

**Why it is rejected.** The transitions cross the phases:
- `ready` and `complete_diplomacy_round` close Diplomacy into the Argument phase through
  `_close_diplomacy_if_ready`;
- `make_con` and `pass_con` close Cons into adjudication or the reply step through
  `_close_cons_if_complete`;
- `_advance_sequencing` and `_complete_turn` reach back into `_start_turn`, `_release_injects`
  and `_expire_effects`.

After a phase split, following one Argument from submission to the next turn would cross four
files instead of scrolling one. The state machine is the thing this module must keep legible.

The file is already organised by banner sections in state-machine order. None of its functions
is over 180 lines. Every operation uses the one `captured`/`mutate`/`_run_idempotent` pattern, so
there is nothing duplicated to consolidate. Tests also patch module attributes such as
`game_service._close_cons_if_complete` (`test_argument_cons.py:554`, `:720`) to inject failures
after a commit, and a split would have to re-point those seams.

**Decision.** Leave it intact. Reopen when a phase gains enough of its own machinery that its
section stops reading as part of the one state machine.

---

## 4. `app/api.py` (1325 lines) — **small extraction warranted → I-126**

**Route-registration containers are not a defect.** `_register_player_routes` (`:920`, 361
lines) and `_register_admin_routes` (`:473`, 319 lines) are lists of short route closures that
share one `app`. Splitting them would only move decorators.

**Hotspot A: `_admin_advance_game`** (`:666`, 116 lines). Its Umpire branch (`:677`–`:729`) and
consequence branch (`:730`–`:774`) each end in the same `finally` block. That block takes the
request-local recorder's attempts, overlays `completed=False` and a `failure_reason` when the
provider answered but the server's own resolution failed, and calls
`provenance.record_agent_run`. This is one existing responsibility written twice: recording a
server-side resolution's invocation-owned attempts (I-081, I-065). A future fix to the overlay
rule would have to be made twice.
*Boundary:* one module-level helper `_record_resolution_attempts(engine, game_id, result, error,
*, role, turn, phase)`. Both branches keep their own adapter, dispatch and error class.

**Hotspot B: the request-size middleware** (`:365`–`:388`). It restores Starlette's private
`request._body` inline, and repeats the 65,536-byte limit three times as a literal.
*Boundary:*
- a named helper `_replay_consumed_body(request, body)` that alone touches `request._body`;
- one `MAX_REQUEST_BODY_BYTES = 65536` constant;
- the same error text, status codes, streaming order and early return.

This is not a middleware redesign.

**Protecting tests.**
- Hotspot A: `test_run_provenance.py`, `test_i081_invocation_owned_provenance.py`,
  `test_i065_umpire_provenance_protocol.py`, `test_t074_consequence_management.py`,
  `test_i050_token_accounting.py`.
- Hotspot B: `test_i088_streaming_body_limit.py`, and every POST test in the suite, since each
  one goes through the middleware.

**Invariants.**
- `AgentRun` rows are identical, including the overlay.
- The 413 and 400 bodies are identical.
- An oversized body is never buffered past the limit.

---

## 5. I-120 — typed hand-offs along the I-125 path

These contracts are stable and documented, and each gets one canonical type:

| Contract | Type | Home | Why this kind |
|---|---|---|---|
| Umpire recipe section | `UmpireRecipe` `TypedDict` (`prompt_version` not required, for legacy recipes) | `amaw_contracts.py` | JSON-shaped, sealed and compared |
| Consequence recipe section | `ConsequenceRecipe` `TypedDict` | `amaw_contracts.py` | same |
| Resume recipe and its `player`/`coordinator` sections | `ResumeRecipe`, `ResumePlayerRecipe`, `ResumeCoordinatorRecipe` | `amaw_contracts.py` | same; already the shared vocabulary of `app.resumption` and `agents.autonomous` |
| Manifest `runtime` section | `RunRuntime` `TypedDict` | `amaw_contracts.py` | JSON-shaped, compared on resume |
| CLI → runner `run_configuration` | `RunConfiguration` `TypedDict` (`runtime`, `resume`) | `amaw_contracts.py` | JSON-shaped, sealed into the manifest |
| CLI flag values / resolved condition | `ConditionFlags`, `ResolvedCondition` frozen dataclasses | `amaw_cli.py` | internal value objects, never serialised |

`amaw_contracts.py` is the right home because `app/` (the resume validator) and `agents/` (the
recipe builder) must share these definitions without `app` importing `agents`. The existing
`RESUME_*_FIELDS` tuples there are derived from the `TypedDict`s, so the field lists exist once.

**Deliberately left open:**
- provider payloads;
- export documents;
- `provenance_configuration()`, whose `settings` sub-mapping passes routing through verbatim;
- the manifest `benchmark` section, which a batch extends per attempt;
- the `experiment` record, which the controls extend;
- `inference_profile`, whose document is validated by its own loader.

**Serialisation.** `TypedDict`s are plain `dict`s at runtime, so nothing about the bytes can
change. Construction order is kept as it is, and a regression test compares the canonical bytes
of representative recipes built before and after.

**Type checker.** mypy. It is a pure-Python dev dependency with one `[tool.mypy]` block in
`pyproject.toml` and no new toolchain. It is scoped to the modules this work touches, and a
test runs it so that `uv run pytest` fails on a violation.

---

## 6. Verdicts

| Module | Split | Item |
|---|---|---|
| `agents/player.py` | `ToolSurface` out of `_build_agent`; tool bodies stay | **I-124** |
| `agents/player.py` `_activate` | none | — |
| `amaw_cli.py` | one typed condition resolution before execution | **I-125** (+ I-120) |
| `app/game_service.py` | none; the phase split would scatter the state machine | — |
| `app/api.py` | advance-route attempt recording + body-replay helper | **I-126** |
| `app/api.py` route containers | none | — |
