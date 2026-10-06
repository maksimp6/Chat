#!/usr/bin/env python3
"""Validate and render Alice's canonical machine-readable integration catalog."""

from __future__ import annotations

import argparse
from datetime import date
import json
from pathlib import Path
import re
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
CATALOG_PATH = ROOT / "config/integrations/catalog.json"
SCHEMA_PATH = ROOT / "config/integrations/catalog.schema.json"
DOC_PATH = ROOT / "docs/integrations/catalog.md"

ALLOWED_STATUSES = {
    "supported",
    "partial",
    "experimental",
    "planned",
    "research",
    "blocked",
    "deprecated",
    "unsupported",
}
SEED_IDS = {"mcp", "browser", "sber_business", "gigachat"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
FORBIDDEN_KEYS = {
    "secret",
    "token",
    "password",
    "api_key",
    "credential",
    "credential_value",
    "secret_value",
}
REQUIRED_FIELDS = {
    "id",
    "name",
    "provider",
    "category",
    "status",
    "provider_exists",
    "alice_implemented",
    "e2e_verified",
    "last_verified",
    "issue",
    "official_source",
    "docs",
    "transports",
    "auth",
    "capabilities",
    "sandbox_available",
    "secret_store_required",
    "approval_required",
    "availability",
    "data_residency",
    "notes",
}


def load_json(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path}: root must be an object")
    return data


def _date(value: str, field: str) -> date:
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field}: expected ISO date") from exc


def _find_forbidden_keys(value: Any, prefix: str = "") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, child in value.items():
            path = f"{prefix}.{key}" if prefix else key
            if str(key).lower() in FORBIDDEN_KEYS:
                found.append(path)
            found.extend(_find_forbidden_keys(child, path))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_find_forbidden_keys(child, f"{prefix}[{index}]"))
    return found


def validate_catalog(data: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    if data.get("schema_version") != 1:
        errors.append("schema_version must be 1")

    try:
        as_of = _date(data.get("as_of"), "as_of")
    except ValueError as exc:
        errors.append(str(exc))
        as_of = date.min

    stale_after = data.get("stale_after_days")
    if not isinstance(stale_after, int) or isinstance(stale_after, bool) or stale_after < 1:
        errors.append("stale_after_days must be a positive integer")

    integrations = data.get("integrations")
    if not isinstance(integrations, list):
        return errors + ["integrations must be an array"]

    ids: set[str] = set()
    for index, record in enumerate(integrations):
        prefix = f"integrations[{index}]"
        if not isinstance(record, dict):
            errors.append(f"{prefix} must be an object")
            continue

        missing = sorted(REQUIRED_FIELDS - set(record))
        if missing:
            errors.append(f"{prefix} missing fields: {', '.join(missing)}")

        integration_id = record.get("id")
        if not isinstance(integration_id, str) or not ID_RE.fullmatch(integration_id):
            errors.append(f"{prefix}.id is invalid")
        elif integration_id in ids:
            errors.append(f"duplicate integration id: {integration_id}")
        else:
            ids.add(integration_id)

        status = record.get("status")
        if status not in ALLOWED_STATUSES:
            errors.append(f"{prefix}.status is invalid: {status!r}")

        for field in (
            "provider_exists",
            "alice_implemented",
            "e2e_verified",
            "secret_store_required",
            "approval_required",
        ):
            if not isinstance(record.get(field), bool):
                errors.append(f"{prefix}.{field} must be boolean")

        issue = record.get("issue")
        if not isinstance(issue, int) or isinstance(issue, bool) or issue < 1:
            errors.append(f"{prefix}.issue must be a positive integer")

        last_verified = record.get("last_verified")
        if last_verified is not None:
            try:
                verified_date = _date(last_verified, f"{prefix}.last_verified")
                if verified_date > as_of:
                    errors.append(f"{prefix}.last_verified is after catalog as_of")
            except ValueError as exc:
                errors.append(str(exc))

        provider_exists = record.get("provider_exists") is True
        implemented = record.get("alice_implemented") is True
        e2e = record.get("e2e_verified") is True

        if implemented and not provider_exists:
            errors.append(f"{prefix}: Alice implementation requires provider_exists")
        if e2e and not implemented:
            errors.append(f"{prefix}: e2e_verified requires alice_implemented")
        if status == "supported" and not (
            provider_exists and implemented and e2e and last_verified is not None
        ):
            errors.append(f"{prefix}: supported requires provider + implementation + E2E verification")
        if status == "partial" and not implemented:
            errors.append(f"{prefix}: partial requires an Alice implementation")
        if status == "planned" and implemented:
            errors.append(f"{prefix}: planned cannot already be Alice-implemented")

        capabilities = record.get("capabilities")
        if not isinstance(capabilities, dict) or set(capabilities) != {"read", "write", "events"}:
            errors.append(f"{prefix}.capabilities must contain read/write/events only")
        elif not all(isinstance(value, bool) for value in capabilities.values()):
            errors.append(f"{prefix}.capabilities values must be boolean")

        for field in ("docs", "transports", "auth"):
            values = record.get(field)
            if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                errors.append(f"{prefix}.{field} must be a string array")

    forbidden = _find_forbidden_keys(data)
    if forbidden:
        errors.append("catalog contains forbidden secret-value keys: " + ", ".join(forbidden))

    if not SEED_IDS.issubset(ids):
        errors.append("missing bootstrap integrations: " + ", ".join(sorted(SEED_IDS - ids)))
    return errors


def stale_integrations(data: dict[str, Any]) -> list[str]:
    as_of = _date(data["as_of"], "as_of")
    threshold = int(data["stale_after_days"])
    stale: list[str] = []
    for record in data["integrations"]:
        value = record["last_verified"]
        if value is None or (as_of - _date(value, f"{record['id']}.last_verified")).days > threshold:
            stale.append(record["id"])
    return stale


def render_markdown(data: dict[str, Any]) -> str:
    lines = [
        "# Alice integration catalog",
        "",
        "Generated from `config/integrations/catalog.json`. Do not edit integration status here.",
        "",
        f"Catalog as of: **{data['as_of']}**. Verification stale threshold: **{data['stale_after_days']} days**.",
        "",
        "| ID | Integration | Status | Provider exists | Alice implemented | E2E verified | Last verified | Issue |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for record in data["integrations"]:
        verified = record["last_verified"] or "unverified"
        lines.append(
            "| {id} | {name} | {status} | {provider} | {implemented} | {e2e} | {verified} | #{issue} |".format(
                id=record["id"],
                name=record["name"],
                status=record["status"],
                provider="yes" if record["provider_exists"] else "no",
                implemented="yes" if record["alice_implemented"] else "no",
                e2e="yes" if record["e2e_verified"] else "no",
                verified=verified,
                issue=record["issue"],
            )
        )
    stale = stale_integrations(data)
    lines.extend(
        [
            "",
            "## Verification attention",
            "",
            "Entries reported as stale or unverified: "
            + (", ".join(f"`{item}`" for item in stale) if stale else "none")
            + ".",
            "",
            "An integration PR that changes implementation/support status must update the structured catalog in the same slice.",
            "",
        ]
    )
    return "\n".join(lines)


def validate_schema_enum(schema: dict[str, Any]) -> list[str]:
    enum = set(
        schema["properties"]["integrations"]["items"]["properties"]["status"]["enum"]
    )
    return [] if enum == ALLOWED_STATUSES else ["schema status enum differs from validator enum"]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--write-doc", action="store_true")
    args = parser.parse_args()

    data = load_json(CATALOG_PATH)
    schema = load_json(SCHEMA_PATH)
    errors = validate_catalog(data) + validate_schema_enum(schema)
    if errors:
        for error in errors:
            print(f"ERROR: {error}")
        return 1

    rendered = render_markdown(data)
    if args.write_doc:
        DOC_PATH.parent.mkdir(parents=True, exist_ok=True)
        DOC_PATH.write_text(rendered, encoding="utf-8")
    elif not DOC_PATH.exists() or DOC_PATH.read_text(encoding="utf-8") != rendered:
        print("ERROR: generated integration catalog documentation is stale")
        return 1

    stale = stale_integrations(data)
    print(f"integration catalog valid: {len(data['integrations'])} records")
    print("stale/unverified: " + (", ".join(stale) if stale else "none"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
