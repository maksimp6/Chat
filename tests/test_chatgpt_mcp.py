import json

import pytest
from flask import Flask

import chatgpt_mcp
from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setenv("ALICE_MCP_ALLOW_ANONYMOUS", "true")
    monkeypatch.delenv("ALICE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)

    app = Flask(__name__)
    app.register_blueprint(chatgpt_mcp.chatgpt_mcp_bp)
    app.testing = True
    return app.test_client()


def mcp_request(client, method, params=None, request_id=1, name=None, **headers):
    payload = {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": method,
        "params": params or {},
    }
    http_headers = {
        "Content-Type": "application/json",
        "MCP-Protocol-Version": chatgpt_mcp.DEFAULT_PROTOCOL_VERSION,
        "Mcp-Method": method,
        **headers,
    }
    if name is not None:
        http_headers["Mcp-Name"] = name
    return client.post("/mcp", data=json.dumps(payload), headers=http_headers)


def test_tools_list_is_deterministic_and_read_only(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 200

    body = response.get_json()
    tools = body["result"]["tools"]
    names = [tool["name"] for tool in tools]

    assert names == sorted(names)
    assert names == [
        "alice_get_invocation",
        "alice_get_invocation_trace",
        "alice_get_session",
        "alice_get_system_status",
        "alice_list_agents",
        "alice_list_project_files",
        "alice_read_project_file",
        "alice_search_project",
    ]
    assert body["result"]["cacheScope"] == "private"
    assert body["result"]["ttlMs"] > 0
    assert all("inputSchema" in tool for tool in tools)
    assert all("securitySchemes" in tool for tool in tools)


def test_tools_call_returns_structured_content(client):
    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_get_system_status", "arguments": {}},
        name="alice_get_system_status",
    )
    assert response.status_code == 200

    body = response.get_json()
    result = body["result"]["structuredContent"]

    assert result["status"] == "ok"
    assert result["mcp"]["transport"] == "streamable-http"
    assert "local_tools" in result
    assert result["server"]["name"] == "Alice Pro"


def test_standard_headers_must_match_json_rpc(client):
    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_get_system_status", "arguments": {}},
        name="wrong-tool-name",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32600


def test_authentication_is_required_when_anonymous_access_is_disabled(client, monkeypatch):
    monkeypatch.setenv("ALICE_MCP_ALLOW_ANONYMOUS", "false")
    response = mcp_request(client, "tools/list")
    assert response.status_code == 401


def test_bearer_authentication_accepts_only_configured_token(client, monkeypatch):
    monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.delenv("ALICE_MCP_ALLOW_ANONYMOUS", raising=False)

    denied = mcp_request(client, "tools/list", Authorization="Bearer wrong")
    assert denied.status_code == 401

    allowed = mcp_request(
        client,
        "tools/list",
        Authorization="Bearer test-token",
    )
    assert allowed.status_code == 200


def test_invocation_data_is_scoped_to_authenticated_user(client, monkeypatch):
    monkeypatch.delenv("ALICE_MCP_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-a")

    invocation = {
        "id": "invocation-1",
        "session_id": "session-1",
        "conversation_id": "conversation-1",
        "trace_id": "trace-1",
        "status": "completed",
        "metadata": {"user_id": "user-b"},
        "result": None,
        "error": None,
        "created_at": 1,
        "started_at": 1,
        "completed_at": 2,
    }
    monkeypatch.setattr(chatgpt_mcp, "get_invocation_status", lambda _id: invocation)

    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_get_invocation", "arguments": {"invocation_id": "invocation-1"}},
        name="alice_get_invocation",
        Authorization="Bearer test-token",
    )
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == -32003


def test_trace_tool_reads_persisted_trace_for_matching_user(client, monkeypatch):
    monkeypatch.delenv("ALICE_MCP_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-a")

    invocation = {
        "id": "invocation-1",
        "session_id": "session-1",
        "conversation_id": "conversation-1",
        "trace_id": "trace-1",
        "status": "completed",
        "metadata": {"user_id": "user-a"},
        "result": None,
        "error": None,
        "created_at": 1,
        "started_at": 1,
        "completed_at": 2,
    }
    context = InvocationContext(
        session_id="session-1",
        conversation_id="conversation-1",
        invocation_id="invocation-1",
        trace_id="trace-1",
        user_id="user-a",
    )
    trace = create_invocation_trace(context).finalize()

    monkeypatch.setattr(chatgpt_mcp, "get_invocation_status", lambda _id: invocation)
    monkeypatch.setattr(chatgpt_mcp, "get_invocation_trace", lambda _id: trace)

    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_get_invocation_trace", "arguments": {"invocation_id": "invocation-1"}},
        name="alice_get_invocation_trace",
        Authorization="Bearer test-token",
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["structuredContent"]["trace"]["trace_id"] == "trace-1"


def test_project_read_tools_are_exposed_and_read_only(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.get_json()["result"]["tools"]}
    assert {"alice_list_project_files", "alice_read_project_file", "alice_search_project"} <= set(tools)
    for name in ("alice_list_project_files", "alice_read_project_file", "alice_search_project"):
        assert tools[name]["_meta"]["read_only"] is True
        assert tools[name]["_meta"]["requires_approval"] is False


def test_project_tools_execute_through_mcp(client):
    cases = [
        ("alice_list_project_files", {"path": "."}),
        ("alice_read_project_file", {"path": "chatgpt_mcp.py", "length": 1024}),
        ("alice_search_project", {"query": "UniversalToolExecutor", "file_pattern": "*.py"}),
    ]
    for name, arguments in cases:
        response = mcp_request(
            client,
            "tools/call",
            {"name": name, "arguments": arguments},
            name=name,
        )
        assert response.status_code == 200, response.get_json()
        result = response.get_json()["result"]["structuredContent"]
        assert isinstance(result, dict)


def test_project_read_path_traversal_is_rejected_by_filesystem_layer(client):
    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_read_project_file", "arguments": {"path": "../../etc/passwd"}},
        name="alice_read_project_file",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32602

    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_read_project_file", "arguments": {"path": "../outside.txt"}},
        name="alice_read_project_file",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32602
