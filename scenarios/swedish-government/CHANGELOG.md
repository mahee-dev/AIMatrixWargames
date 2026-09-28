# Changelog — `svensk-regeringsbildning-2026`

The package directory is `swedish-government`, and the bundled name is the same. Every
version changes the sealed scenario hash, so runs under different versions are never pooled.
The hashes are in [`../versions.lock`](../versions.lock). Recover an old version with
`git checkout scenario/svensk-regeringsbildning-2026/<version> -- scenarios/<directory>`. Up to
version 6 the directory was `swedish-government-v6`; it was renamed without a content change
(I-144). The old name is no longer recognised; `amaw run swedish-government-v6` fails like any
unknown name and lists `swedish-government` among the names that work.

## 7.1.0

2026-09-28. Tuning after run 22, the first 7.0.0 playtest (I-161, I-163). Same Actors, briefs,
objectives, seats, turn order and four 48-hour turns as 7.0.0. Runs under 7.1.0 are never pooled
with runs under 7.0.0.

- **Bounded Diplomacy.** `game` declares `diplomacy_mode: bounded_rounds` with 3 rounds per turn.
  Under open Diplomacy, three of run 22's four Diplomacy phases ran 13 rounds and closed only when
  four parties hit the no-progress ceiling. A flag or `AMAW_*` value still overrides (D-099).
- **Internal approval.** A sixth rule, `intern-foerankring`: joining a government, a budget or
  cooperation agreement, or breaking a public red line also needs the party's own board or
  Riksdag group, which no Actor plays. The Umpire may resolve that approval by probability; the
  leadership's own commitment stands either way. Run 22 drew no roll in 32 rulings, because every
  party that matters is played and the vote is arithmetic, and D-118 never rolls a played
  party's decision. `regeringsregler.md` §12 is rewritten to match, and §11 says where
  probability belongs.

## 7.0.0

2026-09-25. A post-election factual update, frozen at the information state of
25 September 2026, 12:00 CEST (I-145). Same Actors, turn order, four 48-hour turns, and private
incentives as 6. Runs under 7.0.0 are never pooled with runs under 6.

- **Seats.** `valresultat_2026.csv` holds the final result that Valmyndigheten fixed on
  19 September: S 99 and V 30 (6 had S 100 and V 29). The blocs are unchanged at 176–173.
- **Starting state.** The real procedure replaces 6's fictional one. The result is fixed,
  Kristersson asked to be released on 17 September and leads a caretaker government, and
  Andersson has held the Speaker's exploratory assignment since 18 September. No proposal has
  been voted on.
- **Rules.** There is a five-rule kernel in `scenario.yaml`: the 175-no test, the four vote
  states, own commitments only, multiparty acceptance, and seats that allegations do not
  change. The explanation stays in `regeringsregler.md`, which now states the actual procedure
  (§2), the budget deadline (§6) and seats versus votes present (§11.1). Because the scenario
  declares rules, the default Umpire prompt is now `umpire-v1.6`, and a pinned
  version must be v1.5 or v1.6. That is part of the 7.0.0 condition.
- **Resources.**
  - Public positions are split into before and after the election.
  - Three new Resources, each with dated sources:
    - `regeringsbildning-status` covers the timeline, the budget clock and the game's
      endpoint;
    - `valintegritet-2026` covers the Borlänge investigation and the separate early-voting IT
      fault, with every claim marked as established, alleged or undecided;
    - `forhandlingsfragor` covers the documented fault lines.
- **Removed.** `resources/VALRESULTAT_REQUIRED.md`, a leftover note that was never a declared
  Resource.
- **Public descriptions** now state each party's post-election position. The private briefs
  are unchanged, and none mentions the election-integrity case.

## 6

2026-09-14. Eight parties form a government after the 2026 Riksdag election, played once the
seat distribution is known. `AUTHOR_NOTES.md` states the v6 hypothesis. Played by runs 8 and 9.

Earlier versions: v1 and v5 are kept in `scenarios/archive/`; v4, which run 7 used, was never
a separate directory and survives only as the hashes in that run's export.
