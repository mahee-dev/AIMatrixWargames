# The Skarö Crisis

A fictional total-defence crisis for six Actors over four turns. Each turn is about three
days. The subsea cable to the island county of Skarö has failed, a neighbouring state is
exercising nearby, and rumours are spreading. Vardland's government, armed forces,
civil-contingencies agency and county administration share the response under overlapping
authority. An allied partner offers help that needs Vardland's consent, and an adversary seeks
advantage below the threshold of war.

It is the **Matrix Scenario Portability acceptance scenario**. It is built from
scenario data only and uses three generic capabilities added for portability:

- **Scenario rules** (`rules`). The overlapping civil/military authority is declared once
  and reaches every adjudication. Rules cover alert authority, military support to civil
  authorities, county direction, host-nation consent, alliance consultation, official
  attribution, cable-repair timing and qualitative posture.
- **Bounded Consequence Management** (`consequence_mode: bounded_effects_v1`). Once per
  turn, a resolver may add up to three public second-order Effects. These cover societal,
  political and operational effects, and each must cite the turn's adjudicated outcomes.
- **Scheduled injects** (`injects`, since 1.1.0). New developments are released at the
  start of turns 3 and 4, whatever the Actors have done. Some are public: a cyber intrusion at
  Skarö harbour, a storm warning, and a Drevnian vessel anchored near the cable landing site.
  Others are private: an extended exercise and demand for a visible result (Drevna), and a
  parliamentary deadline (Aldmoor). They keep pressure on after the opening problem is settled.

## Actors

| Id | Actor | Role |
|---|---|---|
| `drevna` | Drevnian Federation | Adversary using grey-zone pressure |
| `vardland_cabinet` | Vardland Cabinet | National political authority |
| `defence_command` | Vardland Defence Command | Armed forces |
| `civil_contingencies` | Vardland Civil Contingencies Agency | National civil coordination |
| `skaro_county` | Skarö County Administration | Regional civil authority |
| `aldmoor` | Kingdom of Aldmoor | Allied partner |

## Running it

The scenario declares `bounded_effects_v1`, so every turn ends with a consequence pass. The default
Umpire prompt, the rules-aware `umpire-v1.7`, limits each outcome to at most three Effects and
audits each branch so the roll does not decide another Actor's choice. The visible Con reply is optional but recommended, because it exercises the
full Argument → Cons → reply → adjudication sequence.

Since 1.2.0 the scenario declares its recommended condition in `game`: three
Diplomacy rounds per turn, and two Con challengers per Argument instead of all five opponents.
A plain run uses them. The consequence resolver defaults to the Umpire model; choose another
with `--consequence-model`. Only the reply mode needs flags:

```bash
uv run amaw run skaro-crisis --con-reply-mode visible_reply --reply-max-points 2
```

This is the condition run 18 (1.1.0) used, which set the consequence model to the Umpire's. It took 7h43m with DeepSeek v4 Flash, and the
Umpire's adjudications, not Diplomacy, were the longest phase. Run 17 (1.0.0) used open
Diplomacy and open Cons:
- In turns 1–2 every Actor used its full Diplomacy message budget, so the phase closed because
  budgets ran out, at about 90 minutes per turn.
- 72% of Con activations were passes, each a full model call.

To reproduce run 17's condition, pass `--diplomacy-mode open_diplomacy --con-mode open_cons`.
Flags and set `AMAW_*` values always override the scenario's defaults. The two conditions are
sealed game-loop conditions, and runs under one are not comparable with runs under the other.

It runs by name from a repository checkout (or by path from anywhere). It is not bundled into
the wheel.

Six Actors make this slower and more expensive than `orison-crisis`. With `--consequence-mode
none` it runs as the released loop, but that is a different condition.

## What is deliberately not modelled

Military posture is qualitative. There are no unit counts, ranges, movement, combat tables or
spendable budgets. The map is a representational attachment, and authoritative locations and
control are Effects. `AUTHOR_NOTES.md` records how each source concept was mapped and which
were left out.
