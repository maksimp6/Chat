"""Authorization for the whole deployment.

Two rules live here:

* Without any configured authorization (no short-token gate, no administrator token) a remote
  request is refused for the whole project, except public pages and sign-in. A local request
  (loopback, no forwarding header) is still served, so a developer on the host keeps working.
* Resources shared by the whole deployment (provider files and vector stores, the local
  repository listing, MCP servers) need the deployment administrator. An anonymous user identity
  from /api/users/bootstrap is not enough, because anyone can create one.
"""

from __future__ import annotations

import hmac
import os

from flask import Flask, jsonify, request

_LOOPBACK_ADDRESSES = {"127.0.0.1", "::1"}
_FORWARDING_HEADERS = ("X-Forwarded-For", "X-Real-IP", "Forwarded")
_PUBLIC_PATHS = frozenset({"/", "/healthz", "/auth/logout"})
_PUBLIC_PREFIXES = ("/auth/github/", "/static/")


def _short_token_gate_enabled() -> bool:
    return os.getenv("LOGOS_REQUIRE_SHORT_TOKEN", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _administrator_token() -> str:
    return os.getenv("LOGOS_PROVIDER_CREDENTIALS_TOKEN", "").strip()


def deployment_authorization_configured() -> bool:
    return _short_token_gate_enabled() or bool(_administrator_token())


def is_local_request() -> bool:
    # A reverse proxy makes every request arrive from loopback, so a forwarding header means
    # the request did not originate on this host.
    if any(request.headers.get(name) for name in _FORWARDING_HEADERS):
        return False
    return (request.remote_addr or "") in _LOOPBACK_ADDRESSES


def deployment_admin_authorized() -> bool:
    # When the short-token gate is on, it has already authenticated this request before routing.
    if _short_token_gate_enabled():
        return True

    configured = _administrator_token()
    if configured:
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        return bool(token) and hmac.compare_digest(
            token.encode("utf-8"), configured.encode("utf-8")
        )

    return is_local_request()


def deployment_admin_denied():
    if deployment_admin_authorized():
        return None
    return (
        jsonify(
            {
                "error": "deployment_admin_required",
                "detail": "Для общих ресурсов развёртывания нужна аутентификация администратора.",
            }
        ),
        401,
    )


def install_deployment_gate(app: Flask) -> None:
    """Refuse remote requests to the whole project when no authorization is configured."""

    @app.before_request
    def _refuse_remote_without_authorization():
        if request.path in _PUBLIC_PATHS or request.path.startswith(_PUBLIC_PREFIXES):
            return None
        if deployment_authorization_configured() or is_local_request():
            return None
        return (
            jsonify(
                {
                    "error": "authentication_required",
                    "detail": (
                        "Авторизация не настроена: задайте LOGOS_REQUIRE_SHORT_TOKEN "
                        "или LOGOS_PROVIDER_CREDENTIALS_TOKEN."
                    ),
                }
            ),
            401,
        )
