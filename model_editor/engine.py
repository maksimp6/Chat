"""Bounded, deterministic primitive geometry for the Alice Pro local 3D editor.

No user-defined code, plugin loading, subprocess, network or printer interaction.
"""

from __future__ import annotations

import copy
import io
import math
import struct
import uuid
from collections.abc import Mapping
from typing import Any

MAX_OBJECTS = 64
MAX_SEGMENTS = 128
MAX_TRIANGLES = 40_000
MIN_DIM = 0.4
MAX_DIM = 500.0
MAX_COORD = 1_000.0

Vec3 = tuple[float, float, float]
Triangle = tuple[Vec3, Vec3, Vec3]


def number(value: object, name: str, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")  # noqa: TRY004 - input contract
    result = float(value)
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{name} must be finite, within {low:g}..{high:g}")
    return result


def vector3(value: object, name: str, low: float, high: float) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"{name} must contain three numbers")
    return [number(v, name, low, high) for v in value]


def new_scene(model_id: str, name: str = "Untitled model") -> dict[str, Any]:
    if not isinstance(name, str) or not name.strip() or len(name) > 120:
        raise ValueError("name must have 1..120 characters")
    if any(ord(c) < 32 for c in name):
        raise ValueError("name contains control characters")
    return {
        "schema_version": 1,
        "model_id": str(uuid.UUID(model_id)),
        "name": name.strip(),
        "parent_revision": None,
        "objects": [],
    }


def _check_object(obj: object) -> None:
    if not isinstance(obj, Mapping) or set(obj) != {
        "id",
        "type",
        "dimensions",
        "translation_mm",
        "rotation_deg",
        "scale",
    }:
        raise ValueError("invalid scene object")
    uuid.UUID(str(obj["id"]))
    dims = obj["dimensions"]
    if not isinstance(dims, Mapping):
        raise ValueError("invalid dimensions")  # noqa: TRY004 - corrupt scene
    if obj["type"] == "cube":
        if set(dims) != {"size_mm"}:
            raise ValueError("cube requires size_mm")
        number(dims["size_mm"], "size_mm", MIN_DIM, MAX_DIM)
    elif obj["type"] == "cylinder":
        if set(dims) != {"diameter_mm", "height_mm", "segments"}:
            raise ValueError("cylinder dimensions are invalid")
        number(dims["diameter_mm"], "diameter_mm", MIN_DIM, MAX_DIM)
        number(dims["height_mm"], "height_mm", MIN_DIM, MAX_DIM)
        segments = dims["segments"]
        if (
            isinstance(segments, bool)
            or not isinstance(segments, int)
            or not 12 <= segments <= MAX_SEGMENTS
        ):
            raise ValueError("segments must be an integer from 12 to 128")
    else:
        raise ValueError("unsupported primitive type")
    vector3(obj["translation_mm"], "translation_mm", -MAX_COORD, MAX_COORD)
    vector3(obj["rotation_deg"], "rotation_deg", -360, 360)
    vector3(obj["scale"], "scale", 0.05, 20)


def validate_scene(scene: object) -> None:
    if not isinstance(scene, Mapping) or set(scene) != {
        "schema_version",
        "model_id",
        "name",
        "parent_revision",
        "objects",
    }:
        raise ValueError("invalid scene document")
    if scene["schema_version"] != 1:
        raise ValueError("unsupported scene version")
    new_scene(str(scene["model_id"]), scene["name"])
    parent = scene["parent_revision"]
    if parent is not None and (
        not isinstance(parent, str)
        or len(parent) != 64
        or any(c not in "0123456789abcdef" for c in parent)
    ):
        raise ValueError("invalid parent revision")
    objects = scene["objects"]
    if not isinstance(objects, list) or len(objects) > MAX_OBJECTS:
        raise ValueError("scene object limit exceeded")
    ids: set[str] = set()
    facets = 0
    for obj in objects:
        _check_object(obj)
        if obj["id"] in ids:
            raise ValueError("duplicate object ID")
        ids.add(obj["id"])
        facets += 12 if obj["type"] == "cube" else 4 * obj["dimensions"]["segments"]
    if facets > MAX_TRIANGLES:
        raise ValueError("triangle count limit exceeded")


def add_primitive(
    scene: dict[str, Any],
    kind: str,
    dimensions: dict[str, Any],
    *,
    parent_revision: str,
    translation_mm: object = None,
) -> tuple[dict[str, Any], str]:
    validate_scene(scene)
    if len(scene["objects"]) >= MAX_OBJECTS:
        raise ValueError("scene object limit exceeded")
    translation = (
        [0.0, 0.0, 0.0]
        if translation_mm is None
        else vector3(translation_mm, "translation_mm", -MAX_COORD, MAX_COORD)
    )
    # A retry against the same immutable parent has the same ID and new revision.
    signature = f"{parent_revision}:{len(scene['objects'])}:{kind}:{dimensions!r}:{translation!r}"
    object_id = str(uuid.uuid5(uuid.UUID(scene["model_id"]), signature))
    item = {
        "id": object_id,
        "type": kind,
        "dimensions": dimensions,
        "translation_mm": translation,
        "rotation_deg": [0.0, 0.0, 0.0],
        "scale": [1.0, 1.0, 1.0],
    }
    _check_object(item)
    updated = copy.deepcopy(scene)
    updated["parent_revision"] = parent_revision
    updated["objects"].append(item)
    validate_scene(updated)
    return updated, object_id


def edit_primitive(
    scene: dict[str, Any],
    *,
    parent_revision: str,
    object_id: str,
    operation: str,
    values: object = None,
) -> dict[str, Any]:
    validate_scene(scene)
    target = str(uuid.UUID(object_id))
    updated = copy.deepcopy(scene)
    obj = next((o for o in updated["objects"] if o["id"] == target), None)
    if obj is None:
        raise ValueError("object not found in revision")
    if operation == "delete":
        if values is not None:
            raise ValueError("delete does not accept values")
        updated["objects"].remove(obj)
    elif operation == "move":
        delta = vector3(values, "values", -MAX_COORD, MAX_COORD)
        obj["translation_mm"] = vector3(
            [a + b for a, b in zip(obj["translation_mm"], delta)],
            "translation_mm",
            -MAX_COORD,
            MAX_COORD,
        )
    elif operation == "rotate":
        delta = vector3(values, "values", -360, 360)
        obj["rotation_deg"] = [
            ((a + b + 180) % 360) - 180 for a, b in zip(obj["rotation_deg"], delta)
        ]
    elif operation == "scale":
        factors = vector3(values, "values", 0.05, 20)
        obj["scale"] = vector3([a * b for a, b in zip(obj["scale"], factors)], "scale", 0.05, 20)
    else:
        raise ValueError("unsupported edit operation")
    updated["parent_revision"] = parent_revision
    validate_scene(updated)
    return updated


def _local_triangles(obj: Mapping[str, Any]) -> list[Triangle]:
    d = obj["dimensions"]
    if obj["type"] == "cube":
        h = d["size_mm"] / 2
        s = d["size_mm"]
        vertices: list[Vec3] = [
            (-h, -h, 0),
            (h, -h, 0),
            (h, h, 0),
            (-h, h, 0),
            (-h, -h, s),
            (h, -h, s),
            (h, h, s),
            (-h, h, s),
        ]
        faces = [
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
    else:
        n = d["segments"]
        r = d["diameter_mm"] / 2
        vertices = [
            (r * math.cos(2 * math.pi * i / n), r * math.sin(2 * math.pi * i / n), z)
            for z in (0.0, d["height_mm"])
            for i in range(n)
        ]
        vertices += [(0.0, 0.0, d["height_mm"]), (0.0, 0.0, 0.0)]
        faces = []
        for i in range(n):
            nxt = (i + 1) % n
            faces.extend(
                [
                    (i, nxt, n + nxt),
                    (i, n + nxt, n + i),
                    (n + i, n + nxt, 2 * n),
                    (2 * n + 1, nxt, i),
                ]
            )
    return [(vertices[i], vertices[j], vertices[k]) for i, j, k in faces]


def _transform(point: Vec3, obj: Mapping[str, Any]) -> Vec3:
    x, y, z = (v * s for v, s in zip(point, obj["scale"]))
    rx, ry, rz = (math.radians(a) for a in obj["rotation_deg"])
    y, z = y * math.cos(rx) - z * math.sin(rx), y * math.sin(rx) + z * math.cos(rx)
    x, z = x * math.cos(ry) + z * math.sin(ry), -x * math.sin(ry) + z * math.cos(ry)
    x, y = x * math.cos(rz) - y * math.sin(rz), x * math.sin(rz) + y * math.cos(rz)
    t = obj["translation_mm"]
    return x + t[0], y + t[1], z + t[2]


def triangles(scene: dict[str, Any]) -> list[Triangle]:
    validate_scene(scene)
    return [
        (_transform(a, obj), _transform(b, obj), _transform(c, obj))
        for obj in scene["objects"]
        for a, b, c in _local_triangles(obj)
    ]


def inspect_scene(scene: dict[str, Any]) -> dict[str, Any]:
    validate_scene(scene)
    faces = triangles(scene)
    verts = [p for tri in faces for p in tri]
    bounds = (
        {
            "min_mm": [round(min(p[i] for p in verts), 6) for i in range(3)],
            "max_mm": [round(max(p[i] for p in verts), 6) for i in range(3)],
        }
        if verts
        else None
    )
    sizes = (
        [round(b - a, 6) for a, b in zip(bounds["min_mm"], bounds["max_mm"])]
        if bounds
        else [0.0, 0.0, 0.0]
    )
    summed = 0.0
    for obj in scene["objects"]:
        d = obj["dimensions"]
        v = (
            d["size_mm"] ** 3
            if obj["type"] == "cube"
            else math.pi * (d["diameter_mm"] / 2) ** 2 * d["height_mm"]
        )
        summed += v * math.prod(obj["scale"])
    return {
        "model_id": scene["model_id"],
        "name": scene["name"],
        "object_count": len(scene["objects"]),
        "objects": [
            {
                "id": obj["id"],
                "type": obj["type"],
                "dimensions": obj["dimensions"],
                "translation_mm": obj["translation_mm"],
                "rotation_deg": obj["rotation_deg"],
                "scale": obj["scale"],
            }
            for obj in scene["objects"]
        ],
        "triangles": len(faces),
        "bounds_mm": bounds,
        "size_mm": sizes,
        "sum_primitive_volume_mm3": round(summed, 3),
        "fits_a1_mini_by_dimensions": bool(verts) and all(x <= 180 for x in sizes),
        "geometry_note": "Primitive shells are closed but intersections are not boolean-unioned.",
    }


def export_stl(scene: dict[str, Any]) -> bytes:
    faces = triangles(scene)
    if not faces:
        raise ValueError("cannot export empty model")
    data = io.BytesIO()
    data.write(b"Alice Pro Model Editor STL v1".ljust(80, b" "))
    data.write(struct.pack("<I", len(faces)))
    for a, b, c in faces:
        u = [b[i] - a[i] for i in range(3)]
        v = [c[i] - a[i] for i in range(3)]
        normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        mag = math.sqrt(sum(q * q for q in normal))
        if mag < 1e-12:
            raise ValueError("degenerate triangle")
        normal = (normal[0] / mag, normal[1] / mag, normal[2] / mag)
        data.write(struct.pack("<12fH", *(normal + a + b + c), 0))
    return data.getvalue()


__all__ = [
    "add_primitive",
    "edit_primitive",
    "export_stl",
    "inspect_scene",
    "new_scene",
    "number",
    "validate_scene",
    "vector3",
]
