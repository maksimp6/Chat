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

def test_list_conversations_rejects_invalid_owner_token(monkeypatch):
    from treasury_identity import TreasuryIdentityError

    def raise_invalid_token(*, required=False):
        raise TreasuryIdentityError("invalid authenticated owner token")

    monkeypatch.setattr(app_module, "get_current_owner_id", raise_invalid_token)

    client = app_module.app.test_client()
    response = client.get(
        "/api/conversations",
        headers={"X-Alice-User-Token": "not-a-valid-token"},
    )

    assert response.status_code == 401
    assert response.get_json()["error"] == "invalid authenticated owner token"
