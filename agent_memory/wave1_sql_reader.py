"""Read-only repeatable SQL snapshots for non-authoritative Wave 1 rehearsal.

This module never opens the production database on import, never mutates SQL,
and cannot grant ownership or promote a shadow store.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

from agent_memory.wave1_shadow import ShadowMismatch, Wave1Snapshot, make_snapshot

MAX_ROWS = 100_000


class SnapshotReadError(RuntimeError):
    """SQL read failed or the source is unsafe/ambiguous (no raw data leaked)."""


@dataclass(frozen=True)
class OwnerAudit:
    """A caller-supplied roster is *not* cryptographic owner authentication."""

    status: str
    count: int
    digest: str


@dataclass(frozen=True)
class SqlSourceSnapshot:
    snapshot: Wave1Snapshot
    owner_audit: OwnerAudit
    dialect: str


class SqlSnapshotReader:
    """Read exactly three legacy tables within one read-only transaction."""

    def __init__(
        self,
        dialect: str,
        connection_factory: Callable[[], Any],
        *,
        source_path: Path | None = None,
        between_selects: Callable[[], None] | None = None,
    ) -> None:
        if dialect not in ("sqlite", "postgresql"):
            raise ValueError("unsupported SQL dialect")
        self.dialect = dialect
        self._factory = connection_factory
        self.source_path = source_path
        # Testing seam: insert a concurrent writer after the first SELECT.
        self._between_selects = between_selects

    @classmethod
    def sqlite(
        cls, path: str | Path, *, between_selects: Callable[[], None] | None = None
    ) -> "SqlSnapshotReader":
        source = Path(path).expanduser().absolute()
        if source.is_symlink() or not source.is_file():
            raise SnapshotReadError("source must be an existing non-symlink SQLite file")

        def connect() -> sqlite3.Connection:
            conn = sqlite3.connect(
                source.as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5
            )
            conn.row_factory = sqlite3.Row
            return conn

        return cls("sqlite", connect, source_path=source.resolve(), between_selects=between_selects)

    @classmethod
    def postgresql(
        cls,
        connection_factory: Callable[[], Any],
        *,
        between_selects: Callable[[], None] | None = None,
    ) -> "SqlSnapshotReader":
        """Require a fresh connection opened in autocommit mode by the caller.

        No SQL passwords or connection URIs are retained by the reader.
        PostgreSQL read-only repeatable-read is activated before any SELECT.
        """
        return cls("postgresql", connection_factory, between_selects=between_selects)

    def _start_transaction(self, conn: Any, cursor: Any) -> None:
        if self.dialect == "postgresql":
            if getattr(conn, "autocommit", None) is not True:
                raise SnapshotReadError("PostgreSQL source connection must be autocommit")
            cursor.execute("BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        else:
            cursor.execute("PRAGMA query_only = ON")
            cursor.execute("BEGIN")

    @staticmethod
    def _fetch_dicts(cursor: Any, sql: str) -> list[dict[str, Any]]:
        cursor.execute(sql)
        columns = [
            column.name if hasattr(column, "name") else column[0] for column in cursor.description
        ]
        result: list[dict[str, Any]] = []
        while True:
            rows = cursor.fetchmany(1024)
            if not rows:
                break
            result.extend(dict(zip(columns, row)) for row in rows)
            if len(result) > MAX_ROWS:
                raise SnapshotReadError("SQL snapshot exceeded row limit")
        return result

    def _read_tables(
        self, cursor: Any
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any] | None]:
        sessions = self._fetch_dicts(
            cursor,
            "SELECT id, status, metadata_json, created_at, updated_at, completed_at "
            "FROM sessions ORDER BY id",
        )
        if self._between_selects is not None:
            self._between_selects()
        facts = self._fetch_dicts(
            cursor,
            "SELECT id, category, fact, updated_at FROM global_memory ORDER BY id",
        )
        config_rows = self._fetch_dicts(
            cursor,
            "SELECT value FROM configs WHERE key = 'memory_config'",
        )
        if len(config_rows) > 1:
            raise SnapshotReadError("ambiguous memory configuration")
        if not config_rows:
            return sessions, facts, None
        try:
            config = json.loads(config_rows[0]["value"])
        except (ValueError, TypeError) as exc:
            raise SnapshotReadError("invalid memory configuration") from exc
        if not isinstance(config, dict):
            raise SnapshotReadError("memory configuration must be an object")
        return sessions, facts, config

    def read(
        self,
        *,
        owner_bindings: Mapping[str, str] | None = None,
        known_owner_ids: frozenset[str] | None = None,
    ) -> SqlSourceSnapshot:
        """Never returns validated owner authority; parity only."""
        try:
            with closing(self._factory()) as conn:
                cursor = conn.cursor()
                try:
                    self._start_transaction(conn, cursor)
                    sessions, facts, config = self._read_tables(cursor)
                finally:
                    conn.rollback()  # never commit, even if the read fails
                    cursor.close()
            owner_audit = audit_owners(sessions, owner_bindings, known_owner_ids)
            source_digest = _content_digest(sessions, facts, config)
            snapshot = make_snapshot(
                sessions,
                facts,
                config,
                source_revision=f"{self.dialect}:content-sha256:{source_digest}",
            )
            return SqlSourceSnapshot(snapshot, owner_audit, self.dialect)
        except (SnapshotReadError, ShadowMismatch):
            raise
        except (OSError, sqlite3.Error, ValueError, KeyError, TypeError) as exc:
            raise SnapshotReadError("read-only SQL snapshot failed") from exc


def _content_digest(
    sessions: list[dict[str, Any]], facts: list[dict[str, Any]], config: dict[str, Any] | None
) -> str:
    payload = json.dumps(
        {"sessions": sessions, "facts": facts, "config": config},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def audit_owners(
    sessions: list[dict[str, Any]],
    owner_bindings: Mapping[str, str] | None,
    known_owner_ids: frozenset[str] | None,
) -> OwnerAudit:
    """Check completeness/identity consistency, never manufacture owner proof."""
    ids = [record["id"] for record in sessions]
    if len(set(ids)) != len(ids):
        raise SnapshotReadError("duplicate SQL session identifiers")
    if owner_bindings is None:
        return OwnerAudit("unverified_missing", len(ids), "")
    if set(owner_bindings) != set(ids):
        raise SnapshotReadError("incomplete or extra owner bindings")
    if known_owner_ids is None:
        raise SnapshotReadError("authoritative owner roster is required")
    if any(
        not isinstance(owner, str) or not owner or owner not in known_owner_ids
        for owner in owner_bindings.values()
    ):
        raise SnapshotReadError("unknown owner binding")
    if not all(isinstance(session_id, str) and session_id for session_id in owner_bindings):
        raise SnapshotReadError("invalid session owner binding")
    encoded = json.dumps(
        sorted(owner_bindings.items()), ensure_ascii=False, separators=(",", ":")
    ).encode()
    return OwnerAudit(
        "externally_asserted_unverified", len(ids), hashlib.sha256(encoded).hexdigest()
    )


__all__ = [
    "OwnerAudit",
    "SnapshotReadError",
    "SqlSnapshotReader",
    "SqlSourceSnapshot",
    "audit_owners",
]
