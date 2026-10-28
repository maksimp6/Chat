"""Minimal synchronous typed key/value Memory DB.

One process owns a store. File locking prevents concurrent process writers.
No SQL, background flush, or unsafe pickle deserialization.
"""

from __future__ import annotations
from core.env import env_get

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
import fcntl
import hashlib
import json
import os
from pathlib import Path
from threading import RLock, get_ident
from typing import Any, Iterator, Protocol, TypeVar

from kristall.value_validation import (
    walk_value,
    validate_json_values,
    validate_journal_changes,
)


MAX_JOURNAL_FRAME_BYTES = 1024 * 1024  # 1 MiB including newline
MAX_VALUE_DEPTH = 64
MAX_VALUE_NODES = 100_000
MAX_VALUE_TEXT_BYTES = MAX_JOURNAL_FRAME_BYTES

ValueT = TypeVar("ValueT")


class StoreError(RuntimeError):
    """Persistence, integrity, or ownership failure."""


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise StoreError("name must be a nonempty string")


def _walk_value(value: Any) -> Iterator[Any]:
    return walk_value(
        value,
        max_depth=MAX_VALUE_DEPTH,
        max_nodes=MAX_VALUE_NODES,
        max_text_bytes=MAX_VALUE_TEXT_BYTES,
        error_type=StoreError,
        limit_error_type=StoreError,
    )


def _validate_value_shape(value: Any) -> None:
    """Reject unsafe container graphs before copying into client staging."""
    for _item in _walk_value(value):
        pass


def _validate_value(value: Any) -> None:
    validate_json_values(_walk_value(value), StoreError)


def _validate_journal_changes(changes: list[Any]) -> list[tuple[str, str, str, Any]]:
    return validate_journal_changes(changes, _validate_value)


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


def _trusted_mount_id(fd: int) -> int:
    """Read the kernel mount identity; reject unverifiable Linux mounts."""
    try:
        with open(f"/proc/self/fdinfo/{fd}", encoding="ascii") as info:
            for line in info:
                if line.startswith("mnt_id:"):
                    return int(line.split(":", 1)[1].strip())
    except (OSError, ValueError) as exc:
        raise StoreError("unverifiable Memory DB mount boundary") from exc
    raise StoreError("unverifiable Memory DB mount boundary")


def _reject_extended_acl(fd: int) -> None:
    """Fail closed on POSIX access/default ACL xattrs, including inherited ACLs."""
    import errno

    for name in ("system.posix_acl_access", "system.posix_acl_default"):
        try:
            os.getxattr(fd, name)
        except OSError as exc:
            if exc.errno in (errno.ENODATA,):
                continue
            raise StoreError("unverifiable Memory DB ACL") from exc
        raise StoreError("untrusted Memory DB ACL")


def _open_directory_nofollow(directory: Path, *, trusted: bool | None = None) -> int:
    """Pin every directory component; never traverse a symlink."""
    absolute = Path(os.path.abspath(directory))
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    if trusted is None:
        trusted = env_get("LOGOS_MEMORY_TRUSTED_ROOT") == "1"
    try:
        if trusted:
            _verify_trusted_ancestor(fd)
            previous_mount = _trusted_mount_id(fd)
        for part in absolute.parts[1:]:
            next_fd = os.open(
                part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd
            )
            try:
                if trusted:
                    _verify_trusted_ancestor(next_fd)
                    current_mount = _trusted_mount_id(next_fd)
                    if current_mount != previous_mount:
                        raise StoreError("untrusted Memory DB mount boundary")
                    previous_mount = current_mount
            except BaseException:
                os.close(next_fd)
                raise
            os.close(fd)
            fd = next_fd
        return fd
    except BaseException:
        os.close(fd)
        raise


def _verify_trusted_ancestor(fd: int) -> None:
    """Reject writable ancestors except root-owned sticky shared directories."""
    import stat

    metadata = os.fstat(fd)
    mode = metadata.st_mode
    _reject_extended_acl(fd)
    if not stat.S_ISDIR(mode):
        raise StoreError("untrusted Memory DB ancestor")
    # Only root and the service identity may own path components.
    # Root-owned sticky directories (e.g. /tmp) cannot be renamed
    # by an unrelated UID, but do not isolate a same-UID attacker.
    if metadata.st_uid not in (0, os.geteuid()):
        raise StoreError("untrusted Memory DB ancestor owner")
    if mode & 0o022 and not (metadata.st_uid == 0 and mode & stat.S_ISVTX):
        raise StoreError("untrusted Memory DB ancestor permissions")


def _verify_trusted_root(directory_fd: int) -> None:
    """Fail closed unless the pinned root belongs exclusively to this process."""
    import stat

    metadata = os.fstat(directory_fd)
    _reject_extended_acl(directory_fd)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
        or stat.S_IMODE(metadata.st_mode) != 0o700
    ):
        raise StoreError("untrusted Memory DB directory ownership or permissions")


def _verify_private_file(fd: int) -> None:
    """Reject existing journals and lock files with unsafe DAC permissions."""
    import stat

    metadata = os.fstat(fd)
    _reject_extended_acl(fd)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != os.geteuid()
        or metadata.st_gid != os.getegid()
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise StoreError("untrusted Memory DB file ownership or permissions")


def _open_journal_nofollow(
    directory_fd: int, name: str, flags: int, *, trusted: bool | None = None
) -> int:
    """Open a regular journal relative to the pinned directory."""
    fd = os.open(name, flags | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=directory_fd)
    try:
        import stat

        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise StoreError("journal must be a regular file")
        if trusted is None:
            trusted = env_get("LOGOS_MEMORY_TRUSTED_ROOT") == "1"
        if trusted:
            _verify_private_file(fd)
        return fd
    except BaseException:
        os.close(fd)
        raise


class _JournalStore:
    """In-process committed RAM state backed by an fsynced append-only log."""

    def __setattr__(self, name: str, value: Any) -> None:
        # Prevent accidental policy downgrade after construction. This is not
        # isolation against arbitrary code running inside the service process.
        if name == "_trusted" and "_trusted" in self.__dict__:
            raise StoreError("trusted policy is immutable")
        object.__setattr__(self, name, value)

    def __init__(self, path: str | Path, *, trusted: bool | None = None) -> None:
        self._trusted = (
            (env_get("LOGOS_MEMORY_TRUSTED_ROOT") == "1")
            if trusted is None
            else trusted
        )
        self._pid = os.getpid()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self._directory_fd = _open_directory_nofollow(
                self.path.parent, trusted=self._trusted
            )
            if self._trusted:
                _verify_trusted_root(self._directory_fd)
            self._journal_name = self.path.name
            lock_fd = _open_journal_nofollow(
                self._directory_fd,
                self._journal_name + ".lock",
                os.O_RDWR | os.O_CREAT,
                trusted=self._trusted,
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
        self._ram_valid: bool = True
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
            return DatabaseInfo(last_commit=self._sequence if self._ram_valid else None)

    @property
    def last_commit(self) -> Commit:
        with self._lock:
            self._ensure_open()
            if not self._ram_valid:
                raise StoreError("recovery required; RAM publication was interrupted")
            return Commit(self._sequence, self._digest)

    @staticmethod
    def _inspect_log(
        path: Path, *, trusted: bool | None = None
    ) -> tuple[dict[str, dict[str, Any]], int, str]:
        """Verify journal integrity without changing the live instance."""
        state: dict[str, dict[str, Any]] = {}
        sequence = 0
        digest = "0" * 64
        if not path.exists():
            return state, sequence, digest
        directory_fd = _open_directory_nofollow(path.parent, trusted=trusted)
        try:
            journal_fd = _open_journal_nofollow(
                directory_fd, path.name, os.O_RDONLY, trusted=trusted
            )
        except OSError as exc:
            raise StoreError("unsafe journal path") from exc
        finally:
            os.close(directory_fd)
        with os.fdopen(journal_fd, "rb") as journal:
            return _JournalStore._inspect_stream(journal)

    @staticmethod
    def _inspect_stream(journal: Any) -> tuple[dict[str, dict[str, Any]], int, str]:
        state: dict[str, dict[str, Any]] = {}
        sequence = 0
        digest = "0" * 64
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
        except (
            ValueError,
            KeyError,
            TypeError,
            UnicodeError,
            AttributeError,
            StoreError,
            RecursionError,
        ) as exc:
            raise StoreError("corrupt committed journal") from exc

    def _replay(self) -> None:
        self._state, self._sequence, self._digest = self._inspect_log(
            self.path, trusted=self._trusted
        )

    def get(self, namespace: str, key: str) -> Any | None:
        """Return a deep copy of the last confirmed value from RAM."""
        with self._lock:
            self._ensure_open()
            if not self._ram_valid:
                raise StoreError("recovery required; RAM publication was interrupted")
            return deepcopy(self._state.get(namespace, {}).get(key))

    def _encode_frame(self, changes: list[dict[str, Any]]) -> tuple[bytes, str]:
        for change in changes:
            if not isinstance(change["namespace"], str) or not isinstance(
                change["key"], str
            ):
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
            if (path_stat.st_dev, path_stat.st_ino) != (
                opened_stat.st_dev,
                opened_stat.st_ino,
            ):
                raise StoreError("journal path replaced; recovery required")
            if os.write(fd, frame) != len(frame):
                raise OSError("short journal write")
            os.fsync(fd)
            path_stat = os.stat(
                self._journal_name, dir_fd=self._directory_fd, follow_symlinks=False
            )
            if (path_stat.st_dev, path_stat.st_ino) != (
                opened_stat.st_dev,
                opened_stat.st_ino,
            ):
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
        try:
            self._append_frame(frame)
            # Publish only confirmed changes to the RAM view seen by readers.
            self._ram_valid = False
            self._apply_changes(changes)
            self._sequence += 1
            self._digest = digest
            self._ram_valid = True
        except BaseException:
            # A complete frame may exist even when append or RAM publication
            # was interrupted. Retrying from stale metadata would break replay.
            self._failed = True
            raise

    def set(self, namespace: str, key: str, value: Any) -> None:
        """Synchronous durable write; reject direct writes inside a transaction."""
        with self._lock:
            self._ensure_open()
            if self._active_transaction:
                raise StoreError("direct mutation during transaction is forbidden")
            _validate_value_shape(value)
            self._commit_changes(
                [
                    {
                        "op": "set",
                        "namespace": namespace,
                        "key": key,
                        "value": deepcopy(value),
                    }
                ]
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
        trusted: bool | None = None,
    ) -> None:
        """Copy from a no-follow source fd into a pinned destination directory."""
        import shutil

        destination.parent.mkdir(parents=True, exist_ok=True)
        source_dir = _open_directory_nofollow(source.parent, trusted=trusted)
        target_dir = _open_directory_nofollow(destination.parent, trusted=trusted)
        temp_name = None
        try:
            source_fd = _open_journal_nofollow(
                source_dir, source.name, os.O_RDONLY, trusted=trusted
            )
            temp_name = ".logos-copy-" + os.urandom(16).hex()
            temp_fd = _open_journal_nofollow(
                target_dir,
                temp_name,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL,
                trusted=trusted,
            )
            with os.fdopen(source_fd, "rb") as existing:
                with os.fdopen(temp_fd, "wb") as output:
                    shutil.copyfileobj(existing, output)
                    output.flush()
                    os.fsync(output.fileno())
            verify_fd = _open_journal_nofollow(
                target_dir, temp_name, os.O_RDONLY, trusted=trusted
            )
            with os.fdopen(verify_fd, "rb") as verified:
                actual_state, sequence, digest = _JournalStore._inspect_stream(verified)
                if (
                    actual_state != expected_state
                    or Commit(sequence, digest) != expected_commit
                ):
                    raise StoreError("backup integrity mismatch")
                # Linux procfs names the already-verified open inode, not the
                # attacker-replaceable temporary basename. Fail closed if procfs
                # is unavailable; do not fall back to linking temp_name.
                os.link(
                    f"/proc/self/fd/{verified.fileno()}",
                    destination.name,
                    dst_dir_fd=target_dir,
                    follow_symlinks=True,
                )
                published = os.stat(
                    destination.name, dir_fd=target_dir, follow_symlinks=False
                )
                pinned = os.fstat(verified.fileno())
                if (published.st_dev, published.st_ino) != (
                    pinned.st_dev,
                    pinned.st_ino,
                ):
                    raise StoreError("published backup inode mismatch")
                os.fsync(target_dir)
        except FileExistsError as exc:
            raise StoreError("destination was created concurrently") from exc
        finally:
            if temp_name is not None:
                try:
                    os.unlink(temp_name, dir_fd=target_dir)
                except FileNotFoundError:
                    pass
            os.close(source_dir)
            os.close(target_dir)

    def backup(self, destination: str | Path) -> None:
        """Back up confirmed state; reject alias and symlink destination paths."""
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
                trusted=self._trusted,
            )

    def restore(self, source: str | Path) -> None:
        """Restore a verified backup into an empty store."""
        with self._lock:
            self._ensure_open()
            if self._failed or self._active_transaction:
                raise StoreError("restore requires a healthy idle store")
            if (
                self._sequence
                or self._state
                or (self.path.exists() and self.path.stat().st_size)
            ):
                raise StoreError("restore destination is not empty")
            origin = Path(source)
            if origin.resolve() == self.path.resolve():
                raise StoreError("cannot restore from the live database path")
            if not origin.exists():
                raise StoreError("backup source does not exist")
            state, sequence, digest = self._inspect_log(origin, trusted=self._trusted)
            try:
                self._copy_verified_file(
                    origin,
                    self.path,
                    state,
                    Commit(sequence, digest),
                    trusted=self._trusted,
                )
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

    def __init__(self, path: str | Path, *, trusted: bool | None = None) -> None:
        self._engine = _JournalStore(path, trusted=trusted)
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
            _validate_value_shape(value)
            # Full JSON validation at staging time: a value that cannot be committed
            # must never sit in _pending, where it would fail every later commit.
            _validate_value(value)
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
        return deepcopy(
            self._pending[name] if name in self._pending else self._committed.get(name)
        )

    def set(self, name: str, value: Any) -> None:
        self._ensure_open()
        _validate_name(name)
        _validate_value_shape(value)
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
        _validate_value_shape(value)
        self._changes[(namespace, key)] = {
            "op": "set",
            "namespace": namespace,
            "key": key,
            "value": deepcopy(value),
        }

    def delete(self, namespace: str, key: str) -> None:
        """Stage a key deletion without changing committed RAM state."""
        self._ensure_open()
        self._changes[(namespace, key)] = {
            "op": "delete",
            "namespace": namespace,
            "key": key,
        }

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        return [self._changes[key] for key in sorted(self._changes)]
