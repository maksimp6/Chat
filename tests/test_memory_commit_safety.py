"""Functional fault evidence for the public explicit-commit API."""

import errno
import os
import signal
import subprocess
import sys

import pytest

from kristall import Kristall, KristallError
from kristall import Kristall as MemoryStore, KristallError as StoreError


@pytest.mark.parametrize("mode", ["commit", "transaction"])
@pytest.mark.parametrize("phase", ["fsync", "after_append", "after_apply"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_commit_blocks_retry_until_verified_reopen(
    tmp_path, monkeypatch, mode, phase, interruption
):
    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        store.set("saved", 1)
        store.commit()
        original_append = store._engine._append_frame
        original_apply = store._engine._apply_changes
        with monkeypatch.context() as patch:

            def interrupted_fsync(fd):
                raise interruption("injected commit cancellation")

            def interrupted_append(frame):
                original_append(frame)
                raise interruption("injected commit cancellation")

            def interrupted_apply(changes):
                original_apply(changes)
                raise interruption("injected commit cancellation")

            if phase == "fsync":
                patch.setattr(os, "fsync", interrupted_fsync)
            elif phase == "after_append":
                patch.setattr(store._engine, "_append_frame", interrupted_append)
            else:
                patch.setattr(store._engine, "_apply_changes", interrupted_apply)
            with pytest.raises(interruption):
                if mode == "commit":
                    store.set("new", 2)
                    store.commit()
                else:
                    with store.transaction() as tx:
                        tx.set("new", 2)
        if phase == "after_apply":
            assert store.info().last_commit is None
            with pytest.raises(KristallError, match="RAM publication"):
                store.last_commit
            with pytest.raises(KristallError, match="RAM publication"):
                store.get("saved")
        else:
            assert store.last_commit.sequence == 1
            assert store.get("saved") == 1
        with pytest.raises(KristallError, match="recovery required"):
            store.set("later", 3)
        with pytest.raises(KristallError, match="recovery required"):
            store.commit()
    # A complete unacknowledged frame may survive; never append with stale metadata.
    with Kristall(path) as recovered:
        assert recovered.get("saved") == 1
        assert recovered.get("new") == 2
        assert recovered.last_commit.sequence == 2
        recovered.set("later", 3)
        assert recovered.commit() == 3
    with Kristall(path) as reopened:
        assert reopened.get("saved") == 1
        assert reopened.get("new") == 2
        assert reopened.get("later") == 3


@pytest.mark.parametrize("mode", ["commit", "transaction"])
@pytest.mark.parametrize("phase", ["fsync", "after_append", "after_apply"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_commit_blocks_retry_until_verified_reopen_after_publication(
    tmp_path, monkeypatch, mode, phase, interruption
):
    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        store.set("saved", 1)
        store.commit()
        original_append = store._engine._append_frame
        original_apply = store._engine._apply_changes
        with monkeypatch.context() as patch:

            def interrupted_fsync(fd):
                raise interruption("injected commit cancellation")

            def interrupted_append(frame):
                original_append(frame)
                raise interruption("injected commit cancellation")

            def interrupted_apply(changes):
                original_apply(changes)
                raise interruption("injected commit cancellation")

            if phase == "fsync":
                patch.setattr(os, "fsync", interrupted_fsync)
            elif phase == "after_append":
                patch.setattr(store._engine, "_append_frame", interrupted_append)
            else:
                patch.setattr(store._engine, "_apply_changes", interrupted_apply)
            with pytest.raises(interruption):
                if mode == "commit":
                    store.set("new", 2)
                    store.commit()
                else:
                    with store.transaction() as tx:
                        tx.set("new", 2)
        if phase == "after_apply":
            assert store.info().last_commit is None
            with pytest.raises(KristallError, match="RAM publication"):
                store.last_commit
            with pytest.raises(KristallError, match="RAM publication"):
                store.get("saved")
        else:
            assert store.last_commit.sequence == 1
            assert store.get("saved") == 1
        with pytest.raises(KristallError, match="recovery required"):
            store.set("later", 3)
        with pytest.raises(KristallError, match="recovery required"):
            store.commit()
    # A complete unacknowledged frame may survive; never append with stale metadata.
    with Kristall(path) as recovered:
        assert recovered.get("saved") == 1
        assert recovered.get("new") == 2
        assert recovered.last_commit.sequence == 2
        recovered.set("later", 3)
        assert recovered.commit() == 3
    with Kristall(path) as reopened:
        assert reopened.get("saved") == 1
        assert reopened.get("new") == 2
        assert reopened.get("later") == 3


@pytest.mark.parametrize("mode", ["commit", "transaction"])
@pytest.mark.parametrize("phase", ["fsync", "after_append", "after_apply"])
@pytest.mark.parametrize("interruption", [KeyboardInterrupt, SystemExit])
def test_interrupted_commit_blocks_retry_until_verified_reopen_from_checkpoint(
    tmp_path, monkeypatch, mode, phase, interruption
):
    path = tmp_path / "state.memory"
    with MemoryStore(path) as store:
        store.set("saved", 1)
        store.commit()
        original_append = store._engine._append_frame
        original_apply = store._engine._apply_changes
        with monkeypatch.context() as patch:

            def interrupted_fsync(fd):
                raise interruption("injected commit cancellation")

            def interrupted_append(frame):
                original_append(frame)
                raise interruption("injected commit cancellation")

            def interrupted_apply(changes):
                original_apply(changes)
                raise interruption("injected commit cancellation")

            if phase == "fsync":
                patch.setattr(os, "fsync", interrupted_fsync)
            elif phase == "after_append":
                patch.setattr(store._engine, "_append_frame", interrupted_append)
            else:
                patch.setattr(store._engine, "_apply_changes", interrupted_apply)
            with pytest.raises(interruption):
                if mode == "commit":
                    store.set("new", 2)
                    store.commit()
                else:
                    with store.transaction() as tx:
                        tx.set("new", 2)
        if phase == "after_apply":
            assert store.info().last_commit is None
            with pytest.raises(StoreError, match="RAM publication"):
                store.last_commit
            with pytest.raises(StoreError, match="RAM publication"):
                store.get("saved")
        else:
            assert store.last_commit.sequence == 1
            assert store.get("saved") == 1
        with pytest.raises(StoreError, match="recovery required"):
            store.set("later", 3)
        with pytest.raises(StoreError, match="recovery required"):
            store.commit()
    # A complete unacknowledged frame may survive; never append with stale metadata.
    with MemoryStore(path) as recovered:
        assert recovered.get("saved") == 1
        assert recovered.get("new") == 2
        assert recovered.last_commit.sequence == 2
        recovered.set("later", 3)
        assert recovered.commit() == 3
    with MemoryStore(path) as reopened:
        assert reopened.get("saved") == 1
        assert reopened.get("new") == 2
        assert reopened.get("later") == 3


@pytest.mark.parametrize("name", ["", 1, None])
def test_transaction_rejects_invalid_names_before_journal_write(tmp_path, name):
    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        with pytest.raises(KristallError, match="name"):
            with store.transaction() as tx:
                tx.set(name, "value")
        assert store.last_commit.sequence == 0
        assert not path.exists()


@pytest.mark.parametrize("value", [(1, 2), {1: "integer key"}, {"nested": (1, 2)}])
def test_values_cannot_change_shape_between_ram_and_reopen(tmp_path, value):
    with Kristall(tmp_path / "state.memory") as store:
        store.set("value", value)
        with pytest.raises(KristallError, match="JSON"):
            store.commit()
        assert store.last_commit.sequence == 0


def test_restore_does_not_hide_restored_values_under_staging(tmp_path):
    backup = tmp_path / "backup.memory"
    with Kristall(tmp_path / "source.memory") as source:
        source.set("value", "saved")
        source.commit()
        source.backup(backup)
    with Kristall(tmp_path / "target.memory") as target:
        target.set("value", "pending")
        with pytest.raises(KristallError, match="staged"):
            target.restore(backup)
        assert target.get("value") == "pending"
        assert target.last_commit.sequence == 0


def test_failed_write_does_not_publish_transaction(tmp_path, monkeypatch):
    from kristall.store import _JournalStore

    with Kristall(tmp_path / "state.memory") as store:
        store.set("stable", 1)
        store.commit()

        def full_disk(self, frame):
            self._failed = True
            raise KristallError("commit failed") from OSError(errno.ENOSPC, "full")

        monkeypatch.setattr(_JournalStore, "_append_frame", full_disk)
        with pytest.raises(KristallError):
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
from kristall import Kristall
from kristall.store import _JournalStore
path, stage = sys.argv[1:]
db = Kristall(path)
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
    with Kristall(path) as recovered:
        assert recovered.get("stable") == 1
        expected = None if stage == "before_append" else 2
        assert recovered.get("next") == expected
        assert recovered.last_commit.sequence == (1 if expected is None else 2)
    with Kristall(path) as recovered:
        assert recovered.get("next") == expected


def test_transaction_handle_cannot_be_used_from_another_thread(tmp_path):
    import threading

    errors = []
    with Kristall(tmp_path / "state.memory") as store:
        with store.transaction() as tx:

            def misuse():
                try:
                    tx.set("foreign", 1)
                except KristallError as exc:
                    errors.append(str(exc))

            worker = threading.Thread(target=misuse)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert errors == ["transaction belongs to another thread"]
        assert store.get("foreign") is None


def test_fork_cannot_write_or_release_parent_writer_lock(tmp_path):
    with Kristall(tmp_path / "state.memory") as store:
        child = os.fork()
        if child == 0:
            try:
                for operation in (lambda: store.set("foreign", 1), store.close):
                    try:
                        operation()
                    except KristallError:
                        pass
                    else:
                        os._exit(1)
                os._exit(0)
            except BaseException:
                os._exit(2)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 0
        with pytest.raises(KristallError, match="writer"):
            Kristall(tmp_path / "state.memory")
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
    with pytest.raises(KristallError, match="corrupt"):
        Kristall(path)


def test_info_reads_only_acknowledged_ram_without_filesystem_access(tmp_path, monkeypatch):
    from pathlib import Path

    with Kristall(tmp_path / "state.memory") as store:
        store.set("saved", 1)
        store.commit()
        store.set("staged", 2)

        def forbidden(*args, **kwargs):
            raise AssertionError("diagnostics accessed filesystem")

        monkeypatch.setattr(Path, "open", forbidden)
        assert store.info().last_commit == 1


@pytest.mark.parametrize("operation", ["set", "commit", "backup", "restore", "close"])
def test_active_transaction_rejects_incompatible_operations(tmp_path, operation):
    with Kristall(tmp_path / "state.memory") as store:
        with store.transaction() as tx:
            with pytest.raises(KristallError):
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
        with pytest.raises(KristallError, match="closed"):
            tx.get("inside")


def test_transaction_does_not_silently_include_client_staging(tmp_path):
    with Kristall(tmp_path / "state.memory") as store:
        store.set("pending", 1)
        with pytest.raises(KristallError, match="idle"):
            with store.transaction():
                pass
        assert store.last_commit.sequence == 0
        assert store.commit() == 1


def test_foreign_process_guard_before_read_or_unlock(tmp_path, monkeypatch):
    import kristall.store as engine

    with Kristall(tmp_path / "state.memory") as store:
        real_pid = os.getpid()
        with monkeypatch.context() as patch:
            patch.setattr(engine.os, "getpid", lambda: real_pid + 1)
            with pytest.raises(KristallError, match="process"):
                store.get("anything")
            with pytest.raises(KristallError, match="process"):
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
    with pytest.raises(KristallError, match="corrupt"):
        Kristall(path)
    assert path.read_bytes() == original


def test_invalid_utf8_value_never_reaches_journal(tmp_path):
    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        with pytest.raises(KristallError, match="invalid Unicode"):
            store.set("bad", "\ud800")
        assert store.get("bad") is None
        assert store.commit() == 0
        assert not path.exists()
        store.set("bad", "valid")
        assert store.commit() == 1


@pytest.mark.parametrize("failure", ["short", "readonly"])
def test_write_fault_never_acknowledges_or_publishes(tmp_path, monkeypatch, failure):
    from pathlib import Path

    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        store.set("stable", 1)
        store.commit()
        original_open = Path.open

        class ShortStream:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def write(self, data):
                return 0

        def faulty_open(target, mode="r", *args, **kwargs):
            if target == path and mode == "ab":
                if failure == "short":
                    return ShortStream()
                raise OSError(errno.EROFS, "read only")
            return original_open(target, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", faulty_open)
        with pytest.raises(KristallError, match="commit failed"):
            with store.transaction() as tx:
                tx.set("new", 2)
        assert store.get("new") is None
        assert store.get("stable") == 1
        assert store.last_commit.sequence == 1


def test_backup_rejects_changed_copy_before_publication(tmp_path, monkeypatch):
    import shutil

    path = tmp_path / "state.memory"
    backup = tmp_path / "backup.memory"
    with Kristall(path) as store:
        store.set("saved", 1)
        store.commit()
        monkeypatch.setattr(shutil, "copyfileobj", lambda source, target: None)
        with pytest.raises(KristallError, match="integrity mismatch"):
            store.backup(backup)
        assert not backup.exists()
        assert store.get("saved") == 1


def test_restore_io_failure_preserves_empty_ram_and_blocks_mutations(tmp_path, monkeypatch):
    backup = tmp_path / "backup.memory"
    with Kristall(tmp_path / "source.memory") as source:
        source.set("saved", 1)
        source.commit()
        source.backup(backup)
    with Kristall(tmp_path / "target.memory") as target:

        def fail_replace(*args):
            raise OSError(errno.EROFS, "read only")

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(KristallError, match="restore failed"):
            target.restore(backup)
        assert target.last_commit.sequence == 0
        assert target.get("saved") is None
        with pytest.raises(KristallError, match="recovery required"):
            target.commit()


def test_restore_rejects_missing_source_and_self_path(tmp_path):
    path = tmp_path / "target.memory"
    with Kristall(path) as target:
        with pytest.raises(KristallError, match="live database path"):
            target.restore(path)
        with pytest.raises(KristallError, match="does not exist"):
            target.restore(tmp_path / "missing.memory")
