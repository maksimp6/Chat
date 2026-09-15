"""Schema additions for serverless/sessioned execution."""

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
                updated_at INTEGER NOT NULL
            )
        """)
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
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS execution_traces (
                trace_id TEXT PRIMARY KEY,
                session_id TEXT,
                conversation_id TEXT,
                invocation_id TEXT UNIQUE,
                status TEXT NOT NULL DEFAULT 'completed',
                trace_json TEXT NOT NULL,
                created_at REAL,
                updated_at INTEGER NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id),
                FOREIGN KEY (invocation_id) REFERENCES invocations(id)
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invocations_session ON invocations(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invocations_conversation ON invocations(conversation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_invocations_trace ON invocations(trace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_session ON execution_traces(session_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_conversation ON execution_traces(conversation_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_traces_invocation ON execution_traces(invocation_id)")
        conn.commit()
    finally:
        conn.close()
