"""The registry key is ours. The upstream id is the Yandex model id that every request URI uses.

Keys that are not in the registry pass through unchanged.
"""

import pytest

from core import config


def test_registry_keys_map_to_the_documented_yandex_model_ids():
    assert config.upstream_model_id("yandexgpt-5-lite") == "yandexgpt-5-lite"
    assert config.upstream_model_id("yandexgpt-5-pro") == "yandexgpt/latest"
    assert config.upstream_model_id("yandexgpt-5.1") == "yandexgpt/rc"
    assert config.upstream_model_id("gpt-oss-20b") == "gpt-oss-20b"


def test_model_uri_uses_the_upstream_id_and_keeps_its_version():
    assert config.get_model_uri("folder", "yandexgpt-5-lite") == "gpt://folder/yandexgpt-5-lite/latest"
    assert config.get_model_uri("folder", "yandexgpt-5.1") == "gpt://folder/yandexgpt/rc"
    assert config.get_model_uri("folder", "gpt-oss-20b") == "gpt://folder/gpt-oss-20b/latest"


def test_unregistered_keys_pass_through_unchanged():
    assert config.upstream_model_id("unregistered-model") == "unregistered-model"
    assert (
        config.get_model_uri("folder", "unregistered-model")
        == "gpt://folder/unregistered-model/latest"
    )


def test_unknown_model_info_is_an_error_not_a_fallback():
    with pytest.raises(ValueError, match="no tariff"):
        config.get_model_info("does-not-exist")


def test_creating_a_conversation_defaults_to_the_documented_pro_model(monkeypatch):
    from flask import Flask

    import chat.mcp_routes as mcp_routes

    created = []

    class FakeClient:
        def __init__(self, _config):
            pass

        def create_conversation(self, execution_trace=None):
            return {"id": "yandex-conv-1"}

    monkeypatch.setattr(mcp_routes, "LogosClient", FakeClient)
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: None)
    monkeypatch.setattr(
        mcp_routes,
        "create_conversation",
        lambda conv_id, title, model: created.append((conv_id, title, model)),
    )

    app = Flask(__name__)
    app.register_blueprint(mcp_routes.mcp_bp)
    response = app.test_client().post("/api/conversations", json={"title": "Test"})

    assert response.status_code == 201
    body = response.get_json()
    assert body["model"] == "yandexgpt-5-pro"
    assert created == [(body["id"], "Test", "yandexgpt-5-pro")]


def test_live_model_list_shows_the_registry_key_for_an_upstream_id():
    from provider_accounts.model_discovery import ModelDiscovery

    entry = ModelDiscovery._normalize_model({"id": "gpt://folder/yandexgpt-5-lite/latest"})

    assert entry is not None
    assert entry["id"] == "yandexgpt-5-lite"
    assert entry["input"] == config.TEXT_MODELS["yandexgpt-5-lite"]["input"]


def test_live_model_list_keeps_ids_that_have_no_registry_entry():
    from provider_accounts.model_discovery import ModelDiscovery

    entry = ModelDiscovery._normalize_model({"id": "gpt://folder/unregistered-model/latest"})

    assert entry is not None
    assert entry["id"] == "unregistered-model"
