"""Advanced credential sanitization for logs and traces"""

import re
from typing import Any, Dict, List, Set, Union


class CredentialSanitizer:
    """Sanitize credentials by both key name and value patterns"""

    # Secret key patterns
    SECRET_KEY_PATTERNS = {
        r"(?i)api[_-]?key",
        r"(?i)apikey",
        r"(?i)access[_-]?token",
        r"(?i)refresh[_-]?token",
        r"(?i)auth[_-]?token",
        r"(?i)authorization",
        r"(?i)bearer",
        r"(?i)password",
        r"(?i)passwd",
        r"(?i)secret(?:[_-]?key)?",
        r"(?i)credential",
        r"(?i)private[_-]?key",
        r"(?i)certificate",
        r"(?i)token",
        r"(?i)x[_-]?api[_-]?key",
        r"(?i)x[_-]?auth",
        r"(?i)x[_-]?token",
        r"(?i)jwt",
    }

    # Secret value patterns (high entropy strings)
    SECRET_VALUE_PATTERNS = [
        # API keys (various formats)
        re.compile(r"(?i)(sk-|pk-|api_)[A-Za-z0-9_-]{20,}"),
        # Bearer tokens
        re.compile(r"(?i)bearer\s+[A-Za-z0-9._-]{20,}"),
        # JWT tokens
        re.compile(r"eyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\."),
        # AWS keys
        re.compile(r"AKIA[0-9A-Z]{16}"),
        # Yandex API keys (format: YANDEX_API_KEY)
        re.compile(r"(?i)yandex[._-]?api[._-]?key[_:]?[A-Za-z0-9._-]{30,}"),
        # Generic long base64/hex strings
        re.compile(r"[A-Za-z0-9+/]{40,}={0,2}"),
    ]

    # Known safe patterns (won't mask even if high entropy)
    SAFE_VALUE_PATTERNS = [
        re.compile(r"^https?://"),  # URLs
        re.compile(r"^\d+$"),  # Numbers
        re.compile(r"^[a-z0-9-]+$"),  # Common IDs
        re.compile(r"^[a-zA-Z0-9_ .,;:@%#()[\]-]*$"),  # Common text
    ]

    @classmethod
    def is_secret_key(cls, key: str) -> bool:
        """Check if key name suggests it contains secrets"""
        key_lower = str(key).lower()
        return any(re.search(pattern, key_lower) for pattern in cls.SECRET_KEY_PATTERNS)

    @classmethod
    def is_secret_value(cls, value: Any) -> bool:
        """Check if value matches secret patterns"""
        if not isinstance(value, str):
            return False

        if len(value) < 20:
            return False

        # Check if it's a known safe pattern first
        for safe_pattern in cls.SAFE_VALUE_PATTERNS:
            if safe_pattern.match(value):
                return False

        # Check if it matches secret patterns
        return any(pattern.search(value) for pattern in cls.SECRET_VALUE_PATTERNS)

    @classmethod
    def sanitize_dict(cls, data: Dict[str, Any], redact_keys: bool = True) -> Dict[str, Any]:
        """
        Sanitize dictionary by removing secrets

        Args:
            data: Dictionary to sanitize
            redact_keys: If True, redact by key names. If False, only by value patterns.

        Returns:
            Sanitized copy of dictionary
        """
        result = {}
        for key, value in data.items():
            key_is_secret = redact_keys and cls.is_secret_key(key)

            if key_is_secret:
                result[key] = "<CREDENTIALS MASKED>"
            elif isinstance(value, dict):
                result[key] = cls.sanitize_dict(value, redact_keys)
            elif isinstance(value, (list, tuple)):
                result[key] = [
                    cls.sanitize_value(v, redact_keys) if not isinstance(v, dict)
                    else cls.sanitize_dict(v, redact_keys)
                    for v in value
                ]
            else:
                result[key] = cls.sanitize_value(value, redact_keys)

        return result

    @classmethod
    def sanitize_value(cls, value: Any, redact_by_pattern: bool = True) -> Any:
        """Sanitize a single value"""
        if isinstance(value, dict):
            return cls.sanitize_dict(value, redact_by_pattern)

        if isinstance(value, (list, tuple)):
            return [cls.sanitize_value(v, redact_by_pattern) for v in value]

        if isinstance(value, str) and redact_by_pattern and cls.is_secret_value(value):
            return f"<SECRET MASKED: {len(value)} chars>"

        return value

    @classmethod
    def sanitize_string(cls, text: str) -> str:
        """
        Sanitize secrets from raw text/logs

        Replaces found secrets with masked placeholders
        """
        result = text

        # Replace API keys and tokens
        for pattern in cls.SECRET_VALUE_PATTERNS:
            result = pattern.sub(lambda m: f"<{len(m.group(0))} CHARS MASKED>", result)

        # Replace common secret formats in strings
        result = re.sub(r"(?i)(api[_-]?key\s*[:=]\s*)([^\s,\]\"']+)", r"\1<MASKED>", result)
        result = re.sub(r"(?i)(token\s*[:=]\s*)([^\s,\]\"']+)", r"\1<MASKED>", result)
        result = re.sub(r"(?i)(password\s*[:=]\s*)([^\s,\]\"']+)", r"\1<MASKED>", result)
        result = re.sub(r"(?i)(authorization\s*[:=]\s*)([^\s,\]\"']+)", r"\1<MASKED>", result)

        return result

    @classmethod
    def get_sanitized_traceback(cls, traceback_text: str) -> str:
        """Get sanitized version of exception traceback"""
        return cls.sanitize_string(traceback_text)


# Convenience functions
def sanitize_for_logging(data: Union[Dict, str, Any]) -> Union[Dict, str, Any]:
    """Sanitize data before logging - main entry point"""
    if isinstance(data, dict):
        return CredentialSanitizer.sanitize_dict(data)
    elif isinstance(data, str):
        return CredentialSanitizer.sanitize_string(data)
    else:
        return CredentialSanitizer.sanitize_value(data)


def is_sensitive_data(data: Any, key: str = "") -> bool:
    """Check if data contains sensitive information"""
    if key and CredentialSanitizer.is_secret_key(key):
        return True

    if isinstance(data, str):
        return CredentialSanitizer.is_secret_value(data)

    return False
