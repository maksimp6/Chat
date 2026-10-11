"""Functional fault evidence for the public explicit-commit API."""

import errno
import os
import signal
import subprocess
import sys

import pytest

from memory_engine import MemoryStore, StoreError


@pytest.mark.parametrize("name", ["", 1, None])
def test_transaction_rejects_invalid_names_before_journal_write(tmp_path, name):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as store:
        with pytest.raises(StoreError, match="name"):
            with store.transaction() as tx:
                tx.set(name, "value")
        assert store.last_commit.sequence == 0
        assert not path.exists()


@pytest.mark.parametrize("value", [(1, 2), {1: "integer key"}, {"nested": (1, 2)}])
def test_values_cannot_change_shape_between_ram_and_reopen(tmp_path, value):
    with MemoryStore(tmp_path / "state.memory") as store:
        store.set("value", value)
        with pytest.raises(StoreError, match="JSON"):
            store.commit()
        assert store.last_commit.sequence == 0


def test_restore_does_not_hide_restored_values_under_staging(tmp_path):
    backup = tmp_path / "backup.memory"
    with MemoryStore(tmp_path / "source.memory") as source:
        source.set("value", "saved")
        source.commit()
        source.backup(backup)
    with MemoryStore(tmp_path / "target.memory") as target:
        target.set("value", "pending")
        with pytest.raises(StoreError, match="staged"):
            target.restore(backup)
        assert target.get("value") == "pending"
        assert target.last_commit.sequence == 0


def test_failed_write_does_not_publish_transaction(tmp_path, monkeypatch):
    from memory_engine.store import _JournalStore

    with MemoryStore(tmp_path / "state.memory") as store:
        store.set("stable", 1)
        store.commit()

        def full_disk(self, frame):
            self._failed = True
            raise StoreError("commit failed") from OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(_JournalStore, "_append_frame", full_disk)
        with pytest.raises(StoreError):
            with store.transaction() as tx:
                tx.set("stable", 2)
                tx.set("new", 3)
        assert store.get("stable") == 1
        assert store.get("new") is None
        assert store.last_commit.sequence == 1


@pytest.mark.parametrize("stage", ["before_append", "after_append", "after_ack"])
def test_sigkill_at_commit_boundary_never_loses_acknowledged_data(tmp_path, stage):
    path = tmp_path / "crash.memory"
    script = """
import os, signal, sys
from memory_engine import MemoryStore
from memory_engine.store import _JournalStore
path, stage = sys.argv[1:]
db = MemoryStore(path)
db.set("stable", 1)
db.commit()
original = _JournalStore._append_frame
def append(self, frame):
    if stage == "before_append":
        os.kill(os.getpid(), signal.SIGKILL)
    original(self, frame)
    if stage == "after_append":
        os.kill(os.getpid(), signal.SIGKILL)
_JournalStore._append_frame = append
db.set("next", 2)
db.commit()
print("ACK", flush=True)
os.kill(os.getpid(), signal.SIGKILL)
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(path), stage],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == -signal.SIGKILL, result.stderr
    assert ("ACK" in result.stdout) == (stage == "after_ack")
    with MemoryStore(path) as recovered:
        assert recovered.get("stable") == 1
        expected = None if stage == "before_append" else 2
        assert recovered.get("next") == expected
        assert recovered.last_commit.sequence == (1 if expected is None else 2)
    with MemoryStore(path) as recovered:
        assert recovered.get("next") == expected


def test_transaction_handle_cannot_be_used_from_another_thread(tmp_path):
    import threading

    errors = []
    with MemoryStore(tmp_path / "state.memory") as store:
        with store.transaction() as tx:

            def misuse():
                try:
                    tx.set("foreign", 1)
                except StoreError as exc:
                    errors.append(str(exc))

            worker = threading.Thread(target=misuse)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert errors == ["transaction belongs to another thread"]
        assert store.get("foreign") is None


def test_fork_cannot_write_or_release_parent_writer_lock(tmp_path):
    with MemoryStore(tmp_path / "state.memory") as store:
        child = os.fork()
        if child == 0:
            try:
                for operation in (lambda: store.set("foreign", 1), store.close):
                    try:
                        operation()
                    except StoreError:
                        pass
                    else:
                        os._exit(1)
                os._exit(0)
            except BaseException:
                os._exit(2)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        with pytest.raises(StoreError, match="writer"):
            MemoryStore(tmp_path / "state.memory")
        store.set("parent", 1)
        assert store.commit() == 1


@pytest.mark.parametrize("version", [None, True, 2])
def test_unknown_journal_format_is_rejected_even_with_valid_checksum(tmp_path, version):
    import hashlib
    import json

    payload = {"format": version, "seq": 1, "changes": []}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(bytes.fromhex("0" * 64) + encoded).hexdigest()
    path = tmp_path / "state.memory"
    path.write_text(json.dumps({"payload": payload, "digest": digest}) + "\n")
    with pytest.raises(StoreError, match="corrupt"):
        MemoryStore(path)


def test_info_reads_only_acknowledged_ram_without_filesystem_access(tmp_path, monkeypatch):
    from pathlib import Path

    with MemoryStore(tmp_path / "state.memory") as store:
        store.set("saved", 1)
        store.commit()
        store.set("staged", 2)

        def forbidden(*args, **kwargs):
            raise AssertionError("diagnostics accessed filesystem")

        monkeypatch.setattr(Path, "open", forbidden)
        assert store.info().last_commit == 1


@pytest.mark.parametrize("operation", ["set", "commit", "backup", "restore", "close"])
def test_active_transaction_rejects_incompatible_operations(tmp_path, operation):
    with MemoryStore(tmp_path / "state.memory") as store:
        with store.transaction() as tx:
            with pytest.raises(StoreError):
                if operation == "set":
                    store.set("outside", 1)
                elif operation == "commit":
                    store.commit()
                elif operation == "backup":
                    store.backup(tmp_path / "backup.memory")
                elif operation == "restore":
                    store.restore(tmp_path / "backup.memory")
                else:
                    store.close()
            tx.set("inside", 2)
        assert store.get("inside") == 2
        assert store.get("outside") is None
        with pytest.raises(StoreError, match="closed"):
            tx.get("inside")


def test_transaction_does_not_silently_include_client_staging(tmp_path):
    with MemoryStore(tmp_path / "state.memory") as store:
        store.set("pending", 1)
        with pytest.raises(StoreError, match="idle"):
            with store.transaction():
                pass
        assert store.last_commit.sequence == 0
        assert store.commit() == 1


def test_foreign_process_guard_before_read_or_unlock(tmp_path, monkeypatch):
    import memory_engine.store as engine

    with MemoryStore(tmp_path / "state.memory") as store:
        real_pid = os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr(engine.os, "getpid", lambda: real_pid + 1)
            with pytest.raises(StoreError, match="process"):
                store.get("anything")
            with pytest.raises(StoreError, match="process"):
                store.close()
        store.set("parent", 1)
        assert store.commit() == 1


@pytest.mark.parametrize(
    "changes",
    [
        [None],
        [{"namespace": 3, "key": "a", "op": "delete"}],
        [{"namespace": "values", "key": "a", "op": "unknown"}],
    ],
)
def test_malformed_changes_fail_closed_with_matching_checksum(tmp_path, changes):
    import hashlib
    import json

    payload = {"format": 1, "seq": 1, "changes": changes}
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(bytes.fromhex("0" * 64) + encoded).hexdigest()
    path = tmp_path / "state.memory"
    path.write_text(json.dumps({"payload": payload, "digest": digest}) + "\n")
    original = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt"):
        MemoryStore(path)
    assert path.read_bytes() == original


def test_invalid_utf8_value_never_reaches_journal(tmp_path):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as store:
        store.set("bad", "\ud800")
        with pytest.raises(StoreError, match="JSON"):
            store.commit()
        assert not path.exists()
        store.set("bad", "valid")
        assert store.commit() == 1


@pytest.mark.parametrize("failure", ["short", "readonly"])
def test_write_fault_never_acknowledges_or_publishes(tmp_path, monkeypatch, failure):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        store.commit()
        original_write = os.write
        original_fsync = os.fsync
        journal_fd = store._engine._journal_fd

        def faulty_write(fd, data):
            if fd == journal_fd and failure == "short":
                return 0
            return original_write(fd, data)

        def faulty_fsync(fd):
            if fd == journal_fd and failure == "readonly":
                raise OSError(errno.EROFS, "read only")
            return original_fsync(fd)

        monkeypatch.setattr(os, "write", faulty_write)
        monkeypatch.setattr(os, "fsync", faulty_fsync)
        with pytest.raises(StoreError, match="commit failed"):
            with store.transaction() as tx:
                tx.set("new", 2)
        assert store.get("new") is None
        assert store.get("stable") == 1
        assert store.last_commit.sequence == 1


def test_backup_rejects_changed_copy_before_publication(tmp_path, monkeypatch):
    import shutil

    path = tmp_path / "state.memory"
    backup = tmp_path / "backup.memory"
    with MemoryStore(path) as store:
        store.set("saved", 1)
        store.commit()
        monkeypatch.setattr(shutil, "copyfileobj", lambda source, target: None)
        with pytest.raises(StoreError, match="integrity mismatch"):
            store.backup(backup)
        assert not backup.exists()
        assert store.get("saved") == 1


def test_restore_io_failure_preserves_empty_ram_and_blocks_mutations(tmp_path, monkeypatch):
    backup = tmp_path / "backup.memory"
    with MemoryStore(tmp_path / "source.memory") as source:
        source.set("saved", 1)
        source.commit()
        source.backup(backup)
    with MemoryStore(tmp_path / "target.memory") as target:

        def fail_replace(*args):
            raise OSError(errno.EROFS, "read only")

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(StoreError, match="restore failed"):
            target.restore(backup)
        assert target.last_commit.sequence == 0
        assert target.get("saved") is None
        with pytest.raises(StoreError, match="recovery required"):
            target.commit()


def test_restore_rejects_missing_source_and_self_path(tmp_path):
    path = tmp_path / "target.memory"
    with MemoryStore(path) as target:
        with pytest.raises(StoreError, match="live database path"):
            target.restore(path)
        with pytest.raises(StoreError, match="does not exist"):
            target.restore(tmp_path / "missing.memory")
