import os

import pytest

import db
from observability_migrations import MIGRATION_VERSION, apply_observability_migrations


def test_observability_migration_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setenv("ALICE_DB_PATH", str(tmp_path / "test.db"))
    db.DB_PATH = str(tmp_path / "test.db")
    apply_observability_migrations()
    apply_observability_migrations()

    conn = db.get_conn()
    try:
        versions = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,),
        ).fetchall()
        assert len(versions) == 1
        tables = {
            row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
    finally:
        conn.close()
    assert {"schema_migrations", "project_tree_preferences", "frontend_error_events"} <= tables
