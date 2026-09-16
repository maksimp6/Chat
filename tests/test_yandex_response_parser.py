import unittest

from yandex_response_parser import extract_reasoning_and_text, extract_text, extract_usage


class YandexResponseParserTests(unittest.TestCase):
    def test_extracts_reasoning_and_text(self):
        data = {
            "output": [
                {"content": [{"type": "reasoning_text", "text": "Think"}]},
                {"content": [{"type": "output_text", "text": "Hello"}, {"type": "text", "text": " world"}]},
            ]
        }
        self.assertEqual(extract_reasoning_and_text(data), ("Think", "Hello world"))
        self.assertEqual(extract_text(data), "Hello world")

    def test_uses_fallback_text_fields(self):
        self.assertEqual(extract_text({"output_text": "fallback"}), "fallback")
        self.assertEqual(extract_text({"text": "legacy"}), "legacy")
        self.assertEqual(extract_text(None), "")

    def test_extracts_usage_details(self):
        data = {
            "usage": {
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
                "input_tokens_details": {"cached_tokens": 2, "tool_tokens": 3},
                "output_tokens_details": {"reasoning_tokens": 4},
            },
            "created_at": 1,
            "completed_at": 2,
            "incomplete_details": None,
        }
        usage = extract_usage(data)
        self.assertEqual(usage["total_tokens"], 15)
        self.assertEqual(usage["cached_tokens"], 2)
        self.assertEqual(usage["tool_tokens"], 3)
        self.assertEqual(usage["reasoning_tokens"], 4)
        self.assertEqual(usage["created_at"], 1)

    def test_non_mapping_usage_returns_none(self):
        self.assertIsNone(extract_usage([]))


if __name__ == "__main__":
    unittest.main()
