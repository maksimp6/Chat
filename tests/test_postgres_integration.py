import os

import pytest

import db
import runtime_migrations
from departments import init_department_tables
from local_agent_gateway import init_local_agent_tables
from treasury import init_treasury_tables
from user_identity import init_user_identity_table


def _require_postgres():
    if not os.environ.get("ALICE_DATABASE_URL"):
        pytest.skip("PostgreSQL integration environment is not configured")


def test_postgres_shared_schema_bootstraps():
    _require_postgres()
    db.init_db()
    runtime_migrations.init_runtime_tables()
    init_local_agent_tables()
    init_treasury_tables()
    init_user_identity_table()
    init_department_tables()

    conn = db.get_conn()
    try:
        tables = {
            row["tablename"]
            for row in conn.execute(
                """
                SELECT tablename
                FROM pg_catalog.pg_tables
                WHERE schemaname = 'public'
                """
            ).fetchall()
        }
    finally:
        conn.close()

    expected = {
        "conversations",
        "messages",
        "conv_settings",
        "provider_credentials",
        "managed_keys",
        "sessions",
        "invocations",
        "local_agents",
        "local_agent_jobs",
        "treasury_accounts",
        "treasury_ledger",
        "users",
        "departments",
    }
    assert expected.issubset(tables)


def test_postgres_database_is_reused_by_all_modules():
    _require_postgres()
    db.create_conversation("postgres-shared-test", "Shared DB", "test-model")

    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?",
            ("postgres-shared-test",),
        ).fetchone()
    finally:
        conn.close()

    assert row["title"] == "Shared DB"
