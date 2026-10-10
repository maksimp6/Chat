"""Functional regression coverage for the public synchronous MemoryStore API.

Old private namespace/key APIs are intentionally not tested: the supported
contract is get(name), set(name, value), commit(), transaction(), backup(), restore().
"""

import json
import multiprocessing

import pytest

from memory_engine import MemoryStore, StoreError


def _attempt_second_writer(path, result):
    try:
        with MemoryStore(path):
            result.put("opened")
    except StoreError:
        result.put("blocked")


def test_committed_values_survive_reopen(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("chats:1", {"text": "hello"})
        assert db.last_commit.sequence == 0
        assert db.commit() == 1
    with MemoryStore(path) as db:
        assert db.get("chats:1") == {"text": "hello"}


def test_transaction_rollback_does_not_persist(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("safe", 1)
        db.commit()
        with pytest.raises(ValueError):
            with db.transaction() as tx:
                tx.set("bad", 2)
                raise ValueError("abort")
        assert db.last_commit.sequence == 1
    with MemoryStore(path) as db:
        assert db.get("bad") is None
        assert db.get("safe") == 1


def test_transaction_is_one_commit(tmp_path):
    with MemoryStore(tmp_path / "alice.memory") as db:
        with db.transaction() as tx:
            tx.set("a", 1)
            tx.set("b", 2)
        assert db.last_commit.sequence == 1
        assert db.get("b") == 2


def test_torn_journal_fails_closed(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("a", 1)
        db.commit()
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    before = path.read_bytes()
    with pytest.raises(StoreError, match="incomplete journal tail"):
        MemoryStore(path)
    assert path.read_bytes() == before


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
        db.set("a", {"x": [1]})
        db.commit()
        copy = db.get("a")
        copy["x"].append(2)
        assert db.get("a") == {"x": [1]}


def test_transaction_handle_is_closed_after_commit(tmp_path):
    with MemoryStore(tmp_path / "alice.memory") as db:
        with db.transaction() as tx:
            tx.set("a", 1)
        with pytest.raises(StoreError, match="transaction is closed"):
            tx.set("b", 2)
        with pytest.raises(StoreError, match="transaction is closed"):
            tx.get("a")
        assert db.get("b") is None


def test_nested_transaction_is_rejected(tmp_path):
    with MemoryStore(tmp_path / "alice.memory") as db:
        with db.transaction() as outer:
            with pytest.raises(StoreError, match="nested transactions"):
                with db.transaction():
                    pass
            outer.set("ok", 1)
        assert db.get("ok") == 1


def test_direct_mutation_cannot_overwrite_active_transaction(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        with db.transaction() as tx:
            tx.set("transaction", 1)
            with pytest.raises(StoreError, match="direct mutation during transaction"):
                db.set("direct", 2)
            with pytest.raises(StoreError, match="cannot commit during transaction"):
                db.commit()
        assert db.last_commit.sequence == 1
        assert db.get("transaction") == 1
        assert db.get("direct") is None
    with MemoryStore(path) as reopened:
        assert reopened.get("transaction") == 1


def test_journal_records_only_changed_values(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("large", "x" * 10000)
        db.commit()
        db.set("small", "ok")
        db.commit()
    frames = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(frames) == 2
    assert frames[0]["payload"]["changes"][0]["key"] == "large"
    assert frames[1]["payload"]["changes"] == [
        {"op": "set", "namespace": "values", "key": "small", "value": "ok"}
    ]
    assert "x" * 10000 not in path.read_text().splitlines()[1]
    with MemoryStore(path) as reopened:
        assert reopened.get("large") == "x" * 10000
        assert reopened.get("small") == "ok"


def test_delta_transaction_overwrite_and_replay(tmp_path):
    # The public v1 API has no delete(): rewriting one key is the supported delta.
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("replace", 1)
        db.commit()
        with db.transaction() as tx:
            tx.set("replace", {"value": 2})
            tx.set("keep", {"value": 3})
        assert db.last_commit.sequence == 2
        assert db.get("replace") == {"value": 2}
    with MemoryStore(path) as reopened:
        assert reopened.get("replace") == {"value": 2}
        assert reopened.get("keep") == {"value": 3}
    frames = [json.loads(line) for line in path.read_text().splitlines()]
    assert [change["key"] for change in frames[-1]["payload"]["changes"]] == ["keep", "replace"]


def test_commit_does_not_copy_complete_ram_state(tmp_path, monkeypatch):
    """A one-name commit or transaction must not clone the whole database."""
    import memory_engine.store as memory_module
    from copy import deepcopy as original_deepcopy

    with MemoryStore(tmp_path / "alice.memory") as db:
        db.set("large", ["x" * 128] * 100)
        db.commit()
        committed = db._engine._state
        values = committed["values"]
        seen = []

        def guarded_deepcopy(value):
            seen.append(value is committed or value is values)
            assert value is not committed and value is not values
            return original_deepcopy(value)

        monkeypatch.setattr(memory_module, "deepcopy", guarded_deepcopy)
        db.set("small", {"x": 1})
        db.commit()
        with db.transaction() as tx:
            tx.set("other", 2)
            assert tx.get("small") == {"x": 1}
            assert tx.get("other") == 2
            assert db.get("other") is None
        assert not any(seen)
        assert db.last_commit.sequence == 3


def test_invalid_json_value_cannot_be_acknowledged(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("bad", object())
        with pytest.raises(StoreError, match="unsupported JSON value"):
            db.commit()
        assert db.last_commit.sequence == 0
    with MemoryStore(path) as db:
        db.set("good", {"ok": True})
        assert db.commit() == 1
    with MemoryStore(path) as db:
        assert db.get("good") == {"ok": True}
        assert db.get("bad") is None


def test_failed_fsync_does_not_acknowledge_or_publish(tmp_path, monkeypatch):
    """An uncertain write blocks further commits; it cannot be acknowledged."""
    import memory_engine.store as memory_module

    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("safe", 1)
        db.commit()
        db.set("uncertain", 2)

        def fsync_failure(_fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(memory_module.os, "fsync", fsync_failure)
        with pytest.raises(StoreError, match="commit failed"):
            db.commit()
        assert db.get("safe") == 1
        assert db.last_commit.sequence == 1
        with pytest.raises(StoreError, match="recovery required"):
            db.set("later", 3)
        with pytest.raises(StoreError, match="recovery required"):
            db.commit()


def test_close_during_transaction_cannot_release_writer_lock(tmp_path):
    path = tmp_path / "alice.memory"
    db = MemoryStore(path)
    with db.transaction() as tx:
        with pytest.raises(StoreError, match="cannot close during active transaction"):
            db.close()
        tx.set("safe", 1)
    assert db.get("safe") == 1
    db.close()
    with pytest.raises(StoreError, match="database is closed"):
        db.set("late", 2)
    with MemoryStore(path) as reopened:
        assert reopened.get("safe") == 1


def test_original_mapping_remains_immutable_until_commit(tmp_path):
    path = tmp_path / "alice.memory"
    with MemoryStore(path) as db:
        db.set("a", {"nested": [1]})
        db.commit()
        with db.transaction() as tx:
            value = tx.get("a")
            value["nested"].append(2)
            assert tx.get("a") == {"nested": [1]}
            tx.set("a", {"nested": [3]})
            assert db.get("a") == {"nested": [1]}
        assert db.get("a") == {"nested": [3]}


def test_backup_restore_preserves_chain_and_accepts_new_writes(tmp_path):
    source = tmp_path / "original.memory"
    artifact = tmp_path / "original.backup"
    destination = tmp_path / "new.memory"
    with MemoryStore(source) as db:
        db.set("a", {"value": 1})
        db.commit()
        db.set("b", {"value": 2})
        db.commit()
        expected = db.last_commit
        db.backup(artifact)
    with MemoryStore(destination) as restored:
        restored.restore(artifact)
        assert restored.last_commit == expected
        assert restored.get("a") == {"value": 1}
        restored.set("c", {"value": 3})
        restored.commit()
        assert restored.last_commit.sequence == 3
    with MemoryStore(destination) as reopened:
        assert reopened.get("c") == {"value": 3}


def test_corrupted_backup_rejected_without_touching_empty_target(tmp_path):
    backup = tmp_path / "archive.backup"
    with MemoryStore(tmp_path / "source.memory") as db:
        db.set("secret", "safe")
        db.commit()
        db.backup(backup)
    raw = backup.read_bytes()
    backup.write_bytes(raw.replace(b"safe", b"evil"))
    dest = tmp_path / "restored.memory"
    with MemoryStore(dest) as target:
        with pytest.raises(StoreError, match="corrupt committed journal"):
            target.restore(backup)
        assert target.last_commit.sequence == 0
        assert target.get("secret") is None
    assert not dest.exists()


def test_restore_never_overwrites_existing_commits(tmp_path):
    backup = tmp_path / "previous.backup"
    with MemoryStore(tmp_path / "source.memory") as source:
        source.set("a", 1)
        source.commit()
        source.backup(backup)
    destination = tmp_path / "existing.memory"
    with MemoryStore(destination) as target:
        target.set("keep", 2)
        target.commit()
        with pytest.raises(StoreError, match="restore destination is not empty"):
            target.restore(backup)
    with MemoryStore(destination) as target:
        assert target.get("keep") == 2
        assert target.get("a") is None


def test_backup_never_overwrites_existing_artifact(tmp_path):
    target_path = tmp_path / "saved.backup"
    target_path.write_bytes(b"external artifact")
    with MemoryStore(tmp_path / "source.memory") as source:
        source.set("a", 1)
        source.commit()
        with pytest.raises(StoreError, match="backup destination must be new"):
            source.backup(target_path)
    assert target_path.read_bytes() == b"external artifact"


def test_backup_destination_creation_race_does_not_overwrite(tmp_path, monkeypatch):
    """A competing destination writer must win without being overwritten."""
    from pathlib import Path

    archive = tmp_path / "shared.backup"
    with MemoryStore(tmp_path / "original.memory") as db:
        db.set("a", 1)
        db.commit()
        original_open = Path.open

        def competing_open(path, mode="r", *args, **kwargs):
            if path == archive and mode == "xb":
                archive.write_bytes(b"another backup")
            return original_open(path, mode, *args, **kwargs)

        monkeypatch.setattr(Path, "open", competing_open)
        with pytest.raises(StoreError, match="created concurrently"):
            db.backup(archive)
    assert archive.read_bytes() == b"another backup"


def test_backup_copy_failure_removes_incomplete_destination(tmp_path, monkeypatch):
    """A failed backup publication must not leave a partial final-name file."""
    import shutil

    archive = tmp_path / "partial.backup"
    with MemoryStore(tmp_path / "source.memory") as db:
        db.set("a", 1)
        db.commit()
        original_copy = shutil.copyfileobj
        calls = 0

        def failing_copy(source, target, *args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                target.write(b"partial")
                raise OSError("simulated copy failure")
            return original_copy(source, target, *args, **kwargs)

        monkeypatch.setattr(shutil, "copyfileobj", failing_copy)
        with pytest.raises(OSError, match="simulated copy failure"):
            db.backup(archive)
    assert not archive.exists()


@pytest.mark.parametrize(
    "malformed",
    [
        b"[]\n",
        b'{"payload":[],"digest":"x"}\n',
        b'{"payload":{"seq":1,"changes":[null]},"digest":"x"}\n',
    ],
)
def test_malformed_journal_fails_closed(tmp_path, malformed):
    path = tmp_path / "alice.memory"
    path.write_bytes(malformed)
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
