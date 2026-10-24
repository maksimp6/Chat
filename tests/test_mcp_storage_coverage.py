"""Native MCP storage behavior, lifecycle and uncertain-write failures."""

import pytest
import storage.db as db
import mcp_server.storage as mcp_storage
from agent_memory.client import close_memory_clients
from kristall import KristallError


def _server_data(**overrides):
    return {
        "name": "Local Git",
        "connector_id": "local_git",
        "config": {"timeout": 99},
        **overrides,
    }


def test_server_crud_settings_and_config_round_trip(monkeypatch):
    monkeypatch.setattr(mcp_storage.uuid, "uuid4", lambda: "server-1")
    mcp_storage.init_db()
    sid = mcp_storage.create_server(_server_data(allowed_tools_read_only=True))
    assert sid == "server-1"
    assert mcp_storage.get_server(sid)["allowed_tools_read_only"] is True
    assert [item["id"] for item in mcp_storage.list_servers()] == [sid]
    mcp_storage.save_conv_mcp_settings("chat", [sid], {sid: "always"})
    assert mcp_storage.get_conv_mcp_settings("chat") == {
        "enabled_mcp_servers": [sid],
        "mcp_approval": {sid: "always"},
    }
    assert [item["id"] for item in mcp_storage.get_enabled_servers_for_conv("chat")] == [sid]
    assert mcp_storage.get_server_config(sid)["timeout"] == 99
    mcp_storage.set_server_config(sid, {"timeout": 7})
    close_memory_clients()
    assert mcp_storage.get_server_config(sid)["timeout"] == 7
    mcp_storage.update_server(sid, {"name": "Updated"})
    assert mcp_storage.get_server(sid)["name"] == "Updated"
    mcp_storage.delete_server(sid)
    assert mcp_storage.get_server(sid) is None
    assert mcp_storage.get_enabled_servers_for_conv("chat") == []


def test_missing_records_and_defaults():
    assert mcp_storage.get_server("missing") is None
    assert mcp_storage.get_server_config("missing") == {}
    assert mcp_storage.get_conv_mcp_settings("missing") == {
        "enabled_mcp_servers": [],
        "mcp_approval": {},
    }
    mcp_storage.update_server("missing", {"name": "ignored"})
    mcp_storage.set_server_config("missing", {})
    assert mcp_storage.list_servers() == []


@pytest.mark.parametrize(
    "operation",
    [
        lambda: mcp_storage.init_db(),
        lambda: mcp_storage.list_servers(),
        lambda: mcp_storage.get_server("id"),
        lambda: mcp_storage.get_conv_mcp_settings("chat"),
        lambda: mcp_storage.create_server(_server_data()),
    ],
)
def test_storage_failure_is_not_reported_as_empty_or_success(monkeypatch, operation):
    def fail():
        raise KristallError("recovery required")

    monkeypatch.setattr(mcp_storage, "get_store", fail)
    with pytest.raises(KristallError, match="recovery required"):
        operation()


def test_failed_server_write_preserves_committed_state(monkeypatch):
    sid = mcp_storage.create_server(_server_data())
    store = db.get_store()

    def fail(_frame):
        raise OSError("injected disk failure")

    monkeypatch.setattr(store._engine, "_append_frame", fail)
    with pytest.raises(OSError):
        mcp_storage.update_server(sid, {"name": "failed"})
    assert mcp_storage.get_server(sid)["name"] == "Local Git"
    with pytest.raises(KristallError, match="recovery required"):
        mcp_storage.update_server(sid, {"name": "retry"})
