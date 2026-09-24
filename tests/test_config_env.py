import importlib

import config


def reload_config():
    return importlib.reload(config)


def test_provider_api_key_is_not_loaded_from_environment(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "must-not-be-used")
    monkeypatch.setenv("YC_API_KEY", "legacy-must-not-be-used")

    module = reload_config()

    assert not hasattr(module, "API_KEY")


def test_provider_configuration_contains_no_api_key(monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YC_API_KEY", raising=False)

    module = reload_config()

    assert not hasattr(module, "API_KEY")


def test_model_catalog_contains_only_verified_yandex_models():
    assert "alice-lite" not in config.TEXT_MODELS
    assert "aliceai-llm" in config.TEXT_MODELS
    assert "yandexgpt-5.1" in config.TEXT_MODELS
    assert "yandexgpt-5-lite" in config.TEXT_MODELS
    assert "qwen3-235b-a22b-fp8" in config.TEXT_MODELS
