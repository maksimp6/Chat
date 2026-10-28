"""Source-mesh validation before any 3D printer upload."""

import hashlib
import math
import struct
from collections import Counter
from pathlib import Path
from typing import Any


def _read_mesh_bounds_and_edges(
    data: bytes,
) -> tuple[list[float], list[float], list[float]]:
    # Merge exact shared vertex coordinates for manifold-edge checks.
    points: dict[tuple[float, float, float], int] = {}
    edges: Counter[tuple[int, ...]] = Counter()
    xs, ys, zs = [], [], []
    for offset in range(84, len(data), 50):
        values = struct.unpack_from("<12fH", data, offset)
        verts = [tuple(values[3 + 3 * i : 6 + 3 * i]) for i in range(3)]
        if not all(math.isfinite(n) for v in verts for n in v):
            raise ValueError("nonfinite STL coordinates")
        ids = []
        for v in verts:
            ids.append(points.setdefault(v, len(points)))
            xs.append(v[0])
            ys.append(v[1])
            zs.append(v[2])
        if len(set(ids)) != 3:
            raise ValueError("degenerate STL face")
        for a, b in ((0, 1), (1, 2), (2, 0)):
            edges[tuple(sorted((ids[a], ids[b])))] += 1
    if any(n != 2 for n in edges.values()):
        raise ValueError("STL open or nonmanifold topology")
    return xs, ys, zs


def validate_stl(
    path: str | Path,
    record: dict[str, Any],
    bed: tuple[int, int, int] = (180, 180, 180),
) -> dict[str, Any]:
    """Validate original binary STL, indexed topology and per-part dimensions."""
    path = Path(path)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != record["sha256"]:
        raise ValueError("STL source hash mismatch")
    if len(data) < 84:
        raise ValueError("STL truncated")
    count = struct.unpack_from("<I", data, 80)[0]
    if count != record["geometry"]["triangles"] or len(data) != 84 + 50 * count:
        raise ValueError("STL face count/length mismatch")
    xs, ys, zs = _read_mesh_bounds_and_edges(data)
    bounds = [max(v) - min(v) for v in (xs, ys, zs)]
    if any(d <= 0 or d > bed[i] for i, d in enumerate(bounds)):
        raise ValueError("STL dimensions outside target printer")
    expected = record["geometry"]["bounds_mm"]
    if any(abs(bounds[i] - expected[i]) > 0.03 for i in range(3)):
        raise ValueError("STL dimensions mismatch passport")
    return {
        "sha256": record["sha256"],
        "triangles": count,
        "bounds_mm": bounds,
        "topology": "closed_edge_manifold",
    }


def verify_inventory_sources(
    manifest: dict[str, Any], root: str | Path
) -> dict[str, list[dict[str, Any]]]:
    """All source models, including alternatives and gauges, must pass."""
    results: dict[str, list[dict[str, Any]]] = {}
    for model in manifest["models"]:
        verified = []
        for member in model["members_manifest"]:
            if member["path"].lower().endswith(".stl"):
                path = Path(root) / member["path"]
                verified.append({"path": member["path"], **validate_stl(path, member)})
        if not verified:
            raise ValueError("model has no validated STL source")
        results[model["id"]] = verified
    return results


def require_upload_preflight(
    passport: dict[str, Any],
    source_evidence: list[dict[str, Any]],
    gcode_evidence: dict[str, Any],
) -> dict[str, Any]:
    """Fail closed for missing evidence; do not equate static checks with fit."""
    if not source_evidence or not gcode_evidence:
        raise ValueError("source and sliced G-code verification required")
    if gcode_evidence.get("status") not in (
        "GCODE_STATIC_VERIFIED",
        "GCODE_VERIFIED_FIT_BLOCKED",
    ):
        raise ValueError("G-code has not passed independent audit")
    if not all(e.get("topology") == "closed_edge_manifold" for e in source_evidence):
        raise ValueError("source geometry verification failed")
    if passport["target_printer"].get("firmware_compatibility_verified") is not True:
        raise ValueError("target printer firmware acceptance not verified")
    return {
        "status": "UPLOAD_PREFLIGHT_PASSED",
        "printer": passport["target_printer"]["device_id"],
    }
