import app as app_module


def test_patch_conversation_model_updates_owned_conversation(monkeypatch):
    calls = []

    monkeypatch.setattr(app_module, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(app_module, "check_access", lambda conv_id, owner_id: True)
    monkeypatch.setattr(
        app_module,
        "update_conversation_model",
        lambda conv_id, model: calls.append((conv_id, model)),
    )

    client = app_module.app.test_client()
    response = client.patch(
        "/api/conversations/conv-1",
        json={"model": "model-1"},
    )

    assert response.status_code == 200
    assert calls == [("conv-1", "model-1")]


def test_patch_conversation_model_rejects_unauthorized_conversation(monkeypatch):
    monkeypatch.setattr(app_module, "get_current_owner_id", lambda required=False: "owner-2")
    monkeypatch.setattr(app_module, "check_access", lambda conv_id, owner_id: False)

    def fail_update(*args):
        raise AssertionError("unauthorized update must not reach persistence")

    monkeypatch.setattr(app_module, "update_conversation_model", fail_update)

    client = app_module.app.test_client()
    response = client.patch(
        "/api/conversations/conv-1",
        json={"model": "model-1"},
    )

    assert response.status_code == 404
    assert response.get_json()["error"] == "conversation_not_found"


def test_create_conversation_uses_only_provider_id(monkeypatch):
    import mcp_routes

    created = []
    mapped = []

    class FakeClient:
        def create_conversation(self, execution_trace=None):
            created.append(execution_trace)
            return {"id": "yandex-conv-123"}

        def bind_conversation(self, local_id, yandex_id):
            mapped.append((local_id, yandex_id))

    monkeypatch.setattr(mcp_routes, "AliceClient", lambda config: FakeClient())
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: None)
    monkeypatch.setattr(mcp_routes, "create_conversation", lambda conv_id, title, model: None)

    client = mcp_routes.mcp_bp
    flask_app = __import__("flask").Flask(__name__)
    flask_app.register_blueprint(client)
    response = flask_app.test_client().post("/api/conversations", json={"model": "aliceai-llm"})

    assert response.status_code == 201
    assert response.get_json()["id"] == "yandex-conv-123"
    assert mapped == [("yandex-conv-123", "yandex-conv-123")]
    assert created and created[0] is not None


def test_create_conversation_does_not_fallback_to_local_uuid(monkeypatch):
    import mcp_routes

    class FakeClient:
        def create_conversation(self, execution_trace=None):
            raise RuntimeError("provider unavailable")

    monkeypatch.setattr(mcp_routes, "AliceClient", lambda config: FakeClient())
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: None)

    flask_app = __import__("flask").Flask(__name__)
    flask_app.register_blueprint(mcp_routes.mcp_bp)
    response = flask_app.test_client().post("/api/conversations", json={"model": "aliceai-llm"})

    assert response.status_code == 502
    payload = response.get_json()
    assert payload["error"] == "conversation_creation_failed"
    assert "id" not in payload
    assert payload["trace"]["errors"]
