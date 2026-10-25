"""Fail-closed offline audit of sliced Bambu A1 mini jobs and model passports."""

import hashlib
import json
import math
import re
import zipfile
from pathlib import Path
from typing import Any

REQUIRED = {
    "Metadata/plate_1.gcode",
    "Metadata/plate_1.gcode.md5",
    "Metadata/project_settings.config",
    "Metadata/plate_1.json",
}
# A word is a letter and its number, and the letter may follow a digit ("G1X300") or another word
# ("X5Y300"), so each axis word is checked wherever it appears.
MOVE = re.compile(r"(?<![A-Za-z])([XYZ])([-+]?(?:\d+(?:\.\d*)?|\.\d+))(?![\d.])")


def _verify_motion_bounds(command: str, bed: list[int]) -> None:
    for axis, raw in MOVE.findall(command):
        value = float(raw)
        if not math.isfinite(value):
            raise ValueError("non-finite motion")
        if axis in ("X", "Y") and not -2 <= value <= bed["XY".index(axis)] + 2:
            raise ValueError("motion outside bed")
        if axis == "Z" and not -2 <= value <= bed[2] + 2:
            raise ValueError("Z outside printer height")


def _opcode(command: str) -> str:
    # The firmware skips a leading line number ("N5 M112") and reads leading zeros in a code ("M0112"
    # is M112), so both are removed before the code is compared with the forbidden list.
    words = command.split()
    while words and re.fullmatch(r"N\d+", words[0], re.IGNORECASE):
        words.pop(0)
    if not words:
        return ""
    return re.sub(r"^([GM])0*(\d+)$", r"\1\2", words[0].upper())


def _verify_toolpaths(gcode: str, bed: list[int]) -> tuple[int, int]:
    count, extrusion, heaters = 0, 0, set[str]()
    for line in gcode.splitlines():
        command = line.split(";", 1)[0].strip()
        if not command:
            continue
        opcode = _opcode(command)
        if opcode in ("M112", "M500", "M502", "M997", "M999"):
            raise ValueError("unexpected dangerous firmware command")
        if opcode in ("M190", "M109"):
            heaters.add(opcode)
        if opcode in ("G0", "G1", "G2", "G3"):
            count += 1
            if opcode == "G1" and re.search(r"(?:^|\s)E[-+\d.]", command):
                extrusion += 1
            _verify_motion_bounds(command, bed)
    if count < 10 or extrusion < 10 or heaters != {"M190", "M109"}:
        raise ValueError("missing toolpaths, extrusion or heater commands")
    return count, extrusion


def audit_job(path: str | Path, passport: dict[str, Any]) -> dict[str, Any]:
    """Validate archive, actual commands, material, machine and build boundaries.

    An audit is not a firmware-open or physical-fit acceptance test.
    """
    path = Path(path)
    data = path.read_bytes()
    if not path.name.endswith(".gcode.3mf"):
        raise ValueError("not a sliced .gcode.3mf")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None or not REQUIRED.issubset(archive.namelist()):
            raise ValueError("invalid sliced archive")
        gcode_bytes = archive.read("Metadata/plate_1.gcode")
        expected_md5 = archive.read("Metadata/plate_1.gcode.md5").decode("ascii").strip()
        if hashlib.md5(gcode_bytes, usedforsecurity=False).hexdigest().lower() != expected_md5.lower():
            raise ValueError("G-code MD5 mismatch")
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        plate = json.loads(archive.read("Metadata/plate_1.json"))
    machine = passport["machine"]
    if (
        settings.get("printer_model") != machine["model"]
        or settings.get("printer_variant") != machine["nozzle_mm"]
    ):
        raise ValueError("printer profile mismatch")
    if settings.get("filament_type") != [passport["material"]]:
        raise ValueError("material mismatch")
    if not isinstance(plate.get("bbox_all"), list) or len(plate["bbox_all"]) != 4:
        raise ValueError("missing print area")
    x0, y0, x1, y1 = map(float, plate["bbox_all"])
    bed = machine["bed_mm"]
    if not (0 <= x0 < x1 <= bed[0] and 0 <= y0 < y1 <= bed[1]):
        raise ValueError("print area outside bed")
    gcode = gcode_bytes.decode("utf-8")
    if not gcode.startswith("; HEADER_BLOCK_START") or "; EXECUTABLE_BLOCK_END" not in gcode:
        raise ValueError("incomplete Bambu G-code")
    count, extrusion = _verify_toolpaths(gcode, bed)
    return {
        "status": "GCODE_STATIC_VERIFIED",
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
        "motion_commands": count,
        "extrusion_commands": extrusion,
        "firmware_open_verified": False,
        "physical_fit_verified": False,
    }


def audit_passport(passport: dict[str, Any], job_path: str | Path | None = None) -> dict[str, Any]:
    if not passport.get("id") or passport.get("schema_version") != 1:
        raise ValueError("invalid model passport")
    if not passport.get("source_sha256") or not re.fullmatch(
        "[0-9a-f]{64}", passport["source_sha256"]
    ):
        raise ValueError("source provenance missing")
    if passport.get("fit_status") == "verified" and not passport.get("fit_evidence"):
        raise ValueError("fit status without evidence")
    if job_path is None:
        return {"status": "SOURCE_ONLY_NOT_PRINT_READY", "model_id": passport["id"]}
    if passport.get("fit_status") != "verified":
        result = audit_job(job_path, passport)
        result.update(status="GCODE_VERIFIED_FIT_BLOCKED", model_id=passport["id"])
        return result
    result = audit_job(job_path, passport)
    result["model_id"] = passport["id"]
    return result


def verify_target_printer(
    passport: dict[str, Any], observed: dict[str, Any], job_sha256: str | None = None
) -> dict[str, Any]:
    """Fail closed until this exact device, firmware, readback and UI are proven.

    Observations must come from independent printer-side evidence; caller-supplied
    model names or a matching source G-code hash alone cannot approve printing.
    """
    target = passport["target_printer"]
    if observed.get("host") != target["host"] or observed.get("model") != target["model"]:
        raise ValueError("wrong physical printer")
    if not observed.get("authenticated_mqtt") or not observed.get("ftps_certificate_pin_verified"):
        raise ValueError("device authentication not verified")
    if not observed.get("serial_match_verified"):
        raise ValueError("device serial not independently matched")
    if not observed.get("firmware_version") or not observed.get("firmware_compatibility_verified"):
        raise ValueError("firmware compatibility unknown")
    if job_sha256 is not None:
        if not re.fullmatch("[0-9a-f]{64}", job_sha256):
            raise ValueError("invalid job hash")
        if observed.get("printer_file_sha256") != job_sha256 or not observed.get(
            "printer_file_readback_verified"
        ):
            raise ValueError("printer job readback mismatch")
        if not observed.get("touchscreen_open_verified"):
            raise ValueError("printer firmware has not opened the job")
    return {
        "status": "PRINTER_COMPATIBILITY_VERIFIED",
        "host": target["host"],
        "firmware_version": observed["firmware_version"],
        "job_verified": job_sha256 is not None,
        "print_start_authorized": False,
    }
