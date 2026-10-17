# Database backends

Logos stores its server-side data in one native Kristall file store (`kristall.Kristall`). SQL backends are removed.

## Default: SQLite

Runtime data is kept in the native Kristall journal at `ALICE_MEMORY_PATH` (default: `alice.memory`). `ALICE_DATABASE_URL` is a retired setting: the runtime no longer reads it, and the deploy preflight rejects it.

No PostgreSQL service is required for this mode.

## Optional: PostgreSQL

Set `ALICE_DATABASE_URL` explicitly to switch the server-side persistence layer to PostgreSQL:

```env
ALICE_DATABASE_URL=postgresql://alice:<password>@localhost:5432/alice_pro
```

The application keeps the existing SQLite-oriented SQL surface and translates the small SQLite-specific subset used by the project for PostgreSQL. The goal is one application code path, not separate feature implementations.

## Runtime policy

PostgreSQL is intentionally **not enabled by default**. Current deployment and local startup continue to work without a PostgreSQL server.

The optional compose file is:

```bash
docker compose -f docker-compose.postgres.yml up -d
```

Do not add `ALICE_DATABASE_URL` to the normal Termux environment unless PostgreSQL is intentionally being tested.

## Shared stores

Both backends are expected to serve the same server-side stores:

- conversations and messages;
- conversation settings and configuration;
- provider credentials and managed keys;
- MCP server/configuration state;
- sessions and invocations;
- local-agent state and queued jobs;
- treasury state;
- user identity;
- departments.

Schema bootstrap remains application-controlled, so a new installation can initialize either backend without a separate manual migration step.
