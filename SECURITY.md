# Security

## Reporting

Please report vulnerabilities privately to the maintainer through GitHub's private
vulnerability reporting on this repository rather than in a public issue. Include the
version or commit, how to reproduce, and what an attacker could do with it. You will get an
acknowledgement, and a fix or a decision, before anything is disclosed publicly.

## What AMAW is and is not

AMAW is a research tool for running Matrix Games between language-model agents on a machine
you control. The CLI (`amaw run`, `amaw benchmark`) drives the FastAPI application
**in-process**; there is no serve command and the repository ships nothing that listens on a
network port. The application has no user accounts, no multi-tenancy and no rate limiting;
the 64 KiB request-body limit and the Seat bearer tokens exist to keep agents honest with
each other, not to defend a network-exposed service. If you mount it behind a server of your
own, do not expose it to untrusted networks.

## Things to know before sharing anything

- **Exports are unfiltered administrative records.** `amaw export` and the benchmark output
  directory contain every Actor's private objectives, briefs, Notes and private Messages,
  along with model names and provider generation ids. Actor-facing APIs are filtered; the
  export is not. Treat an export as you would treat the scenario's private material.
- **Seat tokens are per-game credentials.** They are stored hashed and rotated on resume,
  but a running process holds them in memory and logs may reveal their existence, never
  their value. `OPENROUTER_API_KEY` lives in `.env`, which is git-ignored; nothing in the
  repository, the exports or the manifests carries it.
- **Scenario Resources are read from disk.** The loader refuses paths outside the scenario
  package. Only run scenario packages you trust; their text is placed in model prompts.
