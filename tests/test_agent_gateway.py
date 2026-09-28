import json
import time
from urllib.error import HTTPError, URLError
import pytest
from agent_gateway import (
    A2AClient,
    A2AClientConfig,
    A2AProtocolError,
    RESTAgentClient,
    AgentAlreadyRegistered,
    AgentApprovalRequired,
    AgentCircuitOpenError,
    AgentDescriptor,
    AgentGateway,
    AgentInvocationError,
    AgentNotFound,
    AgentPermissionError,
    AgentRateLimitError,
    cloudru_iam_token_provider,
)
from cloudru_iam import CloudRuIamError


def test_register_and_invoke():
    g = AgentGateway(default_max_retries=0)
    g.register(AgentDescriptor("echo", "Echo", ("chat",)), lambda p: {"echo": p["message"]})
    r = g.invoke("echo", {"message": "hi"})
    assert r.status == "completed" and r.result == {"echo": "hi"} and r.attempts == 1


def test_duplicate_and_missing():
    g = AgentGateway(default_max_retries=0)
    d = AgentDescriptor("echo", "Echo")
    g.register(d, lambda p: p)
    with pytest.raises(AgentAlreadyRegistered):
        g.register(d, lambda p: p)
    with pytest.raises(AgentNotFound):
        g.get("missing")


def test_retry_then_success():
    g = AgentGateway(default_max_retries=1)
    calls = {"n": 0}

    def f(_):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("temporary")
        return {"ok": True}

    g.register(AgentDescriptor("retry", "Retry"), f)
    r = g.invoke("retry", {})
    assert r.status == "completed" and r.attempts == 2 and calls["n"] == 2


def test_timeout():
    g = AgentGateway(default_timeout_seconds=0.01, default_max_retries=1)
    g.register(AgentDescriptor("slow", "Slow"), lambda _: time.sleep(0.1))
    r = g.invoke("slow", {})
    assert r.status == "error" and r.attempts == 2 and "timed out" in (r.error or "")


def test_circuit_breaker():
    g = AgentGateway(default_max_retries=0, circuit_failure_threshold=2, circuit_reset_seconds=60)
    g.register(
        AgentDescriptor("broken", "Broken"), lambda _: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    assert g.invoke("broken", {}).status == "error"
    assert g.invoke("broken", {}).status == "error"
    r = g.invoke("broken", {})
    assert r.status == "error" and "circuit is open" in (r.error or "")


def test_rate_limit():
    g = AgentGateway(default_max_retries=0, rate_limit_per_agent=1)
    g.register(AgentDescriptor("echo", "Echo"), lambda p: p)
    assert g.invoke("echo", {"n": 1}).status == "completed"
    r = g.invoke("echo", {"n": 2})
    assert r.status == "error" and "rate limit" in (r.error or "")


def test_policy_and_approval():
    g = AgentGateway(default_max_retries=0)
    g.register(
        AgentDescriptor(
            "secure", "Secure", risk_level="high", requires_approval=True, allowed_users=("alice",)
        ),
        lambda p: p,
    )
    assert g.invoke("secure", {}, user_id="mallory", approved=True).status == "error"
    assert g.invoke("secure", {}, user_id="alice", approved=False).status == "error"
    assert g.invoke("secure", {}, user_id="alice", approved=True).status == "completed"


def test_capability_route_and_fallback():
    g = AgentGateway(default_max_retries=0)
    g.register(
        AgentDescriptor("a", "A", ("chat",)), lambda _: (_ for _ in ()).throw(RuntimeError("down"))
    )
    g.register(AgentDescriptor("b", "B", ("chat",)), lambda p: {"agent": p["value"]})
    r = g.route("chat", {"value": 3})
    assert r.status == "completed" and r.agent_id == "b"


def test_trace_events():
    events = []
    errors = []

    class T:
        def add_event(self, k, p):
            events.append((k, p))

        def record_error(self, s, m, call_id=None):
            errors.append((s, m, call_id))

    g = AgentGateway(default_max_retries=0)
    g.register(AgentDescriptor("e", "E"), lambda p: p)
    r = g.invoke("e", {"x": 1}, trace=T())
    assert (
        r.status == "completed"
        and events[0][0] == "agent_invocation_started"
        and events[-1][1]["invocation_id"] == r.invocation_id
        and not errors
    )


def test_a2a(monkeypatch):
    captured = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return json.dumps(
                {"jsonrpc": "2.0", "id": captured["id"], "result": {"ok": True}}
            ).encode()

    def fake(req, timeout):
        captured["id"] = json.loads(req.data.decode())["id"]
        captured["auth"] = dict((k.lower(), v) for k, v in req.header_items())
        return R()

    monkeypatch.setattr("agent_gateway.urlopen", fake)
    c = A2AClient(A2AClientConfig("https://agent.example/a2a"), lambda: "token")
    assert c.send_message({"message": {"role": "user", "parts": []}}) == {"ok": True}
    assert captured["auth"]["authorization"] == "Bearer token"


def test_a2a_bad_id(monkeypatch):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"jsonrpc":"2.0","id":"wrong","result":{}}'

    monkeypatch.setattr("agent_gateway.urlopen", lambda req, timeout: R())
    with pytest.raises(A2AProtocolError, match="does not match"):
        A2AClient(A2AClientConfig("https://agent.example/a2a")).send_message(
            {"message": {"role": "user", "parts": []}}
        )


def test_a2a_requires_endpoint_or_agent_card():
    with pytest.raises(ValueError, match="must be configured"):
        A2AClient(A2AClientConfig())


def test_a2a_load_agent_card_requires_configured_url():
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    with pytest.raises(A2AProtocolError, match="not configured"):
        client._load_agent_card()


def test_a2a_load_agent_card_wraps_http_errors(monkeypatch):
    client = A2AClient(
        A2AClientConfig(agent_card_url="https://agent.example/.well-known/agent.json")
    )
    error = HTTPError(client._config.agent_card_url, 503, "unavailable", hdrs=None, fp=None)
    monkeypatch.setattr("agent_gateway.urlopen", lambda req, timeout: (_ for _ in ()).throw(error))
    with pytest.raises(A2AProtocolError, match="HTTP error 503: unavailable"):
        client._load_agent_card()


def test_a2a_load_agent_card_wraps_transport_errors(monkeypatch):
    client = A2AClient(
        A2AClientConfig(agent_card_url="https://agent.example/.well-known/agent.json")
    )
    monkeypatch.setattr(
        "agent_gateway.urlopen",
        lambda req, timeout: (_ for _ in ()).throw(URLError("offline")),
    )
    with pytest.raises(A2AProtocolError, match="transport error"):
        client._load_agent_card()


def test_a2a_load_agent_card_rejects_invalid_json(monkeypatch):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{"

    monkeypatch.setattr("agent_gateway.urlopen", lambda req, timeout: R())
    client = A2AClient(
        A2AClientConfig(agent_card_url="https://agent.example/.well-known/agent.json")
    )
    with pytest.raises(A2AProtocolError, match="not valid JSON"):
        client._load_agent_card()


def test_a2a_load_agent_card_requires_json_object(monkeypatch):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'["not","an","object"]'

    monkeypatch.setattr("agent_gateway.urlopen", lambda req, timeout: R())
    client = A2AClient(
        A2AClientConfig(agent_card_url="https://agent.example/.well-known/agent.json")
    )
    with pytest.raises(A2AProtocolError, match="must be a JSON object"):
        client._load_agent_card()


def test_a2a_reads_endpoint_from_agent_card(monkeypatch):
    captured = {}

    class R:
        def __init__(self, payload):
            self._payload = payload

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return self._payload

    def fake(req, timeout):
        if req.get_method() == "GET":
            return R(b'{"name":"demo","endpoint":"https://agent.example/a2a"}')
        body = json.loads(req.data.decode())
        captured["method"] = body["method"]
        captured["params"] = body["params"]
        captured["id"] = body["id"]
        return R(json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": {"ok": True}}).encode())

    monkeypatch.setattr("agent_gateway.urlopen", fake)
    client = A2AClient(
        A2AClientConfig(agent_card_url="https://agent.example/.well-known/agent.json")
    )
    result = client.send_message({"message": {"role": "user", "parts": [{"text": "hi"}]}})
    assert result == {"ok": True}
    assert captured["method"] == "message/send"
    assert captured["params"]["message"]["role"] == "user"


def test_a2a_reads_endpoint_from_agent_card_transports():
    endpoint = A2AClient._extract_card_endpoint(
        {
            "transports": [
                {"protocol": "other", "endpoint": "https://agent.example/ignored"},
                {"name": "a2a", "url": "/a2a"},
            ]
        },
        "https://agent.example/.well-known/agent.json",
    )
    assert endpoint == "https://agent.example/a2a"


def test_a2a_reads_endpoint_from_agent_card_capabilities():
    endpoint = A2AClient._extract_card_endpoint(
        {"capabilities": {"a2a": {"endpoint": "/rpc"}}},
        "https://agent.example/.well-known/agent.json",
    )
    assert endpoint == "https://agent.example/rpc"


def test_a2a_requires_endpoint_in_agent_card():
    with pytest.raises(A2AProtocolError, match="does not define an endpoint"):
        A2AClient._extract_card_endpoint(
            {"transports": ["bad"], "capabilities": {"a2a": []}},
            "https://agent.example/.well-known/agent.json",
        )


def test_a2a_resolve_endpoint_returns_cached_value_inside_lock():
    client = A2AClient(
        A2AClientConfig(agent_card_url="https://agent.example/.well-known/agent.json")
    )

    class LockThatSetsEndpoint:
        def __enter__(self):
            client._endpoint = "https://agent.example/from-lock"
            return self

        def __exit__(self, *a):
            return False

    client._endpoint_lock = LockThatSetsEndpoint()
    client._load_agent_card = lambda: (_ for _ in ()).throw(AssertionError("should not load"))
    assert client._resolve_endpoint() == "https://agent.example/from-lock"


def test_a2a_sse_stream(monkeypatch):
    captured = {}

    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return (
                b'data: {"jsonrpc":"2.0","id":"req-id","result":{"delta":"hello"}}\n\n'
                b'data: {"jsonrpc":"2.0","id":"req-id","result":{"done":true}}\n\n'
                b"data: [DONE]\n\n"
            )

    def fake(req, timeout):
        captured["accept"] = dict((k.lower(), v) for k, v in req.header_items())["accept"]
        return R()

    monkeypatch.setattr("agent_gateway.urlopen", fake)
    monkeypatch.setattr("agent_gateway.uuid4", lambda: "req-id")
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    events = list(client.stream_message({"message": {"role": "user", "parts": []}}))
    assert events == [{"delta": "hello"}, {"done": True}]
    assert captured["accept"] == "text/event-stream"


def test_a2a_sse_stream_with_crlf(monkeypatch):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return (
                b'data: {"jsonrpc":"2.0","id":"req-id","result":{"delta":"one"}}\r\n\r\n'
                b'data: {"jsonrpc":"2.0","id":"req-id","result":{"delta":"two"}}\r\n\r\n'
                b"data: [DONE]\r\n\r\n"
            )

    monkeypatch.setattr("agent_gateway.urlopen", lambda req, timeout: R())
    monkeypatch.setattr("agent_gateway.uuid4", lambda: "req-id")
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    events = list(client.stream_message({"message": {"role": "user", "parts": []}}))
    assert events == [{"delta": "one"}, {"delta": "two"}]


def test_a2a_parse_sse_skips_comments_and_yields_raw_trailing_event():
    events = list(
        A2AClient._parse_sse(':keepalive\ndata: {"result":{"delta":"one"}}\n\ndata: not-json')
    )
    assert events == [{"result": {"delta": "one"}}, "not-json"]


def test_a2a_parse_sse_yields_raw_invalid_json_event():
    assert list(A2AClient._parse_sse("data: not-json\n\n")) == ["not-json"]


def test_a2a_parse_sse_stops_on_trailing_done():
    assert list(A2AClient._parse_sse("data: [DONE]")) == []


def test_a2a_send_message_raises_on_error_response(monkeypatch):
    monkeypatch.setattr(
        A2AClient,
        "_send_rpc",
        lambda self, payload, accept: (
            "req-id",
            {"jsonrpc": "2.0", "id": "req-id", "error": {"message": "boom"}},
        ),
    )
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    with pytest.raises(AgentInvocationError, match="boom"):
        client.send_message({"message": {"role": "user", "parts": []}})


def test_a2a_stream_message_requires_text_stream(monkeypatch):
    monkeypatch.setattr(
        A2AClient,
        "_send_rpc",
        lambda self, payload, accept: ("req-id", b"not-text"),
    )
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    with pytest.raises(A2AProtocolError, match="text stream"):
        list(client.stream_message({"message": {"role": "user", "parts": []}}))


def test_a2a_stream_message_yields_non_mapping_events(monkeypatch):
    monkeypatch.setattr(
        A2AClient,
        "_send_rpc",
        lambda self, payload, accept: ("req-id", "ignored"),
    )
    monkeypatch.setattr(A2AClient, "_parse_sse", staticmethod(lambda raw: iter(["chunk"])))
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    assert list(client.stream_message({"message": {"role": "user", "parts": []}})) == ["chunk"]


def test_a2a_stream_message_rejects_mismatched_event_id(monkeypatch):
    monkeypatch.setattr(
        A2AClient,
        "_send_rpc",
        lambda self, payload, accept: ("req-id", "ignored"),
    )
    monkeypatch.setattr(
        A2AClient,
        "_parse_sse",
        staticmethod(lambda raw: iter([{"id": "wrong", "result": {}}])),
    )
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    with pytest.raises(A2AProtocolError, match="does not match"):
        list(client.stream_message({"message": {"role": "user", "parts": []}}))


def test_a2a_stream_message_raises_on_event_error(monkeypatch):
    monkeypatch.setattr(
        A2AClient,
        "_send_rpc",
        lambda self, payload, accept: ("req-id", "ignored"),
    )
    monkeypatch.setattr(
        A2AClient,
        "_parse_sse",
        staticmethod(lambda raw: iter([{"id": "req-id", "error": {"message": "boom"}}])),
    )
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))
    with pytest.raises(AgentInvocationError, match="boom"):
        list(client.stream_message({"message": {"role": "user", "parts": []}}))


def test_cloudru_iam_token_provider_uses_stored_credentials(monkeypatch):
    import sqlite3

    from provider_credentials import create_schema, save_cloudru_iam_credentials

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    save_cloudru_iam_credentials(
        conn,
        key_id="master-id",
        key_secret="master-secret",
        project_id="project-1",
        service_account_id="sa-1",
        expires_at=None,
        encrypt=lambda value: f"enc:{value}",
    )
    monkeypatch.setattr(
        "cloudru_iam.CloudRuIamClient.access_token",
        lambda self: "iam-token",
    )

    provider = cloudru_iam_token_provider(conn, lambda value: value.replace("enc:", "", 1))
    assert provider() == "iam-token"
    conn.close()


def test_cloudru_iam_token_provider_translates_iam_error(monkeypatch):
    import sqlite3

    from provider_credentials import create_schema, save_cloudru_iam_credentials

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    save_cloudru_iam_credentials(
        conn,
        key_id="master-id",
        key_secret="master-secret",
        project_id="project-1",
        service_account_id="sa-1",
        encrypt=lambda value: f"enc:{value}",
    )
    monkeypatch.setattr(
        "cloudru_iam.CloudRuIamClient.access_token",
        lambda self: (_ for _ in ()).throw(CloudRuIamError("failed")),
    )

    provider = cloudru_iam_token_provider(conn, lambda value: value.replace("enc:", "", 1))
    with pytest.raises(A2AProtocolError, match="token exchange failed"):
        provider()
    conn.close()


def test_cloudru_iam_token_provider_requires_stored_credentials():
    import sqlite3

    from provider_credentials import create_schema

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)
    with pytest.raises(AgentInvocationError, match="not configured"):
        cloudru_iam_token_provider(conn, lambda value: value)
    conn.close()


def test_rest(monkeypatch):
    class R:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"ok":true}'

    monkeypatch.setattr("agent_gateway.urlopen", lambda req, timeout: R())
    assert RESTAgentClient("https://agent.example/run").send({"x": 1}) == {"ok": True}
