"""SQLite/PostgreSQL-compatible repository for shared agent memory."""

from __future__ import annotations

import json
import time
from typing import Any, Callable

from db import get_conn

from .models import MemoryLookup, MemoryRecord


class AgentMemoryStore:
    """Persist compact derived memory while GitHub/source references remain authoritative."""

    def __init__(self, connection_factory: Callable[[], Any] | None = None) -> None:
        self._connection_factory = connection_factory or get_conn
        self._schema_ready = False

    def _connect(self):
        return self._connection_factory()

    def create_schema(self) -> None:
        if self._schema_ready:
            return
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memory (
                    memory_id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    text TEXT NOT NULL,
                    provenance_json TEXT NOT NULL,
                    source_version TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    freshness_json TEXT NOT NULL,
                    sensitivity TEXT NOT NULL,
                    visibility_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    expires_at INTEGER,
                    superseded_by TEXT
                )
                """
            )
            cur.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_agent_memory_kind_scope_status
                ON agent_memory(kind, scope, status)
                """
            )
            conn.commit()
            self._schema_ready = True
        finally:
            conn.close()

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _record_from_row(row: Any) -> MemoryRecord:
        return MemoryRecord(
            memory_id=row["memory_id"],
            kind=row["kind"],
            scope=row["scope"],
            text=row["text"],
            provenance=json.loads(row["provenance_json"]),
            source_version=row["source_version"],
            payload=json.loads(row["payload_json"]),
            confidence=float(row["confidence"]),
            freshness=json.loads(row["freshness_json"]),
            sensitivity=row["sensitivity"],
            visibility=tuple(json.loads(row["visibility_json"])),
            status=row["status"],
            created_at=int(row["created_at"]),
            updated_at=int(row["updated_at"]),
            expires_at=int(row["expires_at"]) if row["expires_at"] is not None else None,
            superseded_by=row["superseded_by"],
        )

    def upsert(self, record: MemoryRecord) -> MemoryRecord:
        self.create_schema()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO agent_memory (
                    memory_id, kind, scope, text, provenance_json, source_version,
                    payload_json, confidence, freshness_json, sensitivity,
                    visibility_json, status, created_at, updated_at, expires_at,
                    superseded_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(memory_id) DO UPDATE SET
                    kind = excluded.kind,
                    scope = excluded.scope,
                    text = excluded.text,
                    provenance_json = excluded.provenance_json,
                    source_version = excluded.source_version,
                    payload_json = excluded.payload_json,
                    confidence = excluded.confidence,
                    freshness_json = excluded.freshness_json,
                    sensitivity = excluded.sensitivity,
                    visibility_json = excluded.visibility_json,
                    status = excluded.status,
                    created_at = agent_memory.created_at,
                    updated_at = excluded.updated_at,
                    expires_at = excluded.expires_at,
                    superseded_by = excluded.superseded_by
                """,
                (
                    record.memory_id,
                    record.kind,
                    record.scope,
                    record.text,
                    self._json(record.as_dict()["provenance"]),
                    record.source_version,
                    self._json(record.as_dict()["payload"]),
                    record.confidence,
                    self._json(record.as_dict()["freshness"]),
                    record.sensitivity,
                    self._json(list(record.visibility)),
                    record.status,
                    record.created_at,
                    record.updated_at,
                    record.expires_at,
                    record.superseded_by,
                ),
            )
            conn.commit()
        finally:
            conn.close()
        stored = self.get(record.memory_id)
        if stored is None:
            raise RuntimeError("memory upsert did not persist")
        return stored

    def get(self, memory_id: str) -> MemoryRecord | None:
        self.create_schema()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("SELECT * FROM agent_memory WHERE memory_id = ?", (str(memory_id),))
            row = cur.fetchone()
            return None if row is None else self._record_from_row(row)
        finally:
            conn.close()

    def lookup(
        self,
        memory_id: str,
        *,
        source_version: str | None = None,
        now: int | None = None,
    ) -> MemoryLookup:
        record = self.get(memory_id)
        if record is None:
            return MemoryLookup(status="miss", memory_id=str(memory_id), reason="not_found")
        if record.status == "superseded":
            return MemoryLookup(
                status="superseded",
                memory_id=record.memory_id,
                record=record,
                reason="superseded",
            )
        if record.status == "stale":
            return MemoryLookup(
                status="stale",
                memory_id=record.memory_id,
                record=record,
                reason="marked_stale",
            )
        if source_version is not None and str(source_version) != record.source_version:
            return MemoryLookup(
                status="stale",
                memory_id=record.memory_id,
                record=record,
                reason="source_version_changed",
            )
        if record.is_expired(now=now):
            return MemoryLookup(
                status="stale",
                memory_id=record.memory_id,
                record=record,
                reason="expired",
            )
        return MemoryLookup(status="hit", memory_id=record.memory_id, record=record)

    def list_records(
        self,
        *,
        kind: str | None = None,
        scope: str | None = None,
        status: str | None = "active",
        visible_to: str | None = None,
    ) -> list[MemoryRecord]:
        self.create_schema()
        conditions: list[str] = []
        params: list[Any] = []
        if kind is not None:
            conditions.append("kind = ?")
            params.append(str(kind))
        if scope is not None:
            conditions.append("scope = ?")
            params.append(str(scope))
        if status is not None:
            conditions.append("status = ?")
            params.append(str(status))

        sql = "SELECT * FROM agent_memory"
        if conditions:
            sql += " WHERE " + " AND ".join(conditions)
        sql += " ORDER BY updated_at DESC, memory_id ASC"

        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute(sql, tuple(params))
            records = [self._record_from_row(row) for row in cur.fetchall()]
        finally:
            conn.close()

        if visible_to is None:
            return records
        role = str(visible_to)
        return [
            record
            for record in records
            if not record.visibility or role in record.visibility
        ]

    def mark_stale(self, memory_id: str, *, reason: str, now: int | None = None) -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        timestamp = int(time.time()) if now is None else int(now)
        freshness = record.as_dict()["freshness"]
        freshness["stale_reason"] = str(reason)
        freshness["invalidated_at"] = timestamp
        updated = MemoryRecord(
            **{
                **record.as_dict(),
                "freshness": freshness,
                "status": "stale",
                "updated_at": timestamp,
            }
        )
        self.upsert(updated)
        return True

    def supersede(
        self,
        memory_id: str,
        *,
        replacement_id: str,
        now: int | None = None,
    ) -> bool:
        record = self.get(memory_id)
        if record is None:
            return False
        if self.get(replacement_id) is None:
            raise ValueError("replacement memory does not exist")
        timestamp = int(time.time()) if now is None else int(now)
        updated = MemoryRecord(
            **{
                **record.as_dict(),
                "status": "superseded",
                "superseded_by": str(replacement_id),
                "updated_at": timestamp,
            }
        )
        self.upsert(updated)
        return True

    def delete(self, memory_id: str) -> bool:
        self.create_schema()
        conn = self._connect()
        try:
            cur = conn.cursor()
            cur.execute("DELETE FROM agent_memory WHERE memory_id = ?", (str(memory_id),))
            changed = cur.rowcount > 0
            conn.commit()
            return changed
        finally:
            conn.close()

    def export_records(self) -> list[dict[str, Any]]:
        records = self.list_records(status=None)
        return [record.as_dict() for record in sorted(records, key=lambda item: item.memory_id)]
