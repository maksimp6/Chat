"""Verify native MemoryStore backup/restore and corruption rejection."""

import pytest

from memory_engine import MemoryStore, StoreError


def test_native_backup_survives_reopen_and_restore(tmp_path):
    source = tmp_path / "live.memory"
    backup = tmp_path / "backup.memory"
    restored_path = tmp_path / "restored.memory"

    with MemoryStore(source) as db:
        with db.transaction() as tx:
            tx.set("identity", {"id": "user-one"})
            tx.set("ownership", {"conversation-a": "user-one"})
        before = db.last_commit
        db.backup(backup)

    with MemoryStore(restored_path) as restored:
        restored.restore(backup)
        assert restored.last_commit == before
        assert restored.get("identity") == {"id": "user-one"}
        assert restored.get("ownership") == {"conversation-a": "user-one"}

    with MemoryStore(restored_path) as reopened:
        assert reopened.get("identity") == {"id": "user-one"}


def test_native_backup_rejects_modified_payload(tmp_path):
    backup = tmp_path / "backup.memory"
    with MemoryStore(tmp_path / "original.memory") as db:
        db.set("private", {"value": "safe"})
        db.commit()
        db.backup(backup)
    backup.write_bytes(backup.read_bytes().replace(b"safe", b"evil"))
    with MemoryStore(tmp_path / "restored.memory") as candidate:
        with pytest.raises(StoreError, match="corrupt committed journal"):
            candidate.restore(backup)
        assert candidate.get("private") is None


def test_native_backup_never_overwrites_existing_destination(tmp_path):
    archive = tmp_path / "archive.memory"
    archive.write_bytes(b"other")
    with MemoryStore(tmp_path / "live.memory") as store:
        with pytest.raises(StoreError, match="backup destination must be new"):
            store.backup(archive)
    assert archive.read_bytes() == b"other"


def test_native_transaction_deletes_only_selected_key(tmp_path):
    path = tmp_path / "live.memory"
    with MemoryStore(path) as store:
        with store.transaction() as tx:
            tx.set("identity", "one")
            tx.set("ownership", "two")
        with store.transaction() as tx:
            tx.delete("ownership")
        assert store.get("ownership") is None
        assert store.get("identity") == "one"
    with MemoryStore(path) as reopened:
        assert reopened.get("identity") == "one"
        assert reopened.get("ownership") is None
