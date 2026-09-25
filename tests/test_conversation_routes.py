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


def test_conversations_does_not_require_treasury_token(monkeypatch):
    import mcp_routes

    monkeypatch.setenv("ALICE_OWNER_ID", "preview-owner")
    monkeypatch.setattr(
        mcp_routes,
        "list_owned_conversations",
        lambda owner_id: [{"id": "conv-1", "owner_id": owner_id}],
    )

    app = mcp_routes.mcp_bp
    from flask import Flask

    flask_app = Flask(__name__)
    flask_app.register_blueprint(app)

    client = flask_app.test_client()
    response = client.get(
        "/api/conversations",
        headers={"X-Alice-User-Token": "invalid-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "conversations": [{"id": "conv-1", "owner_id": "preview-owner"}]
    }


def test_conversation_history_does_not_require_treasury_token(monkeypatch):
    monkeypatch.setenv("ALICE_OWNER_ID", "preview-owner")
    monkeypatch.setattr(
        app_module,
        "check_access",
        lambda conv_id, owner_id: owner_id == "preview-owner",
    )
    monkeypatch.setattr(
        app_module,
        "get_messages",
        lambda conv_id: [{"id": "msg-1", "role": "user", "text": "hello"}],
    )

    client = app_module.app.test_client()
    response = client.get(
        "/api/conversations/conv-1/messages",
        headers={"X-Alice-User-Token": "invalid-token"},
    )

    assert response.status_code == 200
    assert response.get_json() == {
        "messages": [{"id": "msg-1", "role": "user", "text": "hello"}]
    }
