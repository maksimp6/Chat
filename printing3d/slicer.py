"""Dependency-free STL slicer for simple printable parts.

Scope is deliberately small and fully deterministic:

- closed, manifold triangle meshes (binary or ASCII STL);
- one outer perimeter per layer, rectilinear infill alternating between
  horizontal and vertical lines, clipped to the outline;
- no inner walls, supports, skirts or retraction.

A part that needs holes or overhang support is not handled here; it still
slices, but the result is only a rough estimate and must be checked in a real
slicer before printing.
"""

from __future__ import annotations

from core.wikifunctions import lowercase_string

import json
import math
import struct
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

Vec3 = tuple[float, float, float]
Point = tuple[float, float]
Triangle = tuple[Vec3, Vec3, Vec3]

FILAMENT_DENSITY_G_CM3 = 1.24  # PLA
_KEY_DECIMALS = 6


class SliceError(ValueError):
    """Raised when the input cannot be sliced into printable layers."""


@dataclass(frozen=True)
class SliceOptions:
    layer_height: float = 0.2
    line_width: float = 0.45
    filament_diameter: float = 1.75
    nozzle_temp: int = 215
    bed_temp: int = 60
    print_speed: float = 60.0  # mm/s
    travel_speed: float = 150.0  # mm/s
    bed_x: float = 256.0
    bed_y: float = 256.0

    def validate(self) -> None:
        for name in (
            "layer_height",
            "line_width",
            "filament_diameter",
            "print_speed",
            "travel_speed",
        ):
            if getattr(self, name) <= 0:
                raise SliceError(f"{name} must be positive")
        if self.line_width < self.layer_height:
            raise SliceError("line_width must not be smaller than layer_height")


@dataclass
class SliceResult:
    gcode: str
    stats: dict[str, float | int] = field(default_factory=dict)


def read_stl(data: bytes) -> list[Triangle]:
    """Parse binary or ASCII STL bytes into triangles."""
    if len(data) >= 84:
        (count,) = struct.unpack_from("<I", data, 80)
        if 84 + 50 * count == len(data):
            return _read_binary_stl(data, count)
    return _read_ascii_stl(data)


def _read_binary_stl(data: bytes, count: int) -> list[Triangle]:
    triangles: list[Triangle] = []
    for index in range(count):
        offset = 84 + index * 50 + 12  # skip the normal vector
        values = struct.unpack_from("<9f", data, offset)
        triangles.append(
            (
                (values[0], values[1], values[2]),
                (values[3], values[4], values[5]),
                (values[6], values[7], values[8]),
            )
        )
    return triangles


def _read_ascii_stl(data: bytes) -> list[Triangle]:
    try:
        text = data.decode("ascii")
    except UnicodeDecodeError as exc:
        raise SliceError("STL is neither valid binary nor ASCII") from exc
    vertices: list[Vec3] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 4 and lowercase_string(parts[0]) == "vertex":
            try:
                vertices.append((float(parts[1]), float(parts[2]), float(parts[3])))
            except ValueError as exc:
                raise SliceError("invalid vertex coordinate in ASCII STL") from exc
    if not vertices or len(vertices) % 3:
        raise SliceError("ASCII STL has no complete triangles")
    return [(vertices[i], vertices[i + 1], vertices[i + 2]) for i in range(0, len(vertices), 3)]


def _slice_triangle(triangle: Triangle, z: float) -> tuple[Point, Point] | None:
    """Intersect one triangle with the horizontal plane at height z."""
    crossings: list[Point] = []
    for index in range(3):
        a = triangle[index]
        b = triangle[(index + 1) % 3]
        if (a[2] - z) * (b[2] - z) < 0:
            t = (z - a[2]) / (b[2] - a[2])
            crossings.append((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))
    if len(crossings) == 2:
        return crossings[0], crossings[1]
    return None


def _key(point: Point) -> tuple[float, float]:
    return (round(point[0], _KEY_DECIMALS), round(point[1], _KEY_DECIMALS))


def _chain_loops(segments: Sequence[tuple[Point, Point]]) -> list[list[Point]]:
    """Join segments into closed loops; raise if the cut is not closed."""
    by_end: dict[tuple[float, float], list[int]] = defaultdict(list)
    for index, (p, q) in enumerate(segments):
        by_end[_key(p)].append(index)
        by_end[_key(q)].append(index)
    used = [False] * len(segments)
    loops: list[list[Point]] = []
    for start, (first, second) in enumerate(segments):
        if used[start]:
            continue
        used[start] = True
        origin = _key(first)
        current = _key(second)
        loop = [first, second]
        while current != origin:
            following = next((j for j in by_end[current] if not used[j]), None)
            if following is None:
                raise SliceError("mesh is not closed: open contour at slice height")
            used[following] = True
            a, b = segments[following]
            point = b if _key(a) == current else a
            loop.append(point)
            current = _key(point)
        loops.append(loop[:-1])
    return loops


def _slice_layer(triangles: Sequence[Triangle], z: float) -> list[list[Point]]:
    segments = [seg for tri in triangles if (seg := _slice_triangle(tri, z)) is not None]
    return _chain_loops(segments) if segments else []


def _scan_infill(
    outline: Sequence[Point], spacing: float, horizontal: bool, inset: float
) -> list[tuple[Point, Point]]:
    """Rectilinear fill lines inside a closed outline, clipped with an inset."""
    pts = list(outline) if horizontal else [(y, x) for x, y in outline]
    ys = [y for _, y in pts]
    lines: list[tuple[Point, Point]] = []
    y = min(ys) + spacing / 2
    row = 0
    while y < max(ys):
        xs: list[float] = []
        for i in range(len(pts)):
            x1, y1 = pts[i]
            x2, y2 = pts[(i + 1) % len(pts)]
            if (y1 > y) != (y2 > y):
                xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
        xs.sort()
        for left, right in zip(xs[0::2], xs[1::2]):
            if right - left > 2 * inset:
                a, b = (left + inset, y), (right - inset, y)
                if row % 2:
                    a, b = b, a
                lines.append((a, b) if horizontal else ((a[1], a[0]), (b[1], b[0])))
        y += spacing
        row += 1
    return lines


def _dist(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


class _Writer:
    def __init__(self, options: SliceOptions) -> None:
        self.options = options
        self.area = math.pi * (options.filament_diameter / 2) ** 2
        self.lines: list[str] = []
        self.position: Point | None = None
        self.z = 0.0
        self.extruded = 0.0
        self.extrude_mm = 0.0
        self.travel_mm = 0.0
        self.volume_mm3 = 0.0

    def travel(self, point: Point) -> None:
        if self.position is not None:
            self.travel_mm += _dist(self.position, point)
        self.lines.append(
            f"G0 X{point[0]:.3f} Y{point[1]:.3f} F{self.options.travel_speed * 60:.0f}"
        )
        self.position = point

    def extrude(self, point: Point) -> None:
        if self.position is None:
            raise SliceError("extrusion before first travel")
        length = _dist(self.position, point)
        section = self.options.line_width * self.options.layer_height
        self.volume_mm3 += length * section
        self.extrude_mm += length
        self.extruded += length * section / self.area
        self.lines.append(
            f"G1 X{point[0]:.3f} Y{point[1]:.3f} E{self.extruded:.5f} F{self.options.print_speed * 60:.0f}"
        )
        self.position = point

    def raise_to(self, z: float) -> None:
        self.travel_mm += abs(z - self.z)
        self.z = z
        self.lines.append(f"G0 Z{z:.3f} F{self.options.travel_speed * 60:.0f}")


def slice_stl(data: bytes, options: SliceOptions | None = None) -> SliceResult:
    """Slice STL bytes into G-code with print statistics."""
    options = options or SliceOptions()
    options.validate()
    triangles = read_stl(data)
    if not triangles:
        raise SliceError("STL contains no triangles")

    xs = [v[0] for tri in triangles for v in tri]
    ys = [v[1] for tri in triangles for v in tri]
    zs = [v[2] for tri in triangles for v in tri]
    shift_x = options.bed_x / 2 - (min(xs) + max(xs)) / 2
    shift_y = options.bed_y / 2 - (min(ys) + max(ys)) / 2
    if max(xs) - min(xs) > options.bed_x or max(ys) - min(ys) > options.bed_y:
        raise SliceError("model does not fit on the bed")
    base_z = min(zs)
    moved: list[Triangle] = [
        (
            (tri[0][0] + shift_x, tri[0][1] + shift_y, tri[0][2] - base_z),
            (tri[1][0] + shift_x, tri[1][1] + shift_y, tri[1][2] - base_z),
            (tri[2][0] + shift_x, tri[2][1] + shift_y, tri[2][2] - base_z),
        )
        for tri in triangles
    ]
    height = max(zs) - min(zs)
    layer_count = max(1, math.ceil(height / options.layer_height - 1e-9))

    writer = _Writer(options)
    writer.lines.extend(_header(options))
    for index in range(layer_count):
        z_mid = (index + 0.5) * options.layer_height
        z_print = (index + 1) * options.layer_height
        loops = _slice_layer(moved, z_mid)
        writer.raise_to(z_print)
        writer.lines.append(f"; layer {index + 1} z={z_print:.3f}")
        for loop in loops:
            writer.travel(loop[0])
            for point in loop[1:] + [loop[0]]:
                writer.extrude(point)
            horizontal = index % 2 == 0
            for start, end in _scan_infill(
                loop, options.line_width, horizontal, options.line_width / 2
            ):
                writer.travel(start)
                writer.extrude(end)

    writer.lines.extend(["M104 S0", "M140 S0", "M84"])
    stats: dict[str, float | int] = {
        "layers": layer_count,
        "height_mm": round(height, 3),
        "extrude_mm": round(writer.extrude_mm, 3),
        "travel_mm": round(writer.travel_mm, 3),
        "filament_mm": round(writer.extruded, 3),
        "filament_volume_mm3": round(writer.volume_mm3, 3),
        "filament_grams": round(writer.volume_mm3 / 1000 * FILAMENT_DENSITY_G_CM3, 3),
        "print_time_s": round(
            writer.extrude_mm / options.print_speed + writer.travel_mm / options.travel_speed, 1
        ),
    }
    return SliceResult(gcode="\n".join(writer.lines) + "\n", stats=stats)


def _header(options: SliceOptions) -> list[str]:
    return [
        "; generated by printing3d.slicer",
        "G21",
        "G90",
        "M82",
        f"M140 S{options.bed_temp}",
        f"M104 S{options.nozzle_temp}",
        "G28",
        "G92 E0",
        f"M190 S{options.bed_temp}",
        f"M109 S{options.nozzle_temp}",
    ]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args or len(args) > 2:
        print("usage: python -m printing3d.slicer MODEL.stl [OUT.gcode]", file=sys.stderr)
        return 2
    source = Path(args[0])
    result = slice_stl(source.read_bytes())
    target = Path(args[1]) if len(args) == 2 else source.with_suffix(".gcode")
    target.write_text(result.gcode, encoding="utf-8")
    print(json.dumps(result.stats, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
