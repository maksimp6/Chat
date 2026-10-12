"""Wave 1 shadow rehearsal uses synthetic SQLite only, without cutover."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory.file_memory_db import FileMemoryDB
from agent_memory.wave1_shadow import (
    ShadowMismatch,
    make_snapshot,
    stage_shadow,
    verify_shadow,
)


def _sql_snapshot():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(
        """
        CREATE TABLE sessions (id TEXT PRIMARY KEY, status TEXT,
          metadata_json TEXT, created_at INTEGER, updated_at INTEGER, completed_at INTEGER);
        CREATE TABLE global_memory (id INTEGER PRIMARY KEY, category TEXT,
          fact TEXT UNIQUE, updated_at INTEGER);
        INSERT INTO sessions VALUES ('s1', 'active', '{"profile_id":"developer"}', 1, 1, NULL);
        INSERT INTO sessions VALUES ('s2', 'completed', '{}', 2, 3, 3);
        INSERT INTO global_memory VALUES (1, 'architecture', 'Use file memory', 4);
        """
    )
    return db


def _snapshot(db, revision="rev1"):
    return make_snapshot(
        [dict(row) for row in db.execute("SELECT * FROM sessions")],
        [dict(row) for row in db.execute("SELECT * FROM global_memory")],
        {"enabled": True, "max_context_facts": 15},
        source_revision=revision,
    )


def test_shadow_rehearsal_reopens_and_preserves_source(tmp_path):
    sql = _sql_snapshot()
    before = list(sql.iterdump())
    snapshot = _snapshot(sql)
    target = tmp_path / "isolated" / "alice.memory"
    marker = stage_shadow(FileMemoryDB(target), snapshot)
    reopened = FileMemoryDB(target)
    assert verify_shadow(reopened, snapshot)
    assert marker["owner_binding"] == "unverified"
    assert marker["authority"] == "shadow_only"
    assert list(sql.iterdump()) == before
    sql.close()


def test_source_drift_fails_parity(tmp_path):
    sql = _sql_snapshot()
    snap = _snapshot(sql)
    shadow = FileMemoryDB(tmp_path / "stage.memory")
    stage_shadow(shadow, snap)
    sql.execute("UPDATE sessions SET updated_at = 17 WHERE id = 's1'")
    assert _snapshot(sql).digest != snap.digest
    with pytest.raises(ShadowMismatch):
        verify_shadow(shadow, _snapshot(sql))
    sql.close()


def test_corrupt_shadow_row_fails_parity(tmp_path):
    sql = _sql_snapshot()
    snap = _snapshot(sql)
    shadow = FileMemoryDB(tmp_path / "stage.memory")
    stage_shadow(shadow, snap)
    shadow.put("shadow:wave1:session:s1", {"id": "s1", "status": "cancelled"})
    with pytest.raises(ShadowMismatch):
        verify_shadow(shadow, snap)
    sql.close()


def test_partial_staging_never_accepted(tmp_path, monkeypatch):
    snap = _snapshot(_sql_snapshot())
    db = FileMemoryDB(tmp_path / "stage.memory")
    real_put = db.put
    calls = 0

    def crash_on_second(key, value):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated ENOSPC")
        return real_put(key, value)

    monkeypatch.setattr(db, "put", crash_on_second)
    with pytest.raises(OSError):
        stage_shadow(db, snap)
    monkeypatch.undo()
    with pytest.raises(ShadowMismatch, match="uncommitted"):
        verify_shadow(FileMemoryDB(db.path), snap)
    with pytest.raises(ShadowMismatch, match="empty"):
        stage_shadow(FileMemoryDB(db.path), snap)


def test_duplicate_and_secret_bearing_source_rejected():
    sql = _sql_snapshot()
    valid = [dict(row) for row in sql.execute("SELECT * FROM sessions")]
    facts = [dict(row) for row in sql.execute("SELECT * FROM global_memory")]
    with pytest.raises(ShadowMismatch, match="duplicate session"):
        make_snapshot(valid + [valid[0]], facts, None, source_revision="rev")
    valid[0]["metadata_json"] = '{"access_token":"sensitive"}'
    with pytest.raises(ShadowMismatch, match="secret-store"):
        make_snapshot(valid, facts, None, source_revision="rev")
    sql.close()


def test_missing_snapshot_revision_rejected():
    with pytest.raises(ShadowMismatch, match="revision"):
        make_snapshot([], [], None, source_revision="")


def test_mutated_snapshot_object_rejected(tmp_path):
    sql = _sql_snapshot()
    snapshot = _snapshot(sql)
    snapshot.sessions[0]["updated_at"] = 100
    with pytest.raises(ShadowMismatch, match="snapshot changed"):
        stage_shadow(FileMemoryDB(tmp_path / "new.memory"), snapshot)
    sql.close()
