import json
import sys
from types import SimpleNamespace

import pytest
from flask import Flask

from mcp_server import auth, protocol, runtime_bridge, tools, transport


@pytest.fixture()
def mcp_app():
    app = Flask(__name__)
    app.register_blueprint(transport.chatgpt_mcp_bp)
    app.testing = True
    return app


@pytest.fixture()
def mcp_client(mcp_app, monkeypatch):
    monkeypatch.setenv("ALICE_MCP_ALLOW_ANONYMOUS", "true")
    monkeypatch.delenv("ALICE_MCP_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)
    return mcp_app.test_client()


def _mcp_post(client, method, params=None, request_id=1, **headers):
    return client.post(
        "/mcp",
        data=json.dumps(
            {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params if params is not None else {},
            }
        ),
        headers={"Content-Type": "application/json", **headers},
    )


def _patch_runtime_bridge(monkeypatch, *, result=None, exc=None):
    calls = {"failed": [], "finished": [], "persisted": []}
    context = SimpleNamespace(invocation_id="inv-1", trace_id="trace-1")

    class FakeTrace:
        def __init__(self):
            self.request = None
            self.events = []
            self.errors = []

        def set_request(self, payload):
            self.request = payload

        def add_event(self, name, payload):
            self.events.append((name, payload))

        def record_error(self, *args, **kwargs):
            self.errors.append((args, kwargs))

        def finalize(self):
            return {
                "request": self.request,
                "events": list(self.events),
                "errors": list(self.errors),
            }

    trace = FakeTrace()

    monkeypatch.setattr(runtime_bridge, "create_invocation", lambda *args, **kwargs: context)
    monkeypatch.setattr(runtime_bridge, "start_invocation", lambda invocation_id: None)
    monkeypatch.setattr(runtime_bridge, "create_invocation_trace", lambda _context: trace)
    monkeypatch.setattr(
        runtime_bridge,
        "persist_invocation_trace",
        lambda invocation_id, trace_data: calls["persisted"].append((invocation_id, trace_data)),
    )
    monkeypatch.setattr(
        runtime_bridge,
        "fail_invocation",
        lambda invocation_id, error: calls["failed"].append((invocation_id, error)),
    )
    monkeypatch.setattr(
        runtime_bridge,
        "finish_invocation",
        lambda invocation_id, result: calls["finished"].append((invocation_id, result)),
    )

    class FakeExecutor:
        def __init__(self, _registry):
            pass

        def execute_with_trace(self, call, _trace):
            calls["call"] = call
            if exc is not None:
                raise exc
            return result

    monkeypatch.setattr(runtime_bridge, "UniversalToolExecutor", FakeExecutor)
    return calls, trace


def test_auth_helpers_cover_permission_modes_and_challenge(monkeypatch, mcp_app):
    assert auth._permission_error_message(PermissionError()) == "Unauthorized"
    assert auth._permission_error_message(PermissionError("Traceback (most recent call last): boom")) == "Unauthorized"
    assert auth._permission_error_message(PermissionError("nope\nextra")) == "nope"

    monkeypatch.setenv("ALICE_MCP_INTROSPECTION_URL", "https://issuer.example/introspect")
    monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "token")
    monkeypatch.setenv("ALICE_MCP_ALLOW_ANONYMOUS", "true")
    assert auth._auth_mode() == "introspection"

    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)
    assert auth._auth_mode() == "bearer"

    monkeypatch.delenv("ALICE_MCP_BEARER_TOKEN", raising=False)
    assert auth._auth_mode() == "anonymous"

    monkeypatch.setenv("ALICE_MCP_PUBLIC_URL", "https://alice.example/")
    assert auth._protected_resource_url() == "https://alice.example/.well-known/oauth-protected-resource"
    assert auth._www_authenticate().startswith("Bearer ")
    assert (
        'resource_metadata="https://alice.example/.well-known/oauth-protected-resource"'
        in auth._www_authenticate()
    )

    monkeypatch.delenv("ALICE_MCP_PUBLIC_URL", raising=False)
    monkeypatch.setattr(protocol, "PUBLIC_BASE_URL", "")
    assert auth._www_authenticate() == "Bearer"

    with mcp_app.test_request_context("/mcp"):
        monkeypatch.setenv("ALICE_MCP_ALLOW_ANONYMOUS", "true")
        monkeypatch.setenv("ALICE_MCP_USER_ID", "anon-user")
        assert auth._auth_user_from_request() == "anon-user"

    with mcp_app.test_request_context("/mcp", headers={"Authorization": "Bearer   "}):
        monkeypatch.delenv("ALICE_MCP_ALLOW_ANONYMOUS", raising=False)
        with pytest.raises(PermissionError, match="token required"):
            auth._auth_user_from_request()

    with mcp_app.test_request_context("/mcp", headers={"Authorization": "Bearer " + "supplied"}):
        monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "expected")
        monkeypatch.delenv("ALICE_MCP_USER_ID", raising=False)
        with pytest.raises(PermissionError, match="not fully configured"):
            auth._auth_user_from_request()

        monkeypatch.setenv("ALICE_MCP_USER_ID", "user-1")
        with pytest.raises(PermissionError, match="Invalid bearer token"):
            auth._auth_user_from_request()

        monkeypatch.setenv("ALICE_MCP_BEARER_TOKEN", "supplied")
        assert auth._auth_user_from_request() == "user-1"

        monkeypatch.setenv("ALICE_MCP_INTROSPECTION_URL", "https://issuer.example/introspect")
        monkeypatch.setattr(auth, "_introspect_token", lambda token: f"introspected:{token}")
        assert auth._auth_user_from_request() == "introspected:supplied"

        monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)
        monkeypatch.delenv("ALICE_MCP_BEARER_TOKEN", raising=False)
        monkeypatch.delenv("ALICE_MCP_USER_ID", raising=False)
        with pytest.raises(PermissionError, match="not configured"):
            auth._auth_user_from_request()


def test_require_auth_wraps_permission_errors(monkeypatch):
    monkeypatch.setattr(auth, "_auth_user_from_request", lambda: (_ for _ in ()).throw(PermissionError("boom")))
    user, error = auth._require_auth("req-1")
    assert user is None
    assert error.status_code == 401
    assert error.headers["WWW-Authenticate"].startswith("Bearer")


def test_introspect_token_covers_configuration_scopes_and_subject(monkeypatch):
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_URL", raising=False)
    with pytest.raises(PermissionError, match="not configured"):
        auth._introspect_token("token")

    calls = []

    class FakeResponse:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            calls.append("raise_for_status")

        def json(self):
            return self.payload

    def fake_post(url, **kwargs):
        calls.append((url, kwargs))
        payload = fake_post.payloads.pop(0)
        return FakeResponse(payload)

    fake_post.payloads = [
        {"active": False},
        {"active": True, "scope": "other", "sub": "user-1"},
        {"active": True, "scope": protocol.OAUTH_SCOPE, "sub": ""},
    ]

    monkeypatch.setitem(sys.modules, "requests", SimpleNamespace(post=fake_post))
    monkeypatch.setenv("ALICE_MCP_INTROSPECTION_URL", "https://issuer.example/introspect")
    monkeypatch.setenv("ALICE_MCP_INTROSPECTION_CLIENT_ID", "client")
    monkeypatch.setenv("ALICE_MCP_INTROSPECTION_CLIENT_SECRET", "secret")
    monkeypatch.setenv("ALICE_MCP_INTROSPECTION_TIMEOUT", "7.5")
    monkeypatch.setattr(auth, "OAUTH_SCOPE", protocol.OAUTH_SCOPE)

    with pytest.raises(PermissionError, match="inactive"):
        auth._introspect_token("token-1")
    with pytest.raises(PermissionError, match="required scope"):
        auth._introspect_token("token-2")
    assert auth._introspect_token("token-3") is None

    request_call = calls[0]
    assert request_call[0] == "https://issuer.example/introspect"
    assert request_call[1]["auth"] == ("client", "secret")
    assert request_call[1]["timeout"] == 7.5
    assert request_call[1]["data"] == {"token": "token-1"}
    assert request_call[1]["headers"]["Accept"] == "application/json"

    calls.clear()
    fake_post.payloads = [{"active": True, "scope": protocol.OAUTH_SCOPE, "sub": "user-4"}]
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_CLIENT_ID", raising=False)
    monkeypatch.delenv("ALICE_MCP_INTROSPECTION_CLIENT_SECRET", raising=False)
    assert auth._introspect_token("token-4") == "user-4"
    assert "auth" not in calls[0][1]


def test_protocol_helpers_sanitize_payloads_and_version_selection(mcp_app):
    assert protocol._sanitize_error_string(None) == ""
    assert protocol._sanitize_error_string("Traceback (most recent call last): nope") == "Internal error"
    assert protocol._sanitize_error_string("first line\nsecond line") == "first line"
    assert protocol._sanitize_error_payload(
        {"k": ["Traceback (most recent call last): nope", ("line 1\nline 2", 1)]}
    ) == {"k": ["Internal error", ["line 1", 1]]}

    response = protocol._jsonrpc_error(
        "req-1",
        -1,
        "broken\nhidden",
        data={"items": ["first\nsecond"]},
        status=418,
        headers={"X-Test": "1"},
    )
    assert response.status_code == 418
    assert response.headers["X-Test"] == "1"
    body = json.loads(response.get_data(as_text=True))
    assert body["error"]["message"] == "broken"
    assert body["error"]["data"] == {"items": ["first"]}

    with mcp_app.test_request_context("/mcp", headers={"MCP-Protocol-Version": " 2025-06-18 "}):
        assert protocol._request_protocol_version({}) == "2025-06-18"
    with mcp_app.test_request_context("/mcp"):
        assert protocol._request_protocol_version(
            {"params": {"_meta": {"io.modelcontextprotocol/protocolVersion": "2025-03-26"}}}
        ) == "2025-03-26"
        assert protocol._request_protocol_version({"params": {"_meta": []}}) == protocol.DEFAULT_PROTOCOL_VERSION


def test_runtime_bridge_authorization_and_owner_resolution(monkeypatch):
    assert runtime_bridge._owner_id_from_invocation({"metadata": "bad"}) is None
    assert runtime_bridge._owner_id_from_invocation({"metadata": {"user_id": " user-1 "}}) == "user-1"

    monkeypatch.setattr("mcp_server.auth._auth_mode", lambda: "anonymous")
    runtime_bridge._authorize_invocation({"metadata": {"user_id": "user-1"}}, "user-1")
    runtime_bridge._authorize_invocation({"metadata": {}}, None)
    monkeypatch.setattr("mcp_server.auth._auth_mode", lambda: "bearer")
    runtime_bridge._authorize_invocation({"metadata": {}}, None)
    with pytest.raises(PermissionError, match="no trusted user ownership"):
        runtime_bridge._authorize_invocation({"metadata": {}}, "user-1")
    with pytest.raises(PermissionError, match="not accessible"):
        runtime_bridge._authorize_invocation({"metadata": {"user_id": "user-2"}}, "user-1")


@pytest.mark.parametrize(
    ("result", "expected_type", "expected_text"),
    [
        ({"success": False, "error": "bad transport", "metadata": {"phase": "transport"}}, LookupError, "bad transport"),
        (
            {"success": False, "error": "denied", "metadata": {"phase": "authorization"}},
            PermissionError,
            "denied",
        ),
        (
            {"success": False, "error": "LookupError: missing session", "metadata": {"phase": "execution"}},
            LookupError,
            "missing session",
        ),
        (
            {"success": False, "error": "execution failed", "metadata": {"phase": "execution"}},
            RuntimeError,
            "execution failed",
        ),
        ({"success": False, "error": "boom", "metadata": {"phase": "other"}}, RuntimeError, "boom"),
    ],
)
def test_handle_call_maps_executor_failure_phases(monkeypatch, result, expected_type, expected_text):
    calls, trace = _patch_runtime_bridge(monkeypatch, result=result)
    with pytest.raises(expected_type, match=expected_text):
        runtime_bridge._handle_call("tool-name", {}, "user-1")
    assert calls["failed"]
    assert calls["persisted"]
    assert trace.events[-1][0] == "mcp_tool_call_completed"


def test_handle_call_rejects_non_object_arguments():
    with pytest.raises(ValueError, match="arguments must be an object"):
        runtime_bridge._handle_call("tool-name", [], "user-1")


def test_handle_call_records_unexpected_executor_errors(monkeypatch):
    calls, trace = _patch_runtime_bridge(monkeypatch, exc=Exception("crash"))
    with pytest.raises(Exception, match="crash"):
        runtime_bridge._handle_call("tool-name", {}, "user-1")
    assert trace.errors
    assert calls["persisted"]
    assert calls["failed"] == [("inv-1", {"tool": "tool-name", "error": "crash"})]


def test_tools_helpers_cover_lookup_validation_and_wrappers(monkeypatch):
    descriptor, handler = tools._tool("tool-name", "Tool title", "desc", {"type": "object"}, lambda args, user: {})
    assert descriptor["name"] == "tool-name"
    assert descriptor["_meta"]["openai/toolInvocation/invoked"] == "Tool title: готово"
    assert handler({}, None) == {}

    monkeypatch.setattr(
        tools,
        "AgentGateway",
        lambda: SimpleNamespace(
            list_agents=lambda: [
                SimpleNamespace(
                    agent_id="agent-1",
                    name="Agent",
                    capabilities=("read",),
                    version="1.0",
                    transport="local",
                    enabled=True,
                    metadata={"tier": "test"},
                )
            ]
        ),
    )
    assert tools._agents({}, None) == {
        "agents": [
            {
                "agent_id": "agent-1",
                "name": "Agent",
                "capabilities": ["read"],
                "version": "1.0",
                "transport": "local",
                "enabled": True,
                "metadata": {"tier": "test"},
            }
        ]
    }

    monkeypatch.setattr(tools, "get_session", lambda session_id: None if session_id == "missing" else {"id": session_id, "status": "done", "created_at": 1, "updated_at": 2})
    with pytest.raises(LookupError, match="Session not found"):
        tools._session({"session_id": "missing"}, None)
    assert tools._session({"session_id": "session-1"}, None)["session"]["id"] == "session-1"

    monkeypatch.setattr(tools, "get_invocation_status", lambda invocation_id: None if invocation_id == "missing" else {"metadata": {"user_id": "user-1"}, "id": invocation_id})
    monkeypatch.setattr(tools, "_authorize_invocation", lambda invocation, user: invocation.update({"authorized_as": user}))
    with pytest.raises(LookupError, match="Invocation not found"):
        tools._invocation({"invocation_id": "missing"}, "user-1")
    assert tools._invocation({"invocation_id": "inv-1"}, "user-1")["invocation"]["authorized_as"] == "user-1"

    monkeypatch.setattr(tools, "get_invocation_trace", lambda invocation_id: None if invocation_id == "missing-trace" else {"id": invocation_id})
    with pytest.raises(LookupError, match="Invocation not found"):
        tools._trace({"invocation_id": "missing"}, "user-1")
    with pytest.raises(LookupError, match="Invocation trace not found"):
        tools._trace({"invocation_id": "missing-trace"}, "user-1")
    assert tools._trace({"invocation_id": "inv-1"}, "user-1")["trace"]["id"] == "inv-1"

    wrapped = tools._bridge_wrapper(lambda arguments, user: {"arguments": arguments, "user": user})
    assert wrapped({"x": 1}, {"_universal_context": {"user_id": "user-1"}}) == {
        "arguments": {"x": 1},
        "user": "user-1",
    }

    with pytest.raises(PermissionError, match="required"):
        tools._require_conversation_user(None)
    with pytest.raises(ValueError, match="conversation_id is required"):
        tools._conversation({}, "user-1")

    monkeypatch.setattr(tools, "get_owned_conversation", lambda conversation_id, owner: None)
    with pytest.raises(LookupError, match="Conversation not found"):
        tools._conversation({"conversation_id": "conversation-1"}, "user-1")

    with pytest.raises(ValueError, match="conversation_id is required"):
        tools._conversation_messages({}, "user-1")
    monkeypatch.setattr(tools, "check_access", lambda conversation_id, owner: False)
    with pytest.raises(LookupError, match="Conversation not found"):
        tools._conversation_messages({"conversation_id": "conversation-1"}, "user-1")

    monkeypatch.setattr(tools, "_invocation", lambda arguments, user: {"invocation": {"id": arguments["invocation_id"], "user": user}})
    monkeypatch.setattr(tools, "_trace", lambda arguments, user: {"trace": {"id": arguments["invocation_id"], "user": user}})
    assert tools._execution({"invocation_id": "inv-1"}, "user-1") == {
        "execution": {"id": "inv-1", "user": "user-1"}
    }
    assert tools._execution_trace({"invocation_id": "inv-1"}, "user-1") == {
        "trace": {"id": "inv-1", "user": "user-1"}
    }

    with pytest.raises(ValueError, match="path is required"):
        tools._project_read({}, None)
    monkeypatch.setattr(tools, "read_file", lambda payload: {"error": "bad path", "payload": payload})
    with pytest.raises(ValueError, match="bad path"):
        tools._project_read({"path": "chatgpt_mcp.py"}, None)

    with pytest.raises(ValueError, match="query is required"):
        tools._project_search({}, None)

    monkeypatch.setattr(tools, "_auth_mode", lambda: "bearer")
    schemes = tools._security_schemes()
    assert schemes == [{"type": "http", "scheme": "bearer"}]

    monkeypatch.setattr(
        tools.registry,
        "get_universal_definitions",
        lambda transport_name=None: [
            {
                "name": "tool-name",
                "title": "Tool title",
                "description": "desc",
                "input_schema": {"type": "object"},
                "metadata": {"custom": True},
                "read_only": True,
                "requires_approval": False,
                "capabilities": ["mcp"],
                "risk_level": "low",
            }
        ],
    )
    listing = tools._tools_list()
    assert listing == [
        {
            "name": "tool-name",
            "title": "Tool title",
            "description": "desc",
            "inputSchema": {"type": "object"},
            "outputSchema": {"type": "object"},
            "securitySchemes": schemes,
            "_meta": {
                "custom": True,
                "risk_level": "low",
                "read_only": True,
                "requires_approval": False,
                "capabilities": ["mcp"],
                "openai/toolInvocation/invoking": "Tool title…",
                "openai/toolInvocation/invoked": "Tool title: готово",
                "securitySchemes": schemes,
            },
        }
    ]


def test_transport_routes_cover_public_metadata_and_non_post_methods(monkeypatch, mcp_client):
    monkeypatch.setenv("ALICE_MCP_OAUTH_ISSUER", "https://issuer.example/")
    monkeypatch.delenv("ALICE_MCP_PUBLIC_URL", raising=False)
    response = mcp_client.get("/.well-known/oauth-protected-resource")
    assert response.status_code == 200
    assert response.get_json() == {
        "resource": "http://localhost",
        "authorization_servers": ["https://issuer.example"],
        "scopes_supported": [protocol.OAUTH_SCOPE],
    }

    response = mcp_client.open("/mcp", method="OPTIONS")
    assert response.status_code == 204
    assert response.headers["Access-Control-Allow-Methods"] == "POST, OPTIONS"

    response = mcp_client.get("/mcp")
    assert response.status_code == 405
    assert response.headers["Allow"] == "POST, OPTIONS"
    assert response.get_data(as_text=True) == "Alice Pro MCP endpoint accepts POST requests only."

    assert transport._client_error_message(Exception(), "fallback") == "fallback"
    assert transport._client_error_message(Exception("Traceback (most recent call last): boom"), "fallback") == "fallback"


def test_transport_post_covers_protocol_errors_and_helper_methods(monkeypatch, mcp_client):
    monkeypatch.setattr(transport, "_require_auth", lambda request_id: ("user-1", None))

    response = mcp_client.post("/mcp", data="[]", headers={"Content-Type": "application/json"})
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == -32600

    response = mcp_client.post(
        "/mcp",
        data=json.dumps({"jsonrpc": "1.0", "id": 1, "method": "", "params": {}}),
        headers={"Content-Type": "application/json"},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "Invalid JSON-RPC request"

    response = _mcp_post(mcp_client, "notifications/initialized")
    assert response.status_code == 204

    response = _mcp_post(
        mcp_client,
        "tools/list",
        params={"_meta": {"io.modelcontextprotocol/protocolVersion": "2099-01-01"}},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["data"]["requested"] == "2099-01-01"

    response = _mcp_post(mcp_client, "ping")
    assert response.status_code == 200
    assert response.get_json()["result"]["serverInfo"]["name"] == protocol.SERVER_NAME

    response = _mcp_post(mcp_client, "server/discover")
    assert response.status_code == 200
    assert response.get_json()["result"]["capabilities"] == {"tools": {}}

    response = _mcp_post(
        mcp_client,
        "tools/call",
        params=[],
        **{"MCP-Protocol-Version": protocol.DEFAULT_PROTOCOL_VERSION},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "tools/call params must be an object"

    response = _mcp_post(mcp_client, "tools/call", params={"_meta": [1]})
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "tools/call _meta must be an object"

    response = _mcp_post(mcp_client, "tools/call", params={"_meta": {"alice/runtime_id": "   "}})
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "alice/runtime_id must be a non-empty string"

    response = _mcp_post(
        mcp_client,
        "tools/call",
        params={"_meta": {"alice/resource_runtime_id": "   "}},
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["message"] == "alice/resource_runtime_id must be a non-empty string"


@pytest.mark.parametrize(
    ("raised", "status_code", "code", "message"),
    [
        (LookupError("missing\nextra"), 404, -32602, "missing"),
        (PermissionError("denied"), 403, -32003, "denied"),
        (ValueError("invalid"), 400, -32602, "invalid"),
        (RuntimeError("boom"), 500, -32603, "Tool execution failed"),
    ],
)
def test_transport_tools_call_error_mapping(monkeypatch, mcp_client, raised, status_code, code, message):
    monkeypatch.setattr(transport, "_require_auth", lambda request_id: ("user-1", None))
    monkeypatch.setattr(
        transport,
        "_handle_call",
        lambda *args, **kwargs: (_ for _ in ()).throw(raised),
    )

    response = _mcp_post(
        mcp_client,
        "tools/call",
        params={"name": "tool-name", "arguments": {}, "_meta": {}},
    )
    assert response.status_code == status_code
    body = response.get_json()
    assert body["error"]["code"] == code
    assert body["error"]["message"] == message


def test_transport_method_not_found_and_auth_failure(monkeypatch, mcp_client):
    auth_response = protocol._jsonrpc_error(1, -32001, "denied", status=401)
    monkeypatch.setattr(transport, "_require_auth", lambda request_id: (None, auth_response))

    response = _mcp_post(mcp_client, "tools/list")
    assert response.status_code == 401

    monkeypatch.setattr(transport, "_require_auth", lambda request_id: ("user-1", None))
    response = _mcp_post(mcp_client, "unknown/method")
    assert response.status_code == 404
    assert response.get_json()["error"]["message"] == "Method not found: unknown/method"
