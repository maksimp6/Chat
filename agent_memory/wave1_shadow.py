"""Isolated, non-authoritative Wave 1 SQL-to-file snapshot rehearsal.

No live database access, no runtime routing and no production cutover.
Callers supply a repeatable, transactionally consistent legacy snapshot.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping

from agent_memory.file_memory_db import FileMemoryDB

SESSION_PREFIX = "shadow:wave1:session:"
FACT_PREFIX = "shadow:wave1:fact:"
CONFIG_KEY = "shadow:wave1:memory_config"
MARKER_KEY = "shadow:wave1:manifest"
_SECRET_KEYS = re.compile(r"(?:password|secret|token|credential|private.key|api.key|cookie)", re.I)


class ShadowMismatch(ValueError):
    """Shadow source differs, schema unsupported or stage unsafe."""


@dataclass(frozen=True)
class Wave1Snapshot:
    sessions: tuple[dict[str, Any], ...]
    facts: tuple[dict[str, Any], ...]
    config: dict[str, Any] | None
    source_revision: str
    digest: str


def _digest(data: Mapping[str, Any]) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _safe_metadata(value: object) -> bool:
    if isinstance(value, dict):
        return all(
            isinstance(key, str)
            and not _SECRET_KEYS.search(key)
            and _safe_metadata(child)
            for key, child in value.items()
        )
    if isinstance(value, list):
        return all(_safe_metadata(child) for child in value)
    return value is None or type(value) in (str, int, float, bool)


def _session(raw: Mapping[str, Any]) -> dict[str, Any]:
    identifier = raw.get("id")
    status = raw.get("status")
    metadata_json = raw.get("metadata_json")
    created = raw.get("created_at")
    updated = raw.get("updated_at")
    completed = raw.get("completed_at")
    if not isinstance(identifier, str) or not identifier:
        raise ShadowMismatch("invalid session id")
    if status not in ("active", "completed", "failed", "cancelled"):
        raise ShadowMismatch("invalid session status")
    if type(created) is not int or type(updated) is not int or updated < created:
        raise ShadowMismatch("invalid session timestamps")
    if completed is not None and (type(completed) is not int or completed < created):
        raise ShadowMismatch("invalid session completion timestamp")
    if not isinstance(metadata_json, str):
        raise ShadowMismatch("invalid session metadata")
    try:
        metadata = json.loads(metadata_json)
    except (ValueError, TypeError) as exc:
        raise ShadowMismatch("invalid session metadata") from exc
    if not isinstance(metadata, dict) or not _safe_metadata(metadata):
        raise ShadowMismatch("session metadata requires secret-store reconciliation")
    return {
        "id": identifier,
        "status": status,
        "metadata": metadata,
        "created_at": created,
        "updated_at": updated,
        "completed_at": completed,
    }


def _fact(raw: Mapping[str, Any]) -> dict[str, Any]:
    identifier = raw.get("id")
    category = raw.get("category")
    fact = raw.get("fact")
    updated = raw.get("updated_at")
    if type(identifier) is not int or identifier <= 0:
        raise ShadowMismatch("invalid fact id")
    if category is not None and not isinstance(category, str):
        raise ShadowMismatch("invalid fact category")
    if not isinstance(fact, str) or not fact:
        raise ShadowMismatch("invalid fact")
    if type(updated) is not int:
        raise ShadowMismatch("invalid fact timestamp")
    return {"id": identifier, "category": category, "fact": fact, "updated_at": updated}


def make_snapshot(
    sessions: Iterable[Mapping[str, Any]],
    facts: Iterable[Mapping[str, Any]],
    config: Mapping[str, Any] | None,
    *,
    source_revision: str,
) -> Wave1Snapshot:
    if not isinstance(source_revision, str) or not source_revision:
        raise ShadowMismatch("source revision is required")
    ordered_sessions = tuple(sorted((_session(row) for row in sessions), key=lambda row: row["id"]))
    ordered_facts = tuple(sorted((_fact(row) for row in facts), key=lambda row: row["id"]))
    if len({row["id"] for row in ordered_sessions}) != len(ordered_sessions):
        raise ShadowMismatch("duplicate session")
    if len({row["id"] for row in ordered_facts}) != len(ordered_facts):
        raise ShadowMismatch("duplicate fact id")
    if len({row["fact"] for row in ordered_facts}) != len(ordered_facts):
        raise ShadowMismatch("duplicate fact text")
    config_dict = dict(config) if config is not None else None
    if config_dict is not None and not _safe_metadata(config_dict):
        raise ShadowMismatch("invalid or secret-bearing memory config")
    data = {
        "sessions": ordered_sessions,
        "facts": ordered_facts,
        "config": config_dict,
        "revision": source_revision,
    }
    return Wave1Snapshot(
        sessions=ordered_sessions,
        facts=ordered_facts,
        config=config_dict,
        source_revision=source_revision,
        digest=_digest(data),
    )


def _snapshot_digest(snapshot: Wave1Snapshot) -> str:
    return _digest(
        {
            "sessions": snapshot.sessions,
            "facts": snapshot.facts,
            "config": snapshot.config,
            "revision": snapshot.source_revision,
        }
    )


def stage_shadow(db: FileMemoryDB, snapshot: Wave1Snapshot) -> dict[str, Any]:
    """Stage into a *new empty* isolated file, never the authoritative store."""
    if _snapshot_digest(snapshot) != snapshot.digest:
        raise ShadowMismatch("source snapshot changed")
    if db.items() or db.sequence:
        raise ShadowMismatch("shadow target must be empty")
    for row in snapshot.sessions:
        db.put(SESSION_PREFIX + row["id"], row)
    for row in snapshot.facts:
        db.put(FACT_PREFIX + str(row["id"]), row)
    if snapshot.config is not None:
        db.put(CONFIG_KEY, snapshot.config)
    marker = {
        "schema": 1,
        "sessions": len(snapshot.sessions),
        "facts": len(snapshot.facts),
        "digest": snapshot.digest,
        "source_revision": snapshot.source_revision,
        # Legacy sessions have no owner_id. This stage is never promotable.
        "owner_binding": "unverified",
        "authority": "shadow_only",
    }
    db.put(MARKER_KEY, marker)
    return marker


def verify_shadow(db: FileMemoryDB, snapshot: Wave1Snapshot) -> bool:
    """Verify exact record parity; never pronounce a production cutover safe."""
    if _snapshot_digest(snapshot) != snapshot.digest:
        raise ShadowMismatch("source snapshot changed")
    state = db.items()
    marker = state.get(MARKER_KEY)
    if not isinstance(marker, dict):
        raise ShadowMismatch("uncommitted shadow stage")
    if marker != {
        "schema": 1,
        "sessions": len(snapshot.sessions),
        "facts": len(snapshot.facts),
        "digest": snapshot.digest,
        "source_revision": snapshot.source_revision,
        "owner_binding": "unverified",
        "authority": "shadow_only",
    }:
        raise ShadowMismatch("shadow manifest mismatch")
    expected = {SESSION_PREFIX + row["id"]: row for row in snapshot.sessions}
    expected.update({FACT_PREFIX + str(row["id"]): row for row in snapshot.facts})
    if snapshot.config is not None:
        expected[CONFIG_KEY] = snapshot.config
    expected[MARKER_KEY] = marker
    if state != expected:
        raise ShadowMismatch("shadow data mismatch")
    return True


__all__ = [
    "ShadowMismatch",
    "Wave1Snapshot",
    "make_snapshot",
    "stage_shadow",
    "verify_shadow",
]
