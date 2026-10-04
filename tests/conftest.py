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
import threading
import tempfile
from pathlib import Path

import pytest

from tests.postgres_test_guard import require_disposable_postgres_target


_SQLITE_ONLY_MODULES = {
    "test_chat_sqlite_integration.py",
    "test_environment_gateway.py",
}


def _make_lazy_postgres_reset_connector(connect_postgres, lock_factory=threading.Lock):
    reset_lock = lock_factory()
    reset_done = False

    def connect_postgres_for_test(url=None):
        nonlocal reset_done
        conn = connect_postgres(url)
        if reset_done:
            return conn

        with reset_lock:
            if reset_done:
                return conn
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
                reset_done = True
                return conn
            except Exception:
                conn.close()
                raise

    return connect_postgres_for_test


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
    original_connect_postgres = db.connect_postgres

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

    connect_postgres_for_test = _make_lazy_postgres_reset_connector(original_connect_postgres)

    monkeypatch.setattr(db, "postgres_url_from_env", selected_postgres_url)
    monkeypatch.setattr(db, "connect_postgres", connect_postgres_for_test)

    yield


def pytest_sessionstart(session):
    # xdist starts fresh interpreters. Select the worker database before collection
    # imports db/app modules, so import-time initialization is isolated too.
    worker = getattr(session.config, "workerinput", None)
    if worker is None:
        return
    if os.environ.get("ALICE_DATABASE_URL", "").strip():
        raise pytest.UsageError(
            "PostgreSQL xdist requires separate databases; use the serial suite"
        )
    directory = tempfile.TemporaryDirectory(prefix="alice-sqlite-" + worker["workerid"] + "-")
    session.config._alice_sqlite_directory = directory
    session.config._alice_original_db_path = os.environ.get("ALICE_DB_PATH")
    os.environ["ALICE_DB_PATH"] = str(Path(directory.name) / "alice_pro.db")


def pytest_sessionfinish(session):
    directory = getattr(session.config, "_alice_sqlite_directory", None)
    if directory is None:
        return
    original = session.config._alice_original_db_path
    if original is None:
        os.environ.pop("ALICE_DB_PATH", None)
    else:
        os.environ["ALICE_DB_PATH"] = original
    directory.cleanup()
