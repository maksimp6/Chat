import unittest

from trace_security import safe_repr, sanitize_trace_value


class TraceSecurityTests(unittest.TestCase):
    def test_safe_repr_redacts_sensitive_keys(self):
        value = safe_repr({"api_key": "secret-value", "nested": {"token": "hidden"}})
        self.assertEqual(value["api_key"], "<redacted>")
        self.assertEqual(value["nested"]["token"], "<redacted>")

    def test_sanitize_trace_value_limits_items(self):
        value = sanitize_trace_value({str(index): index for index in range(51)})
        self.assertIn("<truncated>", value)
        self.assertEqual(value["<truncated>"], "1 more items")

    def test_sanitize_trace_value_limits_depth(self):
        value = {"level": {"level": {"level": "value"}}}
        self.assertEqual(sanitize_trace_value(value), value)

    def test_safe_repr_truncates_long_strings(self):
        value = safe_repr("x" * 5000)
        self.assertTrue(value.endswith("... <truncated>"))
        self.assertEqual(len(value), 4000 + len("... <truncated>"))


if __name__ == "__main__":
    unittest.main()
