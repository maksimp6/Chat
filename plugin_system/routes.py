"""HTTP API for local plugin discovery and lifecycle management.

Plugins are one process-wide set that runs for every owner, so discovery and every change
to it need the plugin administrator token. Listing needs only an owner.
"""

import hmac
import secrets

from core.env import env_get
from flask import Blueprint, jsonify, request

from economy.treasury_identity import TreasuryIdentityError, get_current_owner_id
from plugin_system.manager import PluginError, plugin_manager

plugin_bp = Blueprint("plugins", __name__, url_prefix="/api/plugins")


def _error(exc: Exception):
    return jsonify({"error": str(exc)}), 400


def _admin_ok() -> bool:
    expected = env_get("LOGOS_PLUGIN_ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("X-Plugin-Admin-Token", "").strip()
    # Compare bytes: compare_digest rejects non-ASCII str, which would be a 500, not a refusal.
    return bool(
        expected
        and supplied
        and secrets.compare_digest(expected.encode("utf-8"), supplied.encode("utf-8"))
    )


def _admin_denied():
    return jsonify({"error": "plugin admin authorization required"}), 401


@plugin_bp.before_request
def require_plugin_identity():
    try:
        get_current_owner_id()
    except TreasuryIdentityError as exc:
        return jsonify({"error": str(exc)}), 401
    return None


@plugin_bp.get("")
def list_plugins():
    plugin_manager.discover()
    return jsonify({"plugins": plugin_manager.list()})


@plugin_bp.post("/discover")
def discover_plugins():
    if not _admin_ok():
        return _admin_denied()
    plugin_manager.discover()
    return jsonify({"plugins": plugin_manager.list()})


@plugin_bp.post("/<plugin_id>/enable")
def enable_plugin(plugin_id: str):
    if not _admin_ok():
        return _admin_denied()
    try:
        return jsonify(plugin_manager.enable(plugin_id))
    except PluginError as exc:
        return _error(exc)


@plugin_bp.post("/<plugin_id>/disable")
def disable_plugin(plugin_id: str):
    if not _admin_ok():
        return _admin_denied()
    try:
        return jsonify(plugin_manager.disable(plugin_id))
    except PluginError as exc:
        return _error(exc)


@plugin_bp.put("/<plugin_id>/config")
def configure_plugin(plugin_id: str):
    if not _admin_ok():
        return _admin_denied()
    try:
        return jsonify(plugin_manager.configure(plugin_id, request.get_json(silent=True) or {}))
    except PluginError as exc:
        return _error(exc)
