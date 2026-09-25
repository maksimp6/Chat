"""HTTP API for inspecting and configuring per-user provider quotas."""

from __future__ import annotations

import os
import secrets

from flask import Blueprint, jsonify, request

from provider_quotas import assign_user_policy, get_usage, get_user_policy, upsert_policy
from treasury_identity import get_current_owner_id


provider_quota_bp = Blueprint(
    "provider_quota",
    __name__,
    url_prefix="/api/provider-quota",
)


def _admin_ok() -> bool:
    expected = os.getenv("ALICE_QUOTA_ADMIN_TOKEN", "").strip()
    supplied = request.headers.get("X-Provider-Quota-Admin-Token", "").strip()
    return bool(expected and supplied and secrets.compare_digest(expected, supplied))


@provider_quota_bp.get("/me")
def quota_me():
    try:
        user_id = get_current_owner_id()
    except Exception as exc:
        return jsonify({"error": "authenticated_identity_required", "detail": str(exc)}), 401

    return jsonify(get_usage(user_id))


@provider_quota_bp.get("/users/<user_id>")
def quota_user(user_id: str):
    if not _admin_ok():
        return jsonify({"error": "provider quota admin authorization required"}), 401
    try:
        return jsonify(get_usage(user_id))
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@provider_quota_bp.put("/policies/<policy_name>")
def quota_policy_update(policy_name: str):
    if not _admin_ok():
        return jsonify({"error": "provider quota admin authorization required"}), 401

    data = request.get_json(silent=True) or {}
    try:
        values = {
            "name": str(policy_name).strip().lower(),
            "period_seconds": int(data.get("period_seconds", 86400)),
            "max_requests": int(data.get("max_requests", 100)),
            "max_requests_per_minute": int(data.get("max_requests_per_minute", 20)),
            "enabled": bool(data.get("enabled", True)),
        }
        upsert_policy(
            values["name"],
            period_seconds=values["period_seconds"],
            max_requests=values["max_requests"],
            max_requests_per_minute=values["max_requests_per_minute"],
            enabled=values["enabled"],
        )
        return jsonify({"status": "ok", "policy": values})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


@provider_quota_bp.put("/users/<user_id>/policy")
def quota_assign_policy(user_id: str):
    if not _admin_ok():
        return jsonify({"error": "provider quota admin authorization required"}), 401

    data = request.get_json(silent=True) or {}
    try:
        assign_user_policy(user_id, str(data.get("policy") or ""))
        return jsonify({"status": "ok", "user_id": user_id, "policy": get_user_policy(user_id)})
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400


__all__ = ["provider_quota_bp"]
