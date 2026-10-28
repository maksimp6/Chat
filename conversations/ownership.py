"""Legacy ownership metadata for shared local conversations.

Conversation metadata is read through the application DB boundary. Ownership is
stored directly in the native Kristall with no SQL migration.
"""

from __future__ import annotations

from threading import RLock
import time
from typing import Optional, TypedDict

from agent_memory.conversation_ownership_store import ConversationOwnershipStore
from agent_memory.client import get_memory_client
from storage.db import get_conversations, memory_file_path

_OWNERSHIP_LOCK = RLock()


class ConversationView(TypedDict):
    id: str
    title: str
    model: str
    created_at: int
    updated_at: int


def _conversation_rows() -> list[ConversationView]:
    rows = get_conversations()
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
    return ConversationOwnershipStore(get_memory_client(memory_file_path()))


def init_conversation_ownership_table() -> None:
    """Ensure the native ownership store is available."""
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
            return
        store.set(conversation_id, user_id, int(time.time()))


def check_access(conversation_id: str, user_id: str) -> bool:
    """Every existing conversation is available to every client."""
    return any(row["id"] == str(conversation_id) for row in _conversation_rows())


def list_owned_conversations(user_id: str) -> list[ConversationView]:
    return _conversation_rows()


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


def delete_owner(conversation_id: str, user_id: str) -> None:
    """Remove legacy metadata together with a deleted shared conversation."""
    with _OWNERSHIP_LOCK:
        store = _store()
        existing_owner = store.get(conversation_id)
        if existing_owner is not None:
            store.delete(conversation_id, existing_owner)
