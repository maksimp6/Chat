import unittest

from yandex_metadata_validator import MetadataValidationError
from yandex_request_builder import build_response_payload, validate_generation_params


class YandexRequestBuilderTests(unittest.TestCase):
    def test_builds_core_payload_and_options(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={
                "background": False,
                "temperature": "0.2",
                "top_p": "0.8",
                "max_output_tokens": "100",
                "prompt_cache_key": "stable",
                "reasoning_effort": "low",
            },
        )
        self.assertEqual(payload["model"], "gpt://project/model/latest")
        self.assertEqual(payload["input"], [{"role": "user", "content": "hello"}])
        self.assertFalse(payload["background"])
        self.assertTrue(payload["store"])
        self.assertEqual(payload["temperature"], 0.2)
        self.assertEqual(payload["top_p"], 0.8)
        self.assertEqual(payload["max_output_tokens"], 100)
        self.assertEqual(payload["prompt_cache_key"], "stable")
        self.assertEqual(payload["reasoning"], {"effort": "low"})

    def test_temperature_boundaries_are_valid(self):
        self.assertEqual(validate_generation_params({"temperature": 0})["temperature"], 0.0)
        self.assertEqual(validate_generation_params({"temperature": 1})["temperature"], 1.0)

    def test_temperature_above_maximum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            validate_generation_params({"temperature": 1.1})

    def test_temperature_below_minimum_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "between 0 and 1"):
            validate_generation_params({"temperature": -0.1})

    def test_temperature_non_numeric_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must be a number"):
            validate_generation_params({"temperature": "not-a-number"})

    def test_background_forces_store(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={"background": True, "store": False},
        )
        self.assertTrue(payload["background"])
        self.assertTrue(payload["store"])

    def test_mcp_tools_are_cleaned(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={
                "background": False,
                "tools": [
                    {"type": "mcp", "server_url": " https://example.test ", "server_label": "demo"},
                    {"type": "mcp", "server_label": "invalid"},
                    {"type": "function", "name": "local"},
                ],
            },
        )
        self.assertEqual(
            payload["tools"],
            [
                {"type": "mcp", "server_label": "demo", "server_url": "https://example.test"},
                {"type": "function", "name": "local"},
            ],
        )
        self.assertTrue(payload["parallel_tool_calls"])

    def test_conversation_and_metadata_are_preserved(self):
        payload = build_response_payload(
            "project",
            "model",
            "hello",
            params={"background": False},
            metadata={"trace_id": "trace-1"},
            conversation_id="conv-local",
            yandex_conv_id="conv-yandex",
        )
        self.assertEqual(payload["metadata"], {"trace_id": "trace-1"})
        self.assertEqual(payload["conversation"], {"id": "conv-yandex"})
        self.assertEqual(payload["prompt_cache_key"], "conv-local")

    def test_invalid_metadata_is_rejected_before_payload_is_returned(self):
        with self.assertRaises(MetadataValidationError):
            build_response_payload(
                "project",
                "model",
                "hello",
                params={"background": False},
                metadata={"trace_id": 123},
            )


if __name__ == "__main__":
    unittest.main()
