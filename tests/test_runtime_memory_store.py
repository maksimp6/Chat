"""Registry uses exactly one owner for each journal path."""

import pytest

from agent_memory.runtime_store import clear_runtime_memory_db, get_runtime_memory_db
from memory_engine.store import StoreError


def test_registry_owns_one_native_engine_and_reopens(tmp_path):
    path = tmp_path / "alice.memory"
    first = get_runtime_memory_db(path)
    assert first is get_runtime_memory_db(path)
    first.set("a", 1)
    first.commit()
    with pytest.raises(StoreError, match="already has a writer"):
        from memory_engine.store import MemoryStore
        MemoryStore(path)
    clear_runtime_memory_db(path)
    with pytest.raises(StoreError, match="closed"):
        first.get("a")
    reopened = get_runtime_memory_db(path)
    assert reopened is not first
    assert reopened.get("a") == 1
    clear_runtime_memory_db(path)


def test_registry_closes_every_cached_path(tmp_path):
    first = get_runtime_memory_db(tmp_path / "a.memory")
    second = get_runtime_memory_db(tmp_path / "b.memory")
    first.set("key", "one")
    first.commit()
    second.set("key", "two")
    second.commit()
    clear_runtime_memory_db()
    assert get_runtime_memory_db(tmp_path / "a.memory").get("key") == "one"
    assert get_runtime_memory_db(tmp_path / "b.memory").get("key") == "two"
    clear_runtime_memory_db()
