"""Checks for the printable models in 3d-models/.

The static checks read the committed STL/3MF/SCAD files and need only the
standard library. Rendering checks need the OpenSCAD CLI and are skipped when it
is not installed.
"""

import re
import shutil
import struct
import subprocess
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from pathlib import Path

import pytest

MODELS_DIR = Path(__file__).resolve().parents[1] / "3d-models"
BAG_CLIP = MODELS_DIR / "bag-clip"
BAG_CLIP_PARTS = ("jaw_a", "jaw_b", "lever")
A1_MINI_BED_MM = 180.0
OPENSCAD = shutil.which("openscad")

requires_openscad = pytest.mark.skipif(OPENSCAD is None, reason="OpenSCAD CLI is not installed")


def _model_dirs():
    return sorted(p for p in MODELS_DIR.iterdir() if p.is_dir())


def _read_binary_stl(path: Path):
    data = path.read_bytes()
    assert len(data) >= 84, f"{path.name}: too short for a binary STL"
    (count,) = struct.unpack_from("<I", data, 80)
    assert count > 0, f"{path.name}: no triangles"
    assert len(data) == 84 + 50 * count, f"{path.name}: size does not match triangle count"
    triangles = []
    for i in range(count):
        values = struct.unpack_from("<12f", data, 84 + 50 * i)
        triangles.append((values[3:6], values[6:9], values[9:12]))
    return triangles


def _bounds(triangles):
    points = [p for tri in triangles for p in tri]
    lo = tuple(min(p[i] for p in points) for i in range(3))
    hi = tuple(max(p[i] for p in points) for i in range(3))
    return lo, hi


def _is_closed(triangles):
    def key(p):
        return tuple(round(c, 4) for c in p)

    edges = Counter()
    for tri in triangles:
        a, b, c = (key(p) for p in tri)
        for u, v in ((a, b), (b, c), (c, a)):
            edges[(u, v) if u < v else (v, u)] += 1
    return all(n == 2 for n in edges.values())


def _scad_params(path: Path):
    params = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([a-z_][a-z0-9_]*)\s*=\s*(-?[0-9.]+)\s*;", line)
        if match:
            params[match.group(1)] = float(match.group(2))
        match = re.match(r"^axle_pos\s*=\s*\[\s*(-?[0-9.]+)\s*,\s*(-?[0-9.]+)\s*\]", line)
        if match:
            params["axle_x"], params["axle_y"] = map(float, match.groups())
    return params


@pytest.mark.parametrize("model_dir", _model_dirs(), ids=lambda p: p.name)
def test_every_model_has_source_readme_and_parts(model_dir):
    assert (model_dir / "README.md").is_file()
    assert list(model_dir.glob("*.scad")), "model must be described as OpenSCAD code"
    assert list(model_dir.glob("*.stl")), "model must ship printable STL parts"


@pytest.mark.parametrize("model_dir", _model_dirs(), ids=lambda p: p.name)
def test_model_stl_parts_are_closed_and_fit_the_bed(model_dir):
    for stl in sorted(model_dir.glob("*.stl")):
        triangles = _read_binary_stl(stl)
        assert _is_closed(triangles), f"{stl.name}: mesh has open or non-manifold edges"
        lo, hi = _bounds(triangles)
        assert lo[2] == pytest.approx(0, abs=1e-3), f"{stl.name}: part must lie on the bed"
        for axis in range(3):
            size = hi[axis] - lo[axis]
            assert 0 < size <= A1_MINI_BED_MM, f"{stl.name}: size {size:.1f} mm on axis {axis}"


def test_bag_clip_part_sizes_stay_in_expected_range():
    expected = {
        # name: ((min_x, max_x), (min_y, max_y), z)
        "jaw_a": ((20, 30), (70, 90), 10),
        "jaw_b": ((20, 30), (70, 90), 16),
        "lever": ((14, 24), (45, 65), 6),
    }
    for name, (x_range, y_range, z) in expected.items():
        lo, hi = _bounds(_read_binary_stl(BAG_CLIP / f"{name}.stl"))
        x, y, height = (hi[i] - lo[i] for i in range(3))
        assert x_range[0] <= x <= x_range[1], f"{name}: width {x:.1f}"
        assert y_range[0] <= y <= y_range[1], f"{name}: length {y:.1f}"
        assert height == pytest.approx(z, abs=0.01), f"{name}: height {height:.2f}"


def test_bag_clip_parameters_meet_strength_limits():
    p = _scad_params(BAG_CLIP / "bag_clip.scad")
    # Same formulas as the asserts in bag_clip.scad.
    cam_contact = p["upper_gap"] / 2 - p["axle_x"]
    cam_r_eff = cam_contact + p["cam_squeeze"] - p["cam_ecc"]
    lever_ring_wall = cam_r_eff - p["cam_ecc"] - p["hole_d"] / 2

    assert p["ring_wall_min"] >= 3
    assert lever_ring_wall >= 3, f"lever ring wall {lever_ring_wall:.2f} mm"
    assert p["pivot_r"] - p["hole_d"] / 2 >= 3, "pivot wall"
    assert p["body_t"] / 2 - p["nut_h"] >= 2, "floor under the hinge nut"
    assert p["body_t"] / 2 - p["head_h"] >= 1.5, "floor under the hinge screw head"
    assert 0.3 <= p["pivot_clear"] <= 0.5, "hinge clearance"
    # Jaw tips stay at least 10 mm wide each.
    assert (2 * p["tip_r"] + p["tip_spread"]) / 2 - p["wave_amp"] >= 10 - 1e-9


def test_bag_clip_3mf_has_three_parts_on_the_bed():
    with zipfile.ZipFile(BAG_CLIP / "bag_clip_plate.3mf") as archive:
        assert "3D/3dmodel.model" in archive.namelist()
        assert not [n for n in archive.namelist() if n.endswith(".gcode")]
        root = ET.fromstring(archive.read("3D/3dmodel.model"))

    ns = {"m": "http://schemas.microsoft.com/3dmanufacturing/core/2015/02"}
    objects = {o.get("id"): o for o in root.findall("m:resources/m:object", ns)}
    assert sorted(o.get("name") for o in objects.values()) == sorted(BAG_CLIP_PARTS)

    for item in root.findall("m:build/m:item", ns):
        dx, dy = (float(v) for v in item.get("transform").split()[9:11])
        vertices = objects[item.get("objectid")].findall("m:mesh/m:vertices/m:vertex", ns)
        xs = [float(v.get("x")) + dx for v in vertices]
        ys = [float(v.get("y")) + dy for v in vertices]
        assert 0 <= min(xs) and max(xs) <= A1_MINI_BED_MM
        assert 0 <= min(ys) and max(ys) <= A1_MINI_BED_MM


def _render(tmp_path, part, *defines):
    out = tmp_path / f"{part}.stl"
    args = [
        OPENSCAD,
        "-D",
        f'part="{part}"',
        *defines,
        "-o",
        str(out),
        str(BAG_CLIP / "bag_clip.scad"),
    ]
    result = subprocess.run(args, capture_output=True, text=True, timeout=600, check=False)
    return result, out


@requires_openscad
@pytest.mark.parametrize("part", BAG_CLIP_PARTS)
def test_bag_clip_renders_each_part_like_the_committed_stl(tmp_path, part):
    result, out = _render(tmp_path, part)
    assert result.returncode == 0, result.stderr
    assert "WARNING" not in result.stderr and "ERROR" not in result.stderr, result.stderr

    rendered = _bounds(_read_ascii_or_binary_stl(out))
    committed = _bounds(_read_binary_stl(BAG_CLIP / f"{part}.stl"))
    for got, want in zip(rendered, committed):
        assert got == pytest.approx(want, abs=0.05)


@requires_openscad
def test_bag_clip_rejects_too_thin_lever_ring(tmp_path):
    result, _ = _render(tmp_path, "lever", "-D", "ring_wall_min=10")
    assert result.returncode != 0, result.stderr
    assert "Стенка вокруг отверстия рычага" in result.stderr


def _read_ascii_or_binary_stl(path: Path):
    data = path.read_bytes()
    if not data.lstrip().startswith(b"solid") or b"facet" not in data[:1000]:
        return _read_binary_stl(path)
    triangles, current = [], []
    for line in data.decode("ascii").splitlines():
        parts = line.split()
        if parts and parts[0] == "vertex":
            current.append(tuple(float(v) for v in parts[1:4]))
            if len(current) == 3:
                triangles.append(tuple(current))
                current = []
    return triangles
