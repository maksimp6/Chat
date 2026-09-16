import unittest

from trace_manager import ExecutionTrace


class ExecutionTraceCompatibilityTests(unittest.TestCase):
    def test_public_api_and_serialized_shape_remain_stable(self):
        trace = ExecutionTrace(trace_id="trace-test")
        trace.set_context(invocation_id="inv-1", conversation_id="conv-1")
        trace.set_request({"message": "hello", "execution_trace": trace})
        trace.add_response(
            {"id": "resp-1", "status": "completed", "output": []},
            step_index=1,
        )

        result = trace.finalize()

        self.assertEqual(result["trace_id"], "trace-test")
        self.assertEqual(result["schema_version"], 1)
        self.assertEqual(result["request"], {"message": "hello"})
        self.assertEqual(result["context"]["invocation_id"], "inv-1")
        self.assertEqual(result["context"]["conversation_id"], "conv-1")
        self.assertEqual(result["responses"][0]["response_id"], "resp-1")
        self.assertIn("timings", result)
        self.assertIn("billing", result)

    def test_response_with_same_id_updates_existing_entry(self):
        trace = ExecutionTrace()
        trace.add_response(
            {"id": "resp-1", "status": "in_progress"},
            step_index=1,
            kind="poll_response",
        )
        trace.add_response(
            {"id": "resp-1", "status": "completed", "output": []},
            step_index=1,
            kind="poll_response",
        )

        result = trace.finalize()

        self.assertEqual(len(result["responses"]), 1)
        response = result["responses"][0]
        self.assertEqual(response["response_id"], "resp-1")
        self.assertEqual(response["raw"]["status"], "completed")
        self.assertEqual(len(response["poll_snapshots"]), 2)


if __name__ == "__main__":
    unittest.main()
