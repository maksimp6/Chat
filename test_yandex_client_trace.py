import json
import unittest
from unittest.mock import Mock, patch

from trace_manager import ExecutionTrace
from yandex_client import YandexClientError, YandexResponsesClient


class TestExecutionTraceResponses(unittest.TestCase):
    def test_full_response_is_preserved_and_sanitized(self):
        trace = ExecutionTrace()
        response = {
            "id": "resp-1", "object": "response", "status": "completed",
            "model": "gpt://project/model/latest", "created_at": 1, "completed_at": 2,
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30,
                      "input_tokens_details": {"cached_tokens": 7},
                      "output_token_details": {"reasoning_tokens": 3}},
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}],
            "error": None, "incomplete_details": None, "conversation": {"id": "conv-1"},
            "metadata": {"trace_id": trace.trace_id}, "authorization": "must-not-survive",
        }
        trace.add_response(response, step_index=1, kind="initial_response")
        data = trace.finalize()
        raw = data["responses"][0]["raw"]
        self.assertEqual(raw["id"], "resp-1")
        self.assertEqual(raw["output"][0]["content"][0]["text"], "hello")
        self.assertEqual(raw["usage"]["input_tokens_details"]["cached_tokens"], 7)
        self.assertEqual(raw["conversation"]["id"], "conv-1")
        self.assertEqual(raw["authorization"], "<redacted>")
        json.dumps(data)

    def test_lifecycle_events_are_preserved_but_trace_finalized_is_internal(self):
        trace = ExecutionTrace()
        trace.add_event("api_poll_completed", {"status": "completed"})
        trace.add_event("trace_finalized", {})
        trace.add_event("api_request_completed", {})
        trace.add_event("api_response_received", {"step": 1})
        data = trace.finalize()
        types = [event["type"] for event in data["events"]]
        self.assertEqual(types, ["api_poll_completed", "api_request_completed", "api_response_received"])
        self.assertNotIn("trace_finalized", types)

    def test_circular_trace_reference_is_not_serialized(self):
        trace = ExecutionTrace()
        trace.trace["request"]["execution_trace"] = trace
        data = trace.finalize()
        self.assertEqual(data["request"]["execution_trace"], {"trace_id": trace.trace_id, "<circular_ref>": True})

    def test_two_ai_steps_aggregate_without_double_counting(self):
        trace = ExecutionTrace()
        trace.add_response({
            "id": "resp-1", "model": "aliceai-llm", "status": "completed",
            "usage": {"input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100},
        }, step_index=1)
        trace.add_response({
            "id": "resp-2", "model": "aliceai-llm", "status": "completed",
            "usage": {"input_tokens": 500, "output_tokens": 50, "total_tokens": 550},
        }, step_index=2)
        first = trace.finalize()
        second = trace.finalize()
        billing = first["billing"]
        self.assertEqual(len(billing["items"]), 2)
        self.assertEqual(billing["input_tokens"], 1500)
        self.assertEqual(billing["output_tokens"], 150)
        self.assertEqual(billing["total_tokens"], 1650)
        self.assertAlmostEqual(billing["total_cost"], sum(i["total_cost"] for i in billing["items"]), places=6)
        self.assertEqual(second["billing"], billing)

    def test_cached_tokens_and_savings_are_accounted_separately(self):
        trace = ExecutionTrace()
        trace.add_response({
            "id": "resp-cache", "model": "aliceai-llm-flash", "status": "completed",
            "usage": {
                "input_tokens": 1000, "output_tokens": 100, "total_tokens": 1100,
                "input_token_details": {"cached_tokens": 800},
            },
        }, step_index=1)
        billing = trace.finalize()["billing"]
        item = billing["items"][0]
        self.assertEqual(item["cached_input_tokens"], 800)
        self.assertGreater(item["cache_savings"], 0)
        self.assertEqual(billing["cached_input_tokens"], 800)

    def test_unknown_pricing_is_explicit_not_zero(self):
        trace = ExecutionTrace()
        trace.add_response({
            "id": "resp-unknown", "model": "not-configured", "status": "completed",
            "usage": {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120},
        }, step_index=1)
        billing = trace.finalize()["billing"]
        self.assertEqual(billing["cost_status"], "partial")
        self.assertEqual(billing["unknown_cost_items"], 1)
        self.assertIsNone(billing["items"][0]["total_cost"])

    def test_billing_survives_json_persistence_roundtrip(self):
        trace = ExecutionTrace()
        trace.set_context(invocation_id="inv-1", session_id="sess-1", conversation_id="conv-1")
        trace.add_response({
            "id": "resp-persist", "model": "aliceai-llm", "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        }, step_index=1)
        persisted = json.loads(json.dumps(trace.finalize()))
        self.assertEqual(persisted["billing"]["invocation_id"], "inv-1")
        self.assertEqual(persisted["billing"]["session_id"], "sess-1")
        self.assertEqual(persisted["billing"]["conversation_id"], "conv-1")
        self.assertEqual(persisted["billing"]["items"][0]["total_tokens"], 15)


class TestYandexResponsesPollingTrace(unittest.TestCase):
    def _client(self):
        client = object.__new__(YandexResponsesClient)
        client.responses_url = "https://example.test/responses"
        client.session = Mock()
        client._log_request = Mock()
        client._log_response = lambda response: response
        return client

    @staticmethod
    def _response(payload, status_code=200):
        response = Mock()
        response.status_code = status_code
        response.url = "https://example.test/responses/resp-1"
        response.json.return_value = payload
        response.raise_for_status.side_effect = None
        return response

    def test_in_progress_to_completed_is_one_logical_response(self):
        client = self._client()
        client.session.get.side_effect = [
            self._response({"id": "resp-1", "status": "in_progress", "output": [], "usage": None}),
            self._response({"id": "resp-1", "status": "in_progress", "output": [], "usage": None}),
            self._response({"id": "resp-1", "status": "completed", "usage": {"total_tokens": 5}, "output": [{"type": "message"}]}),
        ]
        trace = ExecutionTrace()
        result = client._wait("resp-1", timeout=5, execution_trace=trace, trace_step=1)
        data = trace.finalize()
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(data["responses"]), 1)
        self.assertEqual(data["responses"][0]["raw"]["status"], "completed")
        self.assertEqual(data["responses"][0]["raw"]["usage"]["total_tokens"], 5)

    def test_poll_updates_existing_initial_response(self):
        client = self._client()
        trace = ExecutionTrace()
        trace.add_response({"id": "resp-1", "status": "in_progress", "output": []}, step_index=1, kind="initial_response")
        client.session.get.side_effect = [self._response({"id": "resp-1", "status": "completed", "output": [{"type": "message"}]})]
        client._wait("resp-1", timeout=5, execution_trace=trace, trace_step=1)
        data = trace.finalize()
        self.assertEqual(len(data["responses"]), 1)
        self.assertEqual(data["responses"][0]["kind"], "initial_response")
        self.assertEqual(data["responses"][0]["raw"]["status"], "completed")

    def test_duplicate_poll_snapshot_is_not_recorded_twice(self):
        client = self._client()
        same = {"id": "resp-1", "status": "in_progress", "output": [], "usage": None}
        client.session.get.side_effect = [
            self._response(same), self._response(same),
            self._response({"id": "resp-1", "status": "completed", "output": []})
        ]
        trace = ExecutionTrace()
        client._wait("resp-1", timeout=5, execution_trace=trace, trace_step=1)
        responses = trace.finalize()["responses"]
        self.assertEqual(len(responses), 1)

    def test_two_genuine_responses_remain_separate(self):
        trace = ExecutionTrace()
        trace.add_response({"id": "resp-1", "status": "completed"}, step_index=1)
        trace.add_response({"id": "resp-2", "status": "completed"}, step_index=2)
        responses = trace.finalize()["responses"]
        self.assertEqual(len(responses), 2)
        self.assertEqual([item["step"] for item in responses], [1, 2])

    def test_failed_poll_preserves_response_before_exception(self):
        client = self._client()
        failed = {
            "id": "resp-1", "status": "failed",
            "output": [{"type": "message", "content": [{"type": "text", "text": "partial"}]}],
            "error": {"code": "server_error", "message": "boom"},
            "incomplete_details": {"reason": "failure"},
        }
        client.session.get.side_effect = [self._response(failed)]
        trace = ExecutionTrace()
        with self.assertRaises(YandexClientError):
            client._wait("resp-1", timeout=5, execution_trace=trace, trace_step=1)
        raw = trace.finalize()["responses"][0]["raw"]
        self.assertEqual(raw["status"], "failed")
        self.assertEqual(raw["error"]["code"], "server_error")
        self.assertEqual(raw["incomplete_details"]["reason"], "failure")

    def test_cached_token_usage_is_preserved(self):
        trace = ExecutionTrace()
        trace.add_response({
            "id": "resp-cache",
            "status": "completed",
            "usage": {
                "input_tokens": 1019,
                "output_tokens": 7,
                "total_tokens": 1026,
                "input_token_details": {
                    "text_tokens": 27,
                    "cached_tokens": 992,
                },
            },
            "output": [],
        }, step_index=1)
        raw = trace.finalize()["responses"][0]["raw"]
        usage = raw["usage"]
        self.assertEqual(usage["input_tokens"], 1019)
        self.assertEqual(usage["input_token_details"]["cached_tokens"], 992)

    def test_ask_passes_prompt_cache_key(self):
        client = self._client()
        client._config = Mock(PROJECT_ID="project")
        client._resolve_yandex_conv_id = Mock(return_value=None)
        response = self._response({
            "id": "resp-cache-key",
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
            "output": [],
        })
        client.session.post.return_value = response
        params = {"background": False, "prompt_cache_key": "stable-prefix-v1"}
        with patch("yandex_client_modules.request_mixin._resolve_global_provider_credential") as resolve_credential:
            resolve_credential.return_value = Mock(
                api_key="runtime-provider-secret",
                project_id="project",
                trace_key_id="key-1",
            )
            data = client.ask("hello", "test-model", conversation_id="conv-1", params=params)
        self.assertEqual(data["id"], "resp-cache-key")
        sent_payload = client.session.post.call_args.kwargs["json"]
        self.assertEqual(sent_payload["prompt_cache_key"], "stable-prefix-v1")


if __name__ == "__main__":
    unittest.main()
