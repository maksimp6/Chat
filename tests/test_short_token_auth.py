from flask import Flask

import short_token_auth


def _client(monkeypatch, token=None, require=True, preview=False):
    if token is None:
        monkeypatch.delenv("ALICE_SHORT_TOKEN", raising=False)
    else:
        monkeypatch.setenv("ALICE_SHORT_TOKEN", token)
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "1" if require else "0")
    if preview:
        monkeypatch.setenv("ALICE_PREVIEW", "1")
    else:
        monkeypatch.delenv("ALICE_PREVIEW", raising=False)

    app = Flask(__name__)
    app.config["TESTING"] = True
    short_token_auth.install_short_token_auth(app)

    @app.get("/")
    def index():
        return "ok"

    @app.get("/preview/<path:preview_path>")
    def preview(preview_path):
        return f"preview:{preview_path}"

    @app.get("/healthz")
    def healthz():
        return {"status": "ok"}

    return app.test_client()


def test_missing_runtime_secret_fails_closed(monkeypatch):
    client = _client(monkeypatch, token=None, require=True)
    response = client.get("/")
    assert response.status_code == 503
    assert "ALICE_SHORT_TOKEN" not in response.get_data(as_text=True)


def test_invalid_token_is_rejected_without_disclosure(monkeypatch):
    client = _client(monkeypatch, token="unit-test-token", require=True)
    response = client.get("/wrong-token")
    assert response.status_code == 401
    assert "unit-test-token" not in response.get_data(as_text=True)


def test_valid_token_path_is_served_without_redirect(monkeypatch):
    client = _client(monkeypatch, token="unit-test-token", require=True)
    response = client.get("/unit-test-token")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"
    assert response.headers.get("Location") is None
    assert "Set-Cookie" in response.headers
    assert "Referrer-Policy" in response.headers
    assert "unit-test-token" not in response.get_data(as_text=True)

    response = client.get("/")
    assert response.status_code == 200


def test_token_prefix_preserves_nested_preview_path_without_redirect(monkeypatch):
    client = _client(monkeypatch, token="unit-test-token", require=True)
    response = client.get("/unit-test-token/preview/pr-235")
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "preview:pr-235"
    assert response.headers.get("Location") is None


def test_forwarded_token_prefix_is_authenticated_after_proxy_rewrite(monkeypatch):
    client = _client(monkeypatch, token="unit-test-token", require=True, preview=True)
    response = client.get(
        "/",
        headers={"X-Forwarded-Uri": "/unit-test-token/preview/pr-235/"},
    )
    assert response.status_code == 200
    assert response.get_data(as_text=True) == "ok"
    assert "Set-Cookie" in response.headers


def test_health_endpoint_remains_public_for_deployment_checks(monkeypatch):
    client = _client(monkeypatch, token="unit-test-token", require=True)
    response = client.get("/healthz")
    assert response.status_code == 200


def test_preview_requires_token_when_auth_is_enabled(monkeypatch):
    client = _client(monkeypatch, token="unit-test-token", require=True, preview=True)
    response = client.get("/")
    assert response.status_code == 401


def test_auth_can_be_disabled_for_local_development(monkeypatch):
    client = _client(monkeypatch, token=None, require=False, preview=True)
    response = client.get("/")
    assert response.status_code == 200
