"""Validate and export a named mesh from a glTF 2.0 .glb file.

A minimal, deterministic geometry-only stage for the Dota 2 Workshop pipeline.
Unsupported geometry is rejected rather than silently approximated.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import struct
from collections import Counter, defaultdict
from pathlib import Path


class MeshError(ValueError):
    """Input asset cannot be converted safely."""


_COMPONENTS = {5121: "B", 5123: "H", 5125: "I", 5126: "f"}
_DIMENSIONS = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


def read_glb(path: Path) -> tuple[dict, bytes]:
    blob = path.read_bytes()
    if len(blob) < 20 or blob[:4] != b"glTF":
        raise MeshError("Not a GLB container")
    version, declared = struct.unpack_from("<II", blob, 4)
    if version != 2 or declared != len(blob):
        raise MeshError("GLB version or length mismatch")
    parts: dict[bytes, bytes] = {}
    cursor = 12
    while cursor < len(blob):
        if cursor + 8 > len(blob):
            raise MeshError("Truncated GLB chunk header")
        size, kind = struct.unpack_from("<I4s", blob, cursor)
        cursor += 8
        if size > len(blob) - cursor or kind in parts:
            raise MeshError("Invalid or duplicate GLB chunk")
        parts[kind] = blob[cursor : cursor + size]
        cursor += size
    if b"JSON" not in parts or b"BIN\x00" not in parts:
        raise MeshError("GLB requires JSON and BIN chunks")
    doc = json.loads(parts[b"JSON"].decode("utf-8"))
    return doc, parts[b"BIN\x00"]


def accessor(doc: dict, binary: bytes, number: int) -> list[tuple]:
    a = doc["accessors"][number]
    if "sparse" in a or "bufferView" not in a:
        raise MeshError("Sparse or missing buffer view is unsupported")
    view = doc["bufferViews"][a["bufferView"]]
    if view.get("buffer", 0) != 0:
        raise MeshError("Only the GLB binary buffer is supported")
    fmt = _COMPONENTS.get(a["componentType"])
    dims = _DIMENSIONS.get(a["type"])
    if not fmt or not dims or a.get("normalized", False):
        raise MeshError("Unsupported accessor type")
    unit = struct.calcsize("<" + fmt * dims)
    stride = view.get("byteStride", unit)
    offset = view.get("byteOffset", 0) + a.get("byteOffset", 0)
    count = a["count"]
    if not (0 <= count <= 2_000_000) or stride < unit:
        raise MeshError("Invalid accessor shape")
    if count and (
        offset < 0
        or offset + (count - 1) * stride + unit
        > view.get("byteOffset", 0) + view["byteLength"]
        or offset + (count - 1) * stride + unit > len(binary)
    ):
        raise MeshError("Accessor outside binary buffer")
    return [
        struct.unpack_from("<" + fmt * dims, binary, offset + k * stride)
        for k in range(count)
    ]


def select_mesh(doc: dict, binary: bytes, name: str) -> tuple[list, list]:
    matches = [m for m in doc.get("meshes", []) if m.get("name") == name]
    if len(matches) != 1 or len(matches[0].get("primitives", [])) != 1:
        raise MeshError("Expected exactly one named mesh and one primitive")
    primitive = matches[0]["primitives"][0]
    if primitive.get("mode", 4) != 4:
        raise MeshError("Non-triangle mesh")
    points = accessor(doc, binary, primitive["attributes"]["POSITION"])
    indices = [index[0] for index in accessor(doc, binary, primitive["indices"])]
    if not points or len(indices) % 3:
        raise MeshError("Empty mesh or invalid triangle indices")
    if any(not all(math.isfinite(v) for v in point) for point in points):
        raise MeshError("Non-finite vertex coordinates")
    if any(index < 0 or index >= len(points) for index in indices):
        raise MeshError("Triangle refers to missing vertex")
    return points, [tuple(indices[k : k + 3]) for k in range(0, len(indices), 3)]


def weld(points: list, faces: list, digits: int) -> tuple[list, list]:
    locations: dict[tuple, int] = {}
    clean = []
    remap = []
    for point in points:
        key = tuple(round(float(x), digits) for x in point[:3])
        if key not in locations:
            locations[key] = len(clean)
            clean.append(key)
        remap.append(locations[key])
    triangles = [tuple(remap[index] for index in face) for face in faces]
    if any(len(set(face)) != 3 for face in triangles):
        raise MeshError("Degenerate triangle after coordinate welding")
    return clean, triangles


def topology(faces: list) -> tuple[dict, list]:
    directed: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for a, b, c in faces:
        for u, v in ((a, b), (b, c), (c, a)):
            directed[tuple(sorted((u, v)))].append((u, v))
    boundary = [pairs[0] for pairs in directed.values() if len(pairs) == 1]
    extra = sum(len(pairs) > 2 for pairs in directed.values())
    winding = sum(
        len(pairs) == 2 and pairs[0] == pairs[1] for pairs in directed.values()
    )
    return {
        "open_edges": len(boundary),
        "nonmanifold_edges": extra,
        "winding_conflicts": winding,
    }, boundary


def boundary_loops(boundary: list) -> list[list[int]]:
    outgoing: dict[int, int] = {}
    inbound = Counter()
    for a, b in boundary:
        if a in outgoing:
            raise MeshError("Branched or overlapping boundary")
        outgoing[a] = b
        inbound[b] += 1
    if any(inbound[v] != 1 for v in outgoing) or set(inbound) != set(outgoing):
        raise MeshError("Boundary does not form closed loops")
    loops = []
    while outgoing:
        start = next(iter(outgoing))
        cursor = start
        loop = []
        while cursor in outgoing:
            loop.append(cursor)
            cursor = outgoing.pop(cursor)
            if cursor == start:
                break
        if cursor != start or len(loop) < 3:
            raise MeshError("Open or degenerate boundary loop")
        loops.append(loop)
    return loops


def cap_loop(points: list, original_direction: list[int]) -> list:
    # Reverse the existing directed boundary for consistently oriented caps.
    loop = list(reversed(original_direction))

    # Choose the projection with the largest actual polygon area.
    # Narrow slits can have two large but almost collinear dimensions.
    def projection(pair):
        coords = [(points[i][pair[0]], points[i][pair[1]]) for i in loop]
        area = sum(
            coords[k][0] * coords[(k + 1) % len(coords)][1]
            - coords[(k + 1) % len(coords)][0] * coords[k][1]
            for k in range(len(coords))
        )
        return coords, area

    options = [projection(pair) for pair in ((0, 1), (0, 2), (1, 2))]
    flat, signed = max(options, key=lambda value: abs(value[1]))

    def cross(a, b, c):
        x, y = flat[a], flat[b]
        z = flat[c]
        return (y[0] - x[0]) * (z[1] - x[1]) - (y[1] - x[1]) * (z[0] - x[0])

    signed = sum(
        flat[i][0] * flat[(i + 1) % len(flat)][1]
        - flat[(i + 1) % len(flat)][0] * flat[i][1]
        for i in range(len(flat))
    )
    if abs(signed) < 1e-12:
        raise MeshError("Boundary loop has negligible projected area")
    sign = 1 if signed > 0 else -1
    remaining = list(range(len(loop)))
    triangles = []
    while len(remaining) > 3:
        ear_found = False
        for p in range(len(remaining)):
            a, b, c = (
                remaining[(p - 1) % len(remaining)],
                remaining[p],
                remaining[(p + 1) % len(remaining)],
            )
            if sign * cross(a, b, c) <= 1e-12:
                continue
            if any(
                sign * cross(a, b, v) >= -1e-12
                and sign * cross(b, c, v) >= -1e-12
                and sign * cross(c, a, v) >= -1e-12
                for v in remaining
                if v not in (a, b, c)
            ):
                continue
            triangles.append((loop[a], loop[b], loop[c]))
            remaining.pop(p)
            ear_found = True
            break
        if not ear_found:
            # Tiny twisted seams may self-overlap in every 2D projection.
            # Preserve the 3D ring with a centroid fan; flag all caps for review.
            center = tuple(
                sum(points[index][axis] for index in loop) / len(loop)
                for axis in range(3)
            )
            if any(
                sum((points[loop[k]][axis] - center[axis]) ** 2 for axis in range(3))
                < 1e-16
                for k in range(len(loop))
            ):
                raise MeshError("Boundary fan would contain degenerate faces")
            center_id = len(points)
            points.append(center)
            return [
                (loop[k], loop[(k + 1) % len(loop)], center_id)
                for k in range(len(loop))
            ]
    triangles.append(tuple(loop[i] for i in remaining))
    return triangles


def closed_mesh(points: list, faces: list) -> tuple[list, int]:
    stats, border = topology(faces)
    if stats["nonmanifold_edges"] or stats["winding_conflicts"]:
        raise MeshError(f"Invalid source topology: {stats}")
    loops = boundary_loops(border)
    repaired = faces[:]
    for loop in loops:
        repaired.extend(cap_loop(points, loop))
    after, _ = topology(repaired)
    if any(after.values()):
        raise MeshError(f"Mesh remains open: {after}")
    return repaired, len(loops)


def components(faces: list) -> int:
    parents: dict[int, int] = {}

    def root(i):
        parents.setdefault(i, i)
        if parents[i] != i:
            parents[i] = root(parents[i])
        return parents[i]

    for a, b, c in faces:
        parents[root(b)] = root(a)
        parents[root(c)] = root(a)
    return len({root(i) for i in parents})


def physical_mesh(
    points: list, longest_mm: float, orientation: str = "weapon"
) -> tuple[list[tuple[float, float, float]], list[float]]:
    if orientation == "weapon":
        # Z is long, Y is thin: sword lies horizontally.
        oriented = [(p[2], p[0], p[1]) for p in points]
    elif orientation == "head":
        # Y is up: head stands upright, preserving handedness.
        oriented = [(p[0], -p[2], p[1]) for p in points]
    else:
        raise MeshError("Unsupported orientation")
    minimum = [min(p[i] for p in oriented) for i in range(3)]
    maximum = [max(p[i] for p in oriented) for i in range(3)]
    extent = [maximum[i] - minimum[i] for i in range(3)]
    if min(extent) <= 0 or longest_mm <= 0:
        raise MeshError("Invalid geometry scale")
    factor = longest_mm / max(extent)
    scaled = [tuple((p[i] - minimum[i]) * factor for i in range(3)) for p in oriented]
    return scaled, [e * factor for e in extent]


def export_ascii_stl(path: Path, points: list, faces: list):
    def normal(a, b, c):
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        n = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        length = math.sqrt(sum(x * x for x in n))
        if length < 1e-9:
            raise MeshError("Degenerate physical triangle")
        return tuple(x / length for x in n)

    with path.open("w", encoding="ascii") as f:
        f.write("solid dota_ember_spirit_weapon\n")
        for a, b, c in faces:
            p, q, r = points[a], points[b], points[c]
            nx, ny, nz = normal(p, q, r)
            f.write(f"  facet normal {nx:.7g} {ny:.7g} {nz:.7g}\n")
            f.write("    outer loop\n")
            for x, y, z in (p, q, r):
                f.write(f"      vertex {x:.7g} {y:.7g} {z:.7g}\n")
            f.write("    endloop\n  endfacet\n")
        f.write("endsolid dota_ember_spirit_weapon\n")


def convert(
    glb: Path,
    name: str,
    stl: Path,
    report: Path,
    length_mm: float = 110,
    digits: int = 4,
    orientation: str = "weapon",
):
    doc, binary = read_glb(glb)
    source, tris = select_mesh(doc, binary, name)
    verts, faces = weld(source, tris, digits)
    before, _ = topology(faces)
    repaired, holes = closed_mesh(verts, faces)
    count = components(repaired)
    if count != 1:
        raise MeshError(f"Source contains {count} disconnected shells")
    physical, size = physical_mesh(verts, length_mm, orientation)
    if any(d > 180 for d in size):
        raise MeshError("Outside A1 mini build volume")
    stl.parent.mkdir(parents=True, exist_ok=True)
    export_ascii_stl(stl, physical, repaired)
    result = {
        "source_file_sha256": hashlib.sha256(glb.read_bytes()).hexdigest(),
        "mesh_name": name,
        "original_faces": len(faces),
        "vertices_after_weld": len(verts),
        "source_open_edges": before["open_edges"],
        "capped_boundary_loops": holes,
        "final_faces": len(repaired),
        "final_topology": topology(repaired)[0],
        "connected_components": count,
        "dimensions_mm": [round(d, 2) for d in size],
        "stl_sha256": hashlib.sha256(stl.read_bytes()).hexdigest(),
        "print_ready": False,
        "review_blockers": [
            "self-intersections not yet checked",
            "minimum wall thickness not yet checked",
            "overhangs and support placement not yet sliced",
            "caps are algorithmic, not verified against reference artwork",
            "manual orientation and appearance review required",
        ],
    }
    report.write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--glb", type=Path, required=True)
    parser.add_argument("--mesh", default="ember_spirit_weapon")
    parser.add_argument("--stl", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--length-mm", type=float, default=110)
    parser.add_argument("--orientation", choices=("head", "weapon"), default="weapon")
    args = parser.parse_args()
    data = convert(
        args.glb,
        args.mesh,
        args.stl,
        args.report,
        args.length_mm,
        orientation=args.orientation,
    )
    print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()