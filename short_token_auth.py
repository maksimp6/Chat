"""Runtime short-token authentication for production Alice Pro.

The token is supplied only through ALICE_SHORT_TOKEN. Preview deployments bypass
this guard so their existing path-based URLs keep working.
"""

from __future__ import annotations

import hmac
import os
from typing import Optional

from flask import Flask, jsonify, redirect, request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer


COOKIE_NAME = "alice_short_token_session"
COOKIE_SALT = "alice-pro-short-token-v1"
DEFAULT_MAX_AGE = 12 * 60 * 60
_PUBLIC_PATHS = frozenset({"/healthz"})


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


def _token_path_matches(token: str) -> bool:
    candidate = request.path.strip("/")
    return bool(candidate) and "/" not in candidate and hmac.compare_digest(candidate, token)


def install_short_token_auth(app: Flask) -> None:
    """Install fail-closed production auth while leaving previews unchanged."""

    @app.before_request
    def _short_token_guard():
        if not _enabled() or os.environ.get("ALICE_PREVIEW") == "1":
            return None

        if request.path in _PUBLIC_PATHS:
            return None

        token = _token()
        if not token:
            return jsonify({"error": "authentication is not configured"}), 503

        if _token_path_matches(token):
            session_value = _serializer(token).dumps({"authenticated": True})
            response = redirect("/", code=303)
            response.set_cookie(
                COOKIE_NAME,
                session_value,
                max_age=_max_age(),
                httponly=True,
                secure=request.is_secure,
                samesite="Lax",
                path="/",
            )
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["Cache-Control"] = "no-store"
            return response

        if _has_valid_session(token, request.cookies.get(COOKIE_NAME)):
            return None

        return jsonify({"error": "authentication required"}), 401
