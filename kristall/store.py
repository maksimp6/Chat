"""Minimal synchronous typed key/value Kristall.

One process owns a store. File locking prevents concurrent process writers.
No SQL, background flush, or unsafe pickle deserialization.
"""

from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from itertools import chain
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
from threading import RLock, get_ident
from typing import Any, Iterator, Protocol, TypeVar


MAX_JOURNAL_FRAME_BYTES = 1024 * 1024  # 1 MiB including newline
MAX_VALUE_DEPTH = 64
MAX_VALUE_NODES = 100_000
MAX_VALUE_TEXT_BYTES = MAX_JOURNAL_FRAME_BYTES

ValueT = TypeVar("ValueT")


class KristallError(RuntimeError):
    """Persistence, integrity, or ownership failure."""


def _validate_name(name: str) -> None:
    if not isinstance(name, str) or not name:
        raise KristallError("name must be a nonempty string")


def _check_text_budget(value: Any, used: int) -> int:
    """Check UTF-8 incrementally without allocating a full encoded text copy."""
    if type(value) is not str:
        return used
    for offset in range(0, len(value), 16384):
        try:
            used += len(value[offset : offset + 16384].encode("utf-8"))
        except UnicodeEncodeError as exc:
            raise KristallError("invalid Unicode text") from exc
        if used > MAX_VALUE_TEXT_BYTES:
            raise KristallError("value text exceeds size limit")
    return used


def _walk_value(value: Any) -> Iterator[Any]:
    """Bound traversal before recursive copying/encoding; allow shared subtrees."""
    stack: list[tuple[Iterator[Any], int, int | None]] = [(iter((value,)), 0, None)]
    active: set[int] = set()
    nodes = 0
    text_bytes = 0
    while stack:
        children, depth, identity = stack[-1]
        try:
            item = next(children)
        except StopIteration:
            stack.pop()
            if identity is not None:
                active.remove(identity)
            continue
        nodes += 1
        if depth > MAX_VALUE_DEPTH or nodes > MAX_VALUE_NODES:
            raise KristallError("value structure exceeds limit")
        text_bytes = _check_text_budget(item, text_bytes)
        yield item
        if type(item) in (list, tuple, dict):
            identity = id(item)
            if identity in active:
                raise KristallError("cyclic value structure")
            active.add(identity)
            descendants = chain(item.keys(), item.values()) if type(item) is dict else iter(item)
            stack.append((descendants, depth + 1, identity))


def _validate_value_shape(value: Any) -> None:
    """Reject unsafe container graphs before copying into client staging."""
    for _item in _walk_value(value):
        pass


def _validate_value(value: Any) -> None:
    """Reject JSON coercions that would change RAM values after replay."""
    for item in _walk_value(value):
        if item is None or type(item) in (str, bool, int, list):
            continue
        if type(item) is float and math.isfinite(item):
            continue
        if type(item) is dict and all(type(key) is str for key in item):
            continue
        raise KristallError("unsupported JSON value")


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


def _validate_checkpoint(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Validate the complete checkpoint before publishing recovered state."""
    if payload["seq"] < 1 or payload["changes"] != []:
        raise ValueError("invalid checkpoint baseline")
    snapshot = payload.get("state")
    if not isinstance(snapshot, dict):
        raise ValueError("invalid checkpoint state")
    checked: dict[str, dict[str, Any]] = {}
    for namespace, values in snapshot.items():
        if not isinstance(namespace, str) or not isinstance(values, dict):
            raise ValueError("invalid checkpoint namespace")
        checked[namespace] = {}
        for key, value in values.items():
            if not isinstance(key, str):
                raise ValueError("invalid checkpoint key")
            _validate_value(value)
            checked[namespace][key] = value
    return checked


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


class _JournalStore:
    """In-process committed RAM state backed by an fsynced append-only log."""

    def __init__(self, path: str | Path) -> None:
        self._pid = os.getpid()
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = RLock()
        # Keep the lock in a separate stable file: replacing journal files must not
        # silently release ownership of the single-writer role.
        self._lock_file = self.path.with_name(self.path.name + ".lock").open("a+b")
        try:
            fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock_file.close()
            raise KristallError("database already has a writer") from exc
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
            raise KristallError("database handle belongs to another process")
        if self._closed:
            raise KristallError("database is closed")

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
                raise KristallError("recovery required; RAM publication was interrupted")
            return Commit(self._sequence, self._digest)

    @staticmethod
    def _inspect_log(path: Path) -> tuple[dict[str, dict[str, Any]], int, str]:
        """Verify journal integrity without changing the live instance."""
        state: dict[str, dict[str, Any]] = {}
        sequence = 0
        digest = "0" * 64
        if not path.exists():
            return state, sequence, digest
        with path.open("rb") as journal:
            while True:
                frame = journal.readline(MAX_JOURNAL_FRAME_BYTES + 1)
                if not frame:
                    break
                if len(frame) > MAX_JOURNAL_FRAME_BYTES:
                    raise KristallError("journal frame exceeds size limit")
                if not frame.endswith(b"\n"):
                    raise KristallError("incomplete journal tail; recovery required")
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
                or (
                    payload["seq"] != sequence + 1
                    and not (sequence == 0 and payload.get("checkpoint") is True)
                )
            ):
                raise ValueError("commit chain mismatch")
            # A checkpoint is the first and only baseline frame after compaction.
            # Keep the original sequence monotonic across compactions.
            if payload.get("checkpoint") is True:
                if sequence != 0 or state:
                    raise ValueError("checkpoint must be first")
                return _validate_checkpoint(payload), payload["seq"], next_digest
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
            KristallError,
            RecursionError,
        ) as exc:
            raise KristallError("corrupt committed journal") from exc

    def _replay(self) -> None:
        self._state, self._sequence, self._digest = self._inspect_log(self.path)

    def get(self, namespace: str, key: str) -> Any | None:
        """Return a deep copy of the last confirmed value from RAM."""
        with self._lock:
            self._ensure_open()
            if not self._ram_valid:
                raise KristallError("recovery required; RAM publication was interrupted")
            return deepcopy(self._state.get(namespace, {}).get(key))

    def _encode_frame(self, changes: list[dict[str, Any]]) -> tuple[bytes, str]:
        for change in changes:
            if not isinstance(change["namespace"], str) or not isinstance(change["key"], str):
                raise KristallError("namespace and key must be strings")
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
                raise KristallError("journal frame exceeds size limit")
            return frame, digest
        except (TypeError, ValueError, OverflowError) as exc:
            raise KristallError("unsupported JSON value") from exc

    def _append_frame(self, frame: bytes) -> None:
        first_write = not self.path.exists()
        try:
            with self.path.open("ab", buffering=0) as stream:
                if stream.write(frame) != len(frame):
                    raise OSError("short journal write")
                # Do not acknowledge a commit before its journal bytes are synced.
                os.fsync(stream.fileno())
            if first_write:
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
        except OSError as exc:
            # A failed fsync may still have written bytes: the disk outcome is
            # uncertain, so block future writes instead of claiming rollback.
            self._failed = True
            raise KristallError("commit failed; recovery required") from exc

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
            raise KristallError("recovery required")
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
                raise KristallError("direct mutation during transaction is forbidden")
            _validate_value_shape(value)
            self._commit_changes(
                [{"op": "set", "namespace": namespace, "key": key, "value": deepcopy(value)}]
            )

    def delete(self, namespace: str, key: str) -> None:
        """Synchronously record a deletion, including of an absent key."""
        with self._lock:
            self._ensure_open()
            if self._active_transaction:
                raise KristallError("direct mutation during transaction is forbidden")
            self._commit_changes([{"op": "delete", "namespace": namespace, "key": key}])

    @contextmanager
    def transaction(self) -> Iterator["_JournalTransaction"]:
        """Commit staged changes together on success; discard on exception."""
        with self._lock:
            self._ensure_open()
            if self._failed:
                raise KristallError("recovery required")
            if self._active_transaction:
                raise KristallError("nested transactions are not supported")
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
                raise KristallError("backup integrity mismatch")
            try:
                if destination_must_be_new:
                    os.link(temporary, destination)
                else:
                    os.replace(temporary, destination)
            except FileExistsError as exc:
                raise KristallError("backup destination was created concurrently") from exc
            directory = os.open(str(destination.parent), os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        finally:
            temporary.unlink(missing_ok=True)

    def _verify_checkpoint_source(self) -> None:
        """Never compact over corruption or an unobserved source mutation."""
        try:
            state, sequence, digest = self._inspect_log(self.path)
        except KristallError as exc:
            self._failed = True
            raise KristallError("checkpoint source is corrupt; recovery required") from exc
        if state != self._state or Commit(sequence, digest) != self.last_commit:
            self._failed = True
            raise KristallError("checkpoint source changed; recovery required")

    def checkpoint(self) -> int:
        """Atomically replace a long journal with one verified RAM-state frame."""
        import tempfile

        with self._lock:
            self._ensure_open()
            if self._failed or self._active_transaction:
                raise KristallError("checkpoint requires a healthy idle store")
            if self._sequence == 0:
                return 0
            payload = {
                "format": 1,
                "seq": self._sequence,
                "changes": [],
                "checkpoint": True,
                "state": deepcopy(self._state),
            }
            encoded = json.dumps(
                payload,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
                allow_nan=False,
            ).encode("utf-8")
            digest = hashlib.sha256(bytes(32) + encoded).hexdigest()
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
                raise KristallError("checkpoint exceeds frame size limit")
            temporary: Path | None = None
            replaced = False
            publication_started = False
            try:
                self._verify_checkpoint_source()
                fd, temp_name = tempfile.mkstemp(prefix=".alice-checkpoint-", dir=self.path.parent)
                temporary = Path(temp_name)
                with os.fdopen(fd, "wb") as stream:
                    if stream.write(frame) != len(frame):
                        raise OSError("short checkpoint write")
                    stream.flush()
                    os.fsync(stream.fileno())
                state, seq, verified_digest = self._inspect_log(temporary)
                if state != self._state or seq != self._sequence or verified_digest != digest:
                    raise KristallError("checkpoint verification failed")
                # Cancellation can arrive inside replace, after the path changed
                # but before the return/assignment. Treat publication as uncertain
                # from this point until the directory sync and RAM digest update.
                publication_started = True
                os.replace(temporary, self.path)
                replaced = True
                directory = os.open(str(self.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
                self._digest = digest
                return self._sequence
            except OSError as exc:
                # A replace or directory fsync failure leaves publication uncertain.
                self._failed = True
                raise KristallError("checkpoint failed; recovery required") from exc
            except BaseException:
                if publication_started:
                    self._failed = True
                raise
            finally:
                if temporary is not None and not replaced:
                    temporary.unlink(missing_ok=True)

    def backup(self, destination: str | Path) -> None:
        """Create a verified, fsynced backup without overwriting another artifact."""
        with self._lock:
            self._ensure_open()
            if self._failed or self._active_transaction:
                raise KristallError("backup requires a healthy committed state")
            target = Path(destination)
            if target.resolve() == self.path.resolve() or target.exists():
                raise KristallError("backup destination must be new")
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
                raise KristallError("restore requires a healthy idle store")
            if self._sequence or self._state or (self.path.exists() and self.path.stat().st_size):
                raise KristallError("restore destination is not empty")
            origin = Path(source)
            if origin.resolve() == self.path.resolve():
                raise KristallError("cannot restore from the live database path")
            state, sequence, digest = self._inspect_log(origin)
            if not origin.exists():
                raise KristallError("backup source does not exist")
            try:
                self._copy_verified_file(origin, self.path, state, Commit(sequence, digest))
                self._ram_valid = False
                self._state = state
                self._sequence = sequence
                self._digest = digest
                self._ram_valid = True
            except OSError as exc:
                self._failed = True
                raise KristallError("restore failed; recovery required") from exc
            except BaseException:
                # Restore may have replaced the destination before cancellation.
                # Empty/partial RAM metadata cannot be used to append to that log.
                self._failed = True
                raise

    def close(self) -> None:
        """Release the exclusive writer lock when no transaction is active."""
        with self._lock:
            if os.getpid() != self._pid:
                raise KristallError("database handle belongs to another process")
            if self._active_transaction:
                raise KristallError("cannot close during active transaction")
            if not self._closed:
                if not self._lock_file.closed:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                    self._lock_file.close()
                self._closed = True

    def __enter__(self) -> "_JournalStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class Kristall:
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

    def confirm_durable(self) -> Commit:
        """Reconfirm recovered bytes before returning a retried durable receipt."""
        with self._engine._lock:
            self._engine._ensure_open()
            if self._engine._failed or not self._engine._ram_valid:
                raise StoreError("recovery required")
            commit = self._engine.last_commit
            if commit.sequence == 0:
                return commit
            try:
                with self._engine.path.open("rb") as stream:
                    os.fsync(stream.fileno())
                directory = os.open(str(self._engine.path.parent), os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            except OSError as exc:
                self._engine._failed = True
                raise StoreError("durability confirmation failed; recovery required") from exc
            except BaseException:
                # Cancellation leaves the recovered durability barrier uncertain.
                # Fence the writer while preserving the original exception.
                self._engine._failed = True
                raise
            return commit

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
                raise KristallError("recovery required")
            if self._engine._active_transaction:
                raise KristallError("direct mutation during transaction is forbidden")
            _validate_name(name)
            _validate_value_shape(value)
            self._pending[name] = deepcopy(value)

    def commit(self) -> int:
        """Atomically persist staged names and return the confirmed sequence."""
        with self._engine._lock:
            self._engine._ensure_open()
            if self._engine._failed:
                raise KristallError("recovery required")
            if self._engine._active_transaction:
                raise KristallError("cannot commit during transaction")
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
                raise KristallError("transaction requires an idle store")
            with self._engine.transaction() as tx:
                wrapper = Transaction(self._engine._state.get(self._NAMESPACE, {}))
                try:
                    yield wrapper
                    for change in wrapper._pending_changes():
                        if change["op"] == "delete":
                            tx.delete(change["namespace"], change["key"])
                        else:
                            tx.set(change["namespace"], change["key"], change["value"])
                finally:
                    wrapper._closed = True

    def checkpoint(self) -> int:
        """Compact the confirmed journal without changing visible RAM values."""
        with self._engine._lock:
            if self._pending:
                raise KristallError("checkpoint requires no staged values")
            return self._engine.checkpoint()

    def backup(self, destination: str | Path) -> None:
        """Back up only confirmed journal state."""
        self._engine.backup(destination)

    def restore(self, source: str | Path) -> None:
        """Restore a verified backup into an empty store."""
        with self._engine._lock:
            if self._pending:
                raise KristallError("restore requires no staged values")
            self._engine.restore(source)

    def close(self) -> None:
        """Discard staging and release the single-writer lock."""
        with self._engine._lock:
            self._engine.close()
            self._pending.clear()

    def __enter__(self) -> "Kristall":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def delete(self, name: str) -> None:
        """Durably delete an existing named value without an empty commit."""
        _validate_name(name)
        with self._engine._lock:
            self._engine._ensure_open()
            if self._engine._failed:
                raise KristallError("recovery required")
            if self._pending or self._engine._active_transaction:
                raise KristallError("delete requires an idle store")
            if name in self._engine._state.get(self._NAMESPACE, {}):
                self._engine.delete(self._NAMESPACE, name)

    def items(self, prefix: str = "") -> dict[str, Any]:
        """Read a detached snapshot of committed and staged named values."""
        if not isinstance(prefix, str):
            raise KristallError("prefix must be a string")
        with self._engine._lock:
            self._engine._ensure_open()
            self._engine.last_commit
            values = self._engine._state.get(self._NAMESPACE, {})
            names = set(values) | set(self._pending)
            return {
                name: deepcopy(self._pending[name] if name in self._pending else values[name])
                for name in sorted(names)
                if name.startswith(prefix)
            }


class Transaction:
    """Private overlay for one atomic name/value transaction."""

    def __init__(self, committed: dict[str, Any]) -> None:
        self._committed = committed
        self._owner = get_ident()
        self._pending: dict[str, Any] = {}
        self._deleted: set[str] = set()
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise KristallError("transaction is closed")
        if self._owner != get_ident():
            raise KristallError("transaction belongs to another thread")

    def get(self, name: str) -> Any | None:
        self._ensure_open()
        _validate_name(name)
        if name in self._deleted:
            return None
        return deepcopy(self._pending[name] if name in self._pending else self._committed.get(name))

    def set(self, name: str, value: Any) -> None:
        self._ensure_open()
        _validate_name(name)
        _validate_value_shape(value)
        self._deleted.discard(name)
        self._pending[name] = deepcopy(value)

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        changes = [
            {"op": "set", "namespace": "values", "key": name, "value": value}
            for name, value in self._pending.items()
        ]
        changes.extend(
            {"op": "delete", "namespace": "values", "key": name} for name in self._deleted
        )
        return sorted(changes, key=lambda change: change["key"])

    def delete(self, name: str) -> None:
        """Stage a deletion in the same atomic journal transaction."""
        self._ensure_open()
        _validate_name(name)
        self._pending.pop(name, None)
        if name in self._committed:
            self._deleted.add(name)


class _JournalTransaction:
    """Staged changes overlay committed RAM until one synchronous commit."""

    def __init__(self, committed: dict[str, dict[str, Any]]) -> None:
        self._committed = committed
        self._owner = get_ident()
        self._changes: dict[tuple[str, str], dict[str, Any]] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise KristallError("transaction is closed")
        if self._owner != get_ident():
            raise KristallError("transaction belongs to another thread")

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
        self._changes[(namespace, key)] = {"op": "delete", "namespace": namespace, "key": key}

    def _pending_changes(self) -> list[dict[str, Any]]:
        self._ensure_open()
        return [self._changes[key] for key in sorted(self._changes)]
