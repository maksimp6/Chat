"""Durable single-writer file-backed typed Memory DB (first implementation slice).

Uses atomic, fsynced snapshot replacement; no SQL or legacy data import.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable

from memory_db import Column, MemoryDatabase, DatabaseError


class DurableMemoryDatabase(MemoryDatabase):
    """Persist committed typed table rows with a durable file barrier."""

    def __init__(self, path: str | Path) -> None:
        super().__init__()
        self.path = Path(path)
        self._loaded: dict[str, list[dict[str, Any]]] = {}
        self._transaction_depth = 0
        if self.path.exists():
            raw = self.path.read_bytes()
            if raw:
                # A partial trailing record after a complete newline is ignored.
                records = raw.split(b"\n")
                complete = records[:-1] if not raw.endswith(b"\n") else records[:-1]
                if not complete:
                    raise DatabaseError("no complete durable snapshot")
                frame = json.loads(complete[-1])
                payload = frame["payload"].encode("utf-8")
                if hashlib.sha256(payload).hexdigest() != frame["sha256"]:
                    raise DatabaseError("corrupt durable snapshot")
                self._loaded = json.loads(payload)

    def create_table(self, name: str, columns: Iterable[Column]):
        with self._lock:
            table = super().create_table(name, columns)
            for row in self._loaded.get(name, []):
                table.insert(row)
            return table

    def _durable_barrier(self) -> None:
        payload = json.dumps(
            {name: table.rows for name, table in self._tables.items()},
            ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        )
        frame = json.dumps(
            {"payload": payload, "sha256": hashlib.sha256(payload.encode()).hexdigest()},
            ensure_ascii=False, separators=(",", ":"),
        ).encode() + b"\n"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temp = tempfile.mkstemp(prefix=".alice-", dir=self.path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(frame)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp, self.path)
            dir_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if os.path.exists(temp):
                os.unlink(temp)

    def _mutate(self, operation):
        with self._lock:
            from copy import deepcopy
            previous = deepcopy(self._tables)
            result = operation()
            try:
                if not self._transaction_depth:
                    self._durable_barrier()
            except BaseException:
                self._tables = previous
                raise
            return result

    def insert(self, table: str, **values: Any):
        return self._mutate(lambda: super(DurableMemoryDatabase, self).insert(table, **values))

    def update(self, table: str, predicate, **values: Any):
        return self._mutate(lambda: super(DurableMemoryDatabase, self).update(table, predicate, **values))

    def delete(self, table: str, predicate):
        return self._mutate(lambda: super(DurableMemoryDatabase, self).delete(table, predicate))

    def backup(self, destination: str | Path) -> None:
        """Copy an already durable snapshot, never uncommitted memory."""
        import shutil
        with self._lock:
            if not self.path.exists():
                self._durable_barrier()
            shutil.copyfile(self.path, destination)

    def restore(self, source: str | Path) -> None:
        """Restore only into an empty instance before tables are created."""
        with self._lock:
            if self._tables:
                raise DatabaseError("restore requires an empty instance")
            candidate = DurableMemoryDatabase(source)
            self._loaded = candidate._loaded
            # Restore the verified snapshot without replacing it with empty tables.
            from shutil import copyfile
            self.path.parent.mkdir(parents=True, exist_ok=True)
            copyfile(source, self.path)
