"""Record adapter regression tests against the accepted MemoryStore."""

import pytest

from agent_memory.record_adapter import MemoryRecordAdapter
from memory_engine import MemoryStore


def test_record_adapter_persists_and_recovers(tmp_path):
    path = tmp_path / "records.memory"
    with MemoryStore(path) as store:
        records = MemoryRecordAdapter(store)
        assert records.put("owner:1", {"user": "alice"}) == 1
        assert records.get("owner:1") == {"user": "alice"}
        assert records.items() == {"owner:1": {"user": "alice"}}
        assert records.delete("owner:1") == 2
        assert records.items() == {}
    with MemoryStore(path) as reopened:
        assert MemoryRecordAdapter(reopened).items() == {}
        assert reopened.last_commit.sequence == 2


def test_record_adapter_copy_isolation_and_atomic_updates(tmp_path):
    with MemoryStore(tmp_path / "records.memory") as store:
        records = MemoryRecordAdapter(store)
        value = {"nested": [1]}
        records.put("key", value)
        value["nested"].append(2)
        assert records.get("key") == {"nested": [1]}
        copied = records.items()
        copied["key"]["nested"].append(3)
        assert records.get("key") == {"nested": [1]}
        with pytest.raises(ValueError, match="key"):
            records.put("", 1)
        assert store.last_commit.sequence == 1


def test_closing_adapter_does_not_close_shared_store(tmp_path):
    with MemoryStore(tmp_path / "shared.memory") as store:
        first = MemoryRecordAdapter(store)
        second = MemoryRecordAdapter(store)
        first.put("owner:1", {"user": "alice"})
        first.close()
        assert second.get("owner:1") == {"user": "alice"}
        assert second.put("owner:2", {"user": "bob"}) == 2
