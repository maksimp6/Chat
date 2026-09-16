import unittest

from yandex_metadata_validator import MetadataValidationError, validate_metadata


class YandexMetadataValidatorTests(unittest.TestCase):
    def test_none_is_allowed_and_means_omission(self):
        self.assertIsNone(validate_metadata(None))

    def test_empty_object_is_allowed(self):
        metadata = {}
        self.assertIs(validate_metadata(metadata), metadata)

    def test_valid_metadata_is_returned_unchanged(self):
        metadata = {"trace_id": "trace-1", "source": "alice-pro"}
        self.assertIs(validate_metadata(metadata), metadata)

    def test_non_object_is_rejected(self):
        with self.assertRaisesRegex(MetadataValidationError, "must be an object"):
            validate_metadata([("trace_id", "trace-1")])

    def test_non_string_key_is_rejected(self):
        with self.assertRaisesRegex(MetadataValidationError, "all keys must be strings"):
            validate_metadata({1: "trace-1"})

    def test_non_string_value_is_rejected(self):
        with self.assertRaisesRegex(MetadataValidationError, "metadata.trace_id.*must be a string"):
            validate_metadata({"trace_id": 123})

    def test_boolean_value_is_rejected_as_non_string(self):
        with self.assertRaisesRegex(MetadataValidationError, "must be a string"):
            validate_metadata({"enabled": True})

    def test_no_undocumented_length_limit_is_applied(self):
        value = "x" * 4096
        metadata = {"long_value": value}
        self.assertIs(validate_metadata(metadata), metadata)


if __name__ == "__main__":
    unittest.main()
