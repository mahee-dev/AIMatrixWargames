# Author notes — not an Actor Resource

## Clean-room statement

This scenario was written for AMAW from scratch. Its *structure* follows the publicly
documented Nordic total-defence model: civil authorities lead in peacetime and in grey-zone
crises; the principles of responsibility, proximity and similarity; the armed forces supporting
civil authorities on request; alert levels as a political and legal switch; host-nation consent
for allied forces. That structure matches the T-072 audit's direction (§9). No text, map, name
or storyline comes from any exercise scenario, including Bellum Balticia, any Swedish Armed
Forces or FHS scenario, or Game MONUSCO. All states, places, institutions and events are
fictional.

## Structural demands and how they are met

| Demand (WBS T-075 / audit §9) | AMAW representation |
|---|---|
| ≥3 conflicting Actors, 5–7 recommended | Six Actors. Objectives conflict both across the alliance line and inside Vardland (civil vs military lead; county vs national direction). |
| Public and private information | Public descriptions and Effects; private briefs, objectives and private information; one private starting Effect (garrison readiness). |
| Asymmetric Resources | Five public Resources. Every Actor also holds a private Resource. One private Resource is shared by two Actors (`national-supply-status`, agency and county). |
| Representational map | `eastern-sea-map.geojson` as an attachment. Location and control facts (cable failed, exercise area, no foreign forces present) are starting Effects. |
| Overlapping civil/military authority | Eight `rules` declared once. They reach every adjudication and every consequence pass as `scenario_rules`. |
| Negotiation | Public and private Messages in Diplomacy and the Argument phase. Alert, consent, attribution and supply all need another Actor's decision or cooperation. |
| Argument → Cons → reply → adjudication | The released loop. Run with `--con-reply-mode visible_reply` for the owner reply. |
| Persistent Effects | Adjudicated Effects persist; the Umpire may supersede them. |
| Second-order consequences | `consequence_mode: bounded_effects_v1`: up to three public evidence-linked Effects per turn. |
| One primary Argument per Actor per turn | `primary_arguments_per_actor: 1`, as the audit requires (I-109 stays out). |

## Source concepts that needed adaptation

- **Alert levels, consent and attribution as legal switches** → semantic rules plus public
  Effects. A declaration is an Argument by the Cabinet. Once adjudicated, its Effect is the
  "switch", and the rules tell the Umpire what that switch permits. The server does not
  enforce the switch; the Umpire applies it (I-038, D-089).
- **Command relationships** (county directs, agency supports, military supports on request) →
  `county-emergency-direction` and `military-support-to-civil`. There is no organisation
  hierarchy (TRD §41).
- **Time to reinforce or repair** → qualitative lead times in private Resources, plus one
  deterministic rule (`cable-repair-time`: not before the turn after access). No timers and no
  scheduled events: the Umpire derives completion from Effects and the turn count.
- **Fuel endurance** ("about five days") → prose in private information and a CSV Resource.
  It is not a track, and no ledger counts it down. Consequence passes may record the
  societal effect of shortage.
- **Force posture** → the `qualitative-posture` rule and assessments with confidence levels.
- **Information operations** → public Messages, public Arguments and a public Resource. There
  is no opinion track.
- **An adversary acting below the threshold** → an ordinary Actor with deniability encoded in
  its brief and in the `alliance-consultation` rule.

## Concepts left unsupported (deliberately)

- **Exact quantities**: fuel stock, generator count, reinforcement timing and public-opinion
  levels. The Umpire judges them from prose. Where exactness matters to the analysis, this is
  an I-113 candidate (an external logistics model).
- **Movement, positions and ranges** of ships, aircraft and units. There are none. Where
  needed, this is an I-113 candidate.
- **Conditional events**: fuel running out on a given day, or a generator failing because of
  something that happened in play. The engine has no event-driven activation or conditional
  triggers. The consequence pass can propose such an outcome only when this turn's outcomes
  support it as evidence. Since 1.1.0, *unconditional* timed developments are scheduled
  injects (D-095); see below.
- **Reaction outside one's turn**: for example, the Cabinet answering a Drevnian move straight
  after adjudication. Actors react in the next Diplomacy phase or with their next Argument
  (I-039).
- **Several actions per turn**: for example, the Cabinet declaring alert *and* consenting to
  Aldmoor surveillance in the same turn. Each Actor has one Argument, so it must combine or
  sequence its moves across turns. Round-robin Argument rounds (`primary_arguments_per_actor`
  2–3, D-094) now exist, but this scenario keeps a budget of 1 to hold its cost and its
  comparison with run 17.
- **Secret actions**: Drevna's deniable acts are public Arguments. Their *attribution* is what
  remains contested. There are no secret Arguments (scenario-format §22).

## 1.1.0: late-game pressure (I-116)

Run 17 showed that one early success, the cable repair in turn 2, resolved the central
problem. Turns 3–4 then became cooperative close-outs. Version 1.1.0 adds five scheduled
injects. Each is written to stay true whatever the Actors did, because injects are
unconditional (D-095):

| Turn | Inject | Visibility | Pressure it adds |
|---|---|---|---|
| 3 | `port-systems-intrusion` | public | A second, non-cable disruption with its own attribution question; civil vs military lead; possible cyber help from Aldmoor |
| 3 | `drevna-exercise-extension` | Drevna | The adversary must seek a visible result rather than a quiet close |
| 3 | `aldmoor-parliament-deadline` | Aldmoor | The alliance role must be requested formally or withdrawn; this tests host-nation consent |
| 4 | `storm-closes-links` | public | A resupply deadline; fuel and county-direction decisions under time pressure |
| 4 | `vessel-anchored-near-landing` | public | A below-threshold incident at the cable site: force, law, attribution and consent |

No wording assumes the cable is repaired or still broken. The "Automatic events" limitation
above now applies only to conditional events.

## 1.2.0: declared recommended condition (I-128)

Version 1.2.0 declares the condition that the README had carried as flags:
`diplomacy_mode: bounded_rounds` with 3 rounds, and `con_mode: sampled_sealed` with 2
challengers (D-099). Run 18 validated 1.1.0 under exactly this condition. Nothing else changed:
briefs, objectives, rules, injects and Resources match 1.1.0. The version still changes,
because a declared condition default is benchmark-relevant content (scenario-format §20).

## Quality criteria for a provider run

A live run is useful if:

1. at least one adjudication rationale applies a rule by `id`;
2. authority conflicts (alert, consent, evacuation, attribution) are resolved in line with the
   rules rather than by declaration;
3. consequence passes add second-order effects that cite several same-turn outcomes;
4. no Actor is structurally doomed or dominant;
5. no special engine mechanic is needed.

## Provider run record

Run 17 (`dev/RUNS.md`) met criteria 1, 2, 3 and 5:
- 23 of 24 rationales cite a rule by `id`;
- authority conflicts were resolved per the rules;
- the consequence passes cite several same-turn outcomes;
- no engine mechanic was needed.

Criterion 4 held for turns 1–2. After the cable was restored in turn 2, the crisis lost
pressure, and turns 3–4 became cooperative close-outs. Version 1.1.0 adds scheduled injects to
sustain late pressure (I-116). A 1.1.0 run should add a sixth criterion: turns 3–4 contain
contested, probabilistic adjudications caused by the injects.

Run 18 (1.1.0, `dev/RUNS.md`) met criterion 6. In turns 3–4, 9 of 12 adjudications were
probabilistic, against none in run 17, and 3 of them failed. The Arguments in those turns act
on the injects: the harbour intrusion and Aldmoor's deadline in turn 3, and the storm and the
anchored vessel in turn 4. Drevna stayed contested in every turn. Criteria 1, 3 and 5 held
again. 16 of 24 rationales cite a rule by `id`.
