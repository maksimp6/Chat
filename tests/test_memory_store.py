"""Functional tests for synchronous Kristall, independent of SQL."""

import multiprocessing
import pytest

from kristall import KristallError
from kristall.store import _JournalStore as Kristall


def _attempt_second_writer(path, result):
    try:
        with Kristall(path):
            result.put("opened")
    except KristallError:
        result.put("blocked")


def test_committed_values_survive_reopen(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("chats", "1", {"text": "hello"})
        assert db.last_commit.sequence == 1
    with Kristall(path) as db:
        assert db.get("chats", "1") == {"text": "hello"}


def test_transaction_rollback_does_not_persist(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("items", "safe", 1)
        with pytest.raises(ValueError):
            with db.transaction() as tx:
                tx.set("items", "bad", 2)
                raise ValueError("abort")
        assert db.last_commit.sequence == 1
    with Kristall(path) as db:
        assert db.get("items", "bad") is None
        assert db.get("items", "safe") == 1


def test_transaction_is_one_commit(tmp_path):
    with Kristall(tmp_path / "alice.memory") as db:
        with db.transaction() as tx:
            tx.set("items", "a", 1)
            tx.set("items", "b", 2)
        assert db.last_commit.sequence == 1
        assert db.get("items", "b") == 2


def test_torn_journal_fails_closed(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("items", "a", 1)
    with path.open("ab") as stream:
        stream.write(b'{"partial":')
    with pytest.raises(KristallError, match="incomplete journal tail"):
        Kristall(path)


def test_second_process_cannot_write(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path):
        ctx = multiprocessing.get_context("spawn")
        result = ctx.Queue()
        proc = ctx.Process(target=_attempt_second_writer, args=(path, result))
        proc.start()
        proc.join(timeout=10)
        assert proc.exitcode == 0
        assert result.get(timeout=2) == "blocked"


def test_mutable_read_cannot_change_store(tmp_path):
    with Kristall(tmp_path / "alice.memory") as db:
        db.set("items", "a", {"x": [1]})
        copy = db.get("items", "a")
        copy["x"].append(2)
        assert db.get("items", "a") == {"x": [1]}


def test_transaction_handle_is_closed_after_commit(tmp_path):
    with Kristall(tmp_path / "alice.memory") as db:
        with db.transaction() as tx:
            tx.set("items", "a", 1)
        with pytest.raises(KristallError, match="transaction is closed"):
            tx.set("items", "b", 2)
        with pytest.raises(KristallError, match="transaction is closed"):
            tx.get("items", "a")
        assert db.get("items", "b") is None


def test_nested_transaction_is_rejected(tmp_path):
    with Kristall(tmp_path / "alice.memory") as db:
        with db.transaction() as outer:
            with pytest.raises(KristallError, match="nested transactions"):
                with db.transaction():
                    pass
            outer.set("items", "ok", 1)
        assert db.get("items", "ok") == 1


def test_direct_mutation_cannot_overwrite_active_transaction(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        with db.transaction() as tx:
            tx.set("items", "transaction", 1)
            with pytest.raises(KristallError, match="direct mutation during transaction"):
                db.set("items", "direct", 2)
            with pytest.raises(KristallError, match="direct mutation during transaction"):
                db.delete("items", "transaction")
        assert db.last_commit.sequence == 1
        assert db.get("items", "transaction") == 1
        assert db.get("items", "direct") is None
    with Kristall(path) as reopened:
        assert reopened.get("items", "transaction") == 1
        assert reopened.get("items", "direct") is None


def test_journal_records_only_changed_values(tmp_path):
    import json

    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("items", "large", "x" * 10000)
        db.set("items", "small", "ok")
    frames = [json.loads(line) for line in path.read_text().splitlines()]
    assert len(frames) == 2
    assert frames[0]["payload"]["changes"][0]["key"] == "large"
    assert frames[1]["payload"]["changes"] == [
        {"op": "set", "namespace": "items", "key": "small", "value": "ok"}
    ]
    assert "x" * 10000 not in path.read_text().splitlines()[1]
    with Kristall(path) as reopened:
        assert reopened.get("items", "large") == "x" * 10000
        assert reopened.get("items", "small") == "ok"


def test_delta_transaction_delete_and_replay(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("items", "remove", 1)
        with db.transaction() as tx:
            tx.delete("items", "remove")
            tx.set("items", "keep", {"value": 2})
        assert db.last_commit.sequence == 2
    with Kristall(path) as reopened:
        assert reopened.get("items", "remove") is None
        assert reopened.get("items", "keep") == {"value": 2}


def test_commit_does_not_copy_complete_ram_state(tmp_path, monkeypatch):
    """Untouched values must never be copied or diffed just to commit a new key."""
    import kristall.store as memory_module
    from copy import deepcopy as original_deepcopy

    with Kristall(tmp_path / "alice.memory") as db:
        db.set("items", "large", ["x" * 128] * 100)
        committed = db._state
        seen = []

        def guarded_deepcopy(value):
            seen.append(value is committed)
            assert value is not committed, "full database copied on write"
            return original_deepcopy(value)

        monkeypatch.setattr(memory_module, "deepcopy", guarded_deepcopy)
        db.set("items", "small", {"x": 1})
        with db.transaction() as tx:
            tx.set("items", "other", 2)
            assert tx.get("items", "small") == {"x": 1}
            assert tx.get("items", "other") == 2
            assert db.get("items", "other") is None
        assert not any(seen)
        assert db.last_commit.sequence == 3


def test_json_encoder_failure_is_reported_as_kristall_error(tmp_path, monkeypatch):
    """Encoder failures after validation surface as KristallError, not raw ValueError."""
    import kristall.store as store_module

    def failing_dumps(*_args, **_kwargs):
        raise ValueError("encoder rejected value")

    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        monkeypatch.setattr(store_module.json, "dumps", failing_dumps)
        with pytest.raises(KristallError, match="unsupported JSON value") as info:
            db.set("items", "encoder", {"ok": True})
        assert isinstance(info.value.__cause__, ValueError)


def test_invalid_json_value_does_not_poison_writer(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        with pytest.raises(KristallError, match="unsupported JSON value"):
            db.set("items", "bad", object())
        with pytest.raises(KristallError, match="unsupported JSON value"):
            db.set("items", "nan", float("nan"))
        db.set("items", "good", {"ok": True})
        assert db.last_commit.sequence == 1
    with Kristall(path) as db:
        assert db.get("items", "good") == {"ok": True}
        assert db.get("items", "bad") is None


def test_failed_fsync_does_not_acknowledge_or_publish(tmp_path, monkeypatch):
    """A failed sync has an uncertain disk outcome: block further writes."""
    import kristall.store as memory_module

    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("items", "safe", 1)

        def fsync_failure(_fd):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(memory_module.os, "fsync", fsync_failure)
        with pytest.raises(KristallError, match="commit failed"):
            db.set("items", "uncertain", 2)
        assert db.get("items", "safe") == 1
        assert db.get("items", "uncertain") is None
        assert db.last_commit.sequence == 1
        with pytest.raises(KristallError, match="recovery required"):
            db.set("items", "later", 3)


def test_close_during_transaction_cannot_release_writer_lock(tmp_path):
    path = tmp_path / "alice.memory"
    db = Kristall(path)
    with db.transaction() as tx:
        with pytest.raises(KristallError, match="cannot close during active transaction"):
            db.close()
        tx.set("items", "safe", 1)
    assert db.get("items", "safe") == 1
    db.close()
    with pytest.raises(KristallError, match="database is closed"):
        db.set("items", "late", 2)
    with Kristall(path) as reopened:
        assert reopened.get("items", "safe") == 1


def test_original_mapping_remains_immutable_until_commit(tmp_path):
    path = tmp_path / "alice.memory"
    with Kristall(path) as db:
        db.set("items", "a", {"nested": [1]})
        with db.transaction() as tx:
            value = tx.get("items", "a")
            value["nested"].append(2)
            assert tx.get("items", "a") == {"nested": [1]}
            tx.set("items", "a", {"nested": [3]})
            assert db.get("items", "a") == {"nested": [1]}
        assert db.get("items", "a") == {"nested": [3]}


def test_backup_restore_preserves_chain_and_accepts_new_writes(tmp_path):
    source = tmp_path / "original.memory"
    artifact = tmp_path / "original.backup"
    destination = tmp_path / "new.memory"
    with Kristall(source) as db:
        db.set("items", "a", {"value": 1})
        db.set("items", "b", {"value": 2})
        expected = db.last_commit
        db.backup(artifact)
    with Kristall(destination) as restored:
        restored.restore(artifact)
        assert restored.last_commit == expected
        assert restored.get("items", "a") == {"value": 1}
        restored.set("items", "c", {"value": 3})
        assert restored.last_commit.sequence == 3
    with Kristall(destination) as reopened:
        assert reopened.get("items", "c") == {"value": 3}


def test_corrupted_backup_rejected_without_touching_empty_target(tmp_path):
    backup = tmp_path / "archive.backup"
    with Kristall(tmp_path / "source.memory") as db:
        db.set("items", "secret", "safe")
        db.backup(backup)
    raw = backup.read_bytes()
    backup.write_bytes(raw.replace(b"safe", b"evil"))
    dest = tmp_path / "restored.memory"
    with Kristall(dest) as target:
        with pytest.raises(KristallError, match="corrupt committed journal"):
            target.restore(backup)
        assert target.last_commit.sequence == 0
        assert target.get("items", "secret") is None
    assert not dest.exists()


def test_restore_never_overwrites_existing_commits(tmp_path):
    backup = tmp_path / "previous.backup"
    with Kristall(tmp_path / "source.memory") as source:
        source.set("items", "a", 1)
        source.backup(backup)
    destination = tmp_path / "existing.memory"
    with Kristall(destination) as target:
        target.set("items", "keep", 2)
        with pytest.raises(KristallError, match="restore destination is not empty"):
            target.restore(backup)
    with Kristall(destination) as target:
        assert target.get("items", "keep") == 2
        assert target.get("items", "a") is None


def test_backup_never_overwrites_existing_artifact(tmp_path):
    target_path = tmp_path / "saved.backup"
    target_path.write_bytes(b"external artifact")
    with Kristall(tmp_path / "source.memory") as source:
        source.set("items", "a", 1)
        with pytest.raises(KristallError, match="backup destination must be new"):
            source.backup(target_path)
    assert target_path.read_bytes() == b"external artifact"


def test_backup_destination_creation_race_does_not_overwrite(tmp_path, monkeypatch):
    """An atomic no-overwrite publish must preserve a concurrent artifact."""
    import kristall.store as module

    archive = tmp_path / "shared.backup"
    with Kristall(tmp_path / "original.memory") as db:
        db.set("items", "a", 1)
        original_link = module.os.link

        def competing_link(source, destination):
            archive.write_bytes(b"another backup")
            return original_link(source, destination)

        monkeypatch.setattr(module.os, "link", competing_link)
        with pytest.raises(KristallError, match="created concurrently"):
            db.backup(archive)
    assert archive.read_bytes() == b"another backup"


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
    with pytest.raises(KristallError, match="corrupt committed journal"):
        Kristall(path)


def test_internal_deletion_survives_reopen(tmp_path):
    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        store.set("values", "saved", 1)
        store.delete("values", "saved")
        assert store.last_commit.sequence == 2
    with Kristall(path) as store:
        assert store.get("values", "saved") is None


def test_internal_invalid_journal_keys_fail_before_write(tmp_path):
    path = tmp_path / "state.memory"
    with Kristall(path) as store:
        with pytest.raises(KristallError, match="strings"):
            store.set("values", None, 1)
        assert not path.exists()


def test_internal_transaction_handle_refuses_foreign_thread(tmp_path):
    import threading

    errors = []
    with Kristall(tmp_path / "state.memory") as store:
        with store.transaction() as tx:

            def misuse():
                try:
                    tx.get("values", "saved")
                except KristallError as exc:
                    errors.append(str(exc))

            worker = threading.Thread(target=misuse)
            worker.start()
            worker.join(timeout=5)
            assert not worker.is_alive()
            assert errors == ["transaction belongs to another thread"]
