# Northern Strait live-play scenario — design

**Date:** 2026-09-12 · **Decided with the user** · **Implements** observation 9 of
`2026-09-12-ai-smoke-observations.md` (nothing forces conflict).

## Problem

The two provider-backed T-044 runs converged by turn 3 on near-identical "neutral, rotating,
equal-footing forum" proposals. The fixture's objectives are mutually satisfiable, so a
multilateral forum meets all of them and the game becomes decoration. The fixture
(`tests/fixtures/deterministic-game/`) is a golden file (testing.md §4) and is not edited.

## Decisions

1. **New package `scenarios/northern-strait/`** (`id: northern-strait`, `version: 1`), same world,
   names, public descriptions, Resources, turn order, turn count and budgets as the fixture. Only
   briefs, objectives, private information and starting Effects differ.
2. **Zero-sum, triangular objectives.** Each pair of Actors has one dispute; each Actor keeps one
   cooperative objective. No three-way settlement satisfies everyone; every pair can trade.
   - Arkania ↔ Belvar — transit control (Arkanian scheduling authority vs. prevent it).
   - Belvar ↔ Cestia — basing (standing naval access at a Cestian port vs. no foreign forces).
   - Cestia ↔ Arkania — tolls (transit fees vs. toll-free Cestian passage).
3. **Private information:** one own constraint plus one lever on a neighbour per Actor.
4. **Starting Effects:** the fixture's three plus two public ones that put the contest on the
   table at turn 1 (Arkania's draft charter; Belvar's task group with mandate under review).
5. The `llm` smoke test runs this package; offline tests keep the fixture, plus one offline
   six-turn coordinator run against the new package so it cannot rot unnoticed.
6. No engine, API or prompt change. Not T-065 (polished example scenario); noted as groundwork.

## Rejected

- Editing the fixture (golden file; breaks T-025/T-026 expected outputs).
- A crisis starting Effect on a clock (option B) — may be layered on later if A still converges.
- Two-great-powers-plus-kingmaker (option 3) — leaves Cestia without an independent goal.
