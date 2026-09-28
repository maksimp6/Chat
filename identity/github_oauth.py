"""Sign in to Alice Pro with a GitHub account (OAuth App web flow).

Configuration (all runtime environment variables):

- ALICE_GITHUB_CLIENT_ID / ALICE_GITHUB_CLIENT_SECRET: the GitHub OAuth App.
  GitHub sign-in is disabled unless both are set.
- ALICE_GITHUB_REDIRECT_URI: the callback URL registered in the OAuth App, e.g.
  https://maxxxpavlov.ru/auth/github/callback. Defaults to this host's
  /auth/github/callback.
- ALICE_GITHUB_ALLOWED_IDS: comma-separated numeric GitHub account ids (not
  logins, which can be renamed and reassigned). When the short-token gate is on
  (ALICE_REQUIRE_SHORT_TOKEN), only these accounts may sign in, and signing in
  also opens the gate. When the gate is off, any GitHub account may sign in.

The GitHub access token is used once to read the account id and login and is
never stored or logged.
"""

from __future__ import annotations

import hmac
import logging
import os
import secrets
from urllib.parse import urlencode, urlsplit

import requests
from flask import Blueprint, jsonify, make_response, redirect, request

from short_token_auth import COOKIE_NAME as SHORT_TOKEN_COOKIE
from short_token_auth import _enabled as short_token_required
from short_token_auth import grant_short_token_session
from treasury_identity import TreasuryIdentityError, get_current_owner_id
from user_identity import get_github_login, sign_in_with_github

logger = logging.getLogger("alice_github_auth")

github_auth_bp = Blueprint("github_auth", __name__)

AUTHORIZE_URL = "https://github.com/login/oauth/authorize"
TOKEN_URL = "https://github.com/login/oauth/access_token"
USER_URL = "https://api.github.com/user"
STATE_COOKIE = "alice_github_oauth_state"
USER_TOKEN_COOKIE = "alice_user_token"
STATE_MAX_AGE = 10 * 60
HTTP_TIMEOUT = 10
CALLBACK_PATH = "/auth/github/callback"
LOGIN_PATH = "/auth/github/login"


def _client_id() -> str:
    return os.environ.get("ALICE_GITHUB_CLIENT_ID", "").strip()


def _client_secret() -> str:
    return os.environ.get("ALICE_GITHUB_CLIENT_SECRET", "").strip()


def github_login_enabled() -> bool:
    return bool(_client_id() and _client_secret())


def login_path() -> str:
    return LOGIN_PATH


def _allowed_ids() -> set[str]:
    raw = os.environ.get("ALICE_GITHUB_ALLOWED_IDS", "")
    return {item.strip() for item in raw.split(",") if item.strip()}


def _account_allowed(github_id) -> bool:
    if not short_token_required():
        return True
    return str(github_id) in _allowed_ids()


def _redirect_uri() -> str:
    configured = os.environ.get("ALICE_GITHUB_REDIRECT_URI", "").strip()
    if configured:
        return configured
    return request.host_url.rstrip("/") + CALLBACK_PATH


def _is_secure() -> bool:
    return request.is_secure or _redirect_uri().startswith("https://")


def _page(message: str, status: int):
    html = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>Alice Pro</title>'
        f'</head><body style="font-family:sans-serif"><p>{message}</p>'
        f'<p><a href="{LOGIN_PATH}">Попробовать снова</a></p></body></html>'
    )
    return html, status, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


def _current_user_id() -> str | None:
    try:
        return get_current_owner_id(required=False)
    except TreasuryIdentityError:
        return None


@github_auth_bp.route(LOGIN_PATH, methods=["GET"])
def github_login():
    if not github_login_enabled():
        return jsonify({"error": "github_login_not_configured"}), 404

    # The OAuth App has a single callback host; start there so the state cookie
    # is set on the same host that receives the callback.
    callback_host = urlsplit(_redirect_uri()).netloc
    if callback_host and callback_host != request.host:
        scheme = urlsplit(_redirect_uri()).scheme or "https"
        return redirect(f"{scheme}://{callback_host}{LOGIN_PATH}", code=302)

    state = secrets.token_urlsafe(32)
    query = urlencode(
        {
            "client_id": _client_id(),
            "redirect_uri": _redirect_uri(),
            "scope": "read:user",
            "state": state,
            "allow_signup": "true",
        }
    )
    response = redirect(f"{AUTHORIZE_URL}?{query}", code=302)
    response.set_cookie(
        STATE_COOKIE,
        state,
        max_age=STATE_MAX_AGE,
        httponly=True,
        secure=_is_secure(),
        samesite="Lax",
        path="/auth/github/",
    )
    response.headers["Cache-Control"] = "no-store"
    return response


def _fetch_github_account(code: str) -> dict | None:
    try:
        token_response = requests.post(
            TOKEN_URL,
            data={
                "client_id": _client_id(),
                "client_secret": _client_secret(),
                "code": code,
                "redirect_uri": _redirect_uri(),
            },
            headers={"Accept": "application/json"},
            timeout=HTTP_TIMEOUT,
        )
        access_token = (token_response.json() or {}).get("access_token")
        if token_response.status_code != 200 or not access_token:
            logger.warning(
                "[GITHUB_AUTH] code exchange failed: status=%s", token_response.status_code
            )
            return None
        user_response = requests.get(
            USER_URL,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {access_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            timeout=HTTP_TIMEOUT,
        )
        if user_response.status_code != 200:
            logger.warning("[GITHUB_AUTH] user lookup failed: status=%s", user_response.status_code)
            return None
        account = user_response.json() or {}
    except (requests.RequestException, ValueError) as exc:
        logger.warning("[GITHUB_AUTH] GitHub request failed: %s", type(exc).__name__)
        return None
    if not account.get("id") or not account.get("login"):
        return None
    return {"id": account["id"], "login": str(account["login"])}


@github_auth_bp.route(CALLBACK_PATH, methods=["GET"])
def github_callback():
    if not github_login_enabled():
        return jsonify({"error": "github_login_not_configured"}), 404

    expected_state = request.cookies.get(STATE_COOKIE, "")
    state = request.args.get("state", "")
    if not expected_state or not state or not hmac.compare_digest(expected_state, state):
        return _page("Вход через GitHub не удался: сессия входа устарела.", 400)

    if request.args.get("error"):
        return _page("Вход через GitHub отменён.", 400)

    code = request.args.get("code", "")
    if not code:
        return _page("Вход через GitHub не удался.", 400)

    account = _fetch_github_account(code)
    if account is None:
        return _page("Не удалось получить аккаунт GitHub. Попробуйте ещё раз.", 502)

    if not _account_allowed(account["id"]):
        logger.info("[GITHUB_AUTH] rejected account not in allowlist")
        response = make_response(_page("Этому аккаунту GitHub вход в Alice Pro не разрешён.", 403))
        response.delete_cookie(STATE_COOKIE, path="/auth/github/")
        return response

    identity = sign_in_with_github(account["id"], account["login"], _current_user_id())
    logger.info("[GITHUB_AUTH] signed in new_user=%s", identity["new_user"])

    response = redirect("/", code=302)
    response.delete_cookie(STATE_COOKIE, path="/auth/github/")
    response.set_cookie(
        USER_TOKEN_COOKIE,
        identity["auth_token"],
        httponly=True,
        secure=_is_secure(),
        samesite="Lax",
        path="/",
    )
    grant_short_token_session(response)
    response.headers["Cache-Control"] = "no-store"
    return response


@github_auth_bp.route("/api/auth/me", methods=["GET"])
def auth_me():
    user_id = _current_user_id()
    github_login = get_github_login(user_id) if user_id else None
    return jsonify(
        {
            "authenticated": bool(github_login),
            "github_login": github_login,
            "github_login_enabled": github_login_enabled(),
        }
    )


@github_auth_bp.route("/auth/logout", methods=["POST"])
def logout():
    response = jsonify({"status": "signed_out"})
    response.delete_cookie(USER_TOKEN_COOKIE, path="/")
    response.delete_cookie(SHORT_TOKEN_COOKIE, path="/")
    return response
