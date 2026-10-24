"""Isolated HTTP/profile contract, not a live provider or whole-application E2E.

The actual chat handler, allowlist and tool-continuation method are compiled from
their AST so this focused check does not import storage/services unrelated to
Optima. External services are recording fakes; the request builder is real.
"""

import ast
from copy import deepcopy
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from flask import Blueprint, Flask, jsonify, request
import pytest

from core.config import Config
from core.model_profiles import ModelProfileError, completion_model_keys, selected_model_profile
from yandex_client_modules.request_builder import build_response_payload


ROUTES_PATH = Path(__file__).resolve().parents[1] / "chat" / "mcp_routes.py"
REPLY = "Synthetic reply: Optima request shape was recorded locally."


class RecordingTrace:
    def __init__(self):
        self.trace = {}
        self.finalized = False

    def set_request(self, value):
        self.trace["request"] = deepcopy(value)

    def finalize(self):
        self.finalized = True
        return {**self.trace, "billing": {"cost_status": "unknown"}}


def _route_contract(*, drop_profile=False, tool_call=False):
    """Execute unchanged production functions with only their boundaries replaced."""
    state = SimpleNamespace(payloads=[], params=[], invocations=[], messages=[], clients=0)

    def create_invocation(session_id, conversation_id, **kwargs):
        state.invocations.append((session_id, conversation_id))
        return SimpleNamespace(
            session_id=session_id,
            conversation_id=conversation_id,
            invocation_id="synthetic-invocation",
            trace_id="synthetic-trace",
            user_id="synthetic-owner",
        )

    def one_round(response, execute, continuation, *, max_rounds):
        assert max_rounds > 0
        call = response["calls"][0]
        result = execute(call)
        return continuation(
            [{"type": "function_call_output", "call_id": call["call_id"], "output": json.dumps(result)}],
            response,
        )

    noop = lambda *args, **kwargs: None
    namespace = {
        "Any": Any,
        "_json": json,
        "request": request,
        "jsonify": jsonify,
        "logger": logging.getLogger("test.optima.route"),
        "mcp_bp": Blueprint("optima_contract", __name__),
        "Config": Config,
        "ModelProfileError": ModelProfileError,
        "selected_model_profile": selected_model_profile,
        "completion_model_keys": completion_model_keys,
        "YandexResponsesClient": object,
        "ClaudeOAuthTransport": object,
        "SkillRegistryError": type("SkillRegistryError", (Exception,), {}),
        "ProviderQuotaExceeded": type("ProviderQuotaExceeded", (Exception,), {}),
        "skill_registry": SimpleNamespace(load_many=lambda *args, **kwargs: []),
        "get_current_owner_id": lambda **kwargs: "synthetic-owner",
        "check_access": lambda *args: True,
        "create_invocation": create_invocation,
        "create_invocation_trace": lambda _: RecordingTrace(),
        "bind_current_trace": lambda _: "synthetic-trace-token",
        "reset_current_trace": noop,
        "start_invocation": noop,
        "finish_invocation": noop,
        "fail_invocation": noop,
        "persist_invocation_trace": noop,
        "get_conv_settings": lambda _: {},
        "add_message": lambda *args, **kwargs: state.messages.append((args, kwargs)),
        "maybe_update_conversation_title": lambda *args: "Synthetic contract conversation",
        "settle_billing_to_treasury": lambda *args: {"status": "synthetic_not_billed"},
        "record_yandex_mcp_activity": noop,
        "extract_function_calls": lambda response: response.get("calls", []),
        "mcp_storage": SimpleNamespace(list_servers=lambda **kwargs: []),
        "run_tool_loop": one_round,
    }
    names = {
        "_tool_rounds", "_normalize_requested_skills", "_client_params", "LogosClient",
        "_turn_cost", "_append_post_final_error", "chat",
    }
    constants = {"_CLIENT_PARAM_KEYS", "_MAX_TOOL_ROUNDS"}
    tree = ast.parse(ROUTES_PATH.read_text(encoding="utf-8"), filename=str(ROUTES_PATH))
    nodes = [
        node for node in tree.body
        if (isinstance(node, (ast.FunctionDef, ast.ClassDef)) and node.name in names)
        or (isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id in constants for target in node.targets
        ))
    ]
    assert {node.name for node in nodes if isinstance(node, (ast.FunctionDef, ast.ClassDef))} == names
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROUTES_PATH), "exec"), namespace)
    if drop_profile:
        namespace["_CLIENT_PARAM_KEYS"] -= {"model_profile"}

    class RecordingClient(namespace["LogosClient"]):
        def __init__(self, _config):
            state.clients += 1

        def _ask_provider(self, **kwargs):
            state.params.append(deepcopy(kwargs["params"]))
            state.payloads.append(build_response_payload(
                "synthetic-project", kwargs["model_key"], kwargs["message"],
                params=kwargs["params"], conversation_id=kwargs["conversation_id"],
            ))
            if tool_call and len(state.payloads) == 1:
                return {"calls": [{"call_id": "synthetic-call", "name": "read_fixture"}]}
            return {"text": REPLY}

        def _execute_single_tool(self, *args, **kwargs):
            return {"result": {"fixture": "local synthetic data"}}

        def extract_reasoning_and_text(self, response):
            return "", response["text"]

        def extract_usage(self, response):
            return {}

    namespace["LogosClient"] = RecordingClient
    app = Flask(__name__)
    app.testing = True
    app.register_blueprint(namespace["mcp_bp"])
    return app.test_client(), state


def _post(client, model="gpt-oss-120b", **params):
    return client.post("/api/chat", json={
        "conversation_id": "synthetic-conversation",
        "message": "Read the local fixture and report its value.",
        "model": model,
        "params": {"model_profile": "optima", **params},
    })


def _assert_balanced_gpt_payload(payload):
    assert payload["reasoning"] == {"effort": "medium"}, "Optima must reach the provider builder"
    assert payload["truncation"] == "disabled"
    assert "model_profile" not in payload


def test_http_selection_reaches_real_payload_builder_and_returns_reply():
    client, state = _route_contract()
    response = _post(client, temperature=0.4, instructions="Preserve this instruction.")

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["reply"] == REPLY
    assert len(state.payloads) == 1
    _assert_balanced_gpt_payload(state.payloads[0])
    assert state.payloads[0]["model"] == "gpt://synthetic-project/gpt-oss-120b/latest"
    assert state.payloads[0]["temperature"] == 0.4
    assert state.payloads[0]["instructions"] == "Preserve this instruction."
    assert "max_output_tokens" not in state.payloads[0]
    assert state.params[0]["model_profile"] == "optima"
    assert [args[1] for args, _ in state.messages] == ["user", "assistant"]


def test_http_qwen_does_not_receive_stale_manual_reasoning_or_truncation():
    client, state = _route_contract()
    response = _post(
        client, "qwen3.6-35b-a3b", reasoning_effort="medium",
        reasoning={"effort": "high"}, truncation="auto",
    )

    assert response.status_code == 200, response.get_json()
    assert "reasoning" not in state.payloads[0]
    assert state.payloads[0]["truncation"] == "disabled"


@pytest.mark.parametrize("model,profile", [
    ("gpt-oss-120b", "unknown-profile"),
    ("not-in-the-registry", "optima"),
    ("qwen3-235b-a22b-fp8", "optima"),
    ("speech-realtime-deepseek-v4-flash", "optima"),
    ("claude-opus-5-5", "optima"),
])
def test_invalid_profile_is_400_before_any_client_or_storage_effect(model, profile):
    client, state = _route_contract()
    response = _post(client, model, model_profile=profile)

    assert response.status_code == 400, response.get_json()
    assert response.get_json()["code"] == "invalid_model_profile"
    assert state.clients == 0
    assert state.payloads == state.invocations == state.messages == []


def test_explicit_optima_output_limit_survives_the_http_boundary():
    client, state = _route_contract()
    response = _post(client, max_output_tokens=512)

    assert response.status_code == 200, response.get_json()
    assert state.payloads[0]["max_output_tokens"] == 512


def test_tool_continuation_preserves_optima_and_uses_actual_continue_closure():
    client, state = _route_contract(tool_call=True)
    response = _post(client, max_output_tokens=1024)

    assert response.status_code == 200, response.get_json()
    assert response.get_json()["reply"] == REPLY
    assert len(state.payloads) == 2
    for params, payload in zip(state.params, state.payloads):
        assert params["model_profile"] == "optima"
        _assert_balanced_gpt_payload(payload)
        assert payload["max_output_tokens"] == 1024
    assert state.payloads[1]["input"][0]["type"] == "function_call_output"
    assert state.payloads[0]["model"] == state.payloads[1]["model"]


def test_negative_control_old_allowlist_is_detected_by_optima_contract():
    client, state = _route_contract(drop_profile=True)
    response = _post(client)

    assert response.status_code == 200, response.get_json()
    # Reproduce the old route bug: the selector is lost, so Ultra high wins.
    assert "model_profile" not in state.params[0]
    assert state.payloads[0]["reasoning"] == {"effort": "high"}
    with pytest.raises(AssertionError, match="Optima must reach"):
        _assert_balanced_gpt_payload(state.payloads[0])
