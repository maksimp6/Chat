"""Evidence-based functional quality assessment, not slicer-success inference."""

from math import isfinite
from typing import Any


def assess_quality(
    passport: dict[str, Any], capability: dict[str, Any], measurements: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Compare purpose and tolerances against measured printer/material capability.

    Unknown capability cannot become PASS. A process profile is not metrology.
    """
    requirements = passport["quality_requirements"]
    measurements = measurements or {}
    if passport.get("quality_requirements_approved") is not True:
        return {
            "status": "UNKNOWN",
            "purpose": passport["intended_function"],
            "printer_id": passport["target_printer"]["device_id"],
            "checks": [],
            "reason": "functional quality thresholds not approved",
            "print_quality_approved": False,
        }
    results = []
    for requirement in requirements:
        name = requirement["metric"]
        maximum = requirement.get("max_value")
        minimum = requirement.get("min_value")
        if maximum is None and minimum is None:
            raise ValueError("requirement missing numeric threshold")
        if any(
            not isinstance(v, (int, float)) or isinstance(v, bool) or not isfinite(v)
            for v in (maximum, minimum)
            if v is not None
        ):
            raise ValueError("invalid requirement threshold")
        record = measurements.get(name) or capability.get(name)
        if not isinstance(record, dict) or not record.get("evidence_id"):
            results.append({"metric": name, "status": "UNKNOWN", "reason": "no measured evidence"})
            continue
        if (
            record.get("material") != passport["material"]
            or record.get("printer_id") != passport["target_printer"]["device_id"]
        ):
            results.append(
                {
                    "metric": name,
                    "status": "UNKNOWN",
                    "reason": "evidence for different material or printer",
                }
            )
            continue
        if record.get("profile_id") != passport.get("process_profile_id"):
            results.append(
                {"metric": name, "status": "UNKNOWN", "reason": "process profile not calibrated"}
            )
            continue
        value = record.get("value")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
            results.append(
                {"metric": name, "status": "UNKNOWN", "reason": "no numeric measurement"}
            )
            continue
        ok = (maximum is None or value <= maximum) and (minimum is None or value >= minimum)
        results.append(
            {
                "metric": name,
                "status": "PASS" if ok else "FAIL",
                "value": value,
                "evidence_id": record["evidence_id"],
            }
        )
    status = (
        "FAIL"
        if any(r["status"] == "FAIL" for r in results)
        else ("UNKNOWN" if any(r["status"] == "UNKNOWN" for r in results) else "PASS")
    )
    return {
        "status": status,
        "purpose": passport["intended_function"],
        "printer_id": passport["target_printer"]["device_id"],
        "checks": results,
        "print_quality_approved": status == "PASS",
    }
