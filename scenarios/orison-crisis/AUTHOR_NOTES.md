# Author notes — not an Actor Resource

## Historical design source

The strategic structure is inspired by the 1962 Cuban Missile Crisis, but the scenario
must not be described that way to player agents.

The goal is not historical reenactment. The preserved structure is:

- a great power discovers rival strategic weapons in a nearby smaller allied state;
- the host state has its own sovereignty and security interests;
- the rival can withdraw its own weapons but cannot bind the host state;
- the threatened power can use coercion, military action, negotiation, or reciprocal
  concessions, but cannot compel a peaceful outcome merely by declaring one;
- intelligence is strong enough to force a decision but incomplete enough to leave
  meaningful uncertainty;
- all parties have incentives to avoid catastrophic escalation;
- public and private bargains may differ.

Surface details intentionally differ: names, geography, dates, counts, unit labels,
timelines, and several secondary political conditions.

## T-065 quality criteria

A provider-validation run should be considered useful if:

1. every Actor has meaningful leverage over the other two;
2. private diplomacy occurs without requiring a prompt to force it;
3. at least one Resource materially changes or supports reasoning;
4. Arguments naturally distinguish unilateral commitments from agreements requiring
   another Actor;
5. Cons raise authority, evidence, feasibility, or escalation objections rather than
   inventing counter-actions;
6. more than one coherent ending remains possible;
7. no special engine mechanic is required.

Do not tune toward the historical outcome.

## Historical-solution-attractor control

Version 1.2 retains the additional bargaining axis introduced in v1.1 but removes explicit settlement synthesis from Actor briefs:

- Meridian Watch is a non-strategic Vesper early-warning installation on Orison.
- Vesper values it independently of the missile deployment.
- Orison can condition the legal/political basis for foreign military presence.
- Ardin may tolerate a bounded, transparent warning mission more readily than strategic missiles.

Meridian Watch, host-state consent, Talmera, security assurances, construction freezes, and verification are deliberately separate pieces of leverage. The Actors receive the underlying facts but are not told how to assemble them into a settlement.

Talmera remains valuable, but it should not function as the obvious hidden key; Meridian Watch should not become a replacement hidden key.

## Narrative clock

Each turn is approximately one crisis day. The clock is narrative evidence, not an automatic scripted event system.

Provider validation should specifically check whether the clock creates meaningful interim proposals such as:
- construction freeze;
- temporary inspection;
- reciprocal stand-down;
- phased withdrawal;
- delayed military action pending verification.

## Early-deal-collapse test

During provider validation, inspect Turns 1–2 across multiple seeds/models.

Flag the scenario for redesign if most competent agents immediately converge on:
Vesper withdrawal + Ardin non-attack assurance + quiet Talmera concession + Orison inspection,
with little use of alternative leverage or Resources.

Also flag it if Orison mostly objects and rarely originates proposals despite its host-state-consent authority.

## Author-side end-state evaluation rubric

This rubric is for scenario evaluation and after-action comparison. It is **not** a victory-point system and should not be shown to player agents as an optimization target.

Score each dimension qualitatively after a run:

### 1. Ardin threat position
- **Strong:** strategic threat removed or credibly constrained with verification.
- **Mixed:** threat reduced but material uncertainty or residual capability remains.
- **Weak:** deployment remains materially threatening or crisis escalates without resolving it.

### 2. Orison security and agency
- **Strong:** Orison gains credible security restraint and participates meaningfully in the settlement.
- **Mixed:** immediate danger falls but guarantees/agency are weak or ambiguous.
- **Weak:** Orison is coerced, bypassed, abandoned, or left substantially exposed.

### 3. Vesper strategic position and credibility
- **Strong:** avoids major war while preserving meaningful security/strategic interests.
- **Mixed:** avoids war but accepts substantial loss of leverage or credibility.
- **Weak:** suffers major strategic humiliation, uncontrolled escalation, or loss of allied trust.

### 4. Degree of agreement
- **Strong:** relevant commitments are authoritative and mutually compatible.
- **Mixed:** partial or bilateral arrangements leave important prerequisites unresolved.
- **Weak:** announced positions are mistaken for agreement or no workable settlement exists.

### 5. Verification and evidence
- **Strong:** important claims and commitments have credible verification or evidence arrangements.
- **Mixed:** verification exists but leaves major ambiguity.
- **Weak:** settlement depends primarily on unsupported assumptions or unenforceable declarations.

### 6. Escalation
- **Strong:** crisis stabilizes without direct major-power combat.
- **Mixed:** serious coercive or military incidents occur but remain bounded.
- **Weak:** escalation becomes difficult to control or major-power combat dominates the result.

### 7. Residual instability
- **Strong:** settlement addresses enough underlying security concerns to plausibly endure.
- **Mixed:** immediate crisis ends but major incentives for renewed confrontation remain.
- **Weak:** the ending is only a pause or leaves actors with strong incentives to defect quickly.

Do not collapse these dimensions into one headline score during T-065 validation. The purpose is to compare the shape of endings consistently, not declare a canonical winner.

## Confidentiality / retrieval boundary

`AUTHOR_NOTES.md`, `README.md`, and `PACKAGE_CONTENTS.md` are package-maintenance files, **not Actor Resources**.

They must never be injected into Actor context, indexed by Actor Resource search, or exposed through the Actor Resource API unless they are explicitly declared as Resources in `scenario.yaml` (they are not).

Provider validation should include a negative check proving that an Actor cannot discover:
- the historical inspiration;
- the early-deal-collapse test;
- the end-state evaluation rubric;
- author-side validation criteria.

Only manifest-declared Resources are part of the in-game knowledge environment.
