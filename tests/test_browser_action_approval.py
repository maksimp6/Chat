from unittest.mock import patch

from app import app
from mcp_routes import AliceClient, _tool_call_needs_approval, _tool_call_parts
from yandex_client import YandexResponsesClient


BROWSER_META = {
    "description": "Browser",
    "requires_approval": False,
    "metadata": {"approval_actions": ["click", "fill"]},
}


def _call(action):
    return {
        "type": "function_call",
        "name": "browser_local",
        "arguments": (
            '{"action":"' + action + '","target":"page","value":null}'
        ),
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


def test_chat_tool_call_helpers_apply_action_level_approval():
    with patch("mcp_routes.registry.get_tool_meta", return_value=BROWSER_META):
        name, arguments = _tool_call_parts(_call("inspect"))

        assert name == "browser_local"
        assert arguments == {"action": "inspect", "target": "page", "value": None}
        assert _tool_call_needs_approval(_call("inspect")) is False
        assert _tool_call_needs_approval(_call("navigate")) is False
        assert _tool_call_needs_approval(_call("click")) is True
        assert _tool_call_needs_approval(_call("fill")) is True


def test_alice_client_auto_loop_allows_browser_read_but_stops_click():
    client = object.__new__(AliceClient)

    with (
        patch.object(YandexResponsesClient, "ask_with_mcp", return_value={"output": []}),
        patch("mcp_routes.registry.get_tool_meta", return_value=BROWSER_META),
        patch("mcp_routes.mcp_storage.list_servers", return_value=[]),
        patch("mcp_routes.run_tool_loop", return_value={"looped": True}) as loop,
        patch("mcp_routes.extract_function_calls", return_value=[_call("inspect")]),
    ):
        result = client.ask_with_mcp("inspect", "aliceai-llm")

    assert result == {"looped": True}
    loop.assert_called_once()

    with (
        patch.object(YandexResponsesClient, "ask_with_mcp", return_value={"output": ["original"]}),
        patch("mcp_routes.registry.get_tool_meta", return_value=BROWSER_META),
        patch("mcp_routes.mcp_storage.list_servers", return_value=[]),
        patch("mcp_routes.run_tool_loop") as loop,
        patch("mcp_routes.extract_function_calls", return_value=[_call("click")]),
    ):
        result = client.ask_with_mcp("click", "aliceai-llm")

    assert result == {"output": ["original"]}
    loop.assert_not_called()


def test_api_chat_returns_approval_only_for_interactive_browser_action():
    client = app.test_client()

    with (
        patch("mcp_routes.get_conv_settings", return_value={}),
        patch("mcp_routes.add_message"),
        patch("mcp_routes.registry.get_tool_meta", return_value=BROWSER_META),
        patch("mcp_routes.AliceClient.ask_with_mcp", return_value=_response("inspect")),
    ):
        inspect_response = client.post(
            "/api/chat",
            json={"conversation_id": "browser-read", "message": "inspect page"},
        )

    assert inspect_response.status_code == 200
    inspect_payload = inspect_response.get_json()
    assert inspect_payload["reply"] == "done"
    assert inspect_payload.get("requires_approval") is not True

    with (
        patch("mcp_routes.get_conv_settings", return_value={}),
        patch("mcp_routes.add_message"),
        patch("mcp_routes.registry.get_tool_meta", return_value=BROWSER_META),
        patch("mcp_routes.AliceClient.ask_with_mcp", return_value=_response("click")),
    ):
        click_response = client.post(
            "/api/chat",
            json={"conversation_id": "browser-click", "message": "click button"},
        )

    assert click_response.status_code == 200
    click_payload = click_response.get_json()
    assert click_payload["requires_approval"] is True
    assert click_payload["tool_call"]["name"] == "browser_local"
    assert click_payload["tool_call"]["arguments"]["action"] == "click"
