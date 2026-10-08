"""The inspection tool only calls a typed, already-bound Memory DB contract."""

from memory_engine import MemoryStore
from memory_engine.store import bind_database_info
from tool_providers.filesystem import memory_inspect


def test_inspector_reports_only_last_commit(tmp_path):
    with MemoryStore(tmp_path / "alice.memory") as store:
        store.set("private", "token", "PRIVATE_TOKEN")
        bind_database_info(store)
        try:
            assert memory_inspect({}) == {"last_commit": 1}
            assert "PRIVATE_TOKEN" not in str(memory_inspect({}))
        finally:
            bind_database_info(None)


def test_inspector_rejects_user_supplied_options():
    for options in ({"namespace": "secrets"}, {"db_path": "../private"}, {"limit": 1}):
        assert "error" in memory_inspect(options)


def test_inspector_has_no_implicit_database_owner():
    bind_database_info(None)
    assert "error" in memory_inspect({})


def test_inspector_does_not_read_journal(tmp_path, monkeypatch):
    with MemoryStore(tmp_path / "alice.memory") as store:
        store.set("items", "a", 1)
        bind_database_info(store)
        try:
            def unexpected(*_args, **_kwargs):
                raise AssertionError("inspection must not open journal")

            monkeypatch.setattr(MemoryStore, "_inspect_log", unexpected)
            assert memory_inspect({}) == {"last_commit": 1}
        finally:
            bind_database_info(None)


def test_closed_database_owner_is_unavailable(tmp_path):
    store = MemoryStore(tmp_path / "alice.memory")
    bind_database_info(store)
    store.close()
    try:
        assert "error" in memory_inspect({})
    finally:
        bind_database_info(None)
