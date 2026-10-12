"""Negative invariants for the read-only Wave 1 SQL reader."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory.wave1_sql_reader import SnapshotReadError, SqlSnapshotReader
from tests.test_wave1_sql_reader import _source

def test_rejects_unsupported_dialect():
    with pytest.raises(ValueError, match="unsupported"):
        SqlSnapshotReader("mysql", lambda: None)


def test_refuses_sql_row_limit(tmp_path, monkeypatch):
    import agent_memory.wave1_sql_reader as module

    monkeypatch.setattr(module, "MAX_ROWS", 1)
    with pytest.raises(SnapshotReadError, match="row limit"):
        SqlSnapshotReader.sqlite(_source(tmp_path)).read()


def test_refuses_ambiguous_memory_config(tmp_path):
    path = _source(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE configs")
        db.execute("CREATE TABLE configs (key TEXT, value TEXT)")
        db.executemany("INSERT INTO configs VALUES (?, ?)", [
            ("memory_config", '{}'),
            ("memory_config", '{}'),
        ])
    with pytest.raises(SnapshotReadError, match="ambiguous"):
        SqlSnapshotReader.sqlite(path).read()


def test_memory_config_optional(tmp_path):
    path = _source(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DELETE FROM configs WHERE key = 'memory_config'")
    assert SqlSnapshotReader.sqlite(path).read().snapshot.config is None


@pytest.mark.parametrize("bad", ["{", "[]"])
def test_memory_config_malformed_or_wrong_shape(tmp_path, bad):
    path = _source(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("UPDATE configs SET value = ? WHERE key = 'memory_config'", (bad,))
    with pytest.raises(SnapshotReadError, match="memory config"):
        SqlSnapshotReader.sqlite(path).read()


def test_duplicate_sql_session_ids_denied_before_staging(tmp_path):
    path = _source(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("DROP TABLE sessions")
        db.execute(
            "CREATE TABLE sessions (id TEXT, status TEXT, metadata_json TEXT, "
            "created_at INTEGER, updated_at INTEGER, completed_at INTEGER)"
        )
        db.execute("INSERT INTO sessions VALUES ('s1','active','{}',1,1,NULL)")
        db.execute("INSERT INTO sessions VALUES ('s1','active','{}',1,1,NULL)")
    with pytest.raises(SnapshotReadError, match="duplicate"):
        SqlSnapshotReader.sqlite(path).read()


def test_owner_binding_invalid_identifier_is_denied():
    from agent_memory.wave1_sql_reader import audit_owners

    with pytest.raises(SnapshotReadError, match="invalid session owner"):
        audit_owners([{"id": 7}], {7: "owner"}, frozenset({"owner"}))
