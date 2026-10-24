"""Transport-neutral printer data types and telemetry transformations.

This module deliberately imports no networking or cloud SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MODEL_CODES = {"N1": "Bambu Lab A1 mini", "N2S": "Bambu Lab A1"}


@dataclass(frozen=True)
class PrinterIdentity:
    host: str
    model_code: str
    model: str
    firmware: str
    connection: str
    bound: str


def merge_report(previous: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Merge partial status deltas without modifying either input."""
    merged = dict(previous)
    for key, value in incoming.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = merge_report(merged[key], value)
        else:
            merged[key] = value
    return merged


def summarize_status(report: dict[str, Any]) -> dict[str, Any]:
    """Expose only allowlisted fields from authorized telemetry."""
    state = report.get("print", report)
    if not isinstance(state, dict):
        raise ValueError("print status must be an object")
    keys = (
        "gcode_state",
        "nozzle_temper",
        "bed_temper",
        "mc_percent",
        "mc_remaining_time",
        "print_error",
        "wifi_signal",
    )
    return {key: state[key] for key in keys if key in state}
