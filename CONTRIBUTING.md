# Contributing to AMAW

Thank you for looking. AMAW is a small research codebase with a strict working method, and the
method is the part most worth reading before the code.

## Before you start

```bash
uv sync --extra agents
uv run pytest            # layers 1–3: no API key, no network
```

The default suite must pass without an OpenRouter key. Provider-backed tests are marked
`@pytest.mark.llm` and run only with `uv run pytest -m llm` and a key in `.env`.

## What a change looks like

1. Open or choose a GitHub issue and keep the change focused on that issue.
2. Create a short-lived branch with a descriptive name.
3. Write the failing test first. `docs/testing.md` enumerates the acceptance tests by ID
   (`TEST-ARG-008`, …); name the test function after the ID.
4. Implement the smallest thing that makes it pass. Cite the contract you implement in a
   comment (`INV-CON-004`, `api.md §9.1`).
5. In the pull request, state what changed, which contract applies, and paste the relevant
   test output.

## Things that are not negotiable

`app/` never imports an agent framework; the server owns
all randomness and a model never decides an outcome; visibility is one service that filters
before search; Actor identity comes from the Seat token; state changes are transactional;
Effects are append-only. A change that violates one of these is a bug even if the tests pass.

## Changing the specification

`docs/` is normative. If code and a document disagree, the code is wrong unless the pull request
explicitly identifies and justifies a specification defect. Keep a specification correction in
its own commit before the implementation that depends on it.

## Reporting a problem

Open an issue with the scenario, the command, the `run.json` / manifest of the affected game
if you have one, and the actual output. For anything security-related, see `SECURITY.md`.
