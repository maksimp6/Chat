"""Bounded restart/restore and SQL catch-up parity; shadow-only by construction."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from agent_memory.backup import create_backup, restore_backup
from agent_memory.file_memory_db import FileMemoryDB
from agent_memory.wave1_shadow import ShadowMismatch, stage_shadow, verify_shadow
from agent_memory.wave1_sql_reader import SqlSnapshotReader, SqlSourceSnapshot


@dataclass(frozen=True)
class ShadowParityEvidence:
    status: str
    source_revision: str
    source_digest: str
    owner_status: str
    owner_digest: str
    sessions: int
    facts: int
    attempts: int
    verified_restore: bool


def _read(
    reader: SqlSnapshotReader,
    owner_bindings: Mapping[str, str] | None,
    known_owner_ids: frozenset[str] | None,
) -> SqlSourceSnapshot:
    return reader.read(owner_bindings=owner_bindings, known_owner_ids=known_owner_ids)


def _same(a: SqlSourceSnapshot, b: SqlSourceSnapshot) -> bool:
    return (
        a.snapshot.digest == b.snapshot.digest
        and a.snapshot.source_revision == b.snapshot.source_revision
        and a.owner_audit == b.owner_audit
    )


def shadow_catchup(
    reader: SqlSnapshotReader,
    isolated_root: str | Path,
    *,
    owner_bindings: Mapping[str, str] | None = None,
    known_owner_ids: frozenset[str] | None = None,
    max_attempts: int = 2,
) -> ShadowParityEvidence:
    """Stage fresh generations while SQL remains authoritative, then verify restore.

    Stops on persistent source churn. Returned status explicitly forbids cutover.
    The caller must provision a fresh empty isolation directory.
    """
    if not (1 <= max_attempts <= 3):
        raise ValueError("bounded catch-up requires 1..3 attempts")
    root = Path(isolated_root).expanduser().absolute()
    if root.is_symlink() or root.exists():
        raise ShadowMismatch("isolated shadow root must not already exist")
    root.mkdir(mode=0o700, parents=True, exist_ok=False)
    for attempt in range(1, max_attempts + 1):
        source = _read(reader, owner_bindings, known_owner_ids)
        generation = root / f"generation-{attempt}.memory"
        shadow = FileMemoryDB(generation)
        stage_shadow(shadow, source.snapshot)
        verify_shadow(FileMemoryDB(generation), source.snapshot)
        backup_dir = root / f"generation-{attempt}.backup"
        create_backup(shadow, backup_dir)
        restored = restore_backup(backup_dir, root / f"generation-{attempt}.restored")
        verify_shadow(restored, source.snapshot)
        # A second independent SQL snapshot, after the file I/O, detects changes.
        latest = _read(reader, owner_bindings, known_owner_ids)
        if _same(source, latest):
            return ShadowParityEvidence(
                "SHADOW_ONLY_UNVERIFIED_OWNER",
                source.snapshot.source_revision,
                source.snapshot.digest,
                source.owner_audit.status,
                source.owner_audit.digest,
                len(source.snapshot.sessions),
                len(source.snapshot.facts),
                attempt,
                True,
            )
    raise ShadowMismatch("SQL source changed during bounded shadow catch-up")


__all__ = ["ShadowParityEvidence", "shadow_catchup"]
