# Changelog — `skaro-crisis`

Every version changes the sealed scenario hash, so runs under different versions are never
pooled. The hashes are in [`../versions.lock`](../versions.lock); recover an old version with
`git checkout scenario/skaro-crisis/<version> -- scenarios/skaro-crisis`.

## 1.2.0

2026-09-25 (I-128). Declares the recommended condition in `game`: `diplomacy_mode:
bounded_rounds` with 3 rounds and `con_mode: sampled_sealed` with 2 challengers. Nothing else
changed. A bare `amaw run` now plays the condition run 18 validated.

## 1.1.0

2026-09-24 (I-116). Adds five scheduled injects in turns 3–4 so that the late game stays
contested; `AUTHOR_NOTES.md` explains each one. Played by run 18.

## 1.0.0

2026-09-23 (T-075). First version: the Matrix Scenario Portability acceptance scenario, with
six Actors, eight rules and `consequence_mode: bounded_effects_v1`. Played by run 17.
