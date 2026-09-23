import os
import sqlite3

import db
from db_backend import PGRow, translate_sql


def test_sqlite_remains_the_default_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice_pro.db"))

    conn = db.get_conn()
    try:
        assert isinstance(conn, sqlite3.Connection)
    finally:
        conn.close()


def test_postgres_is_only_selected_when_explicitly_configured(monkeypatch):
    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://alice@example/alice_pro")

    sentinel = object()
    monkeypatch.setattr(db, "connect_postgres", lambda url: (sentinel, url))

    conn = db.get_conn()
    assert conn == (sentinel, "postgresql://alice@example/alice_pro")


def test_translate_sql_converts_sqlite_constructs():
    sql = """
    CREATE TABLE demo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
    )
    """
    translated = translate_sql(sql)
    assert "BIGSERIAL PRIMARY KEY" in translated
    assert "AUTOINCREMENT" not in translated
    assert translate_sql("BEGIN IMMEDIATE") == "BEGIN"
    assert translate_sql(
        "INSERT OR IGNORE INTO demo (id, name) VALUES (?, ?)"
    ) == "INSERT INTO demo (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING"


def test_pg_row_supports_mapping_and_numeric_access():
    row = PGRow(["id", "name"], [7, "Alice"])
    assert row["id"] == 7
    assert row[0] == 7
    assert row.get("missing") is None
    assert dict(row) == {"id": 7, "name": "Alice"}
