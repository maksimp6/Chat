import json
from urllib.error import HTTPError

import pytest

from agent_gateway import (
    A2AClient,
    A2AClientConfig,
    A2AProtocolError,
    AgentAlreadyRegistered,
    AgentDescriptor,
    AgentGateway,
    AgentInvocationError,
    AgentNotFound,
)


def test_register_list_and_invoke_agent():
    gateway = AgentGateway()
    gateway.register(
        AgentDescriptor(
            agent_id="echo",
            name="Echo agent",
            capabilities=("chat",),
        ),
        lambda payload: {"echo": payload["message"]},
    )

    assert [agent.agent_id for agent in gateway.list_agents("chat")] == ["echo"]
    result = gateway.invoke("echo", {"message": "hello"})
    assert result.status == "completed"
    assert result.result == {"echo": "hello"}
    assert result.invocation_id


def test_duplicate_and_missing_agents():
    gateway = AgentGateway()
    descriptor = AgentDescriptor(agent_id="echo", name="Echo")
    gateway.register(descriptor, lambda payload: payload)
    with pytest.raises(AgentAlreadyRegistered):
        gateway.register(descriptor, lambda payload: payload)
    with pytest.raises(AgentNotFound):
        gateway.get("missing")


def test_handler_errors_are_returned_in_envelope():
    gateway = AgentGateway()
    gateway.register(
        AgentDescriptor(agent_id="broken", name="Broken"),
        lambda payload: 1 / 0,
    )
    result = gateway.invoke("broken", {})
    assert result.status == "error"
    assert "ZeroDivisionError" in (result.error or "")


def test_a2a_client_sends_json_rpc(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": captured["request"]["id"],
                    "result": {"task": {"id": "task-1"}},
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = {
            key.lower(): value for key, value in request.header_items()
        }
        captured["request"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("agent_gateway.urlopen", fake_urlopen)
    client = A2AClient(
        A2AClientConfig("https://agent.example/a2a", timeout_seconds=7),
        token_provider=lambda: "secret-token",
    )

    result = client.send_message(
        {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": "hello"}],
                "messageId": "message-1",
            },
            "metadata": {"source": "alice-pro"},
        }
    )

    assert result == {"task": {"id": "task-1"}}
    assert captured["url"] == "https://agent.example/a2a"
    assert captured["timeout"] == 7
    assert captured["headers"]["authorization"] == "Bearer secret-token"
    assert captured["request"]["method"] == "message/send"
    assert captured["request"]["params"]["message"]["messageId"] == "message-1"


def test_a2a_client_rejects_mismatched_response_id(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"jsonrpc":"2.0","id":"wrong","result":{}}'

    monkeypatch.setattr(
        "agent_gateway.urlopen", lambda request, timeout: FakeResponse()
    )
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))

    with pytest.raises(A2AProtocolError, match="does not match"):
        client.send_message({"message": {"role": "user", "parts": []}})


def test_a2a_client_surfaces_json_rpc_errors(monkeypatch):
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": captured["request"]["id"],
                    "error": {"code": -1, "message": "denied"},
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["request"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr("agent_gateway.urlopen", fake_urlopen)
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))

    with pytest.raises(AgentInvocationError, match="denied"):
        client.send_message({"message": {"role": "user", "parts": []}})


def test_a2a_client_converts_http_errors(monkeypatch):
    def fake_urlopen(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", hdrs=None, fp=None)

    monkeypatch.setattr("agent_gateway.urlopen", fake_urlopen)
    client = A2AClient(A2AClientConfig("https://agent.example/a2a"))

    with pytest.raises(A2AProtocolError, match="401"):
        client.send_message({"message": {"role": "user", "parts": []}})
