"""Regression tests for InvocationContext ownership in /api/chat."""
from unittest.mock import patch

import mcp_routes
from app import app
from invocation_context import InvocationContext
from trace_manager import ExecutionTrace


def test_chat_creates_one_context_and_uses_its_identifiers():
    captured = {}

    class FakeClient:
        def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
            captured["conversation_id"] = conversation_id
            captured["trace"] = trace
            captured["params"] = params
            trace.add_response(
                {"id": "resp-test", "status": "completed", "output": []},
                step_index=1,
            )
            return {"output": [], "usage": {}}

        @staticmethod
        def extract_reasoning_and_text(_response):
            return "", "reply"

        @staticmethod
        def extract_usage(_response):
            return None

    with patch.object(mcp_routes, "AliceClient", lambda _config: FakeClient()), \
         patch.object(mcp_routes, "get_conv_settings", return_value={}), \
         patch.object(mcp_routes, "add_message"):
        app.config["TESTING"] = True
        with app.test_client() as client:
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": "conv-context-test",
                    "message": "hello",
                    "model": "aliceai-llm",
                    "params": {},
                },
            )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["conversation_id"] == "conv-context-test"
    assert payload["invocation_id"]
    assert payload["session_id"] == "conv-context-test"
    assert payload["trace_id"]
    assert isinstance(captured["trace"], ExecutionTrace)
    assert captured["conversation_id"] == payload["conversation_id"]
    assert captured["trace"].trace_id == payload["trace_id"]
    assert captured["trace"].trace["invocation_id"] == payload["invocation_id"]


def test_chat_context_is_fresh_for_each_request():
    contexts = []

    class FakeClient:
        def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
            contexts.append((conversation_id, trace.trace_id))
            return {"output": [], "usage": {}}

        @staticmethod
        def extract_reasoning_and_text(_response):
            return "", "reply"

        @staticmethod
        def extract_usage(_response):
            return None

    with patch.object(mcp_routes, "AliceClient", lambda _config: FakeClient()), \
         patch.object(mcp_routes, "get_conv_settings", return_value={}), \
         patch.object(mcp_routes, "add_message"):
        app.config["TESTING"] = True
        with app.test_client() as client:
            for message in ("one", "two"):
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": "same-conversation",
                        "message": message,
                        "model": "aliceai-llm",
                        "params": {},
                    },
                )
                assert response.status_code == 200

    assert len(contexts) == 2
    assert contexts[0][0] == contexts[1][0] == "same-conversation"
    assert contexts[0][1] != contexts[1][1]
