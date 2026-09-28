# `dev/` — decisions and history

Nothing in this directory is needed to use, run or extend AMAW. New work is tracked in GitHub
issues and pull requests (`../AGENTS.md` §4).

AMAW was developed in a private repository until this one became the upstream (D-106). The
files below are that repository's process record at the move. They are kept so that the `D-`,
`I-` and `T-` identifiers cited throughout the code, tests and docs resolve.

| File / directory | What it is | State |
|---|---|---|
| `DECISIONS.md` | The decision log (`D-0NN`): every choice the specification does not settle, and every change made to it. | **Living** — new decisions are added at the top. |
| `BACKLOG_RESOLVED.md` | Closed issues (`I-0NN`), newest first, with the commit that resolved each. | Frozen |
| `BACKLOG.md` | Issues still open at the move. Those worth keeping became GitHub issues. | Frozen |
| `WBS.md` | The original plan and progress record: every planned step (`T-0NN`), marked ✅ with its evidence. | Frozen |
| `RUNS.md` | The behavioural record of every provider-backed run: what the agents and the Umpire actually did. | **Living** — new runs are appended under the next run number; their evidence goes to `amaw-runs`. |
| `gates/` | Gate-check records: criterion, real test output, verdict. | Frozen |
| `plans/` | Design notes and reviews too long for a decision entry. | Frozen |
| `archive/docs/original-vision/` | The original PRD, TRD and delivery WBS, the record of the first vision (D-102). | Frozen |
| `runs/` | Not shipped. The conventional place for local run evidence; generated run files there do not count as source changes (D-067). | Local only |

The commit hashes in these files, and the scenario tags they mention, refer to the private
repository's history, which is not published. Task briefs (`dev/tasks/`, `dev/archive/tasks/`) stayed there too. The evidence of
every run cited in `RUNS.md` is published in the separate `amaw-runs` repository.
