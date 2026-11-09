"""HTTP API of the SSH runtime: its settings, read-only."""

from __future__ import annotations

import logging

from remote_runtime.ssh_settings import public_settings
from webframe import Application, jsonify

logger = logging.getLogger("logos.remote_runtime.ssh_routes")

# What the SSH window says when the settings cannot be read; the cause goes to the log.
_DETAILS = {
    "ssh_settings_unreadable": (
        "Не удалось прочитать настройки среды SSH. Проверьте сохранённые цели "
        "и known_hosts в настройках на сервере: в окне они только для чтения."
    ),
}


def get_ssh_runtime_settings():
    try:
        return jsonify(public_settings())
    except Exception as exc:
        logger.exception("Failed to load SSH Runtime settings")
        return jsonify(
            {"error": str(exc), "detail": _DETAILS["ssh_settings_unreadable"]}
        ), 400


def install(app: Application) -> None:
    """Serve the SSH runtime API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/ssh-runtime/settings", view_func=get_ssh_runtime_settings, methods=["GET"]
    )
    # Settings are read-only: they are shown, never saved or tested through the API.
