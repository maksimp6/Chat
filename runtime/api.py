"""Small API helpers for invocation lifecycle endpoints."""

from webframe import Blueprint, jsonify

from runtime.host import (
    get_invocation_status,
    get_invocation_trace,
    validate_api_contract,
)

runtime_bp = Blueprint("runtime", __name__, url_prefix="/api")


def _contract_response(name, payload, status=200):
    validate_api_contract(name, payload)
    return jsonify(payload), status


@runtime_bp.get("/invocations/<invocation_id>")
def api_get_invocation(invocation_id):
    invocation = get_invocation_status(invocation_id)
    if not invocation:
        return _contract_response(
            "runtime.error.basic", {"error": "invocation_not_found"}, 404
        )
    return _contract_response("runtime.invocation.response", invocation)


@runtime_bp.get("/invocations/<invocation_id>/status")
def api_get_invocation_status(invocation_id):
    invocation = get_invocation_status(invocation_id)
    if not invocation:
        return _contract_response(
            "runtime.error.basic", {"error": "invocation_not_found"}, 404
        )
    payload = {
        "id": invocation["id"],
        "session_id": invocation["session_id"],
        "conversation_id": invocation["conversation_id"],
        "trace_id": invocation["trace_id"],
        "status": invocation["status"],
        "created_at": invocation["created_at"],
        "started_at": invocation["started_at"],
        "completed_at": invocation["completed_at"],
        "error": invocation.get("error"),
    }
    return _contract_response("runtime.invocation.status.response", payload)


@runtime_bp.get("/invocations/<invocation_id>/trace")
def api_get_invocation_trace(invocation_id):
    trace = get_invocation_trace(invocation_id)
    if not trace:
        return _contract_response(
            "runtime.error.basic", {"error": "invocation_not_found"}, 404
        )
    return _contract_response("runtime.invocation.trace.response", trace)
