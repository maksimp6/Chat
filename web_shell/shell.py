"""The web shell: page, health, readiness, version, caching, errors and storage start-up."""

from __future__ import annotations

import logging
import os
from typing import Any

from flask import Flask, current_app, jsonify, render_template, request
from werkzeug.exceptions import HTTPException

from web_shell.host import (
    bound,
    current_runtime_base_path,
    get_current_owner_id,
    get_donation_url,
    get_store,
    memory_file_path,
    problem,
)
from web_shell.version import boot_version

logger = logging.getLogger("logos.web_shell")

# What this process serves, captured when the shell is installed (at boot).
STATIC_ASSET_VERSION = ""
APP_VERSION: dict[str, Any] = {}


_storage_ready = False


def preview_base_path():
    """Return the request-scoped preview prefix, falling back to deployment config."""
    scoped = current_runtime_base_path()
    if scoped:
        return scoped
    return os.environ.get("LOGOS_PREVIEW_BASE_PATH", "").rstrip("/")


def _handle_unexpected_error(exc):
    if isinstance(exc, HTTPException):
        return exc.get_response()
    logger.exception("[ERROR] Unhandled application exception")
    return problem(
        500,
        "internal_server_error",
        "Внутренняя ошибка сервера",
        extensions={"code": "UNHANDLED_EXCEPTION"},
    )


def _set_web_cache_headers(response):
    # The HTML shell must never pin an older JavaScript dependency graph across deploys.
    if request.path == "/":
        response.headers["Cache-Control"] = "no-store, max-age=0"
    elif request.path.startswith("/static/"):
        response.headers.setdefault("Cache-Control", "no-cache")
    elif request.path.startswith("/api/"):
        # API responses may contain user-specific state, traces, settings, or credentials.
        response.headers["Cache-Control"] = "no-store"
    return response


def _initialize_storage() -> None:
    """Create durable tables once; runs at boot when the memory path is configured."""
    global _storage_ready
    # The composition root lists every unit's tables, in the order they are created.
    for initialize in bound.storage_initializers:
        initialize()
    _storage_ready = True
    logger.info("[BOOT] hello: database initialization completed")


def _memory_path_configured() -> bool:
    try:
        memory_file_path()
    except bound.KristallError:
        return False
    return True


def _initialize_storage_on_first_request():
    # Without a configured memory path the app still imports; the first data request fails.
    if not _storage_ready and request.path not in {"/healthz", "/api/version"}:
        _initialize_storage()


def index():
    # Messages are rendered only by the client (static/chat.js loadHistory).
    return render_template(
        "index.html",
        preview_base_path=preview_base_path(),
        static_version=STATIC_ASSET_VERSION,
        app_revision=APP_VERSION["runtime_revision"],
        donation_url=get_donation_url(),
    )


def healthz():
    """Process liveness only; it does not certify chat persistence or model access."""
    return jsonify({"status": "ok", "scope": "process", "chat_verified": False})


def readyz():
    """Read-only storage readiness; a successful read is not a chat E2E proof."""
    try:
        if not _storage_ready:
            _initialize_storage()
        get_store().get("chat/next-message-id")
    except Exception:
        current_app.logger.exception("Storage readiness failed")
        return jsonify({"status": "unavailable", "scope": "storage_read"}), 503
    return jsonify(
        {"status": "ok", "scope": "storage_read", "chat_write_verified": False}
    )


def loaded_version():
    """Return the version captured by this process at boot, never a later checkout."""
    return jsonify(dict(APP_VERSION))


def bootstrap_local_user():
    """Compatibility endpoint for older clients; no registration or token is needed."""
    return jsonify({"user_id": get_current_owner_id(), "status": "local"})


def install(app: Flask) -> None:
    """Install the shell: version, error and cache policy, storage start-up, shell routes."""
    global STATIC_ASSET_VERSION
    APP_VERSION.update(boot_version(app.root_path))
    STATIC_ASSET_VERSION = APP_VERSION["ui_version"]
    logger.info("[BOOT] hello: Logos backend module loaded")
    app.register_error_handler(Exception, _handle_unexpected_error)
    app.after_request(_set_web_cache_headers)
    if _memory_path_configured():
        _initialize_storage()
    app.before_request(_initialize_storage_on_first_request)
    app.add_url_rule("/", view_func=index)
    app.add_url_rule("/healthz", view_func=healthz, methods=["GET"])
    app.add_url_rule("/readyz", view_func=readyz, methods=["GET"])
    app.add_url_rule("/api/version", view_func=loaded_version, methods=["GET"])
    app.add_url_rule(
        "/api/users/bootstrap", view_func=bootstrap_local_user, methods=["POST"]
    )
