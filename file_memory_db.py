"""Durable single-file append-only key/value store.

The authoritative database is one JSONL file. Normal writes append one checksummed
record and fsync before success. Compaction writes a temporary snapshot, fsyncs it,
then atomically replaces the authoritative file.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any


class FileMemoryError(Exception):
    """Base error for durable file memory."""


class FileMemoryCorruption(FileMemoryError):
    """Raised when committed content is corrupt."""


class FileMemoryDB:
    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = RLock()
        self._state: dict[str, Any] = {}
        self._seq = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._recover()

    @property
    def sequence(self) -> int:
        with self._lock:
            return self._seq

    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)

    def items(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def put(self, key: str, value: Any) -> int:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with self._lock:
            seq = self._seq + 1
            record = {"v": self.VERSION, "seq": seq, "op": "put", "key": key, "value": value}
            self._append(record)
            self._state[key] = value
            self._seq = seq
            return seq

    def delete(self, key: str) -> int:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with self._lock:
            seq = self._seq + 1
            record = {"v": self.VERSION, "seq": seq, "op": "delete", "key": key}
            self._append(record)
            self._state.pop(key, None)
            self._seq = seq
            return seq

    def compact(self) -> None:
        with self._lock:
            snapshot = {
                "v": self.VERSION,
                "seq": self._seq,
                "op": "snapshot",
                "value": self._state,
            }
            payload = self._encode(snapshot)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
            try:
                self._write_all(fd, payload)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(temporary, self.path)
            self._fsync_parent()

    def _append(self, record: dict[str, Any]) -> None:
        payload = self._encode(record)
        existed = self.path.exists()
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            self._write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            self._fsync_parent()

    @staticmethod
    def _write_all(fd: int, payload: bytes) -> None:
        offset = 0
        while offset < len(payload):
            written = os.write(fd, payload[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written

    def _recover(self) -> None:
        if not self.path.exists():
            return

        data = self.path.read_bytes()
        state: dict[str, Any] = {}
        seq = 0
        offset = 0
        last_good = 0

        for index, raw in enumerate(data.splitlines(keepends=True)):
            is_last = offset + len(raw) == len(data)
            offset += len(raw)

            if not raw.endswith(b"\n"):
                if is_last:
                    self._truncate(last_good)
                    break
                raise FileMemoryCorruption("unterminated record before file tail")

            line = raw[:-1]
            try:
                record = json.loads(line)
                self._validate_record(record, seq, index == 0)
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
                if is_last:
                    self._truncate(last_good)
                    break
                raise FileMemoryCorruption(f"corrupt record at offset {last_good}") from exc

            op = record["op"]
            if op == "snapshot":
                state = dict(record["value"])
            elif op == "put":
                state[record["key"]] = record["value"]
            elif op == "delete":
                state.pop(record["key"], None)

            seq = record["seq"]
            last_good = offset

        self._state = state
        self._seq = seq

    def _validate_record(self, record: dict[str, Any], previous_seq: int, first: bool) -> None:
        if record.get("v") != self.VERSION:
            raise ValueError("unsupported version")
        if not isinstance(record.get("seq"), int) or record["seq"] < 0:
            raise ValueError("invalid sequence")
        if not first and record["seq"] <= previous_seq:
            raise ValueError("non-monotonic sequence")
        if record.get("op") not in {"put", "delete", "snapshot"}:
            raise ValueError("invalid operation")
        if record["op"] in {"put", "delete"} and not isinstance(record.get("key"), str):
            raise ValueError("invalid key")
        if record["op"] == "snapshot" and not isinstance(record.get("value"), dict):
            raise ValueError("invalid snapshot")
        supplied = record.get("sha256")
        unsigned = dict(record)
        unsigned.pop("sha256", None)
        expected = self._checksum(unsigned)
        if not isinstance(supplied, str) or not hashlib.compare_digest(supplied, expected):
            raise ValueError("checksum mismatch")

    @classmethod
    def _encode(cls, record: dict[str, Any]) -> bytes:
        signed = dict(record)
        signed["sha256"] = cls._checksum(record)
        return (json.dumps(signed, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")

    @staticmethod
    def _checksum(record: dict[str, Any]) -> str:
        canonical = json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()

    def _truncate(self, size: int) -> None:
        fd = os.open(self.path, os.O_WRONLY)
        try:
            os.ftruncate(fd, size)
            os.fsync(fd)
        finally:
            os.close(fd)

    def _fsync_parent(self) -> None:
        fd = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


__all__ = ["FileMemoryCorruption", "FileMemoryDB", "FileMemoryError"]
