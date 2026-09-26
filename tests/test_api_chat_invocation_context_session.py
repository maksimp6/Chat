import unittest
from unittest.mock import patch

from app import app
from invocation_manager import get_invocation


class ApiChatSessionContextTests(unittest.TestCase):
    def test_chat_uses_explicit_session_id(self):
        client = app.test_client()
        fake_response = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "status": "completed",
        }
        with (
            patch("mcp_routes.get_conv_settings", return_value={}),
            patch("mcp_routes.add_message"),
            patch("mcp_routes.AliceClient.ask_with_mcp", return_value=fake_response),
        ):
            response = client.post(
                "/api/chat",
                json={
                    "conversation_id": "conv-session-test",
                    "session_id": "session-test",
                    "message": "hello",
                    "model": "aliceai-llm",
                },
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["session_id"], "session-test")
        invocation = get_invocation(payload["invocation_id"])
        self.assertEqual(invocation["session_id"], "session-test")
        self.assertEqual(invocation["conversation_id"], "conv-session-test")
        self.assertEqual(invocation["trace"]["trace_id"], payload["trace_id"])
        self.assertEqual(invocation["trace"]["context"]["invocation_id"], payload["invocation_id"])

    def test_chat_without_session_id_preserves_legacy_contract(self):
        client = app.test_client()
        fake_response = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "status": "completed",
        }
        with (
            patch("mcp_routes.get_conv_settings", return_value={}),
            patch("mcp_routes.add_message"),
            patch("mcp_routes.AliceClient.ask_with_mcp", return_value=fake_response),
        ):
            response = client.post(
                "/api/chat", json={"conversation_id": "conv-legacy-test", "message": "hello"}
            )
        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["session_id"], "conv-legacy-test")
        invocation = get_invocation(payload["invocation_id"])
        self.assertEqual(invocation["session_id"], "conv-legacy-test")
        self.assertEqual(invocation["trace"]["context"]["conversation_id"], "conv-legacy-test")


if __name__ == "__main__":
    unittest.main()
