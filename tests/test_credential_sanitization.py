"""Test that credentials are properly sanitized from logs"""

from pathlib import Path
import sys


def test_sanitize_for_log_masks_api_keys():
    """Test that API keys are masked in logs"""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from yandex_request_utils import sanitize_for_log

    test_data = {
        "api_key": "sk-1234567890abcdef",
        "API_KEY": "sk-9876543210fedcba",
        "model": "gpt-4",
        "data": "some data"
    }

    result = sanitize_for_log(test_data)

    assert result["api_key"] == "<CREDENTIALS MASKED>"
    assert result["API_KEY"] == "<CREDENTIALS MASKED>"
    assert result["model"] == "gpt-4"
    assert result["data"] == "some data"


def test_sanitize_for_log_masks_authorization():
    """Test that Authorization headers are masked"""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from yandex_request_utils import sanitize_for_log

    test_data = {
        "Authorization": "Bearer token-secret",
        "Content-Type": "application/json"
    }

    result = sanitize_for_log(test_data)

    assert result["Authorization"] == "<CREDENTIALS MASKED>"
    assert result["Content-Type"] == "application/json"


def test_sanitize_for_log_masks_multiple_secret_types():
    """Test that various secret types are masked"""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from yandex_request_utils import sanitize_for_log

    test_data = {
        "api_key": "secret1",
        "token": "secret2",
        "password": "secret3",
        "access_token": "secret4",
        "private_key": "secret5",
        "credentials": "secret6",
        "public_data": "not secret"
    }

    result = sanitize_for_log(test_data)

    assert result["api_key"] == "<CREDENTIALS MASKED>"
    assert result["token"] == "<CREDENTIALS MASKED>"
    assert result["password"] == "<CREDENTIALS MASKED>"
    assert result["access_token"] == "<CREDENTIALS MASKED>"
    assert result["private_key"] == "<CREDENTIALS MASKED>"
    assert result["credentials"] == "<CREDENTIALS MASKED>"
    assert result["public_data"] == "not secret"


def test_sanitize_for_log_nested_secrets():
    """Test that nested secrets are also masked"""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from yandex_request_utils import sanitize_for_log

    test_data = {
        "request": {
            "headers": {
                "api_key": "my-secret-key",
                "content_type": "application/json"
            },
            "body": {
                "user_data": "public",
                "token": "my-token"
            }
        }
    }

    result = sanitize_for_log(test_data)

    assert result["request"]["headers"]["api_key"] == "<CREDENTIALS MASKED>"
    assert result["request"]["headers"]["content_type"] == "application/json"
    assert result["request"]["body"]["user_data"] == "public"
    assert result["request"]["body"]["token"] == "<CREDENTIALS MASKED>"


def test_sanitize_for_log_in_lists():
    """Test that secrets in lists are masked"""
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))

    from yandex_request_utils import sanitize_for_log

    test_data = {
        "credentials": [
            {"api_key": "secret1"},
            {"api_key": "secret2"}
        ]
    }

    result = sanitize_for_log(test_data)

    assert result["credentials"] == "<CREDENTIALS MASKED>"


def test_sanitization_source_code():
    """Test that sanitization function is used in logging"""
    root = Path(__file__).resolve().parents[1]

    # Check that yandex_client.py uses sanitization
    yandex_client_source = (root / "yandex_client.py").read_text(encoding="utf-8")
    assert "_sanitize_for_log" in yandex_client_source, (
        "yandex_client should use _sanitize_for_log for request/response logging"
    )

    # Check that file_manager.py doesn't log raw credentials
    file_manager_source = (root / "file_manager.py").read_text(encoding="utf-8")
    # Should not have str(e) in error responses for API errors
    start = file_manager_source.find("class Yandex")
    if start >= 0:
        next_class = file_manager_source.find("\nclass ", start + 1)
        if next_class == -1:
            class_section = file_manager_source[start:]
        else:
            class_section = file_manager_source[start:next_class]

        # Check that _err_response properly sanitizes errors
        if "_err_response" in class_section:
            assert 'str(e)' not in class_section or (
                "logger" in class_section and "_sanitize" in file_manager_source
            ), (
                "Error handling should not expose raw exceptions"
            )
