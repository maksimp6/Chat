"""Functional tests for synchronous MemoryStore, independent of SQL."""
import multiprocessing
import pytest

from memory_store import MemoryStore, StoreError


def _attempt_second_writer(path, result):
    try:
        with MemoryStore(path):
            result.put("opened")
    except StoreError:
        result.put("blocked")


def test_committed_values_survive_reopen(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("chats", "1", {"text": "hello"})
        assert db.last_commit.sequence == 1
    with MemoryStore(path) as db:
        assert db.get("chats", "1") == {"text": "hello"}


def test_transaction_rollback_does_not_persist(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "safe", 1)
        with pytest.raises(ValueError):
            with db.transaction() as tx:
                tx.set("items", "bad", 2)
                raise ValueError("abort")
        assert db.last_commit.sequence == 1
    with MemoryStore(path) as db:
        assert db.get("items", "bad") is None
        assert db.get("items", "safe") == 1


def test_transaction_is_one_commit(tmp_path):
    with MemoryStore(tmp_path / "alice.memory") as db:
        with db.transaction() as tx:
            tx.set("items", "a", 1)
            tx.set("items", "b", 2)
        assert db.last_commit.sequence == 1
        assert db.get("items", "b") == 2


def test_torn_journal_fails_closed(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("items", "a", 1)
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    with pytest.raises(StoreError, match="incomplete journal tail"):
        MemoryStore(path)


def test_second_process_cannot_write(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path):
        ctx = multiprocessing.get_context("spawn")
        result = ctx.Queue()
        proc = ctx.Process(target=_attempt_second_writer, args=(path, result))
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 0
        assert result.get(timeout=2) == "blocked"


def test_mutable_read_cannot_change_store(tmp_path):
    with MemoryStore(tmp_path / "alice.memory") as db:
        db.set("items", "a", {"x": [1]})
        copy = db.get("items", "a")
        copy["x"].append(2)
        assert db.get("items", "a") == {"x": [1]}
