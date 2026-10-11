"""Slice the reviewed PR #1113 3MF into an audited A1 mini PLA prototype.

This tool neither connects to a printer nor authorizes physical print start.
It reuses the pinned Bambu Studio profiles from the already proven cube lane.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from alice_a1mini_pla_cube_slice import (
    _read_sliced_archive,
    _validate_gcode,
    _validate_profile,
    profiles,
)

SOURCE_SHA256 = "6c3005537a4b5bdd25bad2e4d9c29f9c858869b02884cf6c52fe907269a267d1"
SOURCE_BYTES = 38279
EXPECTED_TRIANGLES = sorted((1460, 1412, 1068))
OUTPUT = "Alice_BagClip_PR1113_A1mini_PLA_PROTOTYPE.gcode.3mf"
NS = "{http://schemas.microsoft.com/3dmanufacturing/core/2015/02}"


def validate_source(path: Path) -> str:
    data = path.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if len(data) != SOURCE_BYTES or digest != SOURCE_SHA256:
        raise RuntimeError("source artifact changed: size/hash mismatch")
    with zipfile.ZipFile(path) as z:
        if z.testzip() is not None:
            raise RuntimeError("source 3MF failed CRC")
        names = set(z.namelist())
        if names != {"[Content_Types].xml", "_rels/.rels", "3D/3dmodel.model"}:
            raise RuntimeError("unexpected 3MF source entries")
        root = ET.fromstring(z.read("3D/3dmodel.model"))
    if root.get("unit") != "millimeter":
        raise RuntimeError("source units are not millimeters")
    objects = root.findall(f".//{NS}resources/{NS}object")
    items = root.findall(f".//{NS}build/{NS}item")
    if len(objects) != 3 or len(items) != 3:
        raise RuntimeError("expected exactly three parts on one plate")
    geometry = {}
    for obj in objects:
        mesh = obj.find(f"{NS}mesh")
        if mesh is None:
            raise RuntimeError("3MF object without mesh")
        vertices = mesh.findall(f"{NS}vertices/{NS}vertex")
        triangles = mesh.findall(f"{NS}triangles/{NS}triangle")
        if not vertices or not triangles:
            raise RuntimeError("empty source object")
        points = []
        for vertex in vertices:
            point = tuple(float(vertex.get(axis, "nan")) for axis in ("x", "y", "z"))
            if not all(math.isfinite(x) for x in point):
                raise RuntimeError("non-finite source vertex")
            points.append(point)
        geometry[obj.attrib["id"]] = (points, len(triangles))
    if sorted(value[1] for value in geometry.values()) != EXPECTED_TRIANGLES:
        raise RuntimeError("unexpected source triangle counts")
    ids = set()
    for item in items:
        object_id = item.attrib["objectid"]
        if object_id not in geometry or object_id in ids:
            raise RuntimeError("invalid or duplicate 3MF build object")
        ids.add(object_id)
        matrix = [float(x) for x in item.attrib.get("transform", "").split()]
        if len(matrix) != 12 or not all(math.isfinite(x) for x in matrix):
            raise RuntimeError("invalid object transform")
        if any(abs(matrix[i] - (1 if i in (0, 4, 8) else 0)) > 1e-6 for i in range(9)):
            raise RuntimeError("unexpected source rotation or scaling")
        for x, y, z in geometry[object_id][0]:
            pos = (x + matrix[9], y + matrix[10], z + matrix[11])
            if not (1 <= pos[0] <= 179 and 1 <= pos[1] <= 179 and -0.01 <= pos[2] <= 180):
                raise RuntimeError("source object outside A1 mini build volume")
    return digest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-3mf", type=Path, required=True)
    parser.add_argument("--slicer-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    source = args.source_3mf.resolve()
    source_sha = validate_source(source)
    root = args.slicer_root.resolve()
    executable = root / "bin" / "bambu-studio"
    if not executable.is_file():
        raise FileNotFoundError(executable)
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    machine, process, filament = profiles(root / "resources/profiles/BBL", out)
    proc = json.loads(process.read_text(encoding="utf-8"))
    proc.update(wall_loops="4", sparse_infill_density="25%")
    process.write_text(json.dumps(proc, indent=2) + "\n", encoding="utf-8")
    env = dict(os.environ)
    env.update(LC_ALL="C", LD_LIBRARY_PATH=str(root / "bin"))
    command = [
        str(executable),
        "--arrange", "0",
        "--slice", "0",
        "--export-3mf", OUTPUT,
        "--load-settings", f"{machine};{process}",
        "--load-filaments", str(filament),
        "--outputdir", str(out),
        str(source),
    ]
    result = subprocess.run(
        command, capture_output=True, text=True, cwd=out, env=env,
        timeout=240, check=False,
    )
    print("SLICER_EXIT", result.returncode, flush=True)
    print("SLICER_LOG_TAIL", (result.stdout + result.stderr)[-1800:], flush=True)
    if result.returncode != 0:
        raise RuntimeError("Bambu Studio failed; do not stage artifact")
    dest = out / OUTPUT
    settings, gcode = _read_sliced_archive(dest)
    _validate_profile(settings)
    _validate_gcode(gcode)
    if settings.get("wall_loops") != "4" or settings.get("sparse_infill_density") != "25%":
        raise RuntimeError("four-wall/25-percent process not preserved")
    if not any(line.startswith(("G1 ", "G1\t")) and " E" in line
               for line in gcode.splitlines() if not line.lstrip().startswith(";")):
        raise RuntimeError("no extrusion command in sliced G-code")
    result_file = out / "result.json"
    outcome = json.loads(result_file.read_text(encoding="utf-8"))
    if outcome.get("return_code") != 0:
        raise RuntimeError("slicer result.json nonzero")
    plates = outcome.get("sliced_plates") or []
    if len(plates) != 1 or plates[0].get("warning_message"):
        raise RuntimeError("not exactly one plate or slicer warned")
    objects = plates[0].get("objects") or []
    if sorted(int(o.get("triangle_count", -1)) for o in objects) != EXPECTED_TRIANGLES:
        raise RuntimeError("parts not preserved in sliced result")
    metadata = {
        "source_sha256": source_sha,
        "source_commit": "f6b75493921b8e9b68e207216abca22e8df6340d",
        "output_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
        "filename": dest.name,
        "bytes": dest.stat().st_size,
        "printer": "Bambu Lab A1 mini",
        "nozzle_mm": "0.4",
        "material": "PLA",
        "prototype_only": True,
        "bed": "Textured PEI Plate",
        "nozzle_c": 220,
        "bed_c": 60,
        "perimeters": 4,
        "infill_percent": 25,
        "object_count": len(objects),
        "estimated_seconds": plates[0].get("total_predication"),
        "state": "SLICED_NOT_PRINTED",
    }
    (out / "bagclip_audit.json").write_text(
        json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print("BAG_CLIP_SLICED_VERIFIED", json.dumps(metadata), flush=True)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, RuntimeError, KeyError, TypeError,
            subprocess.SubprocessError, zipfile.BadZipFile, ET.ParseError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
