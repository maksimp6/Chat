"""Bounded read-only preflight for a single sliced Bambu A1 mini .gcode.3mf.

This verifies archive integrity, identity and basic G-code constraints.
It is NOT a slicer, digital signature, mesh repairer, or printer controller.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import stat
import zlib
from pathlib import Path
from typing import BinaryIO
from zipfile import BadZipFile, LargeZipFile, ZipFile, ZipInfo

MAX_FILE_BYTES = 64 * 1024 * 1024
MAX_ARCHIVE_UNCOMPRESSED = 128 * 1024 * 1024
MAX_ENTRIES = 192
MAX_CONFIG_BYTES = 512 * 1024
MAX_GCODE_LINE_BYTES = 16 * 1024
GCODE_NAME = "Metadata/plate_1.gcode"
CONFIG_NAME = "Metadata/project_settings.config"
GCODE_MD5_NAME = "Metadata/plate_1.gcode.md5"
MODEL_NAME = "3D/3dmodel.model"
PROFILES = {
    "PLA": {"nozzle_min": 190, "nozzle_max": 240, "bed_min": 40, "bed_max": 75},
    "PETG": {"nozzle_min": 215, "nozzle_max": 275, "bed_min": 55, "bed_max": 95},
}
# An optional line number may precede the code, and the code may carry leading zeros ("M0112"
# is M112 to the firmware), so the code is read in its normalized form.
COMMAND = re.compile(rb"^\s*(?:N\d+\s*)?([GM])0*(\d+)\b")


def _command_code(line: bytes) -> bytes | None:
    label = COMMAND.match(line)
    if label is None:
        return None
    return label.group(1) + str(int(label.group(2))).encode("ascii")
S_VALUE = re.compile(rb"(?:^|\s)S(-?\d+(?:\.\d+)?)\b")
E_VALUE = re.compile(rb"(?:^|\s)E([+-]?(?:\d+(?:\.\d*)?|\.\d+))")
SINGLE_PLATE = re.compile(r"^Metadata/plate_\d+\.gcode$")
HASH_FORMAT = re.compile(rb"^[0-9a-fA-F]{32}$")
# Never forward maintenance, reboot, emergency-stop or EEPROM-reset commands
# from an untrusted artifact. This list is deliberately conservative.
FORBIDDEN_COMMANDS = frozenset(
    {b"M112", b"M303", b"M500", b"M501", b"M502", b"M851", b"M997", b"M999"}
)


class PlateError(ValueError):
    """Malformed, untrusted or incompatible printing artifact."""


def _preflight_members(archive: ZipFile) -> dict[str, object]:
    members = archive.infolist()
    if len(members) > MAX_ENTRIES:
        raise PlateError("too many 3MF archive members")
    names: dict[str, object] = {}
    total = 0
    for member in members:
        name = member.filename
        if (
            name in names
            or not name
            or name.startswith("/")
            or "\\" in name
            or any(part in ("", ".", "..") for part in name.rstrip("/").split("/"))
            or member.flag_bits & 1
            or stat.S_ISLNK(member.external_attr >> 16)
        ):
            raise PlateError("unsafe or duplicated 3MF archive member")
        names[name] = member
        total += member.file_size
        if (
            member.file_size > MAX_FILE_BYTES
            or total > MAX_ARCHIVE_UNCOMPRESSED
            or member.file_size > max(member.compress_size, 1) * 1000
        ):
            raise PlateError("3MF archive exceeds decompression limit")
    return names


def _validated_wall_loops(value: object) -> int | None:
    if value is None:
        return None
    if type(value) is int:
        count = value
    elif type(value) is str and len(value) <= 2 and value.isdecimal():
        count = int(value)
    else:
        raise PlateError("invalid slicer wall count")
    if not 1 <= count <= 64:
        raise PlateError("invalid slicer wall count")
    return count


def _configuration(archive: ZipFile, names: dict[str, object]) -> tuple[str, float, int | None]:
    if CONFIG_NAME not in names or MODEL_NAME not in names:
        raise PlateError("missing 3MF model or project configuration")
    info = archive.getinfo(CONFIG_NAME)
    if info.file_size > MAX_CONFIG_BYTES:
        raise PlateError("3MF configuration exceeds size limit")
    try:
        settings = json.loads(archive.read(CONFIG_NAME))
    except (ValueError, UnicodeError) as exc:
        raise PlateError("invalid project configuration") from exc
    if not isinstance(settings, dict) or settings.get("printer_model") != "Bambu Lab A1 mini":
        raise PlateError("printer profile is not Bambu Lab A1 mini")
    materials = settings.get("filament_type")
    if (
        not isinstance(materials, list)
        or len(materials) != 1
        or type(materials[0]) is not str
        or materials[0] not in PROFILES
    ):
        raise PlateError("unsupported or ambiguous filament type")
    nozzle = settings.get("nozzle_diameter")
    if not isinstance(nozzle, list) or len(nozzle) != 1:
        raise PlateError("missing nozzle diameter")
    try:
        diameter = float(nozzle[0])
    except (ValueError, TypeError, OverflowError) as exc:
        raise PlateError("invalid nozzle diameter") from exc
    if not math.isfinite(diameter) or diameter != 0.4:
        raise PlateError("nozzle mismatch: this profile requires 0.4 mm")
    return materials[0], diameter, _validated_wall_loops(settings.get("wall_loops"))


def _parse_temperature(command: bytes, material: str) -> tuple[str, float] | None:
    code = _command_code(command)
    if code is None:
        return None
    if code not in (b"M104", b"M109", b"M140", b"M190"):
        return None
    match = S_VALUE.search(command)
    if match is None:
        return None
    value = float(match.group(1))
    if not math.isfinite(value) or value < 0:
        raise PlateError("invalid G-code temperature")
    is_nozzle = code in (b"M104", b"M109")
    field = "nozzle" if is_nozzle else "bed"
    # S0 turns heating off, and M104/M140 can preheat below target.
    if value and value > PROFILES[material][field + "_max"]:
        raise PlateError("G-code exceeds allowed " + field + " temperature")
    return code.decode("ascii"), value


def _sliced_plate_info(archive: ZipFile, names: dict[str, object]) -> tuple[ZipInfo, bytes]:
    if GCODE_NAME not in names or GCODE_MD5_NAME not in names:
        raise PlateError("missing sliced G-code or integrity checksum")
    if len([name for name in names if SINGLE_PLATE.fullmatch(name)]) != 1:
        raise PlateError("multiple or ambiguous sliced plates")
    info = archive.getinfo(GCODE_NAME)
    if not 0 < info.file_size <= MAX_FILE_BYTES:
        raise PlateError("empty or oversized G-code")
    md5_info = archive.getinfo(GCODE_MD5_NAME)
    if md5_info.file_size > 64:
        raise PlateError("G-code integrity checksum exceeds size limit")
    expected = archive.read(GCODE_MD5_NAME).strip()
    if not HASH_FORMAT.fullmatch(expected):
        raise PlateError("invalid G-code integrity checksum")
    return info, expected


def _scan_line(line: bytes, material: str, temperatures: dict[str, float]) -> tuple[int, int]:
    physical = line.split(b";", 1)[0].strip().upper()
    if not physical:
        return 0, 0
    code = _command_code(physical)
    if code is not None and code in FORBIDDEN_COMMANDS:
        raise PlateError("G-code contains a prohibited maintenance command")
    movement = int(code in (b"G0", b"G1"))
    extrusion = E_VALUE.search(physical) if movement else None
    positive = int(extrusion is not None and float(extrusion.group(1)) > 0)
    read_temp = _parse_temperature(physical, material)
    if read_temp is not None:
        code, value = read_temp
        if value:
            temperatures[code] = value
    return movement, positive


def _heat_targets(material: str, temperatures: dict[str, float]) -> tuple[float, float]:
    nozzle_target = temperatures.get("M109")
    bed_target = temperatures.get("M190")
    profile = PROFILES[material]
    if (
        nozzle_target is None
        or not profile["nozzle_min"] <= nozzle_target <= profile["nozzle_max"]
        or bed_target is None
        or not profile["bed_min"] <= bed_target <= profile["bed_max"]
    ):
        raise PlateError("missing or incompatible nozzle/bed heat-and-wait commands")
    return nozzle_target, bed_target


def _gcode(archive: ZipFile, names: dict[str, object], material: str) -> dict[str, object]:
    info, expected = _sliced_plate_info(archive, names)
    checksum = hashlib.md5(usedforsecurity=False)
    digest = hashlib.sha256()
    moves = 0
    extrusions = 0
    temperatures: dict[str, float] = {}
    with archive.open(info) as data:
        while line := data.readline(MAX_GCODE_LINE_BYTES + 1):
            if len(line) > MAX_GCODE_LINE_BYTES and not line.endswith(b"\n"):
                raise PlateError("G-code line exceeds size limit")
            checksum.update(line)
            digest.update(line)
            movement, extrusion = _scan_line(line, material, temperatures)
            moves += movement
            extrusions += extrusion
    if checksum.hexdigest().encode("ascii") != expected.lower():
        raise PlateError("G-code checksum mismatch")
    if moves == 0 or extrusions == 0:
        raise PlateError("G-code contains no printable extrusion path")
    nozzle_target, bed_target = _heat_targets(material, temperatures)
    return {
        "gcode_bytes": info.file_size,
        "gcode_sha256": digest.hexdigest(),
        "motion_commands": moves,
        "extrusion_commands": extrusions,
        "nozzle_target_c": nozzle_target,
        "bed_target_c": bed_target,
    }


def inspect_archive(stream: BinaryIO, filename: str) -> dict[str, object]:
    """Read an existing artifact without extraction, network or printer control."""
    if not filename.endswith(".3mf"):
        return {"status": "blocked", "reason": "expected a .3mf artifact", "filename": filename}
    stream.seek(0, 2)
    size = stream.tell()
    stream.seek(0)
    if not 0 < size <= MAX_FILE_BYTES:
        return {"status": "blocked", "reason": "3MF file exceeds size limit", "filename": filename}
    sha = hashlib.sha256()
    while block := stream.read(65536):
        sha.update(block)
    stream.seek(0)
    try:
        with ZipFile(stream) as archive:
            names = _preflight_members(archive)
            # Source-only .3mf is not a file the printer can start directly.
            if GCODE_NAME not in names:
                return {
                    "status": "needs_slicing",
                    "filename": filename,
                    "sha256": sha.hexdigest(),
                    "reason": "source 3MF has no sliced plate_1.gcode",
                }
            if not filename.endswith(".gcode.3mf"):
                raise PlateError("sliced print must be named .gcode.3mf")
            material, nozzle, wall_loops = _configuration(archive, names)
            result = _gcode(archive, names, material)
    except (
        PlateError,
        BadZipFile,
        LargeZipFile,
        OSError,
        EOFError,
        RuntimeError,
        zlib.error,
    ) as exc:
        # A member whose compressed stream is corrupt raises zlib.error, which is a blocked print,
        # not a crash of the audit.
        return {"status": "blocked", "filename": filename, "reason": str(exc)}
    return {
        "status": "ready_for_review",
        "print_ready": False,
        "unverified_checks": [
            "3mf_package_structure",
            "machine_path_bounds",
            "heater_state_at_extrusion",
            "mesh_geometry",
            "physical_fit",
            "live_printer_readiness",
        ],
        "filename": filename,
        "sha256": sha.hexdigest(),
        "printer": "Bambu Lab A1 mini",
        "material": material,
        "nozzle_mm": nozzle,
        "wall_loops": wall_loops,
        **result,
        "warning": (
            "Archive checks do not prove complete 3MF package validity, machine path bounds, "
            "heater state at extrusion, mesh geometry, mechanical fit, material on spool, "
            "camera quality or live printer readiness. Print start needs explicit approval."
        ),
    }


def inspect_path(path: Path) -> dict[str, object]:
    with path.open("rb") as stream:
        return inspect_archive(stream, path.name)


def main() -> int:
    parser = argparse.ArgumentParser(description="Read-only Bambu A1 mini single-file preflight")
    parser.add_argument("artifact", type=Path)
    args = parser.parse_args()
    result = inspect_path(args.artifact)
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 0 if result["status"] == "ready_for_review" else 2


if __name__ == "__main__":
    raise SystemExit(main())
