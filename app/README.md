# `app/` — the FastAPI core

The authoritative game engine: procedure, visibility, randomness and state. It knows nothing
about agent frameworks — `app/` never imports PydanticAI (TRD §4) — and it never lets a model
decide an outcome: the Umpire proposes, the server rolls (INV-RNG-001). Modules are kept flat
by design (TRD §5: split when a file is unwieldy, not before). Each module's docstring cites
the specification sections it implements.

## Engine

| Module | Owns |
|---|---|
| `models` | The domain entities and transition records (SQLModel), one module by design. |
| `game_service` | The state machine — the **one** owner of every phase transition, Argument, Con, adjudication and Effect. Transactional; revalidates phase and eligibility inside the transaction. |
| `game_errors` | The game-domain errors the state machine raises, shared by mutation and read-model boundaries. Re-exported by `game_service`. |
| `game_results` | The frozen result values each state-machine operation returns, and an idempotent replay rebuilds. Re-exported by `game_service`. |
| `game_queries` | Neutral read-only game-state queries shared by `game_service` and `context`. |
| `game_loop` | The sealed game-loop condition a Game runs under (Con-reply treatment, Diplomacy bounds, Con opposition). |
| `context` | The server-computed read model of one Actor's authorised context, including `available_actions`. |
| `activation_context` | Actor-owned incremental delivery and rolling strategic memory (not authoritative game state). |
| `visibility` | The single visibility service. Filters before search; nothing else decides what an Actor may see. |
| `rng` | Deterministic, restart-safe, server-owned randomness. |
| `consequences` | The framework-neutral consequence resolver boundary (T-074): the public-only end-of-turn context, the evidence-linked proposal and the resolver protocol. |
| `umpire` | The framework-neutral Umpire boundary: what an adjudication proposal is and how the server resolves it. |

## Storage

| Module | Owns |
|---|---|
| `database` | Engine, schema creation, session lifecycle, integrity rules. |
| `persistence` | The data operations `game_service` calls, and the transactional guard. |
| `provenance` | The immutable run manifest and the append-only `AgentRun` ledger. |
| `resumption` | Admin-authorised preparation for resuming an interrupted autonomous game. |

## Scenarios and Resources

| Module | Owns |
|---|---|
| `scenarios` | Manifest loading, validation and game creation. |
| `resources` | Safe Resource materialisation and the authorised search/read projections. |

## Reporting

| Module | Owns |
|---|---|
| `export` | The complete administrative JSON export (unfiltered; contains private data). |
| `summary` | Derived post-game reports and their text rendering. |

## Web and configuration

| Module | Owns |
|---|---|
| `api` | The application factory, error envelope, authentication dependencies and request logging shared by every route. Actor identity comes from the Seat bearer token, never from a body. |
| `api_player` | The Actor-facing routes. Visibility-filtered; never imports the administrative export. |
| `api_admin` | The admin orchestration and export routes, behind the separate admin token. Returns private data with ownership metadata. |
| `security` | Seat token minting and hashing. |
| `config` | Environment-driven settings and safe logging. |

The reference agents (`../agents/`), the benchmark runner (`../benchmark/`) and the CLI
(`../amaw_cli.py`) sit outside this package and talk to it through `api` or `game_service`.
