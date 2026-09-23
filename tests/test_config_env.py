import importlib

import pytest

import config


def reload_config():
    return importlib.reload(config)


def test_uses_canonical_yandex_api_key(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "test-canonical-key")
    monkeypatch.delenv("YC_API_KEY", raising=False)

    module = reload_config()

    assert module.API_KEY == "test-canonical-key"


def test_supports_legacy_yc_api_key(monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.setenv("YC_API_KEY", "test-legacy-key")

    module = reload_config()

    assert module.API_KEY == "test-legacy-key"


def test_canonical_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "test-canonical-key")
    monkeypatch.setenv("YC_API_KEY", "test-legacy-key")

    module = reload_config()

    assert module.API_KEY == "test-canonical-key"


def test_missing_api_key_allows_database_backed_credentials(monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YC_API_KEY", raising=False)

    module = reload_config()

    assert module.API_KEY is None


def test_model_catalog_contains_only_verified_yandex_models():
    assert "alice-lite" not in config.TEXT_MODELS
    assert "aliceai-llm" in config.TEXT_MODELS
    assert "yandexgpt-5.1" in config.TEXT_MODELS
    assert "yandexgpt-5-lite" in config.TEXT_MODELS
    assert "qwen3-235b-a22b-fp8" in config.TEXT_MODELS
