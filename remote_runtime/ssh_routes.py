"""HTTP API of the SSH runtime: settings and a connection test."""

from __future__ import annotations

import logging

from flask import Flask, jsonify, request

from remote_runtime.ssh import SSHRuntimeError
from remote_runtime.ssh_settings import public_settings, save_settings, test_connection
from remote_runtime.host import bound, get_current_owner_id

logger = logging.getLogger("logos.remote_runtime.ssh_routes")


def get_ssh_runtime_settings():
    try:
        return jsonify(public_settings())
    except bound.TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to load SSH Runtime settings")
        return jsonify({"error": str(exc)}), 400


def put_ssh_runtime_settings():
    try:
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            return jsonify({"error": "settings must be an object"}), 400
        saved = save_settings(data)
        return jsonify({"status": "ok", "settings": saved})
    except bound.TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except Exception as exc:
        logger.exception("Failed to save SSH Runtime settings")
        return jsonify({"error": str(exc)}), 400


def test_ssh_runtime_connection():
    try:
        owner_id = get_current_owner_id()
        data = request.get_json(silent=True) or {}
        target = str(data.get("target") or "").strip()
        if not target:
            return jsonify({"error": "target is required"}), 400
        result = test_connection(target, owner_id)
        return jsonify(result)
    except bound.TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    except SSHRuntimeError as exc:
        # The SSH error text (for example Permission denied) is what the window shows the user.
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception:
        logger.exception("SSH Runtime connection test failed")
        return jsonify({"success": False, "error": "ssh_connection_test_failed"}), 400


def install(app: Flask) -> None:
    """Serve the SSH runtime API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/ssh-runtime/settings", view_func=get_ssh_runtime_settings, methods=["GET"]
    )
    app.add_url_rule(
        "/api/ssh-runtime/settings", view_func=put_ssh_runtime_settings, methods=["PUT"]
    )
    app.add_url_rule(
        "/api/ssh-runtime/test", view_func=test_ssh_runtime_connection, methods=["POST"]
    )
