# Nine Minutes to Zero

A five-Actor, three-turn fictional strategic-warning crisis. At simulated 00:00 Aster's
SENTINEL-S8 AI issues a high-confidence attack warning that its own human analysts dispute,
while Boran is running an announced exercise whose vehicle movements have just been
photographed. Two national Leaderships, their two Strategic Commands and a neutral Joint
Verification / Hotline Cell each hold one piece of the picture. Each turn is a three-minute
decision window (00:00–09:00); provider latency never advances the fictional clock.

The scenario stays at leadership, readiness, communications and assessment level: no launch
procedures, targeting or weapon employment are represented. Escalation, restraint or
unresolved uncertainty all emerge from play; nothing catastrophic is scripted.

## Package

```text
scenario.yaml          # 17 rules, 3 injects; bounded Diplomacy (1 round), sampled Cons (K=1)
resources/             # 10 Resources: 3 public, 1 restricted to the hotline parties, 6 private
CHANGELOG.md
AUTHOR_NOTES.md        # operator only, never given to Actors or the Umpire
```

## Run it

```bash
uv run amaw run nine-minutes-to-zero --watch --log-file nine-minutes.log
```
