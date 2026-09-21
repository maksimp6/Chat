# Alice Pro database architecture

## Primary database

Alice Pro production uses **one shared PostgreSQL database** for the server-side application.

The database is started by \`docker-compose.yml\` as the \`db\` service and persists its files in the \`alice_postgres_data\` Docker volume. The application connects through \`ALICE_DATABASE_URL\`.

The Python database layer keeps a SQLite fallback when no PostgreSQL URL is configured. This exists for deterministic local tests and legacy offline tooling; production Docker configuration always supplies PostgreSQL.

## Scope

All server-side application stores that use Alice Pro's database connection share this PostgreSQL instance, including conversations, messages, runtime sessions/invocations, provider credentials, key metadata, memory, MCP server configuration, departments, treasury data, and local-agent state.

A standalone \`mcp_servers.db\` file is not used by the production path.

## Supabase

Supabase is **backup/recovery only**. The application must not use Supabase as its primary runtime database.

\`scripts/backup_primary_to_supabase.sh\` creates a PostgreSQL custom-format dump of the primary DB and uploads it to a Supabase Storage bucket. This keeps backup traffic outside the request path.

Required backup environment:
- \`ALICE_DATABASE_URL\`
- \`SUPABASE_URL\`
- \`SUPABASE_SERVICE_ROLE_KEY\`
- optional \`SUPABASE_BACKUP_BUCKET\` (default \`alice-pro-backups\`)
- optional \`SUPABASE_BACKUP_PREFIX\` (default \`database\`)

The restore process is intentionally an explicit operational action. Do not automatically restore or switch the runtime database after a backup failure.

## Preview

Preview deployments must point to an isolated PostgreSQL database/volume and must never receive production database credentials or production data.
