"""Shared pytest fixtures.

Most tests isolate the database by pointing ALICE_DB_PATH at a fresh SQLite
file. When ALICE_DATABASE_URL selects PostgreSQL, every test shares one
database. Tables are created once (for example when ``app`` is imported), so
empty every table before each test to keep the same isolation.
"""

import pytest

from db_backend import connect_postgres, is_postgres_configured


@pytest.fixture(autouse=True)
def _empty_postgres_tables():
    if not is_postgres_configured():
        yield
        return
    conn = connect_postgres()
    try:
        tables = [
            row["tablename"]
            for row in conn.execute(
                "SELECT tablename FROM pg_catalog.pg_tables WHERE schemaname = 'public'"
            ).fetchall()
        ]
        if tables:
            names = ", ".join(f'"{name}"' for name in tables)
            conn.execute(f"TRUNCATE {names} RESTART IDENTITY CASCADE")
        conn.commit()
    finally:
        conn.close()
    yield
