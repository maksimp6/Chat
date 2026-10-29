"""HTTP API of the global memory panel: configuration and facts, read-only."""

from __future__ import annotations

from flask import Flask, jsonify

from agent_memory.extractor import list_global_memory_facts
from agent_memory.manager import load_memory_config


def api_memory_panel_data():
    cfg = load_memory_config()
    facts = list_global_memory_facts()
    return jsonify({"config": cfg, "facts": facts})


def install(app: Flask) -> None:
    """Serve the memory panel API; endpoints keep the view names."""
    app.add_url_rule(
        "/api/memory/manage", view_func=api_memory_panel_data, methods=["GET"]
    )
    # Settings are read-only: the memory configuration is shown, never saved or cleared
    # through the API.
