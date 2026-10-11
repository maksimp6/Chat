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
    payload = {"format": 1, "seq": seq + 1, "changes": [
        {"op": "set", "namespace": "values", "key": "stable", "value": 2},
        {"op": "invalid", "namespace": "values", "key": "other"},
    ]}
    import hashlib
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    frame = json.dumps({"payload": payload, "digest": hashlib.sha256(bytes.fromhex(digest) + encoded).hexdigest()}).encode() + b"\n"
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
    result = subprocess.run([sys.executable, "-c", code, str(path), boundary], capture_output=True, text=True, timeout=15)
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
