"""Offline verified A1 mini sliced Generic PLA 3MF for a Redmi 9 stand."""

from __future__ import annotations
from core.env import env_snapshot
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct
import subprocess
import sys
import zipfile
from a1mini_pla_cube_slice import (
    _read_sliced_archive,
    _validate_gcode,
    _validate_profile,
    profiles,
)
from a1mini_preview import embed_preview

OUTPUT = "Logos_Redmi9_Stand_v3_Generic_PLA_A1mini.gcode.3mf"
SMALL_OUTPUT = "Logos_Redmi9_Stand_Compact_Generic_PLA_A1mini.gcode.3mf"


def _collect_geometry(data: bytes, count: int):
    axes = [[], [], []]
    edges = Counter()
    for i in range(count):
        values = struct.unpack_from("<12f", data, 84 + 50 * i)
        points = [tuple(round(values[3 + 3 * j + a], 4) for a in range(3)) for j in range(3)]
        if not all(math.isfinite(v) for p in points for v in p) or len(set(points)) < 3:
            raise ValueError("invalid STL triangle")
        for point in points:
            for a, value in enumerate(point):
                axes[a].append(value)
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[tuple(sorted((points[a], points[b])))] += 1
    return axes, edges


def validate_stl(path: Path, variant: str = "v3") -> dict[str, object]:
    data = path.read_bytes()
    if len(data) < 84:
        raise ValueError("STL has no triangles")
    count = struct.unpack_from("<I", data, 80)[0]
    minimum_faces = 40 if variant == "compact" else 400
    if not minimum_faces <= count <= 10000 or len(data) != 84 + count * 50:
        raise ValueError("unexpected STL count or length")
    axes, edges = _collect_geometry(data, count)
    if any(n != 2 for n in edges.values()):
        raise ValueError("non-manifold STL")
    size = [round(max(ax) - min(ax), 2) for ax in axes]
    expected = (78, 85, 60) if variant == "compact" else (112, 104, 107)
    if any(abs(a - b) > 0.35 for a, b in zip(size, expected)):
        raise ValueError(f"stand dimension mismatch: {size}")
    if abs(min(axes[2])) > 0.01 or any(n > 180 for n in size):
        raise ValueError("STL outside A1 mini build area")
    return {
        "source_sha256": hashlib.sha256(data).hexdigest(),
        "source_faces": count,
        "size_mm": size,
        "usb_gap_mm": 27 if variant == "compact" else 30,
        "variant": variant,
    }


def inspect_sliced(path: Path, result_path: Path, variant: str = "v3") -> dict[str, object]:
    settings, gcode = _read_sliced_archive(path)
    _validate_profile(settings)
    layers = _validate_gcode(gcode)
    low_layers, high_layers = (290, 315) if variant == "compact" else (500, 545)
    if not low_layers <= layers <= high_layers:
        raise RuntimeError(f"unanticipated layer count: {layers}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    if result.get("return_code") != 0:
        raise RuntimeError("slicer result is not success")
    plates = result.get("sliced_plates") or []
    if len(plates) != 1 or plates[0].get("warning_message"):
        raise RuntimeError("slicer warning or multiple plates")
    objs = plates[0].get("objects") or []
    minimum_faces = 40 if variant == "compact" else 400
    if len(objs) != 1 or int(objs[0].get("triangle_count", 0)) < minimum_faces:
        raise RuntimeError("stand geometry lost during slicing")
    dims = objs[0].get("bbox", {})
    xyz = [float(dims.get(k, 0)) for k in ("width", "depth", "height")]
    expected_height = 60 if variant == "compact" else 107
    if not all(0 < d <= 180 for d in xyz) or abs(xyz[2] - expected_height) > 0.35:
        raise RuntimeError(f"bad sliced geometry {xyz}")
    return {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "filename": path.name,
        "size_bytes": path.stat().st_size,
        "estimated_seconds": plates[0].get("total_predication"),
        "layers": layers,
        "printer": "Bambu Lab A1 mini",
        "nozzle_mm": 0.4,
        "material": "Generic PLA",
        "plate": "Textured PEI Plate",
        "slicer": "Bambu Studio 02.08.02.61",
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--slicer-root", type=Path)
    ap.add_argument("--output-dir", type=Path, required=True)
    ap.add_argument("--validate-only", action="store_true")
    ap.add_argument("--variant", choices=("v3", "compact"), default="v3")
    args = ap.parse_args()
    info = validate_stl(args.model, args.variant)
    print("MODEL_VERIFIED", json.dumps(info, sort_keys=True), flush=True)
    if args.validate_only:
        return 0
    if args.slicer_root is None:
        raise ValueError("missing slicer-root")
    root = args.slicer_root.resolve()
    exe = root / "bin" / "bambu-studio"
    if not exe.is_file():
        raise FileNotFoundError(exe)
    out = args.output_dir.resolve()
    out.mkdir(exist_ok=True, parents=True)
    machine, process, filament = profiles(root / "resources" / "profiles" / "BBL", out)
    output_name = SMALL_OUTPUT if args.variant == "compact" else OUTPUT
    env = dict(env_snapshot(), LC_ALL="C", LD_LIBRARY_PATH=str(root / "bin"))
    cmd = [
        str(exe),
        "--slice",
        "0",
        "--export-3mf",
        output_name,
        "--load-settings",
        f"{machine};{process}",
        "--load-filaments",
        str(filament),
        "--outputdir",
        str(out),
        str(args.model.resolve()),
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=out, env=env, timeout=330)
    print("SLICER_EXIT", res.returncode, flush=True)
    print("SLICER_LOG_TAIL", (res.stdout + res.stderr)[-1500:], flush=True)
    if res.returncode:
        raise RuntimeError("Bambu Studio CLI exited nonzero")
    dest = out / output_name
    preview = embed_preview(dest, args.model)
    with zipfile.ZipFile(dest) as archive:
        (out / "preview.png").write_bytes(archive.read("Metadata/plate_1.png"))
    report = dict(
        info,
        **inspect_sliced(dest, out / "result.json", args.variant),
        preview=preview,
        state="SLICED_NOT_PRINTED",
    )
    (out / "stand_audit.json").write_text(
        json.dumps(report, sort_keys=True, indent=2) + "\n", encoding="utf-8"
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
