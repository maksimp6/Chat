import unittest

from trace_security import safe_repr, sanitize_trace_value


class TraceSecurityTests(unittest.TestCase):
    def test_safe_repr_redacts_sensitive_keys(self):
        value = safe_repr({"api_key": "secret-value", "nested": {"token": "hidden"}})
        self.assertEqual(value["api_key"], "<redacted>")
        self.assertEqual(value["nested"]["token"], "<redacted>")

    def test_sanitize_trace_value_redacts_private_keys_and_pem_blocks(self):
        value = sanitize_trace_value(
            {
                "private_key": "raw-key",
                "nested": {"client_secret": "client-secret"},
                "text": (
                    "before\n-----BEGIN PRIVATE KEY-----\n"
                    "PRIVATE-MATERIAL\n-----END PRIVATE KEY-----\nafter"
                ),
            }
        )
        self.assertEqual(value["private_key"], "<redacted>")
        self.assertEqual(value["nested"]["client_secret"], "<redacted>")
        self.assertNotIn("PRIVATE-MATERIAL", value["text"])
        self.assertIn("<redacted-private-key>", value["text"])

    def test_sanitize_trace_value_limits_items(self):
        value = sanitize_trace_value({str(index): index for index in range(51)})
        self.assertIn("<truncated>", value)
        self.assertEqual(value["<truncated>"], "1 more items")

    def test_sanitize_trace_value_limits_depth(self):
        value = "value"
        for _ in range(14):
            value = {"level": value}
        sanitized = sanitize_trace_value(value)
        current = sanitized
        for _ in range(13):
            current = current["level"]
        self.assertEqual(current, "<max-depth>")

    def test_safe_repr_truncates_long_strings(self):
        value = safe_repr("x" * 5000)
        self.assertTrue(value.endswith("... <truncated>"))
        self.assertEqual(len(value), 4000 + len("... <truncated>"))

    def test_sanitize_trace_value_redacts_inline_secrets_but_keeps_context(self):
        value = sanitize_trace_value(
            "deploy --api-key=key-value --token credential-value; authorization: ******"
        )
        self.assertEqual(
            value,
            "deploy --api-key=<redacted> --token <redacted>; authorization: <redacted>",
        )


if __name__ == "__main__":
    unittest.main()
