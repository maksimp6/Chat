# Database backend

Logos stores its authoritative runtime data in one native durable store,
`kristall.Kristall`, backed by a single append-only journal file. There is no SQL
backend.

## Retired settings and modes

- `ALICE_DATABASE_URL` is retired. Nothing in the runtime reads it. The deploy
  preflight (`scripts/cloudru_deploy_preflight.py`) rejects it with
  `ALICE_DATABASE_URL: retired setting; native MemoryStore required`. Do not set it
  in any environment.
- SQLite (`alice_pro.db`) and PostgreSQL are not runtime modes. There is no
  PostgreSQL service, compose file, SQL migration or SQL driver to install.
- `tests/test_sql_free_contract.py` keeps SQL driver imports and SQL connection
  helpers out of the runtime.

## The store

`kristall.Kristall` is a synchronous name/value store. Names are non-empty strings.
Values are JSON scalars, lists and string-keyed dictionaries.

- `set(name, value)` stages a copy. `commit()` appends the staged names to the
  journal as one durable frame and returns the new sequence number.
- `transaction()` is the required form for concurrent read-modify-write code.
  Staging belongs to one client instance.
- `get`, `items(prefix)`, `delete`, `info`, `checkpoint`, `backup`, `restore` and
  `close` complete the API.

Durability and integrity:

- A commit is acknowledged only after its frame is fsynced. A new journal file is
  also synced through its directory.
- Each frame is chained to the previous SHA-256 digest with a monotonic sequence
  number. Replay detects reordered, missing or damaged frames (`corrupt committed
  journal`). The chain is an integrity check, not protection against deliberate
  rewrites.
- Opening a store replays the whole journal into memory. A torn final frame stops
  the open with `incomplete journal tail; recovery required`. Nothing is repaired
  automatically.
- Limits: one frame is at most 1 MiB, a value may nest at most 64 levels and 100,000
  nodes, and the text in one value is limited to 1 MiB.

## Journal location

`storage/db.py` (`memory_file_path`) chooses the journal in this order:

1. Inside a preview runtime, `<runtime data root>/alice.memory`.
2. `ALICE_MEMORY_PATH`, when set, is used as the journal path.
3. Otherwise `ALICE_DB_PATH` (no default). A name that does not end in
   `.memory` maps to `alice.memory` in the same directory.

`.env.example` sets `ALICE_MEMORY_PATH=./alice.memory`. Deployments set an explicit
absolute `ALICE_MEMORY_PATH` on persistent storage.

## Deployment modes

Every deployment uses the same store and code path. Only the journal path and the
filesystem under it change.

- **Local development:** a journal file in the working directory, for example
  `./alice.memory`. No database server is started.
- **Termux:** the same file-based journal, with no separate service process.
- **Shared or cloud deployment:** a journal on a persistent filesystem under the
  approved `data_root`, with exactly one writer process. An ephemeral container
  filesystem is not durable storage, and cloud object storage is a backup target,
  not the live journal (see [`deploy/cloudru/README.md`](../deploy/cloudru/README.md),
  "Native storage transition").

## One writer per journal

- `Kristall(path)` takes an exclusive, non-blocking `flock` on `<journal>.lock`. The
  lock is a separate file, so replacing the journal cannot release ownership. A
  second writer fails with `database already has a writer`.
- Inside one process, `agent_memory.client.get_memory_client` returns one shared
  client per canonical path. Repositories use that client and must not close it.
  A preview runtime that stops or fails to start releases its writer through
  `close_memory_clients`.
- Do not open the same journal from several processes. The deployment config
  requires `writer_processes: 1`.
- Read-only checks take the same lock. `--check-db` therefore fails while a service
  holds the journal.

## Legacy SQLite data

- `alice_pro.db` is legacy SQLite data. Kristall never opens it, and the runtime does not check for it.
- Nothing imports legacy data automatically. Activation is a separate, verified step.
  Historical SQL data and backups are not changed by the runtime.

## Backup and restore

- `backup(destination)` writes a new, verified, fsynced copy of the confirmed state.
  It refuses an existing destination and an unhealthy store.
- `restore(source)` restores a verified backup only into an empty store. It never
  targets the live journal path.
- `checkpoint()` replaces the journal with one verified snapshot frame. Sequence
  numbers stay monotonic.
- Deployment requires `database.persistence_evidence` and
  `database.restore_evidence` before rollout.

## Shared stores

Server-side repositories use this store under separate key prefixes, for example
`chat/conversation/`, `config/` and `department/`. They cover:

- conversations, messages and conversation settings;
- configuration;
- user identity and conversation ownership;
- sessions, invocations and traces;
- agent memory;
- MCP selection;
- local-agent jobs and environments;
- provider metadata and encrypted provider credentials;
- quotas;
- departments;
- Treasury and business workflows.

There is no schema to migrate. A new installation starts with an empty journal.

## Operator check

The deployment config names the store as `memory_engine.MemoryStore`. The preflight
validates that exact string as a schema label. No module with that name exists. The
implementation is `kristall.Kristall`.

```bash
ALICE_MEMORY_PATH=/absolute/persistent/path/alice.memory \
  python scripts/cloudru_deploy_preflight.py --config deployment.json --check-db
```

`--check-db` verifies an existing journal under its writer lock. It never repairs,
replaces or deletes source data. A passing result does not mark a deployment ready.
