"""Registry uses exactly one owner for each journal path."""

import pytest

from agent_memory.client import close_memory_clients, get_memory_client
from kristall.store import KristallError


def test_registry_owns_one_native_engine_and_reopens(tmp_path):
    path = tmp_path / "logos.memory"
    first = get_memory_client(path)
    assert first is get_memory_client(path)
    first.set("a", 1)
    first.commit()
    with pytest.raises(KristallError, match="already has a writer"):
        from kristall.store import Kristall

        Kristall(path)
    close_memory_clients(path)
    with pytest.raises(KristallError, match="closed"):
        first.get("a")
    reopened = get_memory_client(path)
    assert reopened is not first
    assert reopened.get("a") == 1
    close_memory_clients(path)


def test_registry_closes_every_cached_path(tmp_path):
    first = get_memory_client(tmp_path / "a.memory")
    second = get_memory_client(tmp_path / "b.memory")
    first.set("key", "one")
    first.commit()
    second.set("key", "two")
    second.commit()
    close_memory_clients()
    assert get_memory_client(tmp_path / "a.memory").get("key") == "one"
    assert get_memory_client(tmp_path / "b.memory").get("key") == "two"
    close_memory_clients()
