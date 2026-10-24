"""Contract tests for Anthropic Haiku 5.5 as an optional Logos model provider."""

import importlib.util
import sqlite3

import pytest

from billing import build_ai_billing_item
from config import TEXT_MODELS
from provider_credentials import (
    SUPPORTED_PROVIDERS,
    NoActiveCredentialError,
    bootstrap_credential,
    create_schema,
    get_active_credential,
)


MODEL_KEY = "claude-haiku-5-5"
API_KEY = "test-anthropic-secret"


def test_anthropic_credential_is_encrypted_and_independent_from_yandex():
    assert "anthropic" in SUPPORTED_PROVIDERS

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    create_schema(conn)

    def encrypt(value):
        return f"enc:{value}"

    def decrypt(value):
        return value.removeprefix("enc:")

    bootstrap_credential(
        conn,
        api_key="test-yandex-secret",
        project_id="test-yandex-project",
        encrypt=encrypt,
        provider="yandex",
    )
    bootstrap_credential(
        conn,
        api_key=API_KEY,
        project_id="",
        encrypt=encrypt,
        provider="anthropic",
    )

    stored = conn.execute(
        "SELECT provider, api_key_encrypted FROM provider_credentials ORDER BY provider"
    ).fetchall()
    assert [(row["provider"], row["api_key_encrypted"]) for row in stored] == [
        ("anthropic", f"enc:{API_KEY}"),
        ("yandex", "enc:test-yandex-secret"),
    ]
    assert get_active_credential(conn, decrypt, provider="anthropic").api_key == API_KEY
    assert get_active_credential(conn, decrypt, provider="yandex").api_key == "test-yandex-secret"
    conn.close()


def test_haiku_catalog_entry_keeps_anthropic_model_and_usd_pricing_explicit():
    model = TEXT_MODELS[MODEL_KEY]
    assert model["provider"] == "anthropic"
    assert model["model_id"] == MODEL_KEY
    assert model["currency"] == "USD"
    assert model["price_unit"] == "per_million_tokens"
    assert model["input"] == pytest.approx(0.10)
    assert model["output"] == pytest.approx(0.50)


def test_messages_client_builds_anthropic_messages_api_request():
    spec = importlib.util.find_spec("anthropic_haiku_provider")
    assert spec is not None, "Logos needs a dedicated Anthropic Haiku provider adapter"
    from anthropic_haiku_provider import build_messages_request

    request = build_messages_request(
        api_key=API_KEY,
        messages=[{"role": "user", "content": "hello"}],
        system="You are Logos.",
        max_tokens=128,
    )
    assert request["url"] == "https://api.anthropic.com/v1/messages"
    assert request["headers"]["x-api-key"] == API_KEY
    assert request["headers"]["anthropic-version"]
    assert request["json"]["model"] == MODEL_KEY
    assert request["json"]["max_tokens"] == 128
    assert request["json"]["system"] == "You are Logos."
    assert request["json"]["messages"] == [{"role": "user", "content": "hello"}]


def test_anthropic_response_tool_use_normalizes_without_bypassing_approval():
    spec = importlib.util.find_spec("anthropic_haiku_provider")
    assert spec is not None, "Logos needs an Anthropic tool-use adapter"
    from anthropic_haiku_provider import normalize_message_response
    from partner_relations import PARTNER_TOOLS
    from responses_tool_loop import extract_function_calls
    from universal_tool_platform import tool_call_requires_approval

    response = normalize_message_response(
        {
            "id": "msg_test",
            "content": [
                {
                    "type": "tool_use",
                    "id": "toolu_test",
                    "name": "partner.message.send",
                    "input": {"partner_id": "partner-test", "message": "hello"},
                }
            ],
            "usage": {"input_tokens": 12, "output_tokens": 5},
        }
    )
    calls = extract_function_calls(response)
    assert calls == [
        {
            "type": "function_call",
            "call_id": "toolu_test",
            "name": "partner.message.send",
            "arguments": {"partner_id": "partner-test", "message": "hello"},
        }
    ]
    tool_definition = PARTNER_TOOLS[calls[0]["name"]]
    assert tool_call_requires_approval(tool_definition, calls[0]["arguments"])


def test_billing_marks_anthropic_usage_as_usd_estimate_and_never_records_api_key():
    item = build_ai_billing_item(
        MODEL_KEY,
        {"input_tokens": 1_000, "output_tokens": 1_000},
        step=1,
    )
    assert item["provider"] == "anthropic"
    assert item["currency"] == "USD"
    assert item["cost_status"] == "estimated"
    assert item["total_cost"] == pytest.approx(0.0006)
    assert API_KEY not in str(item)


def test_missing_anthropic_credential_does_not_fall_back_to_yandex():
    spec = importlib.util.find_spec("model_provider_registry")
    assert spec is not None, "Logos needs explicit provider routing by model"
    from model_provider_registry import create_model_client

    def unavailable(provider):
        raise NoActiveCredentialError(f"No active {provider} credential")

    with pytest.raises(NoActiveCredentialError, match="anthropic"):
        create_model_client(MODEL_KEY, credential_loader=unavailable)
