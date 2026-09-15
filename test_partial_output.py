"""Tests for partial output extraction and error handling in ExecutionTrace."""
import unittest
from unittest.mock import patch
from partial_output import extract_last_response_text, format_partial_output_message
from trace_manager import ExecutionTrace


class TestPartialOutputExtraction(unittest.TestCase):
    def test_empty_responses_list(self):
        text, response = extract_last_response_text([])
        self.assertEqual(text, "")
        self.assertIsNone(response)

    def test_no_text_in_responses(self):
        responses = [{"raw": {"output": [{"type": "function_call", "name": "tool", "arguments": {}}]}}]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "")
        self.assertIsNone(response)

    def test_single_text_output(self):
        responses = [{"raw": {"output": [{"type": "message", "content": [{"type": "text", "text": "Hello world"}]}]}}]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Hello world")
        self.assertIsNotNone(response)

    def test_output_text_type(self):
        responses = [{"raw": {"output": [{"type": "message", "content": [{"type": "output_text", "text": "Output text"}]}]}}]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Output text")
        self.assertIsNotNone(response)

    def test_skip_reasoning_text(self):
        responses = [{"raw": {"output": [{"type": "message", "content": [
            {"type": "reasoning_text", "text": "Internal reasoning"},
            {"type": "output_text", "text": "Final answer"}
        ]}]}}]
        text, _ = extract_last_response_text(responses)
        self.assertEqual(text, "Final answer")

    def test_multiple_responses_get_last(self):
        responses = [
            {"raw": {"output": [{"type": "message", "content": [{"type": "text", "text": "First response"}]}]}},
            {"raw": {"output": [{"type": "message", "content": [{"type": "text", "text": "Second response"}]}]}}
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "Second response")
        self.assertIs(response, responses[1])

    def test_multiple_responses_skip_empty(self):
        responses = [
            {"raw": {"output": [{"type": "message", "content": [{"type": "text", "text": "First response"}]}]}},
            {"raw": {"output": [{"type": "function_call", "name": "tool"}]}}
        ]
        text, response = extract_last_response_text(responses)
        self.assertEqual(text, "First response")
        self.assertIs(response, responses[0])

    def test_concatenate_multiple_text_parts(self):
        responses = [{"raw": {"output": [{"type": "message", "content": [
            {"type": "text", "text": "Hello "}, {"type": "text", "text": "world"}
        ]}]}}]
        text, _ = extract_last_response_text(responses)
        self.assertEqual(text, "Hello world")

    def test_fallback_to_top_level_text(self):
        responses = [{"raw": {"text": "Top level text", "output": []}}]
        text, _ = extract_last_response_text(responses)
        self.assertEqual(text, "Top level text")

    def test_fallback_to_top_level_output_text(self):
        responses = [{"raw": {"output_text": "Top level output text", "output": []}}]
        text, _ = extract_last_response_text(responses)
        self.assertEqual(text, "Top level output text")

    def test_malformed_response_skipped(self):
        responses = [
            {"raw": "not a dict"},
            {"raw": {"output": [{"type": "message", "content": [{"type": "text", "text": "Valid response"}]}]}}
        ]
        text, _ = extract_last_response_text(responses)
        self.assertEqual(text, "Valid response")


class TestFormatPartialOutputMessage(unittest.TestCase):
    def test_no_partial_output(self):
        self.assertEqual(format_partial_output_message("", "Connection timeout"), "⚠️ Ошибка: Connection timeout")

    def test_with_partial_output(self):
        result = format_partial_output_message("Generated text", "Process error")
        self.assertTrue(result.startswith("Generated text"))
        self.assertIn("⚠️ Ошибка: Process error", result)

    def test_newline_separation(self):
        self.assertEqual(format_partial_output_message("Output", "Error"), "Output\n\n⚠️ Ошибка: Error")


class TestExecutionTraceErrorHandling(unittest.TestCase):
    def test_error_recording(self):
        trace = ExecutionTrace()
        try:
            raise ValueError("Test error")
        except ValueError as e:
            trace.record_error("test_source", str(e), exception=e)
        trace_dict = trace.finalize()
        error_entry = trace_dict["errors"][0]
        self.assertEqual(error_entry["source"], "test_source")
        self.assertEqual(error_entry["error"], "Test error")
        self.assertIn("python_exception", error_entry)

    def test_error_with_response(self):
        trace = ExecutionTrace()
        response_data = {"output": [{"type": "message", "content": [{"type": "text", "text": "Partial output"}]}]}
        trace.add_response(response_data, step_index=1)
        trace.record_error("pipeline", "Error after response")
        trace_dict = trace.finalize()
        self.assertEqual(extract_last_response_text(trace_dict["responses"])[0], "Partial output")
        self.assertTrue(trace_dict["errors"])

    def test_trace_finalize_called_on_error(self):
        trace = ExecutionTrace()
        trace.set_request({"test": "request"})
        trace.record_error("test", "error")
        trace_dict = trace.finalize()
        self.assertIn("trace_id", trace_dict)
        self.assertIn("created_at", trace_dict)
        self.assertIn("errors", trace_dict)
        self.assertGreater(trace_dict["timings"]["total_duration_ms"], 0)

    def test_secret_redaction_in_error(self):
        trace = ExecutionTrace()
        try:
            api_key = "secret_key_123"
            password = "super_secret_password"
            raise ValueError("Error occurred")
        except ValueError as e:
            trace.record_error("test", str(e), exception=e)
        serialized = str(trace.finalize()["errors"][0]["python_exception"])
        self.assertNotIn("secret_key_123", serialized)
        self.assertNotIn("super_secret_password", serialized)


class TestPartialOutputIntegration(unittest.TestCase):
    def test_scenario_a_error_before_response(self):
        trace = ExecutionTrace()
        trace.set_request({"message": "test"})
        error_msg = "Connection failed"
        trace.record_error("api", error_msg)
        trace_dict = trace.finalize()
        text, _ = extract_last_response_text(trace_dict["responses"])
        self.assertEqual(format_partial_output_message(text, error_msg), f"⚠️ Ошибка: {error_msg}")

    def test_scenario_b_response_then_error(self):
        trace = ExecutionTrace()
        trace.set_request({"message": "test"})
        trace.add_response({"output": [{"type": "message", "content": [{"type": "text", "text": "Model output"}]}]}, step_index=1)
        error_msg = "Post-processing failed"
        trace.record_error("pipeline", error_msg)
        trace_dict = trace.finalize()
        text, _ = extract_last_response_text(trace_dict["responses"])
        self.assertEqual(text, "Model output")
        self.assertIn(error_msg, format_partial_output_message(text, error_msg))

    def test_scenario_c_multiple_responses(self):
        trace = ExecutionTrace()
        trace.add_response({"output": [{"type": "message", "content": [{"type": "text", "text": "First step"}]}]}, step_index=1)
        trace.add_response({"output": [{"type": "message", "content": [{"type": "text", "text": "Second step"}]}]}, step_index=2)
        trace.record_error("pipeline", "Error at step 3")
        self.assertEqual(extract_last_response_text(trace.finalize()["responses"])[0], "Second step")

    def test_scenario_d_tool_call_error(self):
        trace = ExecutionTrace()
        trace.add_response({"output": [{"type": "message", "content": [{"type": "text", "text": "Starting..."}]}]}, step_index=1)

        def failing_tool():
            raise RuntimeError("Tool failed")

        with self.assertRaises(RuntimeError):
            trace.track_tool_execution("test_tool", {}, failing_tool)
        trace_dict = trace.finalize()
        self.assertEqual(trace_dict["tool_calls"][0]["name"], "test_tool")
        self.assertIsNotNone(trace_dict["tool_calls"][0]["error"])
        self.assertEqual(extract_last_response_text(trace_dict["responses"])[0], "Starting...")


class TestActiveChatRoute(unittest.TestCase):
    """Exercise the actual registered MCP /api/chat route."""

    def test_partial_output_is_returned_and_persisted_when_pipeline_fails(self):
        from app import app
        import mcp_routes

        captured = {}
        test_conversation_metadata = {"test_request": "true"}

        class FakeClient:
            def ask_with_mcp(self, message, model_key, conversation_id, params):
                self.params = params
                self.metadata = params.get("conversation_metadata")
                params["execution_trace"].add_response({
                    "output": [{"type": "message", "content": [{"type": "text", "text": "Generated before failure"}]}]
                }, step_index=1)
                return {"output": []}

            @staticmethod
            def extract_reasoning_and_text(_response):
                raise RuntimeError("Failure after model response")

        fake_client = FakeClient()

        def fake_add_message(conv_id, role, content, **kwargs):
            captured.update(conv_id=conv_id, role=role, content=content, trace=kwargs.get("trace"))

        with patch.object(mcp_routes, "AliceClient", lambda _config: fake_client), \
             patch.object(mcp_routes, "get_conv_settings", return_value={}), \
             patch.object(mcp_routes, "add_message", side_effect=fake_add_message):
            app.config["TESTING"] = True
            with app.test_client() as client:
                response = client.post("/api/chat", json={
                    "conversation_id": "test-conv",
                    "message": "trigger failure",
                    "model": "aliceai-llm",
                    "params": {"conversation_metadata": test_conversation_metadata}
                })

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertEqual(fake_client.metadata, test_conversation_metadata)
        self.assertEqual(payload["partial_output"], "Generated before failure")
        self.assertIn("Generated before failure", payload["reply"])
        self.assertIn("Failure after model response", payload["reply"])
        self.assertTrue(payload["trace"]["responses"])
        self.assertTrue(payload["trace"]["errors"])
        self.assertEqual(captured["content"], payload["reply"])
        self.assertEqual(captured["trace"]["trace_id"], payload["trace"]["trace_id"])


if __name__ == "__main__":
    unittest.main()
