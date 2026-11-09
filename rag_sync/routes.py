"""HTTP side of rag_sync (``/api/rag``): the workflow on the device starts a sync and reads its state."""

from __future__ import annotations

from rag_sync.sync import reset, start, status
from webframe import Blueprint, jsonify, request

__all__ = ["rag_bp"]

rag_bp = Blueprint("rag_sync", __name__, url_prefix="/api/rag")


@rag_bp.post("/sync")
def api_sync():
    """Start a sync. ``token`` in the body is used for this sync only and is not stored."""
    data = request.get_json(silent=True) or {}
    token = data.get("token") if isinstance(data, dict) else None
    result = start(token=token if isinstance(token, str) and token else None)
    return jsonify(result), 202 if result.get("started") else 409


@rag_bp.get("/status")
def api_status():
    return jsonify(status())


@rag_bp.post("/reset")
def api_reset():
    result = reset()
    return jsonify(result), 200 if result.get("reset") else 409
