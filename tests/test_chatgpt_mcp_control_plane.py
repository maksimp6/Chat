import json

import pytest
from flask import Flask

import chatgpt_mcp
import db
import mcp_routes


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("ALICE_DATABASE_URL", "")
    monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-a")
    monkeypatch.setenv("ALICE_MCP_ALLOW_ANONYMOUS", "false")
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice_pro.db"))

    app = Flask(__name__)
    app.register_blueprint(chatgpt_mcp.chatgpt_mcp_bp)
    app.testing = True
    return app.test_client()


def mcp_request(client, method, params=None, request_id=1, name=None, token=None):
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    headers = {
        "Content-Type": "application/json",
        "Mcp-Method": method,
        "MCP-Protocol-Version": chatgpt_mcp.DEFAULT_PROTOCOL_VERSION,
    }
    if name is not None:
        headers["Mcp-Name"] = name
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/mcp", data=json.dumps(payload), headers=headers)


def test_mcp_requires_auth_when_anonymous_is_not_enabled(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 401
    assert response.get_json()["error"]["code"] == -32001
    assert "WWW-Authenticate" in response.headers


def test_conversation_tools_are_exposed(client):
    response = mcp_request(client, "tools/list", token="test-token")
    assert response.status_code == 200
    names = {item["name"] for item in response.get_json()["result"]["tools"]}

    assert {
        "alice_list_conversations",
        "alice_get_conversation",
        "alice_get_messages",
        "alice_create_conversation",
        "alice_send_message",
        "alice_get_execution",
        "alice_get_execution_trace",
    } <= names


def test_conversation_ownership_is_bound_to_authenticated_user(client, monkeypatch):
    monkeypatch.setattr(
        mcp_routes.AliceClient,
        "create_conversation",
        lambda self: {"id": "conversation-owned-by-a"},
    )

    created = mcp_request(
        client,
        "tools/call",
        {"name": "alice_create_conversation", "arguments": {"title": "Private"}},
        name="alice_create_conversation",
        token="test-token",
    )
    assert created.status_code == 200, created.get_json()

    listed = mcp_request(
        client,
        "tools/call",
        {"name": "alice_list_conversations", "arguments": {}},
        name="alice_list_conversations",
        token="test-token",
    )
    assert listed.status_code == 200
    conversations = listed.get_json()["result"]["structuredContent"]["conversations"]
    assert [item["id"] for item in conversations] == ["conversation-owned-by-a"]

    client.application.config["TESTING_USER_SWITCH"] = True
    # The bearer identity is configured server-side, so switching the expected
    # user simulates a separate MCP resource owner without trusting a client arg.
    client_user = client.application
    # Flask app env is read at request time, so changing this value is sufficient.
    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-b")
    other = mcp_request(
        client,
        "tools/call",
        {"name": "alice_list_conversations", "arguments": {}},
        name="alice_list_conversations",
        token="test-token",
        request_id=2,
    )
    assert other.status_code == 200
    assert other.get_json()["result"]["structuredContent"]["conversations"] == []


def test_send_message_creates_execution_and_trace(client, monkeypatch):
    monkeypatch.setattr(
        mcp_routes.AliceClient,
        "create_conversation",
        lambda self: {"id": "conversation-chat"},
    )
    monkeypatch.setattr(
        mcp_routes.AliceClient,
        "ask_with_mcp",
        lambda self, message, model_key, conversation_id, params, trace=None: {
            "fake": True
        },
    )
    monkeypatch.setattr(mcp_routes.AliceClient, "extract_text", lambda self, response: "hello from Alice")
    monkeypatch.setattr(mcp_routes.AliceClient, "extract_usage", lambda self, response: None)

    created = mcp_request(
        client,
        "tools/call",
        {"name": "alice_create_conversation", "arguments": {"model": "test-model"}},
        name="alice_create_conversation",
        token="test-token",
    )
    assert created.status_code == 200

    sent = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_send_message",
            "arguments": {
                "conversation_id": "conversation-chat",
                "message": "test message",
            },
        },
        name="alice_send_message",
        token="test-token",
    )
    assert sent.status_code == 200, sent.get_json()
    execution = sent.get_json()["result"]["structuredContent"]

    assert execution["reply"] == "hello from Alice"
    assert execution["conversation_id"] == "conversation-chat"
    assert execution["execution_id"]
    assert execution["trace_id"]

    status = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_get_execution",
            "arguments": {"execution_id": execution["execution_id"]},
        },
        name="alice_get_execution",
        token="test-token",
        request_id=3,
    )
    assert status.status_code == 200
    assert status.get_json()["result"]["structuredContent"]["execution"]["status"] == "completed"

    trace = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_get_execution_trace",
            "arguments": {"execution_id": execution["execution_id"]},
        },
        name="alice_get_execution_trace",
        token="test-token",
        request_id=4,
    )
    assert trace.status_code == 200
    assert trace.get_json()["result"]["structuredContent"]["trace"]["trace_id"] == execution["trace_id"]


def test_execution_is_not_visible_to_another_user(client, monkeypatch):
    monkeypatch.setattr(
        mcp_routes.AliceClient,
        "create_conversation",
        lambda self: {"id": "conversation-owner"},
    )
    monkeypatch.setattr(
        mcp_routes.AliceClient,
        "ask_with_mcp",
        lambda self, message, model_key, conversation_id, params, trace=None: {},
    )
    monkeypatch.setattr(mcp_routes.AliceClient, "extract_text", lambda self, response: "ok")
    monkeypatch.setattr(mcp_routes.AliceClient, "extract_usage", lambda self, response: None)

    created = mcp_request(
        client,
        "tools/call",
        {"name": "alice_create_conversation", "arguments": {}},
        name="alice_create_conversation",
        token="test-token",
    )
    assert created.status_code == 200

    sent = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_send_message",
            "arguments": {
                "conversation_id": "conversation-owner",
                "message": "hello",
            },
        },
        name="alice_send_message",
        token="test-token",
    )
    execution_id = sent.get_json()["result"]["structuredContent"]["execution_id"]

    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-b")
    denied = mcp_request(
        client,
        "tools/call",
        {"name": "alice_get_execution", "arguments": {"execution_id": execution_id}},
        name="alice_get_execution",
        token="test-token",
    )
    assert denied.status_code == 403
    assert "not accessible" in denied.get_json()["error"]["message"].lower()
