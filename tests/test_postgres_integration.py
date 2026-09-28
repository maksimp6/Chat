import os

import pytest

import db
import mcp_storage
from departments import init_department_tables
from local_agent_gateway import init_local_agent_tables
from runtime_migrations import init_runtime_tables
from treasury import init_treasury_tables
from user_identity import init_user_identity_table
from observability_migrations import MIGRATION_VERSION, apply_observability_migrations


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
    apply_observability_migrations()

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
        "schema_migrations",
        "project_tree_preferences",
        "frontend_error_events",
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


def test_postgres_observability_migration_is_idempotent():
    _require_postgres()
    apply_observability_migrations()
    apply_observability_migrations()
    conn = db.get_conn()
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations WHERE version = ?",
            (MIGRATION_VERSION,),
        ).fetchall()
    finally:
        conn.close()
    assert len(rows) == 1


def test_postgres_runtime_tables_drop_legacy_conversation_foreign_key():
    _require_postgres()
    db.init_db()
    init_runtime_tables()

    conn = db.get_conn()
    try:
        # Recreate invocations as earlier releases did, with the conversation key.
        conn.execute("DROP TABLE invocations")
        conn.execute(
            """
            CREATE TABLE invocations (
                id TEXT PRIMARY KEY,
                session_id TEXT NOT NULL,
                conversation_id TEXT NOT NULL,
                trace_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'created',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                result_json TEXT,
                error_json TEXT,
                trace_json TEXT NOT NULL DEFAULT '{}',
                created_at INTEGER NOT NULL,
                started_at INTEGER,
                completed_at INTEGER,
                FOREIGN KEY (session_id) REFERENCES sessions(id),
                FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    init_runtime_tables()

    conn = db.get_conn()
    try:
        conn.execute(
            "INSERT INTO sessions (id, created_at, updated_at) VALUES (?, ?, ?)",
            ("legacy-session", 1, 1),
        )
        conn.execute(
            """
            INSERT INTO invocations (id, session_id, conversation_id, trace_id, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            ("legacy-invocation", "legacy-session", "github-issue-7", "legacy-trace", 1),
        )
        conn.commit()
        count = conn.execute(
            "SELECT COUNT(*) AS count FROM invocations WHERE conversation_id = ?",
            ("github-issue-7",),
        ).fetchone()["count"]
    finally:
        conn.close()
    assert count == 1


def test_postgres_github_sign_in_promotes_anonymous_user_once():
    _require_postgres()
    import threading
    import time
    import uuid

    from user_identity import (
        authenticate_user_token,
        get_github_login,
        init_github_accounts_table,
        register_anonymous_user,
        sign_in_with_github,
    )

    init_github_accounts_table()  # app.py creates the table at startup
    anon = register_anonymous_user(f"pg-installation-{uuid.uuid4().hex}", {})
    github_id = int(uuid.uuid4().int % 10**12)

    # A first callback has claimed the anonymous row but not committed yet.
    first = db.get_conn()
    first.execute(
        "UPDATE users SET status = 'github' WHERE id = ? AND status = 'anonymous'",
        (anon["user_id"],),
    )

    results = []
    second = threading.Thread(
        target=lambda: results.append(
            sign_in_with_github(github_id, "second-account", anon["user_id"])
        )
    )
    second.start()

    # Wait until the second callback is blocked on the row lock, then commit
    # the first claim so the second must re-check the row's status.
    probe = db.get_conn()
    try:
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            waiting = probe.execute(
                "SELECT count(*) AS n FROM pg_stat_activity WHERE wait_event_type = 'Lock'"
            ).fetchone()["n"]
            probe.commit()
            if waiting:
                break
            time.sleep(0.05)
        assert waiting, "second sign-in never waited on the claimed row"
    finally:
        probe.close()
    first.commit()
    first.close()
    second.join(timeout=10)

    assert len(results) == 1
    identity = results[0]
    assert identity["user_id"] != anon["user_id"]
    assert identity["new_user"] is True
    assert get_github_login(anon["user_id"]) is None
    assert authenticate_user_token(identity["auth_token"]) == identity["user_id"]


def test_postgres_converts_legacy_conv_settings_timestamp_column():
    _require_postgres()
    conn = db.get_conn()
    try:
        conn.execute("DROP TABLE IF EXISTS conv_settings")
        conn.execute(
            """
            CREATE TABLE conv_settings (
                conversation_id TEXT PRIMARY KEY,
                settings_json TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        conn.commit()
    finally:
        conn.close()

    db.init_db()
    db.save_conv_settings("legacy-settings", {"active_tool_categories": ["web"]})

    assert db.get_conv_settings("legacy-settings") == {"active_tool_categories": ["web"]}
