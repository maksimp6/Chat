from yandex_client_modules.mcp_mixin import YandexMcpMixin


class CapturingClient(YandexMcpMixin):
    def __init__(self):
        self.last_params = None

    def ask(
        self,
        message,
        model_key,
        conversation_id=None,
        params=None,
        execution_trace=None,
    ):
        self.last_params = dict(params or {})
        return {"output": []}


def _prepare(monkeypatch, conversation_settings=None):
    import yandex_client_modules.mcp_mixin as mcp_mixin

    monkeypatch.setattr(mcp_mixin.mcp_storage, "list_servers", lambda: [])
    monkeypatch.setattr(
        mcp_mixin,
        "get_conv_settings",
        lambda conversation_id: conversation_settings,
    )
    monkeypatch.setattr(mcp_mixin.registry, "get_tools_by_category", lambda category: [])


def _web_tools(client):
    return [
        tool
        for tool in (client.last_params or {}).get("tools", [])
        if tool.get("type") == "web_search"
    ]


def test_web_search_is_available_when_not_explicitly_configured(monkeypatch):
    _prepare(monkeypatch)

    client = CapturingClient()
    client.ask_with_mcp(
        "Find current 3D printer prices",
        "aliceai-llm",
        conversation_id="conv-1",
    )

    assert _web_tools(client) == [
        {
            "type": "web_search",
            "search_context_size": "medium",
        }
    ]


def test_explicit_web_search_disable_is_respected(monkeypatch):
    _prepare(
        monkeypatch,
        {
            "tools_config": {
                "web_search": {
                    "enabled": False,
                    "context_size": "medium",
                }
            }
        },
    )

    client = CapturingClient()
    client.ask_with_mcp(
        "Do not search the web",
        "aliceai-llm",
        conversation_id="conv-2",
    )

    assert _web_tools(client) == []


def test_explicit_web_search_config_keeps_context_and_domain_filters(monkeypatch):
    _prepare(
        monkeypatch,
        {
            "tools_config": {
                "web_search": {
                    "enabled": True,
                    "context_size": "high",
                    "allowed_domains": "example.com, shop.example",
                    "blocked_domains": "ads.example",
                }
            }
        },
    )

    client = CapturingClient()
    client.ask_with_mcp(
        "Research current offers",
        "aliceai-llm",
        conversation_id="conv-3",
    )

    assert _web_tools(client) == [
        {
            "type": "web_search",
            "search_context_size": "high",
            "filters": {
                "allowed_domains": ["example.com", "shop.example"],
                "blocked_domains": ["ads.example"],
            },
        }
    ]


def test_param_level_explicit_disable_is_respected_without_saved_settings(monkeypatch):
    _prepare(monkeypatch)

    client = CapturingClient()
    client.ask_with_mcp(
        "No web",
        "aliceai-llm",
        params={"tools_config": {"web_search": {"enabled": False}}},
    )

    assert _web_tools(client) == []
