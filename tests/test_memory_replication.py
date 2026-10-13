"""Durable receiver contracts; no phones or network required."""

import hmac
import json
import os

import pytest

from memory_engine.replication import ReplicaSink, ReplicationError
from memory_engine.store import MemoryStore, StoreError

KEY = b"k" * 32


def packet(sequence=1, previous="0" * 64, changes=..., **overrides):
    payload = dict(
        source="source-a",
        epoch=1,
        sequence=sequence,
        previous=previous,
        changes=changes if changes is not ... else [{"op": "set", "name": "item", "value": 7}],
    )
    payload.update(overrides)
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return b"MR01" + hmac.digest(KEY, body, "sha256") + body


def sink(store):
    return ReplicaSink(store, source="source-a", epoch=1, key=KEY)


def test_apply_and_lost_ack_retry_after_reopen(tmp_path):
    path = tmp_path / "replica"
    with MemoryStore(path) as store:
        receipt = sink(store).apply(packet())
        assert receipt.status == "DURABLY_APPLIED" and store.get("item") == 7
        assert store.last_commit.sequence == 1
    with MemoryStore(path) as store:
        retry = sink(store).apply(packet())
        assert retry == receipt and store.last_commit.sequence == 1


def test_order_delete_and_private_cursor(tmp_path):
    with MemoryStore(tmp_path / "db") as store:
        receiver = sink(store)
        first = receiver.apply(packet())
        receiver.apply(packet(2, first.cursor.digest, [{"op": "delete", "name": "item"}]))
        assert store.get("item") is None and store.last_commit.sequence == 2
        # User names cannot address the internal namespace.
        assert store.get("cursor") is None


@pytest.mark.parametrize(
    "mutation",
    [
        {"source": "other"},
        {"epoch": 2},
        {"sequence": True},
        {"sequence": 2},
        {"previous": "f" * 64},
        {"changes": [{"op": "set", "name": "ok", "value": 1}, {"op": "invalid", "name": "bad"}]},
    ],
)
def test_invalid_batch_has_no_partial_effect(tmp_path, mutation):
    with MemoryStore(tmp_path / "db") as store:
        with pytest.raises(ReplicationError):
            sink(store).apply(packet(**mutation))
        assert store.get("ok") is None and store.last_commit.sequence == 0


@pytest.mark.parametrize("bad", [b"", b"MR01" + b"x" * 40, b"x" * (1024 * 1024 + 1)])
def test_authentication_and_size_fail_closed(tmp_path, bad):
    with MemoryStore(tmp_path / "db") as store:
        with pytest.raises(ReplicationError):
            sink(store).apply(bad)
        assert store.last_commit.sequence == 0


def test_same_sequence_different_digest_rejected(tmp_path):
    with MemoryStore(tmp_path / "db") as store:
        receiver = sink(store)
        receiver.apply(packet())
        with pytest.raises(ReplicationError):
            receiver.apply(packet(changes=[{"op": "set", "name": "item", "value": 8}]))
        assert store.get("item") == 7 and store.last_commit.sequence == 1


def test_staging_not_committed_by_receiver(tmp_path):
    with MemoryStore(tmp_path / "db") as store:
        store.set("local", "pending")
        with pytest.raises(ReplicationError):
            sink(store).apply(packet())
        assert store.last_commit.sequence == 0 and store.get("item") is None


def test_uncertain_fsync_reopen_recovers_data_and_cursor_together(tmp_path, monkeypatch):
    path = tmp_path / "db"
    store = MemoryStore(path)
    real_fsync = os.fsync

    def fail(_fd):
        raise OSError("injected fsync failure")

    monkeypatch.setattr(os, "fsync", fail)
    with pytest.raises(StoreError):
        sink(store).apply(packet())
    store.close()
    monkeypatch.setattr(os, "fsync", real_fsync)
    # Complete bytes may survive failed fsync; never invent rollback.
    with MemoryStore(path) as store:
        before = store.last_commit.sequence
        receipt = sink(store).apply(packet())
        assert store.get("item") == 7 and receipt.status == "DURABLY_APPLIED"
        assert before == store.last_commit.sequence == 1


def test_authenticated_noncanonical_body_rejected(tmp_path):
    body = b'{"source":"source-a","source":"source-a"}'
    wire = b"MR01" + hmac.digest(KEY, body, "sha256") + body
    with MemoryStore(tmp_path / "db") as store:
        with pytest.raises(ReplicationError):
            sink(store).apply(wire)
        assert store.last_commit.sequence == 0


def test_retry_cannot_ack_when_reconfirmation_fsync_fails(tmp_path, monkeypatch):
    path = tmp_path / "db"
    with MemoryStore(path) as store:
        sink(store).apply(packet())
    with MemoryStore(path) as store:

        def fail(_fd):
            raise OSError("injected barrier failure")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(StoreError):
            sink(store).apply(packet())
        assert store.last_commit.sequence == 1


def test_concurrent_duplicate_has_one_effect(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    with MemoryStore(tmp_path / "db") as store:
        receiver = sink(store)
        with ThreadPoolExecutor(max_workers=4) as pool:
            receipts = list(pool.map(lambda _: receiver.apply(packet()), range(8)))
        assert len(set(receipts)) == 1 and store.last_commit.sequence == 1


def test_existing_local_database_not_adopted(tmp_path):
    with MemoryStore(tmp_path / "db") as store:
        store.set("local", 1)
        store.commit()
        with pytest.raises(ReplicationError):
            sink(store).apply(packet())
        assert store.get("local") == 1 and store.get("item") is None


def test_local_replica_mutation_fences_retries(tmp_path):
    with MemoryStore(tmp_path / "db") as store:
        receiver = sink(store)
        receiver.apply(packet())
        store.set("item", 99)
        store.commit()
        with pytest.raises(ReplicationError):
            receiver.apply(packet())
        assert store.get("item") == 99


@pytest.mark.parametrize(
    "mutation",
    [
        {"previous": "bad"},
        {"changes": []},
        {"changes": None},
        {"changes": [{"op": "delete", "name": ""}]},
        {"changes": [{"op": "delete", "name": "x"}, {"op": "delete", "name": "x"}]},
        {"extra": 1},
    ],
)
def test_more_invalid_envelopes(tmp_path, mutation):
    with MemoryStore(tmp_path / "db") as store:
        with pytest.raises(ReplicationError):
            sink(store).apply(packet(**mutation))
        assert store.last_commit.sequence == 0


@pytest.mark.parametrize("overrides", [{"source": ""}, {"epoch": True}, {"key": b"short"}])
def test_invalid_receiver_configuration(tmp_path, overrides):
    with MemoryStore(tmp_path / "db") as store:
        cfg = dict(source="source-a", epoch=1, key=KEY)
        cfg.update(overrides)
        with pytest.raises(ReplicationError):
            ReplicaSink(store, **cfg)


def test_valid_mac_noncanonical_json(tmp_path):
    valid = packet()[36:]
    body = b" " + valid
    with MemoryStore(tmp_path / "db") as store:
        with pytest.raises(ReplicationError):
            sink(store).apply(b"MR01" + hmac.digest(KEY, body, "sha256") + body)


def test_authority_rebinding_rejected(tmp_path):
    with MemoryStore(tmp_path / "db") as store:
        sink(store).apply(packet())
        other = ReplicaSink(store, source="other", epoch=1, key=KEY)
        with pytest.raises(ReplicationError):
            other.apply(packet(source="other"))


def test_poisoned_receiver_and_confirmation_rejected(tmp_path, monkeypatch):
    with MemoryStore(tmp_path / "db") as store:

        def fail(_fd):
            raise OSError("injected")

        monkeypatch.setattr(os, "fsync", fail)
        with pytest.raises(StoreError):
            sink(store).apply(packet())
        with pytest.raises(StoreError):
            sink(store).apply(packet())
        with pytest.raises(StoreError):
            store.confirm_durable()


def test_empty_confirmation_and_directory_failure(tmp_path, monkeypatch):
    with MemoryStore(tmp_path / "db") as store:
        assert store.confirm_durable().sequence == 0
        sink(store).apply(packet())
        real = os.fsync
        count = 0

        def fail_directory(fd):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("directory barrier failure")
            real(fd)

        monkeypatch.setattr(os, "fsync", fail_directory)
        with pytest.raises(StoreError):
            sink(store).apply(packet())
        assert count == 2
