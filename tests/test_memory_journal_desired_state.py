"""Desired-state journal checks on the single authoritative MemoryStore."""

import hashlib
import json

import pytest

from memory_engine import MemoryStore, StoreError


def test_commits_have_monotonic_sequence_and_hash_chain(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "a", 1)
        first = db.last_commit
        db.set("items", "b", 2)
        second = db.last_commit
        assert first.sequence == 1
        assert second.sequence == first.sequence + 1
        assert first.digest != second.digest

    records = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(records) == 2
    first_bytes = json.dumps(
        records[0]["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    first_hash = hashlib.sha256(bytes(32) + first_bytes).hexdigest()
    second_bytes = json.dumps(
        records[1]["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    second_hash = hashlib.sha256(bytes.fromhex(first_hash) + second_bytes).hexdigest()
    assert records[0]["digest"] == first_hash
    assert records[1]["digest"] == second_hash == second.digest


def test_reopen_preserves_commit_identity(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "a", 1)
        committed = db.last_commit
    with MemoryStore(path) as reopened:
        assert reopened.last_commit == committed
        assert reopened.get("items", "a") == 1


def test_aborted_transaction_does_not_advance_commit_sequence(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "a", 1)
        before = db.last_commit
        with pytest.raises(ValueError, match="abort"):
            with db.transaction() as tx:
                tx.set("items", "b", 2)
                raise ValueError("abort")
        assert db.last_commit == before
    with MemoryStore(path) as reopened:
        assert reopened.get("items", "b") is None
        assert reopened.get("items", "a") == 1


def test_corrupt_committed_journal_is_not_silently_accepted(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "id", "safe")
    data = path.read_bytes()
    offset = data.find(b"safe")
    assert offset >= 0
    path.write_bytes(data[:offset] + b"evil" + data[offset + 4 :])
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
