"""HTTP API of the global memory panel: configuration, facts and clearing."""

from __future__ import annotations

from flask import Flask, jsonify, request

from agent_memory.extractor import list_global_memory_facts
from agent_memory.manager import (
    clear_global_memory,
    load_memory_config,
    save_memory_config,
)


def api_memory_panel_data():
    cfg = load_memory_config()
    facts = list_global_memory_facts()
    return jsonify({"config": cfg, "facts": facts})


def api_update_memory_config():
    data = request.get_json(silent=True) or {}
    try:
        save_memory_config(data)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify({"status": "ok", "config": load_memory_config()})


def api_clear_memory():
    category = (request.get_json(silent=True) or {}).get("category")
    if category is not None and not isinstance(category, str):
        return jsonify({"error": "category must be a string"}), 400
    clear_global_memory(category)
    return jsonify({"status": "cleared", "category": category or "all"})


def install(app: Flask) -> None:
    """Serve the memory panel API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/memory/manage", view_func=api_memory_panel_data, methods=["GET"]
    )
    app.add_url_rule(
        "/api/memory/config", view_func=api_update_memory_config, methods=["PUT"]
    )
    app.add_url_rule("/api/memory/clear", view_func=api_clear_memory, methods=["POST"])
