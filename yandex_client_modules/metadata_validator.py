"""Validation for Yandex AI Studio Responses API metadata."""


class MetadataValidationError(ValueError):
    """Raised when provider metadata does not match the API contract."""

    def __init__(self, field, reason):
        self.field = field
        self.reason = reason
        super().__init__(f"Invalid metadata field '{field}': {reason}")


def validate_metadata(metadata):
    """Validate documented metadata shape and return it unchanged.

    Yandex documents metadata as an object whose keys and values are strings.
    The field itself is optional, so None is accepted and means omission.
    No undocumented length limit is imposed here.
    """
    if metadata is None:
        return None
    if not isinstance(metadata, dict):
        raise MetadataValidationError("metadata", "must be an object")

    for key, value in metadata.items():
        if not isinstance(key, str):
            raise MetadataValidationError("metadata", "all keys must be strings")
        if not isinstance(value, str):
            raise MetadataValidationError(f"metadata.{key}", "value must be a string")

    return metadata
