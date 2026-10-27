"""Provider terminal state is preserved through the real chat/storage lifecycle.

Run only in the authorized Redmi 9 GitHub runner job. The provider transport is
replaced here to reproduce incomplete/error payloads without paid generation;
the separate real-browser run supplies model/tool execution evidence.
"""

import copy

import pytest
import requests


@pytest.fixture
def terminal_chat(tmp_path, monkeypatch):
    monkeypatch.setenv("LOGOS_MEMORY_PATH", str(tmp_path / "terminal-status.memory"))
    monkeypatch.delenv("LOGOS_OWNER_ID", raising=False)
    from agent_memory.client import close_memory_clients
    import app as app_module
    import chat.mcp_routes as routes
    from storage.db import create_conversation

    close_memory_clients()
    app_module._initialize_storage()
    app_module.app.config.update(TESTING=True)
    create_conversation("terminal-chat", "Terminal status", "yandexgpt-5-pro")

    def forbidden_network(*args, **kwargs):
        raise AssertionError("terminal-state regression attempted an external request")

    monkeypatch.setattr(requests.sessions.Session, "request", forbidden_network)
    settlements = []

    def settle(billing, owner):
        settlements.append((billing, owner))
        return {"status": "posted", "external_charge_verified": False}

    monkeypatch.setattr(routes, "settle_billing_to_treasury", settle)
    yield app_module.app.test_client(), routes, settlements
    close_memory_clients()


def provider_payload(status="completed", text="Файл успешно создан."):
    return {
        "id": "terminal-response",
        "status": status,
        "output": [{"type": "message", "role": "assistant", "content": [
            {"type": "output_text", "text": text},
        ]}],
        "usage": {"input_tokens": 10, "output_tokens": 7, "total_tokens": 17},
    }


def send_provider_payload(client, routes, monkeypatch, payload):
    def transport(self, *, trace, **kwargs):
        # The trace receives exactly what the fake provider returns. Execution
        # counts are never inferred from the assistant's claims in output_text.
        trace.add_response(copy.deepcopy(payload))
        return copy.deepcopy(payload)

    monkeypatch.setattr(routes.LogosClient, "ask_with_mcp", transport)
    return client.post("/api/chat", json={
        "conversation_id": "terminal-chat",
        "message": "Создай тестовый файл и подтверди результат инструментом.",
        "model": "yandexgpt-5-pro",
    })


@pytest.mark.parametrize("status,reason", [
    ("incomplete", "max_output_tokens"),
    ("incomplete", "content_filter"),
    ("failed", None),
    ("cancelled", None),
    ("in_progress", None),
    (None, None),
])
def test_noncompleted_provider_text_is_failed_and_preserved(
    terminal_chat, monkeypatch, status, reason,
):
    client, routes, settlements = terminal_chat
    payload = provider_payload(status)
    if reason:
        payload["incomplete_details"] = {"reason": reason}
    response = send_provider_payload(client, routes, monkeypatch, payload)
    assert response.status_code == 502, response.get_json()
    body = response.get_json()
    assert body["status"] == "failed"
    assert body["provider_status"] == (status or "unknown")
    assert body["partial_output"] == "Файл успешно создан."
    assert body["reply"].startswith(body["partial_output"])
    assert "⚠️" in body["reply"]
    assert body["trace"]["tool_calls"] == []
    if reason:
        assert body["provider_reason"] == reason

    from invocation.manager import get_invocation
    from storage.db import get_messages

    invocation = get_invocation(body["invocation_id"])
    assert invocation["status"] == "failed"
    assert invocation["result"] is None
    assert invocation["error"]["provider_status"] == (status or "unknown")
    assert invocation["error"]["partial_output"] == body["partial_output"]
    assert invocation["trace"]["responses"][-1]["raw"]["status"] == status
    assert invocation["trace"]["tool_calls"] == []
    assert invocation["trace"]["billing"]["settlement"] == {
        "status": "posted", "external_charge_verified": False,
    }
    assert len(settlements) == 1
    assistant = get_messages("terminal-chat")[-1]
    assert assistant["text"] == body["reply"]
    assert assistant["source"] == "error"
    assert assistant["trace"]["invocation_id"] == body["invocation_id"]


def test_completed_with_provider_error_is_not_success(terminal_chat, monkeypatch):
    client, routes, _ = terminal_chat
    payload = provider_payload()
    payload["error"] = {"code": "provider_failure", "message": "private provider detail"}
    response = send_provider_payload(client, routes, monkeypatch, payload)
    body = response.get_json()
    assert response.status_code == 502
    assert body["error"] == "provider_error"
    assert body["provider_error_present"] is True
    assert body["provider_error_code"] == "provider_failure"
    assert "private provider detail" not in body["reply"]


def test_empty_incomplete_keeps_provider_reason(terminal_chat, monkeypatch):
    client, routes, _ = terminal_chat
    from storage.db import add_message

    add_message("terminal-chat", "assistant", "Текст из другого вызова", trace={
        "invocation_id": "unrelated-invocation", "trace_id": "unrelated-trace",
    })
    payload = provider_payload("incomplete", "")
    payload["incomplete_details"] = {"reason": "max_output_tokens"}
    response = send_provider_payload(client, routes, monkeypatch, payload)
    body = response.get_json()
    assert response.status_code == 502
    assert body["error"] == "provider_incomplete"
    assert body["provider_reason"] == "max_output_tokens"
    assert body["partial_output"] is None
    assert "max_output_tokens" in body["reply"]
    assert "Текст из другого вызова" not in body["reply"]


@pytest.mark.parametrize("invalid", [None, [], "invalid provider body", 123])
def test_nonobject_provider_response_has_stable_failure(terminal_chat, monkeypatch, invalid):
    client, routes, _ = terminal_chat
    response = send_provider_payload(client, routes, monkeypatch, invalid)
    body = response.get_json()
    assert response.status_code == 502, body
    assert body["error"] == "provider_invalid_response"
    assert body["status"] == "failed"
    assert body["provider_status"] == "unknown"
    assert body["partial_output"] is None
    assert "invalid provider body" not in body["reply"]
    from invocation.manager import get_invocation

    invocation = get_invocation(body["invocation_id"])
    assert invocation["status"] == "failed"
    assert invocation["error"]["code"] == "provider_invalid_response"
    evidence = next(event["payload"] for event in invocation["trace"]["events"]
                    if event["type"] == "provider_invalid_response")
    assert evidence["raw"] == invalid


def test_empty_final_incomplete_preserves_only_current_invocation_partial(
    terminal_chat, monkeypatch,
):
    client, routes, _ = terminal_chat
    from storage.db import add_message

    add_message("terminal-chat", "assistant", "Несвязанный старый ответ", trace={
        "invocation_id": "old-invocation", "trace_id": "old-trace",
    })
    earlier = provider_payload(text="Частичный ответ текущего вызова.")
    earlier["id"] = "earlier-round"
    final = provider_payload("incomplete", "")
    final["incomplete_details"] = {"reason": "max_output_tokens"}

    def transport(self, *, trace, **kwargs):
        trace.add_response(copy.deepcopy(earlier), step_index=1)
        trace.add_response(copy.deepcopy(final), step_index=2)
        return copy.deepcopy(final)

    monkeypatch.setattr(routes.LogosClient, "ask_with_mcp", transport)
    response = client.post("/api/chat", json={
        "conversation_id": "terminal-chat", "message": "Продолжи проверку",
        "model": "yandexgpt-5-pro",
    })
    body = response.get_json()
    assert response.status_code == 502, body
    assert body["partial_output"] == "Частичный ответ текущего вызова."
    assert "Несвязанный старый ответ" not in body["reply"]
    from invocation.manager import get_invocation

    invocation = get_invocation(body["invocation_id"])
    assert invocation["status"] == "failed"
    assert invocation["trace"]["responses"][-1]["raw"] == final
    assert invocation["trace"]["responses"][0]["raw"] == earlier


def test_actual_completed_payload_completes_invocation(terminal_chat, monkeypatch):
    client, routes, _ = terminal_chat
    response = send_provider_payload(client, routes, monkeypatch, provider_payload())
    body = response.get_json()
    assert response.status_code == 200, body
    assert body["status"] == body["provider_status"] == "completed"
    assert "error" not in body
    assert body["reply"] == "Файл успешно создан."
    from invocation.manager import get_invocation
    from storage.db import get_messages

    invocation = get_invocation(body["invocation_id"])
    assert invocation["status"] == "completed"
    assert invocation["result"]["provider_status"] == "completed"
    assert get_messages("terminal-chat")[-1]["source"] is None


def test_trace_endpoint_returns_complete_native_rich_trace(terminal_chat, monkeypatch):
    """Exercise HTTP serialization and its contract, not only direct store reads."""
    client, routes, _ = terminal_chat
    call = {
        "type": "function_call", "name": "read_file", "call_id": "rich-read-call",
        "arguments": '{"path":"fixture.txt"}', "status": "completed",
    }
    initial = {"id": "rich-tools-response", "status": "completed", "output": [call]}
    final = provider_payload(text="Проверка завершена.")
    final["id"] = "rich-final-response"

    def transport(self, *, trace, **kwargs):
        trace.set_context(agent_id="regression-agent", runtime_id="regression-runtime")
        trace.set_reasoning_plan({"plan_id": "rich-plan", "steps": [{"tool": "read_file"}]})
        trace.add_api_request({"tools": [{
            "type": "function", "name": "read_file",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}},
        }]}, step_index=1)
        trace.add_response(copy.deepcopy(initial), step_index=1)
        # Contract fixture only: trace the local deterministic fixture callback.
        # Real tool/provider execution is checked separately by the browser run.
        trace.track_tool_execution(
            "read_file", {"path": "fixture.txt"},
            lambda: {"success": True, "data": {"content": "fixture content"},
                     "metadata": {"phase": "execution", "executor": "local"}},
            call_id="rich-read-call", step=1,
        )
        trace.add_api_request({"input": [{
            "type": "function_call_output", "call_id": "rich-read-call", "output": "fixture content",
        }]}, step_index=2)
        trace.add_response(copy.deepcopy(final), step_index=2)
        return copy.deepcopy(final)

    monkeypatch.setattr(routes.LogosClient, "ask_with_mcp", transport)
    answer = client.post("/api/chat", json={
        "conversation_id": "terminal-chat", "message": "Проверь файл",
        "model": "yandexgpt-5-pro",
    })
    assert answer.status_code == 200, answer.get_json()
    invocation_id = answer.get_json()["invocation_id"]
    response = client.get(f"/api/invocations/{invocation_id}/trace")
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["context"]["invocation_id"] == invocation_id
    assert body["context"]["agent_id"] == "regression-agent"
    assert body["context"]["runtime_id"] == "regression-runtime"
    assert body["reasoning_plan"] == {"plan_id": "rich-plan", "steps": [{"tool": "read_file"}]}
    assert body["api_requests"][0]["payload"]["tools"][0]["name"] == "read_file"
    assert body["responses"][0]["raw"] == initial
    assert body["responses"][-1]["raw"] == final
    assert body["tool_calls"][0]["call_id"] == "rich-read-call"
    assert body["tool_calls"][0]["result"]["success"] is True
