# Gate 0 — Foundation — PASS

**Checked:** 2026-09-22 · **Criteria:** `docs/testing.md §23` · **Verdict:** PASS

The final public source was built into a new empty directory as 242 allowlisted files, initialized
with its own history-free root commit, and installed only from `uv.lock`:

```text
$ uv sync --locked
Resolved 114 packages
Built amaw @ file:///private/tmp/.../snapshot
Installed 107 packages
```

Its complete included offline suite proved that pytest runs from that clean source tree:

```text
$ uv run pytest -q
1238 passed, 7 deselected, 2 warnings in 414.70s (0:06:54)
```

The canonical scenario loader and temporary stamped SQLite database were also run explicitly in
the maintainer checkout:

```text
$ uv run pytest tests/test_database.py tests/test_scenarios.py -q
......................................................                   [100%]
54 passed in 0.69s
```

This satisfies: fresh checkout installs; pytest runs; fixture scenario loads; temporary database
works. No OpenRouter key or external model was used.
