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
    assert (
        translate_sql("SELECT * FROM demo WHERE name LIKE 'bootstrap-%'")
        == "SELECT * FROM demo WHERE name LIKE 'bootstrap-%%'"
    )


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


class _FakeRawCursor:
    def __init__(self, log, rows=()):
        self._log = log
        self._rows = list(rows)
        self.description = None
        self.rowcount = 0

    def execute(self, sql, params=()):
        self._log.append(sql)
        self.description = [type("Desc", (), {"name": "tablename"})()]

    def fetchall(self):
        return self._rows


class _FakeRawConnection:
    def __init__(self, rows=()):
        self.log = []
        self._rows = rows

    def cursor(self):
        return _FakeRawCursor(self.log, self._rows)

    def commit(self):
        pass

    def close(self):
        pass


def _fake_pg_connection(rows=()):
    from db_backend import PGConnection

    conn = PGConnection.__new__(PGConnection)
    conn._raw = _FakeRawConnection(rows)
    conn._row_factory = None
    return conn


def test_table_names_works_on_both_backends(tmp_path):
    from db_backend import table_names

    conn = sqlite3.connect(tmp_path / "tables.db")
    conn.execute("CREATE TABLE probe (id INTEGER)")
    assert table_names(conn) == {"probe"}
    conn.close()

    pg = _fake_pg_connection(rows=[("conversations",), ("messages",)])
    assert table_names(pg) == {"conversations", "messages"}
    assert "pg_catalog.pg_tables" in pg._raw.log[0]


def test_add_column_if_missing_is_idempotent_and_rejects_bad_identifiers(tmp_path):
    import pytest

    from db_backend import add_column_if_missing

    conn = sqlite3.connect(tmp_path / "columns.db")
    conn.execute("CREATE TABLE probe (id INTEGER)")
    assert add_column_if_missing(conn, "probe", "note", "TEXT DEFAULT ''") is True
    assert add_column_if_missing(conn, "probe", "note", "TEXT DEFAULT ''") is False
    assert [row[1] for row in conn.execute("PRAGMA table_info(probe)")] == ["id", "note"]
    with pytest.raises(ValueError):
        add_column_if_missing(conn, "probe; DROP TABLE probe", "x", "TEXT")
    conn.close()


def test_postgres_integrity_errors_use_backend_neutral_type():
    import pytest

    import db_backend

    class ForeignKeyViolation(Exception):
        pass

    class RaisingCursor(_FakeRawCursor):
        def execute(self, sql, params=()):
            raise ForeignKeyViolation("fk")

    fake_psycopg = type("psycopg", (), {"IntegrityError": ForeignKeyViolation})
    original = db_backend.psycopg
    db_backend.psycopg = fake_psycopg
    try:
        with pytest.raises(db_backend.IntegrityError):
            db_backend.PGCursor(RaisingCursor([])).execute("INSERT INTO t VALUES (?)", (1,))
    finally:
        db_backend.psycopg = original


def test_runtime_tables_drop_legacy_conversation_key_on_postgres(monkeypatch):
    import runtime_migrations

    pg = _fake_pg_connection()
    monkeypatch.setattr(runtime_migrations, "get_conn", lambda: pg)
    runtime_migrations.init_runtime_tables()
    assert any(
        "DROP CONSTRAINT IF EXISTS invocations_conversation_id_fkey" in sql for sql in pg._raw.log
    )
    assert not any("REFERENCES conversations" in sql for sql in pg._raw.log)


def test_postgres_executemany_unique_violation_uses_backend_neutral_type(monkeypatch):
    import pytest

    import db_backend

    class UniqueViolation(Exception):
        pass

    class RaisingCursor(_FakeRawCursor):
        def executemany(self, sql, seq_of_params):
            raise UniqueViolation("duplicate")

    monkeypatch.setattr(db_backend, "psycopg", None)
    with pytest.raises(db_backend.IntegrityError):
        db_backend.PGCursor(RaisingCursor([])).executemany("INSERT INTO t VALUES (?)", [(1,)])


def test_delete_conversation_removes_messages_and_settings(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice_pro.db"))

    db.init_db()
    db.create_conversation("doomed", "Doomed", "test-model")
    db.add_message("doomed", "user", "bye")
    db.save_conv_settings("doomed", {"k": "v"})

    db.delete_conversation("doomed")

    assert db.get_conversations() == []
    assert db.get_messages("doomed") == []
    assert db.get_conv_settings("doomed") is None
