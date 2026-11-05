"""HTTP side of vector_db (``/api/vectors``): the same actions as the ``vectors`` tool."""

from __future__ import annotations

from webframe import Blueprint, jsonify, request

from vector_db.tools import vectors

__all__ = ["vectors_bp"]

vectors_bp = Blueprint("vector_db", __name__, url_prefix="/api/vectors")


@vectors_bp.post("/<action>")
def api_action(action: str):
    """``POST /api/vectors/<action>`` with the tool's fields as JSON; 400 when the action refuses."""
    data = request.get_json(silent=True) or {}
    result = vectors({**(data if isinstance(data, dict) else {}), "action": action})
    return jsonify(result), 200 if result.get("success") else 400
