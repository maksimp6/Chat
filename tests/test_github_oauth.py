import os
import tempfile
from urllib.parse import parse_qs, urlsplit

import pytest
from flask import Flask

import db
import short_token_auth
from identity import github_oauth as github_auth
from user_identity import (
    authenticate_user_token,
    get_github_login,
    register_anonymous_user,
    sign_in_with_github,
)


class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture
def temp_db():
    with tempfile.TemporaryDirectory() as tmp:
        old = db.DB_PATH
        db.DB_PATH = os.path.join(tmp, "users.db")
        try:
            yield
        finally:
            db.DB_PATH = old


@pytest.fixture
def github_env(monkeypatch):
    monkeypatch.setenv("ALICE_GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("ALICE_GITHUB_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("ALICE_GITHUB_REDIRECT_URI", "https://alice.test/auth/github/callback")
    monkeypatch.delenv("ALICE_GITHUB_ALLOWED_LOGINS", raising=False)
    monkeypatch.delenv("ALICE_REQUIRE_SHORT_TOKEN", raising=False)
    monkeypatch.delenv("ALICE_SHORT_TOKEN", raising=False)


def _app():
    app = Flask(__name__)
    app.config["TESTING"] = True
    short_token_auth.install_short_token_auth(app)
    app.register_blueprint(github_auth.github_auth_bp)

    @app.get("/")
    def index():
        return "ok"

    return app.test_client()


def _fake_github(monkeypatch, account=None, token_status=200):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(("post", url, data))
        return _FakeResponse(token_status, {"access_token": "gho_secret_access"})

    def fake_get(url, headers=None, timeout=None):
        calls.append(("get", url, headers))
        return _FakeResponse(200, account or {"id": 42, "login": "octocat"})

    monkeypatch.setattr(github_auth.requests, "post", fake_post)
    monkeypatch.setattr(github_auth.requests, "get", fake_get)
    return calls


def _start_login(client):
    response = client.get("/auth/github/login", base_url="https://alice.test")
    assert response.status_code == 302
    location = urlsplit(response.headers["Location"])
    assert location.netloc == "github.com"
    query = parse_qs(location.query)
    assert query["client_id"] == ["client-id"]
    assert query["redirect_uri"] == ["https://alice.test/auth/github/callback"]
    assert "client-secret-value" not in response.headers["Location"]
    return query["state"][0]


def test_login_disabled_without_oauth_app(monkeypatch):
    monkeypatch.delenv("ALICE_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("ALICE_GITHUB_CLIENT_SECRET", raising=False)
    assert _app().get("/auth/github/login").status_code == 404


def test_login_moves_to_callback_host_first(github_env):
    response = _app().get("/auth/github/login", base_url="https://other.test")
    assert response.status_code == 302
    assert response.headers["Location"] == "https://alice.test/auth/github/login"


def test_callback_rejects_missing_or_wrong_state(github_env, temp_db, monkeypatch):
    calls = _fake_github(monkeypatch)
    client = _app()
    _start_login(client)
    response = client.get(
        "/auth/github/callback?code=abc&state=forged", base_url="https://alice.test"
    )
    assert response.status_code == 400
    assert calls == []


def test_callback_signs_in_and_links_current_anonymous_user(github_env, temp_db, monkeypatch):
    calls = _fake_github(monkeypatch)
    anon = register_anonymous_user("web-installation-0001", {})
    client = _app()
    client.set_cookie("alice_user_token", anon["auth_token"], domain="alice.test")
    state = _start_login(client)

    response = client.get(
        f"/auth/github/callback?code=abc&state={state}", base_url="https://alice.test"
    )

    assert response.status_code == 302
    assert response.headers["Location"] == "/"
    assert calls[0][2]["code"] == "abc"
    cookies = response.headers.getlist("Set-Cookie")
    token_cookie = next(c for c in cookies if c.startswith("alice_user_token="))
    new_token = token_cookie.split(";", 1)[0].split("=", 1)[1]
    assert "HttpOnly" in token_cookie and "Secure" in token_cookie
    assert authenticate_user_token(new_token) == anon["user_id"]
    assert get_github_login(anon["user_id"]) == "octocat"
    body = response.get_data(as_text=True)
    assert "gho_secret_access" not in body and "gho_secret_access" not in str(cookies)

    me = client.get("/api/auth/me", base_url="https://alice.test").get_json()
    assert me["authenticated"] is True
    assert me["github_login"] == "octocat"


def test_returning_github_user_gets_same_user(temp_db):
    first = sign_in_with_github(7, "octocat")
    other_anon = register_anonymous_user("web-installation-0002", {})
    second = sign_in_with_github(7, "octocat-renamed", other_anon["user_id"])
    assert first["new_user"] is True
    assert second["user_id"] == first["user_id"]
    assert get_github_login(first["user_id"]) == "octocat-renamed"
    assert authenticate_user_token(second["auth_token"]) == first["user_id"]


def test_invalid_github_account_is_rejected(temp_db):
    with pytest.raises(ValueError):
        sign_in_with_github("not-a-number", "octocat")


def test_gate_login_page_offers_github_sign_in(github_env, monkeypatch):
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "1")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "unit-test-token")
    client = _app()

    page = client.get("/", headers={"Accept": "text/html"})
    assert page.status_code == 401
    assert "/auth/github/login" in page.get_data(as_text=True)
    assert "unit-test-token" not in page.get_data(as_text=True)

    api = client.get("/api/auth/me", headers={"Accept": "text/html"})
    assert api.status_code == 401
    assert api.get_json() == {"error": "authentication required"}

    login = client.get("/auth/github/login", base_url="https://alice.test")
    assert login.status_code == 302


def test_gate_rejects_login_not_in_allowlist(github_env, temp_db, monkeypatch):
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "1")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "unit-test-token")
    monkeypatch.setenv("ALICE_GITHUB_ALLOWED_LOGINS", "maksimp6")
    _fake_github(monkeypatch, {"id": 99, "login": "stranger"})
    client = _app()
    state = _start_login(client)

    response = client.get(
        f"/auth/github/callback?code=abc&state={state}", base_url="https://alice.test"
    )

    assert response.status_code == 403
    cookies = " ".join(response.headers.getlist("Set-Cookie"))
    assert "alice_user_token=" not in cookies
    assert short_token_auth.COOKIE_NAME + "=" not in cookies
    assert client.get("/", base_url="https://alice.test").status_code == 401


def test_gate_opens_for_allowlisted_login(github_env, temp_db, monkeypatch):
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "1")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "unit-test-token")
    monkeypatch.setenv("ALICE_GITHUB_ALLOWED_LOGINS", "someone, MaksimP6")
    _fake_github(monkeypatch, {"id": 293531601, "login": "maksimp6"})
    client = _app()
    state = _start_login(client)

    response = client.get(
        f"/auth/github/callback?code=abc&state={state}", base_url="https://alice.test"
    )

    assert response.status_code == 302
    assert client.get("/", base_url="https://alice.test").status_code == 200


def test_failed_code_exchange_does_not_sign_in(github_env, temp_db, monkeypatch):
    _fake_github(monkeypatch, token_status=401)
    client = _app()
    state = _start_login(client)
    response = client.get(
        f"/auth/github/callback?code=abc&state={state}", base_url="https://alice.test"
    )
    assert response.status_code == 502
    assert "alice_user_token=" not in " ".join(response.headers.getlist("Set-Cookie"))


def test_logout_clears_identity_cookies(github_env):
    response = _app().post("/auth/logout")
    cookies = " ".join(response.headers.getlist("Set-Cookie"))
    assert "alice_user_token=;" in cookies
    assert short_token_auth.COOKIE_NAME + "=;" in cookies


def test_index_shows_github_button_only_when_configured(github_env, monkeypatch):
    import app as app_module

    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert 'id="github-login-btn"' in html
    assert 'href="/auth/github/login"' in html
    assert "Войти через GitHub" in html

    monkeypatch.delenv("ALICE_GITHUB_CLIENT_SECRET")
    html = app_module.app.test_client().get("/").get_data(as_text=True)
    assert 'id="github-login-btn"' not in html
