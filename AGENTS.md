# AMAW — Working Agreement

Canonical instructions for anyone (human or AI) doing development work in this repository.
`CLAUDE.md` imports this file; do not duplicate rules there.

> **Naming warning:** this file is the *working agreement*. `docs/agents.md` is something
> else entirely — the normative **player-agent, tool and Umpire contract**. When an issue
> says "see agents.md", it means `docs/agents.md`.

---

## 1. What AMAW is

A small Python application that runs **Matrix Games** played by autonomous LLM agents, and
turns them into a repeatable **agent benchmark**.

Three or more Actors, each driven by an LLM, conduct private and public diplomacy, submit
Matrix **Arguments**, challenge each other with **Cons**, and have those Arguments
adjudicated by an LLM **Umpire**. The server — never the model — owns randomness and state.
Every game is exported as machine-readable data so models and agent designs can be compared
across repeated runs of the same scenario.

**The benchmark value comes from agent behaviour, not from engine complexity.**
Keep the engine small. See `docs/TRD.md` §3.

---

## 2. Specification, decisions and history

| Layer | Location | Rule |
|---|---|---|
| **Specification** | `docs/` | The source of truth. Read-only during normal work. |
| **Work** | GitHub issues and pull requests | Where work is proposed, discussed, reviewed and merged. |
| **Decisions** | `dev/DECISIONS.md` | Every choice the specification does not settle, and every change made to it. |
| **Runs** | `dev/RUNS.md` | What the agents and the Umpire actually did in each provider-backed run. |
| **History** | the rest of `dev/` | The record from before this repository became the upstream. Frozen. |

`docs/` is deliberately normative. Code conforms to docs. If code and docs disagree, the code
is wrong — **unless** you have found a genuine spec defect, in which case: stop, add an entry to
`dev/DECISIONS.md`, and change the doc in a separate commit before continuing.

### Documentation precedence

`docs/README.md` defines this and it is binding. Summarised:

- `docs/state-machine.md` — procedural legality, invariants, transitions
- `docs/api.md` — HTTP shapes, auth, idempotency, server behaviour
- `docs/scenario-format.md` — scenario YAML schema
- `docs/agents.md` — player-tool and Umpire interface contracts
- `docs/testing.md` — release-blocking acceptance behaviour
- `docs/benchmark-methodology.md` — experimental design and interpretation

The **TRD** governs architecture and scope where no specialist contract applies. The **PRD**
governs product intent. Examples in higher-level documents are illustrative; the specialist
contract wins.

### Reading the references in the code

Comments, tests and documents cite identifiers from the history. Each one resolves in `dev/`:

| Identifier | Meaning | Where |
|---|---|---|
| `D-0NN` | A decision | `dev/DECISIONS.md` |
| `I-0NN` | A discovered issue | `dev/BACKLOG_RESOLVED.md`, or `dev/BACKLOG.md` if it was open at the move |
| `T-0NN` | A planned step | `dev/WBS.md` |
| run N | A provider-backed run | `dev/RUNS.md`; its evidence is in the `amaw-runs` repository |
| `#N` | A GitHub issue or pull request | this repository |

New work cites GitHub issues and pull requests, never new `I-`/`T-` numbers. Decisions keep
their `D-` numbering, and runs keep theirs: new runs are recorded in `dev/RUNS.md` under the next
run number, and their evidence goes to the `amaw-runs` repository, never into this one
(`dev/runs/` is ignored because exports contain every Actor's private material).

### Scenario versions

`scenarios/versions.lock` records every released version's hashes, including versions released
before this repository became the upstream. Tags `scenario/<id>/<version>` restore a version
with `git checkout <tag> -- scenarios/<directory>`. The first tags here mark each package's
version at the root commit. Earlier versions were tagged in the maintainer repository and are
not restorable from this one; their hashes and changelogs remain.

---

## 3. Where to start

1. Read the issue, and any discussion on it.
2. Read the spec sections it touches **before writing code**. Cite line-level sections; don't
   work from memory of the docs.
3. If the issue implies a change to a normative contract, say so on the issue first.

---

## 4. The development loop

One coherent change per pull request. No parallel half-finished changes on one branch.

```text
branch from master
    ↓
write the failing test first        ← from docs/testing.md test IDs
    ↓
implement the smallest thing that passes
    ↓
uv run pytest                       ← must be green
    ↓
pull request: what changed, which tests now pass, any DECISIONS.md entry,
              and the real pytest summary line
```

A pull request names the issue it closes (`Closes #12`). Commit subjects are short and
imperative; the body says what changed and why.

### Test-driven, keyed to the acceptance spec

`docs/testing.md` enumerates every release-blocking test with a stable ID (`TEST-ARG-008`,
`TEST-VIS-003`, …). Where a change has one, that ID is the definition of done.

- Name tests after the ID: `def test_arg_008_argument_budget_enforced():`. A test with no
  `testing.md` ID is named for the behaviour it checks, and its docstring cites the issue.
- Layers 1–3 (unit, engine integration, API integration) must run **without an OpenRouter
  key**. Provider-dependent tests are marked `@pytest.mark.llm` and excluded by default.
- The canonical fixture at `tests/fixtures/deterministic-game/` is authoritative. Changing it
  means updating every expected output; do not edit it casually.

### Never claim done without evidence

"Should work" is not a completion state. Run the suite and report the real result. If
something is partially done, say which part and why.

### Comments explain the rule first, then give the reference

A comment or docstring in production code must make sense to someone who hasn't read the
history:

- **The first sentence states the rule as it is now.** That can be an invariant, a safety
  property, a compatibility rule or a design reason.
- **Then give the reference**, such as `INV-…`, `api.md §…`, `D-0NN` or `#N`, wherever it adds
  traceability.
- **Keep references that explain why behaviour must not change.** For example, a reference
  that explains why legacy data resolves to an old default, or why an experimental condition
  deliberately stays distinct.
- **Drop references that only retell history.** The history belongs in `dev/`, issues and pull
  requests.

Good: "Legacy resume recipes without `search_contract` resolve to `search-v1`, so persisted
experiments are never reinterpreted under a newer default (I-112, D-088)."

Bad: "I-112 / D-088".

Apply this to code you are already changing. Do not rewrite comments across the repository
just to follow it.

---

## 5. Milestones

The lean MVP — Gates 0–3 (foundation, engine, autonomous play, knowledge) and the release
package — is complete; its record is in `dev/WBS.md` and `dev/BACKLOG_RESOLVED.md`.

The next milestone is a **local workbench** (D-106): `amaw web` serves a browser UI on
127.0.0.1 over the same SQLite file, with no user accounts and provider keys kept server-side.
TRD §41 must be amended, in its own commit, before any workbench code lands.

Multi-model benchmarking (3 models × 3 roles, every permutation × 5 repetitions) stays a
separate milestone with its own exit check (`benchmark-methodology.md`).

---

## 6. Architecture rules that are not negotiable

These come from the TRD and the invariants doc. Violating one is a bug even if tests pass.

1. **`app/` must never import PydanticAI.** The FastAPI core knows nothing about agent
   frameworks.
   (TRD §4)
2. **OpenRouter integration lives outside the core game domain.** (TRD §4)
3. **The server owns all randomness.** The Umpire proposes a `mode` and a `probability`;
   the server draws the roll, picks the branch and creates Effects. A model never decides an
   outcome. (INV-RNG-001, INV-EFFECT-002, INV-ADJ-003)
4. **Visibility is centralised in one service.** No per-endpoint filtering. Filter *before*
   search, not after — result counts must not leak hidden material. (INV-RES-002/003)
5. **Actor identity comes from the Seat bearer token, never from a request body.**
   (INV-ACTOR-001)
6. **State-changing operations are transactional and revalidate phase + eligibility inside
   the transaction.** (INV-GAME-004, INV-GAME-005)
7. **Effects are append-only history.** Supersession deactivates; it does not delete.
   (INV-EFFECT-004)
8. **Do not split modules prematurely.** Refactor when a file is genuinely unwieldy.
   (TRD §5)

### Scope discipline

TRD §41 and the archived original WBS §7
(`dev/archive/docs/original-vision/AMAW_WBS_v0.2.1.md`) list what is explicitly **out** of the
lean MVP — organisations, channels, MCP gateways, vector search, event sourcing, human UIs, and
much more. If a change starts pulling one of those in, stop and raise it on the issue: it needs
a TRD amendment and a decision first.

---

## 7. Technology

| Component | Choice |
|---|---|
| Language | Python 3.12+ |
| API | FastAPI |
| Validation | Pydantic v2 |
| Persistence | SQLModel + SQLite |
| Scenario manifest | YAML |
| Agent framework | PydanticAI (reference player only) |
| Model gateway | OpenRouter |
| HTTP / async | httpx / asyncio |
| CLI | Typer |
| Testing | pytest |
| Type checking | mypy, scoped in `pyproject.toml` |
| Packaging | uv |

Alembic is optional until schema changes hurt. SSE is not required for the first release.

```bash
uv sync --extra agents   # install, with the reference player
uv run pytest            # layers 1-3, no API key needed
uv run pytest -m "not slow and not llm"   # quick inner loop; the full run stays the gate
uv run pytest -m llm     # provider-dependent tests, needs OPENROUTER_API_KEY
uv run mypy              # the typed modules; also run by the suite
uv run amaw --help       # CLI
```

---

## 8. Layout

```text
AMAW/
├── AGENTS.md              # this file — the working agreement
├── CLAUDE.md              # Claude-specific additions, imports this file
├── app/                   # FastAPI core: api, models, game_service, visibility,
│                          # scenarios, resources, database (no agent framework)
├── agents/                # player.py, tools.py, umpire.py, consequence.py
├── benchmark/             # batch runner, controls, adjudication corpus harness
├── amaw_resources/        # prompts and bundled scenarios packaged into the wheel
├── scenarios/             # scenario packages
├── scripts/               # maintenance and experiment scripts
├── tests/                 # incl. fixtures/deterministic-game/
├── docs/                  # SPEC LAYER — PRD, TRD and the normative contracts
├── guide/                 # non-normative user guides, incl. the Field Manual (manual.html)
└── dev/                   # DECISIONS.md and RUNS.md (living), the frozen history; see dev/README.md
```

---

## 9. Adding and changing work

- Something broken or missing found mid-change → open an issue and keep going. The current
  pull request keeps its scope.
- A decision the docs don't settle → `dev/DECISIONS.md`, one short entry with the next `D-`
  number, then proceed.
- A genuine spec defect → `DECISIONS.md` entry, fix the doc in its own commit, and bump the
  doc's version line.
- A scenario content change bumps its `version` and is locked in `scenarios/versions.lock`
  (`uv run python scripts/lock_scenarios.py`).
- Superseded prompts move to `docs/prompts/superseded/`; a prompt file is never edited under
  the same version.
