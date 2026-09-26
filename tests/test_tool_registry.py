import unittest

from tool_registry import ToolRegistry


class ToolRegistryCategoryTests(unittest.TestCase):
    def setUp(self):
        self.registry = ToolRegistry.__new__(ToolRegistry)
        self.registry._tools = {
            "git_status": {
                "description": "Return repository status",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                },
                "func": lambda arguments: {"ok": True},
            },
            "termux_exec": {
                "description": "Execute a Termux command",
                "parameters": {"type": "object", "properties": {}},
                "func": lambda arguments: {"ok": True},
            },
        }
        self.registry._categories = {"git": ["git_status"], "termux": ["termux_exec"]}

    def test_get_tools_by_category_returns_responses_function_definitions(self):
        tools = self.registry.get_tools_by_category("git")

        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0]["type"], "function")
        self.assertEqual(tools[0]["name"], "git_status")
        self.assertTrue(tools[0]["strict"])
        self.assertFalse(tools[0]["defer_loading"])

    def test_get_tools_by_category_applies_strict_schema(self):
        tools = self.registry.get_tools_by_category("git")
        parameters = tools[0]["parameters"]

        self.assertEqual(parameters["required"], ["path"])
        self.assertFalse(parameters["additionalProperties"])
        self.assertIn("anyOf", parameters["properties"]["path"])

    def test_unknown_category_returns_empty_list(self):
        self.assertEqual(self.registry.get_tools_by_category("missing"), [])

    def test_category_does_not_include_other_categories(self):
        tools = self.registry.get_tools_by_category("git")
        names = [tool["name"] for tool in tools]

        self.assertEqual(names, ["git_status"])
        self.assertNotIn("termux_exec", names)


if __name__ == "__main__":
    unittest.main()
