# Test fixtures

## `knowledge-scenario/` — the T-051 scenario knowledge acceptance fixture

`tests/fixtures/knowledge-scenario/` backs the Scenario Knowledge Acceptance Test
(`tests/test_scenario_knowledge.py`, testing.md §21). It is **not** golden and is not driven to
a deterministic golden output — it exists to carry the six §21 content elements and to prove the
Resource visibility boundary end-to-end. It contains: a **public background** (`background.md`),
a **public CSV** dataset (`trade.csv`), a **public PNG map/image attachment** (`strait-map.png`),
and **two private intelligence Resources** — `arkania-intel.md` (visible only to `arkania`) and
`belvar-intel.md` (visible only to `belvar`) — each carrying a decisive **codeword** fact
(`GREYPETREL` / `TIDEHOLD`) that appears in **no** Actor's brief. Do not move these codewords into
a brief: the test relies on them being brief-absent, private-only knowledge an agent must seek.

## `deterministic-game/` — the canonical deterministic game fixture

`tests/fixtures/deterministic-game/` is the **authoritative** deterministic game scenario
(testing.md §4). It is the fixture later deterministic tests build on — the engine
integration run (T-025 ScriptedPlayers, T-026 six-turn integration test) and Resource work
(T-045+).

**Changing this fixture requires updating every expected output that depends on it.** Do not
casually edit Actor briefs, objectives, the turn order, turn count, Argument/Con limits, the
seed, starting Effects or Resource contents — those are exactly the benchmark-relevant fields
(scenario-format §20). Treat a change here as a change to a golden file.

**What "deterministic" covers.** The fixture is deterministic for the engine tests that drive it
with ScriptedPlayers and the FakeUmpire (T-025/T-026, `tests/test_integration_six_turn.py`): the
seed fixes every server roll. Some provider-backed checks in `tests/test_ai_smoke.py` (T-044)
reuse this scenario for its *content* only, and the full smoke game runs
`scenarios/northern-strait`. In both, Arguments, Cons and Umpire proposals are model output and
are not reproducible (`benchmark-methodology §8`: "Seed
controls server stochasticity only. It does not guarantee identical LLM outputs."). Never build
an expected output from an AI run. The polished live-play scenario is
`scenarios/orison-crisis` (T-065).

What it contains today (testing.md §4):

- **3 Actors** — `arkania`, `belvar`, `cestia`;
- **6 turns**, **fixed turn order** `[arkania, belvar, cestia]`;
- **1 primary Argument per Actor per turn** (`primary_arguments_per_actor: 1`);
- **max Cons** configuration (`max_cons_per_actor: 3`);
- **starting Effects** — two public, one private (`arkania`, expiring turn 3);
- **public and private Resource declarations** with backing files under `resources/`
  (`background.md`, `trade.csv` public; `arkania-intel.md` private to `arkania`);
- a **fixed RNG seed** (`game.seed: 424242`).

### What lives outside the manifest

- **Private Messages** are runtime artefacts produced during play by the scripted players, not
  scenario-manifest declarations, so they are not part of `scenario.yaml`.
- **The deterministic adjudications** have no scenario-format representation. They live in
  code: `agents/scripted.py` builds the ScriptedPlayers and the `deterministic_umpire()`
  FakeUmpire (and `deterministic_consequence_resolver()`) that produce the golden schedule.
- **Resource materialisation** — file reading, format parsing and SHA-256 provenance hashing —
  reads the backing files under `resources/`, so a change to their bytes changes the scenario
  hashes as well as any expected output.

## `scenario-baselines/` — earlier manifests that version-bump tests compare against

Each file is a scenario's `scenario.yaml` exactly as it was at an earlier version:
`skaro-crisis-1.1.0.yaml` and `swedish-government-7.0.0.yaml`. `TEST-I128-001` and
`TEST-I161-001` check that the next version changed only what its changelog says. The files
stand in for the commits the maintainer repository compared against, so the checks also run
where that history is absent. Never edit them: they record past content, and `TEST-I169-004`
checks them against those commits in the maintainer repository.
