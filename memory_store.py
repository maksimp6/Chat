"""Minimal synchronous typed key/value Memory DB.

One process owns a store. File locking prevents concurrent process writers.
No SQL, background flush, or unsafe pickle deserialization.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
from threading import RLock
from typing import Any, Iterator, Protocol


class StoreError(RuntimeError):
    """Persistence, integrity, or ownership failure."""


class Store(Protocol):
    """Typed application boundary; never expose mutable internal state."""

    def get(self, namespace: str, key: str) -> Any | None: ...
    def set(self, namespace: str, key: str, value: Any) -> None: ...
    def delete(self, namespace: str, key: str) -> None: ...


@dataclass(frozen=True)
class Commit:
    sequence: int
    digest: str


class MemoryStore:
    """In-process committed RAM state backed by an fsynced append-only log."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        self._lock_file = self.path.with_name(self.path.name + ".lock").open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            raise StoreError("database already has a writer") from exc
        self._state: dict[str, dict[str, Any]] = {}
        self._sequence = 0
        self._digest = "0" * 64
        self._failed = False
        self._active_transaction = False
        try:
            self._replay()
        except BaseException:
            self.close()
            raise

    @property
    def last_commit(self) -> Commit:
        return Commit(self._sequence, self._digest)

    def _replay(self) -> None:
        if not self.path.exists():
            return
        raw = self.path.read_bytes()
        frames = raw.split(b"\n")
        if frames[-1]:
            # Fail closed: never append behind an ambiguous torn frame.
            raise StoreError("incomplete journal tail; recovery required")
        frames.pop()
        for frame in frames:
            try:
                record = json.loads(frame)
                payload = record["payload"]
                encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                digest = hashlib.sha256(bytes.fromhex(self._digest) + encoded).hexdigest()
                if record["digest"] != digest or payload["seq"] != self._sequence + 1:
                    raise ValueError("commit chain mismatch")
                for change in payload["changes"]:
                    namespace, key = change["namespace"], change["key"]
                    if change["op"] == "set":
                        self._state.setdefault(namespace, {})[key] = change["value"]
                    elif change["op"] == "delete":
                        self._state.get(namespace, {}).pop(key, None)
                    else:
                        raise ValueError("invalid journal operation")
                self._sequence = payload["seq"]
                self._digest = digest
            except (ValueError, KeyError, TypeError, UnicodeError) as exc:
                raise StoreError("corrupt committed journal") from exc

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            return deepcopy(self._state.get(namespace, {}).get(key))


    def _commit_changes(self, changes: list[dict[str, Any]]) -> None:
        """Persist one delta record before publishing changes to committed RAM."""
        if self._failed:
            raise StoreError("recovery required")
        if not changes:
            return
        for change in changes:
            if not isinstance(change["namespace"], str) or not isinstance(change["key"], str):
                raise StoreError("namespace and key must be strings")
        payload = {"seq": self._sequence + 1, "changes": changes}
        try:
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            digest = hashlib.sha256(bytes.fromhex(self._digest) + encoded).hexdigest()
            frame = json.dumps(
                {"payload": payload, "digest": digest},
                sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8") + b"\n"
        except (TypeError, ValueError, OverflowError) as exc:
            raise StoreError("unsupported JSON value") from exc

        creating_file = not self.path.exists()
        try:
            with self.path.open("ab", buffering=0) as stream:
                written = stream.write(frame)
                if written != len(frame):
                    raise OSError("short journal write")
                os.fsync(stream.fileno())
            if creating_file:
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            self._failed = True
            raise StoreError("commit failed; recovery required") from exc

        for change in changes:
            namespace, key = change["namespace"], change["key"]
            if change["op"] == "set":
                self._state.setdefault(namespace, {})[key] = change["value"]
            else:
                self._state.get(namespace, {}).pop(key, None)
        self._sequence += 1
        self._digest = digest

    def set(self, namespace: str, key: str, value: Any) -> None:
        with self._lock:
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            self._commit_changes([
                {"op": "set", "namespace": namespace, "key": key, "value": deepcopy(value)}
            ])

    def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            self._commit_changes([{"op": "delete", "namespace": namespace, "key": key}])

    @contextmanager
    def transaction(self) -> Iterator["Transaction"]:
        with self._lock:
            if self._failed:
                raise StoreError("recovery required")
            if self._active_transaction:
                raise StoreError("nested transactions are not supported")
            self._active_transaction = True
            transaction = Transaction(self._state)
            try:
                yield transaction
                self._commit_changes(transaction._pending_changes())
            finally:
                transaction._closed = True
                self._active_transaction = False

    def close(self) -> None:
        if not self._lock_file.closed:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
            self._lock_file.close()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class Transaction:
    """Staged changes overlay committed RAM until one synchronous commit."""

    def __init__(self, committed: dict[str, dict[str, Any]]) -> None:
        self._committed = committed
        self._changes: dict[tuple[str, str], dict[str, Any]] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("transaction is closed")

    def get(self, namespace: str, key: str) -> Any | None:
        self._ensure_open()
        pending = self._changes.get((namespace, key))
        if pending is not None:
            return deepcopy(pending["value"]) if pending["op"] == "set" else None
        return deepcopy(self._committed.get(namespace, {}).get(key))

    def set(self, namespace: str, key: str, value: Any) -> None:
        self._ensure_open()
        self._changes[(namespace, key)] = {
            "op": "set", "namespace": namespace, "key": key, "value": deepcopy(value)
        }

    def delete(self, namespace: str, key: str) -> None:
        self._ensure_open()
        self._changes[(namespace, key)] = {
            "op": "delete", "namespace": namespace, "key": key
        }

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        return [self._changes[key] for key in sorted(self._changes)]
