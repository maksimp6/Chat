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
        self._closed = False
        self._active_transaction = False
        try:
            self._replay()
        except BaseException:
            self.close()
            raise

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("database is closed")

    @property
    def last_commit(self) -> Commit:
        with self._lock:
            self._ensure_open()
            return Commit(self._sequence, self._digest)

    @staticmethod
    def _inspect_log(path: Path) -> tuple[dict[str, dict[str, Any]], int, str]:
        """Verify journal integrity without changing the live instance."""
        state: dict[str, dict[str, Any]] = {}
        sequence = 0
        digest = "0" * 64
        if not path.exists():
            return state, sequence, digest
        raw = path.read_bytes()
        frames = raw.split(b"\n")
        if frames[-1]:
            raise StoreError("incomplete journal tail; recovery required")
        frames.pop()
        for frame in frames:
            try:
                record = json.loads(frame)
                if not isinstance(record, dict):
                    raise ValueError("invalid journal frame")
                payload = record["payload"]
                if not isinstance(payload, dict) or not isinstance(payload.get("changes"), list):
                    raise ValueError("invalid journal payload")
                encoded = json.dumps(
                    payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
                next_digest = hashlib.sha256(bytes.fromhex(digest) + encoded).hexdigest()
                if record["digest"] != next_digest or payload["seq"] != sequence + 1:
                    raise ValueError("commit chain mismatch")
                for change in payload["changes"]:
                    if not isinstance(change, dict):
                        raise ValueError("invalid journal change")
                    namespace, key = change["namespace"], change["key"]
                    if not isinstance(namespace, str) or not isinstance(key, str):
                        raise ValueError("invalid journal key")
                    if change["op"] == "set":
                        state.setdefault(namespace, {})[key] = change["value"]
                    elif change["op"] == "delete":
                        state.get(namespace, {}).pop(key, None)
                    else:
                        raise ValueError("invalid journal operation")
                sequence = payload["seq"]
                digest = next_digest
            except (ValueError, KeyError, TypeError, UnicodeError, AttributeError) as exc:
                raise StoreError("corrupt committed journal") from exc
        return state, sequence, digest

    def _replay(self) -> None:
        self._state, self._sequence, self._digest = self._inspect_log(self.path)

    def get(self, namespace: str, key: str) -> Any | None:
        with self._lock:
            self._ensure_open()
            return deepcopy(self._state.get(namespace, {}).get(key))

    def _encode_frame(self, changes: list[dict[str, Any]]) -> tuple[bytes, str]:
        for change in changes:
            if not isinstance(change["namespace"], str) or not isinstance(change["key"], str):
                raise StoreError("namespace and key must be strings")
        payload = {"seq": self._sequence + 1, "changes": changes}
        try:
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            digest = hashlib.sha256(bytes.fromhex(self._digest) + encoded).hexdigest()
            frame = (
                json.dumps(
                    {"payload": payload, "digest": digest},
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
                + b"\n"
            )
            return frame, digest
        except (TypeError, ValueError, OverflowError) as exc:
            raise StoreError("unsupported JSON value") from exc

    def _append_frame(self, frame: bytes) -> None:
        first_write = not self.path.exists()
        try:
            with self.path.open("ab", buffering=0) as stream:
                if stream.write(frame) != len(frame):
                    raise OSError("short journal write")
                os.fsync(stream.fileno())
            if first_write:
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            self._failed = True
            raise StoreError("commit failed; recovery required") from exc

    def _apply_changes(self, changes: list[dict[str, Any]]) -> None:
        for change in changes:
            namespace, key = change["namespace"], change["key"]
            if change["op"] == "set":
                self._state.setdefault(namespace, {})[key] = change["value"]
            else:
                self._state.get(namespace, {}).pop(key, None)

    def _commit_changes(self, changes: list[dict[str, Any]]) -> None:
        """Publish a transaction only after its delta journal is durable."""
        self._ensure_open()
        if self._failed:
            raise StoreError("recovery required")
        if not changes:
            return
        frame, digest = self._encode_frame(changes)
        self._append_frame(frame)
        self._apply_changes(changes)
        self._sequence += 1
        self._digest = digest

    def set(self, namespace: str, key: str, value: Any) -> None:
        with self._lock:
            self._ensure_open()
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            self._commit_changes(
                [{"op": "set", "namespace": namespace, "key": key, "value": deepcopy(value)}]
            )

    def delete(self, namespace: str, key: str) -> None:
        with self._lock:
            self._ensure_open()
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            self._commit_changes([{"op": "delete", "namespace": namespace, "key": key}])

    @contextmanager
    def transaction(self) -> Iterator["Transaction"]:
        with self._lock:
            self._ensure_open()
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

    @staticmethod
    def _copy_verified_file(
        source: Path,
        destination: Path,
        expected_state: dict[str, dict[str, Any]],
        expected_commit: Commit,
        *,
        destination_must_be_new: bool = False,
    ) -> None:
        """Copy to a same-directory temp file, validate, then atomically publish."""
        import shutil
        import tempfile

        destination.parent.mkdir(parents=True, exist_ok=True)
        fd, temp_name = tempfile.mkstemp(prefix=".alice-copy-", dir=destination.parent)
        temporary = Path(temp_name)
        try:
            with os.fdopen(fd, "wb") as output:
                if source.exists():
                    with source.open("rb") as existing:
                        shutil.copyfileobj(existing, output)
                output.flush()
                os.fsync(output.fileno())
            actual_state, sequence, digest = MemoryStore._inspect_log(temporary)
            if actual_state != expected_state or Commit(sequence, digest) != expected_commit:
                raise StoreError("backup integrity mismatch")
            try:
                if destination_must_be_new:
                    os.link(temporary, destination)
                else:
                    os.replace(temporary, destination)
            except FileExistsError as exc:
                raise StoreError("backup destination was created concurrently") from exc
            directory = os.open(str(destination.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def backup(self, destination: str | Path) -> None:
        """Create a verified, fsynced backup without overwriting another artifact."""
        with self._lock:
            self._ensure_open()
            if self._failed or self._active_transaction:
                raise StoreError("backup requires a healthy committed state")
            target = Path(destination)
            if target.resolve() == self.path.resolve() or target.exists():
                raise StoreError("backup destination must be new")
            self._copy_verified_file(
                self.path,
                target,
                self._state,
                self.last_commit,
                destination_must_be_new=True,
            )

    def restore(self, source: str | Path) -> None:
        """Restore a verified backup into a brand-new or empty store only."""
        with self._lock:
            self._ensure_open()
            if self._failed or self._active_transaction:
                raise StoreError("restore requires a healthy idle store")
            if self._sequence or self._state or (self.path.exists() and self.path.stat().st_size):
                raise StoreError("restore destination is not empty")
            origin = Path(source)
            if origin.resolve() == self.path.resolve():
                raise StoreError("cannot restore from the live database path")
            state, sequence, digest = self._inspect_log(origin)
            if not origin.exists():
                raise StoreError("backup source does not exist")
            try:
                self._copy_verified_file(origin, self.path, state, Commit(sequence, digest))
            except OSError as exc:
                self._failed = True
                raise StoreError("restore failed; recovery required") from exc
            self._state = state
            self._sequence = sequence
            self._digest = digest

    def close(self) -> None:
        with self._lock:
            if self._active_transaction:
                raise StoreError("cannot close during active transaction")
            if not self._closed:
                if not self._lock_file.closed:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                    self._lock_file.close()
                self._closed = True

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
            "op": "set",
            "namespace": namespace,
            "key": key,
            "value": deepcopy(value),
        }

    def delete(self, namespace: str, key: str) -> None:
        self._ensure_open()
        self._changes[(namespace, key)] = {"op": "delete", "namespace": namespace, "key": key}

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        return [self._changes[key] for key in sorted(self._changes)]
