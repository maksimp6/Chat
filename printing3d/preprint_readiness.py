"""Evidence-only pre-print decision; never connects to or starts a printer."""

from pathlib import Path
from typing import Any

from printing3d.gcode_audit import audit_passport
from printing3d.model_preflight import verify_inventory_sources
from printing3d.quality_capability import assess_quality


def prepare_print(
    manifest: dict[str, Any],
    root: str | Path,
    passport: dict[str, Any],
    job_path: str | Path | None = None,
    capability: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Produce an auditable fail-closed pre-print checklist for a model."""
    inventory = {m["id"]: m for m in manifest["models"]}
    model_id = passport["id"]
    if model_id not in inventory:
        raise ValueError("unknown model")
    all_sources = verify_inventory_sources(manifest, root)
    source = all_sources[model_id]
    checks = {
        "source_geometry": "PASS",
        "sliced_job": "BLOCKED",
        "target_printer": "BLOCKED",
        "quality": "UNKNOWN",
        "material_loaded": "UNKNOWN",
        "bed_clean": "UNKNOWN",
        "nozzle_condition": "UNKNOWN",
        "plate_clearance": "UNKNOWN",
        "physical_fit": "UNKNOWN",
        "operator_approval": "BLOCKED",
    }
    if job_path is not None:
        result = audit_passport(passport, job_path)
        checks["sliced_job"] = (
            "PASS" if result["status"].startswith("GCODE_VERIFIED") else "BLOCKED"
        )
    quality = assess_quality(passport, capability or {})
    checks["quality"] = quality["status"]
    observed = observed or {}
    target = passport["target_printer"]
    if (
        observed.get("authenticated_mqtt") is True
        and observed.get("serial_match_verified") is True
        and observed.get("firmware_version") == target.get("firmware_version")
        and observed.get("host") == target.get("host")
    ):
        checks["target_printer"] = "PASS"
    if observed.get("material_loaded") == passport["material"]:
        checks["material_loaded"] = "PASS"
    for key in ("bed_clean", "nozzle_condition", "plate_clearance", "physical_fit"):
        if observed.get(key) is True and observed.get(key + "_evidence"):
            checks[key] = "PASS"
    # Printer firmware-open and real physical fit cannot be inferred from static files.
    if (
        all(checks[k] == "PASS" for k in checks if k != "operator_approval")
        and observed.get("firmware_open_verified") is True
        and observed.get("operator_approval") is True
    ):
        checks["operator_approval"] = "PASS"
    ready = all(v == "PASS" for v in checks.values())
    return {
        "model_id": model_id,
        "target_printer": target["device_id"],
        "status": "READY" if ready else "BLOCKED",
        "checks": checks,
        "source_parts_verified": len(source),
        "quality_details": quality,
        "print_start_authorized": False,
    }
