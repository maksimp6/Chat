from agent_memory.runtime_store import (
    clear_runtime_memory_db,
    get_runtime_memory_db,
)


def test_runtime_memory_store_reuses_one_writer_per_path(tmp_path):
    path = tmp_path / "alice.memory"

    first = get_runtime_memory_db(path)
    second = get_runtime_memory_db(path)

    assert first is second
    assert first.put("a", 1) == 1
    assert second.put("b", 2) == 2
    assert first.items() == {"a": 1, "b": 2}


def test_runtime_memory_store_separates_paths_and_can_reopen(tmp_path):
    first_path = tmp_path / "one.memory"
    second_path = tmp_path / "two.memory"

    first = get_runtime_memory_db(first_path)
    second = get_runtime_memory_db(second_path)
    assert first is not second

    first.put("value", "kept")
    clear_runtime_memory_db(first_path)

    reopened = get_runtime_memory_db(first_path)
    assert reopened is not first
    assert reopened.get("value") == "kept"


def test_runtime_memory_store_can_clear_all_cached_paths(tmp_path):
    first = get_runtime_memory_db(tmp_path / "one.memory")
    second = get_runtime_memory_db(tmp_path / "two.memory")

    clear_runtime_memory_db()

    assert get_runtime_memory_db(tmp_path / "one.memory") is not first
    assert get_runtime_memory_db(tmp_path / "two.memory") is not second
