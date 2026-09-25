"""HTTP API for local plugin discovery and lifecycle management."""

from flask import Blueprint, jsonify, request

from plugin_manager import PluginError, plugin_manager

plugin_bp = Blueprint("plugins", __name__, url_prefix="/api/plugins")


def _error(exc: Exception):
    return jsonify({"error": str(exc)}), 400


@plugin_bp.get("")
def list_plugins():
    plugin_manager.discover()
    return jsonify({"plugins": plugin_manager.list()})


@plugin_bp.post("/discover")
def discover_plugins():
    plugin_manager.discover()
    return jsonify({"plugins": plugin_manager.list()})


@plugin_bp.post("/<plugin_id>/enable")
def enable_plugin(plugin_id: str):
    try:
        return jsonify(plugin_manager.enable(plugin_id))
    except PluginError as exc:
        return _error(exc)


@plugin_bp.post("/<plugin_id>/disable")
def disable_plugin(plugin_id: str):
    try:
        return jsonify(plugin_manager.disable(plugin_id))
    except PluginError as exc:
        return _error(exc)


@plugin_bp.put("/<plugin_id>/config")
def configure_plugin(plugin_id: str):
    try:
        return jsonify(plugin_manager.configure(plugin_id, request.get_json(silent=True) or {}))
    except PluginError as exc:
        return _error(exc)
