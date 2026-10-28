"""HTTP API of the model catalog."""

from __future__ import annotations

from flask import Flask, jsonify, request

from provider_accounts.host import annotate_catalog
from provider_accounts.model_discovery import (
    ModelDiscovery,
    ModelDiscoveryError,
    get_model_discovery,
    static_model_catalog,
)

# The process model discovery, created when the routes are installed (at boot).
MODEL_DISCOVERY: ModelDiscovery | None = None


def list_models():
    force_refresh = request.args.get("refresh", "").lower() in {"1", "true", "yes"}
    try:
        catalog = annotate_catalog(
            MODEL_DISCOVERY.get_models(force_refresh=force_refresh)
        )
        catalog["degraded"] = False
        return jsonify(catalog)
    except ModelDiscoveryError:
        catalog = annotate_catalog(static_model_catalog())
        catalog["degraded"] = True
        catalog["error"] = "model_discovery_unavailable"
        return jsonify(catalog)


def install(app: Flask) -> None:
    """Create the model discovery and serve the catalog; the endpoint keeps the view name."""
    global MODEL_DISCOVERY
    MODEL_DISCOVERY = get_model_discovery()
    app.add_url_rule("/api/models", view_func=list_models, methods=["GET"])
