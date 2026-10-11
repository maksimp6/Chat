"""Deterministic, printer-agnostic FDM geometry test coupons (no printer I/O)."""

from __future__ import annotations

import hashlib
import json
import math
import struct
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any

TRIANGLE_STRUCT = struct.Struct("<12fH")
FACET_LIMIT = 100_000
BYTE_LIMIT = 5_000_000
MAX_COUPON_AXIS_MM = 170.0  # conservative *artifact* gate, not full plate placement

Point = tuple[float, float, float]
Triangle = tuple[Point, Point, Point]
Edge = tuple[Point, Point]
Coupon = tuple[list[Triangle], tuple[float, float, float], str]


def _subtract(a: Point, b: Point) -> Point:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Point, b: Point) -> Point:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _dot(a: Point, b: Point) -> float:
    return sum(x * y for x, y in zip(a, b))


def _triangles_for_prism(profile: list[tuple[float, float]], height: float) -> list[Triangle]:
    if len(profile) < 3 or height <= 0:
        raise ValueError("invalid prism")
    area2 = sum(profile[i][0] * profile[(i + 1) % len(profile)][1] - profile[(i + 1) % len(profile)][0] * profile[i][1] for i in range(len(profile)))
    if not math.isfinite(area2) or area2 <= 0:
        raise ValueError("prism profile must be CCW")
    bottom = [(x, y, 0.0) for x, y in profile]
    top = [(x, y, height) for x, y in profile]
    result: list[Triangle] = []
    for i in range(1, len(profile) - 1):
        result.extend([(bottom[0], bottom[i + 1], bottom[i]), (top[0], top[i], top[i + 1])])
    for i in range(len(profile)):
        j = (i + 1) % len(profile)
        result.extend([(bottom[i], bottom[j], top[j]), (bottom[i], top[j], top[i])])
    return result


def _ring(outer: float, inner: float, height: float, segments: int) -> list[Triangle]:
    if not (outer > inner > 0 and height > 0 and segments >= 12):
        raise ValueError("invalid ring")
    o = [(outer * math.cos(2 * math.pi * i / segments), outer * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
    h = [(inner * math.cos(2 * math.pi * i / segments), inner * math.sin(2 * math.pi * i / segments)) for i in range(segments)]
    triangles: list[Triangle] = []
    for i in range(segments):
        j = (i + 1) % segments
        oi, oj = o[i], o[j]
        hi, hj = h[i], h[j]
        ob, nb = (oi[0], oi[1], 0.0), (oj[0], oj[1], 0.0)
        ib, jb = (hi[0], hi[1], 0.0), (hj[0], hj[1], 0.0)
        ot, nt = (oi[0], oi[1], height), (oj[0], oj[1], height)
        it, jt = (hi[0], hi[1], height), (hj[0], hj[1], height)
        triangles.extend([
            (ot, nt, jt), (ot, jt, it),
            (ob, jb, nb), (ob, ib, jb),
            (ob, nb, nt), (ob, nt, ot),
            (jb, ib, it), (jb, it, jt),
        ])
    return triangles


def _translate_to_positive(triangles: list[Triangle]) -> list[Triangle]:
    mins: Point = (
        min(vertex[0] for tri in triangles for vertex in tri),
        min(vertex[1] for tri in triangles for vertex in tri),
        min(vertex[2] for tri in triangles for vertex in tri),
    )
    return [
        (_subtract(tri[0], mins), _subtract(tri[1], mins), _subtract(tri[2], mins))
        for tri in triangles
    ]


def _samples() -> dict[str, Coupon]:
    n = 96
    circle = [(10 * math.cos(2 * math.pi * i / n), 10 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    triangle = [(0.0, 12.0), (-6 * math.sqrt(3), -6.0), (6 * math.sqrt(3), -6.0)]
    upright = _triangles_for_prism(circle, 12)
    horizontal: list[Triangle] = [
        ((a[0], -a[2], a[1]), (b[0], -b[2], b[1]), (c[0], -c[2], c[1]))
        for a, b, c in _triangles_for_prism(circle, 24)
    ]
    wedge: list[Triangle] = [
        ((a[2], a[0], a[1]), (b[2], b[0], b[1]), (c[2], c[0], c[1]))
        for a, b, c in _triangles_for_prism([(0, 0), (30, 0), (30, 15)], 12)
    ]
    return {
        "circle_vertical": (upright, (20, 20, 12), "XY circularity at three Z levels; seam and surface"),
        "triangle": (_triangles_for_prism(triangle, 12), (12 * math.sqrt(3), 18, 12), "three sides, corner rounding and angles"),
        "square": (_triangles_for_prism([(-10, -10), (10, -10), (10, 10), (-10, 10)], 10), (20, 20, 10), "X/Y skew, squareness and corners"),
        "hole_ring": (_ring(16, 6, 8, n), (32, 32, 8), "outer/inner diameters in XY; ring thickness"),
        "circle_horizontal": (horizontal, (20, 24, 20), "bottom arc, support scars; requires dedicated orientation review"),
        "inclined_wedge": (wedge, (12, 30, 15), "inclined top surface, staircase effect and warp"),
    }


def encode_binary_stl(triangles: list[Triangle]) -> bytes:
    if len(triangles) > FACET_LIMIT:
        raise ValueError("too many facets")
    data = bytearray(b"Alice Print Test geometry in mm; source-only".ljust(80, b"\0"))
    data += struct.pack("<I", len(triangles))
    for tri in triangles:
        a, b, c = tri
        normal = _cross(_subtract(b, a), _subtract(c, a))
        magnitude = math.sqrt(_dot(normal, normal))
        if magnitude < 1e-12:
            raise ValueError("degenerate triangle")
        normal = (normal[0] / magnitude, normal[1] / magnitude, normal[2] / magnitude)
        data += TRIANGLE_STRUCT.pack(*(normal + a + b + c + (0,)))
    return bytes(data)


def _read_facets(data: bytes) -> list[Triangle]:
    if not 84 <= len(data) <= BYTE_LIMIT:
        raise ValueError("STL truncated or exceeds fixture limit")
    facets = struct.unpack_from("<I", data, 80)[0]
    if facets < 4 or facets > FACET_LIMIT or len(data) != 84 + facets * TRIANGLE_STRUCT.size:
        raise ValueError("STL triangle count/length mismatch")
    triangles: list[Triangle] = []
    unique_faces: set[tuple[Point, Point, Point]] = set()
    for index in range(facets):
        row = TRIANGLE_STRUCT.unpack_from(data, 84 + index * TRIANGLE_STRUCT.size)
        if row[-1] != 0 or not all(math.isfinite(v) for v in row[:-1]):
            raise ValueError("STL non-finite coordinate or nonzero facet attribute")
        normal: Point = row[0], row[1], row[2]
        tri: Triangle = (
            (round(row[3], 5), round(row[4], 5), round(row[5], 5)),
            (round(row[6], 5), round(row[7], 5), round(row[8], 5)),
            (round(row[9], 5), round(row[10], 5), round(row[11], 5)),
        )
        a, b, c = tri
        cross = _cross(_subtract(b, a), _subtract(c, a))
        area = math.sqrt(_dot(cross, cross))
        if area < 1e-8 or _dot(cross, normal) / area < 0.98:
            raise ValueError("STL degenerate triangle or inconsistent normal")
        ordered = sorted(tri)
        face_key = (ordered[0], ordered[1], ordered[2])
        if face_key in unique_faces:
            raise ValueError("STL duplicate triangle")
        unique_faces.add(face_key)
        triangles.append(tri)
    return triangles


def _verify_shell(triangles: list[Triangle]) -> float:
    edge_counts: Counter[Edge] = Counter()
    directed: Counter[Edge] = Counter()
    edge_faces: dict[Edge, list[int]] = defaultdict(list)
    signed_volume = 0.0
    for index, (a, b, c) in enumerate(triangles):
        signed_volume += _dot(a, _cross(b, c)) / 6.0
        for va, vb in ((a, b), (b, c), (c, a)):
            key: Edge = (min(va, vb), max(va, vb))
            edge_counts[key] += 1
            directed[(va, vb)] += 1
            edge_faces[key].append(index)
    if any(count != 2 for count in edge_counts.values()):
        raise ValueError("STL open/non-manifold edges")
    if any(directed[(b, a)] != 1 or count != 1 for (a, b), count in directed.items()):
        raise ValueError("STL inconsistent triangle winding")
    if signed_volume <= 0:
        raise ValueError("STL inverted or nonpositive volume")
    neighbors: dict[int, list[int]] = defaultdict(list)
    for first, second in edge_faces.values():
        neighbors[first].append(second)
        neighbors[second].append(first)
    visited = {0}
    queue = deque([0])
    while queue:
        for neighbor in neighbors[queue.popleft()]:
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append(neighbor)
    if len(visited) != len(triangles):
        raise ValueError("STL has multiple disconnected shells")
    return signed_volume


def _validate_extents(
    triangles: list[Triangle], expected_mm: tuple[float, float, float] | None,
    tolerance_mm: float,
) -> tuple[float, float, float]:
    vertices = [point for tri in triangles for point in tri]
    spans = [
        (min(v[d] for v in vertices), max(v[d] for v in vertices)) for d in range(3)
    ]
    extents: Point = (
        round(spans[0][1] - spans[0][0], 4),
        round(spans[1][1] - spans[1][0], 4),
        round(spans[2][1] - spans[2][0], 4),
    )
    if any(v <= 0 or v > MAX_COUPON_AXIS_MM for v in extents):
        raise ValueError("STL coupon dimension outside permitted range")
    if expected_mm is not None:
        if (
            not math.isfinite(tolerance_mm)
            or tolerance_mm < 0
            or len(expected_mm) != 3
            or any(not math.isfinite(e) or e <= 0 for e in expected_mm)
        ):
            raise ValueError("invalid nominal dimensions/tolerance")
        if any(abs(got - expected) > tolerance_mm for got, expected in zip(extents, expected_mm)):
            raise ValueError("STL dimensions differ from independent design specification")
    return extents


def inspect_binary_stl(
    data: bytes, expected_mm: tuple[float, float, float] | None = None,
    tolerance_mm: float = 0.03,
) -> dict[str, Any]:
    """Strict small-fixture inspector. STL units are declared in manifest, not encoded."""
    triangles = _read_facets(data)
    signed_volume = _verify_shell(triangles)
    extents = _validate_extents(triangles, expected_mm, tolerance_mm)
    return {
        "facets": len(triangles), "extents_mm": extents,
        "volume_mm3": round(signed_volume, 4), "watertight": True, "single_shell": True,
    }


def assess_dimension(
    nominal_mm: float,
    measured_mm: float | None,
    tolerance_mm: float | None,
    uncertainty_mm: float | None,
) -> dict[str, Any]:
    """Project guard-band acceptance. Not a claim of ISO-mandated thresholds."""
    if not math.isfinite(nominal_mm) or nominal_mm <= 0:
        raise ValueError("invalid nominal dimension")
    for value in (measured_mm, tolerance_mm, uncertainty_mm):
        if value is not None and (not math.isfinite(value) or value < 0):
            raise ValueError("invalid measurement")
    if measured_mm is None or tolerance_mm is None or uncertainty_mm is None:
        return {"verdict": "MEASURED_UNRATED", "error_mm": None}
    error = measured_mm - nominal_mm
    accepted = abs(error) + uncertainty_mm <= tolerance_mm + 1e-12
    return {"verdict": "PASS" if accepted else "FAIL", "error_mm": round(error, 6)}


def build_coupons(out_dir: Path) -> dict[str, Any]:
    out_dir.mkdir(parents=True, exist_ok=True)
    models = []
    for name, (triangles, expected, measurement) in sorted(_samples().items()):
        data = encode_binary_stl(_translate_to_positive(triangles))
        report = inspect_binary_stl(data, expected)
        name_stl = name + ".stl"
        (out_dir / name_stl).write_bytes(data)
        models.append({"name": name, "file": name_stl, "sha256": hashlib.sha256(data).hexdigest(), "nominal_extents_mm": [round(v, 5) for v in expected], "measured_stl": report, "measure_after_print": measurement, "state": "SOURCE_ONLY_NOT_SLICED"})
    manifest: dict[str, Any] = {"schema": 1, "units_declared": "mm", "build_volume_A1mini_mm": [180, 180, 180], "nozzle_nominal_mm": 0.4, "physical_print_approved": False, "material": "UNKNOWN", "models": models}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Generate reference STL only; does not slice or start a printer")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    result = build_coupons(args.out)
    print(f"Generated {len(result['models'])} source-only STL fixtures; physical printing not approved")
