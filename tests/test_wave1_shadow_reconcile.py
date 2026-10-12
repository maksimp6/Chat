"""Shadow catch-up with real synthetic read-only SQLite snapshots and isolated backups."""

from __future__ import annotations

import sqlite3

import pytest

from agent_memory.wave1_shadow import ShadowMismatch, verify_shadow
from agent_memory.wave1_shadow_reconcile import shadow_catchup
from agent_memory.wave1_sql_reader import SqlSnapshotReader
from agent_memory.file_memory_db import FileMemoryDB
from tests.test_wave1_sql_reader import _source


def test_shadow_reconcile_reopens_restores_and_does_not_modify_sql(tmp_path):
    source = _source(tmp_path)
    with sqlite3.connect(source) as db:
        original = tuple(db.iterdump())
    reader = SqlSnapshotReader.sqlite(source)
    evidence = shadow_catchup(
        reader,
        tmp_path / "isolated",
        owner_bindings={"s1": "owner", "s2": "owner"},
        known_owner_ids=frozenset({"owner"}),
    )
    assert evidence.status == "SHADOW_ONLY_UNVERIFIED_OWNER"
    assert evidence.owner_status == "externally_asserted_unverified"
    assert evidence.verified_restore
    assert evidence.sessions == 2 and evidence.facts == 1
    assert evidence.attempts == 1
    shadow = FileMemoryDB(tmp_path / "isolated/generation-1.memory")
    assert verify_shadow(shadow, reader.read().snapshot)
    with sqlite3.connect(source) as db:
        assert tuple(db.iterdump()) == original


def test_shadow_catches_up_to_second_revision(tmp_path):
    source = _source(tmp_path)
    reader = SqlSnapshotReader.sqlite(source)
    real_read = reader.read
    calls = 0

    def changing_read(**kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            with sqlite3.connect(source) as db:
                db.execute("INSERT INTO global_memory VALUES (2,'general','new fact',6)")
        return real_read(**kwargs)

    reader.read = changing_read
    result = shadow_catchup(reader, tmp_path / "isolated")
    assert result.attempts == 2
    assert result.facts == 2
    assert result.status == "SHADOW_ONLY_UNVERIFIED_OWNER"
    assert (
        FileMemoryDB(tmp_path / "isolated/generation-1.memory").get("shadow:wave1:fact:2") is None
    )
    assert (
        FileMemoryDB(tmp_path / "isolated/generation-2.memory").get("shadow:wave1:fact:2")
        is not None
    )


def test_continuous_sql_churn_fails_closed_without_cutover(tmp_path):
    source = _source(tmp_path)
    reader = SqlSnapshotReader.sqlite(source)
    real_read = reader.read
    calls = 0

    def changing_read(**kwargs):
        nonlocal calls
        calls += 1
        with sqlite3.connect(source) as db:
            db.execute("UPDATE sessions SET updated_at = updated_at + 1 WHERE id = 's1'")
        return real_read(**kwargs)

    reader.read = changing_read
    with pytest.raises(ShadowMismatch, match="changed"):
        shadow_catchup(reader, tmp_path / "isolated", max_attempts=2)
    assert (tmp_path / "isolated/generation-2.memory").exists()


def test_no_target_reuse_or_existing_authoritative_file(tmp_path):
    source = _source(tmp_path)
    reader = SqlSnapshotReader.sqlite(source)
    root = tmp_path / "existing"
    root.mkdir()
    with pytest.raises(ShadowMismatch, match="must not already exist"):
        shadow_catchup(reader, root)


def test_ownership_mismatch_does_not_stage_any_file(tmp_path):
    source = _source(tmp_path)
    root = tmp_path / "isolated"
    with pytest.raises(Exception, match="owner"):
        shadow_catchup(
            SqlSnapshotReader.sqlite(source),
            root,
            owner_bindings={"s1": "owner"},
            known_owner_ids=frozenset({"owner"}),
        )
    assert not (root / "generation-1.memory").exists()


def test_bounded_catchup_attempts(tmp_path):
    with pytest.raises(ValueError, match="1..3"):
        shadow_catchup(
            SqlSnapshotReader.sqlite(_source(tmp_path)), tmp_path / "isolated", max_attempts=0
        )
