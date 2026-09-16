import json
import unittest

from invocation_context import InvocationContext
from invocation_trace import create_invocation_trace


class InvocationTraceBindingTests(unittest.TestCase):
    def test_trace_is_owned_by_context(self):
        context = InvocationContext(
            session_id="session-1",
            conversation_id="conversation-1",
            invocation_id="invocation-1",
            trace_id="trace-1",
            metadata={"source": "test"},
        )

        trace = create_invocation_trace(context)
        data = trace.finalize()

        self.assertEqual(data["trace_id"], "trace-1")
        self.assertEqual(
            data["context"],
            {
                "session_id": "session-1",
                "conversation_id": "conversation-1",
                "invocation_id": "invocation-1",
                "trace_id": "trace-1",
            },
        )

    def test_trace_is_independent_between_invocations(self):
        first = InvocationContext("s1", "c1", "i1", "t1")
        second = InvocationContext("s1", "c1", "i2", "t2")

        first_trace = create_invocation_trace(first)
        second_trace = create_invocation_trace(second)
        first_trace.add_event("first_only", {"value": 1})

        self.assertEqual(first_trace.trace["context"]["invocation_id"], "i1")
        self.assertEqual(second_trace.trace["context"]["invocation_id"], "i2")
        self.assertNotIn("first_only", [event["type"] for event in second_trace.trace["events"]])

    def test_trace_is_serializable_without_context_back_reference(self):
        context = InvocationContext("s", "c", "i", "t")
        trace = create_invocation_trace(context)
        encoded = json.dumps(trace.finalize(), ensure_ascii=False)
        self.assertIn('"trace_id": "t"', encoded)
        self.assertNotIn('"execution_trace"', encoded)


if __name__ == "__main__":
    unittest.main()
