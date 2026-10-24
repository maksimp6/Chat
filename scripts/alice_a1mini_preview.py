"""Deterministic STL-rendered PNG previews for single-plate Bambu .gcode.3mf.

Preview is a visualization of the verified input mesh, not proof of physical fit
or a toolpath simulation. No printer commands, networking or extra packages.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
import tempfile
import xml.etree.ElementTree as ET
import zipfile
import zlib
from pathlib import Path

_MAIN = "Metadata/plate_1.png"
_SMALL = "Metadata/plate_1_small.png"
_TOP = "Metadata/top_1.png"
_PREVIEWS = {_MAIN: 512, _SMALL: 128, _TOP: 320}
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_STL_BYTES = 20 * 1024 * 1024
_PNG_MAGIC = bytes.fromhex("89504e470d0a1a0a")


def _stl_triangles(path: Path) -> list[tuple[tuple[float, float, float], ...]]:
    if path.is_symlink() or not path.is_file():
        raise RuntimeError("preview STL missing or symlink")
    data = path.read_bytes()
    if not 84 <= len(data) <= _MAX_STL_BYTES:
        raise RuntimeError("preview STL size is invalid")
    n = struct.unpack_from("<I", data, 80)[0]
    if not n or len(data) != 84 + n * 50:
        raise RuntimeError("preview must be a complete binary STL")
    faces = []
    for i in range(n):
        values = struct.unpack_from("<12fH", data, 84 + i * 50)
        vertices = (
            tuple(values[3:6]),
            tuple(values[6:9]),
            tuple(values[9:12]),
        )
        if any(not math.isfinite(c) or abs(c) > 10000 for v in vertices for c in v):
            raise RuntimeError("non-finite or out-of-range STL coordinate")
        a, b, c = vertices
        ab = tuple(b[j] - a[j] for j in range(3))
        ac = tuple(c[j] - a[j] for j in range(3))
        cross = (
            ab[1] * ac[2] - ab[2] * ac[1],
            ab[2] * ac[0] - ab[0] * ac[2],
            ab[0] * ac[1] - ab[1] * ac[0],
        )
        if sum(x * x for x in cross) < 1e-14:
            raise RuntimeError("degenerate STL triangle")
        faces.append(vertices)
    return faces


def _base_canvas(width: int) -> bytearray:
    pixels = bytearray(width * width * 3)
    for y in range(width):
        shade = int(21 + 16 * y / width)
        for x in range(width):
            off = (y * width + x) * 3
            pixels[off : off + 3] = bytes((shade, shade + 8, shade + 16))
    for i in range(1, 10):
        at = int(i * width / 10)
        for t in range(width):
            _set_pixel(pixels, width, at, t, (43, 57, 70))
            _set_pixel(pixels, width, t, at, (43, 57, 70))
    return pixels


def _set_pixel(pixels: bytearray, width: int, x: int, y: int, color: tuple[int, int, int]) -> None:
    if 0 <= x < width and 0 <= y < width:
        offset = (y * width + x) * 3
        pixels[offset : offset + 3] = bytes(color)


def _project(point: tuple[float, float, float], *, top: bool) -> tuple[float, float, float]:
    x, y, z = point
    if top:
        return x, -y, z
    return (
        (x - y) * 0.7071067811865476,
        z * 0.816496580927726 - (x + y) * 0.408248290463863,
        (x + y + z) * 0.5773502691896258,
    )


def _paint_triangle(
    pixels: bytearray,
    width: int,
    pts: list[tuple[float, float]],
    triangle: tuple,
) -> None:
    (ax, ay), (bx, by), (cx, cy) = pts
    area = (bx - ax) * (cy - ay) - (by - ay) * (cx - ax)
    if abs(area) < 0.04:
        return
    a, b, c = triangle
    u = tuple(b[j] - a[j] for j in range(3))
    v = tuple(c[j] - a[j] for j in range(3))
    n = (
        u[1] * v[2] - u[2] * v[1],
        u[2] * v[0] - u[0] * v[2],
        u[0] * v[1] - u[1] * v[0],
    )
    norm = math.sqrt(sum(k * k for k in n))
    light = abs((n[0] * -0.34 + n[1] * -0.42 + n[2] * 0.84) / norm)
    color = tuple(int(value * (0.4 + 0.6 * light)) for value in (137, 192, 225))
    for yy in range(max(0, int(min(ay, by, cy))), min(width, int(max(ay, by, cy)) + 2)):
        for xx in range(max(0, int(min(ax, bx, cx))), min(width, int(max(ax, bx, cx)) + 2)):
            px, py = xx + 0.5, yy + 0.5
            w0 = ((bx - px) * (cy - py) - (by - py) * (cx - px)) / area
            w1 = ((cx - px) * (ay - py) - (cy - py) * (ax - px)) / area
            if w0 >= -1e-7 and w1 >= -1e-7 and w0 + w1 <= 1 + 1e-7:
                _set_pixel(pixels, width, xx, yy, color)


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
    )


def _encode_png(pixels: bytearray, width: int) -> bytes:
    scan = b"".join(
        b"\x00" + bytes(pixels[y * width * 3 : (y + 1) * width * 3]) for y in range(width)
    )
    return (
        _PNG_MAGIC
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, width, 8, 2, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(scan, level=6))
        + _png_chunk(b"IEND", b"")
    )


def _render(faces: list, width: int, *, top: bool = False) -> bytes:
    """Flat-shaded orthographic mesh render, deterministic and Pillow-free."""
    # The grid is decorative; placement on the printer is not claimed.
    pixels = _base_canvas(width)
    projected = [(tuple(_project(p, top=top) for p in tri), tri) for tri in faces]
    all_points = [p for vertices, _ in projected for p in vertices]
    min_x = min(p[0] for p in all_points)
    max_x = max(p[0] for p in all_points)
    min_y = min(p[1] for p in all_points)
    max_y = max(p[1] for p in all_points)
    extent = max(max_x - min_x, max_y - min_y)
    if extent < 1e-5:
        raise RuntimeError("STL preview projection has zero area")
    scale = (width * 0.79) / extent
    center_x, center_y = (min_x + max_x) / 2, (min_y + max_y) / 2
    projected.sort(key=lambda item: sum(p[2] for p in item[0]) / 3)
    for vertices, tri in projected:
        pts = [
            (
                width * 0.5 + (p[0] - center_x) * scale,
                width * 0.51 - (p[1] - center_y) * scale,
            )
            for p in vertices
        ]
        _paint_triangle(pixels, width, pts, tri)
    return _encode_png(pixels, width)


def _read_archive(path: Path) -> tuple[list[zipfile.ZipInfo], dict[str, bytes]]:
    if path.is_symlink() or not path.is_file() or path.suffix != ".3mf":
        raise RuntimeError("sliced 3MF missing, symlink or wrong format")
    with zipfile.ZipFile(path) as z:
        infos = z.infolist()
        names = [info.filename for info in infos]
        if len(infos) > 128 or len(names) != len(set(names)):
            raise RuntimeError("duplicate/oversized 3MF members")
        if any(
            not name
            or name.startswith("/")
            or "\\" in name
            or ".." in name.split("/")
            or name.endswith("/")
            for name in names
        ):
            raise RuntimeError("unsafe 3MF member path")
        if sum(info.file_size for info in infos) > _MAX_ARCHIVE_BYTES:
            raise RuntimeError("oversized 3MF contents")
        if z.testzip() is not None:
            raise RuntimeError("3MF ZIP CRC validation failed")
        content = {name: z.read(name) for name in names}
    required = (
        "Metadata/plate_1.gcode",
        "Metadata/model_settings.config",
        "_rels/.rels",
        "[Content_Types].xml",
    )
    if not all(name in content for name in required):
        raise RuntimeError("3MF missing G-code/metadata/relationships")
    if not content["Metadata/plate_1.gcode"]:
        raise RuntimeError("sliced G-code is empty")
    rels = ET.fromstring(content["_rels/.rels"])
    targets = {tag.get("Target", "").lstrip("/") for tag in rels}
    if not {_MAIN, _SMALL}.issubset(targets):
        raise RuntimeError("slicer thumbnail relationships are missing")
    types = ET.fromstring(content["[Content_Types].xml"])
    if not any(tag.get("Extension") == "png" for tag in types):
        raise RuntimeError("3MF lacks a PNG content type")
    return infos, content


def _metadata_with_previews(raw: bytes) -> bytes:
    root = ET.fromstring(raw)
    plates = root.findall("plate")
    if len(plates) != 1:
        raise RuntimeError("only single-plate previews are supported")
    plate = plates[0]
    entries = {item.get("key"): item for item in plate.findall("metadata")}
    if (
        entries.get("gcode_file") is None
        or entries["gcode_file"].get("value") != "Metadata/plate_1.gcode"
    ):
        raise RuntimeError("unexpected plate G-code reference")
    for key, filename in (("thumbnail_file", _MAIN), ("top_file", _TOP)):
        if key in entries:
            entries[key].set("value", filename)
        else:
            ET.SubElement(plate, "metadata", key=key, value=filename)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def _png_chunks(data: bytes) -> list[tuple[bytes, bytes]]:
    if not data.startswith(_PNG_MAGIC):
        raise RuntimeError("invalid PNG signature")
    chunks = []
    pos = 8
    while pos + 12 <= len(data):
        count = struct.unpack_from(">I", data, pos)[0]
        if count > 8 * 1024 * 1024 or pos + 12 + count > len(data):
            raise RuntimeError("invalid PNG chunk size")
        kind = data[pos + 4 : pos + 8]
        payload = data[pos + 8 : pos + 8 + count]
        expected = struct.unpack_from(">I", data, pos + 8 + count)[0]
        if zlib.crc32(kind + payload) & 0xFFFFFFFF != expected:
            raise RuntimeError("invalid PNG chunk CRC")
        chunks.append((kind, payload))
        pos += 12 + count
        if kind == b"IEND":
            break
    if pos != len(data) or not chunks or chunks[0][0] != b"IHDR" or chunks[-1][0] != b"IEND":
        raise RuntimeError("incomplete PNG")
    return chunks


def _verify_png(data: bytes, width: int) -> None:
    chunks = _png_chunks(data)
    header = chunks[0][1]
    if len(header) != 13 or struct.unpack(">II", header[:8]) != (width, width):
        raise RuntimeError("unexpected preview dimensions")
    if header[8:] != bytes((8, 2, 0, 0, 0)):
        raise RuntimeError("preview must be 8-bit RGB PNG")
    parts = b"".join(payload for kind, payload in chunks if kind == b"IDAT")
    if not parts or len(parts) > 8 * 1024 * 1024:
        raise RuntimeError("PNG pixel data missing/oversized")
    raw = zlib.decompress(parts)
    _verify_png_pixels(raw, width)


def _verify_png_pixels(raw: bytes, width: int) -> None:
    if len(raw) != width * (1 + width * 3):
        raise RuntimeError("PNG pixel size mismatch")
    if any(raw[y * (1 + 3 * width)] != 0 for y in range(width)):
        raise RuntimeError("unexpected PNG scanline filter")
    if len(set(raw[1 : 1 + min(width, 30) * 3])) <= 1 and len(set(raw)) <= 3:
        raise RuntimeError("blank thumbnail")


def audit_preview(path: Path) -> dict[str, object]:
    _, files = _read_archive(path)
    plate = ET.fromstring(files["Metadata/model_settings.config"]).find("plate")
    if plate is None:
        raise RuntimeError("3MF missing plate metadata")
    metadata = {e.get("key"): e.get("value") for e in plate.findall("metadata")}
    if metadata.get("thumbnail_file") != _MAIN or metadata.get("top_file") != _TOP:
        raise RuntimeError("3MF preview metadata does not match image files")
    images = {}
    for name, width in _PREVIEWS.items():
        payload = files.get(name)
        if payload is None:
            raise RuntimeError(f"missing referenced preview: {name}")
        _verify_png(payload, width)
        images[name] = {
            "width": width,
            "height": width,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
        }
    return {
        "type": "source_mesh_orthographic_not_toolpath",
        "images": images,
        "gcode_sha256": hashlib.sha256(files["Metadata/plate_1.gcode"]).hexdigest(),
    }


def embed_preview(path: Path, source_stl: Path) -> dict[str, object]:
    """Insert actual model renders atomically, preserving G-code bytes and MD5."""
    faces = _stl_triangles(source_stl)
    infos, existing = _read_archive(path)
    gcode_before = hashlib.sha256(existing["Metadata/plate_1.gcode"]).hexdigest()
    md5_before = existing.get("Metadata/plate_1.gcode.md5")
    renders = {name: _render(faces, width, top=(name == _TOP)) for name, width in _PREVIEWS.items()}
    updated = dict(existing)
    updated.update(renders)
    updated["Metadata/model_settings.config"] = _metadata_with_previews(
        existing["Metadata/model_settings.config"]
    )
    with tempfile.NamedTemporaryFile(
        prefix=".alice-preview-", suffix=".3mf", dir=path.parent, delete=False
    ) as temp:
        replacement = Path(temp.name)
    try:
        with zipfile.ZipFile(replacement, "w", allowZip64=False) as out:
            for info in infos:
                out.writestr(info, updated.pop(info.filename))
            for name in sorted(updated):
                out.writestr(name, updated[name], compress_type=zipfile.ZIP_DEFLATED)
        report = audit_preview(replacement)
        if report["gcode_sha256"] != gcode_before:
            raise RuntimeError("render embedding modified G-code")
        with zipfile.ZipFile(replacement) as check:
            if md5_before is not None and check.read("Metadata/plate_1.gcode.md5") != md5_before:
                raise RuntimeError("render embedding modified G-code MD5")
        os.replace(replacement, path)
        return report
    finally:
        replacement.unlink(missing_ok=True)
