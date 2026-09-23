# Database backends

Alice Pro supports two database backends through the same application data-access layer.

## Default: SQLite

When `ALICE_DATABASE_URL` is not set, Alice Pro uses the local SQLite database `alice_pro.db`. This is the default for development and Termux.

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
