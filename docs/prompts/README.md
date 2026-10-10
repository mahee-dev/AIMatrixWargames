# Versioned prompts

Every system prompt the reference agents have used, one file per version, never edited after
introduction. Every run manifest seals the version it used and its hash, so old files stay for
reproducibility even when nothing loads them any more.

The versions the code can load sit in this directory and are packaged into the wheel. A version
that can no longer be loaded moves, unchanged, to `superseded/` and is not packaged (D-103).

A prompt version is a sealed experimental condition (`docs/benchmark-methodology.md`). Changing
one is a new version with an explicit rationale, never an in-place edit.

## What the code loads today

Since D-048 the player prompts come in pairs: an even minor version for the `tool_loop`
activation mode, the following odd one for `single_shot` (D-046). Since D-060 there is a
second pair for the `visible_reply` Con-reply treatment. Since D-096 a scenario that declares `rules` runs the rules-aware `umpire-v1.6` (D-090 introduced
`umpire-v1.5`, which stays selectable). Since D-114 every game defaults to `umpire-v1.7`
(D-112); v1.3–v1.6 stay selectable. `agents/player.py`
(`PLAYER_PROMPT_VERSIONS`, `REPLY_PLAYER_PROMPT_VERSIONS`) and `agents/umpire.py`
(`UMPIRE_PROMPT_VERSIONS`) are authoritative; `tests/test_release_readiness.py` checks
this table against them.

| File | Role | Status | Introduced by |
|---|---|---|---|
| `player-v1.13.md` | Player, `single_shot`, `visible_reply` | **current** | D-062 (I-069) |
| `player-v1.12.md` | Player, `tool_loop`, `visible_reply` | **current** | D-062 (I-069) |
| `player-v1.11.md` | Player, `single_shot`, sealed Cons | **current** | D-062 (I-069) |
| `player-v1.10.md` | Player, `tool_loop`, sealed Cons | **current** | D-062 (I-069) |
| `umpire-v1.5.md` | Umpire, rules-aware (`scenario_rules`, T-073); reply-aware as v1.4 | **current** (selectable; no longer the rules default since D-096) | D-090 (T-073) |
| `umpire-v1.6.md` | Umpire, v1.5 plus Effect discipline: at most 3 Effects per branch, only real changes, supersede to update (T-082) | **current** (selectable; the rules default from D-096 until D-114) | D-096 (T-082) |
| `umpire-v1.7.md` | Umpire, v1.6 plus a branch audit: no Effect rests on another Actor's new decision; self-binding in every selectable branch; branch shape per mode; state vocabulary (I-156) | **current** (the default for every game since D-114) | D-112 (I-156) |
| `umpire-v1.4.md` | Umpire, reply-aware (`visible_reply`) | **current** (selectable) | D-060 (I-046) |
| `umpire-v1.3.md` | Umpire, released (sealed Cons) | **current** (selectable; legacy recipes resolve to it) | D-044 / D-047 (I-036) |
| `consequence-v1.1.md` | Consequence resolver, `bounded_effects_v1` only (T-074); `agents/consequence.py` (`CONSEQUENCE_PROMPT_VERSIONS`) | **current** | D-091 (T-074) |
| `aar-narrator-v1.0.md` | After-action narrator: writes the story cards from an export, citing it (#18); `aar/narrator.py` (`NARRATOR_PROMPT_VERSIONS`) | **current** | D-121 (#18) |
| `evaluator-v1.1.md` | Post-game objective evaluator | not wired — T-061 is optional and unscheduled | initial pack |

## Superseded, kept for reproducibility in `superseded/`

| File | What it added | Introduced by | Superseded by |
|---|---|---|---|
| `superseded/player-v1.9.md` | `single_shot` + `visible_reply`: reply to Cons once, bounded | D-060 (I-046) | v1.13 |
| `superseded/player-v1.8.md` | `tool_loop` + `visible_reply` | D-060 (I-046) | v1.12 |
| `superseded/player-v1.7.md` | `single_shot` with the per-activation Message budget | D-049 (I-033) | v1.11 |
| `superseded/player-v1.6.md` | `tool_loop` with the per-activation Message budget | D-049 (I-033) | v1.10 |
| `superseded/player-v1.5.md` | `single_shot` with incremental context and strategic memory | D-048 (I-034) | v1.7 |
| `superseded/player-v1.4.md` | `tool_loop` with incremental context and strategic memory | D-048 (I-034) | v1.6 |
| `superseded/player-v1.3.md` | `single_shot` decision format on top of v1.2 | D-046 (I-031) | v1.5 |
| `superseded/player-v1.2.md` | Private information is for use; Cons are strategic; own words | D-027 | v1.4 |
| `superseded/player-v1.1.md` | Initial player prompt | initial pack | v1.2 |
| `superseded/umpire-v1.2.md` | Contradictions supersede, failures cost, anchors kept | D-028 | v1.3 |
| `superseded/umpire-v1.1.md` | Initial Umpire prompt | initial pack | v1.2 |

Retained benchmark evidence names the versions it ran under; the current baseline for
`invalid_tool_calls` used `player-v1.10` and `umpire-v1.3`.
