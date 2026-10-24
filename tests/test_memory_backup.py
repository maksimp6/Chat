"""Verify native Kristall backup/restore and corruption rejection."""

import pytest

from kristall import Kristall, KristallError


def test_native_backup_survives_reopen_and_restore(tmp_path):
    source = tmp_path / "live.memory"
    backup = tmp_path / "backup.memory"
    restored_path = tmp_path / "restored.memory"

    with Kristall(source) as db:
        with db.transaction() as tx:
            tx.set("identity", {"id": "user-one"})
            tx.set("ownership", {"conversation-a": "user-one"})
        before = db.last_commit
        db.backup(backup)

    with Kristall(restored_path) as restored:
        restored.restore(backup)
        assert restored.last_commit == before
        assert restored.get("identity") == {"id": "user-one"}
        assert restored.get("ownership") == {"conversation-a": "user-one"}

    with Kristall(restored_path) as reopened:
        assert reopened.get("identity") == {"id": "user-one"}


def test_native_backup_rejects_modified_payload(tmp_path):
    backup = tmp_path / "backup.memory"
    with Kristall(tmp_path / "original.memory") as db:
        db.set("private", {"value": "safe"})
        db.commit()
        db.backup(backup)
    backup.write_bytes(backup.read_bytes().replace(b"safe", b"evil"))
    with Kristall(tmp_path / "restored.memory") as candidate:
        with pytest.raises(KristallError, match="corrupt committed journal"):
            candidate.restore(backup)
        assert candidate.get("private") is None


def test_native_backup_never_overwrites_existing_destination(tmp_path):
    archive = tmp_path / "archive.memory"
    archive.write_bytes(b"other")
    with Kristall(tmp_path / "live.memory") as store:
        with pytest.raises(KristallError, match="backup destination must be new"):
            store.backup(archive)
    assert archive.read_bytes() == b"other"


def test_native_transaction_deletes_only_selected_key(tmp_path):
    path = tmp_path / "live.memory"
    with Kristall(path) as store:
        with store.transaction() as tx:
            tx.set("identity", "one")
            tx.set("ownership", "two")
        with store.transaction() as tx:
            tx.delete("ownership")
        assert store.get("ownership") is None
        assert store.get("identity") == "one"
    with Kristall(path) as reopened:
        assert reopened.get("identity") == "one"
        assert reopened.get("ownership") is None
