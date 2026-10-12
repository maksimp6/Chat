"""Checkpoint and crash-consistency regressions for the accepted MemoryStore."""

import os
import signal
import subprocess
import sys

import pytest

from memory_engine import MemoryStore, StoreError


def test_long_journal_compacts_and_reopens_with_monotonic_sequence(tmp_path):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        for index in range(250):
            db.set("value", index)
            assert db.commit() == index + 1
        before = path.stat().st_size
        assert db.checkpoint() == 250
        assert path.stat().st_size < before
        assert db.get("value") == 249
        db.set("next", "ok")
        assert db.commit() == 251
    with MemoryStore(path) as reopened:
        assert reopened.get("value") == 249
        assert reopened.get("next") == "ok"
        assert reopened.last_commit.sequence == 251


@pytest.mark.parametrize("phase", ["before_replace", "after_replace"])
def test_sigkill_during_checkpoint_preserves_acknowledged_state(tmp_path, phase):
    path = tmp_path / "state.memory"
    script = """
import os, signal, sys
from memory_engine import MemoryStore
path, phase = sys.argv[1:]
with MemoryStore(path) as db:
    for i in range(80):
        db.set('value', i); db.commit()
    original = os.replace
    def interrupt(src, dst):
        if phase == 'before_replace':
            os.kill(os.getpid(), signal.SIGKILL)
        original(src, dst)
        os.kill(os.getpid(), signal.SIGKILL)
    os.replace = interrupt
    db.checkpoint()
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), phase],
        capture_output=True, text=True, timeout=15, check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    with MemoryStore(path) as recovered:
        assert recovered.get("value") == 79
        assert recovered.last_commit.sequence == 80
        recovered.set("after", "restart")
        assert recovered.commit() == 81


def test_checkpoint_rejects_pending_and_oversized_snapshot(tmp_path):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        db.set("value", "confirmed")
        db.commit()
        db.set("pending", 1)
        with pytest.raises(StoreError, match="staged"):
            db.checkpoint()
        assert db.get("pending") == 1
    with MemoryStore(path) as db:
        db.set("big", "x" * (1024 * 1024))
        with pytest.raises(StoreError, match="frame size"):
            db.commit()
        assert db.get("value") == "confirmed"


def test_checkpoint_backup_restore_roundtrip(tmp_path):
    path = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    with MemoryStore(path) as db:
        for i in range(40):
            db.set("count", i)
            db.commit()
        db.checkpoint()
        db.backup(backup)
    with MemoryStore(tmp_path / "target.memory") as target:
        target.restore(backup)
        assert target.get("count") == 39
        assert target.last_commit.sequence == 40
