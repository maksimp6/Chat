"""Regression coverage for unknown write outcomes and explicit writer recovery."""

import errno
import json
import os
import stat
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from agent_memory import file_memory_db as storage
from agent_memory.file_memory_db import FileMemoryDB, FileMemoryError


def inject_append_failure(patch, failure):
    if failure == "partial_write":
        real_write = os.write
        calls = 0

        def partial_then_full_disk(fd, payload):
            nonlocal calls
            calls += 1
            if calls == 1:
                return real_write(fd, payload[: max(1, len(payload) // 2)])
            raise OSError(errno.ENOSPC, "injected full disk")

        patch.setattr(storage.os, "write", partial_then_full_disk)
    elif failure == "zero_write":
        patch.setattr(storage.os, "write", lambda _fd, _payload: 0)
    elif failure == "close":
        real_close = os.close

        def close_then_fail(fd):
            real_close(fd)
            raise OSError(errno.EIO, "injected close failure")

        patch.setattr(storage.os, "close", close_then_fail)
    elif failure == "open":

        def fail_open(*_args):
            raise OSError(errno.ENOSPC, "injected open failure")

        patch.setattr(storage.os, "open", fail_open)
    else:

        def fail_sync(_fd):
            if failure == "interrupt":
                raise KeyboardInterrupt("injected interruption")
            raise OSError(errno.EIO, "injected sync failure")

        patch.setattr(storage.os, "fsync", fail_sync)


def assert_writer_fenced(db):
    before = db.path.read_bytes() if db.path.exists() else None
    state, sequence = db.items(), db.sequence
    temporary = db.path.with_name(f"{db.path.name}.tmp")
    temp_before = temporary.read_bytes() if temporary.exists() else None
    operations = (
        lambda: db.put("must_not_commit", 99),
        lambda: db.delete("confirmed"),
        db.compact,
    )
    for operation in operations:
        with pytest.raises(FileMemoryError, match="recovery"):
            operation()
    assert db.items() == state
    assert db.sequence == sequence
    assert (db.path.read_bytes() if db.path.exists() else None) == before
    assert (temporary.read_bytes() if temporary.exists() else None) == temp_before


@pytest.mark.parametrize("operation", ["put", "delete"])
@pytest.mark.parametrize(
    "failure", ["partial_write", "file_fsync", "close", "zero_write", "open", "interrupt"]
)
def test_uncertain_append_fences_every_mutation_until_reopen(
    tmp_path, monkeypatch, failure, operation
):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("confirmed", 100)
    mutation = (
        (lambda: db.put("uncertain", 200))
        if operation == "put"
        else (lambda: db.delete("confirmed"))
    )
    error = KeyboardInterrupt if failure == "interrupt" else OSError
    with monkeypatch.context() as patch:
        inject_append_failure(patch, failure)
        with pytest.raises(error):
            mutation()

    assert db.items() == {"confirmed": 100}
    assert db.sequence == 1
    assert_writer_fenced(db)

    # A full record without an ACK may survive. It must not reuse its sequence.
    complete = failure in {"file_fsync", "close", "interrupt"}
    expected = {"confirmed": 100}
    if complete:
        if operation == "put":
            expected["uncertain"] = 200
        else:
            expected.clear()
    recovered = FileMemoryDB(path)
    assert recovered.items() == expected
    assert recovered.sequence == (2 if complete else 1)
    assert recovered.put("after_recovery", 300) == (3 if complete else 2)
    assert FileMemoryDB(path).items() == {**expected, "after_recovery": 300}
    assert_writer_fenced(db)


def test_failed_first_parent_barrier_fences_the_writer(tmp_path, monkeypatch):
    db = FileMemoryDB(tmp_path / "alice.memory")

    def fail_parent():
        raise OSError(errno.EIO, "injected parent sync failure")

    with monkeypatch.context() as patch:
        patch.setattr(db, "_fsync_parent", fail_parent)
        with pytest.raises(OSError, match="parent sync"):
            db.put("uncertain", 100)
    assert db.items() == {}
    assert db.sequence == 0
    assert_writer_fenced(db)
    recovered = FileMemoryDB(db.path)
    assert recovered.items() == {"uncertain": 100}
    assert recovered.put("after_recovery", 200) == 2


@pytest.mark.parametrize("phase", ["write", "fsync", "close", "replace", "replace_after", "parent"])
def test_failed_compaction_cannot_be_used_to_clear_the_writer_fence(tmp_path, monkeypatch, phase):
    db = FileMemoryDB(tmp_path / "alice.memory")
    db.put("confirmed", 100)
    db.put("confirmed", 200)

    def fail(*_args):
        raise OSError(errno.EIO, "injected compaction failure")

    with monkeypatch.context() as patch:
        if phase == "write":
            patch.setattr(db, "_write_all", fail)
        elif phase == "fsync":
            patch.setattr(storage.os, "fsync", fail)
        elif phase == "close":
            real_close = os.close

            def close_then_fail(fd):
                real_close(fd)
                fail()

            patch.setattr(storage.os, "close", close_then_fail)
        elif phase == "replace":
            patch.setattr(storage.os, "replace", fail)
        elif phase == "replace_after":
            real_replace = os.replace

            def replace_then_fail(source, target):
                real_replace(source, target)
                fail()

            patch.setattr(storage.os, "replace", replace_then_fail)
        else:
            patch.setattr(db, "_fsync_parent", fail)
        with pytest.raises(OSError, match="compaction failure"):
            db.compact()

    assert db.items() == {"confirmed": 200}
    assert db.sequence == 2
    assert_writer_fenced(db)
    recovered = FileMemoryDB(db.path)
    assert recovered.items() == {"confirmed": 200}
    assert recovered.put("after_recovery", 300) == 3
    assert FileMemoryDB(db.path).sequence == 3


def test_reopen_syncs_validated_file_and_parent_before_admission(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    FileMemoryDB(path).put("confirmed", 100)
    real_fsync = os.fsync
    barriers = []

    def record_barrier(fd):
        barriers.append("parent" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file")
        return real_fsync(fd)

    monkeypatch.setattr(storage.os, "fsync", record_barrier)
    recovered = FileMemoryDB(path)
    assert recovered.get("confirmed") == 100
    assert barriers == ["file", "parent"]


@pytest.mark.parametrize("failed_barrier", ["file", "parent"])
def test_reopen_fails_closed_when_recovery_barrier_fails(tmp_path, monkeypatch, failed_barrier):
    path = tmp_path / "alice.memory"
    FileMemoryDB(path).put("confirmed", 100)
    before = path.read_bytes()
    real_fsync = os.fsync

    def fail_barrier(fd):
        kind = "parent" if stat.S_ISDIR(os.fstat(fd).st_mode) else "file"
        if kind == failed_barrier:
            raise OSError(errno.EIO, "injected recovery barrier failure")
        return real_fsync(fd)

    with monkeypatch.context() as patch:
        patch.setattr(storage.os, "fsync", fail_barrier)
        with pytest.raises(OSError, match="recovery barrier"):
            FileMemoryDB(path)
    assert path.read_bytes() == before
    assert FileMemoryDB(path).get("confirmed") == 100


def test_validation_error_before_io_does_not_fault_a_healthy_writer(tmp_path):
    db = FileMemoryDB(tmp_path / "alice.memory")
    db.put("confirmed", 100)
    with pytest.raises(TypeError):
        db.put("invalid", object())
    with pytest.raises(ValueError):
        db.put("", 100)
    assert db.put("after_validation", 200) == 2
    db.compact()
    assert FileMemoryDB(db.path).items() == {"confirmed": 100, "after_validation": 200}


def test_waiting_writer_observes_failure_fence(tmp_path, monkeypatch):
    db = FileMemoryDB(tmp_path / "alice.memory")
    db.put("confirmed", 100)
    entered, release, second_started = Event(), Event(), Event()

    def fail_after_wait(_fd, _payload):
        entered.set()
        assert release.wait(5)
        raise OSError(errno.EIO, "injected blocked writer failure")

    def second_write():
        second_started.set()
        return db.put("second", 200)

    with monkeypatch.context() as patch:
        patch.setattr(db, "_write_all", fail_after_wait)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(db.put, "first", 200)
            try:
                assert entered.wait(5)
                second = pool.submit(second_write)
                assert second_started.wait(5)
            finally:
                release.set()
            with pytest.raises(OSError, match="blocked writer"):
                first.result(timeout=5)
            with pytest.raises(FileMemoryError, match="recovery"):
                second.result(timeout=5)
    assert db.sequence == 1
    assert FileMemoryDB(db.path).items() == {"confirmed": 100}


def test_fresh_process_recovers_unknown_write_without_duplicate_sequence(tmp_path, monkeypatch):
    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("confirmed", 100)
    with monkeypatch.context() as patch:
        inject_append_failure(patch, "file_fsync")
        with pytest.raises(OSError):
            db.put("uncertain", 200)
    assert_writer_fenced(db)
    script = """
import json, sys
from agent_memory.file_memory_db import FileMemoryDB
db = FileMemoryDB(sys.argv[1])
assert db.items() == {"confirmed": 100, "uncertain": 200}
assert db.put("after_recovery", 300) == 3
print(json.dumps({"sequence": db.sequence, "items": db.items()}, sort_keys=True))
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        cwd=Path(__file__).resolve().parents[1],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"},
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    evidence = json.loads(result.stdout)
    reopened = FileMemoryDB(path)
    assert evidence == {"sequence": 3, "items": reopened.items()}
    assert reopened.items() == {"confirmed": 100, "uncertain": 200, "after_recovery": 300}


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX SIGKILL and pipe readiness")
def test_ack_after_recovery_survives_sigkill_and_another_fresh_process(tmp_path, monkeypatch):
    import select

    path = tmp_path / "alice.memory"
    db = FileMemoryDB(path)
    db.put("confirmed", 100)
    with monkeypatch.context() as patch:
        inject_append_failure(patch, "file_fsync")
        with pytest.raises(OSError):
            db.put("uncertain", 200)
    assert_writer_fenced(db)
    writer_script = """
import signal, sys
from agent_memory.file_memory_db import FileMemoryDB
db = FileMemoryDB(sys.argv[1])
assert db.put("after_recovery", 300) == 3
print("ACK:3", flush=True)
signal.pause()
"""
    environment = {"PATH": os.environ.get("PATH", ""), "PYTHONIOENCODING": "utf-8"}
    root = Path(__file__).resolve().parents[1]
    writer = subprocess.Popen(
        [sys.executable, "-u", "-c", writer_script, str(path)],
        cwd=root,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert writer.stdout is not None
        assert select.select([writer.stdout], [], [], 10)[0], "writer did not acknowledge"
        assert writer.stdout.readline().strip() == "ACK:3"
    finally:
        writer.kill()
        writer.communicate(timeout=10)
    assert writer.returncode == -9
    reader_script = """
import json, sys
from agent_memory.file_memory_db import FileMemoryDB
db = FileMemoryDB(sys.argv[1])
print(json.dumps({"sequence": db.sequence, "items": db.items()}))
"""
    result = subprocess.run(
        [sys.executable, "-c", reader_script, str(path)],
        cwd=root,
        env=environment,
        capture_output=True,
        text=True,
        timeout=15,
        check=True,
    )
    assert json.loads(result.stdout) == {
        "sequence": 3,
        "items": {"confirmed": 100, "uncertain": 200, "after_recovery": 300},
    }
    assert db.sequence == 1
    assert_writer_fenced(db)
