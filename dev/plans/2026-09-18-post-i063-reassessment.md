# Post-I-063 Reassessment — 2026-09-18

The step the 2026-09-16 structural review (`2026-09-16-structural-cleanup-review.md` §3, row
*reassess*) mandated after I-060…I-063 landed: re-run the dead-code scan and the flow
read-through, and decide whether I-064 (`game_queries`) stays next or I-065 (Umpire provenance
boundary) goes first. Run at commit `75f9e20` (master, I-063 merged).

## 1. Dead-code scan

Method: AST walk of every top-level def/class/assignment in `app/`, `agents/`, `benchmark/`,
`amaw_cli.py`, `amaw_contracts.py`; a name is reported when nothing outside its own module
references it and it is either referenced only at its definition or has no test reference.
Then `uvx ruff check --select F` over source and tests.

| Name | Where | Verdict |
|---|---|---|
| `TOOL_SCHEMA_VERSION = "player-tools-v3"` | `agents/tools.py:44` | **dead** — zero references; `player.py` versions its schemas with `BASELINE_`/`BATCHED_TOOL_SCHEMA_VERSION` |
| `time` import | `agents/umpire.py:31` | **dead** (F401) |
| 19 unused imports/locals | `tests/` (14 modules) | F401/F841, test-only |
| `dev/tasks/I-022-I-027-throughput-accounting.md` | active-brief dir | **stale** — both issues resolved, brief never archived |
| `_main`, `analysis_plan_validate`, `analysis_plan_verify` | `amaw_cli.py` | entry points (`if __name__`, Typer commands) — not dead |
| `reset_default_engine`, `list_agent_runs`, `build_scripted_players`, `classify_measure`, `run_class_of` | `app/`, `agents/`, `benchmark/` | test-only public helpers — leave alone (review F12 verdict unchanged) |

Filed as **I-075** (severity low, housekeeping). Nothing found blocks I-064 or I-065.

## 2. Flow read-through

`scenario → game → phase → activation → mutation → adjudication → export` after I-060…I-063:

- The condition is one value (`GameLoopCondition`, I-060) and the CLI composes one path
  (I-061); `provenance.py` records and validates only, `summary.py` derives (I-062);
  `agents/` no longer carries the inference-profile loader or code-version hashing inside
  `player.py`/`autonomous.py` (I-063). The engine path reads file-by-file as the review
  described; nothing that landed made either remaining finding larger or smaller.
- **F4 still holds exactly as written.** `app/api.py:435 _usage_integer`, `:445 _usage_cost`,
  `:553`/`:658`/`:709` three `getattr(umpire, …)` duck-type probes, dotted OpenRouter key paths
  at `:677–695`. Still the cluster's only AGENTS.md §6 rule 2 violation.
- **F7/F8 still hold.** `app/context.py:307 _actor_ids` ≡ `app/game_service.py:567`;
  `context._has_argument_budget` (`:311`) and `_con_count` (`:341`) still mirror the service
  guards; `game_service._run` (`:388`) and `_require_running_diplomacy` (`:2217`) still present.
- I-064 and I-065 share no module (`app/game_service.py` + `app/context.py` vs `app/umpire.py`
  + `app/api.py` + `agents/umpire.py`), so neither order shrinks the other.

## 3. Decision: I-065 first, then I-064, then I-075

- I-065 is the only §6 violation and is in the review's "if only three land" set
  (I-060, I-061, I-065); I-064 is severity low and touches the most test-sensitive file.
- Ordering does not change either task's scope (§2, last bullet).
- I-064 is deferred, not dropped. I-075 follows I-064 because it is pure housekeeping.

Recorded in `dev/STATUS.md` (Next task) and in the I-064 / I-065 / I-075 backlog headings.
