from unittest.mock import patch

from chat.mcp_routes import LogosClient, _tool_call_parts
from yandex_client_modules.client import YandexResponsesClient


BROWSER_META = {
    "description": "Browser",
    "requires_approval": False,
    "metadata": {"approval_actions": ["click", "fill"]},
}


def _call(action):
    return {
        "type": "function_call",
        "name": "browser_local",
        "arguments": ('{"action":"' + action + '","target":"page","value":null}'),
        "call_id": "browser-call",
    }


def _response(action):
    return {
        "output": [
            _call(action),
            {
                "type": "message",
                "content": [{"type": "output_text", "text": "done"}],
            },
        ],
        "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        "status": "completed",
    }




def test_logos_client_auto_loop_allows_browser_reads_and_clicks():
    client = object.__new__(LogosClient)

    with (
        patch.object(YandexResponsesClient, "ask_with_mcp", return_value={"output": []}),
        patch("chat.mcp_routes.registry.get_tool_meta", return_value=BROWSER_META),
        patch("chat.mcp_routes.mcp_storage.list_servers", return_value=[]),
        patch("chat.mcp_routes.run_tool_loop", return_value={"looped": True}) as loop,
        patch("chat.mcp_routes.extract_function_calls", return_value=[_call("inspect")]),
    ):
        result = client.ask_with_mcp("inspect", "logos-runtime")

    assert result == {"looped": True}
    loop.assert_called_once()

    with (
        patch.object(YandexResponsesClient, "ask_with_mcp", return_value={"output": ["original"]}),
        patch("chat.mcp_routes.registry.get_tool_meta", return_value=BROWSER_META),
        patch("chat.mcp_routes.mcp_storage.list_servers", return_value=[]),
        patch("chat.mcp_routes.run_tool_loop", return_value={"looped": True}) as loop,
        patch("chat.mcp_routes.extract_function_calls", return_value=[_call("click")]),
    ):
        result = client.ask_with_mcp("click", "logos-runtime")

    assert result == {"looped": True}
    loop.assert_called_once()




def test_tool_call_parts_handles_decorated_and_malformed_arguments():
    name, arguments = _tool_call_parts(
        {
            "name": "browser_cloud<|suffix",
            "arguments": "{not-json",
        }
    )
    assert name == "browser_cloud"
    assert arguments == {}

    name, arguments = _tool_call_parts(
        {
            "name": "browser_cloud",
            "arguments": '["not", "an", "object"]',
        }
    )
    assert name == "browser_cloud"
    assert arguments == {}
