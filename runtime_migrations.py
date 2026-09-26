"""Schema additions for serverless/sessioned execution."""

import sqlite3

from db import get_conn


def init_runtime_tables() -> None:
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sessions (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL DEFAULT 'active',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                completed_at INTEGER
            )
        """)
        # Existing installations created before completed_at need the additive migration.
        try:
            conn.execute("ALTER TABLE sessions ADD COLUMN completed_at INTEGER")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        conn.execute("""
            CREATE TABLE IF NOT EXISTS invocations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                trace_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'created',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT,
                error_json TEXT,
                trace_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        # Existing installations created before trace persistence need the additive migration.
        try:
            conn.execute("ALTER TABLE invocations ADD COLUMN trace_json TEXT NOT NULL DEFAULT '{}'")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invocations_session ON invocations(session_id)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_invocations_conversation ON invocations(conversation_id)"
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invocations_trace ON invocations(trace_id)")
        conn.commit()
    finally:
        conn.close()
