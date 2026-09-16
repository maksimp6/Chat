import importlib

import pytest

import config


def reload_config(monkeypatch):
    return importlib.reload(config)


def test_loads_canonical_yandex_api_key_from_environment(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "test-canonical-key")
    monkeypatch.delenv("YC_API_KEY", raising=False)

    module = reload_config(monkeypatch)

    assert module.API_KEY == "test-canonical-key"


def test_supports_legacy_yc_api_key(monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.setenv("YC_API_KEY", "test-legacy-key")

    module = reload_config(monkeypatch)

    assert module.API_KEY == "test-legacy-key"


def test_canonical_key_takes_precedence(monkeypatch):
    monkeypatch.setenv("YANDEX_API_KEY", "test-canonical-key")
    monkeypatch.setenv("YC_API_KEY", "test-legacy-key")

    module = reload_config(monkeypatch)

    assert module.API_KEY == "test-canonical-key"


def test_missing_api_key_has_clear_error(monkeypatch):
    monkeypatch.delenv("YANDEX_API_KEY", raising=False)
    monkeypatch.delenv("YC_API_KEY", raising=False)

    with pytest.raises(RuntimeError, match="Yandex API key is not configured"):
        reload_config(monkeypatch)
