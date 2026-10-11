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
