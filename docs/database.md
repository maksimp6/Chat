# Alice Pro database architecture

## Backend selection

Alice Pro supports two database backends through the same application database layer:

- **SQLite by default**: used when `ALICE_DATABASE_URL` and `DATABASE_URL` are not set. This is the normal local and Termux mode.
- **PostgreSQL when explicitly configured**: used only when `ALICE_DATABASE_URL` (or the compatibility `DATABASE_URL`) is set.

The PostgreSQL support is therefore **implemented but not enabled by default**. A clean Termux launch does not require Docker, PostgreSQL, or a network database.

Set `ALICE_DB_PATH` to choose the local SQLite file path. Otherwise Alice Pro uses `alice_pro.db`.

## PostgreSQL

PostgreSQL 17 is available as an optional Docker Compose service under the `postgres` profile. The application does not depend on that service unless `ALICE_DATABASE_URL` is explicitly supplied.

Example opt-in deployment:

```bash
export POSTGRES_PASSWORD='<random-password>'
export ALICE_DATABASE_URL='postgresql://alice:<password>@db:5432/alice_pro'
docker compose --profile postgres up -d db app
```

Without those settings:

```bash
docker compose up -d app
```

runs the application with SQLite.

## Shared stores

The database abstraction is shared by conversations, messages, runtime sessions/invocations, provider credentials, key metadata, memory, MCP server configuration, departments, treasury data, and local-agent state. MCP storage uses the same `get_conn()` backend selector rather than maintaining a separate `mcp_servers.db` runtime database.

The intent is to keep schema and application behavior aligned across SQLite and PostgreSQL. Backend-specific SQL translation is limited to the compatibility layer.

## Supabase

Supabase is **backup/recovery only**. It is not selected as the Alice Pro runtime primary database.

`scripts/backup_primary_to_supabase.sh` creates a PostgreSQL custom-format dump of the configured primary database and uploads it to Supabase Storage. Restore remains an explicit destructive operational action.

## Preview

Preview deployments must use an isolated database/volume and must never receive production database credentials or production data.
