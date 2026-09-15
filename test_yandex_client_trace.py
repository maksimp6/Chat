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

    def test_internal_lifecycle_events_are_not_user_events(self):
        trace = ExecutionTrace()
        trace.add_event("api_poll_completed", {"status": "completed"})
        trace.add_event("trace_finalized", {})
        trace.add_event("api_request_completed", {})
        trace.add_event("api_response_received", {"step": 1})
        data = trace.finalize()
        types = [event["type"] for event in data["events"]]
        self.assertEqual(types, ["api_response_received"])

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


if __name__ == "__main__":
    unittest.main()
