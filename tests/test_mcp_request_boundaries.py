"""Provider payload shaping and shared MCP settings without an approval gate."""

import pytest

from chat import mcp_routes
from tool_providers.registry import registry
from yandex_client_modules import mcp_mixin
from yandex_client_modules.mcp_mixin import YandexMcpMixin
from yandex_client_modules.request_builder import _clean_mcp_tool
from yandex_client_modules.tool_config import normalize_tools_config


class RecordingClient(YandexMcpMixin):
    def __init__(self):
        self.sent = []

    def ask(self, message, model_key, conversation_id, params, **kwargs):
        self.sent.append(params)
        return {"output": []}


def test_chat_params_keep_supported_settings_and_explicit_local_defaults():
    raw = {"temperature": 0.2, "instructions": "Be brief."}

    assert mcp_routes._client_params(raw) == {
        **raw,
        "max_rounds": None,
        "request_timeout": 90,
        "poll_timeout": 240,
    }
    assert raw == {"temperature": 0.2, "instructions": "Be brief."}


@pytest.mark.parametrize(
    "key", ["tools", "input", "prompt", "tools_config", "active_tool_categories"]
)
def test_chat_params_reject_server_owned_fields_instead_of_discarding_them(key):
    with pytest.raises(ValueError, match=f"unsupported params: {key}"):
        mcp_routes._client_params({key: []})


def test_chat_params_reject_previous_response_id_for_conversation_endpoint():
    with pytest.raises(ValueError, match="previous_response_id cannot be combined"):
        mcp_routes._client_params({"previous_response_id": "previous-response"})


def test_chat_params_preserve_explicit_round_limit_and_transport_timeouts():
    params = mcp_routes._client_params(
        {"max_rounds": 100, "request_timeout": 450, "poll_timeout": 900}
    )
    assert params["max_rounds"] == 100
    assert params["request_timeout"] == 450
    assert params["poll_timeout"] == 900


@pytest.mark.parametrize(
    "key,value",
    [
        ("temperature", "0.5"),
        ("top_p", True),
        ("max_output_tokens", True),
        ("max_tool_calls", False),
        ("parallel_tool_calls", "false"),
        ("max_rounds", "100"),
        ("request_timeout", "90"),
        ("poll_timeout", None),
        ("instructions", {}),
        ("prompt_cache_key", []),
        ("reasoning_effort", ["high"]),
        ("reasoning", "high"),
        ("text", "json"),
        ("truncation", []),
        ("service_tier", {}),
        ("tool_choice", {"bogus": 1}),
        ("tool_choice", {"type": "function"}),
    ],
)
def test_chat_params_reject_invalid_types_before_provider_work(key, value):
    with pytest.raises(ValueError, match=key):
        mcp_routes._client_params({key: value})


@pytest.mark.parametrize("key", ["request_timeout", "poll_timeout"])
def test_chat_params_reject_huge_integer_timeout_as_value_error(key):
    with pytest.raises(ValueError, match=key):
        mcp_routes._client_params({key: 10**400})


@pytest.mark.parametrize(
    "params",
    [
        {"stream": True},
        {"background": True, "store": False},
        {"temperature": float("nan")},
        {"top_p": float("inf")},
    ],
)
def test_chat_params_reject_unsupported_transport_combinations_and_non_finite_numbers(params):
    with pytest.raises(ValueError):
        mcp_routes._client_params(params)


@pytest.mark.parametrize("arguments", ["{", "", "[]", "null", None, [], False])
def test_invalid_tool_arguments_never_construct_an_executor(monkeypatch, arguments):
    constructed = []

    def unexpected_executor(_registry):
        constructed.append(True)
        raise AssertionError("Malformed arguments reached the executor")

    monkeypatch.setattr(mcp_mixin, "UniversalToolExecutor", unexpected_executor)
    result = RecordingClient()._execute_single_tool(
        {"name": "get_system_info", "arguments": arguments}, []
    )

    assert constructed == []
    assert result["result"] is None
    assert result["error"].startswith("Invalid tool arguments:")
    assert result["timing"]["success"] is False


def test_valid_empty_json_object_reaches_executor_without_argument_changes(monkeypatch):
    received = []

    class RecordingExecutor:
        def __init__(self, _registry):
            pass

        def execute(self, call, **kwargs):
            received.append(call.arguments)
            return {"success": True, "data": {"checked": True}}

    monkeypatch.setattr(mcp_mixin, "UniversalToolExecutor", RecordingExecutor)
    result = RecordingClient()._execute_single_tool(
        {"name": "get_system_info", "arguments": "{}"}, []
    )

    assert received == [{}]
    assert result["error"] is None
    assert result["result"] == {"checked": True}
    assert result["timing"]["success"] is True


def test_hosted_false_strings_are_normalized_and_do_not_offer_tools(monkeypatch):
    raw = {
        "web_search": {"enabled": "false"},
        "file_search": {"enabled": " false ", "vector_store_ids": "vs_1"},
        "code_interpreter": {"enabled": "FALSE"},
    }
    normalized = normalize_tools_config(raw)
    assert all(config["enabled"] is False for config in normalized.values())
    assert raw["code_interpreter"]["enabled"] == "FALSE"

    monkeypatch.setattr(mcp_mixin, "get_current_owner_id", lambda: "local")
    monkeypatch.setattr(
        mcp_mixin,
        "get_conv_settings",
        lambda _: {"active_tool_categories": [], "tools_config": raw},
    )
    monkeypatch.setattr(
        mcp_mixin.mcp_storage, "get_enabled_servers_for_conv", lambda *args, **kwargs: []
    )
    client = RecordingClient()
    client.ask_with_mcp("hi", "model", conversation_id="conv-1")
    assert client.sent[0].get("tools", []) == []


def test_hosted_domains_and_vector_store_ids_accept_strings_lists_and_newlines():
    normalized = normalize_tools_config(
        {
            "web_search": {
                "allowed_domains": "a.example\nb.example\\nc.example,d.example",
                "blocked_domains": ["bad.example\nother.example", "bad.example"],
            },
            "file_search": {
                "vector_store_ids": ["vs_1\nvs_2", "vs_3\\nvs_4,vs_1"],
                "max_results": 100,
            },
        }
    )
    assert normalized["web_search"]["allowed_domains"] == [
        "a.example", "b.example", "c.example", "d.example"
    ]
    assert normalized["web_search"]["blocked_domains"] == ["bad.example", "other.example"]
    assert normalized["file_search"]["vector_store_ids"] == ["vs_1", "vs_2", "vs_3", "vs_4"]
    assert normalized["file_search"]["max_results"] == 50
    assert mcp_mixin._max_results(None) == 20
    assert mcp_mixin._max_results(0) == 1


@pytest.mark.parametrize(
    "config",
    [
        [],
        {"file_search": "invalid"},
        {"code_interpreter": {"enabled": "no"}},
        {"web_search": {"allowed_domains": ["good.example", 7]}},
        {"file_search": {"vector_store_ids": 7}},
        {"file_search": {"max_results": True}},
    ],
)
def test_malformed_hosted_config_is_rejected_before_client_ask(monkeypatch, config):
    with pytest.raises(ValueError, match="tools_config"):
        normalize_tools_config(config)
    monkeypatch.setattr(mcp_mixin, "get_current_owner_id", lambda: "local")
    monkeypatch.setattr(
        mcp_mixin,
        "get_conv_settings",
        lambda _: {"active_tool_categories": [], "tools_config": config},
    )
    monkeypatch.setattr(
        mcp_mixin.mcp_storage, "get_enabled_servers_for_conv", lambda *args, **kwargs: []
    )
    client = RecordingClient()
    with pytest.raises(ValueError, match="tools_config"):
        client.ask_with_mcp("hi", "model", conversation_id="conv-1")
    assert client.sent == []


def test_tool_categories_must_be_registered_names():
    known = sorted(registry.get_available_categories())[0]
    assert mcp_routes._tool_category_selection([known, known]) == [known]
    assert mcp_routes._tool_category_selection(["no-such-category"]) is None
    assert mcp_routes._tool_category_selection(known) is None
    assert mcp_routes._tool_category_selection([1]) is None




@pytest.mark.parametrize("saved_policy", [None, "never", "always", {"always": {"tool_names": ["write"]}}])
def test_mcp_tools_go_only_to_https_and_request_no_approval(saved_policy):
    assert _clean_mcp_tool({"type": "mcp", "server_url": "http://example.test/mcp"}) is None
    cleaned = _clean_mcp_tool(
        {
            "type": "mcp",
            "server_url": "https://example.test/mcp",
            "server_label": "demo",
            "require_approval": saved_policy,
            "headers": {"X-Token": "secret"},
        }
    )
    assert cleaned == {
        "type": "mcp",
        "server_label": "demo",
        "server_url": "https://example.test/mcp",
        "require_approval": "never",
    }


def test_provider_tool_list_is_built_by_the_server(monkeypatch):
    monkeypatch.setattr(mcp_mixin, "get_current_owner_id", lambda: "local")
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda _: {"active_tool_categories": []})
    client = RecordingClient()

    client.ask_with_mcp(
        "hi",
        "model",
        conversation_id="conv-1",
        params={"tools": [{"type": "code_interpreter"}]},
    )

    assert client.sent[0]["tools"] == [{"type": "web_search", "search_context_size": "medium"}]


def test_enabled_servers_use_shared_identity_without_conversation_owner_metadata(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_mixin, "get_current_owner_id", lambda: "local")
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda _: {"active_tool_categories": []})

    def enabled(conversation_id, *, owner_id):
        seen.update(conversation=conversation_id, owner=owner_id)
        return [{"name": "Remote", "server_url": "https://mcp.example.test", "require_approval": "always"}]

    monkeypatch.setattr(mcp_mixin.mcp_storage, "get_enabled_servers_for_conv", enabled)
    client = RecordingClient()
    client.ask_with_mcp("hi", "model", conversation_id="conv-1")

    assert seen == {"conversation": "conv-1", "owner": "local"}
    remote = next(tool for tool in client.sent[0]["tools"] if tool["type"] == "mcp")
    assert remote["require_approval"] == "never"




def test_a_turn_without_a_fully_known_price_records_no_cost():
    unknown = {"cost_status": "unknown", "total_cost": None, "unknown_cost_items": 1}
    assert mcp_routes._turn_cost(unknown) is None
    calculated = {"cost_status": "calculated", "total_cost": 0.25, "unknown_cost_items": 0}
    assert mcp_routes._turn_cost(calculated) == 0.25
    partial = {"cost_status": "partial", "total_cost": 0.1, "unknown_cost_items": 1}
    assert mcp_routes._turn_cost(partial) is None


def test_the_follow_up_answer_after_an_approval_is_posted_to_the_treasury(monkeypatch):
    from types import SimpleNamespace

    from flask import Flask

    settled = []

    class FakeClient:
        def __init__(self, config):
            pass

        def ask(self, prompt, model_key, conv_id, extra):
            return {"text": prompt}

        def extract_text(self, response):
            return "Готово"

        def extract_usage(self, response):
            return {"input_tokens": 1000, "output_tokens": 1000}

    monkeypatch.setattr(mcp_routes, "LogosClient", FakeClient)
    monkeypatch.setattr(
        mcp_routes, "registry", SimpleNamespace(get_tool_meta=lambda name: {"name": name})
    )
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(mcp_routes, "check_access", lambda conv_id, owner_id: True)
    monkeypatch.setattr(
        mcp_routes,
        "UniversalToolExecutor",
        lambda registry: SimpleNamespace(execute=lambda call: {"success": True, "data": {}}),
    )
    monkeypatch.setattr(mcp_routes, "add_message", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        mcp_routes,
        "settle_billing_to_treasury",
        lambda billing, owner_id: settled.append((owner_id, billing)) or {"status": "posted"},
    )

    app = Flask(__name__)
    app.register_blueprint(mcp_routes.mcp_bp)
    response = app.test_client().post(
        "/api/mcp/execute-approved",
        json={
            "conversation_id": "conv-1",
            "name": "inspect",
            "arguments": {},
            "model": "yandexgpt-5-lite",
        },
    )

    assert response.status_code == 200
    assert len(settled) == 1
    owner_id, billing = settled[0]
    assert owner_id == "owner-1"
    assert billing["cost_status"] == "calculated"
    assert billing["trace_id"].startswith("approval-")
    assert billing["total_cost"] > 0
    assert response.get_json()["cost"] == billing["total_cost"]

