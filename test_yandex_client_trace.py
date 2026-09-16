import json
import unittest
from unittest.mock import Mock

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
                      "output_tokens_details": {"reasoning_tokens": 3}},
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


class TestExecutionTraceEndToEnd(unittest.TestCase):
    def test_multi_step_responses_polling_and_tool_execution_are_single_trace(self):
        trace = ExecutionTrace()
        trace.set_request({
            "conversation_id": "conv-e2e",
            "message": "run the tool and continue",
            "model": "aliceai-llm",
            "params": {},
        })

        init_event = next(e for e in trace.trace["events"] if e["type"] == "request_initialized")
        api1_start = init_event["timestamp"] + 0.250
        trace.add_api_request({"model": "aliceai-llm", "input": "hello"}, step_index=1, start_timestamp=api1_start)
        trace.add_event("api_request_sent", {"step": 1, "timestamp": api1_start})
        trace.add_response(
            {
                "id": "resp-1",
                "status": "completed",
                "output": [{"type": "function_call", "name": "local_echo", "call_id": "call-1", "arguments": {"text": "hello"}}],
            },
            step_index=1,
            start_timestamp=api1_start,
            end_timestamp=api1_start + 0.400,
        )
        trace.add_event("api_request_completed", {"step": 1, "start_timestamp": api1_start, "end_timestamp": api1_start + 0.400})
        trace.add_event("api_poll_completed", {"step": 1})

        tool_result = trace.track_tool_execution(
            "local_echo",
            {"text": "hello"},
            lambda text: text.upper(),
            "hello",
            call_id="call-1",
            step=1,
            server="local",
        )
        self.assertEqual(tool_result, "HELLO")

        api2_start = api1_start + 0.550
        trace.add_api_request({"model": "aliceai-llm", "tool_result": tool_result}, step_index=2, start_timestamp=api2_start)
        trace.add_event("api_request_sent", {"step": 2, "timestamp": api2_start})
        trace.add_response(
            {
                "id": "resp-2",
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "HELLO"}]}],
            },
            step_index=2,
            start_timestamp=api2_start,
            end_timestamp=api2_start + 0.300,
        )
        trace.add_event("api_request_completed", {"step": 2, "start_timestamp": api2_start, "end_timestamp": api2_start + 0.300})

        data = trace.finalize()
        self.assertEqual(len(data["responses"]), 2)
        self.assertEqual([response["step"] for response in data["responses"]], [1, 2])
        self.assertEqual([response["response_id"] for response in data["responses"]], ["resp-1", "resp-2"])
        self.assertEqual(len(data["tool_calls"]), 1)
        self.assertEqual(data["tool_calls"][0]["call_id"], "call-1")
        self.assertLessEqual(data["responses"][0]["start_timestamp"], data["responses"][0]["end_timestamp"])
        self.assertLessEqual(data["responses"][1]["start_timestamp"], data["responses"][1]["end_timestamp"])
        self.assertLessEqual(data["tool_calls"][0]["start_timestamp"], data["tool_calls"][0]["end_timestamp"])
        event_types = [event["type"] for event in data["events"]]
        for expected in ("request_initialized", "api_request_registered", "api_request_sent", "api_response_received", "api_request_completed", "api_poll_completed", "tool_executed"):
            self.assertIn(expected, event_types)
        self.assertEqual(event_types.count("api_response_received"), 2)
        self.assertNotIn("trace_finalized", event_types)


if __name__ == "__main__":
    unittest.main()
