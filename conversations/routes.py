"""HTTP API of one conversation: title, model, deletion and dialog settings."""

from __future__ import annotations

from flask import Flask, jsonify, request

from conversations.ownership import _conversation_record, check_access, delete_owner
from conversations.host import (
    bound,
    delete_conversation,
    get_conv_settings,
    get_current_owner_id,
    normalize_tools_config,
    save_conv_settings,
    update_conversation_model,
    update_conversation_title,
    validate_dialog_model,
    validate_generation_params,
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


def get_dialog_settings(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    return jsonify(get_conv_settings(conv_id) or {})


def save_dialog_settings(conv_id):
    owner_id = get_current_owner_id(required=False)
    if not owner_id or not check_access(conv_id, owner_id):
        return jsonify({"error": "conversation_not_found"}), 404
    settings = request.get_json(silent=True)
    if not isinstance(settings, dict):
        return jsonify({"error": "JSON object is required"}), 400
    # Refuse here what every later message would be refused for, while the window can still show why.
    if str(settings.get("previous_response_id") or "").strip():
        message = "previous_response_id cannot be used in a dialog; clear it"
        return jsonify({"error": "invalid_params", "message": message}), 400
    try:
        validate_generation_params(
            {
                key: value
                for key, value in settings.items()
                if key != "previous_response_id"
            }
        )
    except ValueError as exc:
        return jsonify({"error": "invalid_params", "message": str(exc)}), 400
    if "tools_config" in settings:
        try:
            settings["tools_config"] = normalize_tools_config(settings["tools_config"])
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
    if "active_tool_categories" not in settings:
        # Tool selection is saved by PUT /tools; a settings save must not drop it.
        stored = get_conv_settings(conv_id) or {}
        if "active_tool_categories" in stored:
            settings["active_tool_categories"] = stored["active_tool_categories"]
    save_conv_settings(conv_id, settings)
    return jsonify({"status": "ok"})


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
    app.add_url_rule(
        "/api/conversations/<conv_id>/settings",
        view_func=get_dialog_settings,
        methods=["GET"],
    )
    app.add_url_rule(
        "/api/conversations/<conv_id>/settings",
        view_func=save_dialog_settings,
        methods=["PUT"],
    )
