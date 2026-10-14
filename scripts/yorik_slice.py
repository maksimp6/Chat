"""Slice the reviewed Yorik mesh using the pinned PR1101 Bambu profiles."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import zipfile

from alice_a1mini_pla_cube_slice import (
    _read_sliced_archive,
    _validate_gcode,
    _validate_profile,
    profiles,
)
from alice_a1mini_preview import embed_preview

OUTPUT = "Yorik_A1mini_PLA.gcode.3mf"


def validate_mesh(path):
    data = path.read_bytes()
    count = struct.unpack_from("<I", data, 80)[0]
    if not 20000 <= count <= 30000 or len(data) != 84 + 50 * count:
        raise ValueError("Yorik STL has unexpected geometry")
    return count


def validate_result(path, count):
    result = json.loads(path.read_text())
    plates = result.get("sliced_plates", [])
    if result.get("return_code") != 0 or len(plates) != 1:
        raise ValueError("slicer did not produce exactly one successful plate")
    plate = plates[0]
    objects = plate.get("objects", [])
    if plate.get("warning_message") or len(objects) != 1:
        raise ValueError("slicer warning or missing Yorik object")
    obj = objects[0]
    size = obj.get("bbox", {})
    if obj.get("triangle_count") != count:
        raise ValueError("mesh triangles changed")
    expected = {"width": 34, "depth": 34, "height": 53.7}
    if any(abs(float(size.get(k, 0)) - v) > 0.3 for k, v in expected.items()):
        raise ValueError(f"Yorik dimensions changed: {size!r}")
    return plate


def slice_model(root, mesh, out):
    out.mkdir(parents=True, exist_ok=True)
    count = validate_mesh(mesh)
    machine, process, filament = profiles(root / "resources/profiles/BBL", out)
    settings = json.loads(process.read_text())
    settings.update(enable_support="1", support_type="normal(auto)", wall_loops="3")
    process.write_text(json.dumps(settings))
    env = dict(os.environ, LC_ALL="C", LD_LIBRARY_PATH=str(root / "bin"))
    command = [
        str(root / "bin/bambu-studio"),
        "--slice",
        "0",
        "--export-3mf",
        OUTPUT,
        "--load-settings",
        f"{machine};{process}",
        "--load-filaments",
        str(filament),
        "--outputdir",
        str(out),
        str(mesh),
    ]
    subprocess.run(command, cwd=out, env=env, timeout=300, check=True)
    destination = out / OUTPUT
    profile, gcode = _read_sliced_archive(destination)
    _validate_profile(profile)
    _validate_gcode(gcode)
    plate = validate_result(out / "result.json", count)
    preview = embed_preview(destination, mesh)
    with zipfile.ZipFile(destination) as archive:
        (out / "preview.png").write_bytes(archive.read("Metadata/plate_1.png"))
    audit = {
        "filename": OUTPUT,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "source_sha256": hashlib.sha256(mesh.read_bytes()).hexdigest(),
        "head_sha": os.environ["SOURCE_HEAD"],
        "size_bytes": destination.stat().st_size,
        "printer": "Bambu Lab A1 mini",
        "material": "PLA",
        "nozzle_mm": "0.4",
        "bed": "Textured PEI Plate",
        "state": "SLICED_NOT_PRINTED",
        "estimated_seconds": plate.get("total_predication"),
        "preview": preview,
    }
    (out / "audit.json").write_text(json.dumps(audit, indent=2) + "\n")
    print(json.dumps(audit))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--slicer-root", type=Path, required=True)
    parser.add_argument("--mesh", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    slice_model(args.slicer_root.resolve(), args.mesh.resolve(), args.output_dir.resolve())
