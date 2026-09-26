"""Regression tests for the pre-Responses-API timing gap in /api/chat."""

import unittest
from unittest.mock import patch

import db
import mcp_routes
from trace_manager import ExecutionTrace


class TestPreApiTiming(unittest.TestCase):
    def test_pre_api_pipeline_is_recorded_from_request_initialization_to_api_send(self):
        trace = ExecutionTrace()
        trace.set_request(
            {"conversation_id": "conv", "message": "hello", "model": "aliceai-llm", "params": {}}
        )

        request_initialized = next(
            event for event in trace.trace["events"] if event["type"] == "request_initialized"
        )
        init_ts = request_initialized["timestamp"]
        api_start = init_ts + 0.450
        trace.add_api_request({}, step_index=1, start_timestamp=api_start)

        api_requests = trace.trace.get("api_requests", [])
        first_api_start = api_requests[0].get("timestamp")
        request_init_timestamp = request_initialized.get("timestamp")
        pre_api_ms = round(max(0.0, first_api_start - request_init_timestamp) * 1000, 2)
        trace.trace.setdefault("timings", {})["pre_api_pipeline"] = {
            "start_timestamp": request_init_timestamp,
            "end_timestamp": first_api_start,
            "duration_ms": pre_api_ms,
        }
        trace.trace.setdefault("events", []).append(
            {
                "type": "pre_api_pipeline_completed",
                "timestamp": first_api_start,
                "payload": {
                    "start_timestamp": request_init_timestamp,
                    "end_timestamp": first_api_start,
                    "timing_ms": pre_api_ms,
                    "step": api_requests[0].get("step", 1),
                },
            }
        )

        finalized = trace.finalize()
        timing = finalized["timings"]["pre_api_pipeline"]
        self.assertAlmostEqual(timing["duration_ms"], 450.0, places=1)
        marker = next(e for e in finalized["events"] if e["type"] == "pre_api_pipeline_completed")
        self.assertEqual(marker["timestamp"], api_start)
        self.assertEqual(marker["payload"]["end_timestamp"], api_start)

    def test_chat_response_contains_pre_api_timing(self):
        from app import app

        db.init_db()
        conversation_id = "pre-api-timing-test"

        class FakeClient:
            def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
                self.assert_trace(trace)
                init_ts = next(
                    e["timestamp"]
                    for e in trace.trace["events"]
                    if e["type"] == "request_initialized"
                )
                api_start = init_ts + 0.450
                trace.add_api_request({"model": model_key}, step_index=1, start_timestamp=api_start)
                trace.add_response(
                    {"id": "resp-test", "status": "completed", "output": []},
                    step_index=1,
                    start_timestamp=api_start,
                    end_timestamp=api_start + 0.100,
                    timing_ms=100.0,
                )
                return {"output": [], "usage": {}}

            @staticmethod
            def assert_trace(trace):
                if not isinstance(trace, ExecutionTrace):
                    raise AssertionError("ExecutionTrace must be passed explicitly")

            @staticmethod
            def extract_reasoning_and_text(_response):
                return "", "reply"

            @staticmethod
            def extract_usage(_response):
                return None

        with (
            patch.object(mcp_routes, "AliceClient", lambda _config: FakeClient()),
            patch.object(mcp_routes, "get_conv_settings", return_value={}),
            patch.object(mcp_routes, "add_message"),
            patch.object(
                mcp_routes, "settle_billing_to_treasury", return_value={"status": "not_applicable"}
            ),
            patch.object(mcp_routes, "persist_invocation_trace"),
            patch.object(mcp_routes, "finish_invocation"),
            patch.object(mcp_routes, "get_conversation_title", return_value=None),
        ):
            app.config["TESTING"] = True
            with app.test_client() as client:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": conversation_id,
                        "message": "hello",
                        "model": "aliceai-llm",
                        "params": {},
                    },
                )

        self.assertEqual(response.status_code, 200)
        trace = response.get_json()["trace"]
        self.assertAlmostEqual(trace["timings"]["pre_api_pipeline"]["duration_ms"], 450.0, places=1)
        self.assertTrue(any(e["type"] == "pre_api_pipeline_completed" for e in trace["events"]))

    def test_chat_survives_treasury_settlement_exception_without_leaking_it(self):
        from app import app

        db.init_db()
        internal_marker = "treasury-settlement-internal-marker"

        class FakeClient:
            def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
                trace.add_response(
                    {"id": "resp-test", "status": "completed", "output": []},
                    step_index=1,
                )
                return {"output": [], "usage": {}}

            @staticmethod
            def extract_reasoning_and_text(_response):
                return "", "reply"

            @staticmethod
            def extract_usage(_response):
                return None

        with (
            patch.object(mcp_routes, "AliceClient", lambda _config: FakeClient()),
            patch.object(mcp_routes, "get_conv_settings", return_value={}),
            patch.object(mcp_routes, "add_message"),
            patch.object(
                mcp_routes,
                "settle_billing_to_treasury",
                side_effect=RuntimeError(internal_marker),
            ),
            patch.object(mcp_routes, "persist_invocation_trace"),
            patch.object(mcp_routes, "finish_invocation"),
            patch.object(mcp_routes, "get_conversation_title", return_value=None),
        ):
            app.config["TESTING"] = True
            with app.test_client() as client:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": "settlement-failure-test",
                        "message": "hello",
                        "model": "aliceai-llm",
                        "params": {},
                    },
                )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(
            payload["trace"]["billing"]["settlement"],
            {"status": "failed", "reason": "treasury_settlement_failed"},
        )
        self.assertNotIn(internal_marker, str(payload))

    def test_chat_error_fallback_survives_trace_persistence_failure(self):
        from app import app

        db.init_db()

        class FailingClient:
            def ask_with_mcp(self, message, model_key, conversation_id, params, trace=None):
                raise RuntimeError("pipeline-internal-marker")

        with (
            patch.object(mcp_routes, "AliceClient", lambda _config: FailingClient()),
            patch.object(mcp_routes, "get_conv_settings", return_value={}),
            patch.object(mcp_routes, "add_message"),
            patch.object(
                mcp_routes,
                "persist_invocation_trace",
                side_effect=RuntimeError("trace-persist-internal-marker"),
            ),
        ):
            app.config["TESTING"] = True
            with app.test_client() as client:
                response = client.post(
                    "/api/chat",
                    json={
                        "conversation_id": "trace-persist-failure-test",
                        "message": "hello",
                        "model": "aliceai-llm",
                        "params": {},
                    },
                )

        self.assertEqual(response.status_code, 500)
        payload = response.get_json()
        self.assertEqual(payload["error"], "Внутренняя ошибка обработки запроса")
        self.assertEqual(payload["reply"], "⚠️ Ошибка: Внутренняя ошибка обработки запроса")
        self.assertNotIn("pipeline-internal-marker", str(payload))
        self.assertNotIn("trace-persist-internal-marker", str(payload))

    def test_execute_approved_hides_unexpected_internal_exception(self):
        from app import app

        with patch.object(
            mcp_routes.registry,
            "get_tool_meta",
            side_effect=RuntimeError("tool-approval-internal-marker"),
        ):
            app.config["TESTING"] = True
            with app.test_client() as client:
                response = client.post(
                    "/api/mcp/execute-approved",
                    json={
                        "conversation_id": "conv",
                        "name": "broken-tool",
                        "arguments": {},
                    },
                )

        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.get_json(), {"error": "tool_execution_failed"})
        self.assertNotIn("tool-approval-internal-marker", response.get_data(as_text=True))


if __name__ == "__main__":
    unittest.main()
