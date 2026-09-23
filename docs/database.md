# Database architecture

Alice Pro supports two persistence backends through the same application-facing db.get_conn() API.

## Default: SQLite

When neither ALICE_DATABASE_URL nor DATABASE_URL is set, Alice Pro uses the existing SQLite database: alice_pro.db.

This is the default and remains the supported local/Termux mode. No PostgreSQL service, credentials, or network connection are required.

The SQLite path can be overridden with ALICE_DB_PATH=/path/to/alice_pro.db.

## Optional: PostgreSQL

PostgreSQL is selected only when ALICE_DATABASE_URL is explicitly configured. DATABASE_URL is accepted as a compatibility fallback.

Example:

    ALICE_DATABASE_URL=postgresql://alice:<password>@db:5432/alice_pro

The repository does not enable this variable by default. Production or Docker deployment can opt in later without changing application-level storage calls.

## Shared storage

When PostgreSQL is selected, the main server-side stores use the same database connection layer, including conversations, messages, runtime sessions/invocations, provider credentials, managed keys, memory, MCP storage, departments, Treasury and local-agent state.

Yandex conversation mapping and MCP server configuration also use db.get_conn(), so they follow the same selected backend.

## Compatibility layer

The PostgreSQL adapter intentionally accepts the small SQLite-oriented SQL subset still used by the application:

- ? parameter placeholders;
- INTEGER PRIMARY KEY AUTOINCREMENT;
- BEGIN IMMEDIATE;
- INSERT OR IGNORE;
- PRAGMA table_info(...) used by existing schema migrations;
- SQLite-only WAL/synchronous pragmas.

This keeps the migration incremental instead of forcing a large rewrite of existing modules.

## Activation rule

Do not set ALICE_DATABASE_URL on a Termux/local installation unless PostgreSQL is intentionally being tested.

Safe default:

    unset ALICE_DATABASE_URL
    unset DATABASE_URL
    python app.py

PostgreSQL support is therefore present but dormant until the deployment explicitly opts in.
