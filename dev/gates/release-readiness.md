# Release readiness — public-repository gate — PASS (with one substitution)

**Checked:** 2026-09-21 · **Criteria:** `dev/plans/2026-09-18-public-release-readiness.md §4`,
`dev/BACKLOG.md` I-089 · **Verdict:** PASS on the information-architecture question; the
provider-backed `amaw run orison-crisis` step was **not executed** (no provider budget
allocated — the retained run 11 export stood in for "the game"; see §2) ·
**Decisions:** D-080 (MIT), D-081 (evidence in `amaw-runs`)

## 1. Fresh clone

The branch was cloned into an empty directory and installed with nothing but the README's
commands. No file from the maintainer's working tree, no `.env`, no `dev/` reading.

```text
$ git clone -q --branch issue/I-089-public-release-readiness . <scratch>/clone
$ git status --short | wc -l
       0
$ du -sh .git .
287M	.git
 12M	.
$ find . -name __pycache__ -o -name '*.egg-info' -o -name .DS_Store | grep -v '^./.git'
(nothing)
$ ls
AGENTS.md CLAUDE.md CONTRIBUTING.md LICENSE MANIFEST.in README.md SECURITY.md agents
amaw_cli.py amaw_contracts.py amaw_unattended.py app benchmark dev docs guide pyproject.toml
scenarios tests uv.lock
$ uv sync --extra agents
 + websockets==16.1.1
 + wrapt==2.4.0
$ uv run python -c "import app; print('import app ok')"
import app ok
$ uv run pytest tests/test_i089_release_readiness.py tests/test_scenarios.py tests/test_orison_scenario.py tests/test_election_scenario.py -q
.........................................................                [100%]
57 passed in 4.13s
$ uv run amaw --help >/dev/null && echo "amaw --help ok"
amaw --help ok
```

The working tree is 12 MB; `.git` is 287 MB because the pruned run exports stay in history
(D-081: no rewrite). The package builds as MIT: `License-Expression: MIT`, `License-File:
LICENSE` (checked with `importlib.metadata` after `uv sync`).

## 2. The three questions, answered from the clone alone

Rule: every answer names the file that gave it; none may be under `dev/`, `DECISIONS.md` or
the TRD.

**What is AMAW?** — `README.md`, first paragraph and "What happens in a game": Matrix Games
played by LLM agents; Actors do diplomacy, research, Arguments and Cons; a fixed AI Umpire
proposes a mode and probability and the server rolls; every game is exported as a
machine-readable record so agent behaviour can be compared across repeated runs. The
repository map in the same file says where the engine (`app/README.md`), the agents, the
scenarios and the contracts are. `docs/README.md` "Read in this order" gives the six documents
to read next. Answered without `dev/`.

**What happened in the game?** — The plan's gate runs `uv run amaw run orison-crisis` and reads
the result. That spends provider budget (run 11: ~75 min and ~$0.04 per game) and none is
allocated; the substitute was the export of run 11 game 1, which the clone carries under
`dev/runs/t060-batch-smoke-20260919/` — the reader was pointed there only by the file being
present, and read it with the commands `guide/operations.md` §Export and inspect gives:

```text
$ jq '.export_format_version, (.game | {scenario_id, turn, phase, status, seed})' export.json
"11"
{ "scenario_id": "northern-strait", "turn": 6, "phase": "complete", "status": "complete", "seed": 60000 }
$ jq '.run_summary | keys'
["activation_wall_ms","activations","by_phase", …, "invalid_tool_calls", …, "resource_research", …]
$ jq -c '[.adjudications[] | {mode, probability, roll}] | .[0:4]'
[{"mode":"probabilistic","probability":0.4,"roll":0.6477…},{"mode":"probabilistic","probability":0.2,"roll":0.6826…},
 {"mode":"automatic_failure","probability":null,"roll":null},{"mode":"probabilistic","probability":0.3,"roll":0.4166…}]
$ jq -c '[.effects[] | select(.active) | .description] | .[0:3]'
["Commercial shipping continues normally.","Arkania formally establishes the Strait Transit Authority with itself as
 scheduling lead, transparent transit fees on non-Arkanian shipping, and toll-free passage for Cestian-flagged
 shipping.","Cestia publicly rejects the naval access proposal as a violation of its red line, …"]
$ jq -c '.messages | length, (map(.visibility) | group_by(.) | map({(.[0]): length}) | add)'
44
{"private":41,"public":3}
```

So: a six-turn Northern Strait game completed; the Umpire proposed probabilities of 0.2–0.4
and one automatic failure; the server's rolls decided each; the active Effects say Arkania
got its Transit Authority and Cestia refused foreign basing; diplomacy was 41 private Messages
to 3 public. `README.md` "What happens in a game" explains what each of those records is;
`guide/operations.md` explains the export. Answered without `dev/` — but with a retained
export in place of a fresh run. **A provider-backed `amaw run orison-crisis` remains to be
done when a budget is allocated; nothing in the information architecture depends on it.**

**How would I write my own scenario?** — `scenarios/README.md` "Writing your own" (copy
`orison-crisis`, change `scenario.id`, `amaw run` validates) → `guide/scenario-authoring.md`
(package shape, starter manifest, Effects/Resources/visibility, validation errors) →
`docs/scenario-format.md` for the schema. `scenarios/orison-crisis/README.md` and
`PACKAGE_CONTENTS.md` show a finished package; `scenarios/northern-strait/README.md` the
smallest one. Answered without `dev/`.

## 3. Guard tests

```text
$ uv run pytest tests/test_i089_release_readiness.py -q
.....                                                                    [100%]
5 passed in 0.82s
```

TEST-I089-001 licence · 002 prompt index matches `agents/` · 003 scenario index · 004
orientation/community files, README self-contained · 005 tracked `dev/runs/` = the D-081 set.

## 4. Carried forward

- `amaw-runs` repository: not yet created (owner's step). Its contents are staged under
  `~/Projects/AMAW-runs-archive/` — 21 directories, 10 of them removed from this repository
  in this task (`git rm`, commit `9b3aceb`), 11 moved from the untracked working tree on
  2026-09-19. `RUNS.md` already cites `amaw-runs/<dir>/` for the moved exports.
- `.git` at 287 MB carries the pruned exports in history; a `git filter-repo` is a separate
  explicit decision if clone size ever matters (D-081).
- The plan §4 provider run — recorded above as not executed.
