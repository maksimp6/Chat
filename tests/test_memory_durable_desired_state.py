"""RED-first desired-state acceptance tests for durable file-native Memory DB.

These tests describe the target API; they must not be weakened to fit the
existing process-local implementation. No legacy SQL backup is imported.
"""

import json
import os
import subprocess
import sys

import pytest

from memory_db import Column, MemoryDatabase


def _open_store(path):
    """Open a durable typed database with a stable file path."""
    return MemoryDatabase(path=path)


def _table(db):
    db.create_table("items", [Column("id", str, nullable=False, unique=True)])


def test_acknowledged_write_survives_fresh_process(tmp_path):
    """A successful insert must be visible after process termination."""
    path = tmp_path / "alice.memory"
    script = (
        "from memory_db import Column, MemoryDatabase\n"
        "db = MemoryDatabase(path=__import__('sys').argv[1])\n"
        "db.create_table('items', [Column('id', str, nullable=False, unique=True)])\n"
        "db.insert('items', id='committed')\n"
    )
    subprocess.run([sys.executable, "-c", script, str(path)], check=True)
    reopened = _open_store(path)
    _table(reopened)
    assert reopened.select("items") == [{"id": "committed"}]


def test_rollback_never_becomes_durable(tmp_path):
    """An aborted transaction cannot survive a reopen."""
    path = tmp_path / "alice.memory"
    db = _open_store(path)
    _table(db)
    db.insert("items", id="committed")
    with pytest.raises(RuntimeError, match="abort"):
        with db.transaction():
            db.insert("items", id="rolled-back")
            raise RuntimeError("abort")
    reopened = _open_store(path)
    _table(reopened)
    assert reopened.select("items") == [{"id": "committed"}]


def test_failed_write_does_not_acknowledge_or_corrupt(tmp_path, monkeypatch):
    """Storage failure must not report success or destroy prior commits."""
    path = tmp_path / "alice.memory"
    db = _open_store(path)
    _table(db)
    db.insert("items", id="committed")

    def no_space(*_args, **_kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(db, "_durable_barrier", no_space)
    with pytest.raises(OSError):
        db.insert("items", id="not-committed")
    reopened = _open_store(path)
    _table(reopened)
    assert reopened.select("items") == [{"id": "committed"}]


def test_backup_restores_into_isolated_database(tmp_path):
    """A backup is accepted only if it can be restored and read."""
    source = _open_store(tmp_path / "source.memory")
    _table(source)
    source.insert("items", id="saved")
    backup = tmp_path / "snapshot.backup"
    source.backup(backup)

    target = _open_store(tmp_path / "restored.memory")
    target.restore(backup)
    _table(target)
    assert target.select("items") == [{"id": "saved"}]


def test_torn_tail_never_invents_a_commit(tmp_path):
    """A truncated final record cannot become a successful new write."""
    path = tmp_path / "alice.memory"
    db = _open_store(path)
    _table(db)
    db.insert("items", id="safe")
    with path.open("ab") as stream:
        stream.write(b'{"record":"unfinished"')
        stream.flush()
        os.fsync(stream.fileno())
    reopened = _open_store(path)
    _table(reopened)
    assert reopened.select("items") == [{"id": "safe"}]
