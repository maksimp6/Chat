"""Checkpoint and crash-consistency regressions for the accepted MemoryStore."""

import os
import errno
import hashlib
import json
import signal
import stat
import subprocess
import sys
import tempfile

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
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
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
        with pytest.raises(StoreError, match="frame exceeds size"):
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


def test_oversized_checkpoint_preserves_journal_and_writable_store(tmp_path):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        for index in range(3):
            db.set(str(index), "x" * 400_000)
            db.commit()
        original = path.read_bytes()
        confirmed = db.last_commit
        with pytest.raises(StoreError, match="checkpoint exceeds"):
            db.checkpoint()
        assert path.read_bytes() == original
        assert db.last_commit == confirmed
        db.set("after", "ok")
        assert db.commit() == 4
    with MemoryStore(path) as recovered:
        assert recovered.get("2") == "x" * 400_000
        assert recovered.get("after") == "ok"


@pytest.mark.parametrize(
    "phase", ["create", "short_write", "file_fsync", "replace", "directory_fsync"]
)
def test_checkpoint_io_failure_blocks_writes_until_reopen(tmp_path, monkeypatch, phase):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        db.set("saved", {"count": 1})
        db.commit()
        original = path.read_bytes()
        confirmed = db.last_commit
        with monkeypatch.context() as patch:
            real_fsync = os.fsync

            def failing_fsync(fd):
                is_directory = stat.S_ISDIR(os.fstat(fd).st_mode)
                if is_directory == (phase == "directory_fsync"):
                    raise OSError(errno.ENOSPC, "injected checkpoint sync failure")
                real_fsync(fd)

            def failing_replace(*args):
                raise OSError(errno.EROFS, "injected checkpoint replace failure")

            def failing_create(*args, **kwargs):
                raise OSError(errno.ENOSPC, "injected checkpoint creation failure")

            real_fdopen = os.fdopen

            class ShortStream:
                def __init__(self, stream):
                    self.stream = stream

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    self.stream.close()

                def write(self, frame):
                    return self.stream.write(frame[:10])

            if phase == "create":
                patch.setattr(tempfile, "mkstemp", failing_create)
            elif phase == "short_write":
                patch.setattr(os, "fdopen", lambda *args: ShortStream(real_fdopen(*args)))
            elif phase == "replace":
                patch.setattr(os, "replace", failing_replace)
            else:
                patch.setattr(os, "fsync", failing_fsync)
            with pytest.raises(StoreError, match="checkpoint failed"):
                db.checkpoint()
        assert db.last_commit == confirmed
        assert db.get("saved") == {"count": 1}
        if phase != "directory_fsync":
            assert path.read_bytes() == original
        assert not list(tmp_path.glob(".alice-checkpoint-*"))
        with pytest.raises(StoreError, match="recovery required"):
            db.set("later", 2)
        with pytest.raises(StoreError, match="recovery required"):
            db.commit()
    with MemoryStore(path) as recovered:
        assert recovered.get("saved") == {"count": 1}
        assert recovered.last_commit.sequence == 1
        recovered.set("later", 2)
        assert recovered.commit() == 2


def test_checkpoint_verification_failure_preserves_original(tmp_path, monkeypatch):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        db.set("saved", 1)
        db.commit()
        original = path.read_bytes()
        with monkeypatch.context() as patch:
            patch.setattr(db._engine, "_inspect_log", lambda path: ({}, 0, "0" * 64))
            with pytest.raises(StoreError, match="verification failed"):
                db.checkpoint()
        assert path.read_bytes() == original
        assert not list(tmp_path.glob(".alice-checkpoint-*"))
        db.set("later", 2)
        assert db.commit() == 2


@pytest.mark.parametrize(
    "field,value",
    [
        ("state", []),
        ("state", {"values": []}),
        ("state", {"values": {"x": float("nan")}}),
        ("seq", 0),
        ("seq", True),
        ("changes", [{"op": "delete", "namespace": "values", "key": "x"}]),
    ],
)
def test_invalid_checkpoint_with_valid_checksum_fails_closed(tmp_path, field, value):
    path = tmp_path / "state.memory"
    payload = {
        "format": 1,
        "seq": 3,
        "changes": [],
        "checkpoint": True,
        "state": {"values": {"x": 1}},
    }
    payload[field] = value
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(bytes(32) + encoded).hexdigest()
    path.write_text(json.dumps({"payload": payload, "digest": digest}) + "\n")
    original = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt"):
        MemoryStore(path)
    assert path.read_bytes() == original


def test_checkpoint_remains_first_and_preserves_writer_lock(tmp_path):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        assert db.checkpoint() == 0
        assert not path.exists()
        db.set("saved", 1)
        db.commit()
        with db.transaction():
            with pytest.raises(StoreError, match="idle"):
                db.checkpoint()
        db.checkpoint()
        checkpoint = path.read_bytes()
        assert db.checkpoint() == 1
        assert path.read_bytes() == checkpoint
        with pytest.raises(StoreError, match="writer"):
            MemoryStore(path)
        db.set("next", 2)
        db.commit()
    # A checkpoint after another committed frame would reset history illegally.
    record = json.loads(checkpoint)
    previous_digest = json.loads(path.read_bytes().splitlines()[-1])["digest"]
    record["payload"]["seq"] = 3
    encoded = json.dumps(
        record["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    record["digest"] = hashlib.sha256(bytes.fromhex(previous_digest) + encoded).hexdigest()
    with path.open("ab") as journal:
        journal.write(json.dumps(record).encode() + b"\n")
    with pytest.raises(StoreError, match="corrupt"):
        MemoryStore(path)


def test_checkpoint_validator_rejects_non_string_key_before_publication():
    from memory_engine.store import _validate_checkpoint

    with pytest.raises(ValueError, match="checkpoint key"):
        _validate_checkpoint({"seq": 1, "changes": [], "state": {"values": {1: "invalid"}}})
