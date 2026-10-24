"""Fault-injection replay regressions ported to the format:1 #1123 journal.

Based on #1083 invariants; does not import the incompatible legacy _JournalEngine.
"""

import json
import os
import signal
import subprocess
import sys

import pytest

from kristall import Kristall, KristallError
from kristall.store import _JournalStore


def test_truncated_frame_fails_closed_without_mutation(tmp_path):
    path = tmp_path / "journal.memory"
    with Kristall(path) as store:
        store.set("stable", {"n": 1})
        assert store.commit() == 1
    with path.open("ab") as out:
        out.write(b'{"payload":')
    before = path.read_bytes()
    with pytest.raises(KristallError, match="incomplete journal tail"):
        Kristall(path)
    assert path.read_bytes() == before


def test_semantically_invalid_hashed_frame_never_publishes_partial_changes(tmp_path):
    path = tmp_path / "journal.memory"
    with Kristall(path) as store:
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
    with pytest.raises(KristallError, match="corrupt committed journal"):
        Kristall(path)
    assert path.read_bytes() == before
    assert state["values"]["stable"] == 1


@pytest.mark.parametrize("boundary", ["before_append", "after_append", "after_ack"])
def test_sigkill_commit_replay_e2e(tmp_path, boundary):
    path = tmp_path / "crash.memory"
    code = """
import os,signal,sys
from kristall import Kristall
from kristall.store import _JournalStore
db=Kristall(sys.argv[1])
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
    with Kristall(path) as recovered:
        assert recovered.get("stable") == 1
        assert recovered.get("next") == (None if boundary == "before_append" else 2)


def test_fsync_failure_fences_writer_and_preserves_acknowledged_state(tmp_path, monkeypatch):
    """An ambiguous fsync failure must not publish or permit another commit."""
    path = tmp_path / "fsync.memory"
    with Kristall(path) as store:
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
        with pytest.raises(KristallError, match="recovery required"):
            store.commit()
        assert store.last_commit.sequence == 1
        assert store.get("stable") == 1
        with pytest.raises(KristallError, match="recovery required"):
            store.commit()

    # The failed fsync can leave a complete frame on disk; reopening must
    # independently verify its fate instead of assuming it was rolled back.
    with Kristall(path) as recovered:
        assert recovered.get("stable") == 1
        assert recovered.get("pending") in (None, 2)
        assert recovered.last_commit.sequence in (1, 2)


def test_oversized_unterminated_frame_rejected_without_truncation(tmp_path):
    from kristall.store import MAX_JOURNAL_FRAME_BYTES

    path = tmp_path / "oversized.memory"
    with Kristall(path) as store:
        store.set("safe", 1)
        assert store.commit() == 1
    with path.open("ab") as stream:
        stream.write(b"x" * (MAX_JOURNAL_FRAME_BYTES + 1))
    before = path.stat().st_size
    with pytest.raises(KristallError, match="journal frame exceeds size limit"):
        Kristall(path)
    assert path.stat().st_size == before


def test_oversized_commit_rejected_without_publishing(tmp_path):
    from kristall.store import MAX_JOURNAL_FRAME_BYTES

    path = tmp_path / "oversized-write.memory"
    with Kristall(path) as store:
        store.set("large", "x" * MAX_JOURNAL_FRAME_BYTES)
        with pytest.raises(KristallError, match="journal frame exceeds size limit"):
            store.commit()
        assert store.last_commit.sequence == 0
        assert not path.exists()


def test_corrupt_complete_frame_fails_closed(tmp_path):
    path = tmp_path / "corrupt.memory"
    with Kristall(path) as store:
        store.set("safe", "committed")
        assert store.commit() == 1
    with path.open("ab") as stream:
        stream.write(b'{"payload": {}, "digest": "bad"}\n')
    original = path.read_bytes()
    with pytest.raises(KristallError, match="corrupt committed journal"):
        Kristall(path)
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
    with pytest.raises(KristallError, match="corrupt committed journal"):
        _JournalStore._apply_verified_frame(frame, state, 0, "0" * 64)
    assert state == {"values": {"stable": {"version": 1}}}
