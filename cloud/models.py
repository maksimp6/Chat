"""Normalized cloud resource models."""

from __future__ import annotations

from typing import Any


def _pick(raw: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = raw.get(key)
        if value not in (None, ""):
            return value
    return None


def normalize_resource(
    provider: str, service: str, resource_type: str, raw: dict[str, Any]
) -> dict[str, Any]:
    resource_id = _pick(raw, "id", "uuid", "resource_id", "instance_id", "name")
    return {
        "provider": provider,
        "service": service,
        "type": resource_type,
        "id": None if resource_id is None else str(resource_id),
        "name": _pick(raw, "name", "display_name", "title"),
        "status": _pick(raw, "status", "state", "health"),
        "region": _pick(raw, "region", "zone", "location"),
        "project_id": _pick(raw, "project_id", "tenant_id", "project"),
        "raw": raw,
    }


def normalize_resources(
    provider: str,
    service: str,
    resource_type: str,
    items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    return [normalize_resource(provider, service, resource_type, item) for item in items]
