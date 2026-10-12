"""Fault injection and multiprocess proof for #776, without production data."""

from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import time
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_memory.backup import create_backup, restore_backup
from agent_memory.file_memory_db import FileMemoryCorruption, FileMemoryDB, FileMemoryError


def _writer(path: str, prefix: str, ready, start, out) -> None:
    db = FileMemoryDB(path)
    ready.put(prefix)
    if not start.wait(10):
        out.put((prefix, "timeout"))
        return
    sequences = [db.put(f"{prefix}:{i}", i) for i in range(12)]
    out.put((prefix, sequences))


def _crash_after_ack(path: str) -> None:
    db = FileMemoryDB(path)
    db.put("acked", {"value": 42})
    os._exit(0)


def _crash_while_locked(path: str) -> None:
    db = FileMemoryDB(path)
    with db._guard(mutate=True):
        os._exit(0)


def _compactor(path: str, ready, start, out) -> None:
    db = FileMemoryDB(path)
    ready.put("compact")
    if not start.wait(10):
        out.put("timeout")
        return
    for _ in range(5):
        db.compact()
    out.put("compact")


def test_acknowledged_write_survives_crash(tmp_path):
    path = str(tmp_path / "alice.memory")
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_crash_after_ack, args=(path,))
    child.start()
    child.join(10)
    assert child.exitcode == 0
    restored = FileMemoryDB(path)
    assert restored.sequence == 1
    assert restored.get("acked") == {"value": 42}


def test_interprocess_writers_and_compaction_no_lost_updates(tmp_path):
    path = str(tmp_path / "alice.memory")
    ctx = multiprocessing.get_context("spawn")
    ready, start, out = ctx.Queue(), ctx.Event(), ctx.Queue()
    children = [
        ctx.Process(target=_writer, args=(path, str(i), ready, start, out)) for i in range(4)
    ] + [ctx.Process(target=_compactor, args=(path, ready, start, out))]
    for child in children:
        child.start()
    try:
        assert {ready.get(timeout=10) for _ in children} == {"0", "1", "2", "3", "compact"}
        start.set()
        reports = [out.get(timeout=20) for _ in children]
        sequences = [seq for record in reports if isinstance(record, tuple) for seq in record[1]]
        assert sorted(sequences) == list(range(1, 49))
        assert reports.count("compact") == 1
        db = FileMemoryDB(path)
        assert db.sequence == 48
        assert db.items() == {f"{p}:{i}": i for p in range(4) for i in range(12)}
    finally:
        for child in children:
            child.join(5)
            if child.is_alive():
                child.terminate()
                child.join(5)
    assert all(child.exitcode == 0 for child in children)


def test_cross_instance_reads_observe_new_commits(tmp_path):
    path = tmp_path / "alice.memory"
    a, b = FileMemoryDB(path), FileMemoryDB(path)
    assert a.put("one", 1) == 1
    assert b.get("one") == 1
    assert b.put("two", 2) == 2
    assert a.items() == {"one": 1, "two": 2}


def test_crashed_file_lock_is_released(tmp_path):
    path = str(tmp_path / "alice.memory")
    ctx = multiprocessing.get_context("spawn")
    child = ctx.Process(target=_crash_while_locked, args=(path,))
    child.start()
    child.join(10)
    assert child.exitcode == 0
    assert FileMemoryDB(path).put("after", 1) == 1


def test_torn_write_recovered_without_ack(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("stable", "value")
    real_write = os.write
    calls = 0

    def torn(fd, payload):
        nonlocal calls
        calls += 1
        if calls == 1:
            return real_write(fd, payload[:9])
        raise OSError(28, "ENOSPC")

    monkeypatch.setattr("agent_memory.file_memory_db.os.write", torn)
    with pytest.raises(OSError):
        db.put("never-acked", 2)
    assert db.items() == {"stable": "value"}
    assert db.sequence == 1
    with pytest.raises(FileMemoryError, match="uncertain"):
        db.put("later", 3)
    monkeypatch.undo()
    recovered = FileMemoryDB(path)
    assert recovered.items() == {"stable": "value"}
    assert recovered.put("safe", True) == 2


def test_complete_but_unacknowledged_fsync_outcome_is_ambiguous(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)

    def failed_fsync(_fd):
        raise OSError("disk fsync error")

    monkeypatch.setattr("agent_memory.file_memory_db.os.fsync", failed_fsync)
    with pytest.raises(OSError):
        db.put("maybe", 1)
    assert db.sequence == 0
    with pytest.raises(FileMemoryError):
        db.put("later", 2)
    monkeypatch.undo()
    # Complete unacknowledged writes may survive. Caller must use idempotent request IDs.
    assert FileMemoryDB(path).sequence == 1


def test_backup_during_concurrent_appends_is_consistent(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)

    def writer():
        for i in range(25):
            db.put(f"k{i}", i)

    with ThreadPoolExecutor(max_workers=2) as pool:
        job = pool.submit(writer)
        for index in range(5):
            manifest = create_backup(db, tmp_path / f"backup{index}")
            restored = restore_backup(tmp_path / f"backup{index}", tmp_path / f"restore{index}")
            assert restored.sequence == manifest.sequence
            assert restored.items() == {f"k{i}": i for i in range(manifest.sequence)}
        job.result()
    assert db.sequence == 25


def test_compaction_fault_preserves_last_good(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("saved", 1)

    def failure(*_args, **_kwargs):
        raise OSError("no space")

    monkeypatch.setattr("agent_memory.file_memory_db.os.replace", failure)
    with pytest.raises(OSError):
        db.compact()
    monkeypatch.undo()
    assert FileMemoryDB(path).get("saved") == 1


def test_snapshot_returns_defensive_copies(tmp_path):
    db = FileMemoryDB(tmp_path / "alice.memory")
    db.put("a", {"nested": [1]})
    db.get("a")["nested"].append(2)
    db.items()["a"]["nested"].append(3)
    assert db.get("a") == {"nested": [1]}


def test_corrupted_committed_record_fails_closed(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("keep", 7)
    encoded = bytearray(path.read_bytes())
    encoded[10] ^= 1
    path.write_bytes(encoded)
    with pytest.raises(FileMemoryCorruption):
        FileMemoryDB(path)


def test_non_object_committed_record_is_corruption(tmp_path):
    path = tmp_path / "alice.memory"
    path.write_text("true\n", encoding="utf-8")
    with pytest.raises(FileMemoryCorruption):
        FileMemoryDB(path)


def _restore_worker(source: str, target: str, start, results) -> None:
    from agent_memory.backup import MemoryBackupError

    if not start.wait(10):
        results.put("timeout")
        return
    try:
        restore_backup(source, target)
        results.put("restored")
    except MemoryBackupError:
        results.put("already_exists")


def test_concurrent_restore_only_one_wins(tmp_path):
    source_db = FileMemoryDB(tmp_path / "source.memory")
    source_db.put("stable", 123)
    backup = tmp_path / "backup"
    create_backup(source_db, backup)

    ctx = multiprocessing.get_context("spawn")
    start, results = ctx.Event(), ctx.Queue()
    target = tmp_path / "restored.memory"
    children = [
        ctx.Process(target=_restore_worker, args=(str(backup), str(target), start, results))
        for _ in range(2)
    ]
    for child in children:
        child.start()
    try:
        start.set()
        assert sorted(results.get(timeout=15) for _ in children) == ["already_exists", "restored"]
    finally:
        for child in children:
            child.join(5)
            if child.is_alive():
                child.terminate()
                child.join(5)
    assert all(child.exitcode == 0 for child in children)
    assert FileMemoryDB(target).items() == {"stable": 123}


def test_dangling_symlink_restore_target_rejected(tmp_path):
    from agent_memory.backup import MemoryBackupError

    db = FileMemoryDB(tmp_path / "source.memory")
    db.put("one", 1)
    backup = tmp_path / "backup"
    create_backup(db, backup)
    target = tmp_path / "destination.memory"
    target.symlink_to(tmp_path / "missing.memory")
    with pytest.raises(MemoryBackupError, match="already exists"):
        restore_backup(backup, target)
    assert target.is_symlink()


def test_poisoned_handle_cannot_produce_backup(tmp_path, monkeypatch):
    db = FileMemoryDB(tmp_path / "source.memory")
    db.put("stable", 1)

    def fail_append(_record):
        raise OSError("simulated ENOSPC")

    monkeypatch.setattr(db, "_append", fail_append)
    with pytest.raises(OSError):
        db.put("failed", 2)
    with pytest.raises(FileMemoryError, match="uncertain"):
        create_backup(db, tmp_path / "uncommitted")
    assert not (tmp_path / "uncommitted" / "COMMITTED").exists()

def test_deleting_authoritative_file_after_ack_fails_closed(tmp_path):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("keep", 1)
    path.unlink()
    with pytest.raises(FileMemoryCorruption, match="disappeared"):
        db.items()
