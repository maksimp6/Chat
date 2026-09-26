"""Small API helpers for session/invocation lifecycle endpoints."""

from flask import Blueprint, jsonify, request

from api_contracts import ContractViolation, validate_api_contract
from invocation_api import get_invocation_status, get_invocation_trace
from invocation_manager import create_invocation
from session_manager import create_session, get_session
from session_profiles import clone_profile, get_profile, list_profiles, session_profile

runtime_bp = Blueprint("runtime", __name__, url_prefix="/api")


def _contract_response(name, payload, status=200):
    validate_api_contract(name, payload)
    return jsonify(payload), status


def _request_payload(name):
    payload = request.get_json(silent=True)
    if payload is None:
        payload = {}
    validate_api_contract(name, payload)
    return payload


def _invalid_request(exc):
    payload = {"error": "invalid_request", "message": str(exc)}
    return _contract_response("runtime.error.invalid_request", payload, 400)


@runtime_bp.get("/session-profiles")
def api_list_session_profiles():
    return _contract_response(
        "runtime.profile.list.response",
        {"profiles": list_profiles()},
    )


@runtime_bp.get("/session-profiles/<profile_id>")
def api_get_session_profile(profile_id):
    profile = get_profile(profile_id)
    if not profile:
        return _contract_response("runtime.error.basic", {"error": "profile_not_found"}, 404)
    return _contract_response("runtime.profile.response", profile)


@runtime_bp.post("/session-profiles/<profile_id>/clone")
def api_clone_session_profile(profile_id):
    try:
        data = _request_payload("runtime.profile.clone.request")
    except ContractViolation as exc:
        return _invalid_request(exc)

    name = data.get("name")
    try:
        payload = clone_profile(profile_id, name.strip() if name else None)
    except KeyError:
        return _contract_response("runtime.error.basic", {"error": "profile_not_found"}, 404)
    return _contract_response("runtime.profile.clone.response", payload, 201)


@runtime_bp.post("/sessions")
def api_create_session():
    try:
        data = _request_payload("runtime.session.create.request")
    except ContractViolation as exc:
        return _invalid_request(exc)
    return _contract_response(
        "runtime.session.response",
        create_session(metadata=data.get("metadata")),
        201,
    )


@runtime_bp.get("/sessions/<session_id>")
def api_get_session(session_id):
    session = get_session(session_id)
    if not session:
        return _contract_response("runtime.error.basic", {"error": "session_not_found"}, 404)
    response = dict(session)
    response["profile"] = session_profile(session_id)
    return _contract_response("runtime.session.detail.response", response)


@runtime_bp.post("/sessions/<session_id>/invocations")
def api_create_invocation(session_id):
    try:
        data = _request_payload("runtime.invocation.create.request")
    except ContractViolation as exc:
        return _invalid_request(exc)

    try:
        context = create_invocation(
            session_id=session_id,
            conversation_id=data["conversation_id"],
            metadata=data.get("metadata"),
            create_missing_session=False,
        )
    except ValueError as exc:
        return _contract_response(
            "runtime.error.message",
            {"error": "session_not_found", "message": str(exc)},
            404,
        )
    return _contract_response("runtime.invocation.create.response", context.as_dict(), 201)


@runtime_bp.get("/invocations/<invocation_id>")
def api_get_invocation(invocation_id):
    invocation = get_invocation_status(invocation_id)
    if not invocation:
        return _contract_response("runtime.error.basic", {"error": "invocation_not_found"}, 404)
    return _contract_response("runtime.invocation.response", invocation)


@runtime_bp.get("/invocations/<invocation_id>/status")
def api_get_invocation_status(invocation_id):
    invocation = get_invocation_status(invocation_id)
    if not invocation:
        return _contract_response("runtime.error.basic", {"error": "invocation_not_found"}, 404)
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
        return _contract_response("runtime.error.basic", {"error": "invocation_not_found"}, 404)
    return _contract_response("runtime.invocation.trace.response", trace)
