"""Regression controls for side effects and the advertised tool contract."""

import unittest

from agent_runtime.responses_tool_loop import run_tool_loop
from tool_providers.registry import ToolRegistry
from tool_providers.universal_tool_platform import UniversalToolCall, UniversalToolExecutor


def response(*ids, status="completed"):
    return {
        "status": status,
        "output": [
            {"type": "function_call", "call_id": call_id, "name": "record", "arguments": "{}"}
            for call_id in ids
        ],
    }


class ToolLoopIntegrityTests(unittest.TestCase):
    def test_incomplete_and_failed_responses_do_not_execute(self):
        for status in ("incomplete", "failed", "cancelled", "queued", "in_progress"):
            with self.subTest(status=status):
                executed = []
                original = response("one", status=status)
                final = run_tool_loop(original, executed.append, lambda *_: self.fail("continued"))
                self.assertIs(final, original)
                self.assertEqual(executed, [])

    def test_error_with_completed_status_does_not_execute(self):
        for error in ({"code": "provider_failure"}, {}, ""):
            with self.subTest(error=error):
                original = {**response("one"), "error": error}
                self.assertIs(run_tool_loop(original, lambda _: self.fail("executed"), None), original)

    def test_invalid_second_id_prevents_first_side_effect(self):
        for bad_id in (None, "", "  ", 42, ["bad"]):
            with self.subTest(call_id=bad_id):
                executed = []
                with self.assertRaisesRegex(ValueError, "call_id/id"):
                    run_tool_loop(response("valid", bad_id), executed.append, None)
                self.assertEqual(executed, [])

    def test_duplicate_ids_prevent_entire_batch(self):
        executed = []
        with self.assertRaisesRegex(ValueError, "duplicate"):
            run_tool_loop(response("same", "same"), executed.append, None)
        self.assertEqual(executed, [])

    def test_replayed_id_does_not_repeat_side_effect(self):
        executed = []
        with self.assertRaisesRegex(ValueError, "already executed"):
            run_tool_loop(response("same"), executed.append, lambda *_: response("same"))
        self.assertEqual(len(executed), 1)

    def test_partial_call_prevents_entire_batch(self):
        original = response("first", "second")
        original["output"][1]["status"] = "in_progress"
        executed = []
        with self.assertRaisesRegex(ValueError, "not completed"):
            run_tool_loop(original, executed.append, None)
        self.assertEqual(executed, [])

    def test_last_round_preserves_provider_failure(self):
        failure = response("partial", status="incomplete")
        self.assertIs(run_tool_loop(response("one"), lambda _: {}, lambda *_: failure,
                                    max_rounds=1), failure)

    def test_invalid_continuation_is_explicit_error(self):
        for max_rounds in (1, None):
            with self.subTest(max_rounds=max_rounds):
                with self.assertRaisesRegex(ValueError, "non-object"):
                    run_tool_loop(response("one"), lambda _: {}, lambda *_: None,
                                  max_rounds=max_rounds)

    def test_real_executor_receives_normalized_optional_arguments(self):
        registry = ToolRegistry.__new__(ToolRegistry)
        registry._tools = {}
        registry._categories = {}
        received = []
        registry.register("test", "record", {
            "inputSchema": {"type": "object", "properties": {"optional": {"type": "string"}}},
            "parameters": {"type": "object", "properties": {"stale": {"type": "integer"}}},
            "func": lambda args: received.append(args) or {"value": "recorded"},
        })
        advertised = registry.get_definitions()[0]
        self.assertIn("optional", advertised["parameters"]["properties"])
        self.assertNotIn("stale", advertised["parameters"]["properties"])
        executor = UniversalToolExecutor(registry)
        first = response("one")
        first["output"][0]["arguments"] = '{"optional":null}'
        continued = []

        def execute(call):
            import json
            result = executor.execute(UniversalToolCall(
                "record", json.loads(call["arguments"]), call_id=call["call_id"],
                transport="responses_api",
            ))
            self.assertTrue(result["success"], result)
            return result["data"]

        def continuation(items, previous):
            continued.extend(items)
            return response()

        run_tool_loop(first, execute, continuation)
        self.assertEqual(received, [{}])
        self.assertEqual(continued[-1]["call_id"], "one")
        self.assertIn("recorded", continued[-1]["output"])

    def test_open_maps_are_not_advertised_as_closed_strict_objects(self):
        for additional in (True, {"type": "string"}):
            with self.subTest(additional=additional):
                schema = {"type": "object", "properties": {}, "additionalProperties": additional}
                registry = ToolRegistry.__new__(ToolRegistry)
                tool = registry._function_definition("record", {"inputSchema": schema})
                self.assertFalse(tool["strict"])
                self.assertEqual(tool["parameters"], schema)

    def test_server_configuration_reaches_the_registered_executor(self):
        registry = ToolRegistry.__new__(ToolRegistry)
        registry._tools = {}
        registry._categories = {}
        received = []
        registry.register("test", "demo_read", {
            "parameters": {"type": "object", "properties": {}},
            "func": lambda args, cfg: received.append(cfg.get("endpoint")) or {},
        })
        result = UniversalToolExecutor(registry).execute(
            UniversalToolCall("demo_read", {}, transport="responses_api"),
            server_configs=[{"connector_id": "demo", "config": {"endpoint": "fixture"}}],
        )
        self.assertTrue(result["success"], result)
        self.assertEqual(received, ["fixture"])
        self.assertNotIn("endpoint", result["metadata"])


if __name__ == "__main__":
    unittest.main()
