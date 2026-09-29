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


def test_default_local_tool_categories_include_3d(monkeypatch):
    import yandex_client_modules.mcp_mixin as mcp_mixin

    categories = []

    monkeypatch.setattr(mcp_mixin.mcp_storage, "list_servers", lambda: [])
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda conversation_id: None)

    def get_tools(category):
        categories.append(category)
        if category == "3d":
            return [
                {
                    "type": "function",
                    "name": "printing3d.finance.assess",
                    "description": "AI-first 3D finance assessment",
                    "parameters": {
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    "strict": True,
                    "defer_loading": False,
                }
            ]
        return []

    monkeypatch.setattr(mcp_mixin.registry, "get_tools_by_category", get_tools)

    client = CapturingClient()
    client.ask_with_mcp("check 3d financing", "aliceai-llm")

    assert "3d" in categories
    assert client.last_params is not None
    names = [
        tool.get("name")
        for tool in client.last_params.get("tools", [])
        if tool.get("type") == "function"
    ]
    assert "printing3d.finance.assess" in names


def test_explicit_tool_categories_still_override_defaults(monkeypatch):
    import yandex_client_modules.mcp_mixin as mcp_mixin

    categories = []

    monkeypatch.setattr(mcp_mixin.mcp_storage, "list_servers", lambda: [])
    monkeypatch.setattr(mcp_mixin, "get_conv_settings", lambda conversation_id: None)
    monkeypatch.setattr(
        mcp_mixin.registry,
        "get_tools_by_category",
        lambda category: categories.append(category) or [],
    )

    client = CapturingClient()
    client.ask_with_mcp(
        "git only",
        "aliceai-llm",
        params={"active_tool_categories": ["git"]},
    )

    assert categories == ["git"]
