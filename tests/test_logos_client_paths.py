"""Coverage for LogosClient paths that the rename made visible to the diff gate (#1316).

The continuation after a non-approval tool call and the approved-action route both
run LogosClient code. The network, the database and the tool executor are replaced
by fakes, so these tests stay deterministic.
"""

from types import SimpleNamespace

from flask import Flask

import chat.mcp_routes as mcp_routes
from chat.mcp_routes import LogosClient
from yandex_client_modules.client import YandexResponsesClient


def test_continuation_after_a_non_approval_tool_call_reissues_the_request(monkeypatch):
    sent = []

    def base_ask_with_mcp(self, **kwargs):
        sent.append(kwargs)
        return {"id": f"resp-{len(sent)}"}

    monkeypatch.setattr(YandexResponsesClient, "ask_with_mcp", base_ask_with_mcp)
    monkeypatch.setattr(
        mcp_routes,
        "extract_function_calls",
        lambda response: [{"name": "inspect"}] if response["id"] == "resp-1" else [],
    )
    monkeypatch.setattr(mcp_routes, "_tool_call_needs_approval", lambda call: False)
    monkeypatch.setattr(
        mcp_routes, "mcp_storage", SimpleNamespace(list_servers=lambda **_owner: [])
    )
    monkeypatch.setattr(
        LogosClient,
        "_execute_single_tool",
        lambda self, call, servers, trace=None: {"result": "ok"},
    )

    def run_tool_loop_once(response, execute_tool, continue_request, max_rounds, should_pause):
        execute_tool({"name": "inspect"})
        return continue_request([{"type": "function_call_output", "output": "ok"}], response)

    monkeypatch.setattr(mcp_routes, "run_tool_loop", run_tool_loop_once)

    client = object.__new__(LogosClient)
    final = client.ask_with_mcp(message="hi", model_key="logos-runtime", conversation_id="c1")

    assert final == {"id": "resp-2"}
    assert sent[1]["message"] == ""
    assert sent[1]["params"]["input"] == [{"type": "function_call_output", "output": "ok"}]
    assert sent[1]["params"]["background"] is True


def test_execute_approved_runs_the_tool_and_returns_the_synthesized_reply(monkeypatch):
    class FakeClient:
        def __init__(self, config):
            pass

        def ask(self, prompt, model_key, conv_id, extra):
            return {"text": prompt}

        def extract_text(self, response):
            return "Готово"

        def extract_usage(self, response):
            return None

    added = []
    monkeypatch.setattr(mcp_routes, "LogosClient", FakeClient)
    monkeypatch.setattr(
        mcp_routes, "registry", SimpleNamespace(get_tool_meta=lambda name: {"name": name})
    )
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(mcp_routes, "check_access", lambda conv_id, owner_id: True)
    monkeypatch.setattr(
        mcp_routes,
        "UniversalToolExecutor",
        lambda registry: SimpleNamespace(execute=lambda call: {"success": True, "data": {"ok": 1}}),
    )
    monkeypatch.setattr(
        mcp_routes,
        "add_message",
        lambda conv_id, role, text, cost=0.0: added.append((conv_id, role, text, cost)),
    )
    token = mcp_routes._approval_token("owner-1", "conv-1", "inspect", {"a": 1})

    app = Flask(__name__)
    app.register_blueprint(mcp_routes.mcp_bp)
    response = app.test_client().post(
        "/api/mcp/execute-approved",
        json={
            "conversation_id": "conv-1",
            "name": "inspect",
            "arguments": {"a": 1},
            "model": "logos-runtime",
            "approval_token": token,
        },
    )

    assert response.status_code == 200
    body = response.get_json()
    assert body["reply"] == "Готово"
    assert body["cost"] == 0.0
    assert added == [("conv-1", "assistant", "Готово", 0.0)]
