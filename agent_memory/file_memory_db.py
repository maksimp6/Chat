"""Durable JSONL Memory DB with serialized cross-process file operations.

A persistent sidecar flock serializes recover/read/append/compaction and export.
Writes are acknowledged only after fsync; uncertain writes poison the handle.
All participants must use this API on one POSIX filesystem supporting flock.
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import hmac
import json
import os
from contextlib import contextmanager
from pathlib import Path
from threading import RLock
from typing import Any, Iterator


class FileMemoryError(Exception):
    """Base error for durable file memory."""


class FileMemoryCorruption(FileMemoryError):
    """Raised when committed content is corrupt."""


class FileMemoryDB:
    VERSION = 1

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock_path = self.path.with_name(f"{self.path.name}.lock")
        self._lock = RLock()
        self._state: dict[str, Any] = {}
        self._seq = 0
        self._poisoned = False
        self._ever_seen = self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._guard():
            pass

    @contextmanager
    def _guard(self, *, mutate: bool = False) -> Iterator[None]:
        """Hold process and filesystem locks around a consistent state boundary."""
        with self._lock:
            if self._poisoned:
                if mutate:
                    raise FileMemoryError("uncertain write outcome; reopen after recovery")
                yield  # Reads return only the last locally acknowledged state.
                return
            flags = os.O_CREAT | os.O_RDWR | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(self._lock_path, flags, 0o600)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
                self._recover()
                yield
            finally:
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                finally:
                    os.close(fd)

    @property
    def sequence(self) -> int:
        with self._guard():
            return self._seq

    def get(self, key: str, default: Any = None) -> Any:
        with self._guard():
            return copy.deepcopy(self._state.get(key, default))

    def items(self) -> dict[str, Any]:
        with self._guard():
            return copy.deepcopy(self._state)

    def put(self, key: str, value: Any) -> int:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with self._guard(mutate=True):
            seq = self._seq + 1
            record = {"v": self.VERSION, "seq": seq, "op": "put", "key": key, "value": value}
            self._commit_record(record)
            self._state[key] = copy.deepcopy(value)
            self._seq = seq
            return seq

    def delete(self, key: str) -> int:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        with self._guard(mutate=True):
            seq = self._seq + 1
            record = {"v": self.VERSION, "seq": seq, "op": "delete", "key": key}
            self._commit_record(record)
            self._state.pop(key, None)
            self._seq = seq
            return seq

    def _commit_record(self, record: dict[str, Any]) -> None:
        payload = self._encode(record)
        try:
            self._append_payload(payload)
        except OSError:
            self._poisoned = True
            raise

    def _append_payload(self, payload: bytes) -> None:
        existed = self.path.exists()
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_WRONLY, 0o600)
        try:
            self._write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        if not existed:
            self._fsync_parent()
        self._ever_seen = True

    def export_snapshot(self) -> tuple[bytes, int]:
        """Return mutually consistent bytes and sequence, for backups only."""
        with self._guard(mutate=True):
            return self.path.read_bytes() if self.path.exists() else b"", self._seq

    def compact(self) -> None:
        with self._guard(mutate=True):
            snapshot = {
                "v": self.VERSION,
                "seq": self._seq,
                "op": "snapshot",
                "value": self._state,
            }
            payload = self._encode(snapshot)
            temporary = self.path.with_name(f"{self.path.name}.tmp")
            try:
                fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
                try:
                    self._write_all(fd, payload)
                    os.fsync(fd)
                finally:
                    os.close(fd)
                os.replace(temporary, self.path)
                self._fsync_parent()
                self._ever_seen = True
            except OSError:
                self._poisoned = True
                raise
            finally:
                temporary.unlink(missing_ok=True)

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
            if self._ever_seen:
                raise FileMemoryCorruption("authoritative store disappeared")
            self._state = {}
            self._seq = 0
            return

        data = self.path.read_bytes()
        state: dict[str, Any] = {}
        seq = 0
        offset = 0
        last_good = 0

        for index, raw in enumerate(data.splitlines(keepends=True)):
            offset += len(raw)
            if not raw.endswith(b"\n"):
                self._truncate(last_good)
                break

            try:
                record = json.loads(raw[:-1])
                self._validate_record(record, seq, index == 0)
            except (json.JSONDecodeError, UnicodeDecodeError, KeyError, TypeError, ValueError) as exc:
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
        self._ever_seen = True

    def _validate_record(self, record: dict[str, Any], previous_seq: int, first: bool) -> None:
        if not isinstance(record, dict):
            raise ValueError("record must be an object")
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
        if not isinstance(supplied, str) or not hmac.compare_digest(supplied, expected):
            raise ValueError("checksum mismatch")

    @classmethod
    def _encode(cls, record: dict[str, Any]) -> bytes:
        signed = dict(record)
        signed["sha256"] = cls._checksum(record)
        return (
            json.dumps(signed, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n"
        ).encode("utf-8")

    @staticmethod
    def _checksum(record: dict[str, Any]) -> str:
        canonical = json.dumps(
            record, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
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
