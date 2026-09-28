# Test-Suite Review — 2026-09-24

> **I-134.** A read-only review of `tests/` at `752eaab`. No test, source file or spec was
> changed. The only outputs are this report and backlog items **I-134…I-140**.

**Method.**
- `docs/testing.md` headings and ranges were parsed and cross-referenced with every `TEST-*`
  string, test name and docstring in `tests/` (Python `ast`).
- `uv run pytest --durations=40 -q` gave the slowest tests (§6).
- A second full run used a throwaway out-of-tree pytest plugin (`sys.addaudithook`, loaded with
  `-p` from the scratchpad, not committed). For every test it recorded the files opened, the
  subprocesses spawned, `time.sleep` calls and the wall time. §3, §5 and §6 use this data; it
  reports where each test *actually* reads, not where the source text suggests it reads.
- `cProfile` was run on the slowest test.
- Near-duplicates were found with 5-token shingles (Jaccard > 0.45) and shared-assertion overlap,
  then every pair cited below was read by hand.

**Coverage was not measured.** `pytest-cov` is not installed (`uv.lock` has no `coverage`,
`pytest-cov` or `xdist` entry), and adding it was out of scope.

**Counts.**
- 124 files in `tests/`: 120 `test_*.py` plus `__init__.py`, `conftest.py`, `api_helpers.py`
  and `agent_fakes.py`.
- 39,431 lines and 1,197 test functions, which collect as 1,476 tests after parametrisation, plus
  8 `llm` tests.
- 52 files are issue-named (`test_iNNN_*`), 8 are task-named (`test_tNNN_*`) and 60 are named by
  module or behaviour.

---

## 1. Summary

- **Verdict: sound and trustworthy, but organised by history and slower than it needs to be.**
  - The suite is green, hermetic and key-free (`conftest.py:23` removes `OPENROUTER_API_KEY`).
    It keeps the service layer and the HTTP layer separate, and no test is permanently skipped.
  - Every release-blocking `docs/testing.md` ID has a carrying test, with one gap:
    `TEST-ADJ-008` is carried only as part of a combined name. Nothing found here weakens a gate.
- **Traceability gaps are small but real.**
  - `TEST-I053-008` and `TEST-I053-012` (declared in testing.md §26) have no test.
  - 8 cited IDs are defined nowhere.
  - 164 of the 173 non-catalogue IDs cited in tests are defined only under `dev/`, which the
    public snapshot does not include.
- **Speed is concentrated.** 67 tests of 3 s or more take 332 s of the 447 s total (74%);
  1,189 tests take 33 s combined. Almost all of that cost is full six-turn games that only feed
  bookkeeping assertions, mostly in the benchmark and concurrency files. Sleeps cost nothing, and
  worktree hashing costs about 14 s.
- **Document-wording tests:**
  - 6 tests pin prose phrases and are brittle.
  - 6 tests read `dev/`.
  - The snapshot excludes whole files to deal with 5 of them, so it also drops 8 tests that would
    run fine without `dev/`.
- **Duplication is mostly boilerplate, not logic.**
  - 12 copies of the import-boundary check.
  - 28 files that re-derive the canonical fixture path.
  - Identical `_bearer`, `_actor`, `_count` and `_invoke` helpers.
  - Two exact test duplicates, and the §19 run re-asserting the §18 golden outcomes.
  - One vacuous assertion (`test_model_client.py:145`) and 7 cwd-relative paths that fail when
    pytest runs from `tests/` (checked by running one).

---

## 2. Behaviour map

Every `test_*.py` file is listed once, under the behaviour that most of its tests cover. Support
modules: `conftest.py` (DB and hermetic env), `api_helpers.py` (fixture seed, client, phase
drivers) and `agent_fakes.py` (offline PydanticAI models).

| # | Behaviour | Files | Spread across issue/task files? |
|---|---|---|---|
| 1 | **Scenarios & packages** — loading, validation, hashes, shipped packages | `test_scenarios.py`, `test_election_scenario.py`, `test_orison_scenario.py`, `test_t073_scenario_rules.py`, `test_t075_portability_scenario.py`, `test_t083_skaro_crisis_1_1.py`, `test_i118_scenario_condition_defaults.py` | Yes. Scenario rules (T-073), condition defaults (I-118) and three per-package files |
| 2 | **Domain model & persistence** | `test_models.py`, `test_model_integrity.py`, `test_database.py`, `test_persistence.py`, `test_i082_persistence_integrity.py` | I-003 and I-082 add integrity checks beside `test_models`/`test_persistence` |
| 3 | **Game start, phases, turn sequencing, RNG, events** | `test_game_service.py`, `test_sequencing.py`, `test_rng.py`, `test_events.py`, `test_game_loop.py`, `test_i053_bounded_diplomacy.py`, `test_i064_game_queries.py`, `test_t080_argument_rounds.py`, `test_t081_scheduled_injects.py` | Yes. Diplomacy-round closure is in `test_i053_*`, `test_autonomous_concurrency.py:119-262`, `test_database.py:91` and `test_resume_game.py:238`. `TEST-I053-002` exists twice (`test_i053_bounded_diplomacy.py:49`, `test_autonomous_concurrency.py:119`) |
| 4 | **Arguments & Cons** — submission, budget, Con limits, sealing, reply | `test_argument_cons.py`, `test_i079_cons_edge_states.py`, `test_i055_sampled_cons.py`, `test_i046_visible_reply.py`, `test_i080_sealed_con_events.py` | Yes. Con semantics span 5 files plus `test_api_cons.py`, `test_t080_*` and `test_i043_con_capacity.py` (player side) |
| 5 | **Adjudication, Effects, Umpire, consequences** | `test_adjudication.py`, `test_umpire.py`, `test_ai_umpire.py`, `test_i065_umpire_provenance_protocol.py`, `test_t074_consequence_management.py`, `test_t082_umpire_effect_discipline.py` | The Umpire prompt contract is in `test_ai_umpire`, `test_t073_*`, `test_t082_*` and `test_i046_*` |
| 6 | **Visibility & auth** | `test_visibility.py`, `test_auth.py`, `test_notes.py` | `TEST-VIS-003` is cited in 6 files (§3) |
| 7 | **HTTP API surface** — endpoints, idempotency, races, body limits | `test_api_app.py`, `test_api_arguments.py`, `test_api_cons.py`, `test_api_context.py`, `test_api_events.py`, `test_api_messages.py`, `test_api_notes.py`, `test_api_ready.py`, `test_idempotency.py`, `test_concurrency.py`, `test_i088_streaming_body_limit.py`, `test_i126_api_helpers.py`, `test_i091_stale_context.py`, `test_i093_i095_i097_boundary_hardening.py` | I-088, I-091, I-093/097 and I-126 each add a file for one boundary |
| 8 | **Resources & research** | `test_resources.py`, `test_api_resources.py`, `test_resource_access_log.py`, `test_scenario_knowledge.py`, `test_i045_resource_activation_correlation.py`, `test_i054_resource_search_replay_cache.py`, `test_i057_batched_research_context_compaction.py`, `test_i098_i099_research_operations_and_result_identity.py`, `test_i100_search_v2.py`, `test_i101_batch_item_outcomes.py`, `test_i112_search_default.py` | **Most spread: 11 files, 7 of them issue-named.** `_search`/`_read` helpers are defined 5 times each |
| 9 | **Player agent** — tools, player, lifecycle, guardrails, single-shot, memory, tool surface | `test_agent_tools.py`, `test_player.py`, `test_lifecycle.py`, `test_guardrails.py`, `test_single_shot.py`, `test_incremental_memory.py`, `test_i033_message_budget.py`, `test_i043_con_capacity.py`, `test_i051_strategic_memory_limit.py`, `test_i052_invalid_tool_call_telemetry.py`, `test_i092_tool_surface.py`, `test_i124_tool_surface_extraction.py`, `test_i063_agent_module_extraction.py` | Yes. `_player` is defined 16 times and `_memory` 10 times across these and group 10 |
| 10 | **Model client & provider accounting** | `test_model_client.py`, `test_i032_inference_profile.py`, `test_i050_token_accounting.py`, `test_i056_request_token_topology.py`, `test_i117_player_cost.py`, `test_i122_provider_response_limit.py` | Yes. One issue file per accounting field |
| 11 | **Autonomous runner, resume, integration games** | `test_ai_smoke.py`, `test_autonomous_concurrency.py`, `test_resume_game.py`, `test_scripted_players.py`, `test_integration_six_turn.py`, `test_integration_api.py`, `test_i081_invocation_owned_provenance.py` | `_coordinator` is defined 8 times and `_factory` 4 times |
| 12 | **Run provenance, export, analysis plan** | `test_export.py`, `test_run_provenance.py`, `test_code_provenance.py`, `test_i047_analysis_plan.py`, `test_i062_summary_split.py`, `test_i078_precommitment.py`, `test_i083_condition_projections.py`, `test_i085_runtime_identity.py`, `test_i086_serialization_boundaries.py`, `test_i120_typed_handoffs.py` | **Yes: 10 files, 7 issue-named.** `_recipe` is defined 6 times |
| 13 | **Benchmark batches & outputs** | `test_benchmark_controls.py`, `test_benchmark_runner.py`, `test_benchmark_smoke.py`, `test_i084_benchmark_output_mechanics.py`, `test_i102_agents_csv_attempt_vs_activation_faults.py`, `test_i105_games_csv_condition_columns.py`, `test_i114_benchmark_consequence_condition.py` | `_offline_batch`, `_read_tables`, `_invoke_benchmark` and `_schedule` are each defined 3 times |
| 14 | **CLI, configuration, launcher, contracts** | `test_cli_run.py`, `test_config.py`, `test_contracts.py`, `test_i061_cli_composition.py`, `test_i096_cli_engine_ownership.py`, `test_i127_typed_scope.py`, `test_unattended_launcher.py` | Yes. A `_cli_capture` harness is re-built in `test_i046_*:898`, `test_t073_*:312`, `test_t074_*:1051` and `test_t075_*:234` |
| 15 | **Packaging, release, public snapshot, docs, harness** | `test_release_package.py`, `test_i087_packaged_runtime_assets.py`, `test_i089_release_readiness.py`, `test_t071_public_source.py`, `test_operational_docs.py`, `test_i103_tool_loop_sibling_race_documented.py`, `test_i123_deployment_envelope.py`, `test_i075_dead_code_leftovers.py`, `test_harness.py` | See §4 |
| — | **Grab-bag** | `test_review_remediation.py`: 14 tests for I-004…I-014, I-027 and I-070, spanning groups 2, 3, 6, 7, 9 and 10 | Each test belongs to another group |

**Crosscut: the condition resolution** (diplomacy, Con and reply modes, consequence mode and
their defaults) is tested in `test_game_loop.py`, `test_config.py`, `test_i083_*`, `test_i118_*`,
`test_i120_*`, `test_cli_run.py` and the condition files `test_i053`, `test_i055` and `test_i046`.
It is the behaviour hardest to find tests for.

---

## 3. Traceability gaps

### 3.1 Release-blocking catalogue IDs (testing.md §5–§17): 65 headed IDs

Every ID has at least one carrying test, found by function name (`test_arg_005_…` → `TEST-ARG-005`)
or by a literal in the docstring or comments. Two are weak:

| ID | Carried by | Issue |
|---|---|---|
| `TEST-ADJ-008` | `test_ai_umpire.py:356` `test_adj_007_008_009_authority_first_policy_is_versioned` only | Only in a combined name. The test asserts phrases in the **v1.3** prompt only and pins `UMPIRE_PROMPT_VERSION == "umpire-v1.3"`. v1.4–v1.6, which are selected under `visible_reply`, rules and Effect discipline, all contain the 7 phrases today (checked with grep), but no test keeps them there. ADJ-007/008 are LLM-policy IDs, so a prompt check is the right kind of test; it should cover every shipped Umpire prompt |
| `TEST-RNG-003` | `test_ai_umpire.py:21` (module docstring) and `:1072` (comment) | Not in any test name. The property is proven by `test_outage_pauses_game_with_no_draw_record_or_effect` (`:1063`) and the parity tests `test_adj_00N_parity_*`, which never let the Umpire pass a roll |

The un-numbered acceptance sections are carried as follows:
- §18 → `test_integration_six_turn.py` (7 `test_int_18_*`).
- §19 → `test_integration_api.py` (10 `test_int_api_19_*`).
- §20 → `test_ai_smoke.py` (`llm`, 2 params) plus 3 offline coordinator tests.
- §21 → `test_scenario_knowledge.py` (`llm` live plus offline).
- §22 → `test_benchmark_smoke.py::test_t060_001` (offline) and `::test_t060_002` (`llm`).

### 3.2 Range-declared IDs (testing.md §26–§29)

| Range | Missing |
|---|---|
| `TEST-I053-001…012` (§26) | **`TEST-I053-008`**: configuration, manifest, AgentRun, export and summary round-trip (`dev/archive/tasks/I-053-*.md:55`). **`TEST-I053-012`**: a failed or stalled activation does not complete or close a bounded round (`:59`). Neither ID appears in any test. The nearest test, `test_i081_002_diplomacy_round_activations_are_persisted_before_settlement_failure` (`test_i081_invocation_owned_provenance.py:161`), proves that AgentRuns persist when settlement fails, not that no round is charged |
| `TEST-I055-001…010` (§27) | none. All ten carried, 002 and 010 by name only |
| `TEST-I047-001…009` (§28) | none |
| `TEST-I046-001…015` (§29) | none. Sub-IDs `001b`, `007b`, `009b/c`, `011b–e` and `012a` are not declared in §29 |

### 3.3 Cited IDs that do not exist

Of the 173 `TEST-*` IDs cited in `tests/` that are not in `docs/testing.md`:

- **8 are defined nowhere in the repository:**
  - `TEST-I055-007b` (`test_i055_sampled_cons.py:522`).
  - `TEST-I118-004…010`. `dev/BACKLOG_RESOLVED.md:128` names only the range `001…010`, and the
    brief (`dev/archive/tasks/I-118-*.md:67`) lists only `TEST-I118-001…`.
- **164 are defined only under `dev/`** (task briefs and the resolved log), for example
  `TEST-I092-*`, `TEST-T080-*` and `TEST-I085-*`. 20 test files point readers at `dev/…` paths,
  43 references in all. In the public snapshot (D-083) none of these IDs or paths can be
  resolved. Two ways to fix it: state in `docs/testing.md` (§1 or a new short section) that
  issue- and task-scoped IDs are maintainer traceability and not a public contract, or move their
  one-line definitions into a public index. **Adding `testing.md` IDs is a spec change and needs
  its own commit.**
- **1 false positive:** `TEST-AUTH-00` is the prefix `TEST-AUTH-00x` inside `docs/testing.md`.

---

## 4. Tests that check document wording

The table covers every test the audit run saw opening a `.md` file, excluding:
- scenario Resource files, which are game content;
- the 90 tests whose `code_version` worktree hash reads every tracked file (§6.2).

Prompt files under `docs/prompts/` are model input, loaded and hashed at runtime, so a phrase
assertion on a prompt guards behaviour. They are listed together at the end.

| Test | Reads | Asserts on | Class |
|---|---|---|---|
| `test_operational_docs.py:18` `test_t_064_…cover_release_workflows` | README, guide/*.md | env-var names and CLI commands (contract); `"administrative"`, `"private"`, `"deferred"`, `"never aborts the batch"`, `"GitHub issue"`, `"normative"` and `len(development) < 100` lines (prose) | **Mixed. Loosen:** keep the env-var and command checks, drop the prose and line-count asserts |
| `test_operational_docs.py:60` `…local_links_resolve` | README, guide/*.md | relative links exist | **Keep** (link integrity) |
| `test_i089_release_readiness.py:37` `test_i089_001` | LICENSE, README, pyproject | MIT licence text and SPDX tag | **Keep** (licence declaration) — *currently excluded from the snapshot* |
| `test_i089_release_readiness.py:60` `test_i089_002` | docs/README.md, docs/prompts/README.md | the index lists every prompt file and marks exactly the loaded versions current | **Keep** (index ↔ code) — *excluded from the snapshot* |
| `test_i089_release_readiness.py:81` `test_i089_003` | scenarios/README.md | every package is indexed; hard-coded list of 4 package names | **Keep; loosen** the hard-coded list to "every directory is indexed" — *excluded from the snapshot* |
| `test_i089_release_readiness.py:96` `test_i089_004` | README, SECURITY, app/README, docs/README, **dev/README.md** | file existence and `app/README` naming every module (contract); `"## Contributing"`, `"unfiltered administrative record"` and `"## Read in this order"` (prose); `dev/README.md` exists (dev) | **Mixed:** keep the module-index check, drop the headings and phrases, guard the dev/ part |
| `test_i089_release_readiness.py:138` `test_i089_005` | `git ls-files dev/runs`, dev/RUNS.md | the retained run set | **dev/** (maintainer-only) |
| `test_i102_*.py:203` `test_i102_004` | docs/benchmark-methodology.md | the §25 `agents.csv` list contains the column in `_TABLE_COLUMNS` | **Keep** (doc ↔ code). Better as one test: the §25 lists equal `_TABLE_COLUMNS` for every table |
| `test_i105_*.py:108` `test_i105_003` | same | the same for `games.csv` | **Keep**, merged with the above |
| `test_i103_*.py:28` `…agents_md_states_the_sibling_dispatch_property` | docs/agents.md §5 | `"D-082"`, `"sibling"`, `"concurrently"`, `"both surfaces"` | **Prose. Drop** (the property is documentation, not code) — *excluded from the snapshot* |
| `test_i103_*.py:37` `…decision_log_records_d082` | dev/DECISIONS.md | `"### D-082"` | **dev/. Drop** (it pins a ledger entry) |
| `test_i123_*.py:30` `test_i123_001_trd_states_the_deployment_envelope` | TRD §26 | 9 phrases, e.g. `"not a configuration change"` | **Prose. Drop or loosen** to `"D-100"` plus the section heading. `test_i123_002` (`:65`) already guards the code side (SQLite, `BEGIN IMMEDIATE`) |
| `test_i123_*.py:46` `…races_section_points_at_the_envelope` | TRD §17 | `"§26.1"` | **Prose. Drop** |
| `test_i123_*.py:57` `…decision_log_records_d100` | dev/DECISIONS.md | `"### D-100"` | **dev/** (already `skipif` without dev/) |
| `test_orison_scenario.py:62` `test_t_065_readme_…first_run` | scenarios/orison-crisis/README.md | the run command (contract); `"six turns"`, `"diplomacy"`, `"argument"` and actor names (prose) | **Mixed. Loosen** to the command. `test_t083_003` shows the stronger form: it parses the README command through the CLI |
| `test_t083_*.py:95` `test_t083_003_readme_command_parses_…` | scenarios/skaro-crisis/README.md | the README's bash block runs through `amaw_cli` with the intended conditions | **Keep** (model test) |
| `test_t075_*.py:106` `test_t075_001` | scenarios/README.md | the scenario ID is indexed | **Keep** (index ↔ packages) |
| `test_release_package.py:14` `test_t066_release_inventory_and_version` | pyproject, .env.example | inventory and metadata | **Keep** — *excluded from the snapshot* |
| `test_release_package.py:40` `test_t066_quick_start_and_export_guide_…` | README, guide/operations.md | the `export_format_version` string equals `EXPORT_FORMAT_VERSION`; token field names | **Keep** (doc ↔ code) — *excluded from the snapshot* |
| `test_release_package.py:59` `test_t066_superseded_guides_are_archived…` | dev/archive/docs/ | an archived directory exists | **dev/. Drop** (pins archive housekeeping) |
| `test_release_package.py:66` `test_i059_docs_example_yaml_is_in_the_sdist` | MANIFEST.in | the sdist includes docs/*.yaml | **Keep** — *excluded from the snapshot* |
| `test_i075_*.py:28` `test_i075_002_no_resolved_issue_briefs_left_active` | dev/BACKLOG_RESOLVED.md, dev/tasks/ | no resolved brief left in dev/tasks | **dev/** (process hygiene, maintainer-only) |
| `test_t071_public_source.py:75` `test_i133_002_public_markdown_links_resolve` | the built snapshot's *.md | relative links resolve inside the snapshot | **Keep** |
| Prompt readers: `test_ai_umpire.py:356`, `test_i046_visible_reply.py:556` (`i046_014`) and `:1002`, `test_single_shot.py:354`, `test_t073_scenario_rules.py:287`, `test_t082_*.py:76`, `test_i087_*` | docs/prompts, amaw_resources/prompts | the loader returns the file; packaged copies equal the docs copy; v1.6 = v1.5 + one section; player-v1.12/13 disclose Con visibility | **Keep** (the prompt is runtime input and hashed provenance). See §7 for their cwd-relative paths |

**Snapshot consequence.** `MAINTAINER_ONLY_TESTS` (`scripts/build_public_source.py:40-45`)
excludes 4 whole files, 13 tests in all, matching 1476 − 1462 − 1 (the `test_i123` skip). Only
5 of those 13 tests read `dev/`: `i075_002`, `i089_004`, `i089_005`, `i103 …d082` and
`t066_superseded`. The other 8 would pass in the snapshot but are lost there:
- `i075_001`;
- `i089_001`, `i089_002` and `i089_003`;
- `i103 …agents_md`;
- `t066_release_inventory`, `t066_quick_start` and `i059_…sdist`.

`test_i123` already uses the per-test pattern (`@pytest.mark.skipif(not (ROOT / "dev").is_dir())`,
`test_i123_deployment_envelope.py:55`), which makes whole-file exclusion unnecessary.

*Side note:* `dev/plans/2026-09-24-structural-hotspot-plan.md:17` says the subprocess
import-boundary test is in `test_release_package.py`. It is not. The subprocess checks are in
`test_api_app.py:146`, `test_export.py:398`, `test_umpire.py:92`, `test_ai_umpire.py:1119`,
`test_benchmark_runner.py:697` and `test_t074_consequence_management.py:1164`, all of which run
in the snapshot.

---

## 5. Duplication

Each pair below was read by hand. Line ranges are inclusive.

**Exact or subsumed tests**

1. `test_i079_cons_edge_states.py:25-32` `test_i079_001_an_unsupported_budget_is_rejected_at_load`
   (budgets 4 and 0) and `test_t080_argument_rounds.py:134-139`
   `test_t080_001_budgets_outside_one_to_three_are_rejected` (0, 4 and 10). The bodies are
   identical (Jaccard 1.00) and the second is a superset. *ID mapping:* delete the I-079 test and
   add `TEST-I079-001` to the T-080 test's docstring.
2. `test_cli_run.py:333-369` `test_cli_run_writes_best_effort_partial_export_after_failure` and
   `test_i093_i095_i097_boundary_hardening.py:392-428`
   `test_i097_002_best_effort_partial_export_goes_through_the_atomic_writer`. They use the same
   fakes (`FakeUmpire`, `FailingRunner`, the same game UUID) and the same `expected` and
   `export_path` assertions. The I-097 test adds the writer spy; the CLI test adds only the
   `"autonomous run for game … failed"` output check. *Mapping:* move that one assertion into
   `test_i097_002` and delete the CLI copy.

**Same assertions over the same game, one layer apart (§18 vs §19)**

3. `test_integration_api.py` replays the §18 golden checks after driving the Actor half over HTTP:
   - `:287-305` ↔ `test_integration_six_turn.py:100-118` (18 adjudications, 3/3 assertions shared);
   - `:311-337` ↔ `:124-152` (RNG sequence, 3/3);
   - `:343-373` ↔ `:158-195` (final Effects, 4/4);
   - `:268-281` ↔ `:79-94` (Arguments and budget, 3/4);
   - `:509-559` ↔ `:266-299` (event structure, 11 of 15).

   The §19 re-check is intentional (`test_integration_api.py:24-26`: "reproduces the
   18-adjudication schedule"), so keep the property. Move the golden assertions into one shared
   helper (e.g. `tests/golden.py::assert_matches_fixture(engine, game_id)`) that both files call
   once. **Separately, each of the 17 tests replays the whole game**
   (`_run_fixture_game`/the HTTP driver per test). One module-scoped play per file would do; §6.3.

**Copied boilerplate that should be one parametrised test or helper**

4. **Import-boundary checks, 12 copies.**
   - AST walkers, 3 near-identical at Jaccard 0.74–0.90: `test_lifecycle.py:313`,
     `test_player.py:464`, `test_ai_smoke.py:314`; plus `test_agent_tools.py:247`,
     `test_i062_*.py:55` and `test_i064_*.py:61`.
   - Clean-interpreter subprocess checks, near-identical at Jaccard 0.66: `test_api_app.py:146`,
     `test_export.py:398`, `test_umpire.py:92`, `test_ai_umpire.py:1119`,
     `test_benchmark_runner.py:697` and `test_t074_*.py:1164`.

   One `tests/test_architecture_boundaries.py` with a table (module → forbidden imports) and one
   subprocess that imports the whole `app` package would replace them. It keeps AGENTS.md §6.1 in
   one place, and the named IDs (`TEST-T054-005`, `TEST-T074-010`, `TEST-I062-002`,
   `TEST-I064-003`) become parametrisation IDs.
5. **Identical helpers** (byte-identical after `ast.unparse`):
   - `_bearer` ×4 (`test_auth.py:42`, `test_export.py:63`, `test_resume_game.py:27`,
     `test_scenario_knowledge.py:33`), identical to `api_helpers.bearer`;
   - `_actor` ×7, 6 identical (`test_adjudication.py:36`, `test_argument_cons.py:28`,
     `test_concurrency.py:42`, `test_events.py:38`, `test_idempotency.py:33`,
     `test_sequencing.py:30`);
   - `_count` ×3 identical (`test_concurrency.py:81`, `test_idempotency.py:73`,
     `test_scripted_players.py:46`);
   - `_invoke` ×3 identical (`test_i046_*:936`, `test_t073_*:340`, `test_t074_*:1085`).

   Near-identical: `_cli_capture` ×4, `_offline_batch` ×3 (`test_benchmark_controls.py:138`,
   `test_benchmark_runner.py:262`, `test_i114_*:60`), `_read_tables` ×3 and `_invoke_benchmark` ×3.

**Shared post-conditions (not duplicates, candidates for a helper)**

6. `test_ai_umpire.py:958` (`i074_004`, semantic exhaustion) and `:1063` (provider outage) reach
   the same paused state by different triggers and share 5 assertions: paused, phase kept,
   `rng_draw_index == 0`, no record, no Effects, `game_paused` event. Keep both. An
   `_assert_paused_unresolved(engine, game_id, argument_id)` helper would stop them drifting.

**Not duplication.** `test_argument_cons.py` and `test_api_arguments.py` share the names
`test_arg_001_active_actor_can_submit` and `test_arg_003_wrong_phase_rejected`, and
`test_argument_cons.py` and `test_api_cons.py` carry the same `TEST-CON-*`. These are the service
layer and the HTTP layer (testing.md §2 layers 1 and 3): one asserts the domain exception and the
persisted rows, the other the status code and error envelope. Keep both.
`test_cons_concurrency_below_one_rejected` also exists in two places (`test_autonomous_concurrency.py:733`,
`test_config.py:117`), for the runner and the settings respectively.

---

## 6. Speed

### 6.1 `uv run pytest --durations=40 -q` (the real output, trimmed to the table)

```text
============================= slowest 40 durations =============================
15.60s call     tests/test_benchmark_controls.py::test_t056_003_model_batch_declares_and_verifies_held_constants
13.22s call     tests/test_autonomous_concurrency.py::test_i028_002_diplomacy_concurrency_respects_bound
9.57s call     tests/test_benchmark_smoke.py::test_t060_001_offline_batch_smoke_walks_the_section_22_checklist
8.93s call     tests/test_autonomous_concurrency.py::test_i042_diplomacy_concurrency_one_uses_affine_workers
8.52s call     tests/test_autonomous_concurrency.py::test_i028_008_diplomacy_provenance_once_in_sweep_order
7.75s call     tests/test_benchmark_controls.py::test_t056_003_held_constant_drift_is_an_integrity_failure_in_model_mode
7.72s call     tests/test_autonomous_concurrency.py::test_i028_006_diplomacy_concurrency_completes_with_same_shape_as_serial
7.64s call     tests/test_benchmark_controls.py::test_t056_002_umpire_drift_stops_the_batch_and_keeps_the_denominator[agent]
7.61s call     tests/test_benchmark_controls.py::test_t056_002_umpire_drift_stops_the_batch_and_keeps_the_denominator[model]
7.59s call     tests/test_benchmark_controls.py::test_t056_001_umpire_condition_is_sealed_and_matches_every_clean_game
7.56s call     tests/test_benchmark_controls.py::test_t055_003_cyclic_batch_records_scheduled_assignment_in_every_game
7.28s call     tests/test_autonomous_concurrency.py::test_cons_concurrency_completes_game_with_same_shape_as_serial
7.26s call     tests/test_autonomous_concurrency.py::test_i028_011_manifest_records_both_concurrency_bounds
6.85s call     tests/test_benchmark_runner.py::test_t054_005_cli_runs_a_batch_end_to_end_without_manual_setup
6.71s call     tests/test_benchmark_controls.py::test_t057_001_benchmark_mode_is_persisted_everywhere[model]
6.68s call     tests/test_benchmark_runner.py::test_t054_004_run_class_and_sealed_plan_reach_every_game
6.66s call     tests/test_benchmark_runner.py::test_t054_003_offline_batch_runs_end_to_end
6.51s call     tests/test_benchmark_controls.py::test_t057_001_benchmark_mode_is_persisted_everywhere[agent]
6.51s call     tests/test_benchmark_controls.py::test_i078_002_planned_and_observed_attempt_are_reconciled[seed-<lambda>-None-seed]
6.51s call     tests/test_benchmark_controls.py::test_t056_003_agent_mode_records_but_does_not_hold_player_constants
6.50s call     tests/test_i114_benchmark_consequence_condition.py::test_i114_001_bounded_batch_seals_and_verifies_the_resolver[agent]
6.47s call     tests/test_benchmark_controls.py::test_i078_003_agent_mode_holds_the_common_condition
6.44s call     tests/test_i114_benchmark_consequence_condition.py::test_i114_003_none_batch_record_is_unchanged
6.40s call     tests/test_i114_benchmark_consequence_condition.py::test_i114_002_resolver_drift_stops_the_batch_and_keeps_the_denominator[model]
6.34s call     tests/test_i114_benchmark_consequence_condition.py::test_i114_002_resolver_drift_stops_the_batch_and_keeps_the_denominator[agent]
6.30s call     tests/test_benchmark_controls.py::test_i078_002_planned_and_observed_attempt_are_reconciled[missing_manifest-None-_strip_manifest-run_manifest]
6.27s call     tests/test_benchmark_controls.py::test_i078_002_planned_and_observed_attempt_are_reconciled[assignment-_swap_assignment-None-player.assignments]
6.22s call     tests/test_i055_sampled_cons.py::test_i055_007_resume_continues_sealed_schedule_and_refuses_mismatch
6.19s call     tests/test_i085_runtime_identity.py::test_i085_004_environment_drift_stops_the_batch_in_both_modes[agent]
6.12s call     tests/test_i114_benchmark_consequence_condition.py::test_i114_001_bounded_batch_seals_and_verifies_the_resolver[model]
6.11s call     tests/test_autonomous_concurrency.py::test_cons_activations_actually_overlap
6.04s call     tests/test_i085_runtime_identity.py::test_i085_004_environment_drift_stops_the_batch_in_both_modes[model]
6.03s call     tests/test_benchmark_controls.py::test_i078_008_tables_carry_observed_seed_and_assignment_beside_the_planned
5.62s call     tests/test_ai_smoke.py::test_autonomous_coordinator_completes_six_turns_offline
4.74s call     tests/test_autonomous_concurrency.py::test_i053_003_bounded_n_never_exceeds_complete_round_limit
4.62s call     tests/test_autonomous_concurrency.py::test_i053_002_bounded_one_runs_one_complete_round_per_turn[1]
4.58s call     tests/test_i046_visible_reply.py::test_i046_012_runner_spends_exactly_one_owner_activation_per_contested_argument[tool_loop]
4.50s call     tests/test_resume_game.py::test_i048_002_003_004_resume_reobserves_existing_game_after_committed_mutations[adjudication_commit]
4.44s call     tests/test_autonomous_concurrency.py::test_serial_sweep_is_the_default_shape_for_cons_concurrency_one
4.37s call     tests/test_autonomous_concurrency.py::test_every_con_activation_runs_on_the_pool_when_concurrent
1476 passed, 8 deselected, 2 warnings in 502.08s (0:08:22)
```

### 6.2 Time per file (audit run: 1476 passed in 449 s; protocol time per test summed)

| s | % | tests | git subprocs | file |
|---:|---:|---:|---:|---|
| 96.6 | 21.6 | 39 | 54 | `test_benchmark_controls.py` |
| 87.6 | 19.6 | 25 | 150 | `test_autonomous_concurrency.py` |
| 31.4 | 7.0 | 10 | 19 | `test_i114_benchmark_consequence_condition.py` |
| 25.8 | 5.8 | 23 | 61 | `test_benchmark_runner.py` |
| 17.0 | 3.8 | 32 | 24 | `test_i046_visible_reply.py` |
| 14.2 | 3.2 | 10 | 123 | `test_i085_runtime_identity.py` |
| 13.5 | 3.0 | 11 | 42 | `test_i055_sampled_cons.py` |
| 12.4 | 2.8 | 5 | 24 | `test_ai_smoke.py` |
| 12.0 | 2.7 | 45 | 7 | `test_t074_consequence_management.py` |
| 11.4 | 2.6 | 8 | 54 | `test_resume_game.py` |
| 11.3 | 2.5 | 32 | 36 | `test_i047_analysis_plan.py` |
| 9.8 | 2.2 | 10 | 0 | `test_integration_api.py` |
| 9.5 | 2.1 | 1 | 24 | `test_benchmark_smoke.py` |
| 8.4 | 1.9 | 30 | 18 | `test_single_shot.py` |
| 6.8 | 1.5 | 20 | 12 | `test_t080_argument_rounds.py` |
| 4.8 | 1.1 | 4 | 7 | `test_i087_packaged_runtime_assets.py` |
| 3.9 | 0.9 | 28 | 126 | `test_i083_condition_projections.py` |
| 2.6 | 0.6 | 7 | 0 | `test_integration_six_turn.py` |
| 2.1 | 0.5 | 8 | 158 | `test_code_provenance.py` |

These 19 files take 381 s; the other 101 files take about 66 s together.

| Duration bucket | Tests | Total |
|---|---:|---:|
| < 0.1 s | 1,189 | 33 s |
| 0.1–1 s | 203 | 54 s |
| 1–3 s | 17 | 29 s |
| ≥ 3 s | **67** | **332 s (74%)** |

### 6.3 What makes them slow

- **Full autonomous games that only feed bookkeeping assertions** (the dominant cost).
  - `_offline_batch` (`test_benchmark_controls.py:136-235`) runs a real `run_batch` of 2–4 full
    six-turn games of the canonical fixture. It uses `tool_loop` `ReferencePlayer`s on
    `FunctionModel`s through a `TestClient`.
  - The profile of `test_t056_003_model_batch_declares_and_verifies_held_constants` (4 games):
    2,248 player HTTP requests (`agents/tools.py:227`), 288 activations, 168 sweeps, 404 admin
    requests. `run_batch` accounts for 23.6 s of cumulative time under the profiler.
  - Its assertions (`:548-570`) read `batch.json` and each export's `run_manifest` and never
    depend on turns 2–6.
  - The same pattern drives `test_benchmark_runner.py`, `test_i114_*`, `test_i085_004`,
    `test_benchmark_smoke.py` and most of `test_autonomous_concurrency.py`, which plays a full
    game per test to check a concurrency bound, a manifest field or worker affinity.
- **Repeated identical plays.**
  - The 7 §18 tests each call `_run_fixture_game` (`test_integration_six_turn.py:53`).
  - The 10 §19 tests each drive the HTTP game, and `deterministic_across_runs` drives it twice.
- **Git subprocesses from `code_version`.**
  - 1,102 of the 1,120 subprocesses the suite spawns are `git`: `rev-parse`, `diff-index`,
    `ls-files --stage` and `ls-files --others`.
  - 160 full worktree hashes at about 86 ms each (measured: `code_version()` averages 0.086 s)
    come to **about 14 s**. Real, but not the main cost.
  - These hashes read every tracked file, including `dev/`, `AGENTS.md` and `.claude/`. The
    snapshot needs a root commit because of this (I-133: "without a `HEAD` … fails 88 runner
    tests").
- **Not a cause.** The audit hook recorded no `time.sleep` event during the run. The source
  has 3 calls of ≤ 20 ms (`test_autonomous_concurrency.py:384`, `:590`;
  `test_model_client.py:232`). Scenario loading is
  cheap: 121 `load_scenario` call sites, but files under 0.1 s dominate the count.
- **Real non-git subprocesses are few:** 8 `python -c`, 3 `python -m`, 1 `uv build` and
  1 `uv pip`. The 4 `test_i087` tests take 4.8 s.

### 6.4 Cheaper equivalents (estimates, not measured)

1. **A derived short-game fixture for bookkeeping tests.** Generate a copy of
   `tests/fixtures/deterministic-game/` with `max_turns: 1` (or 2) into a session-scoped
   `tmp_path_factory` directory. `test_t080_*` already mutates a scenario text this way
   (`_mutate`). **The canonical fixture is not edited** and §18/§19 keep playing six turns. Use
   the short fixture in `_offline_batch`, `test_benchmark_runner`, `test_i114`, `test_i085_004`
   and the `test_autonomous_concurrency` tests whose assertions are per-activation or
   per-manifest.
   - *Payoff:* these files take about 255 s. Cutting play from 6 turns to 1–2 should save about
     **2–3 min**.
   - *Risk:* a test that counts per-turn totals (for example `completed_games`, round counts per
     turn) needs its expected numbers checked. The scenario hash differs from the canonical one
     but is constant within a batch.
2. **Play once per module for §18/§19.** Module-scoped fixtures return the finished `(engine,
   game_id)`; the tests only read. *Payoff:* about 8 s. *Risk:* the tests must stay read-only.
   `test_int_api_19_deterministic_across_runs` keeps its own two plays.
3. **A `slow` marker** on the 67 tests of 3 s or more, registered beside `llm` in
   `pyproject.toml:77`. The default run stays the full suite; `-m "not slow and not llm"` gives
   an inner loop of about 1,409 tests in about 2 min. CI (`.github/workflows/ci.yml:26`) is
   unchanged.
4. **Batch-bookkeeping tests with a canned executor.** Where a test checks only drift detection
   or denominators (`t056_002`, `i078_002`, `i114_002`), `execute(attempt)` could return a stored
   export with the manifest mutated. At least one end-to-end batch test stays real (`t054_003`,
   `t060_001`).
5. **Not recommended now:** `pytest-xdist`. It is not in `uv.lock`. It would be a new dependency
   and would need the SQLite-per-test isolation re-checked; 1–3 above remove most of the cost
   without it.

---

## 7. Dead or weak tests

| Where | Problem | Class |
|---|---|---|
| `test_model_client.py:145` `test_transient_5xx_status_is_retried_once` | `assert result.ok if hasattr(result, "ok") else True`. `ModelResult` (`agents/model_client.py:128-144`) has no `ok`, so **the assertion is always true**. The test still checks `calls["n"] == 2` but not `result.request_count == 2` or the completion | **Vacuous assertion.** Replace it with `result.request_count == 2` |
| `test_i046_visible_reply.py:558`, `:1010`; `test_single_shot.py:365`; `test_t073_scenario_rules.py:291`; `test_t082_*.py:78`, `:80`; `test_i096_*.py:54` | `Path("docs/prompts/…")` and `Path("scenarios/…")` are relative to the **cwd**. **Checked:** `cd tests && uv run pytest test_t082_umpire_effect_discipline.py::test_t082_001_…` → `1 failed`. From the repository root they pass | **Fragile.** Anchor on `Path(__file__).resolve().parents[1]` |
| `test_i063_agent_module_extraction.py:122-131` `test_i063_004_dead_symbols_are_gone`; `test_i075_dead_code_leftovers.py:24` `test_i075_001`; `test_i062_summary_split.py:52` (`hasattr` half); `test_i081_*.py:311-312`; `test_i078_precommitment.py:116`; `test_persistence.py:202`; `test_i063_*.py:70`, `:106` | "Tombstone" asserts that a removed name (`DiplomacyMode`, `TOOL_SCHEMA_VERSION`, `drain_provenance`, `set_require_run_manifest`, …) stays absent. They protect against a re-add that nothing suggests. They never fail on a behaviour regression | **Weak.** Drop them, or keep only those that guard a real compatibility-shim risk (the I-062 "no re-export" check is the one with a stated reason) |
| `test_i063_*.py:76` `test_i063_002_code_version_module_owns_worktree_hashing` | Asserts the **exact set** of names defined in `agents/code_version.py`, including 8 private helpers (`_describe`, `_uv_lock_sha256`, …). Any private refactor fails it | **Tests private structure.** Loosen to the public names (`code_version`, `runtime_fingerprint`, `CodeVersionError`) and the import-direction check |
| `test_i062_summary_split.py:61` `test_i062_003_module_docstrings_are_short` | Module docstrings of 5 modules are at most 15 lines | **Style check**, not behaviour. Move it to a linter or drop it |
| `test_argument_cons.py:607` `test_con_completion_survives_fresh_engine_reload` | Only asserts `completed is not None` after reload. It would pass if the row were reloaded with the wrong kind (Con vs pass) | **Weak.** Also assert the recorded participation kind |
| `test_game_loop.py:61` `test_every_experimental_mode_validates_with_its_bound` | No assertion; it passes by not raising | **Acceptable.** It is the positive half of `test_pairing_rules_reject_each_invalid_combination` (`:56`) |
| `test_models.py:232`, `:258` | No `assert`; the check is in `_rejects_incomplete` (`pytest.raises`) | Fine |
| `test_t074_*.py:262-420` (11 tests) | No `assert` in the body; they delegate to `_assert_rejected_and_paused` | Fine |

**Permanently skipped:** none. The 8 `llm` tests are deselected by `addopts` (by design,
testing.md §2) and each also skips when there is no key. The only other skip is `test_i123:55`
when `dev/` is absent.

**Private helpers that no longer exist:** none. Every private production symbol the tests reach
(`game_service._expire_effects`, `_close_cons_if_complete`, `_build_umpire_context`,
`_start_turn`, `_release_injects`, `runner._write_attempt`, `_unrecoverable_key`,
`_admin_request`, `amaw_cli._resolve_scenario`, `benchmark.outputs._cell`, `_TABLE_COLUMNS`)
exists, which the green run proves. The coupling is still worth knowing about when those modules
are refactored.

**Shadowed tests:** none. No module defines the same test name twice (checked with `ast`).

---

## 8. Fixtures

**Shared fixtures today**
- `tests/conftest.py` is 54 lines: `_hermetic_env` (autouse, removes the key for non-`llm`
  tests), `db_path` and `db_engine` (a fresh file-backed SQLite per test through
  `app.database`). It is small and correct, and `test_harness.py` pins it.
- `tests/api_helpers.py` (`seed`, `client`, `bearer`, `start_to_*` and the canonical
  `FIXTURE_DIR`) is imported by many files.
- `tests/agent_fakes.py` supplies offline PydanticAI models.
- There are no session-scoped fixtures. The only non-function scope is `test_i087_*.py:42`
  (module).

**Duplicated setup that belongs in a fixture or helper**
- **The canonical fixture path is re-derived in 28 files** (for example
  `test_integration_six_turn.py:50`, `test_scenarios.py`, `test_resume_game.py`,
  `test_t081_*.py`) instead of importing `api_helpers.FIXTURE_DIR`. It is harmless, but a move
  of the fixture would touch 29 files.
- **"Seed a game at a phase" is written per file.** `_running_game` ×7 and `_adjudicating_game` ×4
  (the four below), plus row builders such as `_seed_game` (`test_argument_cons.py:38`) and
  `_game(engine, **options)` (`test_i078_precommitment.py:33`). A further 9 files define a
  `_game(engine, game_id)` that only loads the `Game` row; that could be one shared helper.
  `_adjudicating_game` is at `test_adjudication.py:85`, `test_ai_umpire.py:155`,
  `test_i065_*:70` and `test_i081_*:365`. Most phase seeders build rows directly
  instead of driving `game_service`. A `game_at(phase, …)` factory fixture in `conftest.py`,
  next to `api_helpers.start_to_*`, would remove most of them. The row-building variants in the
  service-layer tests (`test_argument_cons.py:79`, `test_concurrency.py:74`) are deliberate: they
  set exact states the service would not reach in one step. Leave those.
- **Player and coordinator builders:** `_player` ×16, `_memory` ×10 and `_coordinator` ×8 each
  differ slightly per file (activation mode, fake model). A `make_player(activation_mode=…,
  model=…)` factory in `agent_fakes.py` would absorb most of them.
- **Benchmark harness:** `_offline_batch` ×3, `_read_tables` ×3, `_invoke_benchmark` ×3 and
  `_schedule` ×3. These should be one `tests/benchmark_helpers.py`, which is also the natural
  home for the short-game fixture in §6.4.
- **CLI capture:** `_cli_capture` ×4 and `_invoke` ×3 identical, as above.

**The canonical `tests/fixtures/deterministic-game/`** stays untouched. `tests/fixtures/README.md`
says "Treat a change here as a change to a golden file", and nothing in this review requires a
change. The short-game proposal (§6.4) *derives* a temporary copy at session scope. The other
fixtures are `knowledge-scenario/` (§21, not golden) and `i120/` (4 JSON hand-off baselines).

---

## 9. Recommended restructuring, in order

Every step must leave:
- `uv run pytest` green;
- every `TEST-*` ID findable by `grep`, in the test name or docstring;
- the public snapshot green, verified by building it with `scripts/build_public_source.py`, making
  the root commit (`PUBLIC_SOURCE.md`) and running `uv run pytest` there.

| # | Step (backlog) | Payoff | Risk to the public snapshot |
|---|---|---|---|
| 1 | **Fix the fragile and vacuous tests (I-139):** anchor the 7 cwd-relative paths, replace the vacuous assert, strengthen the reload assert, drop the tombstones, loosen `i063_002`, drop `i062_003` | 0 files removed, ~0 s. Removes one false-pass and one cwd trap. About 10 tests fewer (tombstones) | None. All of these are public files and none reads `dev/` |
| 2 | **Close the traceability gaps (I-136):** add or rename tests for `TEST-I053-008`/`-012`; cover `TEST-ADJ-007/008` for every shipped Umpire prompt; name a `TEST-RNG-003` test; resolve `I055-007b` and `I118-004…010`; decide where task-scoped IDs are defined for public readers (a `docs/testing.md` note is a spec change and needs its own commit) | Every cited ID resolves | Low. Any doc change must not break `test_i133_002` (links) |
| 3 | **Doc-wording tests and a per-test `dev/` guard (I-137):** drop or loosen the 6 prose tests (§4); give the 5 `dev/`-reading tests the `test_i123:55` `skipif`, or drop them; then empty `MAINTAINER_ONLY_TESTS` | 1–2 files removed (`test_i103_*`, perhaps `test_operational_docs` folded into `test_release_package`). The snapshot **gains 8 tests** (1462 → 1470 passing, skips 1 → about 4) | **Medium.** This changes what the snapshot runs. Build the snapshot and run it before merging. `test_t071_001` asserts the snapshot's content set and must stay true |
| 4 | **Remove duplication (I-138):** delete `i079_001` (ID → `t080_001` docstring), fold the CLI partial-export test into `i097_002`, one `test_architecture_boundaries.py` for the 12 checks (IDs become parametrisation IDs), a shared §18/§19 golden-assert helper, shared helpers in `api_helpers.py`/`conftest.py`/`agent_fakes.py`, `FIXTURE_DIR` imported everywhere | About 500–800 lines removed (estimate); 0–1 files net. Seconds only | Low. New helper modules sit in `tests/`, which is public. The import-boundary merge must keep the clean-interpreter subprocess form (D-017, `test_api_app.py:147-152`) |
| 5 | **Speed (I-135):** derived short-game fixture for bookkeeping tests; play once per module for §18/§19; `slow` marker; optionally a canned executor for drift tests | **About 2.5–3.5 min saved** of the 8.4 min (§6.4, estimate); a `-m "not slow"` inner loop of about 2 min | Low. The fixture is generated in `tmp_path`, not shipped. The snapshot's `pyproject.toml` gains the marker registration |
| 6 | **Regroup the issue- and task-named files by behaviour (I-140), optional:** fold the 60 `test_iNNN`/`test_tNNN` files into the §2 groups, e.g. `test_research_*.py` for group 8 and `test_provenance_*.py` for group 12, and split up `test_review_remediation.py` | About 60 → about 20 files; tests become findable by behaviour. No speed change | **Medium.** Update `MAINTAINER_ONLY_TESTS` paths if any remain; `test_t071_001` names `tests/test_integration_six_turn.py`; `test_release_package` names the same. Use `git mv`, never rename a test ID, keep each `TEST-*` in the moved test's name or docstring. Best done after 4 so less code moves |

Steps 1–4 are cheap and independent. Step 5 has the only large payoff. Step 6 is churn with a
navigation payoff; do it only if the owner wants the file layout to read by behaviour.

---

## 10. Backlog items filed

- **I-134**: this review (umbrella; closes when I-135…I-140 are resolved or consciously dropped).
- **I-135**: speed: short-game fixture, play-once §18/§19, `slow` marker. *severity: medium*
- **I-136**: traceability gaps (I053-008/012, ADJ-007/008 across prompts, RNG-003, undefined and
  dev-only IDs). *severity: low*
- **I-137**: document-wording tests and the per-test `dev/` guard for the snapshot.
  *severity: low*
- **I-138**: duplicate tests and copied helpers. *severity: low*
- **I-139**: vacuous, fragile and tombstone tests. *severity: low*
- **I-140**: regroup issue- and task-named files by behaviour (optional). *severity: low*

---

## Appendix: `uv run pytest` after this report

Run on `issue/I-134-test-suite-review` after this report and the backlog entries were written.
No test, source or spec file changed.

```text
$ uv run pytest
...
-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
========== 1476 passed, 8 deselected, 2 warnings in 439.22s (0:07:19) ==========

$ git status --short
 M dev/BACKLOG.md
?? dev/plans/2026-09-24-test-suite-review.md
?? repo_files.txt          # pre-existing, not part of this change
```
