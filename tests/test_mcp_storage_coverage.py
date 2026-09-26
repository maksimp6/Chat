import sqlite3

import pytest

import mcp_storage


def _connect(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def _server_data(**overrides):
    data = {
        "name": "Local Git",
        "server_url": "",
        "connector_id": "local_git",
        "server_label": "Local Git",
        "server_description": "Local repository tools",
        "authorization": "",
        "headers": "{}",
        "allowed_tools": "status,log",
        "allowed_tools_read_only": True,
        "require_approval": "always",
        "require_approval_tools": "",
        "require_approval_read_only": False,
        "require_approval_never_tools": "",
        "require_approval_never_read_only": False,
        "defer_loading": True,
        "config": {"timeout": 99, "custom": "value"},
    }
    data.update(overrides)
    return data


def test_get_conn_delegates_and_reraises(monkeypatch):
    marker = object()
    monkeypatch.setattr(mcp_storage.database, "get_conn", lambda: marker)
    assert mcp_storage._get_conn() is marker

    monkeypatch.setattr(
        mcp_storage.database,
        "get_conn",
        lambda: (_ for _ in ()).throw(RuntimeError("db down")),
    )
    with pytest.raises(RuntimeError, match="db down"):
        mcp_storage._get_conn()


def test_init_db_creates_tables_and_swallows_connection_error(monkeypatch, tmp_path):
    db_path = tmp_path / "mcp.sqlite"
    monkeypatch.setattr(mcp_storage, "_get_conn", lambda: _connect(db_path))

    mcp_storage.init_db()

    conn = _connect(db_path)
    tables = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall()
    }
    conn.close()
    assert {"mcp_servers", "conv_mcp_settings"}.issubset(tables)

    monkeypatch.setattr(
        mcp_storage,
        "_get_conn",
        lambda: (_ for _ in ()).throw(RuntimeError("init failure")),
    )
    mcp_storage.init_db()


def test_server_crud_settings_and_config_round_trip(monkeypatch, tmp_path):
    db_path = tmp_path / "mcp.sqlite"
    monkeypatch.setattr(mcp_storage, "_get_conn", lambda: _connect(db_path))
    monkeypatch.setattr(mcp_storage.uuid, "uuid4", lambda: "server-1")
    mcp_storage.init_db()

    server_id = mcp_storage.create_server(_server_data())
    assert server_id == "server-1"

    server = mcp_storage.get_server(server_id)
    assert server["name"] == "Local Git"
    assert server["transport"] == "streamable"
    assert server["allowed_tools_read_only"] is True
    assert server["require_approval_read_only"] is False
    assert server["defer_loading"] is True
    assert server["config"] == {"timeout": 99, "custom": "value"}

    listed = mcp_storage.list_servers()
    assert [item["id"] for item in listed] == ["server-1"]

    updated = _server_data(
        name="Updated",
        transport="sse",
        allowed_tools_read_only=False,
        require_approval_read_only=True,
        require_approval_never_read_only=True,
        defer_loading=False,
        config={"timeout": 7},
    )
    mcp_storage.update_server(server_id, updated)
    server = mcp_storage.get_server(server_id)
    assert server["name"] == "Updated"
    assert server["transport"] == "sse"
    assert server["allowed_tools_read_only"] is False
    assert server["require_approval_read_only"] is True
    assert server["require_approval_never_read_only"] is True
    assert server["defer_loading"] is False

    assert mcp_storage.get_conv_mcp_settings("missing") == {
        "enabled_mcp_servers": [],
        "mcp_approval": {},
    }
    assert mcp_storage.get_enabled_servers_for_conv("missing") == []

    mcp_storage.save_conv_mcp_settings(
        "conv-1",
        [server_id],
        {"status": "never"},
    )
    assert mcp_storage.get_conv_mcp_settings("conv-1") == {
        "enabled_mcp_servers": ["server-1"],
        "mcp_approval": {"status": "never"},
    }
    enabled = mcp_storage.get_enabled_servers_for_conv("conv-1")
    assert [item["id"] for item in enabled] == ["server-1"]

    merged = mcp_storage.get_server_config(server_id)
    assert merged["repo_path"] == "/sdcard/alice_pro"
    assert merged["timeout"] == 7
    assert merged["default_log_limit"] == 5

    mcp_storage.set_server_config(server_id, {"timeout": 3, "new": True})
    merged = mcp_storage.get_server_config(server_id)
    assert merged["timeout"] == 3
    assert merged["new"] is True

    mcp_storage.save_conv_mcp_settings("conv-empty", [], {})
    assert mcp_storage.get_enabled_servers_for_conv("conv-empty") == []

    mcp_storage.delete_server(server_id)
    assert mcp_storage.get_server(server_id) is None
    assert mcp_storage.get_enabled_servers_for_conv("conv-1") == []
    assert mcp_storage.get_server_config(server_id) == {}


def test_server_config_invalid_json_falls_back_to_defaults(monkeypatch, tmp_path):
    db_path = tmp_path / "mcp.sqlite"
    monkeypatch.setattr(mcp_storage, "_get_conn", lambda: _connect(db_path))
    monkeypatch.setattr(mcp_storage.uuid, "uuid4", lambda: "server-bad-json")
    mcp_storage.init_db()
    server_id = mcp_storage.create_server(_server_data(config={}))

    conn = _connect(db_path)
    conn.execute("UPDATE mcp_servers SET config = ? WHERE id = ?", ("{bad", server_id))
    conn.commit()
    conn.close()

    config = mcp_storage.get_server_config(server_id)
    assert config == mcp_storage.DEFAULT_CONFIGS["local_git"]


class FailingConnection:
    def __init__(self):
        self.closed = False

    def execute(self, *args, **kwargs):
        raise RuntimeError("sql failure")

    def commit(self):
        raise AssertionError("commit must not be reached")

    def close(self):
        self.closed = True


@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (lambda: mcp_storage.list_servers(), []),
        (lambda: mcp_storage.get_server("server"), None),
        (
            lambda: mcp_storage.get_conv_mcp_settings("conv"),
            {"enabled_mcp_servers": [], "mcp_approval": {}},
        ),
        (lambda: mcp_storage.get_enabled_servers_for_conv("conv"), []),
        (lambda: mcp_storage.get_server_config("server"), {}),
    ],
)
def test_read_operations_return_safe_fallback_on_sql_error(monkeypatch, call, expected):
    conn = FailingConnection()
    monkeypatch.setattr(mcp_storage, "_get_conn", lambda: conn)

    assert call() == expected
    assert conn.closed is True


@pytest.mark.parametrize(
    "call",
    [
        lambda: mcp_storage.create_server(_server_data()),
        lambda: mcp_storage.update_server("server", _server_data()),
        lambda: mcp_storage.delete_server("server"),
        lambda: mcp_storage.save_conv_mcp_settings("conv", ["server"], {"tool": "always"}),
        lambda: mcp_storage.set_server_config("server", {"timeout": 1}),
    ],
)
def test_write_operations_reraise_sql_error_and_close_connection(monkeypatch, call):
    conn = FailingConnection()
    monkeypatch.setattr(mcp_storage, "_get_conn", lambda: conn)

    with pytest.raises(RuntimeError, match="sql failure"):
        call()
    assert conn.closed is True
