"""Optional authenticated, ordered receiver over the native RAM/journal engine.

No sockets, background jobs, source exporter, failover or remote commit policy.
The caller supplies already reassembled bounded messages from a trusted channel.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
from dataclasses import dataclass
from typing import Any

from kristall.store import (
    MAX_JOURNAL_FRAME_BYTES,
    _validate_value,
)
from kristall.store import (
    Kristall as MemoryStore,
)
from kristall.store import (
    KristallError as StoreError,
)


class ReplicationError(StoreError):
    """Invalid, divergent or unsupported replication input; no acceptance."""


@dataclass(frozen=True)
class Cursor:
    source: str
    epoch: int
    sequence: int
    digest: str


@dataclass(frozen=True)
class Receipt:
    status: str
    cursor: Cursor
    local_commit: int


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReplicationError("duplicate field")
        result[key] = value
    return result


class ReplicaSink:
    """One pinned source/epoch for a dedicated replica MemoryStore.

    Library-internal namespace holds the cursor in the same native transaction as
    the data. Local application writes to a replica are unsupported. Only the most
    recent transaction can be retried; stale history needs an explicit resync.
    """

    def __init__(
        self, store: MemoryStore, *, source: str, epoch: int, key: bytes
    ) -> None:
        if not isinstance(source, str) or not source or len(source) > 128:
            raise ReplicationError("invalid source identity")
        if type(epoch) is not int or epoch < 1:
            raise ReplicationError("invalid authority epoch")
        if not isinstance(key, bytes) or len(key) != 32:
            raise ReplicationError("invalid authentication key")
        self._store, self._source, self._epoch, self._key = store, source, epoch, key

    def _decode(self, wire: bytes) -> tuple[dict[str, Any], str]:
        if not isinstance(wire, bytes) or not 36 < len(wire) <= MAX_JOURNAL_FRAME_BYTES:
            raise ReplicationError("invalid message size")
        body = wire[36:]
        if wire[:4] != b"MR01" or not hmac.compare_digest(
            wire[4:36], hmac.digest(self._key, body, "sha256")
        ):
            raise ReplicationError("authentication failed")
        try:
            payload = json.loads(body, object_pairs_hook=_object)
            _validate_value(payload)
            if not isinstance(payload, dict) or set(payload) != {
                "source",
                "epoch",
                "sequence",
                "previous",
                "changes",
            }:
                raise ReplicationError("invalid envelope")
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
            if canonical != body:
                raise ReplicationError("noncanonical message")
            self._validate_headers(payload)
            self._validate_changes(payload)
            return payload, hashlib.sha256(body).hexdigest()
        except (ValueError, TypeError, RecursionError, UnicodeError, StoreError) as exc:
            raise ReplicationError("invalid authenticated transaction") from exc

    def _validate_headers(self, payload: dict[str, Any]) -> None:
        if (
            payload["source"] != self._source
            or type(payload["epoch"]) is not int
            or payload["epoch"] != self._epoch
        ):
            raise ReplicationError("source or epoch mismatch")
        if type(payload["sequence"]) is not int or payload["sequence"] < 1:
            raise ReplicationError("invalid sequence")
        if not isinstance(payload["previous"], str) or not re.fullmatch(
            r"[0-9a-f]{64}", payload["previous"]
        ):
            raise ReplicationError("invalid previous digest")

    @staticmethod
    def _validate_changes(payload: dict[str, Any]) -> None:
        changes = payload["changes"]
        if not isinstance(changes, list) or not changes or len(changes) > 10000:
            raise ReplicationError("invalid transaction size")
        names: set[str] = set()
        for change in changes:
            if not isinstance(change, dict) or change.get("op") not in (
                "set",
                "delete",
            ):
                raise ReplicationError("invalid operation")
            expected = (
                {"op", "name", "value"} if change["op"] == "set" else {"op", "name"}
            )
            name = change.get("name")
            if (
                set(change) != expected
                or not isinstance(name, str)
                or not name
                or name in names
            ):
                raise ReplicationError("invalid transaction name")
            names.add(name)

    def _admit(
        self, prior: Any, payload: dict[str, Any], digest: str
    ) -> tuple[int, str, Receipt | None]:
        engine = self._store._engine
        if prior is not None:
            if prior["local_commit"] != engine.last_commit.sequence:
                raise ReplicationError("local mutation of replica")
            if prior["source"] != self._source or prior["epoch"] != self._epoch:
                raise ReplicationError("replica authority mismatch")
            if payload["sequence"] == prior["sequence"]:
                if digest != prior["digest"]:
                    raise ReplicationError("divergent retry")
                self._store.confirm_durable()
                receipt = Receipt(
                    "DURABLY_APPLIED",
                    Cursor(
                        self._source, self._epoch, prior["sequence"], prior["digest"]
                    ),
                    prior["local_commit"],
                )
                return prior["sequence"], prior["digest"], receipt
            sequence, previous = prior["sequence"], prior["digest"]
        else:
            if engine.last_commit.sequence != 0:
                raise ReplicationError("initial replica must be empty")
            sequence, previous = 0, "0" * 64
        return sequence, previous, None

    def apply(self, wire: bytes) -> Receipt:
        """Return only after native durable apply; uncertainty raises StoreError."""
        payload, digest = self._decode(wire)
        engine = self._store._engine
        # Metadata/data admission and publication share the native writer lock.
        with engine._lock:
            engine._ensure_open()
            if self._store._pending:
                raise ReplicationError("replica has local staging")
            if engine._failed:
                raise StoreError("recovery required")
            with engine.transaction() as tx:
                prior = tx.get("_replication", "cursor")
                sequence, previous, retry = self._admit(prior, payload, digest)
                if retry is not None:
                    return retry
                if (
                    payload["sequence"] != sequence + 1
                    or payload["previous"] != previous
                ):
                    raise ReplicationError("history gap or divergence")
                local_commit = engine.last_commit.sequence + 1
                for change in payload["changes"]:
                    if change["op"] == "set":
                        tx.set("values", change["name"], change["value"])
                    else:
                        tx.delete("values", change["name"])
                tx.set(
                    "_replication",
                    "cursor",
                    dict(
                        source=self._source,
                        epoch=self._epoch,
                        sequence=payload["sequence"],
                        digest=digest,
                        local_commit=local_commit,
                    ),
                )
            return Receipt(
                "DURABLY_APPLIED",
                Cursor(self._source, self._epoch, payload["sequence"], digest),
                local_commit,
            )
