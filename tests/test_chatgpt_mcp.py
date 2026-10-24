import json
import subprocess

import pytest
from flask import Flask

import mcp_server as chatgpt_mcp
import mcp_server
from invocation.context import InvocationContext
from invocation.trace import create_invocation_trace


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "mcp.memory"))
    monkeypatch.setenv("LOGOS_MCP_ALLOW_ANONYMOUS", "true")
    monkeypatch.delenv("LOGOS_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("LOGOS_MCP_INTROSPECTION_URL", raising=False)

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
        definition["name"] for definition in chatgpt_mcp.registry.get_universal_definitions("mcp")
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
        definition["name"] for definition in chatgpt_mcp.registry.get_universal_definitions("mcp")
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


@pytest.mark.parametrize("authorization", [None, "Bearer test-token", "Bearer invalid"])
def test_mcp_old_auth_configuration_does_not_require_login(client, monkeypatch, authorization):
    monkeypatch.setenv("LOGOS_MCP_BEARER_TOKEN", "test-token")
    monkeypatch.setenv("LOGOS_MCP_INTROSPECTION_URL", "https://issuer.invalid/introspect")
    monkeypatch.setenv("LOGOS_MCP_ALLOW_ANONYMOUS", "false")
    headers = {"Authorization": authorization} if authorization is not None else {}
    response = mcp_request(client, "tools/list", **headers)
    assert response.status_code == 200
    assert "WWW-Authenticate" not in response.headers
    assert response.get_json()["result"]["tools"]
    assert all(item["securitySchemes"] == [{"type": "noauth"}]
               for item in response.get_json()["result"]["tools"])


def test_tools_call_returns_structured_content(client):
    response = mcp_request(
        client,
        "tools/call",
        {"name": "logos_get_system_status", "arguments": {}},
        name="logos_get_system_status",
    )
    assert response.status_code == 200

    body = response.get_json()
    result = body["result"]["structuredContent"]

    assert result["status"] == "ok"
    assert result["mcp"]["transport"] == "streamable-http"
    assert "local_tools" in result
    assert result["server"]["name"] == "Logos Pro"


def test_tools_call_rejects_malformed_params(client):
    response = mcp_request(client, "tools/call", ["not", "an", "object"])
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32602


@pytest.mark.parametrize("caller_owner", ["owner-a", "another-owner"])
def test_runtime_scoped_tool_call_executes_without_owner_check(client, monkeypatch, caller_owner):
    monkeypatch.setenv("LOGOS_OWNER_ID", caller_owner)
    runtime_id = "mcp-test-runtime-success"
    chatgpt_mcp.mcp_runtime_dispatcher.register_runtime(runtime_id, owner_id="owner-a")
    try:
        response = mcp_request(
            client,
            "tools/call",
            {
                "name": "logos_get_system_status",
                "arguments": {},
                "_meta": {
                    "logos/runtime_id": runtime_id,
                    "logos/resource_runtime_id": runtime_id,
                },
            },
        )
    finally:
        chatgpt_mcp.mcp_runtime_dispatcher.unregister_runtime(runtime_id)

    assert response.status_code == 200
    assert response.get_json()["result"]["structuredContent"]["status"] == "ok"


def test_runtime_scoped_tool_call_denies_cross_runtime_access(client, monkeypatch):
    monkeypatch.setenv("LOGOS_OWNER_ID", "owner-a")
    caller = "mcp-test-runtime-a"
    target = "mcp-test-runtime-b"
    chatgpt_mcp.mcp_runtime_dispatcher.register_runtime(caller, owner_id="owner-a")
    chatgpt_mcp.mcp_runtime_dispatcher.register_runtime(target, owner_id="owner-a")
    try:
        response = mcp_request(
            client,
            "tools/call",
            {
                "name": "logos_get_system_status",
                "arguments": {},
                "_meta": {
                    "logos/runtime_id": caller,
                    "logos/resource_runtime_id": target,
                },
            },
        )
    finally:
        chatgpt_mcp.mcp_runtime_dispatcher.unregister_runtime(caller)
        chatgpt_mcp.mcp_runtime_dispatcher.unregister_runtime(target)

    assert response.status_code == 403
    body = response.get_json()
    assert body["error"]["code"] == -32003
    assert body["error"]["message"] == "Runtime scope is unavailable"
    assert caller not in json.dumps(body)
    assert target not in json.dumps(body)


def test_runtime_scoped_tool_call_hides_unavailable_runtime(client, monkeypatch):
    monkeypatch.setenv("LOGOS_OWNER_ID", "owner-a")
    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "logos_get_system_status",
            "arguments": {},
            "_meta": {"logos/runtime_id": "missing-runtime"},
        },
    )

    assert response.status_code == 403
    assert response.get_json()["error"]["message"] == "Runtime scope is unavailable"


def test_standard_mcp_request_does_not_require_nonstandard_headers(client):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/list",
        "params": {},
    }
    response = client.post(
        "/mcp",
        data=json.dumps(payload),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["tools"]


def test_project_read_tools_are_exposed_and_read_only(client):
    response = mcp_request(client, "tools/list")
    assert response.status_code == 200
    tools = {tool["name"]: tool for tool in response.get_json()["result"]["tools"]}
    assert {"logos_list_project_files", "logos_read_project_file", "logos_search_project"} <= set(
        tools
    )
    for name in ("logos_list_project_files", "logos_read_project_file", "logos_search_project"):
        assert tools[name]["_meta"]["read_only"] is True
        assert tools[name]["_meta"]["requires_approval"] is False


def test_project_tools_execute_through_mcp(client):
    cases = [
        ("logos_list_project_files", {"path": "."}),
        ("logos_read_project_file", {"path": "app.py", "length": 1024}),
        ("logos_search_project", {"query": "UniversalToolExecutor", "file_pattern": "*.py"}),
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


@pytest.mark.parametrize("linked", [False, True])
def test_existing_git_read_tools_execute_through_mcp(client, linked, tmp_path, monkeypatch):
    if linked:
        repository = tmp_path / "repository"
        repository.mkdir()
        (repository / "tracked.txt").write_text("synthetic\n", encoding="utf-8")
        commands = [
            ["init", "-q"],
            ["add", "."],
            [
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "-c",
                "commit.gpgsign=false",
                "commit",
                "-qm",
                "fixture",
            ],
            ["worktree", "add", "-q", "-b", "linked", str(tmp_path / "linked")],
        ]
        for command in commands:
            subprocess.run(
                ["git", "-C", str(repository), *command], check=True, capture_output=True
            )
        monkeypatch.chdir(tmp_path / "linked")
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
        assert tools[name]["_meta"]["requires_approval"] is False
        assert tools[name]["_meta"]["risk_level"] == "high"
        assert "mcp" in tools[name]["_meta"]["capabilities"]


def test_existing_git_write_tools_do_not_require_approval_with_stubbed_handlers(client, monkeypatch):
    cases = [
        ("git_add", {"path": "chatgpt_mcp.py"}),
        ("git_commit", {"message": "test"}),
        ("git_remote", {"action": "remove", "name": "nonexistent"}),
        ("git_push", {}),
        ("git_pull", {}),
        ("git_fetch", {}),
    ]
    executed = []

    def forbidden_subprocess(*args, **kwargs):
        raise AssertionError("Git write test must never invoke a subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden_subprocess)
    for name, arguments in cases:
        def handler(args, cfg, tool_name=name):
            executed.append((tool_name, args))
            return {"stubbed": tool_name}

        monkeypatch.setitem(chatgpt_mcp.registry._tools, name, {
            **chatgpt_mcp.registry._tools[name], "func": handler,
        })
        response = mcp_request(
            client,
            "tools/call",
            {"name": name, "arguments": arguments},
            name=name,
        )
        assert response.status_code == 200, response.get_json()
        assert response.get_json()["result"]["structuredContent"] == {"stubbed": name}
    assert executed == cases


def test_project_read_path_traversal_is_rejected_by_filesystem_layer(client):
    response = mcp_request(
        client,
        "tools/call",
        {"name": "logos_read_project_file", "arguments": {"path": "../../etc/passwd"}},
        name="logos_read_project_file",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32602

    response = mcp_request(
        client,
        "tools/call",
        {"name": "logos_read_project_file", "arguments": {"path": "../outside.txt"}},
        name="logos_read_project_file",
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32602


def test_mcp_works_without_auth_configuration(monkeypatch):
    monkeypatch.delenv("LOGOS_MCP_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.delenv("LOGOS_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("LOGOS_MCP_INTROSPECTION_URL", raising=False)

    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(chatgpt_mcp.chatgpt_mcp_bp)
    app.testing = True

    with app.test_client() as test_client:
        response = mcp_request(test_client, "tools/list")
    assert response.status_code == 200
    assert "WWW-Authenticate" not in response.headers


def test_mcp_conversation_tools_expose_shared_local_conversations(client, monkeypatch, tmp_path):
    import storage.db as db

    monkeypatch.setenv("LOGOS_OWNER_ID", "user-a")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "logos.db"))
    db.init_db()

    from conversations.ownership import init_conversation_ownership_table, set_owner

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
        {"name": "logos_list_conversations", "arguments": {}},
        name="logos_list_conversations",
    )
    assert response.status_code == 200
    conversations = response.get_json()["result"]["structuredContent"]["conversations"]
    assert {item["id"] for item in conversations} == {"conversation-a", "conversation-b"}

    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "logos_get_conversation",
            "arguments": {"conversation_id": "conversation-a"},
        },
        name="logos_get_conversation",
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["structuredContent"]["conversation"]["title"] == "A"

    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "logos_get_conversation_messages",
            "arguments": {"conversation_id": "conversation-b"},
        },
        name="logos_get_conversation_messages",
    )
    assert response.status_code == 200
    assert response.get_json()["result"]["structuredContent"]["messages"] == []

    response = mcp_request(
        client,
        "tools/call",
        {
            "name": "logos_get_conversation_messages",
            "arguments": {"conversation_id": "conversation-a"},
        },
        name="logos_get_conversation_messages",
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
        "logos_list_conversations",
        "logos_get_conversation",
        "logos_get_conversation_messages",
        "logos_get_execution",
        "logos_get_execution_trace",
    }.issubset(names)


def test_mcp_initialize_handshake_is_supported(client):
    response = mcp_request(client, "initialize", {"clientInfo": {"name": "test", "version": "1"}})
    assert response.status_code == 200
    result = response.get_json()["result"]
    assert result["protocolVersion"] == chatgpt_mcp.DEFAULT_PROTOCOL_VERSION
    assert result["capabilities"]["tools"] == {}
    assert result["serverInfo"]["name"] == "Logos Pro"


def test_mcp_tool_call_is_persisted_in_execution_trace(monkeypatch, tmp_path):
    import storage.db as db

    monkeypatch.delenv("LOGOS_MCP_ALLOW_ANONYMOUS", raising=False)
    monkeypatch.setenv("LOGOS_MCP_BEARER_TOKEN", "trace-token")
    monkeypatch.setenv("LOGOS_OWNER_ID", "trace-user")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "logos.db"))
    db.init_db()

    from flask import Flask

    app = Flask(__name__)
    app.register_blueprint(chatgpt_mcp.chatgpt_mcp_bp)
    app.testing = True

    with app.test_client() as test_client:
        response = mcp_request(
            test_client,
            "tools/call",
            {"name": "logos_get_system_status", "arguments": {}},
            name="logos_get_system_status",
            Authorization="Bearer trace-token",
        )

        assert response.status_code == 200
        meta = response.get_json()["result"]["_meta"]
        assert meta["trace_id"]
        assert meta["invocation_id"]

        trace_response = mcp_request(
            test_client,
            "tools/call",
            {
                "name": "logos_get_invocation_trace",
                "arguments": {"invocation_id": meta["invocation_id"]},
            },
            name="logos_get_invocation_trace",
            Authorization="Bearer trace-token",
        )

        assert trace_response.status_code == 200
        trace = trace_response.get_json()["result"]["structuredContent"]["trace"]
        assert trace["trace_id"] == meta["trace_id"]
        assert trace["context"]["user_id"] == "trace-user"
        assert any(
            call.get("tool_name") == "logos_get_system_status" for call in trace["tool_calls"]
        )
        assert any(event.get("type") == "mcp_tool_call_completed" for event in trace["events"])
