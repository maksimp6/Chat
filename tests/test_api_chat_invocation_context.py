import unittest
from unittest.mock import patch

from mcp_routes import chat
from app import app
from invocation.context import InvocationContext
from invocation.manager import get_invocation
from trace_manager import get_current_trace


class ApiChatInvocationContextTests(unittest.TestCase):
    def test_chat_creates_and_completes_invocation_with_trace_context(self):
        client = app.test_client()
        fake_response = {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}],
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            "status": "completed",
        }

        def assert_trace_is_bound(*args, **kwargs):
            self.assertIs(kwargs.get("trace"), get_current_trace())
            return fake_response

        with (
            patch("mcp_routes.get_conv_settings", return_value={}),
            patch("mcp_routes.add_message"),
            patch("mcp_routes.AliceClient.ask_with_mcp", side_effect=assert_trace_is_bound),
        ):
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
        self.assertIsNone(get_current_trace())

    def test_chat_failure_persists_failed_invocation_and_trace(self):
        client = app.test_client()

        with (
            patch("mcp_routes.get_conv_settings", return_value={}),
            patch("mcp_routes.add_message"),
            patch("mcp_routes.AliceClient.ask_with_mcp", side_effect=RuntimeError("boom")),
        ):
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

    def test_chat_binds_fallback_trace_after_invocation_creation_failure(self):
        client = app.test_client()
        context = InvocationContext(
            "session-fallback", "conv-fallback", "inv-fallback", "trace-fallback"
        )

        def assert_trace_is_bound(trace):
            self.assertIs(trace, get_current_trace())
            return 0

        with (
            patch(
                "mcp_routes.create_invocation",
                side_effect=[RuntimeError("before trace"), context],
            ),
            patch("mcp_routes.start_invocation"),
            patch("mcp_routes.persist_invocation_trace"),
            patch("mcp_routes.fail_invocation"),
            patch("mcp_routes.add_message"),
            patch("mcp_routes.record_yandex_mcp_activity", side_effect=assert_trace_is_bound),
        ):
            response = client.post(
                "/api/chat",
                json={"conversation_id": "conv-fallback", "message": "hello"},
            )

        self.assertEqual(response.status_code, 500)
        self.assertIsNone(get_current_trace())


if __name__ == "__main__":
    unittest.main()
