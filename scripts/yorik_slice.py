"""Slice the reviewed Yorik mesh using the pinned PR1101 Bambu profiles."""

import argparse
import hashlib
import json
import os
import struct
import subprocess
import zipfile
from pathlib import Path

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
        raise ValueError(
            f"slicer warning/object audit: {plate.get('warning_message')!r}; objects={len(objects)}"
        )
    obj = objects[0]
    size = obj.get("bbox", {})
    if obj.get("triangle_count") != count:
        raise ValueError("mesh triangles changed")
    expected = {"width": 34, "depth": 34, "height": 53.7}
    if any(abs(float(size.get(k, 0)) - v) > 0.3 for k, v in expected.items()):
        raise ValueError(f"Yorik dimensions changed: {size!r}")
    return plate


def place_mesh(mesh, destination):
    data = bytearray(mesh.read_bytes())
    count = validate_mesh(mesh)
    for index in range(count):
        offset = 84 + 50 * index + 12
        values = list(struct.unpack_from("<9f", data, offset))
        for vertex in range(3):
            values[3 * vertex] += 90
            values[3 * vertex + 1] += 90
        struct.pack_into("<9f", data, offset, *values)
    destination.write_bytes(data)
    return destination


def preview_mesh(source, destination):
    """Cull microscopic triangles for raster preview only; preserve print input."""
    raw = source.read_bytes()
    count = struct.unpack_from("<I", raw, 80)[0]
    faces = []
    for index in range(count):
        record = raw[84 + 50 * index : 84 + 50 * (index + 1)]
        values = struct.unpack("<12fH", record)
        a, b, c = values[3:6], values[6:9], values[9:12]
        u, v = [b[j] - a[j] for j in range(3)], [c[j] - a[j] for j in range(3)]
        cross = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        if sum(x * x for x in cross) >= 1e-14:
            faces.append(record)
    if not faces:
        raise ValueError("empty raster preview")
    destination.write_bytes(raw[:80] + struct.pack("<I", len(faces)) + b"".join(faces))
    return count - len(faces)


def validate_process(profile):
    expected = {
        "enable_support": "1",
        "support_type": "normal(auto)",
        "wall_loops": "3",
        "wall_generator": "classic",
        "detect_thin_wall": "0",
    }
    if any(profile.get(k) != v for k, v in expected.items()):
        raise ValueError("slicer did not preserve the supported figurine process")


def slice_model(root, mesh, out):
    from a1mini_pla_cube_slice import (
        _read_sliced_archive,
        _validate_gcode,
        _validate_profile,
        profiles,
    )
    from a1mini_preview import embed_preview

    out.mkdir(parents=True, exist_ok=True)
    count = validate_mesh(mesh)
    placed = place_mesh(mesh, out / "yorik_on_bed.stl")
    machine, process, filament = profiles(root / "resources/profiles/BBL", out)
    settings = json.loads(process.read_text())
    settings.update(
        enable_support="1",
        support_type="normal(auto)",
        wall_loops="3",
        wall_generator="classic",
        detect_thin_wall="0",
    )
    process.write_text(json.dumps(settings))
    env = dict(os.environ, LC_ALL="C", LD_LIBRARY_PATH=str(root / "bin"))
    command = [
        str(root / "bin/bambu-studio"),
        "--arrange",
        "1",
        "--allow-rotations=0",
        "--orient",
        "0",
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
        str(placed),
    ]
    subprocess.run(command, cwd=out, env=env, timeout=300, check=True)
    destination = out / OUTPUT
    profile, gcode = _read_sliced_archive(destination)
    _validate_profile(profile)
    validate_process(profile)
    _validate_gcode(gcode)
    plate = validate_result(out / "result.json", count)
    preview_source = out / "yorik_preview_only.stl"
    culled = preview_mesh(placed, preview_source)
    preview = embed_preview(destination, preview_source)
    preview["raster_only_omitted_microscopic_faces"] = culled
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
    slice_model(
        args.slicer_root.resolve(), args.mesh.resolve(), args.output_dir.resolve()
    )
