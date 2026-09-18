"""Cloud.ru IAM API-key wizard routes."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import ipaddress
import os
from typing import Any

from flask import Blueprint, jsonify, request

from cloudru_iam import CloudRuIamClient, CloudRuIamError
from key_manager import store_secret

cloudru_iam_bp = Blueprint("cloudru_iam", __name__, url_prefix="/api/cloudru/iam")


def _enabled() -> bool:
    return os.getenv("CLOUDRU_IAM_WIZARD_ENABLED", "false").strip().lower() == "true"


def _authorized() -> bool:
    configured = os.getenv("CLOUDRU_IAM_WIZARD_TOKEN", "").strip()
    if configured:
        auth = request.headers.get("Authorization", "")
        token = auth[7:].strip() if auth.startswith("Bearer ") else ""
        return token == configured

    remote = request.remote_addr or ""
    return remote in {"127.0.0.1", "::1"}


def _guard():
    if not _enabled():
        return jsonify({"error": "Cloud.ru IAM wizard is disabled"}), 404
    if not _authorized():
        return jsonify({"error": "Cloud.ru IAM wizard authentication required"}), 401
    return None


def _parse_expiry(value: Any) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    try:
        expiry = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("expires_at must be an ISO-8601 timestamp") from exc
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    now = datetime.now(timezone.utc)
    if expiry < now + timedelta(days=1):
        raise ValueError("Cloud.ru API-key lifetime must be at least 1 day")
    if expiry > now + timedelta(days=365):
        raise ValueError("Cloud.ru API-key lifetime cannot exceed 1 year")
    return expiry.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _validate_ips(values: list[str]) -> list[str]:
    result = []
    for item in values:
        value = str(item).strip()
        if not value:
            continue
        try:
            ipaddress.ip_network(value, strict=False)
        except ValueError as exc:
            raise ValueError(f"Invalid IP or subnet: {value}") from exc
        result.append(value)
    return result


def _validate_time_slots(values: list[dict[str, Any]]) -> list[dict[str, int]]:
    result = []
    for slot in values:
        if not isinstance(slot, dict):
            raise ValueError("time_slots must contain objects")
        start = int(slot.get("start"))
        end = int(slot.get("end"))
        if not 0 <= start <= 23 or not 0 <= end <= 23 or start == end:
            raise ValueError("time slot hours must be between 0 and 23 and non-equal")
        result.append({"start": start, "end": end})
    return result


@cloudru_iam_bp.post("/api-keys")
def create_api_key():
    guard = _guard()
    if guard:
        return guard

    data = request.get_json(silent=True) or {}
    if data.get("confirm") is not True:
        return jsonify({"error": "Explicit confirmation is required"}), 400

    try:
        service_account_id = str(data.get("service_account_id") or "").strip()
        name = str(data.get("name") or "").strip()
        description = str(data.get("description") or "").strip()
        products = data.get("products") or []
        environment = str(data.get("environment") or "production").strip()
        owner = str(data.get("owner") or "local-admin").strip()
        ip_addresses = _validate_ips(list(data.get("ip_addresses") or []))
        time_slots = _validate_time_slots(list(data.get("time_slots") or []))
        timezone_offset = data.get("timezone")
        if timezone_offset is not None:
            timezone_offset = int(timezone_offset)
            if not -12 <= timezone_offset <= 14:
                raise ValueError("timezone must be between -12 and 14")
        expires_at = _parse_expiry(data.get("expires_at"))

        if not isinstance(products, list):
            raise ValueError("products must be a list")
        products = [str(item).strip() for item in products if str(item).strip()]
        if not products:
            raise ValueError("At least one Cloud.ru service is required")

        client = CloudRuIamClient()
        cloud_response = client.create_api_key(
            service_account_id=service_account_id,
            name=name,
            description=description,
            products=products,
            expires_at=expires_at,
            ip_addresses=ip_addresses,
            time_slots=time_slots,
            timezone_offset=timezone_offset,
        )

        api_key = cloud_response.get("secret") or ""
        key_id = str(cloud_response.get("id") or "")
        if not api_key or not key_id:
            return jsonify({"error": "Cloud.ru did not return the API-key secret"}), 502

        metadata = store_secret(
            secret=api_key,
            name=name,
            purpose="api-key",
            provider="cloudru",
            environment=environment,
            owner=owner,
            expires_at=(
                datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
                if expires_at
                else None
            ),
            key_ref=f"cloudru_{key_id}",
        )

        return jsonify(
            {
                "success": True,
                "id": key_id,
                "key_ref": metadata.key_ref,
                "name": metadata.name,
                "products": products,
                "expires_at": expires_at or cloud_response.get("expires_at"),
                "secret": api_key,
                "secret_policy": "shown once; never written to logs or browser storage",
            }
        )
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400
    except CloudRuIamError:
        return jsonify({"error": "Cloud.ru IAM request failed"}), 502
    except Exception:
        return jsonify({"error": "Unable to create and store Cloud.ru API key"}), 500


@cloudru_iam_bp.get("/api-keys")
def list_api_keys():
    guard = _guard()
    if guard:
        return guard
    service_account_id = str(request.args.get("service_account_id") or "").strip()
    if not service_account_id:
        return jsonify({"error": "service_account_id is required"}), 400
    try:
        keys = CloudRuIamClient().list_api_keys(service_account_id=service_account_id)
        safe = []
        for item in keys:
            safe.append(
                {
                    "id": item.get("id"),
                    "name": item.get("name"),
                    "description": item.get("description"),
                    "service_account_id": item.get("service_account_id"),
                    "products": item.get("products") or [],
                    "enabled": item.get("enabled"),
                    "created_at": item.get("created_at"),
                    "updated_at": item.get("updated_at"),
                    "expires_at": item.get("expires_at"),
                    "restrictions": item.get("restrictions") or {},
                }
            )
        return jsonify({"keys": safe})
    except CloudRuIamError:
        return jsonify({"error": "Cloud.ru IAM request failed"}), 502
