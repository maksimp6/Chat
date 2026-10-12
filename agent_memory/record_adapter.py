"""Compatibility-shaped application repository backed by the accepted MemoryStore.

This is an API adapter, not a second storage engine. One JSON object under a
reserved key provides atomic put/delete and a consistent items snapshot.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from memory_engine import MemoryStore, StoreError

_RECORDS = "alice/legacy-records"


class MemoryRecordAdapter:
    def __init__(self, store: MemoryStore) -> None:
        self._store = store

    @property
    def path(self) -> Path:
        return self._store._engine.path

    @property
    def sequence(self) -> int:
        return self._store.last_commit.sequence

    def get(self, key: str, default: Any = None) -> Any:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        values = self._store.get(_RECORDS) or {}
        return deepcopy(values.get(key, default))

    def items(self) -> dict[str, Any]:
        return deepcopy(self._store.get(_RECORDS) or {})

    def put(self, key: str, value: Any) -> int:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with self._store.transaction() as tx:
            values = tx.get(_RECORDS) or {}
            values[key] = deepcopy(value)
            tx.set(_RECORDS, values)
        return self.sequence

    def delete(self, key: str) -> int:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with self._store.transaction() as tx:
            values = tx.get(_RECORDS) or {}
            values.pop(key, None)
            tx.set(_RECORDS, values)
        return self.sequence

    def close(self) -> None:
        self._store.close()


__all__ = ["MemoryRecordAdapter"]
