import unittest

from yandex_client import _sanitize_for_log
from yandex_request_utils import sanitize_for_log


class YandexRequestUtilsTests(unittest.TestCase):
    def test_legacy_yandex_client_alias_matches_helper(self):
        value = {"audio": "x" * 1001, "nested": ["ok", "y" * 100_001]}
        self.assertEqual(_sanitize_for_log(value), sanitize_for_log(value))

    def test_binary_fields_are_masked(self):
        value = sanitize_for_log({"image_data": "x" * 1001})
        self.assertEqual(value["image_data"], "<AUDIO/BINARY MASKED: 1001 chars>")

    def test_large_data_uri_is_masked(self):
        value = sanitize_for_log("data:image/png;base64," + "x" * 100_000)
        self.assertTrue(value.startswith("<DATA_URI MASKED:"))

    def test_large_base64_is_masked(self):
        value = sanitize_for_log("A" * 100_001)
        self.assertTrue(value.startswith("<BASE64 MASKED:"))


if __name__ == "__main__":
    unittest.main()
