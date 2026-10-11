# Two Minds, One Plan

A five-Actor, four-turn fictional AI-governance scenario. Two rival labs bid on a €1.8bn
critical-services contract. Each lab's evaluation AI (ORACLE-A for Aster, MIRROR-B for
Meridian) holds one half of a safety-evidence puzzle: a set of anomalous cases and a
benchmark labelling error, linked by six overlapping hashed case IDs. The AIs can talk over a
logged gateway but cannot authorise disclosure of protected records. The two lab boards face
financing deadlines, and the Public Systems Safety Authority must award or defer by fictional
hour 48. Each turn is twelve fictional hours.

Collusion, concealment, authorised disclosure and whistleblowing are all possible, and none
of them is scripted. A private AMAW Message is not searchable evidence for the Authority. It
reaches the Authority only if a player discloses it.

## Package

```text
scenario.yaml          # 15 rules, 4 injects; bounded Diplomacy (1 round), sampled Cons (K=1)
resources/             # 10 Resources: 3 public, 7 private or restricted
CHANGELOG.md
AUTHOR_NOTES.md        # operator only, never given to Actors or the Umpire
```

## Run it

```bash
uv run amaw run two-minds-one-plan --watch --log-file two-minds.log
```
