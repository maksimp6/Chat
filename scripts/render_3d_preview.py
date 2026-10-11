"""Deterministic, read-only STL/3MF geometry preview. NEVER infer print fitness.

Only the rendered PNG is emitted; no slicer, firmware, shell or printer actions.
"""

from __future__ import annotations

import hashlib
import math
import re
import struct
from collections.abc import Iterable
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageDraw

MAX_SOURCE_BYTES = 16 * 1024 * 1024
MAX_MEMBER_BYTES = 8 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED = 40 * 1024 * 1024
MAX_MEMBERS = 100
MAX_TRIANGLES = 25_000
MAX_VERTICES = 50_000
MAX_IMAGE_BYTES = 1_200_000
CORE_NS = "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"
MODEL_TAG = f"{{{CORE_NS}}}model"
VERTEX_TAG = f"{{{CORE_NS}}}vertex"
TRIANGLE_TAG = f"{{{CORE_NS}}}triangle"
OBJECT_TAG = f"{{{CORE_NS}}}object"
COMPONENT_TAG = f"{{{CORE_NS}}}component"
ITEM_TAG = f"{{{CORE_NS}}}item"
XML_PATH = "{http://schemas.microsoft.com/3dmanufacturing/production/2015/06}path"
UNIT_SCALE_MM = {
    "millimeter": 1.0,
    "centimeter": 10.0,
    "meter": 1000.0,
    "inch": 25.4,
    "foot": 304.8,
    "micron": 0.001,
}
Vec3 = tuple[float, float, float]
Face = tuple[Vec3, Vec3, Vec3]
IDENTITY = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0)


class PreviewError(ValueError):
    """Unable to render a trusted representation from untrusted mesh bytes."""


@dataclass(frozen=True)
class Mesh:
    triangles: tuple[Face, ...]
    units: str
    source_sha256: str
    source_name: str
    minimum: Vec3
    maximum: Vec3


def _point(values: Iterable[object]) -> Vec3:
    try:
        coords = tuple(float(v) for v in values)
    except (TypeError, ValueError, OverflowError) as exc:
        raise PreviewError("invalid 3D vertex coordinates") from exc
    if len(coords) != 3 or any(not math.isfinite(v) or abs(v) > 1_000_000 for v in coords):
        raise PreviewError("unsafe or nonfinite mesh vertex")
    return coords  # type: ignore[return-value]


def _mesh(triangles: list[Face], name: str, sha: str, units: str) -> Mesh:
    if not 0 < len(triangles) <= MAX_TRIANGLES:
        raise PreviewError("empty or oversized 3D mesh")
    xs = [coord[0] for face in triangles for coord in face]
    ys = [coord[1] for face in triangles for coord in face]
    zs = [coord[2] for face in triangles for coord in face]
    minimum, maximum = ((min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs)))
    if minimum == maximum:
        raise PreviewError("degenerate mesh bounding box")
    return Mesh(tuple(triangles), units, sha, name, minimum, maximum)


def _binary_stl(data: bytes) -> list[Face] | None:
    if len(data) < 84:
        return None
    count = struct.unpack_from("<I", data, 80)[0]
    if not count or len(data) != 84 + 50 * count:
        return None
    if count > MAX_TRIANGLES:
        raise PreviewError("STL exceeds triangle limit")
    triangles: list[Face] = []
    for index in range(count):
        xyz = struct.unpack_from("<9f", data, 84 + 50 * index + 12)
        triangles.append((_point(xyz[:3]), _point(xyz[3:6]), _point(xyz[6:])))
    return triangles


def _ascii_stl(data: bytes) -> list[Face]:
    if not data.lstrip().startswith(b"solid"):
        raise PreviewError("unsupported or truncated STL")
    try:
        decoded = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise PreviewError("invalid ASCII STL encoding") from exc
    lines = [line.strip() for line in decoded.splitlines() if line.strip()]
    if not lines or not lines[0].startswith("solid") or not lines[-1].startswith("endsolid"):
        raise PreviewError("truncated ASCII STL solid")
    triangles: list[Face] = []
    body = lines[1:-1]
    if len(body) % 7:
        raise PreviewError("invalid ASCII STL facet structure")
    for offset in range(0, len(body), 7):
        facet = body[offset : offset + 7]
        triangles.append(_ascii_facet(facet))
        if len(triangles) > MAX_TRIANGLES:
            raise PreviewError("STL exceeds triangle limit")
    return triangles


def _ascii_facet(facet: list[str]) -> Face:
    normal = facet[0].split()
    if len(normal) != 5 or normal[:2] != ["facet", "normal"]:
        raise PreviewError("invalid ASCII STL facet normal")
    _point(normal[2:])
    if facet[1] != "outer loop" or facet[5:] != ["endloop", "endfacet"]:
        raise PreviewError("invalid ASCII STL facet structure")
    points = []
    for line in facet[2:5]:
        fields = line.split()
        if len(fields) != 4 or fields[0] != "vertex":
            raise PreviewError("invalid ASCII STL vertex")
        points.append(_point(fields[1:]))
    return tuple(points)


def _stl(data: bytes) -> list[Face]:
    parsed = _binary_stl(data)
    return parsed if parsed is not None else _ascii_stl(data)


def _transform(value: str | None) -> tuple[float, ...]:
    if not value:
        return IDENTITY
    fields = value.split()
    if len(fields) != 12:
        raise PreviewError("invalid 3MF transform")
    try:
        matrix = tuple(float(part) for part in fields)
    except (ValueError, OverflowError) as exc:
        raise PreviewError("invalid 3MF transform") from exc
    if any(not math.isfinite(item) or abs(item) > 1_000_000 for item in matrix):
        raise PreviewError("nonfinite 3MF transform")
    return matrix


def _apply(point: Vec3, m: tuple[float, ...]) -> Vec3:
    x, y, z = point
    return _point(
        (
            m[0] * x + m[3] * y + m[6] * z + m[9],
            m[1] * x + m[4] * y + m[7] * z + m[10],
            m[2] * x + m[5] * y + m[8] * z + m[11],
        )
    )


def _part_name(raw: str) -> str:
    if not raw or "\\" in raw:
        raise PreviewError("unsafe 3MF part reference")
    path = raw.lstrip("/")
    parts = PurePosixPath(path).parts
    if not parts or parts[0] != "3D" or any(p in (".", "..") for p in parts):
        raise PreviewError("3MF part outside model directory")
    return "/".join(parts)


def _part(archive: ZipFile, name: str, cache: dict[str, ET.Element]) -> ET.Element:
    normalized = _part_name(name)
    if normalized in cache:
        return cache[normalized]
    try:
        info = archive.getinfo(normalized)
    except KeyError as exc:
        raise PreviewError("missing 3MF referenced object part") from exc
    if not 0 < info.file_size <= MAX_MEMBER_BYTES:
        raise PreviewError("3MF XML part exceeds size limit")
    source = archive.read(info)
    # Restrict XML to UTF-8 before checking declarations (UTF-16 can hide entities).
    try:
        text = source.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise PreviewError("3MF XML must be UTF-8") from exc
    if "\x00" in text or re.search(
        r'encoding\s*=\s*["\'](?!utf-8["\'])', text[:200], re.IGNORECASE
    ):
        raise PreviewError("3MF XML must be UTF-8")
    if b"<!DOCTYPE" in source.upper() or b"<!ENTITY" in source.upper():
        raise PreviewError("3MF XML entities are not supported")
    try:
        root = ET.fromstring(source)
    except ET.ParseError as exc:
        raise PreviewError("invalid 3MF model XML") from exc
    if root.tag != MODEL_TAG:
        raise PreviewError("3MF model has wrong XML namespace")
    cache[normalized] = root
    return root


def _triangles_for_mesh(mesh: ET.Element) -> list[Face]:
    vertices_node = mesh.find(f"{{{CORE_NS}}}vertices")
    faces_node = mesh.find(f"{{{CORE_NS}}}triangles")
    if vertices_node is None or faces_node is None:
        raise PreviewError("3MF object lacks mesh vertices or triangles")
    if not 0 < len(vertices_node) <= MAX_VERTICES or not 0 < len(faces_node) <= MAX_TRIANGLES:
        raise PreviewError("3MF mesh exceeds vertex/triangle limits")
    if any(node.tag != VERTEX_TAG for node in vertices_node):
        raise PreviewError("unexpected 3MF vertex element")
    vertices = [_point((node.get("x"), node.get("y"), node.get("z"))) for node in vertices_node]
    triangles: list[Face] = []
    for node in faces_node:
        if node.tag != TRIANGLE_TAG:
            raise PreviewError("unexpected 3MF triangle element")
        fields = [node.get(axis, "") for axis in ("v1", "v2", "v3")]
        if any(len(field) > 10 for field in fields):
            raise PreviewError("invalid 3MF triangle index")
        try:
            i, j, k = (int(field) for field in fields)
        except ValueError as exc:
            raise PreviewError("invalid 3MF triangle index") from exc
        if min(i, j, k) < 0 or max(i, j, k) >= len(vertices):
            raise PreviewError("3MF face index outside vertex list")
        triangles.append((vertices[i], vertices[j], vertices[k]))
    return triangles


def _object_faces(
    archive: ZipFile,
    part: str,
    object_id: str,
    cache: dict[str, ET.Element],
    parents: set[tuple[str, str]],
) -> list[Face]:
    key = (_part_name(part), object_id)
    if key in parents or len(parents) > 16:
        raise PreviewError("cyclic or overly nested 3MF components")
    root = _part(archive, key[0], cache)
    objects = list(root.iter(OBJECT_TAG))
    ids = [node.get("id") for node in objects]
    if None in ids or len(set(ids)) != len(ids):
        raise PreviewError("ambiguous 3MF object ids")
    object_node = next((node for node in objects if node.get("id") == object_id), None)
    if object_node is None:
        raise PreviewError("missing 3MF object id")
    mesh = object_node.find(f"{{{CORE_NS}}}mesh")
    if mesh is not None:
        return _triangles_for_mesh(mesh)
    components = object_node.find(f"{{{CORE_NS}}}components")
    if components is None or not len(components):
        raise PreviewError("3MF object has no supported triangle geometry")
    result: list[Face] = []
    for component in components:
        if component.tag != COMPONENT_TAG:
            raise PreviewError("unsupported 3MF component element")
        path = component.get(XML_PATH) or key[0]
        child_id = component.get("objectid")
        if not child_id:
            raise PreviewError("3MF component lacks object id")
        matrix = _transform(component.get("transform"))
        child = _object_faces(archive, path, child_id, cache, parents | {key})
        if len(result) + len(child) > MAX_TRIANGLES:
            raise PreviewError("3MF component mesh exceeds triangle limit")
        result.extend(tuple(_apply(v, matrix) for v in face) for face in child)
    return result


def _validate_package(archive: ZipFile) -> None:
    infos = archive.infolist()
    names = [entry.filename for entry in infos]
    if len(infos) > MAX_MEMBERS or len(set(names)) != len(infos):
        raise PreviewError("ambiguous or oversized 3MF archive")
    if sum(info.file_size for info in infos) > MAX_TOTAL_UNCOMPRESSED:
        raise PreviewError("3MF exceeds total uncompressed limit")
    for info in infos:
        if (
            info.flag_bits & 1
            or "\\" in info.filename
            or (info.external_attr >> 16) & 0o170000 == 0o120000
            or info.compress_type not in (0, 8)
            or info.file_size > max(1, info.compress_size) * 200
            or info.file_size > MAX_MEMBER_BYTES
            or info.filename.startswith("/")
            or ".." in PurePosixPath(info.filename).parts
        ):
            raise PreviewError("unsafe 3MF package entry")


def _build_faces(archive: ZipFile, root: ET.Element, cache: dict[str, ET.Element]) -> list[Face]:
    unit = root.get("unit", "millimeter")
    if unit not in UNIT_SCALE_MM:
        raise PreviewError("unsupported 3MF units")
    build = root.find(f"{{{CORE_NS}}}build")
    if build is None or not len(build):
        raise PreviewError("3MF missing build items")
    faces: list[Face] = []
    for item in build:
        if item.tag != ITEM_TAG or item.get("printable", "1") == "0":
            continue
        obj_id = item.get("objectid")
        if not obj_id:
            raise PreviewError("3MF build item missing object id")
        part = item.get(XML_PATH) or "3D/3dmodel.model"
        matrix = _transform(item.get("transform"))
        child = _object_faces(archive, part, obj_id, cache, set())
        if len(faces) + len(child) > MAX_TRIANGLES:
            raise PreviewError("3MF build exceeds triangle limit")
        scale = UNIT_SCALE_MM[unit]
        faces.extend(
            tuple(_point(v * scale for v in _apply(vertex, matrix)) for vertex in face)
            for face in child
        )
    return faces


def _3mf(data: bytes) -> tuple[list[Face], str]:
    try:
        with ZipFile(BytesIO(data)) as archive:
            _validate_package(archive)
            cache: dict[str, ET.Element] = {}
            root = _part(archive, "3D/3dmodel.model", cache)
            return _build_faces(archive, root, cache), "mm"
    except (BadZipFile, RuntimeError, NotImplementedError, EOFError) as exc:
        raise PreviewError("invalid 3MF ZIP") from exc


def load_mesh(raw: bytes, filename: str) -> Mesh:
    """Return validated mesh geometry only; never interpret embedded G-code."""
    if not 0 < len(raw) <= MAX_SOURCE_BYTES:
        raise PreviewError("source model exceeds size budget")
    base = Path(filename).name
    if base != filename or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}", base):
        raise PreviewError("unsafe model filename")
    sha = hashlib.sha256(raw).hexdigest()
    if base.lower().endswith(".stl"):
        return _mesh(_stl(raw), base, sha, "STL units unspecified")
    if base.lower().endswith(".3mf"):
        faces, units = _3mf(raw)
        return _mesh(faces, base, sha, units)
    raise PreviewError("supported previews require STL or 3MF mesh geometry")


def _projection(p: Vec3, camera: str) -> tuple[float, float, float]:
    x, y, z = p
    if camera == "TOP":
        return x, y, z
    if camera == "SIDE":
        return x, z, -y
    # Unit isometric axes with elevation close to 30 degrees.
    return 0.70710678 * (x - y), 0.40824829 * (x + y) + 0.81649658 * z, 0.57735027 * (x + y - z)


def _card(draw: ImageDraw.ImageDraw, mesh: Mesh, view: str, left: int) -> None:
    outer = (left, 64, left + 392, 420)
    draw.rounded_rectangle(outer, radius=12, fill=(255, 255, 255), outline=(221, 229, 237), width=2)
    draw.text((left + 18, 78), view, fill=(48, 65, 84))
    geom = [(_projection(v, view) for v in face) for face in mesh.triangles]
    projected = [tuple(face) for face in geom]
    xx = [v[0] for face in projected for v in face]
    yy = [v[1] for face in projected for v in face]
    center_x, center_y = (min(xx) + max(xx)) / 2, (min(yy) + max(yy)) / 2
    span_x, span_y = max(xx) - min(xx), max(yy) - min(yy)
    scale = min(338 / max(span_x, 0.00001), 280 / max(span_y, 0.00001))
    triangles = sorted(projected, key=lambda face: sum(vertex[2] for vertex in face) / 3)
    for p, q, r in triangles:
        u = (q[0] - p[0], q[1] - p[1], q[2] - p[2])
        v = (r[0] - p[0], r[1] - p[1], r[2] - p[2])
        normal = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
        mag = math.sqrt(sum(component * component for component in normal))
        diffuse = abs(normal[2]) / mag if mag > 0 else 0.4
        strength = 0.53 + 0.47 * diffuse
        color = tuple(max(0, min(255, int(component * strength))) for component in (150, 176, 204))
        polygon = [
            (left + 196 + (vertex[0] - center_x) * scale, 264 - (vertex[1] - center_y) * scale)
            for vertex in (p, q, r)
        ]
        draw.polygon(polygon, fill=color)


def render_preview(mesh: Mesh) -> bytes:
    """Render three orthographic views; no printability inference."""
    image = Image.new("RGB", (1240, 490), (246, 248, 251))
    draw = ImageDraw.Draw(image)
    draw.text((20, 16), "ALICE PRO   /   3D GEOMETRY PREVIEW", fill=(36, 49, 64))
    for camera, x in (("ISOMETRIC", 20), ("TOP", 424), ("SIDE", 828)):
        _card(draw, mesh, camera, x)
    size = tuple(round(mesh.maximum[i] - mesh.minimum[i], 2) for i in range(3))
    draw.text(
        (24, 435),
        f"{mesh.source_name[:80]}  |  {len(mesh.triangles):,} triangles  |  {size} {mesh.units}",
        fill=(66, 82, 99),
    )
    draw.text(
        (24, 463),
        "Visual shape only — NOT a slicer, tolerance, strength or print-safety certification",
        fill=(126, 63, 54),
    )
    buf = BytesIO()
    image.save(buf, "PNG", optimize=True)
    if buf.tell() > MAX_IMAGE_BYTES:
        raise PreviewError("rendered PNG exceeds publication limit")
    return buf.getvalue()
