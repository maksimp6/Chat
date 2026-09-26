import unittest
from unittest.mock import patch

from trace_manager import ExecutionTrace
from universal_tool_platform import (
    UniversalToolExecutor,
    UniversalToolCall,
    UniversalToolDefinition,
)
from tool_registry import registry
from yandex_client_modules.mcp_mixin import YandexMcpMixin


class TestUniversalToolExecutionIntegration(unittest.TestCase):
    def _definition(self, name):
        return UniversalToolDefinition(
            name=name,
            description="demo",
            input_schema={
                "type": "object",
                "properties": {"value": {"type": "integer"}},
                "required": ["value"],
            },
            output_schema={"type": "object"},
            read_only=True,
            requires_approval=False,
            supported_transports=("responses_api",),
            executor={"type": "local"},
        )

    def test_responses_api_tool_goes_through_universal_executor(self):
        trace = ExecutionTrace()
        trace.set_context(
            invocation_id="inv-1",
            session_id="sess-1",
            conversation_id="conv-1",
            user_id="user-1",
        )
        call = {
            "type": "function_call",
            "name": "demo_tool",
            "arguments": {"value": 7},
            "call_id": "call-7",
        }

        with (
            patch.object(
                registry, "get_universal_definition", return_value=self._definition("demo_tool")
            ),
            patch.object(registry, "execute", return_value={"value": 7}) as legacy_execute,
        ):
            result = YandexMcpMixin()._execute_single_tool(call, [], trace=trace)

        self.assertEqual(result["result"], {"value": 7})
        self.assertIsNone(result["error"])
        legacy_execute.assert_called_once()
        self.assertEqual(trace.trace["tool_calls"][0]["call_id"], "call-7")
        self.assertEqual(trace.trace["tool_calls"][0]["name"], "demo_tool")

    def test_failed_universal_execution_is_not_reported_as_success(self):
        class FakeRegistry:
            def get_universal_definition(self, name):
                return UniversalToolDefinition(
                    name=name,
                    description="demo",
                    input_schema={
                        "type": "object",
                        "properties": {},
                        "required": [],
                        "additionalProperties": False,
                    },
                    output_schema={"type": "object"},
                    read_only=True,
                    requires_approval=False,
                    supported_transports=("responses_api",),
                    executor={"type": "local"},
                )

            def execute(self, name, arguments, **kwargs):
                return {"error": "permission denied"}

        result = UniversalToolExecutor(FakeRegistry()).execute(
            UniversalToolCall(
                tool_name="demo_tool",
                arguments={},
                transport="responses_api",
            )
        )
        self.assertFalse(result["success"])
        self.assertEqual(result["error"], "permission denied")
        self.assertEqual(result["metadata"]["phase"], "execution")


if __name__ == "__main__":
    unittest.main()
