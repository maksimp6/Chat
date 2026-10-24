"""The Logos model key maps to the Yandex model id that Yandex still knows (#1316).

The registry key is ours and may be renamed. The upstream id is what Yandex serves,
so every request URI must keep using it. Known stored aliases resolve to the
official provider IDs; unregistered keys pass through unchanged.
"""

from core import config


def test_registry_keys_map_to_the_unchanged_yandex_model_ids():
    assert config.TEXT_MODELS["logos-runtime"]["upstream"] == "aliceai-llm"
    assert config.TEXT_MODELS["logos-runtime-flash"]["upstream"] == "aliceai-llm-flash"
    assert config.upstream_model_id("logos-runtime") == "aliceai-llm"
    assert config.upstream_model_id("logos-runtime-flash") == "aliceai-llm-flash"


def test_model_uri_uses_the_upstream_id_not_the_registry_key():
    assert config.get_model_uri("folder", "logos-runtime") == "gpt://folder/aliceai-llm/latest"
    assert (
        config.get_model_uri("folder", "logos-runtime-flash")
        == "gpt://folder/aliceai-llm-flash/latest"
    )


def test_stored_aliases_resolve_to_the_official_provider_id():
    assert config.upstream_model_id("logosai-llm") == "aliceai-llm"
    assert config.get_model_uri("folder", "logosai-llm") == "gpt://folder/aliceai-llm/latest"
    assert config.get_model_uri("folder", "logosai-llm-flash") == "gpt://folder/aliceai-llm-flash/latest"


def test_unregistered_keys_pass_through_unchanged():
    assert config.upstream_model_id("unregistered-model") == "unregistered-model"


def test_unknown_model_info_falls_back_to_the_logos_runtime_entry():
    assert config.get_model_info("does-not-exist") == config.TEXT_MODELS["logos-runtime"]


def test_creating_a_conversation_defaults_to_the_logos_model_key(monkeypatch):
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
    assert body["model"] == "logos-runtime"
    assert created == [(body["id"], "Test", "logos-runtime")]


def test_legacy_keys_keep_their_pricing():
    usage = {"input_tokens": 1000, "output_tokens": 1000}
    flash = config.calculate_full_cost("logos-runtime-flash", usage)

    assert config.calculate_full_cost("logosai-llm-flash", usage) == flash
    assert config.calculate_full_cost("logosai-llm", usage) == config.calculate_full_cost(
        "logos-runtime", usage
    )
    assert flash != config.calculate_full_cost("logos-runtime", usage)


def test_legacy_billing_rows_are_calculated_not_unknown():
    from economy.billing import build_ai_billing_item

    item = build_ai_billing_item(
        "logosai-llm-flash", {"input_tokens": 1000, "output_tokens": 1000}, step=1
    )

    assert item["cost_status"] == "calculated"


def test_live_model_list_shows_the_registry_key_for_an_upstream_id():
    from provider_accounts.model_discovery import ModelDiscovery

    entry = ModelDiscovery._normalize_model({"id": "gpt://folder/aliceai-llm/latest"})

    assert entry is not None
    assert entry["id"] == "logos-runtime"
    assert entry["input"] == config.TEXT_MODELS["logos-runtime"]["input"]


def test_live_model_list_keeps_ids_that_have_no_registry_entry():
    from provider_accounts.model_discovery import ModelDiscovery

    entry = ModelDiscovery._normalize_model({"id": "gpt://folder/unregistered-model/latest"})

    assert entry is not None
    assert entry["id"] == "unregistered-model"
