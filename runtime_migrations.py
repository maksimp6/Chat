"""Schema additions for serverless/sessioned execution."""

from db import get_conn
from db_backend import OperationalError, is_postgres_connection


def _drop_legacy_invocation_conversation_fk(conn) -> None:
    """Remove the legacy PostgreSQL FK that rejects synthetic conversation IDs."""
    if not is_postgres_connection(conn):
        return

    constraints = conn.execute(
        """
        SELECT DISTINCT constraint_info.conname AS name
        FROM (
            SELECT c.conname, unnest(c.conkey) AS attnum
            FROM pg_constraint AS c
            WHERE c.conrelid = 'invocations'::regclass
              AND c.contype = 'f'
        ) AS constraint_info
        JOIN pg_attribute AS a
          ON a.attrelid = 'invocations'::regclass
         AND a.attnum = constraint_info.attnum
        WHERE a.attname = 'conversation_id'
        """
    ).fetchall()
    for row in constraints:
        constraint_name = str(row["name"])
        quoted_name = '"' + constraint_name.replace('"', '""') + '"'
        conn.execute(f"ALTER TABLE invocations DROP CONSTRAINT {quoted_name}")


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
        # Existing installations created before completed_at need the additive
        # migration. Introspect first so PostgreSQL does not take an
        # AccessExclusiveLock on every request that calls init_runtime_tables().
        session_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "completed_at" not in session_columns:
            try:
                conn.execute("ALTER TABLE sessions ADD COLUMN completed_at INTEGER")
            except OperationalError as exc:
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
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            )
        """)
        # Existing installations created before trace persistence need the
        # additive migration. Avoid unconditional ALTER on PostgreSQL: even
        # ADD COLUMN IF NOT EXISTS takes a strong table lock.
        invocation_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(invocations)").fetchall()
        }
        if "trace_json" not in invocation_columns:
            try:
                conn.execute(
                    "ALTER TABLE invocations ADD COLUMN trace_json TEXT NOT NULL DEFAULT '{}'"
                )
            except OperationalError as exc:
                if "duplicate column name" not in str(exc).lower():
                    raise
        _drop_legacy_invocation_conversation_fk(conn)
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
