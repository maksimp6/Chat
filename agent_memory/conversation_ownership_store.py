"""Durable conversation ownership records on the native Kristall engine."""

from __future__ import annotations

from typing import Any, TypedDict

OWNER_PREFIX = "conversation_owner:"


class OwnershipRecord(TypedDict):
    user_id: str
    created_at: int


class ConversationOwnershipStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    @staticmethod
    def key(conversation_id: str) -> str:
        return OWNER_PREFIX + str(conversation_id)

    @staticmethod
    def _record(record: object) -> OwnershipRecord:
        if not isinstance(record, dict):
            raise ValueError("invalid conversation ownership record")
        owner = record.get("user_id")
        created_at = record.get("created_at")
        if not isinstance(owner, str) or not owner or type(created_at) is not int:
            raise ValueError("invalid conversation ownership record")
        return {"user_id": owner, "created_at": created_at}

    def get(self, conversation_id: str) -> str | None:
        record = self.db.get(self.key(conversation_id))
        if record is None:
            return None
        return self._record(record)["user_id"]

    def records(self) -> dict[str, OwnershipRecord]:
        return {
            key[len(OWNER_PREFIX) :]: self._record(value)
            for key, value in self.db.items(prefix=OWNER_PREFIX).items()
        }

    def set(self, conversation_id: str, user_id: str, created_at: int) -> None:
        key = self.key(conversation_id)
        with self.db.transaction() as tx:
            previous = tx.get(key)
            if previous is not None:
                if self._record(previous)["user_id"] != user_id:
                    raise PermissionError("conversation_not_owned")
                return
            tx.set(key, {"user_id": str(user_id), "created_at": int(created_at)})

    def delete(self, conversation_id: str, user_id: str) -> None:
        """Delete the record only if ``user_id`` owns it; there is no unscoped delete."""
        key = self.key(conversation_id)
        with self.db.transaction() as tx:
            previous = tx.get(key)
            if previous is None:
                return
            if self._record(previous)["user_id"] != str(user_id):
                return
            tx.delete(key)


__all__ = ["OWNER_PREFIX", "ConversationOwnershipStore", "OwnershipRecord"]
