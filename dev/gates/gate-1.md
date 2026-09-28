# Gate 1 — Engine Proven — PASS

**Checked:** 2026-09-22 · **Criteria:** `docs/testing.md §23` · **Verdict:** PASS

The authoritative deterministic six-turn integration test ran without an external model:

```text
$ uv run pytest tests/test_integration_six_turn.py -q
.......                                                                  [100%]
7 passed in 3.47s
```

The seven tests cover completion through six turns with three ScriptedPlayers and FakeUmpire,
Argument/adjudication counts and budgets, reconstructable server RNG, final active Effects,
visibility isolation and structurally valid event order. Gate 1 passes.
