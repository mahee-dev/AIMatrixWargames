# Public Release Readiness — 2026-09-18

Plan for making the repository publishable as open source. Prompted by an external structural
review of the file inventory (2026-09-18); this plan records which of its recommendations
survive verification against `git ls-files` and the working agreement, and sequences the work.
Tracked as **I-089**. Not started until I-077/I-078 close.

Principle: **simplify what the public sees, not the implementation.** No package moves, no
module splits, no `src/` layout before launch.

---

## 1. Findings from the review, verified

| Review claim | Verified? | Consequence |
|---|---|---|
| `.env`, `data/amaw.db`, `dist/`, `.DS_Store`, `.claude/settings.local.json` are tracked | **No.** All in `.gitignore`; `git log --all` shows `.env` and the db were never committed | No action, no credential rotation |
| No LICENSE file | Yes — and `pyproject.toml` says `license = "LicenseRef-Proprietary"` | **A licence decision is the gate for everything else** |
| `dev/runs` is the bloat | Yes — 39 tracked files / 16 MB; 70 MB on disk incl. 12 untracked run dirs | Decide where benchmark evidence lives (§2.2) |
| Run logs may contain material not consciously cleared for publication | Yes — exports are unfiltered admin records (README, CLAUDE.md) with private objectives and Notes | Audit before any run is published |
| Five scenario dirs, three of them Swedish versions | Yes — `svensk-regeringsbildning-2026-v5` is used by `tests/test_election_scenario.py`; `swedish-government-v6` by `tests/test_unattended_launcher.py` and `guide/operations.md`; `swedish-government-formation-2026` is referenced nowhere | Prune with test/guide updates |
| Prompt versions need a "which is current" README | Yes — 18 flat files; `docs/README.md` §Versioned prompts still names player-v1.2 / umpire-v1.3 as current, code uses player-v1.13 / umpire-v1.3+v1.4 | Fix the stale index; add a table, not subdirs |
| Demote `dev/` to "history" | Partly — `dev/` is the live process layer `AGENTS.md §3` depends on, not history. `WBS.md`/`STATUS.md` cannot move | `dev/README.md` stating it is maintainer process; main README stops pointing there |
| Reorganise `docs/` into `reference/` + `design/` | **Rejected** — `docs/README.md` defines precedence by path; every brief, ADR and code comment cites `docs/api.md §n` | Curated index + getting-started page instead |
| Introduce `app/game|storage|reporting|api` packages "mechanically" | **Rejected for launch** — 81 files import `app.*`, and 36 string references to `app.<module>` paths (`logging.getLogger("app.api")`, module lists in `tests/test_ai_smoke.py:317`, monkeypatch targets) are invisible to import rewriting | `app/README.md` module map (15 lines) gives most of the benefit at no risk; revisit post-launch if navigation ever hurts |
| Leave `game_service.py`, `player.py`, `amaw_cli.py`, tests alone | Yes | No action; note `amaw_contracts.py` and `amaw_unattended.py` sit beside the CLI at root and would move together if it ever moves |
| Fresh-clone test as the readiness gate | Yes | §4 |

---

## 2. Work, in order

### 2.1 Licence (blocks all of the below)
- Choose the licence. Options in `DECISIONS.md` as `D-0NN`; the decision is the owner's.
- Add `LICENSE`; set `pyproject.toml` `license` accordingly; add the SPDX line to `README.md`.

### 2.2 Run evidence location (decision + prune)
- `AGENTS.md §8` and `RUNS.md` define `dev/runs/` as the evidence store; the Benchmarking
  milestone needs "30 retained attempts, machine-readable provenance". The public repo cannot
  carry that. `D-0NN`: evidence moves to a separate `amaw-runs` repository (or Release assets);
  `RUNS.md` entries link to it; `dev/runs/` in this repo keeps at most one small completed
  canonical run and the T-060 batch smoke (needed as I-076 evidence).
- Update `AGENTS.md §8` and `RUNS.md` header to say so.
- **Audit every retained run** for private material before it stays: objectives, Notes,
  private Messages, provider metadata. Nothing is retained that has not been read.
- `git rm` the rest (history stays; if history size matters, that is a separate, explicit
  `git filter-repo` decision — not assumed).
- Move the 12 untracked `dev/runs/swedish-government-v6-*` dirs out of the working tree.
  **Done 2026-09-19:** 11 moved to `~/Projects/AMAW-runs-archive/`; run 9's dir tracked instead.

### 2.3 Scenarios
- `orison-crisis` is the canonical starter (already what `README.md` runs).
- `northern-strait`: keep as the strategic example; add its missing `README.md`.
- `swedish-government-v6`: keep as the large/stress example.
- `svensk-regeringsbildning-2026-v5`: `tests/test_election_scenario.py` loads it. Either
  repoint the test at `-v6` (if the assertions hold) or move the scenario to
  `scenarios/archive/` with the test following. Decide when doing the work; don't guess.
  **Done 2026-09-19:** moved to `scenarios/archive/`, test follows (assertions are v5-specific).
- `swedish-government-formation-2026`: unreferenced → `scenarios/archive/` (or delete;
  reproducibility of `RUNS.md` entries decides — check which runs used it).
  **Done 2026-09-19:** it is v1, used by no recorded run; moved to `scenarios/archive/`.
- `scenarios/README.md`: one paragraph per scenario, "start here" on Orison.

### 2.4 Prompts
- `docs/prompts/README.md`: table of every version, which is current for what
  (player-v1.13; umpire-v1.3 sealed / v1.4 visible_reply), the `D-0NN` that made it current,
  and why old versions remain (experimental reproducibility — `RUNS.md` cites them).
- Fix `docs/README.md` §Versioned prompts, which is stale (names v1.2 / v1.3 as current).
- No `current/` / `archive/` subdirectories: they would break `RUNS.md` and brief citations for
  no gain.

### 2.5 Orientation files (additive, no moves)
- `dev/README.md`: "maintainer process and history; not required to use, run or extend AMAW";
  one line per file.
- `app/README.md`: module map grouped as engine (`models`, `game_service`, `game_loop`,
  `context`, `activation_context`, `visibility`, `rng`), storage (`database`, `persistence`,
  `provenance`, `resumption`), reporting (`summary`, `export`), scenarios (`scenarios`,
  `resources`), web (`api`, `security`), plus `config`, `umpire`.
- `README.md`: stays ~80 lines. Drop the last line's pointer to `dev/STATUS.md` for
  non-contributors (keep `AGENTS.md` under a *Contributing* heading). Add a 10-line "what
  happens in a game" transcript or Mermaid turn diagram. Replace `<repository-url>`.
- `docs/README.md`: add a "read in this order" list for newcomers (concepts → scenario
  format → api) above the precedence rules; precedence rules unchanged.

### 2.6 Community files
- `CONTRIBUTING.md`: points at `AGENTS.md`, the test-ID discipline, `uv run pytest`, and
  branch/commit conventions. Short.
- `SECURITY.md`: how to report; note that exports are unfiltered admin records.
- No issue/PR templates, no code of conduct at launch.

### 2.7 Working-tree hygiene at launch time
- Confirm `git status` clean, no untracked run dirs, `__pycache__`/`amaw.egg-info` absent
  from the clone (already ignored — just verify).

---

## 3. Explicitly not done before launch

- `app/` package hierarchy (see §1). If wanted later: its own `I-0NN`, one commit, `git mv` +
  import rewrite + a grep for every string `"app.` reference, suite green before and after.
- `docs/` reorganisation.
- `src/amaw/` layout; splitting `amaw_cli.py`; test directory restructuring.
- Any change to `game_service.py`, `api.py`, `player.py` beyond what open backlog items
  already require.
- Rewriting git history.

---

## 4. Readiness gate

A person or an AI session with **no** AMAW context does only:

```bash
git clone <url> && cd AMAW
uv sync --extra agents
cp .env.example .env   # set OPENROUTER_API_KEY
uv run amaw run orison-crisis
```

and then answers, from the repo alone: *What is AMAW? What happened in the game? How would I
write my own scenario?* If any answer required `dev/`, `DECISIONS.md` or the TRD, the
information architecture is not ready. Record the transcript under `dev/gates/` as
`release-readiness.md`.

---

## 5. Sequencing relative to the backlog

After I-077 and I-078 (both high, both in flight or next). Before any benchmark batch is
published. Independent of I-079…I-088; none of those touch the files above except I-076
(`dev/runs/t060-batch-smoke-20260918/` is its evidence and is retained).
