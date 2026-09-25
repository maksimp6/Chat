-- Versioned reference migration for observability and Project Tree.
-- Runtime application is performed by observability_migrations.py for SQLite/PostgreSQL parity.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS project_tree_preferences (
    owner_id TEXT PRIMARY KEY,
    root_path TEXT NOT NULL,
    expanded_json TEXT NOT NULL DEFAULT '[]',
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS frontend_error_events (
    id TEXT PRIMARY KEY,
    event_name TEXT NOT NULL,
    error_code TEXT,
    message TEXT NOT NULL,
    context_json TEXT NOT NULL DEFAULT '{}',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
