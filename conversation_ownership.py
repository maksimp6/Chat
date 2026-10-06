"""Durable ownership boundary for user-facing conversations.

Conversation metadata remains behind the typed db API during Wave 1 migration.
Ownership itself is authoritative in the file-native Memory DB after a verified
one-time import from the legacy SQL side table.
"""

from __future__ import annotations

from threading import RLock
import time
from typing import Optional, TypedDict

from agent_memory.conversation_ownership_migration import ensure_conversation_ownership_migrated
from agent_memory.conversation_ownership_store import ConversationOwnershipStore
from agent_memory.file_memory_db import FileMemoryDB
from db import get_conversations, memory_file_path

_OWNERSHIP_LOCK = RLock()


class ConversationView(TypedDict):
    id: str
    title: str
    model: str
    created_at: int
    updated_at: int


def _conversation_rows() -> list[ConversationView]:
    rows = get_conversations()  # type: ignore[no-untyped-call]
    return [
        ConversationView(
            id=str(row["id"]),
            title=str(row["title"]),
            model=str(row["model"]),
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
        )
        for row in rows
    ]


def _store() -> ConversationOwnershipStore:
    store = ConversationOwnershipStore(FileMemoryDB(memory_file_path()))
    ensure_conversation_ownership_migrated(store)
    return store


def init_conversation_ownership_table() -> None:
    """Import/verify legacy ownership once, then reopen durable ownership state."""
    with _OWNERSHIP_LOCK:
        _store()


def get_owner(conversation_id: str) -> Optional[str]:
    with _OWNERSHIP_LOCK:
        return _store().get(conversation_id)


def set_owner(conversation_id: str, user_id: str) -> None:
    conversation_id = str(conversation_id).strip()
    user_id = str(user_id).strip()
    if not conversation_id or not user_id:
        raise ValueError("conversation_id and user_id are required")

    with _OWNERSHIP_LOCK:
        store = _store()
        existing_owner = store.get(conversation_id)
        if existing_owner is not None:
            if existing_owner != user_id:
                raise PermissionError("conversation_not_owned")
            return
        store.set(conversation_id, user_id, int(time.time()))


def check_access(conversation_id: str, user_id: str) -> bool:
    owner = get_owner(conversation_id)
    return owner is not None and owner == str(user_id)


def _owned_ids(user_id: str) -> set[str]:
    owner = str(user_id)
    with _OWNERSHIP_LOCK:
        records = _store().records()
    return {
        conversation_id for conversation_id, record in records.items() if record["user_id"] == owner
    }


def list_owned_conversations(user_id: str) -> list[ConversationView]:
    owned = _owned_ids(user_id)
    return [conversation for conversation in _conversation_rows() if conversation["id"] in owned]


def get_owned_conversation(conversation_id: str, user_id: str) -> Optional[ConversationView]:
    if not check_access(conversation_id, user_id):
        return None
    conversation_id = str(conversation_id)
    return next(
        (
            conversation
            for conversation in _conversation_rows()
            if conversation["id"] == conversation_id
        ),
        None,
    )


def delete_owner(conversation_id: str, user_id: Optional[str] = None) -> None:
    with _OWNERSHIP_LOCK:
        store = _store()
        existing_owner = store.get(conversation_id)
        if existing_owner is None:
            return
        if user_id is not None and existing_owner != str(user_id):
            return
        store.delete(conversation_id)
