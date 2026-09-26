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
