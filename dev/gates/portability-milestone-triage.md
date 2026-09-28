# Matrix Scenario Portability milestone — boundary backlog triage

**Checked:** 2026-09-24 · **Milestone:** Matrix Scenario Portability (D-086), T-072…T-075 ✅ ·
**Basis:** AGENTS.md §3/§9 ("at each gate boundary, triage the open backlog: fix what blocks the
next gate, and say plainly what is being carried forward") · **Suite:** 1357 passed,
8 LLM deselected (after T-075).

## 1. Position

No next step is queued in `WBS.md`, so no open item blocks planned work:

- T-076 does not open (D-093).
- T-061 is optional.
- T-063, T-070, Gate 4 and T-067–T-069 are deferred (D-076).

## 2. Open items and dispositions

| Item | Severity / kind | New evidence since last triage | Disposition |
|---|---|---|---|
| I-090 `analysis-plan-v2` policy block | low · benchmark methodology | none | **Carry forward.** It only matters for a multi-cell analytical batch, and T-063 is deferred. |
| I-038 free-text Effects carry no mechanical authority guarantee | accepted limitation (D-089) | Run 17: 23 of 24 rationales cite a rule. An authority rule produced the one automatic failure. | **Carry forward** as an accepted limitation. There is no reopening trigger. |
| I-039 reaction diplomacy | deferred · game-loop change | Run 17: no post-adjudication communication need | **Carry forward** (D-093). |
| I-109 more than one primary Argument per turn | deferred · ready to plan | Run 17: the Cabinet bundled several decisions into one Argument that received one roll. That is a fidelity cost, not a blocker. | **Carry forward, ready to plan.** It becomes the next capability task when a scenario needs typed, conditional or timed extra actions, or when the owner wants the fidelity gain. |
| I-113 external-system connector | deferred · architecture | T-075 recorded no need | **Carry forward.** T-076 stays unopened (D-093). |
| I-115 residual matrix mechanics | deferred · dispositions only | Filed at T-075 | **Carry forward.** It is a disposition register, not work. |
| I-116 `skaro-crisis` loses pressure after turn 2 | low · scenario content | Filed at T-075 (run 17) | **Carry forward, available.** It needs no engine change, but validating it takes a provider run of about 8 hours. |
| I-104 bounded/cursor `read_resource` | deferred · tool contract | I-099/I-100 telemetry and run 17 contradict the premise | **Closed: not warranted** (§3). |

## 3. I-104 closed as not warranted

I-104 was "justified only if I-099 telemetry shows agents receiving the right Resource id and not
reading it, rather than reformulating after poor results". The telemetry shows the opposite.

- **Run 15 (I-100).** The low read rate came from poor search results:
  - under `search-v1`, 97 of 109 searches returned nothing, and only one returned Resource was
    read;
  - under `search-v2`, 2 of 56 searches returned nothing, and 33 returned Resources were read.
- **Run 17.** Under the released `search-v2` (I-112), there were 131 searches and 97 reads, with
  every Actor reading its private Resource.

Whole-Resource reads are therefore viable. No tool-contract change is justified, and I-104
reopens only if a scenario with very large Resources shows reads being avoided.

## 4. Verdict

Nothing blocks, and nothing is fixed at this boundary. Seven items are carried forward and one
(I-104) is closed.

**Recommendation for the owner:** choose whether to plan next
- I-109 (sequencing design),
- I-116 (a cheap scenario revision plus a long provider run),
- or optional T-061 (objective scoring).

Otherwise the project rests at a complete milestone.
