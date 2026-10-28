"""HTTP API for inspecting and configuring per-user provider quotas."""

from __future__ import annotations
from flask import Blueprint, jsonify, request

from provider_accounts.quotas import (
    assign_user_policy,
    get_usage,
    get_user_policy,
    upsert_policy,
)
from provider_accounts.host import get_current_owner_id


provider_quota_bp = Blueprint(
    "provider_quota",
    __name__,
    url_prefix="/api/provider-quota",
)


def _flag(value) -> bool:
    # bool("false") is True and bool(None) is False, so a string or null would silently turn a
    # policy on or off. Only real booleans and their JSON-string spellings are accepted.
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise ValueError("enabled must be a boolean")


@provider_quota_bp.get("/me")
def quota_me():
    user_id = get_current_owner_id()

    return jsonify(get_usage(user_id))


@provider_quota_bp.get("/users/<user_id>")
def quota_user(user_id: str):
    try:
        return jsonify(get_usage(user_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@provider_quota_bp.put("/policies/<policy_name>")
def quota_policy_update(policy_name: str):

    data = request.get_json(silent=True) or {}
    try:
        values = {
            "name": str(policy_name).strip().lower(),
            "period_seconds": int(data.get("period_seconds", 86400)),
            "max_requests": int(data.get("max_requests", 100)),
            "max_requests_per_minute": int(data.get("max_requests_per_minute", 20)),
            "enabled": _flag(data.get("enabled", True)),
        }
        upsert_policy(
            values["name"],
            period_seconds=values["period_seconds"],
            max_requests=values["max_requests"],
            max_requests_per_minute=values["max_requests_per_minute"],
            enabled=values["enabled"],
        )
        return jsonify({"status": "ok", "policy": values})
    except (TypeError, ValueError) as exc:
        # int(None) raises TypeError; a missing or null number is a bad request, not a 500.
        return jsonify({"error": str(exc)}), 400


@provider_quota_bp.put("/users/<user_id>/policy")
def quota_assign_policy(user_id: str):

    data = request.get_json(silent=True) or {}
    try:
        assign_user_policy(user_id, str(data.get("policy") or ""))
        return jsonify(
            {"status": "ok", "user_id": user_id, "policy": get_user_policy(user_id)}
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


__all__ = ["provider_quota_bp"]
