# AMAW specification

This directory is the specification: the product and technical requirements, the six normative
contracts that code is implemented and tested against, and every versioned prompt. How to install,
run and author scenarios is in [`../guide/`](../guide/README.md), which is non-normative.

## Read in this order

If you are new to AMAW and want to understand it rather than change it:

1. [`../guide/manual.html`](../guide/manual.html), the illustrated Field Manual: how a game plays,
   how adjudication works, the settings, and how runs become a benchmark.
2. `PRD.md` §1–§3: what a Matrix Game is here, and why agents play it.
3. `state-machine.md`: turns, phases, Arguments, Cons and adjudication, as the rules of play.
4. `agents.md`: what a player agent sees and can do, and what the Umpire proposes but never
   decides.
5. `api.md`: the HTTP surface, for when you want to drive the server yourself.
6. `benchmark-methodology.md`: how repeated runs are made comparable.

## Documents

| Document | Governs |
|---|---|
| `PRD.md` | Product intent: why AMAW exists, what it is for, what the MVP deliberately leaves out |
| `TRD.md` | Architecture and scope, where no specialist contract applies |
| `state-machine.md` | Procedural legality, invariants and state transitions |
| `api.md` | HTTP shapes, authentication, idempotency and external server behaviour |
| `scenario-format.md` | The scenario schema |
| `agents.md` | Player-tool, Umpire and consequence-resolver interface contracts |
| `testing.md` | Release-blocking acceptance behaviour, by test ID |
| `benchmark-methodology.md` | Experimental design, run semantics and interpretation |
| `prompts/` | Every prompt the reference agents have used. [`prompts/README.md`](prompts/README.md) is the index and says which versions the code loads today |
| `analysis-plan-example.yaml` | An example predeclared analysis plan (`benchmark-methodology.md`) |
| `analysis-plan-v2-example.yaml` | The same plan as `analysis-plan-v2`, with a structured `design` (`benchmark-methodology.md` §4.2) |

## Conflict resolution

When documents appear to conflict, the specialist contract for the subject wins:

- `state-machine.md` controls procedural legality, invariants and state transitions;
- `api.md` controls HTTP shapes, authentication, idempotency and external server behaviour;
- `scenario-format.md` controls the scenario schema;
- `agents.md` controls player-tool and Umpire interface contracts;
- `testing.md` controls release-blocking acceptance behaviour;
- `benchmark-methodology.md` controls experimental design, run semantics and interpretation.

The TRD controls architecture and scope where no specialist contract applies. The PRD controls
product intent. Examples in higher-level documents and in the guides are illustrative and must
match the relevant contract.
