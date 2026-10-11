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
import math
import os
from pathlib import Path
from threading import RLock, get_ident
from typing import Any, Iterator, Protocol, TypeVar


MAX_JOURNAL_FRAME_BYTES = 1024 * 1024  # 1 MiB including newline

ValueT = TypeVar("ValueT")


class StoreError(RuntimeError):
    """Persistence, integrity, or ownership failure."""


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise StoreError("name must be a nonempty string")


def _validate_value(value: Any) -> None:
    """Reject JSON coercions that would change RAM values after replay."""
    if value is None or type(value) in (str, bool, int):
        return
    if type(value) is float and math.isfinite(value):
        return
    if type(value) is list:
        for item in value:
            _validate_value(item)
    elif type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_value(item)
    else:
        raise StoreError("unsupported JSON value")


def _validate_journal_changes(changes: list[Any]) -> list[tuple[str, str, str, Any]]:
    """Validate every change before any committed state mutation."""
    validated: list[tuple[str, str, str, Any]] = []
    for change in changes:
        if not isinstance(change, dict):
            raise ValueError("invalid journal change")
        namespace, key = change["namespace"], change["key"]
        if not isinstance(namespace, str) or not isinstance(key, str):
            raise ValueError("invalid journal key")
        operation = change["op"]
        if operation == "set":
            value = change["value"]
            _validate_value(value)
            validated.append((operation, namespace, key, value))
        elif operation == "delete":
            validated.append((operation, namespace, key, None))
        else:
            raise ValueError("invalid journal operation")
    return validated


class Store(Protocol[ValueT]):
    """Copy-safe name/value staging with an explicit durable commit."""

    def get(self, name: str) -> ValueT | None:
        """Read a staged value, otherwise committed data; missing names return None."""
        ...

    def set(self, name: str, value: ValueT) -> None:
        """Stage a value without claiming persistence."""
        ...

    def commit(self) -> int:
        """Atomically persist staging and return the last acknowledged sequence."""
        ...


@dataclass(frozen=True)
class DatabaseInfo:
    """Public diagnostic fact: last acknowledged commit, or unknown."""

    last_commit: int | None


class DatabaseInfoContract(Protocol):
    """Expose committed metadata without revealing stored values."""

    def info(self) -> DatabaseInfo:
        """Return the last acknowledged sequence without disk I/O."""
        ...


@dataclass(frozen=True)
class Commit:
    """Confirmed journal position and chained SHA-256 digest."""

    sequence: int
    digest: str


def _open_directory_nofollow(directory: Path) -> int:
    """Pin every directory component; never traverse a symlink."""
    absolute = Path(os.path.abspath(directory))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in absolute.parts[1:]:
            next_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _open_journal_nofollow(directory_fd: int, name: str, flags: int) -> int:
    """Open a regular journal relative to the pinned directory."""
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        import stat

        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StoreError("journal must be a regular file")
        return fd
    except BaseException:
        os.close(fd)
        raise


class _JournalStore:
    """In-process committed RAM state backed by an fsynced append-only log."""

    def __init__(self, path: str | Path) -> None:
        self._pid = os.getpid()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._directory_fd = _open_directory_nofollow(self.path.parent)
            self._journal_name = self.path.name
            lock_fd = _open_journal_nofollow(
                self._directory_fd, self._journal_name + ".lock", os.O_RDWR | os.O_CREAT
            )
        except (OSError, StoreError) as exc:
            if hasattr(self, "_directory_fd"):
                os.close(self._directory_fd)
            raise StoreError("unsafe journal or lock path") from exc
        self._lock = RLock()
        # Keep the lock in a separate stable file: replacing journal files must not
        # silently release ownership of the single-writer role.
        self._lock_file = os.fdopen(lock_fd, "a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            os.close(self._directory_fd)
            raise StoreError("database already has a writer") from exc
        self._journal_fd: int | None = None
        self._state: dict[str, dict[str, Any]] = {}
        self._sequence: int = 0
        self._digest: str = "0" * 64
        self._failed: bool = False
        self._closed: bool = False
        self._active_transaction: bool = False
        try:
            self._replay()
        except BaseException:
            self.close()
            raise

    def _ensure_open(self) -> None:
        if os.getpid() != self._pid:
            raise StoreError("database handle belongs to another process")
        if self._closed:
            raise StoreError("database is closed")

    def info(self) -> DatabaseInfo:
        """Return confirmed in-memory sequence without reading the journal."""
        with self._lock:
            self._ensure_open()
            return DatabaseInfo(last_commit=self._sequence)

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
        directory_fd = _open_directory_nofollow(path.parent)
        try:
            journal_fd = _open_journal_nofollow(directory_fd, path.name, os.O_RDONLY)
        except OSError as exc:
            raise StoreError("unsafe journal path") from exc
        finally:
            os.close(directory_fd)
        with os.fdopen(journal_fd, "rb") as journal:
            while True:
                frame = journal.readline(MAX_JOURNAL_FRAME_BYTES + 1)
                if not frame:
                    break
                if len(frame) > MAX_JOURNAL_FRAME_BYTES:
                    raise StoreError("journal frame exceeds size limit")
                if not frame.endswith(b"\n"):
                    raise StoreError("incomplete journal tail; recovery required")
                state, sequence, digest = _JournalStore._apply_verified_frame(
                    frame, state, sequence, digest
                )
        return state, sequence, digest

    @staticmethod
    def _apply_verified_frame(
        frame: bytes,
        state: dict[str, dict[str, Any]],
        sequence: int,
        digest: str,
    ) -> tuple[dict[str, dict[str, Any]], int, str]:
        """Validate a journal frame before applying its ordered changes."""
        try:
            record = json.loads(frame)
            if not isinstance(record, dict):
                raise ValueError("invalid journal frame")
            payload = record["payload"]
            if (
                not isinstance(payload, dict)
                or type(payload.get("format")) is not int
                or payload["format"] != 1
                or not isinstance(payload.get("changes"), list)
            ):
                raise ValueError("invalid journal payload")
            encoded = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            # Chain each frame to the previous confirmed digest so replay detects
            # reordered, missing, or modified records (not malicious rewrites).
            next_digest = hashlib.sha256(bytes.fromhex(digest) + encoded).hexdigest()
            if (
                type(payload["seq"]) is not int
                or record["digest"] != next_digest
                or payload["seq"] != sequence + 1
            ):
                raise ValueError("commit chain mismatch")
            # Validate the entire frame before mutating caller-owned state.
            # A malformed later operation must not publish earlier changes.
            validated = _validate_journal_changes(payload["changes"])
            for operation, namespace, key, value in validated:
                if operation == "set":
                    state.setdefault(namespace, {})[key] = value
                else:
                    state.get(namespace, {}).pop(key, None)
            return state, payload["seq"], next_digest
        except (ValueError, KeyError, TypeError, UnicodeError, AttributeError, StoreError) as exc:
            raise StoreError("corrupt committed journal") from exc

    def _replay(self) -> None:
        self._state, self._sequence, self._digest = self._inspect_log(self.path)

    def get(self, namespace: str, key: str) -> Any | None:
        """Return a deep copy of the last confirmed value from RAM."""
        with self._lock:
            self._ensure_open()
            return deepcopy(self._state.get(namespace, {}).get(key))

    def _encode_frame(self, changes: list[dict[str, Any]]) -> tuple[bytes, str]:
        for change in changes:
            if not isinstance(change["namespace"], str) or not isinstance(change["key"], str):
                raise StoreError("namespace and key must be strings")
            if change["op"] == "set":
                _validate_value(change["value"])
        payload = {"format": 1, "seq": self._sequence + 1, "changes": changes}
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
            if len(frame) > MAX_JOURNAL_FRAME_BYTES:
                raise StoreError("journal frame exceeds size limit")
            return frame, digest
        except (TypeError, ValueError, OverflowError) as exc:
            raise StoreError("unsupported JSON value") from exc

    def _append_frame(self, frame: bytes) -> None:
        try:
            if self._journal_fd is None:
                self._journal_fd = _open_journal_nofollow(
                    self._directory_fd,
                    self._journal_name,
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT,
                )
            fd = self._journal_fd
            # Pin the inode for this writer. A replaced pathname must never
            # redirect appends to a different regular file.
            path_stat = os.stat(
                self._journal_name, dir_fd=self._directory_fd, follow_symlinks=False
            )
            opened_stat = os.fstat(fd)
            if (path_stat.st_dev, path_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
                raise StoreError("journal path replaced; recovery required")
            if os.write(fd, frame) != len(frame):
                raise OSError("short journal write")
            os.fsync(fd)
            path_stat = os.stat(
                self._journal_name, dir_fd=self._directory_fd, follow_symlinks=False
            )
            if (path_stat.st_dev, path_stat.st_ino) != (opened_stat.st_dev, opened_stat.st_ino):
                raise StoreError("journal path replaced; recovery required")
            os.fsync(self._directory_fd)
        except (OSError, StoreError) as exc:
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
        # Publish only confirmed changes to the RAM view seen by readers.
        self._apply_changes(changes)
        self._sequence += 1
        self._digest = digest

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Synchronous durable write; reject direct writes inside a transaction."""
        with self._lock:
            self._ensure_open()
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            self._commit_changes(
                [{"op": "set", "namespace": namespace, "key": key, "value": deepcopy(value)}]
            )

    def delete(self, namespace: str, key: str) -> None:
        """Synchronously record a deletion, including of an absent key."""
        with self._lock:
            self._ensure_open()
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            self._commit_changes([{"op": "delete", "namespace": namespace, "key": key}])

    @contextmanager
    def transaction(self) -> Iterator["_JournalTransaction"]:
        """Commit staged changes together on success; discard on exception."""
        with self._lock:
            self._ensure_open()
            if self._failed:
                raise StoreError("recovery required")
            if self._active_transaction:
                raise StoreError("nested transactions are not supported")
            self._active_transaction = True
            # Transaction writes stay in a private overlay; a successful exit
            # appends one record, while an exception discards staged changes.
            transaction = _JournalTransaction(self._state)
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
            # Build and fsync the copy before publishing the destination path.
            # The authoritative source remains unchanged on copy failure.
            with os.fdopen(fd, "wb") as output:
                if source.exists():
                    with source.open("rb") as existing:
                        shutil.copyfileobj(existing, output)
                output.flush()
                os.fsync(output.fileno())
            actual_state, sequence, digest = _JournalStore._inspect_log(temporary)
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
        """Release the exclusive writer lock when no transaction is active."""
        with self._lock:
            if os.getpid() != self._pid:
                raise StoreError("database handle belongs to another process")
            if self._active_transaction:
                raise StoreError("cannot close during active transaction")
            if not self._closed:
                if self._journal_fd is not None:
                    os.close(self._journal_fd)
                    self._journal_fd = None
                if not self._lock_file.closed:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                    self._lock_file.close()
                os.close(self._directory_fd)
                self._closed = True

    def __enter__(self) -> "_JournalStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class MemoryStore:
    """Client-owned name/value facade over the durable journal engine.

    Staging is owned by this client instance. Use transaction() for concurrent
    read-modify-write operations. The journal format is private to the library.
    """

    _NAMESPACE = "values"

    def __init__(self, path: str | Path) -> None:
        self._engine = _JournalStore(path)
        self._pending: dict[str, Any] = {}

    @property
    def last_commit(self) -> Commit:
        """Return the last acknowledged durable journal position."""
        return self._engine.last_commit

    def info(self) -> DatabaseInfo:
        """Expose acknowledged sequence only, without values or filesystem access."""
        return self._engine.info()

    def get(self, name: str) -> Any | None:
        """Read staged data first; otherwise return a copy of committed data."""
        _validate_name(name)
        with self._engine._lock:
            self._engine._ensure_open()
            if name in self._pending:
                return deepcopy(self._pending[name])
            return self._engine.get(self._NAMESPACE, name)

    def set(self, name: str, value: Any) -> None:
        """Stage a copy without acknowledging a durable write."""
        with self._engine._lock:
            self._engine._ensure_open()
            if self._engine._failed:
                raise StoreError("recovery required")
            if self._engine._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            _validate_name(name)
            self._pending[name] = deepcopy(value)

    def commit(self) -> int:
        """Atomically persist staged names and return the confirmed sequence."""
        with self._engine._lock:
            self._engine._ensure_open()
            if self._engine._failed:
                raise StoreError("recovery required")
            if self._engine._active_transaction:
                raise StoreError("cannot commit during transaction")
            changes = [
                {"op": "set", "namespace": self._NAMESPACE, "key": name, "value": value}
                for name, value in sorted(self._pending.items())
            ]
            self._engine._commit_changes(changes)
            self._pending.clear()
            return self._engine.last_commit.sequence

    @contextmanager
    def transaction(self) -> Iterator["Transaction"]:
        """Serialize an atomic read-modify-write operation."""
        with self._engine._lock:
            if self._pending:
                raise StoreError("transaction requires an idle store")
            with self._engine.transaction() as tx:
                wrapper = Transaction(self._engine._state.get(self._NAMESPACE, {}))
                try:
                    yield wrapper
                    for change in wrapper._pending_changes():
                        tx.set(change["namespace"], change["key"], change["value"])
                finally:
                    wrapper._closed = True

    def backup(self, destination: str | Path) -> None:
        """Back up only confirmed journal state."""
        self._engine.backup(destination)

    def restore(self, source: str | Path) -> None:
        """Restore a verified backup into an empty store."""
        with self._engine._lock:
            if self._pending:
                raise StoreError("restore requires no staged values")
            self._engine.restore(source)

    def close(self) -> None:
        """Discard staging and release the single-writer lock."""
        with self._engine._lock:
            self._engine.close()
            self._pending.clear()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class Transaction:
    """Private overlay for one atomic name/value transaction."""

    def __init__(self, committed: dict[str, Any]) -> None:
        self._committed = committed
        self._owner = get_ident()
        self._pending: dict[str, Any] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("transaction is closed")
        if self._owner != get_ident():
            raise StoreError("transaction belongs to another thread")

    def get(self, name: str) -> Any | None:
        self._ensure_open()
        _validate_name(name)
        return deepcopy(self._pending[name] if name in self._pending else self._committed.get(name))

    def set(self, name: str, value: Any) -> None:
        self._ensure_open()
        _validate_name(name)
        self._pending[name] = deepcopy(value)

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        return [
            {"op": "set", "namespace": "values", "key": name, "value": value}
            for name, value in sorted(self._pending.items())
        ]


class _JournalTransaction:
    """Staged changes overlay committed RAM until one synchronous commit."""

    def __init__(self, committed: dict[str, dict[str, Any]]) -> None:
        self._committed = committed
        self._owner = get_ident()
        self._changes: dict[tuple[str, str], dict[str, Any]] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("transaction is closed")
        if self._owner != get_ident():
            raise StoreError("transaction belongs to another thread")

    def get(self, namespace: str, key: str) -> Any | None:
        """Read a staged value, falling back to committed state."""
        self._ensure_open()
        pending = self._changes.get((namespace, key))
        if pending is not None:
            return deepcopy(pending["value"]) if pending["op"] == "set" else None
        return deepcopy(self._committed.get(namespace, {}).get(key))

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Stage a copy of the value without publishing it."""
        self._ensure_open()
        self._changes[(namespace, key)] = {
            "op": "set",
            "namespace": namespace,
            "key": key,
            "value": deepcopy(value),
        }

    def delete(self, namespace: str, key: str) -> None:
        """Stage a key deletion without changing committed RAM state."""
        self._ensure_open()
        self._changes[(namespace, key)] = {"op": "delete", "namespace": namespace, "key": key}

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        return [self._changes[key] for key in sorted(self._changes)]
