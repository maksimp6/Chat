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


@pytest.mark.parametrize(
    "change",
    [
        None,
        {"op": "set", "namespace": 13, "key": "x", "value": 1},
        {"op": "set", "namespace": "values", "key": 13, "value": 1},
        {"op": "unknown", "namespace": "values", "key": "x"},
    ],
)
def test_validly_hashed_invalid_change_fails_closed(tmp_path, change):
    """Hash integrity alone does not make a malformed journal frame valid."""
    import hashlib

    payload = {"seq": 1, "changes": [change]}
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    digest = hashlib.sha256(bytes(32) + canonical).hexdigest()
    frame = json.dumps({"payload": payload, "digest": digest}) + "\n"
    path = tmp_path / "invalid-change.memory"
    path.write_text(frame, encoding="utf-8")
    original = path.read_bytes()
    with pytest.raises(StoreError, match="corrupt committed journal"):
        MemoryStore(path)
    assert path.read_bytes() == original


def test_legacy_delete_record_is_recoverable(tmp_path):
    """Old journal deltas remain readable without reintroducing public delete()."""
    path = tmp_path / "delete.memory"
    with MemoryStore(path) as db:
        db.set("gone", 1)
        db.commit()
        assert not hasattr(db, "delete")
        db._engine._commit_changes([{"op": "delete", "namespace": "values", "key": "gone"}])
        assert db.last_commit.sequence == 2
        assert db.get("gone") is None
    with MemoryStore(path) as reopened:
        assert reopened.get("gone") is None
        assert reopened.last_commit.sequence == 2


def test_internal_journal_writer_rejects_nonstring_key(tmp_path):
    with MemoryStore(tmp_path / "invalid-key.memory") as db:
        with pytest.raises(StoreError, match="namespace and key must be strings"):
            db._engine._encode_frame([{"op": "set", "namespace": "values", "key": 0, "value": 1}])
        assert db.last_commit.sequence == 0


def test_short_write_blocks_commit_transaction_backup_and_restore(tmp_path, monkeypatch):
    """A short write creates uncertain durability; no subsequent operation may ACK."""
    from pathlib import Path

    path = tmp_path / "short.memory"
    with MemoryStore(path) as db:
        db.set("queued", 1)
        original_open = Path.open

        class ShortWriter:
            def __init__(self, wrapped):
                self.wrapped = wrapped

            def __enter__(self):
                self.wrapped.__enter__()
                return self

            def __exit__(self, *args):
                return self.wrapped.__exit__(*args)

            def write(self, data):
                return len(data) - 1

        def limited_open(file, mode="r", *args, **kwargs):
            stream = original_open(file, mode, *args, **kwargs)
            if file == path and mode == "ab":
                return ShortWriter(stream)
            return stream

        monkeypatch.setattr(Path, "open", limited_open)
        with pytest.raises(StoreError, match="commit failed"):
            db.commit()
        assert db.last_commit.sequence == 0
        with pytest.raises(StoreError, match="recovery required"):
            db._engine._commit_changes(
                [{"op": "set", "namespace": "values", "key": "x", "value": 2}]
            )
        with pytest.raises(StoreError, match="recovery required"):
            with db.transaction():
                pass
        with pytest.raises(StoreError, match="backup requires a healthy"):
            db.backup(tmp_path / "blocked.backup")
        with pytest.raises(StoreError, match="restore requires a healthy"):
            db.restore(tmp_path / "blocked.backup")


def test_backup_rejects_a_different_valid_journal(tmp_path, monkeypatch):
    """A copy with a valid but different hash chain cannot become a backup."""
    import shutil

    source = tmp_path / "source.memory"
    alternate = tmp_path / "alternate.memory"
    target = tmp_path / "destination.backup"
    with MemoryStore(alternate) as store:
        store.set("record", "other")
        store.commit()
    with MemoryStore(source) as store:
        store.set("record", "correct")
        store.commit()
        original_copy = shutil.copyfileobj

        def substituted_copy(reader, writer, *args, **kwargs):
            if reader.name == str(source):
                writer.write(alternate.read_bytes())
            else:
                original_copy(reader, writer, *args, **kwargs)

        monkeypatch.setattr(shutil, "copyfileobj", substituted_copy)
        with pytest.raises(StoreError, match="backup integrity mismatch"):
            store.backup(target)
        assert store.get("record") == "correct"
    assert not target.exists()


def test_failed_restore_requires_explicit_recovery(tmp_path, monkeypatch):
    """Restore I/O faults cannot publish state or permit subsequent writes."""
    source = tmp_path / "source.memory"
    backup = tmp_path / "saved.backup"
    with MemoryStore(source) as store:
        store.set("record", 123)
        store.commit()
        store.backup(backup)
    with MemoryStore(tmp_path / "target.memory") as target:

        def failing_copy(*_args, **_kwargs):
            raise OSError(5, "injected restore failure")

        monkeypatch.setattr(target._engine, "_copy_verified_file", failing_copy)
        with pytest.raises(StoreError, match="restore failed; recovery required"):
            target.restore(backup)
        assert target.last_commit.sequence == 0
        with pytest.raises(StoreError, match="recovery required"):
            target.set("record", 456)
