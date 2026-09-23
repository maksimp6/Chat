import sqlite3

import db
from db_backend import is_postgres_configured, postgres_url_from_env


def test_postgres_backend_is_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setenv("DATABASE_URL", "postgresql://must-not-enable")
    assert postgres_url_from_env() == ""
    assert is_postgres_configured() is False

    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://explicit")
    assert postgres_url_from_env() == "postgresql://explicit"
    assert is_postgres_configured() is True


def test_sql_translation_keeps_sqlite_semantics():
    from db_backend import translate_sql

    assert "BIGSERIAL PRIMARY KEY" in translate_sql(
        "CREATE TABLE demo (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT)"
    )
    assert translate_sql("BEGIN IMMEDIATE") == "BEGIN"


def test_sqlite_is_the_default_backend(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice_pro.db"))

    conn = db.get_conn()
    try:
        assert isinstance(conn, sqlite3.Connection)
        conn.execute("CREATE TABLE probe (id INTEGER PRIMARY KEY, value TEXT)")
        conn.execute("INSERT INTO probe (id, value) VALUES (?, ?)", (1, "termux"))
        conn.commit()
        assert conn.execute("SELECT value FROM probe WHERE id = ?", (1,)).fetchone()[0] == "termux"
    finally:
        conn.close()


def test_core_db_schema_and_conversations_work_on_sqlite(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice_pro.db"))

    db.init_db()
    db.create_conversation("termux-test", "Termux", "test-model")
    db.add_message("termux-test", "user", "local database")

    assert db.get_conversations()[0]["id"] == "termux-test"
    assert db.get_messages("termux-test")[0]["text"] == "local database"
