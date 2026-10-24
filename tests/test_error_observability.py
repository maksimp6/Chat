import logging

import app as app_module
import chat.mcp_routes as mcp_routes


def test_unhandled_backend_exception_is_logged_and_not_swallowed(monkeypatch, caplog):
    def explode(*args, **kwargs):
        raise RuntimeError("deliberate swallowed-error regression")

    # /api/conversations is owned by the MCP routes blueprint, so patch the
    # dependency where that route resolves it rather than the app module alias.
    # Anonymous callers get no conversations, so exercise the owned-list path.
    monkeypatch.setattr(mcp_routes, "get_current_owner_id", lambda required=False: "owner-1")
    monkeypatch.setattr(mcp_routes, "list_owned_conversations", explode)
    client = app_module.app.test_client()

    with caplog.at_level(logging.ERROR, logger="alice_app"):
        response = client.get("/api/conversations")

    assert response.status_code == 500
    assert response.get_json()["code"] == "UNHANDLED_EXCEPTION"
    assert "deliberate swallowed-error regression" in caplog.text
    assert "[ERROR] Unhandled application exception" in caplog.text
