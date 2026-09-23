"""Ownership boundary for user-facing conversations.

Conversation rows predate multi-user identity, so ownership lives in a small
side table. This keeps legacy conversation storage compatible while preventing
the MCP surface from reading another user's conversation by id.
"""
from __future__ import annotations

import time
from typing import Optional

from db import get_conn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_owners (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
"""

_INDEX = "CREATE INDEX IF NOT EXISTS idx_conversation_owners_user ON conversation_owners(user_id)"


def init_conversation_ownership_table() -> None:
    conn = get_conn()
    try:
        conn.execute(_SCHEMA)
        conn.execute(_INDEX)
        conn.commit()
    finally:
        conn.close()


def get_owner(conversation_id: str) -> Optional[str]:
    init_conversation_ownership_table()
    conn = get_conn()
    try:
        row = conn.execute(
            "SELECT user_id FROM conversation_owners WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        return str(row["user_id"]) if row else None
    finally:
        conn.close()


def set_owner(conversation_id: str, user_id: str) -> None:
    conversation_id = str(conversation_id).strip()
    user_id = str(user_id).strip()
    if not conversation_id or not user_id:
        raise ValueError("conversation_id and user_id are required")

    init_conversation_ownership_table()
    conn = get_conn()
    try:
        existing = conn.execute(
            "SELECT user_id FROM conversation_owners WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if existing and str(existing["user_id"]) != user_id:
            raise PermissionError("conversation_not_owned")
        conn.execute(
            """INSERT INTO conversation_owners (conversation_id, user_id, created_at)
               VALUES (?, ?, ?)
               ON CONFLICT(conversation_id) DO UPDATE SET user_id = excluded.user_id""",
            (conversation_id, user_id, int(time.time())),
        )
        conn.commit()
    finally:
        conn.close()


def check_access(conversation_id: str, user_id: str) -> bool:
    owner = get_owner(conversation_id)
    return owner is not None and str(owner) == str(user_id)


def list_owned_conversations(user_id: str) -> list[dict]:
    init_conversation_ownership_table()
    conn = get_conn()
    try:
        rows = conn.execute(
            """SELECT c.id, c.title, c.model, c.created_at, c.updated_at
               FROM conversations c
               JOIN conversation_owners o ON o.conversation_id = c.id
               WHERE o.user_id = ?
               ORDER BY c.updated_at DESC""",
            (str(user_id),),
        ).fetchall()
        return [
            {
                "id": row["id"],
                "title": row["title"],
                "model": row["model"],
                "created_at": row["created_at"],
                "updated_at": row["updated_at"],
            }
            for row in rows
        ]
    finally:
        conn.close()


def get_owned_conversation(conversation_id: str, user_id: str) -> Optional[dict]:
    init_conversation_ownership_table()
    conn = get_conn()
    try:
        row = conn.execute(
            """SELECT c.id, c.title, c.model, c.created_at, c.updated_at
               FROM conversations c
               JOIN conversation_owners o ON o.conversation_id = c.id
               WHERE c.id = ? AND o.user_id = ?""",
            (conversation_id, str(user_id)),
        ).fetchone()
        if not row:
            return None
        return {
            "id": row["id"],
            "title": row["title"],
            "model": row["model"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }
    finally:
        conn.close()


def delete_owner(conversation_id: str, user_id: Optional[str] = None) -> None:
    init_conversation_ownership_table()
    conn = get_conn()
    try:
        if user_id:
            conn.execute(
                "DELETE FROM conversation_owners WHERE conversation_id = ? AND user_id = ?",
                (conversation_id, str(user_id)),
            )
        else:
            conn.execute(
                "DELETE FROM conversation_owners WHERE conversation_id = ?",
                (conversation_id,),
            )
        conn.commit()
    finally:
        conn.close()
