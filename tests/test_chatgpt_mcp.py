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

    registry_names = sorted(
        definition["name"]
        for definition in chatgpt_mcp.registry.get_universal_definitions("mcp")
    )
    assert names == registry_names

    assert body["result"]["cacheScope"] == "private"
    assert body["result"]["ttlMs"] > 0
    assert all("inputSchema" in tool for tool in tools)
    assert all("securitySchemes" in tool for tool in tools)


def test_every_registered_tool_is_exposed_through_mcp(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 200

    mcp_names = {tool["name"] for tool in response.get_json()["result"]["tools"]}
    registry_names = {
        definition["name"]
        for definition in chatgpt_mcp.registry.get_universal_definitions("mcp")
    }

    assert mcp_names == registry_names
    assert registry_names
    assert all(
        "mcp" in definition["supported_transports"]
        for definition in chatgpt_mcp.registry.get_universal_definitions()
    )


def test_protocol_version_defaults_to_latest_when_header_is_absent(client):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    response = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={
            "Content-Type": "application/json",
            "Mcp-Method": "tools/list",
        },
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["tools"]


def test_mcp_bearer_auth_is_enforced_even_when_anonymous_mode_was_enabled(client, monkeypatch):
    monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-1")
    response = mcp_request(client, "tools/list")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers

    response = mcp_request(
        client,
        "tools/list",
        Authorization="Bearer test-token",
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["tools"]


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


def test_existing_git_read_tools_execute_through_mcp(client):
    cases = [
        ("git_status", {"repo_path": "."}),
        ("git_log", {"repo_path": ".", "limit": 2}),
        ("git_diff", {"repo_path": "."}),
        ("git_branches", {"repo_path": "."}),
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


def test_existing_git_tools_have_expected_mcp_permissions(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.get_json()["result"]["tools"]}

    for name in ("git_status", "git_log", "git_diff", "git_branches"):
        assert tools[name]["_meta"]["read_only"] is True
        assert tools[name]["_meta"]["requires_approval"] is False
        assert tools[name]["_meta"]["risk_level"] == "low"
        assert "mcp" in tools[name]["_meta"]["capabilities"]

    for name in ("git_add", "git_commit", "git_remote", "git_push", "git_pull", "git_fetch"):
        assert tools[name]["_meta"]["read_only"] is False
        assert tools[name]["_meta"]["requires_approval"] is True
        assert tools[name]["_meta"]["risk_level"] == "high"
        assert "mcp" in tools[name]["_meta"]["capabilities"]


def test_existing_git_write_tools_require_approval(client):
    cases = [
        ("git_add", {"path": "chatgpt_mcp.py"}),
        ("git_commit", {"message": "test"}),
        ("git_remote", {"action": "remove", "name": "nonexistent"}),
        ("git_push", {}),
        ("git_pull", {}),
        ("git_fetch", {}),
    ]
    for name, arguments in cases:
        response = mcp_request(
            client,
            "tools/call",
            {"name": name, "arguments": arguments},
            name=name,
        )
        assert response.status_code == 403, response.get_json()
        body = response.get_json()
        assert body["error"]["code"] == -32003
        assert "approval" in body["error"]["message"].lower()


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


def test_mcp_requires_auth_when_not_configured(monkeypatch):
    monkeypatch.delenv("ALICE_MCP_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("ALICE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)

    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(chatgpt_mcp.chatgpt_mcp_bp)
    app.testing = True

    with app.test_client() as test_client:
        response = mcp_request(test_client, "tools/list")
    assert response.status_code == 401
    assert "WWW-Authenticate" in response.headers


def test_mcp_conversation_tools_are_owner_scoped(client, monkeypatch, tmp_path):
    import db

    monkeypatch.setenv("ALICE_MCP_USER_ID", "user-a")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "alice.db"))
    db.init_db()

    from conversation_ownership import init_conversation_ownership_table, set_owner

    init_conversation_ownership_table()
    db.create_conversation("conversation-a", "A", "model")
    db.create_conversation("conversation-b", "B", "model")
    set_owner("conversation-a", "user-a")
    set_owner("conversation-b", "user-b")
    db.add_message(
        "conversation-a",
        "user",
        "private A",
        trace={"trace_id": "trace-a"},
    )

    response = mcp_request(
        client,
        "tools/call",
        {"name": "alice_list_conversations", "arguments": {}},
        name="alice_list_conversations",
    )
    assert response.status_code == 200
    conversations = response.get_json()["result"]["structuredContent"]["conversations"]
    assert [item["id"] for item in conversations] == ["conversation-a"]

    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_get_conversation",
            "arguments": {"conversation_id": "conversation-a"},
        },
        name="alice_get_conversation",
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["structuredContent"]["conversation"]["title"] == "A"

    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_get_conversation_messages",
            "arguments": {"conversation_id": "conversation-b"},
        },
        name="alice_get_conversation_messages",
    )
    assert response.status_code == 404
    assert response.get_json()["error"]["code"] == -32602

    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "alice_get_conversation_messages",
            "arguments": {"conversation_id": "conversation-a"},
        },
        name="alice_get_conversation_messages",
    )
    assert response.status_code == 200
    messages = response.get_json()["result"]["structuredContent"]["messages"]
    assert messages[0]["text"] == "private A"
    assert messages[0]["trace"]["trace_id"] == "trace-a"


def test_mcp_conversation_tools_are_advertised(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 200
    names = {item["name"] for item in response.get_json()["result"]["tools"]}
    assert {
        "alice_list_conversations",
        "alice_get_conversation",
        "alice_get_conversation_messages",
        "alice_get_execution",
        "alice_get_execution_trace",
    }.issubset(names)
