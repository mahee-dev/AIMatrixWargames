# Structural Cleanup Review — 2026-09-16

> Backlog items **I-060 … I-065** in `dev/BACKLOG.md` are the actionable form of this review.
> This file holds the evidence and the per-finding detail so the backlog entries can stay short.

**Goal:** a simpler codebase where the flow
`scenario → game → phase → activation → mutation → adjudication → export` is easier to trace.
Not fewer lines, not smaller files. No new features, architecture, persistence or Phase-2 work.

**Method:** read-through of `app/`, `agents/`, `benchmark/`, `amaw_cli.py` at commit `cc912d7`
(post I-046), plus a reference scan for top-level names with zero source references. Reviewed a
second time against an external critique; the sequencing in §3 is the reconciled result.

---

## 1. How the flow reads today

The engine path is traceable file-by-file: `app/scenarios.py` → `app/game_service.py` (all
transitions) → `app/activation_context.py` → `app/api.py` → `app/export.py`.

What obscures it is everything *around* the engine: the experimental condition and its
provenance are assembled, validated and re-validated in five or six places as six loose keyword
arguments, and the CLI's two big commands each rebuild the whole composition root by hand.

---

## 2. Findings

Classification is one of: delete · consolidate · split · rename/clarify · leave alone.

### F1 — `amaw_cli.py`: `run` and `benchmark` are two copies of one composition root
**consolidate** — `amaw_cli.py:602` (`run`, ~470 lines), `amaw_cli.py:1069` (`benchmark`, ~410 lines).

Each command: parses flags, validates `--run-class`/`--analysis-plan`, checks the OpenRouter key,
overlays flag values onto `Settings`, lazily imports the agent stack, loads the inference profile,
resolves the game-loop condition, builds `PlayerConfig`, builds the resume recipe, builds
`run_runtime`, defines `player_factory`, resolves the Umpire inference entry, constructs
`AIUmpire`, calls `create_app`, opens a `TestClient`, runs. Nine of these are byte-identical or
near-identical between the two (compare `amaw_cli.py:646-705` with `:1189-1268`, and `:895-925`
with `:1347-1372`). The shared `*Opt` annotations were added to stop flag drift; the bodies never
got the same treatment.

*Proposed:* one private "compose an autonomous session" helper returning
`(settings, admin_token, engine, umpire, player_config, resume_recipe, run_runtime, player_factory)`.
Not a large `Session` abstraction — a couple of focused helpers/dataclasses. `run` and `benchmark`
become: parse → resolve condition → compose → drive → write outputs.
*Risk:* low-medium. Error text and exit codes are asserted by tests; validation must still
precede any DB/provider side effect (I-047: an unusable plan costs no Game).
*Tests:* `test_cli_run.py` (8), `test_benchmark_runner.py`, `test_benchmark_controls.py`,
`test_i047_analysis_plan.py`, `test_i032_inference_profile.py`, `test_release_package.py`
(import boundary on `amaw_cli`).

### F2 — The game-loop condition is six loose kwargs validated in three places
**consolidate** — `amaw_cli.py:341-438` (`_game_loop_condition`), `app/scenarios.py:522-580`
(`create_game_from_scenario`), `app/resumption.py:82-115` (`_validate`),
`agents/autonomous.py:345-368` (`resume`, field-by-field), plus threading through
`app/api.py:389` (`_CreateGameIn`), `agents/autonomous.py:297-308` (`create_body`), `:829-839`
(`base["game_loop"]`), `:1069-1074` and `:1130-1135` (`model_parameters`),
`app/provenance.py:291-311`.

`diplomacy_mode / diplomacy_rounds_per_turn / con_mode / con_challengers_per_argument /
con_reply_mode / reply_max_points` travel as six parameters through 11 files (58 mentions of one
field alone). The rule "mode X requires positive N; otherwise N must be None" is written three
times with three error vocabularies. `AutonomousGameRunner.resume` checks the three pairs by hand
and then *also* compares the whole `game_loop` dict (`autonomous.py:381-388`) — the hand checks
are redundant.

*Proposed:* one frozen value object `GameLoopCondition` in a new **`app/game_loop.py`** (not
`scenarios.py` — a scenario loader should not own runtime experimental conditions; not
`amaw_contracts.py` — that file is constants, and this is a persisted `Game` property with a
domain validator). Methods: `validate()`, `from_mapping()`, `as_dict()`. `create_game_from_scenario`
keeps its one scenario-specific rule (K ≤ opponents, needs the Actor count) and delegates the
pairing rules. CLI, `resumption._validate` and `AutonomousGameRunner` pass/compare the object.
`agents/` may import it (`agents` already imports `app.models`/`app.umpire`; the forbidden
direction is `app → agents`). **Flat API body, `Game` columns, manifest `game_loop` shape and
export are unchanged.**
*Risk:* medium. The three validators differ subtly in bool rejection and error strings; manifest
hashes must not move (fixture + `test_run_manifest_is_complete_immutable…`).
*Tests:* `test_i053_bounded_diplomacy.py`, `test_i055_sampled_cons.py`, `test_i046_visible_reply.py`,
`test_resume_game.py`, `test_cli_run.py::test_i048_007_*`, `test_run_provenance.py`,
`test_scenarios.py`, `test_integration_six_turn.py`.

### F3 — The `autonomous-resume-v1` recipe is built in two places and unpacked into 28 locals in a third
**consolidate** — `amaw_cli.py:550-600` (`_resume_recipe`), `agents/autonomous.py:855-889`
(`_manifest_configuration` fallback), `amaw_cli.py:753-860` (`run --resume-game` unpacking).

The CLI builds the recipe from flags; the coordinator rebuilds the identical shape from
`PlayerConfig` when none is supplied; on resume the CLI reads it back into ~28 locals, threads them
through `_player_config`, hands the *original* recipe to the runner, which rebuilds
`_manifest_configuration` and compares. The `changing = {...}` dict at `amaw_cli.py:760-790`
re-lists every flag by hand to refuse overrides.

*Proposed:* the coordinator's builder is the only builder; the CLI constructs
`PlayerConfig`/runner kwargs from the recipe dict directly (a small classmethod on the existing
dataclass). **Keep the explicit named override set** — do not replace it with reflection over
Typer `Annotated` defaults — but add a test asserting every condition-bearing `*Opt` parameter of
`run` appears in that set, so the set cannot drift silently.
*Risk:* medium. Byte-identical manifests on resume are the invariant (`resume` compares seven
keys). Legacy recipes without `research_mode`/`umpire.prompt_version` must still resolve to the
documented defaults.
*Tests:* `test_cli_run.py::test_i048_007_cli_resume_reconstructs_condition_and_calls_resume`,
`test_resume_game.py` (5), `test_run_provenance.py`, `test_i032_inference_profile.py`,
`test_benchmark_controls.py`.

### F4 — Umpire provenance → `AgentRun` mapping lives in the HTTP route and knows OpenRouter field names
**consolidate (move) + rename/clarify (make the contract explicit)** — `app/api.py:647-733`
(`_admin_advance_game`), `app/api.py:450-466` (`_usage_integer`, `_usage_cost`),
`agents/umpire.py:343-408` (two near-identical `_pending_provenance.append({...})` blocks),
`app/umpire.py:89-100` (`Umpire` Protocol).

The advance route drains raw provider records via `getattr(umpire, "drain_provenance")`, then maps
them to `record_agent_run(...)` with 25 kwargs, normalising tokens with dotted OpenRouter/OpenAI
paths (`prompt_tokens_details.cached_tokens`, `completion_tokens_details.reasoning_tokens`). This
is the one concrete **AGENTS.md §6.2 violation** ("OpenRouter integration lives outside the core
game domain"). `provenance_configuration`, `drain_provenance`, `last_failure` are reached by
`getattr`; none is on the `Umpire` Protocol, so the contract the server relies on is invisible. The
player path already normalises usage in `agents/player.py::_request_usage_dict` — two
normalisers, different rules.

*Proposed:* (a) `AIUmpire` emits already-normalised, `AgentRun`-shaped records via one private
builder replacing the two dict literals, reusing the player path's normalisation rule, in
`agents/`; (b) a **second** `runtime_checkable` Protocol in `app/umpire.py` (e.g.
`UmpireProvenance`: `provenance_configuration()`, `drain_provenance()`, `last_failure`) — **do not
widen `Umpire`**, which stays the pure adjudication contract; `api.py` uses `isinstance` instead
of three `getattr`s. `FakeUmpire` already implements two of the three (`agents/umpire.py:128-143`)
and needs `last_failure = None`. `_usage_integer`/`_usage_cost` leave `app/`.
*Risk:* medium. Umpire `AgentRun` rows are exported and summarised; `reasoning_tokens`,
`cache_read_tokens`, `costed_model_calls`, `failure_reason` on outage must be identical.
`last_failure` on a *server-side* resolution error (not provider) must still surface.
*Tests:* `test_run_provenance.py::test_admin_advance_persists_umpire_provider_call`,
`::test_cost_is_unavailable…`, `test_ai_umpire.py`, `test_i050_token_accounting.py`,
`test_adjudication.py`, `test_umpire.py`.

### F5 — `_record_activation` / `_record_failure` are one record with two overlays
**consolidate** — `agents/autonomous.py:1045-1192`.

Two ~70-line methods; ~55 keys identical (`model_parameters`, framework fields, five token fields,
`costed_model_calls`, `cost_usd`). They differ in `activation_id` source, `model_returned`/
`provider`, `stalls`, `completed`, `failure_reason`, `provider_metadata`, and
`agent_framework_version` (`None` on failure for no stated reason).

*Proposed:* one `_agent_run_body(...)` for the common record; two callers overlay ~10 keys.
Preserve the `agent_framework_version: None` asymmetry or fix it deliberately with a
`DECISIONS.md` entry. This is agent-layer work, not engine work — it belongs with I-061, not I-064.
*Risk:* low.
*Tests:* `test_i050_token_accounting.py`, `test_i052_invalid_tool_call_telemetry.py`,
`test_i056_request_token_topology.py`, `test_lifecycle.py`, `test_autonomous_concurrency.py`,
`test_run_provenance.py`.

### F6 — `app/provenance.py` is two modules: recording and reporting
**split** — `app/provenance.py` (1025 lines).

Lines 1–430 write and validate immutable provenance (`create_run_manifest`, `record_agent_run`,
topology/breakdown validation, sensitive-key rejection). Lines 430–1025 derive reports
(`build_resource_research_report`, `build_game_summary`, `_con_condition_summary`,
`_con_reply_condition_summary`, `format_game_summary`, `_duration`). Nothing in the second half
writes.

*Proposed:* `app/provenance.py` keeps recording + validation; **`app/summary.py`** (matching the
`/admin/games/{id}/summary` route) gets the derived reports. `api.py` and `benchmark/outputs.py`
re-pointed.
*Risk:* very low (move-only).
*Tests:* `test_run_provenance.py`, `test_i045_resource_activation_correlation.py`,
`test_i054_resource_search_replay_cache.py`, `test_i055_sampled_cons.py`,
`test_i046_visible_reply.py`, `test_export.py`.

### F7 — `game_service.py`: seven "current Argument by status" queries and three near-duplicate guards
**consolidate (small, local)** — `app/game_service.py:636` `_get_current_argument`, `:1521`
`_get_awaiting_reply_argument`, `:1714` `_current_adjudicating_argument`, `:2135`
`_resolved_current_argument`, `:2207-2235` inline in `adjudication_pending`, `:1268-1275` inline in
`make_argument` (game-wide, no turn/actor filter — do not fold in blindly); `app/context.py:221`
`_current_argument`. Also `:2272-2289` `_require_running_diplomacy` ≡
`_require_running_phase(game, GamePhase.diplomacy)`; `:446` `_run` ≡ `_run_idempotent(key=None)`
with the same `GuardFailed→GameNotFound` mapping; `_actor_ids` defined identically at `:622` and
`app/context.py:307`.

*Proposed:* see F8 — the shared reads go to `app/game_queries.py`; `game_service.py` keeps the
"exactly one or fail closed" wrappers with their service-specific error classes
(`ArgumentNotCurrent` vs `AlreadyAdjudicated`). Delete `_require_running_diplomacy`; make `_run` a
one-line alias or remove.
*Risk:* low if each caller keeps its exact status set and error class.
*Tests:* `test_game_service.py`, `test_argument_cons.py`, `test_adjudication.py`,
`test_sequencing.py`, `test_idempotency.py`, `test_concurrency.py`, `test_i046_visible_reply.py`,
`test_integration_six_turn.py`.

### F8 — `app/context.py` mirrors service guards with its own queries
**consolidate** — `app/context.py:311-346` (`_has_argument_budget`, `_con_eligible`, `_con_count`)
vs `app/game_service.py:1280-1292` and `:1400-1410`.

`_available_actions` re-implements "Arguments used this turn" and "Cons on this Argument" so it
advertises exactly what the service would accept. `_con_eligible` already delegates to
`game_service.eligible_con_actor_ids`; the other two do not. `context.py` already imports
`game_service` (line 33), so the concern is importing *private* helpers, not a new dependency.

*Proposed:* a small neutral **`app/game_queries.py`** holding: Argument-by-status for the active
Actor (returns a list), arguments used, Cons submitted, Actor ids, and `eligible_con_actor_ids`
(a pure read of `argument.challenger_actor_ids`). Both `game_service.py` and `context.py` depend
downward on it; `context.py` drops its `game_service` import entirely.
*Risk:* low.
*Tests:* `test_api_context.py`, `test_i043_con_capacity.py`, `test_i055_sampled_cons.py`,
`test_single_shot.py`.

### F9 — Canonical-JSON hashing and sensitive-key rejection are re-implemented six and two times
**consolidate** — `_canonical` in `app/activation_context.py:66`, `app/provenance.py:237`,
`agents/request_topology.py:59`, `benchmark/analysis_plan.py:66`; `canonical_sha256`
`benchmark/controls.py:48`; `_semantic_hash` `app/game_service.py:461`; `canonical_json_size`
`amaw_contracts.py:43`. `_SENSITIVE_KEYS` + `_reject_sensitive_keys` duplicated in
`app/provenance.py:48` and `benchmark/analysis_plan.py:41`.

*Proposed:* `amaw_contracts.canonical_json(value) -> bytes`, `canonical_sha256(value) -> str`,
`SENSITIVE_KEYS`; the sites call them. `_semantic_hash` keeps its `default=str` variant only if a
test proves the difference matters. **Keep the exact current serialisation**
(`sort_keys`, `separators=(",", ":")`, `ensure_ascii=False`).
*Risk:* low-medium — any serialisation change moves persisted hashes; the fixture and manifest
tests catch it.
*Tests:* `test_run_provenance.py`, `test_i047_analysis_plan.py`, `test_benchmark_controls.py`,
`test_idempotency.py`, `test_i056_request_token_topology.py`, `test_incremental_memory.py`.

### F10 — Inference-profile vocabulary duplicated across the `app`/`agents` boundary; `InferenceProfile` buried in `player.py`
**consolidate (constants) + split (module)** — `agents/player.py:129-141` and `:145-305`
(`InferenceProfile`), `app/resumption.py:118-165` (`_validate_sealed_inference_profile`,
"structural twin").

Schema name, phase names, setting keys and effort enum are written twice because `app` may not
import `agents`. `InferenceProfile` (160 lines, a config document with its own loader) sits in the
1750-line player module and is imported by `amaw_cli`, `benchmark/controls`, `agents/autonomous`.

*Proposed:* constants → `amaw_contracts.py` (already imported by both sides).
`InferenceProfile` + `load_inference_profile` → **`agents/inference_profile.py`**; `player.py`
re-exports. `resumption.py` keeps its validator (deliberate, TRD §4) but reads the constants from
contracts.
*Risk:* very low.
*Tests:* `test_i032_inference_profile.py` (holds both sides to the same documents),
`test_player.py`, `test_benchmark_controls.py`.

### F11 — Git worktree hashing inside the game coordinator
**split** — `agents/autonomous.py:897-1030` (`_framework_version`, `_code_version`,
`_dirty_source_digest`, `_is_generated_run_evidence`, `_hash_worktree_path`,
`_GENERATED_RUN_EVIDENCE_NAMES`): ~135 lines of `subprocess git`, `os.stat` and SHA-256 as
static methods on `AutonomousGameRunner`.

*Proposed:* **`agents/code_version.py`** with `code_version(root=None) -> str`; the runner calls
it. `tests/test_code_provenance.py` calls `AutonomousGameRunner._code_version(repo)` directly and
is re-pointed.
*Risk:* very low.
*Tests:* `test_code_provenance.py` (5), `test_resume_game.py`, `test_benchmark_controls.py`.

### F12 — Dead helpers
**delete**

| Symbol | Where | Evidence |
|---|---|---|
| `DiplomacyMode` enum | `app/models.py:93` | zero references; `Game.diplomacy_mode` is `str` |
| `PlayerToolContext` + `_DEFAULT_BASE_URL` / `PlayerTools.for_base_url` | `agents/tools.py:405-427` | zero references in src or tests |
| `_run`, `_require_running_diplomacy` | `app/game_service.py:446`, `:2272` | subsumed by F7 |

Test-only but legitimate public helpers — **leave alone**: `provenance.list_agent_runs`,
`analysis_plan.classify_measure`, `analysis_plan.run_class_of`, `database.reset_default_engine`.

### F13 — Module docstrings that are task ledgers
**rename/clarify** — `app/game_service.py:1-79` (79 lines listing T-013…T-022 and what was "kept
out on purpose" for tasks since landed), `app/api.py:1-30`, `amaw_cli.py:1-21` ("a later
`benchmark` command (T-054) will drive real agents" — it exists).

*Proposed:* each docstring states what the module *is* and the invariants it owns, ≤15 lines.
Keep inline `INV-*`/`§` citations at the rule sites. Check `test_operational_docs.py` before editing.

---

## 2b. Leave alone (considered, rejected)

- **`game_service.py` at 2289 lines.** The `captured` + `mutate` closure + `_run_idempotent`
  pattern is uniform across all nine mutations and is the clearest expression of INV-GAME-004.
  Splitting by phase would scatter the one place that owns transitions.
- **`agents/scripted.py`'s engine-level mini-coordinator.** Gate-1 offline harness; deliberately
  bypasses HTTP.
- **`benchmark/`** — `runner.py`, `controls.py`, `analysis_plan.py`, `outputs.py` have single,
  well-named responsibilities.
- **`app/persistence.py`, `visibility.py`, `rng.py`, `security.py`, `umpire.py`** — small,
  single-purpose.
- **`api.py` player routes (~360 lines).** Thin; `_log_resource_access` is the documented §10
  side-effect.
- **`_TimedModel`, `RequestTopologyProfiler`, `compact_research_history`** — measurement
  instruments protected by I-050/I-056/I-057 tests.
- **`ReferencePlayer._activate` builds `counts={...}` four times** (`player.py:1583-1660`). A
  `_failure_counts()` helper would help but touches the most test-sensitive code. Deferred.

---

## 3. Sequencing (reconciled after external review)

| Task | Contains | Branch scope |
|---|---|---|
| **I-060** (C1) | F2 `GameLoopCondition` in `app/game_loop.py`; F9 canonical JSON/sha256 + `SENSITIVE_KEYS`; F10 constants → `amaw_contracts.py`; three validators replaced; flat shapes unchanged | `app/`, `amaw_contracts.py`, call sites |
| **I-061** (C2) | F1 one composition path for `run`/`benchmark`; F3 one recipe builder (coordinator) + one parser (CLI), explicit override set + guard test; F5 record merge | `amaw_cli.py`, `agents/autonomous.py` |
| **I-062** (C3a) | F6 `provenance.py` → recording only, reports → `app/summary.py`; F13 docstrings | `app/` |
| **I-063** (C3b) | F10 `agents/inference_profile.py`; F11 `agents/code_version.py`; F12 delete `DiplomacyMode`, `PlayerToolContext` | `agents/`, `app/models.py` |
| *reassess* | re-run the dead-code scan and the flow read-through | — |
| **I-064** (C4) | F7 + F8 `app/game_queries.py`; `game_service` and `context` depend on it; drop `_require_running_diplomacy`, `_run` | `app/` |
| **I-065** (C5) | F4 `UmpireProvenance` protocol; `AIUmpire` emits normalised records; `_usage_integer`/`_usage_cost` leave `app/` | `app/umpire.py`, `app/api.py`, `agents/umpire.py` |

Order rationale: I-060 removes the six-argument plumbing before anyone touches CLI composition;
I-061 is the biggest duplication and reads short once the condition is one value; I-062/I-063
are behaviour-preserving moves and are split because they do not share a module or acceptance
boundary (AGENTS.md §4); I-064 waits for the reassessment; I-065 is its own small design task
because it changes a protocol, but it is the only AGENTS.md §6.2 violation and is not optional.

If only three land: I-060, I-061, I-065.

**Every task:** `uv run pytest` green with output pasted into the brief; no manifest hash, export
shape, API body or persisted column changes; `test_release_package.py` import-boundary test stays
green.
