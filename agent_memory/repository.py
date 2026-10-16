"""Native MemoryStore repository for source-linked agent memory records."""

from __future__ import annotations

import time
from typing import Any

from agent_memory.client import get_memory_client
from storage.db import memory_file_path
from memory_engine.store import MemoryStore

from agent_memory.models import MemoryLookup, MemoryRecord


_MEMORY_PREFIX = "agent_memory:"


class AgentMemoryStore:
    """Persist validated records as independent native MemoryStore journal keys."""

    def __init__(self, store: MemoryStore | None = None) -> None:
        self._store = store if store is not None else get_memory_client(memory_file_path())

    @staticmethod
    def _key(memory_id: str) -> str:
        return _MEMORY_PREFIX + str(memory_id)

    @staticmethod
    def _record_from_value(value: Any) -> MemoryRecord:
        if not isinstance(value, dict):
            raise ValueError("invalid agent memory record")
        return MemoryRecord(**value)

    def upsert(self, record: MemoryRecord) -> MemoryRecord:
        key = self._key(record.memory_id)
        with self._store.transaction() as tx:
            existing = tx.get(key)
            data = record.as_dict()
            if existing is not None:
                original = self._record_from_value(existing)
                data["created_at"] = original.created_at
            tx.set(key, data)
        stored = self.get(record.memory_id)
        if stored is None:
            raise RuntimeError("memory upsert did not persist")
        return stored

    def get(self, memory_id: str) -> MemoryRecord | None:
        value = self._store.get(self._key(memory_id))
        return None if value is None else self._record_from_value(value)

    def lookup(
        self,
        memory_id: str,
        *,
        source_version: str | None = None,
        visible_to: str | None = None,
        now: int | None = None,
    ) -> MemoryLookup:
        record = self.get(memory_id)
        if record is None:
            return MemoryLookup(status="miss", memory_id=str(memory_id), reason="not_found")
        if record.visibility and (visible_to is None or str(visible_to) not in record.visibility):
            return MemoryLookup(
                status="miss",
                memory_id=record.memory_id,
                reason="not_visible",
            )
        if record.status == "superseded":
            return MemoryLookup(
                status="superseded",
                memory_id=record.memory_id,
                record=record,
                reason="superseded",
            )
        if record.status == "stale":
            return MemoryLookup(
                status="stale",
                memory_id=record.memory_id,
                record=record,
                reason="marked_stale",
            )
        if source_version is not None and str(source_version) != record.source_version:
            return MemoryLookup(
                status="stale",
                memory_id=record.memory_id,
                record=record,
                reason="source_version_changed",
            )
        if record.is_expired(now=now):
            return MemoryLookup(
                status="stale",
                memory_id=record.memory_id,
                record=record,
                reason="expired",
            )
        return MemoryLookup(status="hit", memory_id=record.memory_id, record=record)

    def list_records(
        self,
        *,
        kind: str | None = None,
        scope: str | None = None,
        status: str | None = "active",
        visible_to: str | None = None,
        now: int | None = None,
    ) -> list[MemoryRecord]:
        records = [
            self._record_from_value(item)
            for item in self._store.items(prefix=_MEMORY_PREFIX).values()
        ]
        if kind is not None:
            records = [item for item in records if item.kind == str(kind)]
        if scope is not None:
            records = [item for item in records if item.scope == str(scope)]
        if status is not None:
            records = [item for item in records if item.status == str(status)]
        records.sort(key=lambda item: (-item.updated_at, item.memory_id))
        if status == "active":
            records = [item for item in records if not item.is_expired(now=now)]
        if visible_to is None:
            return records
        role = str(visible_to)
        return [item for item in records if not item.visibility or role in item.visibility]

    def mark_stale(self, memory_id: str, *, reason: str, now: int | None = None) -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        timestamp = int(time.time()) if now is None else int(now)
        freshness = record.as_dict()["freshness"]
        freshness["stale_reason"] = str(reason)
        freshness["invalidated_at"] = timestamp
        updated = MemoryRecord(
            **{
                **record.as_dict(),
                "freshness": freshness,
                "status": "stale",
                "updated_at": timestamp,
            }
        )
        self.upsert(updated)
        return True

    def supersede(
        self,
        memory_id: str,
        *,
        replacement_id: str,
        now: int | None = None,
    ) -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        if str(replacement_id) == record.memory_id:
            raise ValueError("memory cannot supersede itself")
        if self.get(replacement_id) is None:
            raise ValueError("replacement memory does not exist")
        timestamp = int(time.time()) if now is None else int(now)
        updated = MemoryRecord(
            **{
                **record.as_dict(),
                "status": "superseded",
                "superseded_by": str(replacement_id),
                "updated_at": timestamp,
            }
        )
        self.upsert(updated)
        return True

    def delete(self, memory_id: str) -> bool:
        key = self._key(memory_id)
        with self._store.transaction() as tx:
            if tx.get(key) is None:
                return False
            tx.delete(key)
        return True

    def export_records(self) -> list[dict[str, Any]]:
        records = self.list_records(status=None)
        return [record.as_dict() for record in sorted(records, key=lambda item: item.memory_id)]
