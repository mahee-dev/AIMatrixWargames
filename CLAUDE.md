# CLAUDE.md

The working agreement for this repository lives in `AGENTS.md`. Read it — it is imported
below and its rules are binding. This file only adds Claude-Code-specific detail.

@AGENTS.md

---

## Session start

1. Read the GitHub issue you are working on (`gh issue view <n>`), including its comments.
2. Open the spec sections it touches. Read them; don't recall them.
3. When the code cites `D-`, `I-` or `T-` identifiers you need to understand, look them up in
   `dev/` (AGENTS.md §2 has the table). They are history, not a queue of work.

## Working style here

- **One change per pull request.** If you notice unrelated work, open an issue and carry on.
  Do not opportunistically fix things in the current diff.
- **Tests before implementation.** Write the failing test, then make it pass.
- **Evidence before claims.** Run `uv run pytest` and put the real summary line in the pull
  request. Never report a change complete on the strength of code that looks right.
- **Quote the contract.** When implementing a rule, cite the invariant or section it comes
  from in a comment or the pull request (`INV-CON-004`, `api.md §9.1`).
- **Small files, clear boundaries.** But do not pre-split modules; TRD §5 says refactor when
  a file is unwieldy, not before.

## Things that are easy to get wrong here

- `docs/agents.md` is the **player-agent/Umpire contract**, not this working agreement.
- The Umpire never decides an outcome. It proposes `mode` + `probability`; the server rolls.
- `game.primary_arguments_per_actor` is the single canonical scenario field for the budget.
- `pass_con` is its own tool. `ready` is **not** overloaded for Con participation.
- A new private Message to a diplomacy recipient who is already ready **clears** that
  recipient's ready state before the phase can close (TEST-PHASE-003).
- Administrative exports include private data (objectives, Notes) with ownership metadata.
  Actor-facing APIs stay filtered. Never blur the two.
- Provider-backed runs cost money and take 70–120 minutes. Do not start one unless the person
  you are working with asks for it.

## Scope

The lean MVP excluded a long list of tempting features (TRD §41): organisations, channels, MCP
tool gateways, embeddings/RAG, OCR, event sourcing, combat models. Human UIs are now the local
workbench milestone (D-106), which starts with a TRD §41 amendment. If a change drifts toward
any of the others, stop and raise it on the issue.
