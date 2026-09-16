"""Small API helpers for session/invocation lifecycle endpoints."""

from flask import Blueprint, jsonify, request

from invocation_api import get_invocation_status, get_invocation_trace
from invocation_manager import create_invocation
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
    invocation = get_invocation_status(invocation_id)
    if not invocation:
        return jsonify({"error": "invocation_not_found"}), 404
    return jsonify(invocation)


@runtime_bp.get("/invocations/<invocation_id>/status")
def api_get_invocation_status(invocation_id):
    invocation = get_invocation_status(invocation_id)
    if not invocation:
        return jsonify({"error": "invocation_not_found"}), 404
    return jsonify({
        "id": invocation["id"],
        "session_id": invocation["session_id"],
        "conversation_id": invocation["conversation_id"],
        "trace_id": invocation["trace_id"],
        "status": invocation["status"],
        "created_at": invocation["created_at"],
        "started_at": invocation["started_at"],
        "completed_at": invocation["completed_at"],
        "error": invocation.get("error"),
    })


@runtime_bp.get("/invocations/<invocation_id>/trace")
def api_get_invocation_trace(invocation_id):
    trace = get_invocation_trace(invocation_id)
    if not trace:
        return jsonify({"error": "invocation_not_found"}), 404
    return jsonify(trace)
