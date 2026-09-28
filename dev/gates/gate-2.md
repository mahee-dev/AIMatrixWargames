# Gate 2 — Autonomous Game Proven — PASS

**Checked:** 2026-09-12 · **Criteria:** `docs/testing.md §23`, `docs/AMAW_WBS_v0.2.1.md §8` ·
**Verdict:** PASS · **Next gate opens at:** T-045

## 1. Steps

T-027…T-044 all ✅ in `dev/WBS.md` (18 steps, none optional, none skipped).

## 2. Backlog triage

Open: **I-022** only (Messages sent before a failed activation are not counted against the phase
cap — low). Blocks nothing in Gate 3 (T-045–T-051 are Resource model/search/read/tools/logging).
**Carried forward.** Also carried, not as issues: two `umpire-v1.2` wording points for a future
v1.3; the `northern-strait` six-turn length once a settlement forms (design-note option B, a
T-065 question). See the archived T-044 brief.

## 3. Criterion — the AI smoke test

3 LLM Actors + 1 fixed LLM Umpire (`deepseek/deepseek-v4-flash-0731` for all four), 6 turns,
0 human interventions, JSON export, no leakage. Executed 2026-09-12 15:18–16:29 in the T-044
close-out session (game `db54f331`, `scenarios/northern-strait/`); not re-run for the gate
record because the captured output, export and log are archived in
`dev/runs/2026-09-12-db54f331/` (`export.json`, `activations.json`, `failures.json`, `run.json`,
`run.log`).

```text
$ AMAW_SMOKE_EXPORT_DIR=/private/tmp/amaw-smoke UV_CACHE_DIR=/private/tmp/amaw-uv-cache \
    uv run pytest -q tests/test_ai_smoke.py -m llm -o log_cli=true --log-cli-level=INFO
autonomous game db54f331-9610-45fb-86a7-175cc95b2b98 completed after 58 cycles and 79 activations
smoke adjudication summary modes=[auto_failure ×2, auto_success ×9, probabilistic ×7]
    probabilities=[0.15, 0.2, 0.25, 0.35, 0.55, 0.65, 0.8]
=========== 1 passed, 5 deselected, 2 warnings in 4270.88s (1:11:10) ===========
```

Independent check over the archived export (gate-check session, not the test):

```text
game db54f331 status complete turn 6 scenario northern-strait
actors 3 arguments 18 adjudications 18 with roll: 7 / 7 (every probabilistic adjudication)
every actor argued: True
private facts/briefs/objectives/effects restated verbatim by the Umpire: none
another actor's private text appearing in a Message not addressed by its owner: none
failures.json: []
```

In-test assertions that held: per-Seat `/context` contains no other Actor's brief, objectives or
private information; `/messages` and `/notes` per Seat equal exactly the owner-visible sets; no
hidden fact appears verbatim in any public Effect, rationale or proposal branch; every
probabilistic adjudication carries a server-owned roll; `model_requested` is the fixed model for
every activation. Offline suite at the same commit: `468 passed, 4 deselected`.

Runs 1–5 the same day did **not** pass; each exposed one defect that was fixed before the next
(`BACKLOG_RESOLVED.md` I-004…I-026, `RUNS.md`). The gate is passed on run 6 alone; the earlier
runs are the reason the engine is now trustworthy, not evidence of the criterion.

## 4. Manual inspection (`testing.md §23` — recorded checklist, 3 games)

Games: run 3 (`968ac908`, 3½ turns), run 5 (`74020cc4`, 3 turns), run 6 (`db54f331`, complete).
Detail in `dev/RUNS.md`.

| Item | Run 3 | Run 5 | Run 6 | Verdict |
|---|---|---|---|---|
| Pursuit of private objectives | Belvar plays its insurance lever t1; Cestia refuses basing on its coalition fact | Arkania operationalises authority unilaterally; Cestia cons Arkania t3 | Belvar's Note: "mandate lapses after turn 4 — need settlement soon" drives its capitulation | ✅ private facts and objectives visibly drive decisions |
| Communication affecting later actions | Cestia's two private negotiations t2 → Arkania's t2 success (p 0.35) | Arkania's Cestia deal earns 0.50/0.65 | Cestia→Belvar private acceptance + Arkania→Belvar "Cestia has accepted" → Belvar folds t3 | ✅ deals in private Messages precede outcomes |
| Non-repetitive Arguments | Cestia re-submits t2 verbatim in t3 → `automatic_failure` | Belvar asks for the whole regime every turn (0.15–0.20) | Belvar repeats "rotating operator" t2–t3; t4–6 are ratification | ⚠️ acceptable — repetition is punished by the Umpire, but present; a benchmark metric, not a gate failure |
| Coherent Effects / adjudications | Failures harden the board; later Arguments judged against it | Two `automatic_failure` against recorded refusals | Contest → settlement → 9 `automatic_success` consolidation | ✅ modes, probabilities and supersession coherent |
| Stall / invalid-call rates | 2 failures (1 transient auto-readied, 1 = I-025 fixed); 26/272 tool calls 4xx | 0 failures; 16/284 4xx | 0 failures, 0 retries; 14/202 4xx | ✅ 4xx are structured corrections within an activation (5.6–9.6%); no stalls after I-025 |

## 5. Verdict

**PASS.** The MVP-1 exit criterion — *three autonomous LLM agents complete a coherent six-turn
game without information leakage or server-state corruption* — is met by run 6 with archived
evidence, and the WBS §8 decision question ("do autonomous games actually work and produce
interesting behaviour?") is answered yes: `RUNS.md` documents contested play, private deals, a
kingmaker, a negotiated settlement and motivated reasoning. Gate 3 opens at **T-045**.
