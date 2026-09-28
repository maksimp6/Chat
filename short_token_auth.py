"""Runtime short-token authentication for production and preview Alice Pro.

The token is supplied only through ALICE_SHORT_TOKEN. A token may be used as a
URL path prefix. The prefix is consumed internally; no HTTP redirect is used.
"""

from __future__ import annotations

import os
from typing import Optional
from urllib.parse import urlsplit

from flask import Flask, jsonify, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


COOKIE_NAME = "alice_short_token_session"
COOKIE_SALT = "alice-pro-short-token-v1"
DEFAULT_MAX_AGE = 12 * 60 * 60
_PUBLIC_PATHS = frozenset({"/healthz"})
# GitHub sign-in must be reachable before the visitor has the short token.
_PUBLIC_PREFIXES = ("/auth/github/",)
_TOKEN_PATH_MARKER = "alice.short_token_path_authenticated"
_PROXY_AUTH_HEADER = "X-Alice-Proxy-Authenticated"


def _enabled() -> bool:
    return os.environ.get("ALICE_REQUIRE_SHORT_TOKEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _token() -> str:
    return os.environ.get("ALICE_SHORT_TOKEN", "")


def _serializer(token: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(token, salt=COOKIE_SALT)


def _max_age() -> int:
    raw = os.environ.get("ALICE_SHORT_TOKEN_TTL_SECONDS", str(DEFAULT_MAX_AGE))
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_AGE
    return max(60, value)


def _has_valid_session(token: str, value: Optional[str]) -> bool:
    if not value:
        return False
    try:
        payload = _serializer(token).loads(value, max_age=_max_age())
    except (BadSignature, SignatureExpired):
        return False
    return isinstance(payload, dict) and payload.get("authenticated") is True


def _request_path() -> str:
    """Use the original URI when a trusted reverse proxy supplied it."""
    forwarded_uri = request.headers.get("X-Forwarded-Uri", "")
    if forwarded_uri.startswith("/"):
        return urlsplit(forwarded_uri).path or "/"
    return request.path


def _token_path_remainder(token: str, path: Optional[str] = None) -> Optional[str]:
    """Return the path after an exact token prefix, or None if it does not match."""
    path = _request_path() if path is None else path
    prefix = f"/{token}"
    if not path == prefix and not path.startswith(prefix + "/"):
        return None
    remainder = path[len(prefix) :]
    return remainder or "/"


def grant_short_token_session(response) -> None:
    """Give a visitor authenticated by another trusted method the short-token session."""
    token = _token()
    if not (_enabled() and token):
        return
    response.set_cookie(
        COOKIE_NAME,
        _serializer(token).dumps({"authenticated": True}),
        max_age=_max_age(),
        httponly=True,
        secure=True,
        samesite="Lax",
        path="/",
    )


def _login_page_response():
    """Offer GitHub sign-in to browsers when it is configured."""
    from identity.github_oauth import github_login_enabled, login_path

    if request.method != "GET" or not github_login_enabled():
        return None
    if request.path.startswith("/api/") or not request.accept_mimetypes.accept_html:
        return None
    html = (
        '<!doctype html><html lang="ru"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<title>Alice Pro</title></head><body style="font-family:sans-serif;'
        'display:flex;min-height:90vh;align-items:center;justify-content:center">'
        f'<a href="{login_path()}" style="font-size:1.2em">Войти через GitHub</a>'
        "</body></html>"
    )
    return html, 401, {"Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store"}


class _TokenPathMiddleware:
    """Rewrite token-prefixed URLs before Flask performs route matching."""

    def __init__(self, app):
        self.app = app

    def __call__(self, environ, start_response):
        if _enabled():
            token = _token()
            path = environ.get("PATH_INFO") or "/"
            if token:
                prefix = f"/{token}"
                if path == prefix or path.startswith(prefix + "/"):
                    environ["PATH_INFO"] = path[len(prefix) :] or "/"
                    environ[_TOKEN_PATH_MARKER] = True
        return self.app(environ, start_response)


def install_short_token_auth(app: Flask) -> None:
    """Install fail-closed short-token auth without redirecting token-prefixed URLs."""
    app.wsgi_app = _TokenPathMiddleware(app.wsgi_app)

    @app.after_request
    def _short_token_headers(response):
        if _enabled():
            response.headers["Referrer-Policy"] = "no-referrer"
        if _enabled() and request.environ.get(_TOKEN_PATH_MARKER):
            token = _token()
            if token:
                session_value = _serializer(token).dumps({"authenticated": True})
                response.set_cookie(
                    COOKIE_NAME,
                    session_value,
                    max_age=_max_age(),
                    httponly=True,
                    secure=request.is_secure or _enabled(),
                    samesite="Lax",
                    path="/",
                )
                response.headers["Cache-Control"] = "no-store"
        return response

    @app.before_request
    def _short_token_guard():
        if not _enabled():
            return None

        if request.path in _PUBLIC_PATHS or request.path.startswith(_PUBLIC_PREFIXES):
            return None

        token = _token()
        if not token:
            return jsonify({"error": "authentication is not configured"}), 503

        if request.environ.get(_TOKEN_PATH_MARKER):
            return None

        if request.headers.get(_PROXY_AUTH_HEADER, "").strip().lower() == "true":
            return None

        remainder = _token_path_remainder(token)
        if remainder is not None:
            request.environ[_TOKEN_PATH_MARKER] = True
            return None

        if _has_valid_session(token, request.cookies.get(COOKIE_NAME)):
            return None

        return _login_page_response() or (jsonify({"error": "authentication required"}), 401)
