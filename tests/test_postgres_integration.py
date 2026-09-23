import os

import pytest

import db
import mcp_storage
from departments import init_department_tables
from local_agent_gateway import init_local_agent_tables
from runtime_migrations import init_runtime_tables
from treasury import init_treasury_tables
from user_identity import init_user_identity_table


def _require_postgres():
    if not os.environ.get("ALICE_DATABASE_URL"):
        pytest.skip("PostgreSQL integration environment is not configured")


def test_postgres_bootstraps_shared_application_schema():
    _require_postgres()

    db.init_db()
    init_runtime_tables()
    mcp_storage.init_db()
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
        "configs",
        "provider_credentials",
        "managed_keys",
        "mcp_servers",
        "conv_mcp_settings",
        "conv_yandex_map",
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


def test_postgres_is_the_same_store_used_by_application_modules():
    _require_postgres()

    db.create_conversation("postgres-shared", "Shared DB", "test-model")
    conn = db.get_conn()
    try:
        row = conn.execute(
            "SELECT title FROM conversations WHERE id = ?",
            ("postgres-shared",),
        ).fetchone()
    finally:
        conn.close()

    assert row["title"] == "Shared DB"

    mcp_storage.create_server(
        {
            "name": "postgres-shared",
            "server_url": "http://example.invalid/mcp",
            "connector_id": "test",
            "server_label": "",
            "server_description": "",
            "authorization": "",
            "headers": "",
            "allowed_tools": "",
            "allowed_tools_read_only": 0,
            "require_approval": "always",
            "require_approval_tools": "",
            "require_approval_read_only": 0,
            "require_approval_never_tools": "",
            "require_approval_never_read_only": 0,
            "defer_loading": 0,
        }
    )

    conn = db.get_conn()
    try:
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM mcp_servers WHERE name = ?",
            ("postgres-shared",),
        ).fetchone()["count"]
    finally:
        conn.close()

    assert count == 1
