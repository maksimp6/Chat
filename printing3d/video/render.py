"""Render deterministic turntable frames from a trusted STL file.

Requires trimesh, matplotlib (Agg), numpy. Frames are then encoded with ffmpeg.
"""
from __future__ import annotations

from pathlib import Path


def render_turntable(stl_path: Path, frames_dir: Path, *, frames: int = 48, dpi: int = 90) -> list[Path]:
    if not 1 <= frames <= 900:
        raise ValueError("frames must be between 1 and 900")
    if not 50 <= dpi <= 160:
        raise ValueError("dpi out of range")
    if stl_path.suffix.lower() != ".stl" or not stl_path.is_file():
        raise ValueError("Expected existing STL file")
    if stl_path.stat().st_size > 20_000_000:
        raise ValueError("STL exceeds 20 MB")
    import numpy as np
    import trimesh
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection

    mesh = trimesh.load_mesh(str(stl_path), process=False)
    if not isinstance(mesh, trimesh.Trimesh) or mesh.is_empty or not mesh.is_watertight:
        raise ValueError("STL must be a nonempty watertight mesh")
    if len(mesh.faces) > 100_000 or len(mesh.vertices) > 100_000:
        raise ValueError("STL too complex")
    vertices = np.asarray(mesh.vertices, dtype=float)
    if not np.isfinite(vertices).all():
        raise ValueError("Non-finite vertices")
    vertices = vertices - (vertices.min(axis=0) + vertices.max(axis=0)) / 2
    extent = np.ptp(vertices, axis=0)
    radius = max(float(extent.max()) * 0.65, 0.001)
    triangles = vertices[mesh.faces]
    frames_dir.mkdir(parents=True, exist_ok=True)
    output = []
    for i in range(frames):
        fig = plt.figure(figsize=(6, 6))
        ax = fig.add_subplot(111, projection="3d")
        collection = Poly3DCollection(triangles, alpha=1, facecolor="#8bb6d9",
                                      edgecolor="#29465d", linewidths=0.08)
        ax.add_collection3d(collection)
        ax.set(xlim=(-radius, radius), ylim=(-radius, radius), zlim=(-radius, radius))
        ax.view_init(elev=25, azim=35 + 360 * i / frames)
        ax.set_axis_off()
        path = frames_dir / f"frame_{i:04d}.png"
        fig.savefig(path, dpi=dpi, bbox_inches="tight", pad_inches=0.1)
        plt.close(fig)
        output.append(path)
    return output
