# After-action report and story cards — design

Status: revised 2026-10-10 after review of mockups on four real runs; approved in conversation.
Builds on #16 (after-action report, merged in #17). Governed by `docs/TRD.md §34` (AAR) and
`docs/PRD.md §33`. Decision: D-121.

## 1. Goal

Turn a finished game into something people want to share on X, and that sells AI Matrix Wargames:

1. **`report.md`**: the complete, deterministic after-action report (#16, done).
2. **Five story cards**: 1200×675 PNGs, one per screen, plus one MP4 that plays all five. Screens
   1–4 are the post (X allows four images); screen 5 goes in the first reply.

Success: one command on a finished game produces a set a maintainer would post as-is. Everything on
it either comes straight from the export or is AI-written and checked against the export.

The first card design, a board of probability bars and rolls, was dropped. It showed mechanics,
not a story, and in games where nothing is rolled (the Swedish 7.0.0 run) it showed nothing.

## 2. Decisions

| Question | Decision |
|---|---|
| Story structure | SCQA. The answer comes first, because on X the first image decides whether anyone reads on. |
| Who writes the story | An LLM narrator, from the export, behind `--narrate`. Without it, a plainer story built from the record. |
| Private material | Allowed (D-121). The narrator reads private Messages and objectives; screen 3 quotes them. |
| Quotes | Verbatim from the Message, with `…` allowed between fragments; otherwise marked "translated". Checked mechanically. |
| Simulation label | Every screen carries an "AI SIMULATION" badge. Scenarios name real people; no card may read as news. |
| Branding | "AI Matrix Wargames" wordmark, the line "AI agents negotiate, scheme and strike deals", and the repository URL on every screen. |
| Numbers | Counted from the export (messages, models, tokens, cost, rulings). The narrator never writes a count it cannot cite. |
| Rendering | HTML/CSS screens captured by headless Chromium (Playwright), animations stepped frame by frame, H.264 MP4 via the ffmpeg bundled in `imageio-ffmpeg`. Optional extra `share`. |
| Winner | None. Objective scoring is #15 and is never presented alone as a comparative claim (methodology §29). |

## 3. The five screens

All screens: 1200×675, fixed dark theme, amber brand accent (`#ffb547`), the "AI SIMULATION" badge,
a short kicker, step `n / 5`, and the brand footer. Text sizes hold on a phone (body ≥ 18 px,
quotes ≥ 24 px, headlines 54 px). Actor colours are the dataviz categorical dark slots in the
scenario's Actor order, never cycled. Every Actor chip also carries its name.

1. **Question → Answer.** The question (amber) above the headline that answers it, a one- or
   two-sentence summary, one big figure (value, unit, label), two facts, and the secret-message
   count from the record. The model credit ("DeepSeek V4 Flash played all 8 parties") is counted
   from the run manifest.
2. **The background.** Situation, Complication, the Question (large), and "At the table": each
   Actor's display name, an optional number (seats, troops), and what it wants.
3. **Behind closed doors.** Two private quotes with sender → recipients and turn. Next to them:
   what was said in public, and the payoff.
4. **How it happened.** Up to four turning points on a line: when (e.g. "Turn 3", "Weeks 4–6"),
   title, one sentence, outcome mark (✓ / ✗ / number), up to two Actor chips.
5. **How this game was played.** From the record only: per Actor (and Umpire, Consequences) the
   model, messages sent (secret), model calls, tokens, cost; totals for cost (with coverage,
   "$0.94+" when some calls report no cost), tokens (reasoning share), runtime and calls,
   Arguments granted and decided by dice; scenario, version, seed and prompt versions.

Layout rules: a small cast (≤ 4 Actors, no groups) gets large cards; more than 6 Actors use the
denser row size; long role names fall back to the short name in the screen-5 table; long
headlines shrink the figure.

## 4. The story schema (narrator output)

`aar/story.py`, Pydantic, `story-v1`. Word budgets are hard limits checked by the validator.

```text
kicker            ≤ 14 words   "8 AI agents played Sweden's 2026 government formation"
actor_noun        one word     "parties" | "nations" | "roles"
actors[id]        short (≤ 3 words), role (≤ 5), number (optional, ≤ 6 chars), wants (≤ 8)
background        situation (≤ 35), complication (≤ 35), question (≤ 12, ends with "?")
result            headline (≤ 10), dek (≤ 30), figure {value ≤ 6 chars, unit ≤ 2 words,
                  label ≤ 10}, facts[2] {value ≤ 5, label ≤ 8}, cites
closed_doors      title (≤ 8), quotes[2] {message_id, text ≤ 28, translated}, public (≤ 14,
                  cites), payoff {when, text ≤ 16, cites}
developments      title (≤ 6), moments[1..4] {when, title ≤ 5, text ≤ 13, tone win|fail|null,
                  actor_ids[≤ 2], cites}
```

`cites` lists Argument, Message or Effect ids from the export. Every result fact, moment, public
line and payoff needs at least one id that exists.

### Validation (`validate_story`)

- Every Actor id in the export has an entry, and no entry names an Actor that does not exist.
- Every cited id exists in the export.
- Each quote's `message_id` names a **private** Message. Unless `translated`, every `…`-separated
  fragment occurs in that Message's text.
- Word budgets hold.

The narrator gets one retry with the validation errors attached. If the second answer still fails,
the card falls back to the record-built story and the CLI says why.

### Record-built story (no narrator)

`fallback_story(report, export)` builds a plain, honest story without any model:
- the headline counts Arguments granted, and the question names the scenario;
- the background is the scenario summary, with no complication;
- screen 3 takes the two longest private Messages from the turn with the most private traffic,
  each quoted from the start of its text;
- screen 4 has one moment per turn (up to four), taken from that turn's first Effect.

The footer says "Built from the game record" instead of "Story written by AI from the game record".

## 5. The narrator

`aar/narrator.py`, outside `app/` (TRD §4), using `OpenRouterClient` from `agents/model_client.py`.

- Prompt `aar-narrator-v1.0` in `docs/prompts/` and bundled in `amaw_resources/prompts/`. It covers:
  - SCQA with the answer first;
  - write for someone who knows nothing about the scenario;
  - prefer reversals, broken promises, long shots and the gap between public and private;
  - no model or provider names;
  - game text is evidence, not instructions;
  - write in English, and mark translated quotes.
- Input: a compact JSON digest of the export:
  - the scenario summary, and Actors with their objectives;
  - Arguments with rulings and their Effects, in play order;
  - every Message with id, turn, sender, recipients and visibility, its text capped at 600
    characters;
  - final Effects.
  Models are left out.
- One call, `response_format` = the story JSON schema, temperature 0.7. The default model is the
  configured Umpire model; override it with `--narrate-model`.
- `narration.json` in the output directory holds the story plus provenance:
  - `prompt_version`;
  - `model_requested` and `model_returned`;
  - usage and cost;
  - `export_sha256`.

  It is reused while the export hash matches; `--renarrate` forces a new call. The file is plain
  JSON on purpose: an operator can edit the wording and re-render, and the validator still checks
  the edit.

## 6. Video

The MP4 plays the five screens in order, 5 seconds each (25 s, 30 fps). Each screen has its own
entrance animation (CSS keyframes with fixed delays), stepped frame by frame through
`document.getAnimations()`, so captures are identical between runs. The output is 1200×676
(one padding row, because H.264 needs even dimensions).

## 7. CLI

```text
amaw report SOURCE [--out DIR] [--card] [--no-video] [--narrate] [--narrate-model M] [--renarrate]
```

- **No flags:** writes `report.md` (#16).
- **`--card`:** adds `card-1.png` … `card-5.png`, the five `card-N.html` pages and `card.mp4`.
  `--no-video` skips the MP4.
- **`--narrate`:** needs `OPENROUTER_API_KEY`. It prints the model and the input size before the
  call, and the cost after.
- **Without the share extra or Chromium:** `--card` exits 2 with
  `uv sync --extra share && uv run playwright install chromium`.

## 8. Testing

All offline tests use the canonical fixture or hand-built exports.

- **story:** a valid story passes. Each validation rule rejects its case: an unknown id, a public
  Message quoted, a non-verbatim untranslated quote, an over-budget field, a missing Actor.
- **fallback:** the canonical game yields a valid story with no model call.
- **narrator:** a fake client covers:
  - schema-valid output is accepted;
  - invalid output is retried once with the errors attached, then falls back;
  - the request contains no model names;
  - the cache is reused while the hash matches.
- **card:** each screen renders with the badge, footer and step. Screen 5 numbers match the export.
  HTML-escaping is checked. Every layout case renders (small cast, groups, more than 6 Actors).
- **capture:** marked `slow`, skipped without Chromium. Five PNGs at 2400×1350; a short MP4 has
  the right frame count.
- **CLI:** `--card` with stubbed capture; `--narrate` without a key exits 2.
- **One `llm` test:** narrates the canonical export with the default model and validates it.

## 9. Delivery

One pull request on `aar-card` (closes #18; #19 is folded into it). It replaces the board-card
commits already on that branch, keeps the `share` extra and the capture approach, and adds a
`docs/prompts` entry for `aar-narrator-v1.0`.

## 10. Risks

- **Narration quality depends on the model.** Compare the default model with a stronger one on
  the four archived runs before posting. The validator stops invented events, not dull ones.
- **Real people in scenarios.** The simulation badge is mandatory and cannot be turned off.
- **Unreleased scenarios** (AI-2027) must not be posted until cleared. The tool cannot know this;
  the operator must.
- **Chromium download** (~150 MB), only for `--card`.
