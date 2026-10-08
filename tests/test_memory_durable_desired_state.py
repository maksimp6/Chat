"""Acceptance contract for the SQL-free, synchronous MemoryStore.

The new database starts empty: legacy SQL backup import is explicitly out of scope.
"""

import os
import subprocess
import sys

import pytest

from memory_engine import MemoryStore, StoreError


def test_acknowledged_write_survives_fresh_process(tmp_path):
    """A successful write must be visible after process termination."""
    path = tmp_path / "alice.memory"
    script = (
        "from memory_engine import MemoryStore\n"
        "with MemoryStore(__import__('sys').argv[1]) as db:\n"
        "    db.set('items', 'id', 'committed')\n"
    )
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    with MemoryStore(path) as reopened:
        assert reopened.get("items", "id") == "committed"


def test_rollback_never_becomes_durable(tmp_path):
    """An aborted transaction cannot reach the committed journal."""
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "first", "committed")
        with pytest.raises(RuntimeError, match="abort"):
            with db.transaction() as tx:
                tx.set("items", "second", "rolled-back")
                raise RuntimeError("abort")
    with MemoryStore(path) as reopened:
        assert reopened.get("items", "first") == "committed"
        assert reopened.get("items", "second") is None


def test_failed_fsync_does_not_acknowledge_or_publish(tmp_path, monkeypatch):
    """An uncertain failed write blocks later writes; no success is returned."""
    import memory_engine.store as store_module

    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "first", "committed")

        def no_space(_fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(store_module.os, "fsync", no_space)
        with pytest.raises(StoreError, match="commit failed"):
            db.set("items", "second", "unacknowledged")
        assert db.get("items", "first") == "committed"
        assert db.get("items", "second") is None
        with pytest.raises(StoreError, match="recovery required"):
            db.set("items", "third", "forbidden")


def test_backup_restores_into_isolated_database(tmp_path):
    """Backup acceptance requires an isolated restore and digest match."""
    origin = tmp_path / "source.memory"
    backup = tmp_path / "snapshot.backup"
    restored = tmp_path / "restored.memory"
    with MemoryStore(origin) as source:
        source.set("items", "id", "saved")
        expected = source.last_commit
        source.backup(backup)
    with MemoryStore(restored) as target:
        target.restore(backup)
        assert target.get("items", "id") == "saved"
        assert target.last_commit == expected


def test_truncated_tail_is_never_silently_accepted(tmp_path):
    """Corrupt tails must fail closed until recovery is explicitly validated."""
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "id", "safe")
    with path.open("ab") as stream:
        stream.write(b'{"record":"unfinished"')
        stream.flush()
        os.fsync(stream.fileno())
    with pytest.raises(StoreError, match="incomplete journal tail"):
        MemoryStore(path)
