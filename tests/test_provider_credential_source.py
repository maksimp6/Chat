from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_config_has_no_yandex_api_key_source():
    config = (ROOT / "config.py").read_text()
    assert 'os.getenv("YANDEX_API_KEY")' not in config
    assert 'os.getenv("YC_API_KEY")' not in config
    assert "API_KEY = " not in config


def test_yandex_client_does_not_read_config_api_key():
    client = (ROOT / "yandex_client.py").read_text()
    assert "config.API_KEY" not in client
    assert 'Authorization"] = "Api-Key " + config.API_KEY' not in client


def test_yandex_request_path_requires_runtime_credential_store():
    mixin = (ROOT / "yandex_client_modules/request_mixin.py").read_text()
    assert "resolve_client_credential(" in mixin
    assert "ALICE_PROVIDER_CREDENTIAL_KEY" in mixin
    assert 'resolve_client_credential(client._config, provider="yandex")' not in mixin


def test_runtime_credential_resolution_has_no_environment_fallback_path():
    provider = (ROOT / "provider_credentials.py").read_text()
    mixin = (ROOT / "yandex_client_modules/request_mixin.py").read_text()
    assert "get_conn()" in mixin
    assert "decrypt_secret" in mixin
    assert "provider_credentials" in provider
