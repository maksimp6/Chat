"""Anonymous user identity bootstrap for first-run clients.

The anonymous user id is a stable application identity, not an authentication
credential. Bootstrap also issues a random bearer token; only its SHA-256 hash
is stored server-side. Sensitive operations resolve ownership from that token
or another trusted server-side authentication context.
"""

from __future__ import annotations

import hashlib
import json
import re
import secrets
import sqlite3
import time
import uuid
from typing import Any, Mapping, Optional

from db import get_conn

_INSTALLATION_RE = re.compile(r"^[A-Za-z0-9._:-]{16,128}$")
_SENSITIVE_KEY_RE = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|token|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)


def _now() -> int:
    return int(time.time())


def _hash_auth_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _new_auth_token() -> str:
    return secrets.token_urlsafe(32)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _sanitize(v) for k, v in value.items() if not _SENSITIVE_KEY_RE.search(str(k))
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    if isinstance(value, tuple):
        return [_sanitize(v) for v in value]
    return value


def init_user_identity_table() -> None:
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
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
        if "auth_token_hash" not in columns:
            conn.execute("ALTER TABLE users ADD COLUMN auth_token_hash TEXT")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_users_installation_id ON users(installation_id)"
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_users_auth_token_hash ON users(auth_token_hash)"
        )
        conn.commit()
    finally:
        conn.close()


def register_anonymous_user(
    installation_id: str,
    metadata: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    installation_id = str(installation_id or "").strip()
    if not _INSTALLATION_RE.fullmatch(installation_id):
        raise ValueError("invalid installation_id")

    init_user_identity_table()
    sanitized = _sanitize(dict(metadata or {}))
    now = _now()
    auth_token = _new_auth_token()
    auth_token_hash = _hash_auth_token(auth_token)

    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE installation_id = ?",
            (installation_id,),
        ).fetchone()

        if row is None:
            user_id = str(uuid.uuid4())
            conn.execute(
                """INSERT INTO users
                   (id, installation_id, status, metadata_json, auth_token_hash, created_at, updated_at)
                   VALUES (?, ?, 'anonymous', ?, ?, ?, ?)""",
                (
                    user_id,
                    installation_id,
                    json.dumps(sanitized, ensure_ascii=False),
                    auth_token_hash,
                    now,
                    now,
                ),
            )
            conn.commit()
            return {
                "user_id": user_id,
                "installation_id": installation_id,
                "status": "anonymous",
                "new_user": True,
                "created_at": now,
                "auth_token": auth_token,
            }

        if row["status"] != "anonymous":
            raise ValueError("installation is linked to a signed-in account")

        conn.execute(
            """UPDATE users
               SET metadata_json = ?, auth_token_hash = ?, updated_at = ?
               WHERE installation_id = ?""",
            (
                json.dumps(sanitized, ensure_ascii=False),
                auth_token_hash,
                now,
                installation_id,
            ),
        )
        conn.commit()
        return {
            "user_id": row["id"],
            "installation_id": installation_id,
            "status": row["status"],
            "new_user": False,
            "created_at": row["created_at"],
            "auth_token": auth_token,
        }
    finally:
        conn.close()


def authenticate_user_token(token: str) -> Optional[str]:
    token = str(token or "").strip()
    if not token:
        return None

    init_user_identity_table()
    token_hash = _hash_auth_token(token)
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT id FROM users WHERE auth_token_hash = ?",
            (token_hash,),
        ).fetchone()
    finally:
        conn.close()
    return str(row["id"]) if row else None


def get_anonymous_user(user_id: str) -> Optional[dict[str, Any]]:
    init_user_identity_table()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT * FROM users WHERE id = ?",
            (str(user_id),),
        ).fetchone()
    finally:
        conn.close()

    if row is None:
        return None

    try:
        metadata = json.loads(row["metadata_json"] or "{}")
    except (TypeError, ValueError):
        metadata = {}

    return {
        "user_id": row["id"],
        "installation_id": row["installation_id"],
        "status": row["status"],
        "metadata": metadata,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def init_github_accounts_table() -> None:
    init_user_identity_table()
    conn = get_conn()
    try:
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
    finally:
        conn.close()


def _github_installation_id(github_id: str) -> str:
    # Random suffix: bootstrap accepts client-chosen ids, so a fixed one could collide.
    return f"github-{github_id}-{uuid.uuid4().hex}"


def _link_github_account(
    github_id: str,
    login: str,
    current_user_id: Optional[str],
    auth_token: str,
) -> tuple[str, bool]:
    now = _now()
    conn = get_conn()
    try:
        link = conn.execute(
            "SELECT user_id FROM github_accounts WHERE github_id = ?",
            (github_id,),
        ).fetchone()
        new_user = False
        if link is not None:
            user_id = str(link["user_id"])
            conn.execute(
                "UPDATE github_accounts SET login = ?, updated_at = ? WHERE github_id = ?",
                (login, now, github_id),
            )
        else:
            user_id = None
            if current_user_id:
                # Only an anonymous user may be promoted; a user already backed by
                # another GitHub account must never gain a second one this way.
                # The status check sits in the UPDATE itself, so of two concurrent
                # callbacks only one can promote the row. Retiring the installation
                # id stops anonymous bootstrap from minting tokens for it.
                promoted = conn.execute(
                    """UPDATE users SET status = 'github', installation_id = ?
                       WHERE id = ? AND status = 'anonymous'""",
                    (_github_installation_id(github_id), str(current_user_id)),
                )
                if promoted.rowcount == 1:
                    user_id = str(current_user_id)
            if user_id is None:
                user_id = str(uuid.uuid4())
                new_user = True
                conn.execute(
                    """INSERT INTO users
                       (id, installation_id, status, metadata_json, created_at, updated_at)
                       VALUES (?, ?, 'github', '{}', ?, ?)""",
                    (user_id, _github_installation_id(github_id), now, now),
                )
            conn.execute(
                """INSERT INTO github_accounts (github_id, user_id, login, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?)""",
                (github_id, user_id, login, now, now),
            )

        conn.execute(
            "UPDATE users SET status = 'github', auth_token_hash = ?, updated_at = ? WHERE id = ?",
            (_hash_auth_token(auth_token), now, user_id),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        conn.rollback()
        raise
    finally:
        conn.close()
    return user_id, new_user


def sign_in_with_github(
    github_id: Any,
    login: str,
    current_user_id: Optional[str] = None,
) -> dict[str, Any]:
    """Resolve the Alice user for a verified GitHub account and issue a fresh token.

    A GitHub account already linked to a user always signs in as that user.
    Otherwise it is linked to ``current_user_id`` (the anonymous user of this
    browser, so its history is kept) or to a new user.
    """
    github_id = str(github_id or "").strip()
    login = str(login or "").strip()
    if not github_id.isdigit() or not login:
        raise ValueError("invalid github account")

    init_github_accounts_table()
    auth_token = _new_auth_token()
    try:
        user_id, new_user = _link_github_account(github_id, login, current_user_id, auth_token)
    except sqlite3.IntegrityError:
        # A concurrent sign-in created the link first; the retry signs in as it.
        user_id, new_user = _link_github_account(github_id, login, current_user_id, auth_token)

    return {
        "user_id": user_id,
        "github_login": login,
        "new_user": new_user,
        "auth_token": auth_token,
    }


def get_github_login(user_id: str) -> Optional[str]:
    # Runs on every page load; app.py creates the table at startup.
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT login FROM github_accounts WHERE user_id = ? ORDER BY updated_at DESC",
            (str(user_id),),
        ).fetchone()
    finally:
        conn.close()
    return str(row["login"]) if row else None
