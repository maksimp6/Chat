import unittest
from unittest.mock import patch

from trace_manager import ExecutionTrace
from tool_registry import registry
from yandex_client_modules.mcp_mixin import YandexMcpMixin


class TestLocalToolExecutionContract(unittest.TestCase):
    def _call(self, execute):
        trace = ExecutionTrace()
        with patch.object(registry, "execute", side_effect=execute):
            result = YandexMcpMixin()._execute_single_tool(
                {
                    "name": "test_tool",
                    "call_id": "call-1",
                    "arguments": "{}",
                },
                [],
                trace=trace,
            )
        return result, trace.finalize()

    def test_returned_error_is_not_reported_as_success(self):
        result, trace = self._call(lambda _name, _args, _servers: {
            "error": "permission denied",
        })

        self.assertEqual(result["error"], "permission denied")
        self.assertFalse(result["timing"]["success"])
        self.assertEqual(trace["tool_calls"][0]["name"], "test_tool")
        self.assertEqual(trace["tool_calls"][0]["call_id"], "call-1")
        self.assertEqual(trace["tool_calls"][0]["error"], "permission denied")
        self.assertEqual(trace["errors"][0]["source"], "tool:test_tool")
        self.assertEqual(trace["errors"][0]["call_id"], "call-1")

    def test_exception_is_not_converted_to_success(self):
        def execute(_name, _args, _servers):
            raise RuntimeError("backend exploded")

        result, trace = self._call(execute)

        self.assertEqual(result["error"], "backend exploded")
        self.assertFalse(result["timing"]["success"])
        self.assertEqual(trace["tool_calls"][0]["error"], "backend exploded")
        tool_events = [event for event in trace["events"] if event["type"] == "tool_executed"]
        self.assertEqual(len(tool_events), 1)
        self.assertFalse(tool_events[0]["payload"]["success"])

        errors = [e for e in trace["errors"] if e["source"] == "tool:test_tool"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["error"], "backend exploded")
        self.assertEqual(errors[0]["python_exception"]["exception_type"], "RuntimeError")


if __name__ == "__main__":
    unittest.main()
