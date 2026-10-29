"""OAuth 2.0 connect flow for a remote MCP server (authorization code + PKCE).

A server whose ``.well-known`` advertises an authorization server can be connected
without pasting a token: the owner taps "Connect", the browser is redirected to the
provider's authorization endpoint, and the callback exchanges the code for an access
token that is stored as the server's ``authorization`` (Bearer). Discovery,
dynamic client registration (RFC 7591) and PKCE (S256) are handled here; no secret
is logged. Tokens are kept by the MCP storage, sealed like any server credential.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from mcp_server import storage
from mcp_server.host import get_store

logger = logging.getLogger("logos.mcp_server.oauth")

HTTP_TIMEOUT = 20
PENDING_TTL = 600  # An unfinished authorization is forgotten after ten minutes.
CLIENT_NAME = "Logos"
DEFAULT_SCOPE = "mcp:tools desktop:auth"


class OAuthError(Exception):
    """A connect step that failed, with a message safe to show the owner."""


def _origin(url: str) -> str:
    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        raise OAuthError("Некорректный адрес MCP-сервера")
    return f"{parts.scheme}://{parts.netloc}"


def _http_json(method: str, url: str, *, data: dict[str, Any] | None = None, form: dict[str, str] | None = None) -> dict[str, Any]:
    headers = {"Accept": "application/json", "User-Agent": "logos-mcp-oauth"}
    body: bytes | None = None
    if form is not None:
        body = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif data is not None:
        body = json.dumps(data).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise OAuthError(f"{_origin(url)} ответил {error.code}: {detail}") from None
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise OAuthError(f"Сервер авторизации не ответил ({type(error).__name__})") from None
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise OAuthError("Сервер авторизации вернул не JSON") from None
    if not isinstance(parsed, dict):
        raise OAuthError("Сервер авторизации вернул неожиданный ответ")
    return parsed


def discover(server_url: str) -> dict[str, Any]:
    """The authorization-server metadata for an MCP server, or raise if it has none."""
    origin = _origin(server_url)
    meta = _http_json("GET", origin + "/.well-known/oauth-authorization-server")
    for field in ("authorization_endpoint", "token_endpoint"):
        if not isinstance(meta.get(field), str):
            raise OAuthError("Сервер не поддерживает OAuth (нет endpoints)")
    return meta


def _client_key(issuer: str, redirect_uri: str) -> str:
    digest = hashlib.sha256((issuer + "\n" + redirect_uri).encode()).hexdigest()[:32]
    return "mcp/oauth/client/" + digest


def _register_client(meta: dict[str, Any], redirect_uri: str, scope: str) -> dict[str, Any]:
    """A client registered for this issuer and redirect URI, reused across connects."""
    issuer = str(meta.get("issuer") or "")
    key = _client_key(issuer, redirect_uri)
    stored = get_store().get(key)
    if isinstance(stored, dict) and stored.get("client_id"):
        return stored
    endpoint = meta.get("registration_endpoint")
    if not isinstance(endpoint, str):
        raise OAuthError("Сервер не поддерживает динамическую регистрацию клиента")
    registration = _http_json(
        "POST",
        endpoint,
        data={
            "client_name": CLIENT_NAME,
            "redirect_uris": [redirect_uri],
            "grant_types": ["authorization_code", "refresh_token"],
            "response_types": ["code"],
            "token_endpoint_auth_method": "none",
            "scope": scope,
        },
    )
    client_id = registration.get("client_id")
    if not isinstance(client_id, str) or not client_id:
        raise OAuthError("Регистрация клиента не вернула client_id")
    record = {
        "client_id": client_id,
        "client_secret": registration.get("client_secret") or "",
        "redirect_uri": redirect_uri,
        "issuer": issuer,
        "registered_at": time.time(),
    }
    with get_store().transaction() as tx:
        tx.set(key, record)
    return record


def _pkce() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    return verifier, challenge


def start(server_id: str, *, owner_id: str, redirect_uri: str) -> str:
    """Begin a connect: returns the authorization URL the browser is sent to."""
    server = storage.get_server(server_id, owner_id=owner_id)
    if not isinstance(server, dict) or not server.get("server_url"):
        raise OAuthError("MCP-сервер не найден или без адреса")
    if not redirect_uri.startswith(("http://", "https://")):
        raise OAuthError("Некорректный redirect_uri")
    meta = discover(str(server["server_url"]))
    scope = " ".join(meta.get("scopes_supported") or []) or DEFAULT_SCOPE
    client = _register_client(meta, redirect_uri, scope)
    verifier, challenge = _pkce()
    state = secrets.token_urlsafe(24)
    pending = {
        "server_id": server_id,
        "owner_id": owner_id,
        "code_verifier": verifier,
        "redirect_uri": redirect_uri,
        "token_endpoint": meta["token_endpoint"],
        "client_id": client["client_id"],
        "client_secret": client.get("client_secret") or "",
        "resource": _origin(str(server["server_url"])),
        "created_at": time.time(),
    }
    with get_store().transaction() as tx:
        tx.set("mcp/oauth/pending/" + state, pending)
    query = {
        "response_type": "code",
        "client_id": client["client_id"],
        "redirect_uri": redirect_uri,
        "scope": scope,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "resource": pending["resource"],
    }
    return meta["authorization_endpoint"] + "?" + urllib.parse.urlencode(query)


def finish(state: str, code: str) -> str:
    """Exchange the code for a token and store it; returns the server's name."""
    key = "mcp/oauth/pending/" + state
    pending = get_store().get(key)
    if not isinstance(pending, dict):
        raise OAuthError("Запрос авторизации не найден или истёк")
    if time.time() - float(pending.get("created_at") or 0) > PENDING_TTL:
        with get_store().transaction() as tx:
            tx.delete(key)
        raise OAuthError("Запрос авторизации истёк, начните заново")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": pending["redirect_uri"],
        "client_id": pending["client_id"],
        "code_verifier": pending["code_verifier"],
        "resource": pending["resource"],
    }
    if pending.get("client_secret"):
        form["client_secret"] = pending["client_secret"]
    token = _http_json("POST", pending["token_endpoint"], form=form)
    access = token.get("access_token")
    if not isinstance(access, str) or not access:
        raise OAuthError("Токен не получен")
    token_type = str(token.get("token_type") or "Bearer").strip() or "Bearer"
    authorization = f"{token_type} {access}"
    updated = storage.update_server(
        pending["server_id"],
        pending["owner_id"],
        {"authorization": authorization},
    )
    with get_store().transaction() as tx:
        tx.delete(key)
    if not updated:
        raise OAuthError("MCP-сервер исчез во время авторизации")
    server = storage.get_server(pending["server_id"], owner_id=pending["owner_id"])
    return str((server or {}).get("name") or (server or {}).get("server_label") or "MCP-сервер")
