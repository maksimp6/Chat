"""Small API helpers for session/invocation lifecycle endpoints."""

from flask import Blueprint, jsonify, request

from invocation_manager import create_invocation, get_invocation
from session_manager import create_session, get_session

runtime_bp = Blueprint("runtime", __name__, url_prefix="/api")


@runtime_bp.post("/sessions")
def api_create_session():
    data = request.get_json(silent=True) or {}
    return jsonify(create_session(metadata=data.get("metadata"))), 201


@runtime_bp.get("/sessions/<session_id>")
def api_get_session(session_id):
    session = get_session(session_id)
    if not session:
        return jsonify({"error": "session_not_found"}), 404
    return jsonify(session)


@runtime_bp.post("/sessions/<session_id>/invocations")
def api_create_invocation(session_id):
    data = request.get_json(silent=True) or {}
    conversation_id = data.get("conversation_id")
    if not conversation_id:
        return jsonify({"error": "conversation_id_required"}), 400
    try:
        context = create_invocation(
            session_id=session_id,
            conversation_id=conversation_id,
            metadata=data.get("metadata"),
        )
    except ValueError as exc:
        return jsonify({"error": "session_not_found", "message": str(exc)}), 404
    return jsonify(context.as_dict()), 201


@runtime_bp.get("/invocations/<invocation_id>")
def api_get_invocation(invocation_id):
    invocation = get_invocation(invocation_id)
    if not invocation:
        return jsonify({"error": "invocation_not_found"}), 404
    return jsonify(invocation)
