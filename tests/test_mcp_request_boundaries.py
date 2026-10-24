"""Caller-supplied request fields never choose the provider's input, tools or approval gate,
and MCP servers are read only for the conversation's owner."""

import pytest

from chat import mcp_routes
from tool_providers.registry import registry
from yandex_client_modules import mcp_mixin
from yandex_client_modules.mcp_mixin import YandexMcpMixin
from yandex_client_modules.request_builder import _clean_mcp_tool


class RecordingClient(YandexMcpMixin):
    def __init__(self):
        self.sent = []

    def ask(self, message, model_key, conversation_id, params, **kwargs):
        self.sent.append(params)
        return {"output": []}


def test_chat_params_keep_only_client_settings():
    raw = {
        "temperature": 0.2,
        "instructions": "Be brief.",
        "tools": [{"type": "code_interpreter"}],
        "input": [{"role": "system", "content": "override"}],
        "prompt": {"id": "server-side-prompt"},
        "tools_config": {"code_interpreter": {"enabled": True}},
        "active_tool_categories": ["git"],
        "previous_response_id": "response-of-another-owner",
    }
    assert mcp_routes._client_params(raw) == {"temperature": 0.2, "instructions": "Be brief."}


def test_tool_categories_must_be_registered_names():
    known = sorted(registry.get_available_categories())[0]
    assert mcp_routes._tool_category_selection([known, known]) == [known]
    assert mcp_routes._tool_category_selection(["no-such-category"]) is None
    assert mcp_routes._tool_category_selection(known) is None
    assert mcp_routes._tool_category_selection([1]) is None


def test_approval_key_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("LOGOS_APPROVAL_SECRET", "k" * 32)
    assert mcp_routes._approval_key() == b"k" * 32
    monkeypatch.setenv("LOGOS_APPROVAL_SECRET", "too-short")
    with pytest.raises(RuntimeError, match="at least 32 bytes"):
        mcp_routes._approval_key()
    monkeypatch.delenv("LOGOS_APPROVAL_SECRET")
    assert len(mcp_routes._approval_key()) == 32


def test_mcp_tools_go_only_to_https_and_never_skip_approval():
    assert _clean_mcp_tool({"type": "mcp", "server_url": "http://example.test/mcp"}) is None
    cleaned = _clean_mcp_tool(
        {
            "type": "mcp",
            "server_url": "https://example.test/mcp",
            "server_label": "demo",
            "require_approval": "never",
            "headers": {"X-Token": "secret"},
        }
    )
    assert cleaned == {
        "type": "mcp",
        "server_label": "demo",
        "server_url": "https://example.test/mcp",
    }


def test_provider_tool_list_is_built_by_the_server(monkeypatch):
    monkeypatch.setattr(mcp_mixin, "get_owner", lambda _: None)
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda _: {"active_tool_categories": []})
    client = RecordingClient()

    client.ask_with_mcp(
        "hi",
        "model",
        conversation_id="conv-1",
        params={"tools": [{"type": "code_interpreter"}]},
    )

    assert client.sent[0]["tools"] == [{"type": "web_search", "search_context_size": "medium"}]


def test_enabled_servers_are_read_for_the_conversation_owner(monkeypatch):
    seen = {}
    monkeypatch.setattr(mcp_mixin, "get_owner", lambda _: "owner-a")
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda _: {"active_tool_categories": []})

    def enabled(conversation_id, *, owner_id):
        seen.update(conversation=conversation_id, owner=owner_id)
        return []

    monkeypatch.setattr(mcp_mixin.mcp_storage, "get_enabled_servers_for_conv", enabled)
    RecordingClient().ask_with_mcp("hi", "model", conversation_id="conv-1")

    assert seen == {"conversation": "conv-1", "owner": "owner-a"}
