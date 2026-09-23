from db_backend import PGRow, translate_sql


def test_translate_sql_converts_sqlite_placeholders_and_types():
    sql = """
    CREATE TABLE demo (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL
    )
    """
    translated = translate_sql(sql)
    assert "BIGSERIAL PRIMARY KEY" in translated
    assert "AUTOINCREMENT" not in translated


def test_translate_sql_converts_begin_immediate():
    assert translate_sql("BEGIN IMMEDIATE") == "BEGIN"


def test_translate_sql_converts_insert_or_ignore():
    translated = translate_sql(
        "INSERT OR IGNORE INTO demo (id, name) VALUES (?, ?)"
    )
    assert translated == (
        "INSERT INTO demo (id, name) VALUES (%s, %s) ON CONFLICT DO NOTHING"
    )


def test_pg_row_supports_mapping_and_numeric_access():
    row = PGRow(["id", "name"], [7, "Alice"])
    assert row["id"] == 7
    assert row[0] == 7
    assert row["name"] == "Alice"
    assert dict(row.items()) == {"id": 7, "name": "Alice"}


def test_sqlite_is_the_default_backend_without_database_url(monkeypatch, tmp_path):
    monkeypatch.delenv("ALICE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("ALICE_DB_PATH", str(tmp_path / "termux.db"))

    import importlib
    import db

    db = importlib.reload(db)
    assert db.get_conn().__class__.__module__ == "sqlite3"

    db.init_db()
    db.create_conversation("termux-test", "Termux", "test-model")
    db.add_message("termux-test", "user", "SQLite works", trace={"trace_id": "t1"})

    assert db.get_conversations()[0]["id"] == "termux-test"
    assert db.get_messages("termux-test")[0]["text"] == "SQLite works"
