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

    server_id = mcp_storage.create_server(
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
    mcp_storage.delete_server(server_id)


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


def test_postgres_legacy_identity_import_then_file_native_github_promotion():
    _require_postgres()
    import hashlib
    import uuid

    import agent_memory.user_identity_migration as identity_migration
    from agent_memory.runtime_store import clear_runtime_memory_db
    from user_identity import (
        authenticate_user_token,
        get_github_login,
        init_github_accounts_table,
        sign_in_with_github,
    )

    clear_runtime_memory_db(db.memory_file_path())
    identity_migration._ensure_legacy_schema()

    user_id = str(uuid.uuid4())
    installation_id = f"pg-legacy-installation-{uuid.uuid4().hex}"
    bootstrap_token = "pg-legacy-bootstrap-token"
    token_hash = hashlib.sha256(bootstrap_token.encode("utf-8")).hexdigest()

    conn = db.get_conn()
    try:
        conn.execute(
            """INSERT INTO users
               (id, installation_id, status, metadata_json, auth_token_hash, created_at, updated_at)
               VALUES (?, ?, 'anonymous', '{}', ?, ?, ?)""",
            (user_id, installation_id, token_hash, 10, 20),
        )
        conn.commit()
    finally:
        conn.close()

    init_github_accounts_table()
    assert authenticate_user_token(bootstrap_token) == user_id

    identity = sign_in_with_github(77, "second-account", user_id)

    assert identity["user_id"] == user_id
    assert identity["new_user"] is False
    assert get_github_login(user_id) == "second-account"
    assert authenticate_user_token(identity["auth_token"]) == user_id


def test_postgres_upgrade_drops_legacy_invocation_conversation_fk():
    _require_postgres()
    db.init_db()
    init_runtime_tables()

    conn = db.get_conn()
    try:
        conn.execute(
            """
            ALTER TABLE invocations
            ADD CONSTRAINT invocations_conversation_id_fkey
            FOREIGN KEY (conversation_id) REFERENCES conversations(id)
            """
        )
        conn.commit()
    finally:
        conn.close()

    init_runtime_tables()

    conn = db.get_conn()
    try:
        remaining = conn.execute(
            """
            SELECT count(*) AS count
            FROM pg_constraint AS c
            JOIN pg_attribute AS a
              ON a.attrelid = c.conrelid
             AND a.attnum = ANY(c.conkey)
            WHERE c.conrelid = 'invocations'::regclass
              AND c.contype = 'f'
              AND a.attname = 'conversation_id'
            """
        ).fetchone()["count"]
        assert remaining == 0

        conn.execute(
            """
            INSERT INTO sessions
                (id, status, metadata_json, created_at, updated_at)
            VALUES (?, 'active', '{}', 1, 1)
            """,
            ("legacy-fk-session",),
        )
        conn.execute(
            """
            INSERT INTO invocations
                (id, session_id, conversation_id, trace_id, status,
                 metadata_json, trace_json, created_at)
            VALUES (?, ?, ?, ?, 'created', '{}', '{}', 1)
            """,
            (
                "legacy-fk-invocation",
                "legacy-fk-session",
                "mcp:synthetic-conversation",
                "legacy-fk-trace",
            ),
        )
        conn.commit()
    finally:
        conn.close()
