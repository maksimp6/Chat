import logging

import app as app_module
import mcp_routes


def test_unhandled_backend_exception_is_logged_and_not_swallowed(monkeypatch, caplog):
    def explode(*args, **kwargs):
        raise RuntimeError("deliberate swallowed-error regression")

    # /api/conversations is owned by the MCP routes blueprint, so patch the
    # dependency where that route resolves it rather than the app module alias.
    monkeypatch.setattr(mcp_routes, "get_conversations", explode)
    client = app_module.app.test_client()

    with caplog.at_level(logging.ERROR, logger="alice_app"):
        response = client.get("/api/conversations")

    assert response.status_code == 500
    assert response.get_json()["code"] == "UNHANDLED_EXCEPTION"
    assert "deliberate swallowed-error regression" in caplog.text
    assert "[ERROR] Unhandled application exception" in caplog.text
