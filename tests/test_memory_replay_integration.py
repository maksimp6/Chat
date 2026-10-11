"""Fault-injection replay regressions ported to the format:1 #1123 journal.

Based on #1083 invariants; does not import the incompatible legacy _JournalEngine.
"""

import json
import os
import signal
import subprocess
import sys

import pytest

from memory_engine import MemoryStore, StoreError
from memory_engine.store import _JournalStore


def test_truncated_frame_fails_closed_without_mutation(tmp_path):
    path = tmp_path / "journal.memory"
    with MemoryStore(path) as store:
        store.set("stable", {"n": 1})
        assert store.commit() == 1
    with path.open("ab") as out:
        out.write(b'{"payload":')
    before = path.read_bytes()
    with pytest.raises(StoreError, match="incomplete journal tail"):
        MemoryStore(path)
    assert path.read_bytes() == before


def test_semantically_invalid_hashed_frame_never_publishes_partial_changes(tmp_path):
    path = tmp_path / "journal.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        store.commit()
    state, seq, digest = _JournalStore._inspect_log(path)
    payload = {
        "format": 1,
        "seq": seq + 1,
        "changes": [
            {"op": "set", "namespace": "values", "key": "stable", "value": 2},
            {"op": "invalid", "namespace": "values", "key": "other"},
        ],
    }
    import hashlib

    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    frame = (
        json.dumps(
            {
                "payload": payload,
                "digest": hashlib.sha256(bytes.fromhex(digest) + encoded).hexdigest(),
            }
        ).encode()
        + b"\n"
    )
    with path.open("ab") as out:
        out.write(frame)
    before = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
    assert path.read_bytes() == before
    assert state["values"]["stable"] == 1


@pytest.mark.parametrize("boundary", ["before_append", "after_append", "after_ack"])
def test_sigkill_commit_replay_e2e(tmp_path, boundary):
    path = tmp_path / "crash.memory"
    code = """
import os,signal,sys
from memory_engine import MemoryStore
from memory_engine.store import _JournalStore
db=MemoryStore(sys.argv[1])
db.set('stable',1);db.commit()
original=_JournalStore._append_frame
def injected(self,frame):
    if sys.argv[2]=='before_append': os.kill(os.getpid(),signal.SIGKILL)
    original(self,frame)
    if sys.argv[2]=='after_append': os.kill(os.getpid(),signal.SIGKILL)
_JournalStore._append_frame=injected
db.set('next',2);db.commit()
print('ACK',flush=True)
os.kill(os.getpid(),signal.SIGKILL)
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(path), boundary],
        capture_output=True,
        check=False,
        text=True,
        timeout=15,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert ("ACK" in result.stdout) == (boundary == "after_ack")
    with MemoryStore(path) as recovered:
        assert recovered.get("stable") == 1
        assert recovered.get("next") == (None if boundary == "before_append" else 2)


def test_fsync_failure_fences_writer_and_preserves_acknowledged_state(tmp_path, monkeypatch):
    """An ambiguous fsync failure must not publish or permit another commit."""
    path = tmp_path / "fsync.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        assert store.commit() == 1
        store.set("pending", 2)

        original_fsync = os.fsync
        injected = {"done": False}

        def fail_once(fd):
            if not injected["done"]:
                injected["done"] = True
                raise OSError(5, "injected fsync failure")
            return original_fsync(fd)

        monkeypatch.setattr(os, "fsync", fail_once)
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()
        assert store.last_commit.sequence == 1
        assert store.get("stable") == 1
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()

    # The failed fsync can leave a complete frame on disk; reopening must
    # independently verify its fate instead of assuming it was rolled back.
    with MemoryStore(path) as recovered:
        assert recovered.get("stable") == 1
        assert recovered.get("pending") in (None, 2)
        assert recovered.last_commit.sequence in (1, 2)


def test_oversized_unterminated_frame_rejected_without_truncation(tmp_path):
    from memory_engine.store import MAX_JOURNAL_FRAME_BYTES

    path = tmp_path / "oversized.memory"
    with MemoryStore(path) as store:
        store.set("safe", 1)
        assert store.commit() == 1
    with path.open("ab") as stream:
        stream.write(b"x" * (MAX_JOURNAL_FRAME_BYTES + 1))
    before = path.stat().st_size
    with pytest.raises(StoreError, match="journal frame exceeds size limit"):
        MemoryStore(path)
    assert path.stat().st_size == before


def test_oversized_commit_rejected_without_publishing(tmp_path):
    from memory_engine.store import MAX_JOURNAL_FRAME_BYTES

    path = tmp_path / "oversized-write.memory"
    with MemoryStore(path) as store:
        store.set("large", "x" * MAX_JOURNAL_FRAME_BYTES)
        with pytest.raises(StoreError, match="journal frame exceeds size limit"):
            store.commit()
        assert store.last_commit.sequence == 0
        assert not path.exists()


def test_corrupt_complete_frame_fails_closed(tmp_path):
    path = tmp_path / "corrupt.memory"
    with MemoryStore(path) as store:
        store.set("safe", "committed")
        assert store.commit() == 1
    with path.open("ab") as stream:
        stream.write(b'{"payload": {}, "digest": "bad"}\n')
    original = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
    assert path.read_bytes() == original


def test_invalid_later_operation_does_not_mutate_input_state():
    """Replay validation must be atomic even for direct helper callers."""
    import hashlib

    state = {"values": {"stable": {"version": 1}}}
    payload = {
        "format": 1,
        "seq": 1,
        "changes": [
            {
                "op": "set",
                "namespace": "values",
                "key": "stable",
                "value": {"version": 2},
            },
            {"op": "invalid", "namespace": "values", "key": "broken"},
        ],
    }
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(bytes(32) + encoded).hexdigest()
    frame = json.dumps({"payload": payload, "digest": digest}).encode() + b"\n"
    with pytest.raises(StoreError, match="corrupt committed journal"):
        _JournalStore._apply_verified_frame(frame, state, 0, "0" * 64)
    assert state == {"values": {"stable": {"version": 1}}}


def test_symlink_journal_cannot_modify_target(tmp_path):
    target = tmp_path / "target.memory"
    with MemoryStore(target) as store:
        store.set("safe", 1)
        store.commit()
    before = target.read_bytes()
    link = tmp_path / "linked.memory"
    link.symlink_to(target)
    with pytest.raises(StoreError):
        MemoryStore(link)
    assert target.read_bytes() == before


def test_symlink_lock_file_is_rejected(tmp_path):
    path = tmp_path / "safe.memory"
    victim = tmp_path / "victim"
    victim.write_text("unchanged")
    path.with_name(path.name + ".lock").symlink_to(victim)
    with pytest.raises(StoreError, match="unsafe journal or lock path"):
        MemoryStore(path)
    assert victim.read_text() == "unchanged"


def test_symlink_parent_directory_is_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "alias"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(StoreError, match="unsafe journal or lock path"):
        MemoryStore(link / "journal.memory")
    assert not (outside / "journal.memory").exists()


def test_replaced_journal_path_cannot_redirect_append(tmp_path):
    path = tmp_path / "journal.memory"
    victim = tmp_path / "victim.memory"
    with MemoryStore(path) as store:
        store.set("safe", 1)
        store.commit()
        victim.write_bytes(b"untouched")
        path.unlink()
        path.symlink_to(victim)
        store.set("attack", 2)
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()
        assert victim.read_bytes() == b"untouched"


def test_nonregular_lock_file_is_rejected_without_fd_leak(tmp_path):
    import stat

    from memory_engine.store import _open_journal_nofollow

    directory_fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fifo = tmp_path / "fake.lock"
        os.mkfifo(fifo)
        assert stat.S_ISFIFO(fifo.stat().st_mode)
        with pytest.raises(StoreError, match="journal must be a regular file"):
            _open_journal_nofollow(directory_fd, fifo.name, os.O_RDONLY | os.O_NONBLOCK)
    finally:
        os.close(directory_fd)


def test_regular_inode_swap_does_not_write_to_replacement(tmp_path, monkeypatch):
    path = tmp_path / "journal.memory"
    victim = tmp_path / "victim.memory"
    displaced = tmp_path / "displaced.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        store.commit()
        victim.write_bytes(b"victim-untouched")
        original = os.stat
        swapped = False

        def swap_before_stat(target, *args, **kwargs):
            nonlocal swapped
            if target == path.name and kwargs.get("dir_fd") is not None and not swapped:
                swapped = True
                path.rename(displaced)
                victim.rename(path)
            return original(target, *args, **kwargs)

        monkeypatch.setattr(os, "stat", swap_before_stat)
        store.set("next", 2)
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()
        assert path.read_bytes() == b"victim-untouched"
        assert store.last_commit.sequence == 1


def test_renamed_parent_does_not_redirect_to_recreated_directory(tmp_path):
    parent = tmp_path / "db"
    parent.mkdir()
    path = parent / "journal.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        store.commit()
        displaced = tmp_path / "db-moved"
        parent.rename(displaced)
        parent.mkdir()
        victim = parent / "journal.memory"
        victim.write_bytes(b"victim-untouched")
        store.set("next", 2)
        store.commit()
        assert victim.read_bytes() == b"victim-untouched"
        assert b"next" in (displaced / "journal.memory").read_bytes()


def test_post_fsync_regular_inode_swap_fences_writer(tmp_path, monkeypatch):
    path = tmp_path / "journal.memory"
    displaced = tmp_path / "displaced.memory"
    victim = tmp_path / "victim.memory"
    with MemoryStore(path) as store:
        store.set("safe", 1)
        store.commit()
        victim.write_bytes(b"unchanged")
        original = os.stat
        calls = 0

        def swap_after_fsync(target, *args, **kwargs):
            nonlocal calls
            if target == path.name and kwargs.get("dir_fd") is not None:
                calls += 1
                if calls == 2:
                    path.rename(displaced)
                    victim.rename(path)
            return original(target, *args, **kwargs)

        monkeypatch.setattr(os, "stat", swap_after_fsync)
        store.set("next", 2)
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()
        assert path.read_bytes() == b"unchanged"
        assert store.last_commit.sequence == 1


def test_backup_regular_inode_swap_does_not_copy_victim(tmp_path, monkeypatch):
    import shutil

    path = tmp_path / "source.memory"
    victim = tmp_path / "victim.memory"
    backup = tmp_path / "backup.memory"
    with MemoryStore(path) as store:
        store.set("safe", 1)
        store.commit()
        victim.write_bytes(b"private-victim")
        original_copy = shutil.copyfileobj

        def swap_source_after_open(source, output, *args, **kwargs):
            path.unlink()
            path.symlink_to(victim)
            return original_copy(source, output, *args, **kwargs)

        monkeypatch.setattr(shutil, "copyfileobj", swap_source_after_open)
        store.backup(backup)
        assert victim.read_bytes() == b"private-victim"
        assert b"private-victim" not in backup.read_bytes()


def test_backup_parent_rename_publishes_only_to_pinned_directory(tmp_path, monkeypatch):
    import shutil

    parent = tmp_path / "parent"
    parent.mkdir()
    moved = tmp_path / "moved"
    source = parent / "source.memory"
    backup = parent / "backup.memory"
    with MemoryStore(source) as store:
        store.set("safe", 1)
        store.commit()
        original_copy = shutil.copyfileobj

        def rename_during_copy(source_stream, output, *args, **kwargs):
            parent.rename(moved)
            parent.mkdir()
            return original_copy(source_stream, output, *args, **kwargs)

        monkeypatch.setattr(shutil, "copyfileobj", rename_during_copy)
        try:
            store.backup(backup)
        except StoreError:
            pass
        assert not (parent / "backup.memory").exists()
