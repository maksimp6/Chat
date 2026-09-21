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
