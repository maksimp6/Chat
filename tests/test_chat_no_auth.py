"""Offline integration coverage for the shared chat, runtime and voice APIs."""

import pytest
import requests


@pytest.fixture
def clients(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "logos.memory"))
    monkeypatch.delenv("LOGOS_OWNER_ID", raising=False)
    monkeypatch.setenv("LOGOS_REQUIRE_SHORT_TOKEN", "true")
    from agent_memory.client import close_memory_clients
    import app as app_module
    import chat.voice_routes as voice

    close_memory_clients()
    app_module._initialize_storage()
    app_module.app.config.update(TESTING=True)

    def forbidden_network(*args, **kwargs):
        raise AssertionError("offline test attempted an external request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_network)
    yield app_module.app.test_client(), app_module.app.test_client()
    voice._sessions.clear()
    close_memory_clients()


class FakeClient:
    def __init__(self, _config):
        pass

    def create_conversation(self, execution_trace=None):
        return {"id": "shared-conversation"}

    def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
        trace.add_response({"id": "offline-response", "status": "completed", "output": []})
        return {"output": [], "usage": {}}

    def ask(self, *args, **kwargs):
        return {"output": []}

    @staticmethod
    def extract_reasoning_and_text(response):
        return "", "offline reply"

    @staticmethod
    def extract_text(response):
        return "offline reply"

    @staticmethod
    def extract_usage(response):
        return None


def test_conversation_and_chat_work_between_clients_without_identity(clients, monkeypatch):
    first, second = clients
    import chat.mcp_routes as routes

    monkeypatch.setattr(routes, "LogosClient", FakeClient)
    created = first.post("/api/conversations", json={"title": "Shared"})
    assert created.status_code == 201
    conversation_id = created.get_json()["id"]
    stale_headers = {"X-Logos-User-Token": "obsolete", "Authorization": "Bearer obsolete"}
    second.set_cookie("logos_user_token", "obsolete")
    listing = second.get("/api/conversations", headers=stale_headers)
    assert listing.status_code == 200
    assert [row["id"] for row in listing.get_json()["conversations"]] == [conversation_id]
    answer = second.post(
        "/api/chat", headers=stale_headers,
        json={"conversation_id": conversation_id, "message": "hello"},
    )
    assert answer.status_code == 200, answer.get_json()
    assert answer.get_json()["reply"] == "offline reply"
    history = first.get(f"/api/conversations/{conversation_id}/messages")
    assert history.status_code == 200
    assert [message["text"] for message in history.get_json()["messages"]] == [
        "hello", "offline reply"
    ]
    invocation_id = answer.get_json()["invocation_id"]
    assert first.get(f"/api/invocations/{invocation_id}/trace").status_code == 200
    assert second.get("/api/conversations/missing/messages").status_code == 404


def test_legacy_runtime_session_is_shared_and_reusable(clients):
    _, second = clients
    from sessions.manager import create_session
    from invocation.manager import create_invocation
    from storage.db import create_conversation
    from conversations.ownership import set_owner

    create_conversation("legacy-chat", "Legacy", "yandexgpt-5-lite")
    set_owner("legacy-chat", "old-owner")
    create_session("legacy-session", metadata={"user_id": "old-owner"})
    old_invocation = create_invocation("legacy-session", "legacy-chat", user_id="old-owner")
    assert second.get("/api/sessions/legacy-session").status_code == 200
    assert second.get(f"/api/invocations/{old_invocation.invocation_id}").status_code == 200
    result = second.post(
        "/api/sessions/legacy-session/invocations", json={"conversation_id": "legacy-chat"}
    )
    assert result.status_code == 201, result.get_json()
    assert result.get_json()["user_id"] == "default"
    assert second.get("/api/sessions/missing").status_code == 404


def test_legacy_voice_session_has_no_owner_gate(clients):
    first, second = clients
    import chat.voice_routes as voice

    created = first.post("/api/voice/session", json={})
    assert created.status_code == 200
    session_id = created.get_json()["session_id"]
    voice._sessions[session_id].owner_id = "old-owner"
    voice._sessions[session_id].output_audio = b"OggOffline"
    uploaded = second.post(f"/api/voice/audio?session_id={session_id}", data=b"offline")
    assert uploaded.status_code == 200
    output = second.get(f"/api/voice/output?session_id={session_id}")
    assert output.status_code == 200
    assert output.data == b"OggOffline"


def test_mcp_server_records_from_legacy_owner_are_shared(clients):
    _, second = clients
    from mcp_server.storage import create_server

    server_id = create_server("old-owner", {"server_label": "Legacy", "server_url": "https://example.com/mcp"})
    result = second.get(f"/api/mcp-servers/{server_id}")
    assert result.status_code == 200
    assert result.get_json()["server_label"] == "Legacy"
    changed = second.put(f"/api/mcp-servers/{server_id}", json={"server_label": "Shared"})
    assert changed.status_code == 200
    listing = second.get("/api/mcp-servers").get_json()["data"]
    assert [row["server_label"] for row in listing] == ["Shared"]
    assert second.delete(f"/api/mcp-servers/{server_id}").status_code == 200
    assert second.get(f"/api/mcp-servers/{server_id}").status_code == 404


def test_execute_approved_compatibility_route_no_longer_needs_token(clients, monkeypatch):
    first, _ = clients
    import chat.mcp_routes as routes
    from storage.db import create_conversation

    create_conversation("tool-chat", "Tool", "yandexgpt-5-lite")
    monkeypatch.setattr(routes, "LogosClient", FakeClient)
    monkeypatch.setattr(routes.registry, "get_tool_meta", lambda name: {"description": "Offline"})
    calls = []

    class OfflineExecutor:
        def __init__(self, registry):
            pass

        def execute(self, call):
            calls.append(call)
            return {"success": True, "data": "done"}

    monkeypatch.setattr(routes, "UniversalToolExecutor", OfflineExecutor)
    response = first.post(
        "/api/mcp/execute-approved", json={"conversation_id": "tool-chat", "name": "offline_tool"}
    )
    assert response.status_code == 200, response.get_json()
    assert response.get_json()["reply"] == "offline reply"
    assert len(calls) == 1
    assert calls[0].tool_name == "offline_tool"


def test_tool_call_chain_runs_without_approval_pause(clients, monkeypatch):
    import chat.mcp_routes as routes

    client = object.__new__(routes.LogosClient)
    proposals = iter([
        {"id": "tool-step", "output": [{"type": "function_call", "name": "offline_tool", "call_id": "call-1", "arguments": "{}"}]},
        {"id": "answer-step", "output": [{"type": "message", "content": [{"type": "output_text", "text": "done"}]}]},
    ])
    executed = []
    requests_seen = []

    def fake_provider(**kwargs):
        requests_seen.append(kwargs)
        return next(proposals)

    def fake_execute(call, tool_servers, **kwargs):
        executed.append(call)
        return {"name": "offline_tool", "result": "done"}

    monkeypatch.setattr(client, "_ask_provider", fake_provider)
    monkeypatch.setattr(client, "_execute_single_tool", fake_execute)
    response = client.ask_with_mcp("do it", "yandexgpt-5-lite", params={"approval_mode": "pause"})
    assert response["id"] == "answer-step"
    assert len(executed) == 1
    assert len(requests_seen) == 2
    assert requests_seen[1]["params"]["input"][-1] == {
        "type": "function_call_output", "call_id": "call-1", "output": "done"
    }
