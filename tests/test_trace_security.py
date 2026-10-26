import unittest

from observability.trace_security import MAX_REPR, redact_text, safe_repr, sanitize_trace_value


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

        inline = sanitize_trace_value(
            "private_key=raw --client-secret client-value secret_key: secret-value"
        )
        self.assertEqual(
            inline,
            "private_key=<redacted> --client-secret <redacted> secret_key: <redacted>",
        )

    def test_private_key_redaction_handles_malformed_and_non_key_markers(self):
        malformed = sanitize_trace_value("before -----BEGIN PRIVATE KEY")
        self.assertEqual(malformed, "before -----BEGIN PRIVATE KEY")

        certificate = sanitize_trace_value(
            "before -----BEGIN CERTIFICATE-----data-----END CERTIFICATE----- after"
        )
        self.assertIn("BEGIN CERTIFICATE", certificate)
        self.assertIn("data", certificate)

        unterminated = sanitize_trace_value("before -----BEGIN RSA PRIVATE KEY-----SECRET-MATERIAL")
        self.assertEqual(unterminated, "before <redacted-private-key>")
        self.assertNotIn("SECRET-MATERIAL", unterminated)

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

    def test_safe_repr_redacts_bearer_in_bytes_and_object_repr(self):
        class Holder:
            def __repr__(self):
                return "Holder(header='Authorization: Bearer SYNTHETIC_VALUE')"

        self.assertNotIn("SYNTHETIC_VALUE", safe_repr(b"Bearer SYNTHETIC_VALUE"))
        self.assertNotIn("SYNTHETIC_VALUE", safe_repr(Holder()))

    def test_redact_text_keeps_full_length_for_long_tracebacks(self):
        tail = "ValueError: final line"
        text = "x" * (MAX_REPR + 10) + " Bearer SYNTHETIC_VALUE\n" + tail
        result = redact_text(text)
        self.assertNotIn("SYNTHETIC_VALUE", result)
        self.assertTrue(result.endswith(tail))


if __name__ == "__main__":
    unittest.main()
