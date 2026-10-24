"""Native MCP storage behavior, owner scoping, lifecycle and uncertain-write failures."""

import pytest
import storage.db as db
import mcp_server.storage as mcp_storage
from agent_memory.client import close_memory_clients
from kristall import KristallError

OWNER = "owner-a"
OTHER = "owner-b"


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
    sid = mcp_storage.create_server(OWNER, _server_data(allowed_tools_read_only=True))
    assert sid == "server-1"
    assert mcp_storage.get_server(sid, owner_id=OWNER)["allowed_tools_read_only"] is True
    assert [item["id"] for item in mcp_storage.list_servers(owner_id=OWNER)] == [sid]
    mcp_storage.save_conv_mcp_settings("chat", [sid], {sid: "always"})
    assert mcp_storage.get_conv_mcp_settings("chat") == {
        "enabled_mcp_servers": [sid],
        "mcp_approval": {sid: "always"},
    }
    enabled = mcp_storage.get_enabled_servers_for_conv("chat", owner_id=OWNER)
    assert [item["id"] for item in enabled] == [sid]
    assert mcp_storage.get_server_config(sid, owner_id=OWNER)["timeout"] == 99
    mcp_storage.set_server_config(sid, OWNER, {"timeout": 7})
    close_memory_clients()
    assert mcp_storage.get_server_config(sid, owner_id=OWNER)["timeout"] == 7
    assert mcp_storage.update_server(sid, OWNER, {"name": "Updated"}) is True
    assert mcp_storage.get_server(sid, owner_id=OWNER)["name"] == "Updated"
    mcp_storage.delete_server(sid, OWNER)
    assert mcp_storage.get_server(sid, owner_id=OWNER) is None
    assert mcp_storage.get_enabled_servers_for_conv("chat", owner_id=OWNER) == []


def test_other_owners_cannot_read_change_delete_or_enable_a_server():
    mcp_storage.init_db()
    sid = mcp_storage.create_server(OWNER, _server_data())
    try:
        assert mcp_storage.get_server(sid, owner_id=OTHER) is None
        assert mcp_storage.list_servers(owner_id=OTHER) == []
        assert mcp_storage.update_server(sid, OTHER, {"name": "hijack"}) is False
        mcp_storage.delete_server(sid, OTHER)
        mcp_storage.save_conv_mcp_settings("other-chat", [sid], {})
        assert mcp_storage.get_enabled_servers_for_conv("other-chat", owner_id=OTHER) == []
        assert mcp_storage.get_server_config(sid, owner_id=OTHER) == {}
        assert mcp_storage.get_server(sid, owner_id=OWNER)["name"] == "Local Git"
    finally:
        mcp_storage.delete_server(sid, OWNER)
        mcp_storage.save_conv_mcp_settings("other-chat", [], {})


def test_request_cannot_set_identity_fields():
    sid = mcp_storage.create_server(
        OWNER, _server_data(id="forged", owner_id=OTHER, created_at=1)
    )
    try:
        assert sid != "forged"
        assert mcp_storage.get_server("forged", owner_id=OTHER) is None
        assert mcp_storage.get_server(sid, owner_id=OWNER)["owner_id"] == OWNER
    finally:
        mcp_storage.delete_server(sid, OWNER)


@pytest.mark.parametrize(
    "server_url",
    [
        "http://example.test/mcp",
        "https://user:secret@example.test/mcp",
        "https:///mcp",
        "ftp://example.test/mcp",
        ["https://example.test/mcp"],
    ],
)
def test_server_url_must_be_https_without_credentials(server_url):
    with pytest.raises(ValueError):
        mcp_storage.create_server(OWNER, _server_data(server_url=server_url))


def test_records_without_an_owner_are_invisible_to_everyone():
    with db.get_store().transaction() as tx:
        tx.set("mcp/server/legacy", {**mcp_storage._DEFAULTS, "id": "legacy", "created_at": 1})
    try:
        assert mcp_storage.get_server("legacy", owner_id=OWNER) is None
        assert mcp_storage.list_servers(owner_id=OWNER) == []
        assert mcp_storage.list_servers(owner_id="") == []
    finally:
        with db.get_store().transaction() as tx:
            tx.delete("mcp/server/legacy")


def test_missing_records_and_defaults():
    assert mcp_storage.get_server("missing", owner_id=OWNER) is None
    assert mcp_storage.get_server_config("missing", owner_id=OWNER) == {}
    assert mcp_storage.get_conv_mcp_settings("missing") == {
        "enabled_mcp_servers": [],
        "mcp_approval": {},
    }
    assert mcp_storage.update_server("missing", OWNER, {"name": "ignored"}) is False
    mcp_storage.set_server_config("missing", OWNER, {})
    assert mcp_storage.list_servers(owner_id=OWNER) == []


@pytest.mark.parametrize(
    "operation",
    [
        lambda: mcp_storage.init_db(),
        lambda: mcp_storage.list_servers(owner_id=OWNER),
        lambda: mcp_storage.get_server("id", owner_id=OWNER),
        lambda: mcp_storage.get_conv_mcp_settings("chat"),
        lambda: mcp_storage.create_server(OWNER, _server_data()),
    ],
)
def test_storage_failure_is_not_reported_as_empty_or_success(monkeypatch, operation):
    def fail():
        raise KristallError("recovery required")

    monkeypatch.setattr(mcp_storage, "get_store", fail)
    with pytest.raises(KristallError, match="recovery required"):
        operation()


def test_failed_server_write_preserves_committed_state(monkeypatch):
    sid = mcp_storage.create_server(OWNER, _server_data())
    store = db.get_store()

    def fail(_frame):
        raise OSError("injected disk failure")

    monkeypatch.setattr(store._engine, "_append_frame", fail)
    with pytest.raises(OSError):
        mcp_storage.update_server(sid, OWNER, {"name": "failed"})
    assert mcp_storage.get_server(sid, owner_id=OWNER)["name"] == "Local Git"
    with pytest.raises(KristallError, match="recovery required"):
        mcp_storage.update_server(sid, OWNER, {"name": "retry"})
