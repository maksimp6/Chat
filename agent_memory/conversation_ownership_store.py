"""Typed durable repository for conversation ownership records."""

from __future__ import annotations

import hashlib
import json
from typing import Iterable, Mapping, TypedDict

from agent_memory.file_memory_db import FileMemoryDB

OWNER_PREFIX = "conversation_owner:"
MIGRATION_KEY = "migration:conversation_owners:v1"


class OwnershipRecord(TypedDict):
    user_id: str
    created_at: int


class MigrationMarker(TypedDict):
    schema: int
    count: int
    digest: str


class ConversationOwnershipStore:
    def __init__(self, db: FileMemoryDB) -> None:
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
        if not isinstance(owner, str) or not owner or not isinstance(created_at, int):
            raise ValueError("invalid conversation ownership record")
        return {"user_id": owner, "created_at": created_at}

    def get(self, conversation_id: str) -> str | None:
        record = self.db.get(self.key(conversation_id))
        if record is None:
            return None
        return self._record(record)["user_id"]

    def records(self) -> dict[str, OwnershipRecord]:
        result: dict[str, OwnershipRecord] = {}
        for key, record in self.db.items().items():
            if not key.startswith(OWNER_PREFIX):
                continue
            result[key[len(OWNER_PREFIX) :]] = self._record(record)
        return result

    def set(self, conversation_id: str, user_id: str, created_at: int) -> None:
        self.db.put(
            self.key(conversation_id),
            {"user_id": str(user_id), "created_at": int(created_at)},
        )

    def delete(self, conversation_id: str) -> None:
        key = self.key(conversation_id)
        if self.db.get(key) is not None:
            self.db.delete(key)

    def migration_marker(self) -> MigrationMarker | None:
        marker = self.db.get(MIGRATION_KEY)
        if marker is None:
            return None
        if not isinstance(marker, dict):
            raise ValueError("invalid conversation ownership migration marker")

        schema = marker.get("schema")
        count = marker.get("count")
        digest = marker.get("digest")
        if (
            not isinstance(schema, int)
            or not isinstance(count, int)
            or not isinstance(digest, str)
            or not digest
        ):
            raise ValueError("invalid conversation ownership migration marker")
        return {"schema": schema, "count": count, "digest": digest}

    def mark_migrated(self, records: Mapping[str, OwnershipRecord]) -> None:
        self.db.put(
            MIGRATION_KEY,
            {
                "schema": 1,
                "count": len(records),
                "digest": ownership_digest(records.items()),
            },
        )


def ownership_digest(records: Iterable[tuple[str, OwnershipRecord]]) -> str:
    ordered = sorted(records, key=lambda item: item[0])
    normalized = [
        {
            "conversation_id": conversation_id,
            "user_id": record["user_id"],
            "created_at": record["created_at"],
        }
        for conversation_id, record in ordered
    ]
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


__all__ = [
    "ConversationOwnershipStore",
    "MIGRATION_KEY",
    "MigrationMarker",
    "OWNER_PREFIX",
    "OwnershipRecord",
    "ownership_digest",
]
