import os
import sqlite3
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
    init_github_accounts_table,
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
            init_github_accounts_table()  # app.py does this at startup
            yield
        finally:
            db.DB_PATH = old


@pytest.fixture
def github_env(monkeypatch):
    monkeypatch.setenv("ALICE_GITHUB_CLIENT_ID", "client-id")
    monkeypatch.setenv("ALICE_GITHUB_CLIENT_SECRET", "client-secret-value")
    monkeypatch.setenv("ALICE_GITHUB_REDIRECT_URI", "https://alice.test/auth/github/callback")
    monkeypatch.delenv("ALICE_GITHUB_ALLOWED_IDS", raising=False)
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


def _fake_github(monkeypatch, account=None, token_status=200, user_status=200, raise_error=None):
    calls = []

    def fake_post(url, data=None, headers=None, timeout=None):
        calls.append(("post", url, data))
        if raise_error is not None:
            raise raise_error
        return _FakeResponse(token_status, {"access_token": "gho_secret_access"})

    def fake_get(url, headers=None, timeout=None):
        calls.append(("get", url, headers))
        return _FakeResponse(user_status, account or {"id": 42, "login": "octocat"})

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
    monkeypatch.setenv("ALICE_GITHUB_ALLOWED_IDS", "293531601")
    # The allowlisted login, reassigned to a different account, is not enough.
    _fake_github(monkeypatch, {"id": 99, "login": "maksimp6"})
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
    monkeypatch.setenv("ALICE_GITHUB_ALLOWED_IDS", "12, 293531601")
    # A renamed account keeps its id and keeps access.
    _fake_github(monkeypatch, {"id": 293531601, "login": "maksimp6-renamed"})
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


def _callback(client, query):
    return client.get(f"/auth/github/callback?{query}", base_url="https://alice.test")


@pytest.mark.parametrize(
    "fake",
    [
        {"user_status": 500},
        {"raise_error": github_auth.requests.ConnectionError("down")},
        {"account": {"id": 5, "login": ""}},
    ],
)
def test_github_lookup_failures_do_not_sign_in(github_env, temp_db, monkeypatch, fake):
    _fake_github(monkeypatch, **fake)
    client = _app()
    state = _start_login(client)
    response = _callback(client, f"code=abc&state={state}")
    assert response.status_code == 502
    assert "alice_user_token=" not in " ".join(response.headers.getlist("Set-Cookie"))


def test_callback_handles_denied_and_missing_code(github_env, temp_db, monkeypatch):
    calls = _fake_github(monkeypatch)
    client = _app()
    state = _start_login(client)
    assert _callback(client, f"error=access_denied&state={state}").status_code == 400
    assert _callback(client, f"state={state}").status_code == 400
    assert calls == []


def test_callback_disabled_without_oauth_app(monkeypatch):
    monkeypatch.delenv("ALICE_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("ALICE_GITHUB_CLIENT_SECRET", raising=False)
    assert _app().get("/auth/github/callback?code=a&state=b").status_code == 404


def test_default_redirect_uri_uses_request_host(github_env, monkeypatch):
    monkeypatch.delenv("ALICE_GITHUB_REDIRECT_URI")
    response = _app().get("/auth/github/login", base_url="https://host.test")
    query = parse_qs(urlsplit(response.headers["Location"]).query)
    assert query["redirect_uri"] == ["https://host.test/auth/github/callback"]


def test_invalid_user_token_is_treated_as_signed_out(github_env, temp_db):
    import app as app_module

    me = _app()
    me.set_cookie("alice_user_token", "not-a-valid-token")
    assert me.get("/api/auth/me").get_json()["authenticated"] is False

    index = app_module.app.test_client()
    index.set_cookie("alice_user_token", "not-a-valid-token")
    html = index.get("/").get_data(as_text=True)
    assert "Войти через GitHub" in html


def test_github_user_cannot_gain_second_github_account(temp_db):
    first = sign_in_with_github(1, "account-a")
    second = sign_in_with_github(2, "account-b", first["user_id"])
    assert second["user_id"] != first["user_id"]
    assert second["new_user"] is True
    assert get_github_login(first["user_id"]) == "account-a"
    assert get_github_login(second["user_id"]) == "account-b"


def test_bootstrap_cannot_mint_token_for_promoted_user(temp_db):
    anon = register_anonymous_user("web-installation-0003", {})
    promoted = sign_in_with_github(3, "octocat", anon["user_id"])
    assert promoted["user_id"] == anon["user_id"]

    # After logout the browser bootstraps its old installation id again.
    fresh = register_anonymous_user("web-installation-0003", {})
    assert fresh["new_user"] is True
    assert fresh["user_id"] != promoted["user_id"]
    assert authenticate_user_token(fresh["auth_token"]) == fresh["user_id"]

    # Guessing a GitHub-style installation id gives a separate anonymous user.
    github_user = sign_in_with_github(293531601, "maksimp6")
    guessed = register_anonymous_user("github-293531601", {})
    assert guessed["user_id"] != github_user["user_id"]


def test_bootstrap_refuses_rows_that_are_no_longer_anonymous(temp_db):
    anon = register_anonymous_user("web-installation-0004", {})
    conn = db.get_conn()
    conn.execute("UPDATE users SET status = 'github' WHERE id = ?", (anon["user_id"],))
    conn.commit()
    conn.close()
    with pytest.raises(ValueError):
        register_anonymous_user("web-installation-0004", {})


def test_prior_bootstrap_with_github_style_id_does_not_block_sign_in(temp_db):
    register_anonymous_user("github-293531601", {})
    identity = sign_in_with_github(293531601, "maksimp6")
    assert authenticate_user_token(identity["auth_token"]) == identity["user_id"]


def test_concurrent_first_sign_in_retries_as_existing_link(temp_db, monkeypatch):
    import user_identity

    real_link = user_identity._link_github_account
    calls = []

    def racing_link(*args):
        calls.append(args)
        if len(calls) == 1:
            # Another callback links the same GitHub account first.
            winner = real_link("77", "octocat", None, "winner-token")
            assert winner[1] is True
            raise sqlite3.IntegrityError("UNIQUE constraint failed")
        return real_link(*args)

    monkeypatch.setattr(user_identity, "_link_github_account", racing_link)
    identity = sign_in_with_github(77, "octocat")
    assert identity["new_user"] is False
    assert authenticate_user_token(identity["auth_token"]) == identity["user_id"]


def test_link_rolls_back_on_unique_violation(temp_db, monkeypatch):
    import uuid as uuid_module

    import user_identity

    existing = register_anonymous_user("web-installation-0005", {})
    fixed = uuid_module.UUID(existing["user_id"])
    monkeypatch.setattr(user_identity.uuid, "uuid4", lambda: fixed)

    with pytest.raises(sqlite3.IntegrityError):
        user_identity._link_github_account("99", "octocat", None, "token")
    assert get_github_login(existing["user_id"]) is None


def test_logout_works_after_gate_session_expired(github_env, monkeypatch):
    monkeypatch.setenv("ALICE_REQUIRE_SHORT_TOKEN", "1")
    monkeypatch.setenv("ALICE_SHORT_TOKEN", "unit-test-token")
    response = _app().post("/auth/logout")
    assert response.status_code == 200
    assert "alice_user_token=;" in " ".join(response.headers.getlist("Set-Cookie"))


def test_stale_bootstrap_cannot_overwrite_promoted_user_token(temp_db, monkeypatch):
    import user_identity

    anon = register_anonymous_user("web-installation-0006", {})
    real_get_conn = user_identity.get_conn
    promoted = {}

    class _PromoteAfterRead:
        """Runs a GitHub sign-in between bootstrap's read and its write."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, params=()):
            if sql.lstrip().startswith("UPDATE users") and not promoted:
                promoted["started"] = True
                promoted.update(sign_in_with_github(4, "octocat", anon["user_id"]))
            return self._conn.execute(sql, params)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    monkeypatch.setattr(user_identity, "get_conn", lambda: _PromoteAfterRead(real_get_conn()))
    with pytest.raises(ValueError):
        register_anonymous_user("web-installation-0006", {})
    monkeypatch.setattr(user_identity, "get_conn", real_get_conn)

    assert promoted["user_id"] == anon["user_id"]
    assert authenticate_user_token(promoted["auth_token"]) == anon["user_id"]
