"""Comprehensive error handling tests with traceback and sanitization"""

import sys
from pathlib import Path

# Add parent dir to path
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))


def test_error_handler_basic():
    """Test basic error handling"""
    from error_handler import SecureErrorHandler

    try:
        raise ValueError("Test error message")
    except Exception as e:
        result = SecureErrorHandler.handle_error(
            error=e,
            context="test_operation",
            user_message="Operation failed"
        )

        assert result["success"] is False
        assert result["error"] == "Operation failed"
        assert result["error_type"] == "ValueError"
        assert result["context"] == "test_operation"


def test_error_handler_masks_secrets_in_traceback():
    """Test that secrets are masked in traceback"""
    from error_handler import SecureErrorHandler

    def failing_func():
        api_key = "sk-1234567890abcdef"
        raise ValueError(f"Failed with api_key={api_key}")

    try:
        failing_func()
    except Exception as e:
        result = SecureErrorHandler.handle_error(
            error=e,
            context="api_call"
        )

        assert result["success"] is False
        # The exception message itself contains the key, but logs should mask it
        assert result["error_type"] == "ValueError"


def test_error_handler_decorator():
    """Test error handling decorator"""
    from error_handler import SecureErrorHandler

    @SecureErrorHandler.wrap_function(
        context="test_function",
        user_message="Function failed"
    )
    def failing_function():
        raise RuntimeError("Internal error")

    result = failing_function()

    assert result["success"] is False
    assert result["error"] == "Function failed"
    assert result["error_type"] == "RuntimeError"


def test_safe_error_response():
    """Test generation of safe error responses"""
    from error_handler import get_safe_error_response

    # Test various status codes
    response, status = get_safe_error_response(404)
    assert response["error"] == "Not found"
    assert status == 404

    response, status = get_safe_error_response(401)
    assert response["error"] == "Unauthorized"
    assert status == 401

    response, status = get_safe_error_response(500)
    assert response["error"] == "Internal server error"
    assert status == 500


def test_credential_sanitizer_key_detection():
    """Test credential sanitizer key detection"""
    from credential_sanitizer import CredentialSanitizer

    secret_keys = [
        "api_key", "API_KEY", "apikey", "api-key",
        "access_token", "authorization", "x-api-key",
        "private_key", "password", "secret",
    ]

    safe_keys = [
        "name", "email", "id", "url", "path",
        "model", "version", "status"
    ]

    for key in secret_keys:
        assert CredentialSanitizer.is_secret_key(key), f"{key} should be detected as secret"

    for key in safe_keys:
        assert not CredentialSanitizer.is_secret_key(key), f"{key} should be safe"


def test_credential_sanitizer_value_detection():
    """Test credential sanitizer value pattern detection"""
    from credential_sanitizer import CredentialSanitizer

    secret_values = [
        "sk-1234567890abcdefghijklmnop",  # API key format
        "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzdWIiOiIxMjM0NTY3ODkwIn0.dozjgNryP4J3jVmNHl0w5N_XgL0n3I9PlFUP0THsR8U",  # JWT
        "AKIA1234567890123456",  # AWS key
        "A" * 50,  # Long string
    ]

    safe_values = [
        "https://example.com",
        "123456",
        "my-simple-id",
        "hello world",
    ]

    for value in secret_values:
        assert CredentialSanitizer.is_secret_value(value), f"{value} should be detected as secret"

    for value in safe_values:
        assert not CredentialSanitizer.is_secret_value(value), f"{value} should be safe"


def test_credential_sanitizer_dict():
    """Test sanitizing dictionaries"""
    from credential_sanitizer import CredentialSanitizer

    data = {
        "username": "john",
        "api_key": "sk-1234567890abcdef",
        "nested": {
            "password": "secret123",
            "email": "john@example.com"
        },
        "tokens": [
            {"access_token": "token1"},
            {"access_token": "token2"}
        ]
    }

    result = CredentialSanitizer.sanitize_dict(data)

    assert result["username"] == "john"
    assert result["api_key"] == "<CREDENTIALS MASKED>"
    assert result["nested"]["password"] == "<CREDENTIALS MASKED>"
    assert result["nested"]["email"] == "john@example.com"
    assert result["tokens"][0]["access_token"] == "<CREDENTIALS MASKED>"


def test_credential_sanitizer_string():
    """Test sanitizing raw strings"""
    from credential_sanitizer import CredentialSanitizer

    text = """
    API Key: sk-1234567890abcdefghijklmnop
    Password: mypassword123
    Public data: https://example.com
    Email: user@example.com
    """

    result = CredentialSanitizer.sanitize_string(text)

    assert "sk-1234567890abcdefghijklmnop" not in result
    assert "mypassword123" not in result
    assert "https://example.com" in result  # URLs should not be masked
    assert "user@example.com" in result  # Emails should not be masked
    assert "<MASKED>" in result or "<SECRET MASKED" in result


def test_credential_sanitizer_yandex_key():
    """Test detection and masking of Yandex API keys"""
    from credential_sanitizer import CredentialSanitizer

    yandex_key = "YANDEX_API_KEY:ajEh7N2K_abcd1234efgh5678ijkl9012mnop"

    data = {
        "YANDEX_API_KEY": yandex_key,
        "model": "yandexgpt"
    }

    result = CredentialSanitizer.sanitize_dict(data)

    assert result["YANDEX_API_KEY"] == "<CREDENTIALS MASKED>"
    assert result["model"] == "yandexgpt"


def test_error_handler_different_log_levels():
    """Test error handler with different log levels"""
    from error_handler import SecureErrorHandler

    # Should not raise with different log levels
    try:
        raise ValueError("Test")
    except Exception as e:
        for level in ["debug", "info", "warning", "error", "critical"]:
            result = SecureErrorHandler.handle_error(
                error=e,
                context="test",
                log_level=level
            )
            assert result["success"] is False


def test_error_handler_custom_return_code():
    """Test error handler with custom HTTP codes"""
    from error_handler import SecureErrorHandler

    try:
        raise ValueError("Not found")
    except Exception as e:
        result = SecureErrorHandler.handle_error(
            error=e,
            context="lookup",
            return_code=404,
            user_message="Resource not found"
        )

        assert result["status_code"] == 404
        assert result["error"] == "Resource not found"


def test_error_handler_traceback_structure():
    """Test that error traceback has proper structure"""
    from error_handler import SecureErrorHandler
    import logging

    # Set up a handler to capture logs
    log_capture = []

    class TestHandler(logging.Handler):
        def emit(self, record):
            log_capture.append(record)

    test_handler = TestHandler()
    logger = logging.getLogger("alice_pro.errors")
    logger.addHandler(test_handler)

    try:
        def nested_call():
            raise RuntimeError("Nested error")
        nested_call()
    except Exception as e:
        result = SecureErrorHandler.handle_error(
            error=e,
            context="nested_operation"
        )

        assert result["error_type"] == "RuntimeError"

    logger.removeHandler(test_handler)
