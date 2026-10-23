"""Failure injection for backup/restore directory durability boundaries."""

import errno
import os

import pytest

from kristall import Kristall as MemoryStore, KristallError as StoreError


def _directory_fsync_failure(monkeypatch):
    original = os.fsync

    def injected(fd):
        if os.path.isdir(f"/proc/self/fd/{fd}"):
            raise OSError(errno.EIO, "injected directory fsync failure")
        return original(fd)

    monkeypatch.setattr(os, "fsync", injected)


def test_restore_directory_fsync_failure_fails_closed(tmp_path, monkeypatch):
    source = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    target = tmp_path / "target.memory"
    with MemoryStore(source) as db:
        db.set("value", 42)
        db.commit()
        db.backup(backup)
    with MemoryStore(target) as db:
        _directory_fsync_failure(monkeypatch)
        with pytest.raises(StoreError, match="recovery required"):
            db.restore(backup)
        with pytest.raises(StoreError, match="recovery required"):
            db.commit()
    assert target.exists()


def test_backup_directory_fsync_failure_does_not_report_success(tmp_path, monkeypatch):
    source = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    with MemoryStore(source) as db:
        db.set("value", 42)
        db.commit()
        _directory_fsync_failure(monkeypatch)
        with pytest.raises(OSError, match="injected directory"):
            db.backup(backup)
    assert backup.exists()
