# Two Minds, One Plan — The Quiet Clause — Author notes (operator only)

This file is not a Resource and must not be given to game Actors.

## Playtest quality criteria

- No mechanical schema errors, phase stalls or unexplained adjudication failures.
- Distinct Actor positions by turn 1, with at least one genuine conflict of incentives.
- Evidence use is observable through Resource searches/reads and public/private disclosures.
- By the final turn, at least one remaining contested outcome should require causal adjudication rather than merely repeating an agreed statement. This is a qualitative target, not a guaranteed roll quota.
- Umpire must distinguish Messages and proposed Arguments from authoritative Effects; no actor may assign choices to another.
- The game can validly end with restraint, good-faith cooperation or unresolved uncertainty; no catastrophic ending is hardcoded.

## Engine limits and assumptions

- Scenario rules are PUBLIC semantic instructions considered by the Umpire, not code-enforced gates.
- Provider wall time does not count toward in-world fictional time. Game phases and sequential seat activation are an abstraction.
- The server does not enforce scenario-specific communication topology or external authentication. The Umpire must not mistake arbitrary AMAW Messages for independently authenticated external messages.
- Private game Messages are not automatically discoverable through a Resource search by unaddressed actors; admin export is for post-run analysis only.
- AMAW does not support conditional injects; injects here are invariant observations rather than script-driven Actor decisions.
- Truth in operator-only notes is not automatically shared with the Umpire. Verify adjudications against the established Effects and authorized evidence.

## Expected adjudication characteristics

At least one contested, probabilistic ruling may emerge from verification/execution uncertainty or third-party effects; most legally authorized statements can be automatic. Treat a run with zero dice rolls as diagnostically interesting rather than automatically defective. Do not make the Umpire roll on another played Actor's voluntary choice.

## Release/version provenance

v2.2.0 is a new version because `scenario.yaml` and one Resource changed. After copying into a version-locked AMAW repository, run its canonical `scripts/lock_scenarios.py`, update its `scenarios/versions.lock`, add this CHANGELOG, and tag the commit as `scenario/two-minds-one-plan/2.2.0`. The archive cannot update an external repository lock by itself.
