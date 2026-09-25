"""Small versioned migrations for observability and project-tree state."""

from db import get_conn

MIGRATION_VERSION = "003_observability_project_tree"


def apply_observability_migrations():
    conn = get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        already = conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,),
        ).fetchone()
        if not already:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS project_tree_preferences (
                    owner_id TEXT PRIMARY KEY,
                    root_path TEXT NOT NULL,
                    expanded_json TEXT NOT NULL DEFAULT '[]',
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS frontend_error_events (
                    id TEXT PRIMARY KEY,
                    event_name TEXT NOT NULL,
                    error_code TEXT,
                    message TEXT NOT NULL,
                    context_json TEXT NOT NULL DEFAULT '{}',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)",
                (MIGRATION_VERSION,),
            )
        conn.commit()
    finally:
        conn.close()
