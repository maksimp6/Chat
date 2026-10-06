"""One-time verified import of legacy SQL identity state into alice.memory."""

from __future__ import annotations

from agent_memory.user_identity_store import (
    GitHubAccountRecord,
    UserIdentityStore,
    UserRecord,
    identity_digest,
    migrated_identity_state,
)
from db import get_conn
from db_backend import OperationalError


def _ensure_legacy_schema() -> None:
    conn = get_conn()
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS users (
                id TEXT PRIMARY KEY,
                installation_id TEXT NOT NULL UNIQUE,
                status TEXT NOT NULL DEFAULT 'anonymous',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                auth_token_hash TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        columns = {str(row["name"]) for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "auth_token_hash" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN auth_token_hash TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_installation_id ON users(installation_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_auth_token_hash ON users(auth_token_hash)"
        )
        conn.execute(
            """CREATE TABLE IF NOT EXISTS github_accounts (
                github_id TEXT PRIMARY KEY,
                user_id TEXT NOT NULL,
                login TEXT NOT NULL,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )"""
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_github_accounts_user_id ON github_accounts(user_id)"
        )
        conn.commit()
    except OperationalError:
        conn.rollback()
        raise
    finally:
        conn.close()


def _legacy_identity() -> tuple[dict[str, UserRecord], dict[str, GitHubAccountRecord]]:
    _ensure_legacy_schema()
    conn = get_conn()
    try:
        user_rows = conn.execute(
            """SELECT id, installation_id, status, metadata_json, auth_token_hash,
                      created_at, updated_at
               FROM users
               ORDER BY id"""
        ).fetchall()
        account_rows = conn.execute(
            """SELECT github_id, user_id, login, created_at, updated_at
               FROM github_accounts
               ORDER BY github_id"""
        ).fetchall()
    finally:
        conn.close()

    users: dict[str, UserRecord] = {
        str(row["id"]): {
            "installation_id": str(row["installation_id"]),
            "status": str(row["status"]),
            "metadata_json": str(row["metadata_json"] or "{}"),
            "auth_token_hash": (
                str(row["auth_token_hash"]) if row["auth_token_hash"] is not None else None
            ),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }
        for row in user_rows
    }
    accounts: dict[str, GitHubAccountRecord] = {
        str(row["github_id"]): {
            "user_id": str(row["user_id"]),
            "login": str(row["login"]),
            "created_at": int(row["created_at"]),
            "updated_at": int(row["updated_at"]),
        }
        for row in account_rows
    }
    return users, accounts


def ensure_user_identity_migrated(store: UserIdentityStore) -> None:
    if store.load() is not None:
        return

    users, accounts = _legacy_identity()
    source_digest = identity_digest(users, accounts)
    store.save(migrated_identity_state(users, accounts))

    recovered = store.load()
    if recovered is None:
        raise RuntimeError("user identity migration verification failed")
    if recovered["users"] != users or recovered["github_accounts"] != accounts:
        raise RuntimeError("user identity migration verification failed")
    if recovered["migration"]["digest"] != source_digest:
        raise RuntimeError("user identity migration verification failed")


__all__ = ["ensure_user_identity_migrated"]
