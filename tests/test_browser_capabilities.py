from browser_capabilities import (
    BROWSER_CAPABILITY_CONTRACTS,
    BrowserAction,
    sanitize_browser_value,
    validate_browser_action,
)


def test_cloud_and_local_contracts_are_separate_and_scoped():
    cloud = BROWSER_CAPABILITY_CONTRACTS["browser_cloud"]
    local = BROWSER_CAPABILITY_CONTRACTS["browser_local"]

    assert cloud.mode == "cloud"
    assert local.mode == "local"
    assert cloud.allowed_scopes != local.allowed_scopes
    assert local.requires_emulator is True
    assert cloud.metadata["real_profile_access"] is False


def test_contracts_expose_universal_tool_definitions():
    definition = BROWSER_CAPABILITY_CONTRACTS["browser_local"].to_definition()

    assert definition["capabilities"] == ["browser", "local"]
    assert definition["executor"] == {"type": "browser_adapter", "mode": "local"}
    assert definition["inputSchema"]["additionalProperties"] is False
    assert "action" in definition["inputSchema"]["required"]


def test_browser_action_validation_rejects_unknown_or_empty_targets():
    assert (
        validate_browser_action(BrowserAction("browser_local", "navigate", "https://example.test"))
        == []
    )
    assert validate_browser_action(BrowserAction("browser_local", "submit", "form"))
    assert validate_browser_action(BrowserAction("browser_local", "click", "   "))


def test_browser_sanitizer_redacts_nested_secrets_without_mutating_input():
    source = {
        "url": "https://example.test",
        "headers": {"Authorization": "Bearer secret", "Accept": "text/html"},
        "cookies": [{"name": "sid", "value": "cookie-value"}],
        "nested": ("safe", {"otp": "123456"}),
    }

    sanitized = sanitize_browser_value(source)

    assert sanitized["url"] == source["url"]
    assert sanitized["headers"]["Authorization"] == "<redacted>"
    assert sanitized["headers"]["Accept"] == "text/html"
    assert sanitized["cookies"] == "<redacted>"
    assert sanitized["nested"][1]["otp"] == "<redacted>"
    assert source["headers"]["Authorization"] == "Bearer secret"
