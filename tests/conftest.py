"""Shared pytest fixtures.

Most tests isolate the database by pointing ALICE_DB_PATH at a fresh SQLite
file. When ALICE_DATABASE_URL selects PostgreSQL, every test shares one
database. Tables are created once (for example when ``app`` is imported), so
empty every table before each test to keep the same isolation.

Emptying tables destroys data, so it only runs when ALICE_TEST_DATABASE_RESET=1
confirms that ALICE_DATABASE_URL points at a disposable test database.
"""

import os

import pytest

from db_backend import connect_postgres, is_postgres_configured


@pytest.fixture(autouse=True)
def _empty_postgres_tables():
    if not is_postgres_configured():
        yield
        return
    if os.environ.get("ALICE_TEST_DATABASE_RESET") != "1":
        pytest.exit(
            "ALICE_DATABASE_URL is set: tests empty every table in that database. "
            "Set ALICE_TEST_DATABASE_RESET=1 only for a disposable test database.",
            returncode=2,
        )
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
