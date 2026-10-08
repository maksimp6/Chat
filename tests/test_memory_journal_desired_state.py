"""Desired-state contract for a future mirror-compatible synchronous journal.

These tests are intentionally RED until the durable engine exposes the
commit metadata and fail-closed durability policy. No network replica yet.
"""
import json

import pytest

from durable_memory_db import DurableMemoryDatabase
from memory_db import Column


def _db(path):
    db = DurableMemoryDatabase(path)
    db.create_table("items", [Column("id", str, nullable=False, unique=True)])
    return db


def test_commits_have_monotonic_sequence_and_hash_chain(tmp_path):
    db = _db(tmp_path / "alice.memory")
    db.insert("items", id="a")
    first = db.last_commit
    db.insert("items", id="b")
    second = db.last_commit
    assert first["version"] == 1
    assert first["epoch"] == second["epoch"]
    assert second["sequence"] == first["sequence"] + 1
    assert second["previous_hash"] == first["hash"]
    assert second["hash"] != first["hash"]


def test_reopen_preserves_commit_identity(tmp_path):
    path = tmp_path / "alice.memory"
    db = _db(path)
    db.insert("items", id="a")
    committed = db.last_commit
    reopened = _db(path)
    assert reopened.last_commit == committed
    assert reopened.select("items") == [{"id": "a"}]


def test_aborted_transaction_does_not_advance_commit_sequence(tmp_path):
    db = _db(tmp_path / "alice.memory")
    db.insert("items", id="a")
    before = db.last_commit
    with pytest.raises(ValueError, match="abort"):
        with db.transaction():
            db.insert("items", id="b")
            raise ValueError("abort")
    assert db.last_commit == before
    reopened = _db(db.path)
    assert reopened.select("items") == [{"id": "a"}]


def test_durability_policy_must_confirm_before_ack(tmp_path):
    db = _db(tmp_path / "alice.memory")
    confirmations = []

    def reject(_commit):
        confirmations.append("attempted")
        raise OSError("mirror unavailable")

    db.set_durability_policy(reject)
    with pytest.raises(OSError, match="mirror unavailable"):
        db.insert("items", id="unacknowledged")
    assert confirmations == ["attempted"]
    assert db.select("items") == []
    assert _db(db.path).select("items") == []


def test_corrupt_committed_journal_is_not_silently_accepted(tmp_path):
    path = tmp_path / "alice.memory"
    db = _db(path)
    db.insert("items", id="safe")
    data = path.read_bytes()
    assert data
    # Corrupt an actual committed byte rather than append an ignorable tail.
    offset = data.find(b"safe")
    assert offset >= 0
    damaged = data[:offset] + b"evil" + data[offset + 4:]
    path.write_bytes(damaged)
    with pytest.raises(Exception):
        _db(path)
