# AMAW Documentation Alignment Update

Updated 2026-09-10.

This revision reconciles the implementation pack around the existing Lean MVP architecture. It does not redesign the product.

## Updated documents

- README.md
- AMAW_PRD_v0.4.1.md
- AMAW_TRD_v0.5.1_Lean_MVP.md
- AMAW_WBS_v0.2.1.md
- agents.md (v0.2)
- api.md (v0.2)
- state-machine.md (v0.2)
- testing.md (v0.2)
- benchmark-methodology.md (v0.2)
- scenario-format.md (v0.2)
- player-v1.1.md

## Main reconciliations

1. `pass_con` is a mandatory agent-facing tool and `ready` is no longer overloaded for Con participation.
2. `game.primary_arguments_per_actor` is the single canonical scenario field.
3. PRD adjudication examples now match the normative Umpire contract (`mode`, `rationale`, `supersedes_effect_ids`).
4. A new private Message to a ready diplomacy recipient deterministically clears that recipient's ready state before phase closure.
5. Scenario-defined termination rules, custom diplomacy policies and communication restrictions beyond baseline messaging are explicitly MVP+.
6. Administrative exports explicitly include private game data such as objectives and Notes with ownership/visibility metadata; Actor APIs remain filtered.
7. Benchmark runs record stronger immutable provenance, including prompt/configuration metadata and content hashes.
8. The benchmark CLI uses `--repetitions N`, defined as N repetitions per generated model/role assignment.
9. SHA-256 hashing of scenario YAML and referenced Resources is brought into the Lean MVP.
10. Documentation precedence is explicit in README.md.

The Umpire v1 and Objective Evaluator v1 prompts were not changed.

## MVP review correction — 2026-09-10

TRD v0.5.2, WBS v0.2.2, state-machine/testing/benchmark-methodology v0.3 and the
Umpire/evaluator v1.1 prompts correct pre-T-007 persistence gaps and strengthen the fixed
30-attempt benchmark design. D-008 records the decision and scope.
