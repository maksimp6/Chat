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
            real_inspect = db._engine._inspect_log
            patch.setattr(
                db._engine,
                "_inspect_log",
                lambda candidate: (
                    real_inspect(candidate) if candidate == path else ({}, 0, "0" * 64)
                ),
            )
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


@pytest.mark.parametrize("phase", ["before_replace", "after_replace", "directory_fsync"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_checkpoint_never_allows_a_stale_digest_commit(
    tmp_path, monkeypatch, phase, interruption
):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        db.set("saved", {"count": 1})
        db.commit()
        confirmed = db.last_commit
        real_replace, real_fsync = os.replace, os.fsync
        with monkeypatch.context() as patch:

            def interrupted_replace(source, destination):
                if phase == "before_replace":
                    raise interruption("injected cancellation")
                real_replace(source, destination)
                raise interruption("injected cancellation")

            def interrupted_fsync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise interruption("injected cancellation")
                real_fsync(fd)

            if phase == "directory_fsync":
                patch.setattr(os, "fsync", interrupted_fsync)
            else:
                patch.setattr(os, "replace", interrupted_replace)
            with pytest.raises(interruption):
                db.checkpoint()
        assert db.get("saved") == {"count": 1}
        assert db.last_commit == confirmed
        with pytest.raises(StoreError, match="recovery required"):
            db.set("next", 2)
        with pytest.raises(StoreError, match="recovery required"):
            db.commit()
        assert not list(tmp_path.glob(".alice-checkpoint-*"))
    with MemoryStore(path) as recovered:
        assert recovered.get("saved") == {"count": 1}
        assert recovered.last_commit.sequence == 1
        recovered.set("next", 2)
        assert recovered.commit() == 2
    with MemoryStore(path) as reopened:
        assert reopened.get("saved") == {"count": 1}
        assert reopened.get("next") == 2


@pytest.mark.parametrize("damage", ["checksum", "truncated", "missing", "valid_other_state"])
def test_checkpoint_preserves_changed_source_evidence_and_blocks_writer(tmp_path, damage):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as db:
        db.set("saved", "confirmed")
        db.commit()
        confirmed = db.last_commit
        if damage == "missing":
            path.unlink()
            evidence = None
        else:
            raw = path.read_bytes()
            if damage == "checksum":
                evidence = raw.replace(b"confirmed", b"corrupted")
            elif damage == "truncated":
                evidence = raw[:-5]
            else:
                record = json.loads(raw)
                record["payload"]["changes"][0]["value"] = "other"
                encoded = json.dumps(
                    record["payload"], sort_keys=True, separators=(",", ":"), ensure_ascii=False
                ).encode()
                record["digest"] = hashlib.sha256(bytes(32) + encoded).hexdigest()
                evidence = json.dumps(record).encode() + b"\n"
            path.write_bytes(evidence)
        with pytest.raises(StoreError, match="source.*recovery required"):
            db.checkpoint()
        assert (path.read_bytes() if path.exists() else None) == evidence
        assert not list(tmp_path.glob(".alice-checkpoint-*"))
        assert db.last_commit == confirmed
        assert db.get("saved") == "confirmed"
        with pytest.raises(StoreError, match="recovery required"):
            db.set("later", 2)
        with pytest.raises(StoreError, match="recovery required"):
            db.commit()


@pytest.mark.parametrize("checkpointed", [False, True])
@pytest.mark.parametrize("phase", ["directory_fsync", "after_copy"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_restore_never_allows_an_empty_state_append(
    tmp_path, monkeypatch, checkpointed, phase, interruption
):
    backup = tmp_path / "backup.memory"
    target = tmp_path / "target.memory"
    with MemoryStore(tmp_path / "source.memory") as source:
        source.set("saved", {"count": 1})
        source.commit()
        if checkpointed:
            source.checkpoint()
        source.backup(backup)
    with MemoryStore(target) as restored:
        real_fsync = os.fsync
        real_copy = restored._engine._copy_verified_file
        with monkeypatch.context() as patch:

            def interrupted_fsync(fd):
                if stat.S_ISDIR(os.fstat(fd).st_mode):
                    raise interruption("injected restore cancellation")
                real_fsync(fd)

            def interrupted_copy(*args, **kwargs):
                real_copy(*args, **kwargs)
                raise interruption("injected restore cancellation")

            if phase == "directory_fsync":
                patch.setattr(os, "fsync", interrupted_fsync)
            else:
                patch.setattr(restored._engine, "_copy_verified_file", interrupted_copy)
            with pytest.raises(interruption):
                restored.restore(backup)
        assert restored.last_commit.sequence == 0
        assert restored.get("saved") is None
        assert not list(tmp_path.glob(".alice-copy-*"))
        with pytest.raises(StoreError, match="recovery required"):
            restored.set("next", 2)
        with pytest.raises(StoreError, match="recovery required"):
            restored.commit()
    with MemoryStore(target) as recovered:
        assert recovered.get("saved") == {"count": 1}
        assert recovered.last_commit.sequence == 1
        recovered.set("next", 2)
        assert recovered.commit() == 2
    with MemoryStore(target) as reopened:
        assert reopened.get("saved") == {"count": 1}
        assert reopened.get("next") == 2


@pytest.mark.parametrize("interruption", [KeyboardInterrupt, MemoryError])
def test_interrupted_restore_ram_publication_never_exposes_partial_metadata(
    tmp_path, monkeypatch, interruption
):
    backup = tmp_path / "backup.memory"
    target = tmp_path / "target.memory"
    with MemoryStore(tmp_path / "source.memory") as source:
        source.set("saved", 1)
        source.commit()
        source.checkpoint()
        source.backup(backup)
    with MemoryStore(target) as restored:
        engine = restored._engine
        cls = type(engine)
        real_setattr = cls.__setattr__

        def interrupted_setattr(instance, name, value):
            if instance is engine and name == "_sequence":
                raise interruption("injected RAM publication failure")
            real_setattr(instance, name, value)

        with monkeypatch.context() as patch:
            patch.setattr(cls, "__setattr__", interrupted_setattr)
            with pytest.raises(interruption):
                restored.restore(backup)
        assert restored.info().last_commit is None
        with pytest.raises(StoreError, match="RAM publication"):
            restored.last_commit
        with pytest.raises(StoreError, match="RAM publication"):
            restored.get("saved")
        with pytest.raises(StoreError, match="recovery required"):
            restored.commit()
    with MemoryStore(target) as recovered:
        assert recovered.get("saved") == 1
        assert recovered.last_commit.sequence == 1
        recovered.set("next", 2)
        assert recovered.commit() == 2
