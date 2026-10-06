"""Bounded SQL reader for the conversation-ownership cutover.

SQL remains only as the legacy import source. Once the durable migration marker is
committed, normal ownership operations never consult SQL again.
"""

from __future__ import annotations

from agent_memory.conversation_ownership_store import (
    ConversationOwnershipStore,
    OwnershipRecord,
)
from db import get_conn

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_owners (
    conversation_id TEXT PRIMARY KEY,
    user_id TEXT NOT NULL,
    created_at INTEGER NOT NULL
)
"""

_INDEX = "CREATE INDEX IF NOT EXISTS idx_conversation_owners_user ON conversation_owners(user_id)"


def _legacy_records() -> dict[str, OwnershipRecord]:
    conn = get_conn()
    try:
        conn.execute(_SCHEMA)
        conn.execute(_INDEX)
        conn.commit()
        rows = conn.execute(
            """SELECT conversation_id, user_id, created_at
               FROM conversation_owners
               ORDER BY conversation_id"""
        ).fetchall()
        return {
            str(row["conversation_id"]): OwnershipRecord(
                user_id=str(row["user_id"]),
                created_at=int(row["created_at"]),
            )
            for row in rows
        }
    finally:
        conn.close()


def ensure_conversation_ownership_migrated(store: ConversationOwnershipStore) -> None:
    if store.migration_marker() is not None:
        return

    source = _legacy_records()
    target = store.records()

    unexpected = set(target) - set(source)
    conflicts = {
        conversation_id
        for conversation_id in set(target) & set(source)
        if target[conversation_id] != source[conversation_id]
    }
    if unexpected or conflicts:
        raise RuntimeError("conversation ownership migration verification failed")

    for conversation_id, record in source.items():
        if conversation_id not in target:
            store.set(conversation_id, record["user_id"], record["created_at"])

    if store.records() != source:
        raise RuntimeError("conversation ownership migration verification failed")

    store.mark_migrated(source)


__all__ = ["ensure_conversation_ownership_migrated"]
