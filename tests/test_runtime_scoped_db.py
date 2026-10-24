"""Host and runtime records use different native writers and survive scope exit."""

from pathlib import Path
import pytest
import storage.db as db
from agent_memory.client import close_memory_clients
from kristall import Kristall
from runtime import bind_runtime_request, current_runtime_data_root, current_runtime_id


def test_runtime_database_scope_is_private_and_context_is_restored(tmp_path):
    first = tmp_path / "first"
    second = tmp_path / "second"
    db.set_config("probe", "host")
    with bind_runtime_request("first", "/preview/first", data_root=str(first)):
        db.set_config("probe", "alpha")
        with bind_runtime_request("second", "/preview/second", data_root=str(second)):
            assert db.get_config("probe") is None
            db.set_config("probe", "beta")
        assert db.get_config("probe") == "alpha"
        assert current_runtime_id() == "first"
    assert current_runtime_id() is None and current_runtime_data_root() is None
    assert db.get_config("probe") == "host"
    close_memory_clients()
    with Kristall(first / "alice.memory") as store:
        assert store.get("config/probe") == {"value": "alpha"}
    with Kristall(second / "alice.memory") as store:
        assert store.get("config/probe") == {"value": "beta"}


@pytest.mark.parametrize("fail_runtime", [False, True])
def test_host_scope_restored_after_runtime_error(tmp_path, monkeypatch, fail_runtime):
    monkeypatch.setenv("ALICE_DATABASE_URL", "postgresql://unused.invalid/retired")
    host = db.get_store()
    root = tmp_path / "runtime"

    def work():
        with bind_runtime_request("runtime", "/preview/runtime", data_root=str(root)):
            assert db.get_store() is not host
            db.set_config("probe", "private")
            if fail_runtime:
                raise RuntimeError("injected")

    if fail_runtime:
        with pytest.raises(RuntimeError, match="injected"):
            work()
    else:
        work()
    assert db.get_store() is host
    assert Path(root / "alice.memory").is_file()
