import unittest
from unittest.mock import patch

from mcp_routes import chat
from app import app
from invocation_manager import get_invocation


class ApiChatInvocationContextTests(unittest.TestCase):
    def test_chat_creates_and_completes_invocation_with_trace_context(self):
        client = app.test_client()
        fake_response = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "ok"}]}
            ],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "status": "completed",
        }

        with patch("mcp_routes.get_conv_settings", return_value={}), \
             patch("mcp_routes.add_message"), \
             patch("mcp_routes.AliceClient.ask_with_mcp", return_value=fake_response):
            response = client.post(
                "/api/chat",
                json={"conversation_id": "conv-test", "message": "hello", "model": "aliceai-llm"},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertTrue(payload["invocation_id"])
        self.assertEqual(payload["conversation_id"], "conv-test")
        self.assertEqual(payload["trace_id"], payload["trace"]["trace_id"])
        self.assertEqual(payload["trace"]["context"]["invocation_id"], payload["invocation_id"])
        self.assertEqual(payload["trace"]["context"]["conversation_id"], "conv-test")

    def test_chat_failure_persists_failed_invocation_and_trace(self):
        client = app.test_client()

        with patch("mcp_routes.get_conv_settings", return_value={}), \
             patch("mcp_routes.add_message"), \
             patch("mcp_routes.AliceClient.ask_with_mcp", side_effect=RuntimeError("boom")):
            response = client.post(
                "/api/chat",
                json={"conversation_id": "conv-test", "message": "hello"},
            )

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertTrue(payload["invocation_id"])
        self.assertTrue(payload["trace_id"])
        self.assertEqual(payload["trace_id"], payload["trace"]["trace_id"])

        invocation = get_invocation(payload["invocation_id"])
        self.assertIsNotNone(invocation)
        self.assertEqual(invocation["status"], "failed")
        self.assertEqual(invocation["trace_id"], payload["trace_id"])


if __name__ == "__main__":
    unittest.main()
