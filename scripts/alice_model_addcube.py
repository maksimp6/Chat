"""Deterministic, side-effect-limited 3D cube tool for Alice Pro.

A geometry engine MVP: no printer calls, network, or arbitrary code execution.
"""

from __future__ import annotations

import hashlib
import math
import struct
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CubeResult:
    size_mm: float
    triangles: int
    sha256: str
    output_path: str


def model_addcube(size_mm: float = 20.0, *, output_dir: str | Path) -> CubeResult:
    """Write a centered cube as a binary STL and return verified metadata.

    The caller owns the output directory. No filename or code is accepted from
    an untrusted agent. This is *not* a printer upload or print-start action.
    """
    if isinstance(size_mm, bool) or not isinstance(size_mm, (int, float)):
        raise TypeError("size_mm must be a number")
    size_mm = float(size_mm)
    if not math.isfinite(size_mm) or not 0.4 <= size_mm <= 180:
        raise ValueError("size_mm must be finite and within 0.4..180")
    half = size_mm / 2
    vertices = [
        (-half, -half, -half),
        (half, -half, -half),
        (half, half, -half),
        (-half, half, -half),
        (-half, -half, half),
        (half, -half, half),
        (half, half, half),
        (-half, half, half),
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
    body = bytearray(b"Alice Pro model.addcube".ljust(80, b" "))
    body.extend(struct.pack("<I", len(faces)))
    for face in faces:
        a, b, c = (vertices[i] for i in face)
        u = tuple(b[i] - a[i] for i in range(3))
        v = tuple(c[i] - a[i] for i in range(3))
        n = (
            u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0],
        )
        length = math.sqrt(sum(x * x for x in n))
        normal = tuple(x / length for x in n)
        body.extend(struct.pack("<12fH", *(normal + a + b + c), 0))
    digest = hashlib.sha256(body).hexdigest()
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / ("alice_cube_" + str(size_mm).replace(".", "_") + "mm.stl")
    path.write_bytes(body)
    if hashlib.sha256(path.read_bytes()).hexdigest() != digest:
        raise OSError("STL verification failed")
    return CubeResult(size_mm, len(faces), digest, str(path))