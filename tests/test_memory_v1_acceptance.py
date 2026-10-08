"""RED-first acceptance tests for the standalone name/value commit API.

Tests use only the public MemoryStore interface except for explicit OS fault
injection. No Alice runtime, SQL, or historical backup data is required.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading

import pytest

from memory_engine import MemoryStore, StoreError


@pytest.mark.parametrize(
    "value",
    ["текст", 0, 42, 3.5, True, False, [], [1, "x"], {}, {"nested": [1, 2]}],
)
def test_json_values_round_trip_after_commit(tmp_path, value):
    path = tmp_path / "values.memory"
    with MemoryStore(path) as store:
        store.set("data", value)
        assert store.get("data") == value
        assert store.commit() == 1
    with MemoryStore(path) as store:
        assert store.get("data") == value


def test_missing_name_is_none(tmp_path):
    with MemoryStore(tmp_path / "missing.memory") as store:
        assert store.get("absent") is None


def test_no_commit_discards_staged_changes_on_close(tmp_path):
    path = tmp_path / "discard.memory"
    with MemoryStore(path) as store:
        store.set("name", "not committed")
        assert store.get("name") == "not committed"
    with MemoryStore(path) as store:
        assert store.get("name") is None
        assert store.last_commit.sequence == 0


def test_multiple_names_share_one_atomic_commit(tmp_path):
    path = tmp_path / "atomic.memory"
    with MemoryStore(path) as store:
        store.set("a", 1)
        store.set("b", 2)
        assert store.commit() == 1
        assert store.commit() == 1
        store.set("a", 3)
        assert store.commit() == 2
    with MemoryStore(path) as store:
        assert (store.get("a"), store.get("b")) == (3, 2)
        assert store.last_commit.sequence == 2


def test_last_staged_value_wins(tmp_path):
    with MemoryStore(tmp_path / "last.memory") as store:
        store.set("a", 1)
        store.set("a", 2)
        assert store.get("a") == 2
        assert store.commit() == 1
        assert store.get("a") == 2


def test_returned_values_cannot_mutate_committed_state(tmp_path):
    with MemoryStore(tmp_path / "copies.memory") as store:
        source = {"items": [1]}
        store.set("data", source)
        source["items"].append(2)
        assert store.get("data") == {"items": [1]}
        store.commit()
        result = store.get("data")
        result["items"].append(3)
        assert store.get("data") == {"items": [1]}


@pytest.mark.parametrize("value", [object(), float("nan"), float("inf")])
def test_invalid_json_value_is_rejected_without_commit(tmp_path, value):
    with MemoryStore(tmp_path / "invalid.memory") as store:
        store.set("bad", value)
        with pytest.raises((StoreError, TypeError, ValueError)):
            store.commit()
        assert store.last_commit.sequence == 0


def test_closed_store_rejects_operations(tmp_path):
    store = MemoryStore(tmp_path / "closed.memory")
    store.close()
    with pytest.raises(StoreError):
        store.get("name")
    with pytest.raises(StoreError):
        store.set("name", 1)
    with pytest.raises(StoreError):
        store.commit()


def test_second_writer_same_path_is_refused(tmp_path):
    path = tmp_path / "single-writer.memory"
    with MemoryStore(path):
        with pytest.raises(StoreError, match="writer"):
            MemoryStore(path)


def test_different_paths_are_independent(tmp_path):
    with MemoryStore(tmp_path / "a.memory") as first:
        with MemoryStore(tmp_path / "b.memory") as second:
            first.set("same", "first")
            second.set("same", "second")
            assert first.commit() == second.commit() == 1
            assert first.get("same") == "first"
            assert second.get("same") == "second"


def test_reopen_in_fresh_python_process(tmp_path):
    path = tmp_path / "process.memory"
    with MemoryStore(path) as store:
        store.set("text", "persisted")
        store.commit()
    script = (
        "import sys; from memory_engine import MemoryStore; "
        "db=MemoryStore(sys.argv[1]); "
        "assert db.get('text') == 'persisted'; "
        "assert db.last_commit.sequence == 1; db.close()"
    )
    result = subprocess.run(
        [sys.executable, "-c", script, str(path)],
        capture_output=True,
        text=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_corrupt_committed_journal_fails_closed(tmp_path):
    path = tmp_path / "corrupt.memory"
    with MemoryStore(path) as store:
        store.set("data", "good")
        store.commit()
    original = path.read_bytes()
    assert original
    path.write_bytes(original.replace(b"good", b"evil", 1))
    with pytest.raises(StoreError, match="corrupt"):
        MemoryStore(path)


def test_incomplete_journal_tail_requires_recovery(tmp_path):
    path = tmp_path / "tail.memory"
    with MemoryStore(path) as store:
        store.set("data", 1)
        store.commit()
    with path.open("ab") as output:
        output.write(b'{"payload":')
    with pytest.raises(StoreError):
        MemoryStore(path)


def test_failed_fsync_never_acknowledges_commit(tmp_path, monkeypatch):
    path = tmp_path / "fsync.memory"
    with MemoryStore(path) as store:
        store.set("data", "pending")
        original_fsync = os.fsync

        def fail_fsync(_fd):
            raise OSError("simulated disk failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)
        try:
            with pytest.raises(StoreError):
                store.commit()
        finally:
            monkeypatch.setattr(os, "fsync", original_fsync)
        assert store.last_commit.sequence == 0
        with pytest.raises(StoreError):
            store.commit()


def test_backup_restore_and_subsequent_commit(tmp_path):
    source = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    destination = tmp_path / "restored.memory"
    with MemoryStore(source) as store:
        store.set("data", {"count": 1})
        store.commit()
        store.backup(backup)
    with MemoryStore(destination) as store:
        store.restore(backup)
        assert store.get("data") == {"count": 1}
        store.set("data", {"count": 2})
        assert store.commit() == 2
    with MemoryStore(destination) as store:
        assert store.get("data") == {"count": 2}


def test_two_threads_cannot_claim_same_queued_record(tmp_path):
    with MemoryStore(tmp_path / "queue.memory") as store:
        store.set("tasks/1", {"status": "queued"})
        store.commit()
        barrier = threading.Barrier(2)
        claimed = []
        errors = []

        def worker():
            try:
                barrier.wait(timeout=5)
                with store.transaction() as tx:
                    task = tx.get("tasks/1")
                    if task["status"] == "queued":
                        tx.set("tasks/1", {"status": "running", "owner": threading.get_ident()})
                        won = True
                    else:
                        won = False
                if won:
                    claimed.append(threading.get_ident())
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert not any(thread.is_alive() for thread in threads)
        assert not errors
        assert len(claimed) == 1
        assert store.get("tasks/1")["owner"] == claimed[0]


def test_backup_contains_only_committed_state(tmp_path):
    """Staged values are not silently included in an exported backup."""
    path = tmp_path / "source.memory"
    backup = tmp_path / "backup.memory"
    with MemoryStore(path) as store:
        store.set("saved", 1)
        store.commit()
        store.set("staged", 2)
        store.backup(backup)
    with MemoryStore(backup) as restored:
        assert restored.get("saved") == 1
        assert restored.get("staged") is None


def test_failed_commit_keeps_last_confirmed_version(tmp_path, monkeypatch):
    """A failed durable barrier must not acknowledge a new commit."""
    path = tmp_path / "failure.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        assert store.commit() == 1
        store.set("new", 2)
        original_fsync = os.fsync

        def fail_fsync(_fd):
            raise OSError("simulated fsync failure")

        monkeypatch.setattr(os, "fsync", fail_fsync)
        try:
            with pytest.raises(StoreError):
                store.commit()
        finally:
            monkeypatch.setattr(os, "fsync", original_fsync)
        assert store.last_commit.sequence == 1
        with pytest.raises(StoreError):
            store.set("later", 3)
        with pytest.raises(StoreError):
            store.commit()
    # A failed fsync leaves an ambiguous disk outcome. No automatic recovery
    # or silent truncation is permitted; a separate recovery test must prove it.


def test_close_rejected_during_transaction_keeps_staging(tmp_path):
    """A rejected close must not silently discard staged data."""
    path = tmp_path / "close.memory"
    with MemoryStore(path) as store:
        store.set("pending", "keep")
        with pytest.raises(StoreError):
            with store._engine.transaction():
                store.close()
        assert store.get("pending") == "keep"
        assert store.commit() == 1
    with MemoryStore(path) as reopened:
        assert reopened.get("pending") == "keep"


def test_committed_sequence_matches_replayed_journal(tmp_path):
    """The acknowledged sequence must equal the journal's recovered sequence."""
    path = tmp_path / "sequence.memory"
    with MemoryStore(path) as store:
        for index in range(3):
            store.set("counter", index)
            assert store.commit() == index + 1
        confirmed = store.last_commit
    with MemoryStore(path) as reopened:
        assert reopened.last_commit == confirmed
        assert reopened.get("counter") == 2


def test_close_is_idempotent(tmp_path):
    """Repeated close must not fail after the writer lock is released."""
    store = MemoryStore(tmp_path / "idempotent.memory")
    store.set("temporary", 1)
    store.close()
    store.close()
    with MemoryStore(tmp_path / "idempotent.memory") as reopened:
        assert reopened.get("temporary") is None


def test_value_transaction_rollback_on_exception(tmp_path):
    """One failed transaction must not publish staged values."""
    path = tmp_path / "rollback.memory"
    with MemoryStore(path) as store:
        store.set("counter", 0)
        store.commit()
        with pytest.raises(ValueError):
            with store.transaction() as tx:
                tx.set("counter", 1)
                raise ValueError("abort")
        assert store.get("counter") == 0
        assert store.last_commit.sequence == 1
    with MemoryStore(path) as reopened:
        assert reopened.get("counter") == 0


def test_value_transaction_cannot_be_nested(tmp_path):
    """Only one serialized transaction can own the journal at a time."""
    with MemoryStore(tmp_path / "nested.memory") as store:
        with store.transaction():
            with pytest.raises(StoreError):
                with store.transaction():
                    pass


def test_none_is_reserved_for_missing_name(tmp_path):
    """Reject ambiguous stored None without modifying staged or durable state."""
    path = tmp_path / "none.memory"
    with MemoryStore(path) as store:
        with pytest.raises(StoreError, match="None"):
            store.set("empty", None)
        with pytest.raises(StoreError, match="None"):
            with store.transaction() as transaction:
                transaction.set("empty", None)
        assert store.commit() == 0
        assert store.get("empty") is None
    with MemoryStore(path) as reopened:
        assert reopened.get("empty") is None


def test_complete_frame_after_uncertain_fsync_replays_on_reopen(tmp_path, monkeypatch):
    """A complete hash-valid frame can be recovered despite missing fsync ACK."""
    path = tmp_path / "uncertain.memory"
    with MemoryStore(path) as store:
        store.set("stable", 1)
        assert store.commit() == 1
        store.set("maybe", 2)
        original_fsync = os.fsync

        def uncertain_fsync(_fd):
            raise OSError("acknowledgement lost after write")

        monkeypatch.setattr(os, "fsync", uncertain_fsync)
        try:
            with pytest.raises(StoreError, match="recovery required"):
                store.commit()
        finally:
            monkeypatch.setattr(os, "fsync", original_fsync)
        assert store.last_commit.sequence == 1
        with pytest.raises(StoreError):
            store.commit()
    with MemoryStore(path) as recovered:
        assert recovered.last_commit.sequence == 2
        assert recovered.get("stable") == 1
        assert recovered.get("maybe") == 2


def test_transaction_checks_pending_after_acquiring_engine_lock(tmp_path):
    """A concurrent staged write must prevent transaction start."""
    path = tmp_path / "transaction-race.memory"
    with MemoryStore(path) as store:
        started = threading.Event()
        completed = threading.Event()
        errors = []

        def contender():
            started.set()
            try:
                with store.transaction():
                    pass
            except StoreError as exc:
                errors.append(str(exc))
            finally:
                completed.set()

        with store._engine.value_guard():
            thread = threading.Thread(target=contender)
            thread.start()
            assert started.wait(timeout=5)
            store.set("pending", "must-not-be-ignored")
        assert completed.wait(timeout=5)
        thread.join(timeout=5)
        assert errors == ["transaction requires an idle store"]
        assert store.get("pending") == "must-not-be-ignored"
        assert store.commit() == 1
