# After-action report and share card — design

Status: approved in conversation 2026-10-10, pending spec review.
Builds on #16 (after-action report). Governed by `docs/TRD.md §34` (AAR) and `docs/PRD.md §33`.

## 1. Goal

Turn a finished game into something people want to share. Three products, all derived after
the game from its export, none able to affect gameplay (TRD §34):

1. **`report.md`** — the complete, deterministic after-action report #16 asks for.
2. **A share card** — a 1200×675 image (`card.png`) and a ~12 s animated video (`card.mp4`)
   sized for X's large-image card, drawn from the same data.
3. **An optional narrated headline** — an LLM-written headline and short story beats, shown on
   the card, behind an explicit flag.

Success: one command on a finished game produces a card a maintainer would post as-is, and every
fact on it traces to the export.

## 2. Decisions taken

| Question | Decision |
|---|---|
| What carries the card | Deterministic highlights from the export (A), plus an optional LLM headline (B). No "winner" until #15 lands. |
| Private material on the card | Allowed. The card and the narrator may use private Messages, objectives and private briefs. It is the operator's own game. |
| Rendering | HTML/CSS card, captured by headless Chromium (Playwright). |
| Video | Deterministic frame stepping, H.264 MP4 via `imageio-ffmpeg`. No GIF. |
| Dependencies | New optional extra `share` (`playwright`, `imageio-ffmpeg`). Core install unchanged. |

Not in scope: a winner or score panel (#15; methodology §29 forbids presenting it alone as a
comparative claim), GIF output, posting to X, any web UI (workbench, #1), styling themes.

## 3. Architecture

New top-level package `aar/`, a sibling of `benchmark/`. It lives outside `app/`, so the model
call in the narrator respects TRD §4 (OpenRouter stays outside the core game domain).

```text
export.json ──► aar/model.py      build_report(export) → AfterActionReport   (pure, deterministic)
                   │
                   ├─► aar/markdown.py   render_markdown(report) → report.md
                   ├─► aar/narrator.py   narrate(report, export, client) → narration.json   (--narrate)
                   └─► aar/card.py       render_card_html(report, narration?) → card.html
                                             └─► aar/capture.py  → card.png, card.mp4      (--card)
```

**Input is only an export.** `aar/` never opens the database. This makes it work on any export,
including archived runs from `amaw-runs`. `amaw report GAME_ID` writes the export through the
existing `_write_export` path first, then reads it back.

### 3.1 `aar/model.py` — the report model

`build_report(export: dict) -> AfterActionReport`, a set of Pydantic models. No I/O, no
randomness, no clock. Same export in, same report out.

Contents:

- **Game**: scenario id and version, `scenario_summary`, turns played, `max_turns`, status, seed.
- **Actors**: id, name, `public_description`, objectives, and the model that played it. The
  model comes from `model_assignments` when present; otherwise from the `agent_runs` of that
  Actor (`model_returned`, falling back to `model_requested`); otherwise `"unknown"`.
- **Turns**, each with:
  - its Arguments: author, `action`, `desired_effect`, `pros`, status;
  - each Argument's Cons (author, reason) and the owner's reply points;
  - the ruling: `mode`, `probability`, `roll`, `success`, `rationale`;
  - the Effects it created (`source_argument_id`), and consequence-pass Effects for the turn;
  - the turn's Messages, each marked public or private with sender and recipients.
- **Highlights**, computed deterministically. Ties break by turn, then by Argument id:
  - `long_shot`: the successful rolled Argument with the lowest probability;
  - `bust`: the failed rolled Argument with the highest probability;
  - `most_contested`: the Argument with the most Cons;
  - `swing_turn`: the turn that created the most Effects;
  - each is absent when no Argument qualifies (for example, no rolled Arguments).
- **Tallies per Actor**: Arguments granted, failed, decided without a roll; Cons raised;
  public and private Messages sent.
- **Rolled vs. unrolled**: the two lists #16's closing section needs.

Fields the export can lack (legacy exports, games without a run manifest, `probability: null`
for non-rolled modes) map to `None` and are rendered as such. They are never guessed.

### 3.2 `aar/markdown.py` — `report.md`

Exactly #16's contract:

1. Header: scenario, version, turns, Actors with their models.
2. `## Turn N` per turn: Messages (each prefixed **[public]** or **[private → recipients]**),
   Arguments with Cons and replies, the ruling as `mode · p=0.35 · roll=0.62 → failed`, and the
   resulting Effects.
3. `## Highlights`.
4. `## Rolled and unrolled Arguments`.
5. A footer stating the report was generated from export format version N and that it includes
   private material, since the export is an administrative record.

### 3.3 `aar/narrator.py` — optional headline (`--narrate`)

- Input: the report model plus the private material from the export (objectives, private
  briefs, private Messages).
- Output (`Narration`, validated by Pydantic): `headline` (≤100 characters) and 3–5 `beats`.
  Each beat has `text` (≤140 characters) and `cites`: one or more Argument or Message ids.
- **Citation check:** a beat whose ids do not all exist in the export is dropped. If fewer than
  two beats survive, or the headline is missing, the narration is discarded and the card renders
  without it, with a warning. The narrator can embellish wording but cannot invent an event.
- Prompt: `docs/prompts/aar-narrator-v1.0.md`, versioned like every other prompt. Superseded
  versions move to `docs/prompts/superseded/`.
- Model: `--narrate-model`, defaulting to the Umpire model in settings. The call goes through the
  existing OpenRouter client in `agents/` (`OpenRouterClient` in `agents/model_client.py`), not a new client.
- `narration.json` records `prompt_version`, `model_requested`, `model_returned`, the token
  counts and cost, and the export's `manifest_sha256` or file hash. Re-rendering the card reuses
  it. `--narrate` calls the model again only when that file is missing or `--renarrate` is passed.
- The card shows "Headline by <model> · AI-written" in its footer whenever narration is present.

### 3.4 `aar/card.py` — `card.html`

One self-contained HTML file: inline CSS, inline JS, the data embedded as
`<script type="application/json" id="aar-data">`, system fonts plus one Google Font fallback
that is not required to render. No network access is needed to render it.

The viewport is fixed at 1200×675. Animation (≈12 s, all CSS/Web Animations, no timers):

| Time | Scene |
|---|---|
| 0–1.5 s | Scenario title, "N turns · M Actors" |
| 1.5–3.5 s | The Actors, each with its model name |
| 3.5–9 s | Turn by turn: each rolled Argument's probability bar fills, the roll marker drops, a GRANTED / DENIED stamp lands. Unrolled Arguments flash their mode. Cards compress when there are many Arguments, so the scene always fits 5.5 s. |
| 9–10.5 s | The best highlight: `long_shot`, else `bust`, else `most_contested` |
| 10.5–12 s | Final scorecard: per-Actor tallies, the narrated headline if present, AMAW wordmark. This frame is the PNG. |

Text that would overflow is truncated with an ellipsis at render time. Long scenario names
shrink to a minimum size, then truncate.

### 3.5 `aar/capture.py` — PNG and MP4

- Launch Chromium with Playwright at 1200×675, `device_scale_factor=2` for the PNG and 1 for video.
- Pause every animation (`document.getAnimations()`), then for frame k at 30 fps set
  `currentTime = k × 1000/30` on each and screenshot. Stepping frames makes the output identical
  between runs and independent of machine speed.
- Encode the frames with `imageio-ffmpeg` to H.264, yuv420p, 30 fps MP4, which X accepts.
- `card.png` is the final frame at 2×.
- If Playwright or its Chromium is missing, `--card` fails with the exact install command:
  `uv sync --extra share && uv run playwright install chromium`.

## 4. CLI

```text
amaw report SOURCE [--out DIR] [--card] [--narrate] [--narrate-model MODEL] [--renarrate] [--no-video]
```

- `SOURCE` is a game id (exported from the configured database first) or a path to an export
  JSON.
- The default `--out` is `<export dir>/<game id>-aar/`.
- With no flags it writes `report.md` only. This is #16's behaviour, so the first PR is complete
  on its own.
- `--card` adds `card.html`, `card.png` and `card.mp4`. `--no-video` skips the MP4.
- `--narrate` requires the agent stack (`_require_agent_stack`) and an OpenRouter key, and
  prints an estimated cost before calling the model.

## 5. Testing

The tests follow the repository's layers. Nothing here needs an OpenRouter key except tests
marked `llm`.

- **Model** (`tests/test_aar_model.py`): build the report from the canonical deterministic
  fixture's export. Check the turn count, one known ruling (mode, probability, roll), the
  highlights, the tallies, and that a legacy export without `model_assignments` falls back
  correctly.
- **Markdown** (`tests/test_aar_markdown.py`): headings present, one known ruling line, private
  Messages carry the private marker, closing section lists rolled and unrolled Arguments
  correctly. This is #16's acceptance test.
- **Narrator** (`tests/test_aar_narrator.py`): a fake model client returns a narration with one
  fabricated citation; that beat is dropped. Too few surviving beats discard the narration. The
  cache file is reused without a second call.
- **Card HTML** (`tests/test_aar_card.py`): the embedded JSON matches the report, the HTML has
  no external `<script src>`, and overflow cases (a very long scenario name, 12 Arguments)
  render.
- **Capture** (`tests/test_aar_capture.py`): marked `slow`, skipped when Chromium is not
  installed. The PNG is 2400×1350; the MP4 decodes to 360 frames ±1 at 1200×675; two captures of
  the same HTML give identical PNG bytes.
- **CLI**: `amaw report export.json` writes `report.md`; `--card` without the extra fails with
  the install hint.
- **Boundary**: `tests/test_architecture_boundaries.py` is extended so that `app/` never imports `aar`.

## 6. Delivery

Three pull requests, each green on `uv run pytest`:

1. **Closes #16.** `aar/model.py`, `aar/markdown.py`, `amaw report` without `--card` and
   `--narrate`. Plus a `dev/DECISIONS.md` entry: the share card and narration belong to the
   TRD §34 AAR. They are static, post-game artifacts with no authority over game state, so they
   are not a "human UI" under TRD §41 and need no §41 amendment.
2. **New issue: share card.** `aar/card.py`, `aar/capture.py`, the `share` extra, `--card`.
3. **New issue: narrated headline.** `aar/narrator.py`, the narrator prompt v1.0, `--narrate`.

## 7. Risks

- **Chromium is a large download** (~150 MB). It is confined to the optional extra and only
  needed for `--card`.
- **Narration can mislead in tone** even with valid citations. Mitigations: the
  "AI-written" label on the card, and `narration.json` stays editable before the card is
  rendered again.
- **Private material leaves the admin record** when a card is shared. This is a deliberate
  choice (§2). `report.md`'s footer and the CLI's `--card` output both say the card may contain
  private diplomacy.
