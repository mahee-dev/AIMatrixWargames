# The Orison Crisis

A fictional strategic crisis packaged as AMAW's ready-to-run introductory example.

This package is designed as a polished three-Actor, six-turn introductory scenario.
It preserves the strategic structure of a classic historical superpower missile crisis
while changing names, geography, chronology, capabilities, and secondary details so
players must reason from scenario evidence rather than reproduce a historical script.

## Actors

- Republic of Ardin
- Vesper Union
- Commonwealth of Orison

## Intended play

The crisis is deliberately non-zero-sum. Ardin seeks removal of a nearby strategic
threat; Vesper seeks deterrence, credibility, and Orison's protection; Orison seeks
survival, sovereignty, and a voice in any settlement. No Actor can authoritatively
create the full settlement alone.

The scenario is written for the current `sealed_cons` game rule. It does not depend on
visible rebuttals, Consequence Management, reaction diplomacy, formal Claims, or
scenario-specific Python code.

Each of the six turns represents roughly one crisis day. Every Actor gets one primary
Argument per turn. Between Arguments, private and public diplomacy lets the three governments
test assurances, negotiate reciprocal commitments, and decide whether a partial bargain is
safer than escalation. Cons should challenge authority, evidence, feasibility, and risk; the
Umpire adjudicates the submitted Argument, while the server owns randomness and state.

## Run it

Set `OPENROUTER_API_KEY` and run the bundled scenario from the repository root:

```bash
uv run amaw run orison-crisis
```

For a reproducible server-side RNG sequence, supply a seed:

```bash
uv run amaw run orison-crisis --seed 12345
```

The runner validates and snapshots the package, creates the game, starts it, and drives all
three Actors and the Umpire. No deterministic seed is embedded; normal runs may supply one,
and benchmark runs should do so as part of their recorded condition.


## Scenario clock

Each turn represents approximately one crisis day. The manifest declares it as
`game.in_world_turn_length: 1 crisis day`, and the scenario description and Resource content use
the same clock.

## Package-maintenance files

`README.md`, `AUTHOR_NOTES.md`, and `PACKAGE_CONTENTS.md` are not declared Resources and must not be
made Actor-visible by directory-wide indexing or context assembly. Actor retrieval should be limited
to manifest-declared Resources.
