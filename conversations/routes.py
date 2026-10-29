"""HTTP API of conversations: title, model, deletion, and the global settings every dialog uses."""

from __future__ import annotations

from flask import Flask, jsonify, request

from conversations.ownership import _conversation_record, check_access, delete_owner
from conversations.host import (
    bound,
    delete_conversation,
    get_current_owner_id,
    global_settings,
    update_conversation_model,
    update_conversation_title,
    validate_dialog_model,
)


def patch_conversation(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "json_body_required"}), 400
    if "model" in data:
        try:
            data["model"] = validate_dialog_model(data["model"])
        except ValueError:
            return jsonify({"error": "model_not_available_for_dialog"}), 400
        except bound.ModelDiscoveryError:
            return jsonify({"error": "model_catalog_unavailable"}), 503
    if "title" in data:
        update_conversation_title(conv_id, data["title"], owner_id)
    if "model" in data:
        update_conversation_model(conv_id, data["model"])
    return jsonify({"status": "ok"})


def set_conv_title(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    title = (request.get_json(silent=True) or {}).get("title")
    if not title:
        return jsonify({"error": "Title is required"}), 400
    update_conversation_title(conv_id, title, owner_id)
    record = _conversation_record(conv_id)
    return jsonify(
        {"status": "ok", "title": record["title"] if record is not None else title}
    )


def set_conv_model(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "json_body_required"}), 400
    try:
        model = validate_dialog_model(data.get("model"))
    except ValueError:
        return jsonify({"error": "model_not_available_for_dialog"}), 400
    except bound.ModelDiscoveryError:
        return jsonify({"error": "model_catalog_unavailable"}), 503
    update_conversation_model(conv_id, model)
    return jsonify({"status": "ok", "model": model})


def remove_conv(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    delete_conversation(conv_id)
    delete_owner(conv_id, owner_id)
    return jsonify({"status": "deleted"})


def get_settings():
    """The global settings every dialog uses; there are no per-dialog settings."""
    if not get_current_owner_id(required=False):
        return jsonify({"error": "owner_required"}), 401
    return jsonify(global_settings())


def install(app: Flask) -> None:
    """Serve the conversation API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/conversations/<conv_id>", view_func=patch_conversation, methods=["PATCH"]
    )
    app.add_url_rule(
        "/api/conversations/<conv_id>/title", view_func=set_conv_title, methods=["PUT"]
    )
    app.add_url_rule(
        "/api/conversations/<conv_id>/model", view_func=set_conv_model, methods=["PUT"]
    )
    app.add_url_rule(
        "/api/conversations/<conv_id>", view_func=remove_conv, methods=["DELETE"]
    )
    # Settings are global and read-only: shown, never saved through the API.
    app.add_url_rule("/api/settings", view_func=get_settings, methods=["GET"])
