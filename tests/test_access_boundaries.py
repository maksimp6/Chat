"""Data existence, request and runtime boundaries exercised through the HTTP API."""

from __future__ import annotations

import pytest

import app as app_module
import chat.mcp_routes as mcp_routes
from agent_memory.client import close_memory_clients
from kristall import KristallError
from runtime.loader import RuntimeLoader, RuntimeLoadError
from storage import db
from tool_providers import termux_system


@pytest.fixture(autouse=True)
def isolated_memory(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    yield
    close_memory_clients()


def test_conversation_settings_are_denied_without_owner_access(monkeypatch):
    client = app_module.app.test_client()

    monkeypatch.setattr(app_module, "get_current_owner_id", lambda required=False: None)
    assert client.get("/api/conversations/c1/settings").status_code == 404
    assert client.put("/api/conversations/c1/settings", json={"x": 1}).status_code == 404

    monkeypatch.setattr(app_module, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(app_module, "check_access", lambda conv_id, owner_id: False)
    assert client.get("/api/conversations/c1/settings").status_code == 404
    assert client.put("/api/conversations/c1/settings", json={"x": 1}).status_code == 404


def test_conversation_settings_are_read_and_written_for_the_owner(monkeypatch):
    saved = []
    monkeypatch.setattr(app_module, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(app_module, "check_access", lambda conv_id, owner_id: True)
    monkeypatch.setattr(app_module, "get_conv_settings", lambda conv_id: {"temperature": 0.2})
    monkeypatch.setattr(
        app_module, "save_conv_settings", lambda conv_id, data: saved.append((conv_id, data))
    )
    client = app_module.app.test_client()

    assert client.get("/api/conversations/c1/settings").get_json() == {"temperature": 0.2}
    assert client.put("/api/conversations/c1/settings", json={"temperature": 0.5}).status_code == 200
    assert saved == [("c1", {"temperature": 0.5})]




class _FailingExecutor:
    """Stands in for the real executor: the token check runs first, then the call fails."""

    def __init__(self, registry):
        self.registry = registry

    def execute(self, call):
        return {"success": False, "error": "stubbed", "metadata": {"phase": "execution"}}


def _approval_request(client, arguments, token=None):
    body = {
        "conversation_id": "c1",
        "name": "run_command",
        "arguments": arguments,
        "model": "yandexgpt-5-lite",
    }
    if token is not None:
        body["approval_token"] = token
    return client.post("/api/mcp/execute-approved", json=body)






def test_memory_path_is_required_when_nothing_is_configured(monkeypatch):
    monkeypatch.delenv("LOGOS_MEMORY_PATH", raising=False)
    monkeypatch.setattr(db, "DB_PATH", "")

    with pytest.raises(KristallError, match="memory path is not configured"):
        db.memory_file_path()


@pytest.mark.parametrize(
    "source",
    [
        'value = "{0.__globals__}".format(func)\n',
        'value = "{0}".format_map({})\n',
        "value = getattr(object, 'name')\n",
    ],
)
def test_revision_loader_rejects_string_formatting_and_reflection(source):
    with pytest.raises(RuntimeLoadError):
        RuntimeLoader._validate(source, "runtime.py")


def test_revision_loader_accepts_plain_code():
    RuntimeLoader._validate("value = 1\n", "runtime.py")


def test_take_photo_requires_an_explicit_output_path():
    result = termux_system.take_photo({}, {})
    assert "output_path" in result["error"]
    assert "обязателен" in result["error"]


def test_conversation_tools_are_denied_without_owner_access(monkeypatch):
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: None)
    client = app_module.app.test_client()

    assert client.get("/api/conversations/c1/tools").status_code == 404
    denied = client.put("/api/conversations/c1/tools", json={"active_tool_categories": []})
    assert denied.status_code == 404


def test_conversation_tools_are_read_for_the_owner(monkeypatch):
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(mcp_routes, "check_access", lambda conv_id, owner_id: True)
    monkeypatch.setattr(
        mcp_routes, "get_conv_settings", lambda conv_id: {"active_tool_categories": ["web"]}
    )

    response = app_module.app.test_client().get("/api/conversations/c1/tools")
    assert response.get_json() == {"active_tool_categories": ["web"]}




def test_memory_config_rejects_unknown_keys_and_wrong_types(monkeypatch):
    monkeypatch.setattr("app.get_current_owner_id", lambda required=True: "owner-1")
    client = app_module.app.test_client()

    unknown = client.put("/api/memory/config", json={"shell": "rm -rf /"})
    assert unknown.status_code == 400
    assert "unknown memory config keys" in unknown.get_json()["error"]

    not_an_int = client.put("/api/memory/config", json={"max_context_facts": "abc"})
    assert not_an_int.status_code == 400

    too_many = client.put("/api/memory/config", json={"max_context_facts": 10_000})
    assert too_many.status_code == 400


def test_memory_config_accepts_a_valid_update(monkeypatch):
    monkeypatch.setattr("app.get_current_owner_id", lambda required=True: "owner-1")
    client = app_module.app.test_client()

    response = client.put(
        "/api/memory/config",
        json={"enabled": False, "max_context_facts": 3, "categories_allowed": ["paths"]},
    )
    assert response.status_code == 200
    config = response.get_json()["config"]
    assert config["enabled"] is False
    assert config["max_context_facts"] == 3
    assert config["categories_allowed"] == ["paths"]
