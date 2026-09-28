# Archived scenarios

Superseded scenario packages, kept so that closed briefs and regression tests that cite them
stay readable. Nothing here is a supported starting point — use `orison-crisis` (starter),
`northern-strait` (strategic example), `swedish-government` (large/stress example) or
`skaro-crisis` (rules, injects and consequences).

| Directory | Scenario id / version | Superseded by | Why kept |
|---|---|---|---|
| `swedish-government-formation-2026/` | `svensk-regeringsbildning-2026` v1 | `svensk-regeringsbildning-2026-v5`, then `swedish-government-v6` (now `swedish-government`) | Original 2026-09-13 package; the pre-election template with the "fill in the result" step. Referenced nowhere else. |
| `svensk-regeringsbildning-2026-v5/` | `svensk-regeringsbildning-2026` v5 | `swedish-government` | `tests/test_election_scenario.py` loads it for compatibility regressions; it was never used in a retained run. |

Archived 2026-09-19. The v4 package that runs 7 and earlier used (`RUNS.md`) was never a
separate directory and is not recoverable here; its exports carry its hashes.
