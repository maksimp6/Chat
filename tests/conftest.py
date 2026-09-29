"""Backend-selection and state-isolation rules for the cross-database CI matrix.

When the PostgreSQL CI job exports ALICE_DATABASE_URL, normal application tests
exercise PostgreSQL. Tests that explicitly choose a temporary SQLite database
by replacing `db.DB_PATH` keep that isolation instead of being silently
redirected to PostgreSQL.

For PostgreSQL-backed tests the schema is preserved, but all table rows are
truncated before each test so fixed IDs and stateful fixtures cannot leak across
test cases.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from postgres_test_guard import require_disposable_postgres_target


_SQLITE_ONLY_MODULES = {
    "test_chat_sqlite_integration.py",
    "test_environment_gateway.py",
}


@pytest.fixture(autouse=True)
def isolate_selected_database(request, monkeypatch):
    database_url = os.environ.get("ALICE_DATABASE_URL", "").strip()
    if not database_url:
        yield
        return

    require_disposable_postgres_target(database_url)

    import db

    module_name = Path(str(request.fspath)).name
    original_db_path = db.DB_PATH
    original_postgres_url_from_env = db.postgres_url_from_env

    if module_name in _SQLITE_ONLY_MODULES:
        monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
        yield
        return

    def selected_postgres_url() -> str:
        # Tests that replace DB_PATH are explicitly selecting a temporary
        # SQLite database. Preserve that choice even in the PostgreSQL CI job.
        if db.DB_PATH != original_db_path:
            return ""
        return original_postgres_url_from_env()

    monkeypatch.setattr(db, "postgres_url_from_env", selected_postgres_url)

    conn = db.get_conn()
    try:
        rows = conn.execute(
            """
            SELECT tablename
            FROM pg_catalog.pg_tables
            WHERE schemaname = 'public'
            """
        ).fetchall()
        tables = [str(row["tablename"]) for row in rows]
        if tables:
            quoted = ", ".join('"' + name.replace('"', '""') + '"' for name in tables)
            conn.execute(f"TRUNCATE TABLE {quoted} RESTART IDENTITY CASCADE")
            conn.commit()
    finally:
        conn.close()

    yield
