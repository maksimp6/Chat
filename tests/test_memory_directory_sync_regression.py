"""Failure injection for backup/restore directory durability boundaries."""
import errno
import os
import pytest
from memory_engine import MemoryStore, StoreError


def test_restore_directory_fsync_failure_fails_closed(tmp_path, monkeypatch):
    source = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    target = tmp_path / "target.memory"
    with MemoryStore(source) as db:
        db.set("value", 42)
        db.commit()
        db.backup(backup)
    with MemoryStore(target) as db:
        original = os.fsync
        def injected(fd):
            if os.path.isdir(f"/proc/self/fd/{fd}"):
                raise OSError(errno.EIO, "injected directory fsync failure")
            return original(fd)
        monkeypatch.setattr(os, "fsync", injected)
        with pytest.raises(StoreError, match="recovery required"):
            db.restore(backup)
        with pytest.raises(StoreError, match="recovery required"):
            db.commit()
    # A published path may exist despite the failed directory sync.
    assert target.exists()


def test_backup_directory_fsync_failure_does_not_report_success(tmp_path, monkeypatch):
    source = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    with MemoryStore(source) as db:
        db.set("value", 42)
        db.commit()
        original = os.fsync
        def injected(fd):
            if os.path.isdir(f"/proc/self/fd/{fd}"):
                raise OSError(errno.EIO, "injected directory fsync failure")
            return original(fd)
        monkeypatch.setattr(os, "fsync", injected)
        with pytest.raises(OSError, match="injected directory"):
            db.backup(backup)
    assert backup.exists()
