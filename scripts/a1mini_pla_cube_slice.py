"""Verified, deterministic A1 mini PLA calibration cube slicing using Bambu Studio CLI.

Does not access printers, network, secret stores or print-start controls.
All physical printer actions are separate from this artifact-generation stage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import struct
import subprocess
import sys
import zipfile
from pathlib import Path

if __package__:
    from scripts.a1mini_preview import embed_preview
else:
    from a1mini_preview import embed_preview

NOZZLE_C = 220
BED_C = 60
SIZE_MM = 20.0
FILAMENT_TYPE = "PLA"
PRINTER = "Bambu Lab A1 mini"
NOZZLE_MM = "0.4"
BED_TYPE = "Textured PEI Plate"
SLICED_FILENAME = "Logos_Cube_20mm_Generic_PLA_A1mini.gcode.3mf"
EXPECTED_LAYERS = 100  # 20 mm cube at the pinned 0.20 mm layer height
_LAYER_HEADER = re.compile(r"^; total layer number: (\d+)[ \t\r]*$", re.MULTILINE)


def cube_stl() -> bytes:
    half = SIZE_MM / 2
    verts = [
        (-half, -half, 0.0),
        (half, -half, 0.0),
        (half, half, 0.0),
        (-half, half, 0.0),
        (-half, -half, SIZE_MM),
        (half, -half, SIZE_MM),
        (half, half, SIZE_MM),
        (-half, half, SIZE_MM),
    ]
    triangles = [
        (0, 2, 1),
        (0, 3, 2),
        (4, 5, 6),
        (4, 6, 7),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    payload = bytearray(b"Logos A1 mini Generic PLA 20 mm cube".ljust(80, b" "))
    payload.extend(struct.pack("<I", len(triangles)))
    volume = 0.0
    for face in triangles:
        a, b, c = (verts[i] for i in face)
        u = tuple(b[i] - a[i] for i in range(3))
        v = tuple(c[i] - a[i] for i in range(3))
        normal = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        mag = math.sqrt(sum(n * n for n in normal))
        normal = tuple(n / mag for n in normal)
        payload.extend(struct.pack("<12fH", *(normal + a + b + c), 0))
        cross = (
            b[1] * c[2] - b[2] * c[1],
            b[2] * c[0] - b[0] * c[2],
            b[0] * c[1] - b[1] * c[0],
        )
        volume += sum(a[i] * cross[i] for i in range(3)) / 6
    if abs(volume - 8000) > 0.01:
        raise ValueError("cube STL signed volume is invalid")
    if len(payload) != 684:
        raise ValueError("cube STL length is invalid")
    return bytes(payload)


def profiles(profile_root: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    machine = profile_root / "machine" / "Bambu Lab A1 mini 0.4 nozzle.json"
    process_source = profile_root / "process" / "0.20mm Standard @BBL A1M.json"
    filament_base = profile_root / "filament" / "Generic PLA @base.json"
    filament_override = profile_root / "filament" / "Generic PLA @BBL A1M.json"
    filament = json.loads(filament_base.read_text(encoding="utf-8"))
    filament.update(json.loads(filament_override.read_text(encoding="utf-8")))
    filament.pop("inherits", None)
    filament.update(
        {
            "name": "Logos Verified Generic PLA A1mini 0.4",
            "filament_type": ["PLA"],
            "filament_density": ["1.24"],
            "nozzle_temperature": [str(NOZZLE_C)],
            "nozzle_temperature_initial_layer": [str(NOZZLE_C)],
            "textured_plate_temp": [str(BED_C)],
            "textured_plate_temp_initial_layer": [str(BED_C)],
            "filament_max_volumetric_speed": ["8"],
        }
    )
    filament_path = output_dir / "generic_pla_resolved.json"
    filament_path.write_text(json.dumps(filament, indent=2) + "\n", encoding="utf-8")

    process = json.loads(process_source.read_text(encoding="utf-8"))
    process["name"] = "Logos A1mini PLA Standard 0.20mm Textured PEI"
    process["curr_bed_type"] = BED_TYPE
    process_path = output_dir / "a1mini_process.json"
    process_path.write_text(json.dumps(process, indent=2) + "\n", encoding="utf-8")
    return machine, process_path, filament_path


def _read_sliced_archive(path: Path) -> tuple[dict[str, object], str]:
    if not path.is_file():
        raise RuntimeError("Bambu Studio did not produce the sliced 3MF")
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None:
            raise RuntimeError("3MF ZIP failed CRC validation")
        names = set(archive.namelist())
        for required in ("Metadata/project_settings.config", "Metadata/plate_1.gcode"):
            if required not in names:
                raise RuntimeError(f"3MF is missing {required}")
        settings = json.loads(archive.read("Metadata/project_settings.config"))
        gcode = archive.read("Metadata/plate_1.gcode").decode("utf-8", errors="replace")
    if not isinstance(settings, dict):
        raise TypeError("invalid project settings JSON")
    return settings, gcode


def _validate_profile(settings: dict[str, object]) -> None:
    expected: dict[str, object] = {
        "filament_type": ["PLA"],
        "filament_density": ["1.24"],
        "printer_model": PRINTER,
        "printer_variant": NOZZLE_MM,
        "curr_bed_type": BED_TYPE,
        "nozzle_temperature": [str(NOZZLE_C)],
        "nozzle_temperature_initial_layer": [str(NOZZLE_C)],
        "textured_plate_temp": [str(BED_C)],
        "textured_plate_temp_initial_layer": [str(BED_C)],
    }
    for key, wanted in expected.items():
        actual = settings.get(key)
        if actual != wanted:
            raise RuntimeError(f"unsafe profile: {key}: expected {wanted!r}, got {actual!r}")


def _layer_count(gcode: str) -> int:
    """Return the layer count from the G-code header; every occurrence must agree."""
    counts = {int(value) for value in _LAYER_HEADER.findall(gcode)}
    if not counts:
        raise RuntimeError("G-code has no layer-count header")
    if len(counts) != 1:
        raise RuntimeError("G-code layer-count headers disagree")
    (layers,) = counts
    if layers <= 0:
        raise RuntimeError("G-code layer count is not positive")
    return layers


def _validate_gcode(gcode: str) -> int:
    # Do not mistake commented material settings for executable G-code commands.
    commands = [
        line.strip()
        for line in gcode.splitlines()
        if line.strip() and not line.lstrip().startswith(";")
    ]
    for command, target in (("M109", NOZZLE_C), ("M190", BED_C)):
        if not any(re.match(rf"^{command}\s+S{target}(?:\s|;|$)", line) for line in commands):
            raise RuntimeError(f"G-code missing verified {command} S{target} temperature")
    if not gcode.startswith("; HEADER_BLOCK_START"):
        raise RuntimeError("not a Bambu Studio G-code header")
    if len(gcode) < 5000:
        raise RuntimeError("G-code is unexpectedly small")
    return _layer_count(gcode)


def _validate_result(result_path: Path) -> dict[str, object]:
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("return_code") != 0:
        raise RuntimeError(f"slicer failed: {result.get('error_string')}")
    plates = result.get("sliced_plates") or []
    if len(plates) != 1:
        raise RuntimeError("expected exactly one sliced plate")
    plate = plates[0]
    if plate.get("warning_message"):
        raise RuntimeError(f"slicer warning: {plate['warning_message']}")
    objects = plate.get("objects") or []
    if len(objects) != 1 or (objects[0].get("triangle_count") != 12):
        raise RuntimeError("cube geometry was not preserved in slicing")
    dimensions = objects[0].get("bbox", {})
    size = (
        dimensions.get("width", 0),
        dimensions.get("depth", 0),
        dimensions.get("height", 0),
    )
    if not 19.8 <= size[2] <= 20.2:
        raise RuntimeError(f"unexpected model height: {size!r}")
    if any(float(value) <= 0 or float(value) > 180 for value in size):
        raise RuntimeError(f"invalid A1 mini build dimensions: {size!r}")
    return {
        "estimated_seconds": plate.get("total_predication"),
        "model_object_id": objects[0].get("id"),
    }


def audit(path: Path, result_path: Path) -> dict[str, object]:
    settings, gcode = _read_sliced_archive(path)
    _validate_profile(settings)
    layers = _validate_gcode(gcode)
    if layers != EXPECTED_LAYERS:
        raise RuntimeError(f"G-code layer count {layers} != expected {EXPECTED_LAYERS}")
    slice_info = _validate_result(result_path)
    blob = path.read_bytes()
    return {
        "filename": path.name,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size_bytes": len(blob),
        "material": FILAMENT_TYPE,
        "nozzle_c": NOZZLE_C,
        "bed_c": BED_C,
        "plate": BED_TYPE,
        "printer": PRINTER,
        "nozzle_mm": NOZZLE_MM,
        **slice_info,
        "layers": layers,
        "source": "Bambu Studio 02.08.02.61 CLI",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slicer-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    root = args.slicer_root.resolve()
    executable = root / "bin" / "bambu-studio"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    stl = out / "Logos_Cube_20mm_PLA.stl"
    stl.write_bytes(cube_stl())
    print("STL_VERIFIED", hashlib.sha256(stl.read_bytes()).hexdigest(), flush=True)

    profile_root = root / "resources" / "profiles" / "BBL"
    machine, process, filament = profiles(profile_root, out)
    env = dict(os.environ)
    env.update(LC_ALL="C", LD_LIBRARY_PATH=str(root / "bin"))
    command = [
        str(executable),
        "--slice",
        "0",
        "--export-3mf",
        SLICED_FILENAME,
        "--load-settings",
        f"{machine};{process}",
        "--load-filaments",
        str(filament),
        "--outputdir",
        str(out),
        str(stl),
    ]
    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        cwd=out,
        env=env,
        timeout=150,
        check=False,
    )
    print("SLICER_EXIT", result.returncode, flush=True)
    print("SLICER_LOG_TAIL", (result.stdout + result.stderr)[-1200:], flush=True)
    if result.returncode != 0:
        raise RuntimeError("Bambu Studio CLI exited nonzero")
    destination = out / SLICED_FILENAME
    preview = embed_preview(destination, stl)
    with zipfile.ZipFile(destination) as image_archive:
        (out / "preview.png").write_bytes(image_archive.read("Metadata/plate_1.png"))
    report = audit(destination, out / "result.json")
    report["preview"] = preview
    (out / "audit.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print("SLICED_VERIFIED", json.dumps(report, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (
        OSError,
        ValueError,
        RuntimeError,
        KeyError,
        TypeError,
        subprocess.SubprocessError,
        zipfile.BadZipFile,
    ) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
