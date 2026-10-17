"""The Logos model key maps to the Yandex model id that Yandex still knows (#1316).

The registry key is ours and may be renamed. The upstream id is what Yandex serves,
so every request URI must keep using it. Keys that are not in the registry, such as
keys stored in older conversations, pass through unchanged.
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


def test_unregistered_and_legacy_keys_pass_through_unchanged():
    assert config.upstream_model_id("aliceai-llm") == "aliceai-llm"
    assert config.get_model_uri("folder", "aliceai-llm") == "gpt://folder/aliceai-llm/latest"


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
