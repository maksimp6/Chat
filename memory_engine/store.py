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
from typing import Any, Iterator


# Maximum serialized journal frame, including newline. Keep RAM replay bounded.
MAX_JOURNAL_FRAME_BYTES = 8 * 1024 * 1024


class StoreError(RuntimeError):
    """Persistence, integrity, or ownership failure."""


MAX_VALUE_DEPTH = 64
MAX_VALUE_NODES = 100_000
MAX_VALUE_TEXT_BYTES = MAX_JOURNAL_FRAME_BYTES


def _validate_value_shape(value: Any) -> None:
    """Validate JSON-compatible values and bound traversal before staging."""
    import math

    # Keep only active ancestors on the stack. Wide lists must not create
    # one pending stack entry per element.
    from collections.abc import Iterator as ValueIterator

    stack: list[tuple[ValueIterator[Any], int, int]] = []
    ancestors: set[int] = set()
    count = 0
    text_bytes = 0

    def check_text(text: str) -> None:
        nonlocal text_bytes
        remaining = MAX_VALUE_TEXT_BYTES - text_bytes
        if len(text) > remaining:
            raise StoreError("value text exceeds size limit")
        # Encode bounded chunks in C: avoid a whole-string temporary buffer.
        # Keep chunks aligned to Python Unicode characters.
        for offset in range(0, len(text), 16384):
            try:
                chunk_bytes = len(text[offset:offset + 16384].encode("utf-8"))
            except UnicodeEncodeError as exc:
                raise StoreError("invalid Unicode text") from exc
            text_bytes += chunk_bytes
            if text_bytes > MAX_VALUE_TEXT_BYTES:
                raise StoreError("value text exceeds size limit")

    item = value
    depth = 0
    while True:
        count += 1
        if count > MAX_VALUE_NODES or depth > MAX_VALUE_DEPTH:
            raise StoreError("value structure exceeds limit")
        if isinstance(item, (dict, list, tuple)):
            identity = id(item)
            if identity in ancestors:
                raise StoreError("shared or cyclic container is unsupported")
            ancestors.add(identity)
            if isinstance(item, dict):
                for key in item:
                    count += 1
                    if count > MAX_VALUE_NODES:
                        raise StoreError("value structure exceeds limit")
                    if type(key) is not str:
                        raise StoreError("object keys must be strings")
                    check_text(key)
                children = iter(item.values())
            else:
                children = iter(item)
            stack.append((children, depth, identity))
        elif type(item) is str:
            check_text(item)
        elif item is None or type(item) in (int, bool):
            pass
        elif type(item) is float and math.isfinite(item):
            pass
        else:
            # Preserve commit-time rejection of unsupported scalar values.
            pass
        while stack:
            children, parent_depth, identity = stack[-1]
            try:
                item = next(children)
                depth = parent_depth + 1
                break
            except StopIteration:
                stack.pop()
                ancestors.remove(identity)
        else:
            return


@dataclass(frozen=True)
class Commit:
    """Confirmed journal position and chained SHA-256 digest."""

    sequence: int
    digest: str


class _JournalEngine:
    """In-process committed RAM state backed by an fsynced append-only log."""

    def __init__(self, path: str | Path) -> None:
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
            raise StoreError("database already has a writer") from exc
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
        # Stream complete newline-delimited frames: never load the full log.
        # Reject a partial final frame without truncating ambiguous data.
        with path.open("rb") as stream:
            while frame := stream.readline(MAX_JOURNAL_FRAME_BYTES + 1):
                if len(frame) > MAX_JOURNAL_FRAME_BYTES:
                    raise StoreError("journal frame exceeds size limit")
                if not frame.endswith(b"\n"):
                    raise StoreError("incomplete journal tail; recovery required")
                state, sequence, digest = _JournalEngine._apply_verified_frame(
                    frame[:-1], state, sequence, digest
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
            if not isinstance(payload, dict) or not isinstance(payload.get("changes"), list):
                raise ValueError("invalid journal payload")
            serialized_payload = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ).encode()
            # Chain each frame to the previous confirmed digest so replay detects
            # reordered, missing, or modified records (not malicious rewrites).
            next_digest = hashlib.sha256(bytes.fromhex(digest) + serialized_payload).hexdigest()
            if record["digest"] != next_digest or payload["seq"] != sequence + 1:
                raise ValueError("commit chain mismatch")
            # Validate every operation before touching committed state.
            # Replay must not copy the accumulated database for each frame.
            validated: list[tuple[str, str, str, Any]] = []
            for change in payload["changes"]:
                if not isinstance(change, dict):
                    raise ValueError("invalid journal change")
                namespace, key = change["namespace"], change["key"]
                if not isinstance(namespace, str) or not isinstance(key, str):
                    raise ValueError("invalid journal key")
                op = change["op"]
                if op not in ("set", "delete"):
                    raise ValueError("invalid journal operation")
                value = change["value"] if op == "set" else None
                validated.append((op, namespace, key, value))
            for op, namespace, key, value in validated:
                if op == "set":
                    state.setdefault(namespace, {})[key] = value
                else:
                    state.get(namespace, {}).pop(key, None)
            return state, payload["seq"], next_digest
        except (ValueError, KeyError, TypeError, UnicodeError, AttributeError) as exc:
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
        payload = {"seq": self._sequence + 1, "changes": changes}
        try:
            # Bound serialization itself, not just the completed frame.
            # Stream chunks and reject before accumulating unbounded output.
            parts: list[bytes] = []
            used = 0
            frame_overhead = len(b'{"digest":"') + 64 + len(b'","payload":') + len(b'}\n')
            encoder = json.JSONEncoder(
                sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
            )
            for chunk in encoder.iterencode(payload):
                # JSONEncoder can yield a whole large string. Slice before
                # encoding so each temporary UTF-8 buffer remains bounded.
                for offset in range(0, len(chunk), 16384):
                    encoded = chunk[offset:offset + 16384].encode("utf-8")
                    used += len(encoded)
                    if used + frame_overhead > MAX_JOURNAL_FRAME_BYTES:
                        raise StoreError("journal frame exceeds size limit")
                    parts.append(encoded)
            serialized_payload = b"".join(parts)
            digest = hashlib.sha256(bytes.fromhex(self._digest) + serialized_payload).hexdigest()
            journal_frame = (
                b'{"digest":"' + digest.encode("ascii") + b'","payload":'
                + serialized_payload + b'}\n'
            )
            if len(journal_frame) > MAX_JOURNAL_FRAME_BYTES:
                raise StoreError("journal frame exceeds size limit")
            return journal_frame, digest
        except (TypeError, ValueError, OverflowError) as exc:
            raise StoreError("unsupported JSON value") from exc

    def _append_frame(self, journal_frame: bytes) -> None:
        first_write = not self.path.exists()
        try:
            with self.path.open("ab", buffering=0) as stream:
                if stream.write(journal_frame) != len(journal_frame):
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
        journal_frame, digest = self._encode_frame(changes)
        self._append_frame(journal_frame)
        # Publish only confirmed changes to the RAM view seen by readers.
        self._apply_changes(changes)
        self._sequence += 1
        self._digest = digest

    @contextmanager
    def value_guard(self) -> Iterator[None]:
        """Synchronize public value operations and reject closed instances."""
        with self._lock:
            self._ensure_open()
            yield

    def ensure_value_writable(self) -> None:
        """Reject staging while the journal is failed or a transaction is active."""
        self._ensure_open()
        if self._failed:
            raise StoreError("recovery required")
        if self._active_transaction:
            raise StoreError("direct mutation during transaction is forbidden")

    def committed_value(self, name: str) -> Any | None:
        """Return one committed value without exposing the internal namespace."""
        return self.get("values", name)

    @contextmanager
    def value_transaction(self, pending: dict[str, Any]) -> Iterator["_ValueTransaction"]:
        """Own one serialized name/value transaction and its durable commit."""
        with self._lock:
            self._ensure_open()
            if self._failed:
                raise StoreError("recovery required")
            if self._active_transaction:
                raise StoreError("nested transactions are not supported")
            if pending:
                raise StoreError("transaction requires an idle store")
            self._active_transaction = True
            transaction = _ValueTransaction(deepcopy(self._state.get("values", {})))
            try:
                yield transaction
                self._commit_changes(transaction._pending_changes())
            finally:
                transaction._closed = True
                self._active_transaction = False

    def commit_values(self, values: dict[str, Any]) -> int:
        """Own journal record encoding and durable publication for named values."""
        with self._lock:
            self._ensure_open()
            if self._failed:
                raise StoreError("recovery required")
            if self._active_transaction:
                raise StoreError("cannot commit during transaction")
            changes = [
                {"op": "set", "namespace": "values", "key": name, "value": value}
                for name, value in sorted(values.items())
            ]
            self._commit_changes(changes)
            return self._sequence

    @staticmethod
    def _copy_verified_file(
        source: Path,
        destination: Path,
        expected_state: dict[str, dict[str, Any]],
        expected_commit: Commit,
        *,
        destination_must_be_new: bool = False,
    ) -> None:
        """Verify a temp copy, then publish it (exclusive backup is not atomic)."""
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
            actual_state, sequence, digest = _JournalEngine._inspect_log(temporary)
            if actual_state != expected_state or Commit(sequence, digest) != expected_commit:
                raise StoreError("backup integrity mismatch")
            try:
                if destination_must_be_new:
                    # Exclusive creation prevents overwriting another backup.
                    # The destination is visible while being copied; remove our
                    # incomplete copy if writing or syncing fails.
                    with destination.open("xb") as published:
                        try:
                            with temporary.open("rb") as verified:
                                shutil.copyfileobj(verified, published)
                            published.flush()
                            os.fsync(published.fileno())
                        except BaseException:
                            destination.unlink(missing_ok=True)
                            raise
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
            if self._active_transaction:
                raise StoreError("cannot close during active transaction")
            if not self._closed:
                if not self._lock_file.closed:
                    fcntl.flock(self._lock_file.fileno(), fcntl.LOCK_UN)
                    self._lock_file.close()
                self._closed = True

    def __enter__(self) -> "_JournalEngine":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class MemoryStore:
    """Client-owned name/value API with explicit durable journal commits."""

    def __init__(self, path: str | Path) -> None:
        self._engine = _JournalEngine(path)
        self._pending: dict[str, Any] = {}

    @property
    def last_commit(self) -> Commit:
        """Return the last acknowledged durable journal position."""
        return self._engine.last_commit

    def get(self, name: str) -> Any | None:
        """Read staged data first; otherwise return a copy of committed data."""
        with self._engine.value_guard():
            if name in self._pending:
                return deepcopy(self._pending[name])
            return self._engine.committed_value(name)

    def set(self, name: str, value: Any) -> None:
        """Stage a copy without acknowledging a durable write."""
        with self._engine.value_guard():
            self._engine.ensure_value_writable()
            if not isinstance(name, str) or not name:
                raise StoreError("name must be a nonempty string")
            if value is None:
                raise StoreError("None is reserved for missing names")
            _validate_value_shape(value)
            self._pending[name] = deepcopy(value)

    def commit(self) -> int:
        """Durably publish staged names, then clear acknowledged staging."""
        with self._engine.value_guard():
            sequence = self._engine.commit_values(self._pending)
            self._pending.clear()
            return sequence

    @contextmanager
    def transaction(self) -> Iterator["_ValueTransaction"]:
        """Delegate atomic read-modify-write to the single transaction owner."""
        with self._engine.value_transaction(self._pending) as transaction:
            yield transaction

    def backup(self, destination: str | Path) -> None:
        """Back up only confirmed journal state."""
        self._engine.backup(destination)

    def restore(self, source: str | Path) -> None:
        """Restore a verified backup into an empty store."""
        self._engine.restore(source)

    def close(self) -> None:
        """Release the writer and discard staging only after successful close."""
        self._engine.close()
        self._pending.clear()

    def __enter__(self) -> "MemoryStore":
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()


class _ValueTransaction:
    """Private overlay for one atomic name/value transaction."""

    def __init__(self, committed: dict[str, Any]) -> None:
        self._committed = committed
        self._pending: dict[str, Any] = {}
        self._closed = False

    def _ensure_open(self) -> None:
        if self._closed:
            raise StoreError("transaction is closed")

    def get(self, name: str) -> Any | None:
        self._ensure_open()
        return deepcopy(self._pending[name] if name in self._pending else self._committed.get(name))

    def set(self, name: str, value: Any) -> None:
        self._ensure_open()
        if value is None:
            raise StoreError("None is reserved for missing names")
        _validate_value_shape(value)
        self._pending[name] = deepcopy(value)

    def _pending_changes(self) -> list[dict[str, Any]]:
        """Return a journal-ready snapshot without exposing mutable staging."""
        self._ensure_open()
        return [
            {"op": "set", "namespace": "values", "key": name, "value": deepcopy(value)}
            for name, value in sorted(self._pending.items())
        ]
