"""Deterministic, offline tests for Dota Workshop mesh conversion."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.dota_workshop_mesh import (
    MeshError,
    boundary_loops,
    cap_loop,
    closed_mesh,
    components,
    convert,
    physical_mesh,
    read_glb,
    select_mesh,
    topology,
    weld,
)


def cube_with_open_top():
    verts = [
        (0, 0, 0),
        (1, 0, 0),
        (1, 1, 0),
        (0, 1, 0),
        (0, 0, 1),
        (1, 0, 1),
        (1, 1, 1),
        (0, 1, 1),
    ]
    tris = [
        (0, 2, 1),
        (0, 3, 2),
        (0, 1, 5),
        (0, 5, 4),
        (1, 2, 6),
        (1, 6, 5),
        (2, 3, 7),
        (2, 7, 6),
        (3, 0, 4),
        (3, 4, 7),
    ]
    return verts, tris


def closed_cube(offset=(0.0, 0.0, 0.0)):
    verts, faces = cube_with_open_top()
    faces = faces + [(4, 5, 6), (4, 6, 7)]
    shifted = [tuple(v + o for v, o in zip(point, offset)) for point in verts]
    return shifted, faces


_INDEX_FORMATS = {5123: "H", 5125: "I", 5126: "f"}


def build_document(
    verts, faces, *, mesh_name="test_mesh", position_type="VEC3", index_component=5123
):
    width = {"VEC3": 3, "VEC4": 4}[position_type]
    position = b"".join(
        struct.pack("<" + "f" * width, *(tuple(v) + (1.0,) * (width - 3))) for v in verts
    )
    index = b"".join(
        struct.pack("<" + _INDEX_FORMATS[index_component], vertex)
        for face in faces
        for vertex in face
    )
    binary = position + index
    doc = {
        "asset": {"version": "2.0"},
        "buffers": [{"byteLength": len(binary)}],
        "bufferViews": [
            {"buffer": 0, "byteOffset": 0, "byteLength": len(position)},
            {"buffer": 0, "byteOffset": len(position), "byteLength": len(index)},
        ],
        "accessors": [
            {
                "bufferView": 0,
                "componentType": 5126,
                "count": len(verts),
                "type": position_type,
            },
            {
                "bufferView": 1,
                "componentType": index_component,
                "count": len(faces) * 3,
                "type": "SCALAR",
            },
        ],
        "meshes": [
            {
                "name": mesh_name,
                "primitives": [{"attributes": {"POSITION": 0}, "indices": 1}],
            }
        ],
    }
    return doc, binary


def encode_glb(doc, binary):
    metadata = json.dumps(doc, separators=(",", ":")).encode("utf-8")
    metadata += b" " * ((-len(metadata)) % 4)
    binary += b"\0" * ((-len(binary)) % 4)
    chunks = (
        struct.pack("<I4s", len(metadata), b"JSON")
        + metadata
        + struct.pack("<I4s", len(binary), b"BIN\0")
        + binary
    )
    return struct.pack("<4sII", b"glTF", 2, 12 + len(chunks)) + chunks


def fixture_glb(mesh_name="test_mesh", invert=False):
    verts, faces = cube_with_open_top()
    if invert:
        faces = [(a, c, b) for a, b, c in faces]
    return encode_glb(*build_document(verts, faces, mesh_name=mesh_name))


def convert_case(doc, binary, *, length_mm=15, orientation="head", mesh="test_mesh"):
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        glb = folder / "case.glb"
        glb.write_bytes(encode_glb(doc, binary))
        return convert(
            glb,
            mesh,
            folder / "case.stl",
            folder / "case.json",
            length_mm=length_mm,
            orientation=orientation,
        )


def stl_points(path):
    rows = [
        line.split()[1:4]
        for line in path.read_text().splitlines()
        if line.strip().startswith("vertex ")
    ]
    return [tuple(float(value) for value in row) for row in rows]


def stl_signed_volume(path):
    # Independent check on the written file: outward normals give positive volume.
    points = stl_points(path)
    total = 0.0
    for index in range(0, len(points), 3):
        (ax, ay, az), (bx, by, bz), (cx, cy, cz) = points[index : index + 3]
        total += ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    return total / 6.0


def stl_extent(path):
    points = stl_points(path)
    return [max(p[i] for p in points) - min(p[i] for p in points) for i in range(3)]


class MeshPipelineTests(unittest.TestCase):
    def assert_point(self, actual, expected):
        for got, want in zip(actual, expected):
            self.assertAlmostEqual(got, want)

    def test_cap_single_closed_loop(self):
        vertices, faces = cube_with_open_top()
        report, boundary = topology(faces)
        self.assertEqual(report["open_edges"], 4)
        self.assertEqual(len(boundary_loops(boundary)), 1)
        repaired, count = closed_mesh(vertices, faces)
        self.assertEqual(count, 1)
        self.assertEqual(len(repaired), 12)
        self.assertEqual(components(repaired), 1)
        self.assertEqual(topology(repaired)[0]["open_edges"], 0)
        self.assertEqual(topology(repaired)[0]["winding_conflicts"], 0)

    def test_reject_nonmanifold(self):
        vertices, faces = cube_with_open_top()
        with self.assertRaises(MeshError):
            closed_mesh(vertices, faces + [faces[0]])

    def test_reject_degenerate_weld(self):
        with self.assertRaises(MeshError):
            weld([(0, 0, 0), (0, 0, 0), (1, 0, 0)], [(0, 1, 2)], 4)

    def test_physical_head_orientation_and_size(self):
        # Source +Y is up and must become output +Z; source +Z must become -Y.
        verts = [(0, 0, 0), (2, 0, 0), (0, 1, 0), (0, 0, 4)]
        faces = [(0, 1, 2), (0, 2, 3), (1, 3, 2)]
        points, size = physical_mesh(verts, faces, 40, "head")
        self.assert_point(size, (20, 40, 10))
        self.assert_point(points[1], (20, 40, 0))  # source +X stays +X
        self.assert_point(points[2], (0, 40, 10))  # source +Y becomes top +Z
        self.assert_point(points[3], (0, 0, 0))  # source +Z becomes low -Y

    def test_physical_weapon_orientation_puts_long_axis_on_x(self):
        verts = [(0, 0, 0), (0, 0, 4), (1, 0, 0), (0, 1, 0)]
        faces = [(0, 1, 2), (0, 2, 3), (1, 3, 2)]
        points, size = physical_mesh(verts, faces, 110, "weapon")
        self.assert_point(size, (110, 27.5, 27.5))
        self.assert_point(points[1], (110, 0, 0))  # source +Z becomes long +X
        self.assert_point(points[2], (0, 27.5, 0))  # source +X becomes +Y

    def test_physical_scale_ignores_unreferenced_vertices(self):
        verts, faces = closed_cube()
        verts = verts + [(10.0, 10.0, 10.0)]
        _, size = physical_mesh(verts, faces, 15, "head")
        self.assert_point(size, (15, 15, 15))

    def test_glb_to_stl_report_is_review_gated(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            glb = folder / "tiny.glb"
            stl = folder / "tiny.stl"
            report = folder / "tiny.json"
            glb.write_bytes(fixture_glb())
            data = convert(glb, "test_mesh", stl, report, length_mm=15, orientation="head")
            self.assertTrue(stl.read_text().startswith("solid "))
            self.assertTrue(stl.read_text().endswith("endsolid dota_ember_spirit_weapon\n"))
            self.assertFalse(data["print_ready"])
            self.assertEqual(data["capped_boundary_loops"], 1)
            self.assertEqual(data["final_faces"], 12)
            self.assertEqual(data["final_topology"]["open_edges"], 0)
            self.assertEqual(data["connected_components"], 1)
            self.assertFalse(data["orientation_corrected"])
            self.assertAlmostEqual(data["enclosed_volume_mm3"], 15**3, places=2)
            self.assertAlmostEqual(stl_signed_volume(stl), 15**3, places=3)
            self.assertEqual(json.loads(report.read_text())["stl_sha256"], data["stl_sha256"])

    def test_unreferenced_vertex_does_not_scale_the_mesh(self):
        verts, faces = cube_with_open_top()
        verts.append((10, 10, 10))  # POSITION entry that no triangle uses
        doc, binary = build_document(verts, faces)
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            glb = folder / "stray.glb"
            glb.write_bytes(encode_glb(doc, binary))
            data = convert(
                glb,
                "test_mesh",
                folder / "stray.stl",
                folder / "stray.json",
                length_mm=15,
                orientation="head",
            )
            self.assertEqual(data["dimensions_mm"], [15.0, 15.0, 15.0])
            self.assert_point(stl_extent(folder / "stray.stl"), (15, 15, 15))
            self.assertAlmostEqual(stl_signed_volume(folder / "stray.stl"), 15**3, places=3)

    def test_inside_out_source_is_flipped_outward(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            glb = folder / "inverted.glb"
            glb.write_bytes(fixture_glb(invert=True))
            data = convert(
                glb,
                "test_mesh",
                folder / "inverted.stl",
                folder / "inverted.json",
                length_mm=15,
                orientation="head",
            )
            self.assertTrue(data["orientation_corrected"])
            self.assertAlmostEqual(data["enclosed_volume_mm3"], 15**3, places=2)
            self.assertEqual(data["final_topology"]["winding_conflicts"], 0)
            self.assertAlmostEqual(stl_signed_volume(folder / "inverted.stl"), 15**3, places=3)

    def test_disconnected_shells_are_rejected(self):
        first_verts, first_faces = closed_cube()
        second_verts, second_faces = closed_cube((3.0, 0.0, 0.0))
        verts = first_verts + second_verts
        faces = first_faces + [(a + 8, b + 8, c + 8) for a, b, c in second_faces]
        doc, binary = build_document(verts, faces)
        with self.assertRaisesRegex(MeshError, "disconnected shells"):
            convert_case(doc, binary)

    def test_a1_build_volume_is_enforced(self):
        doc, binary = build_document(*cube_with_open_top())
        with self.assertRaisesRegex(MeshError, "build volume"):
            convert_case(doc, binary, length_mm=200)
        data = convert_case(doc, binary, length_mm=180)
        self.assertEqual(data["dimensions_mm"], [180.0, 180.0, 180.0])

    def test_sparse_position_accessor_is_rejected(self):
        doc, binary = build_document(*cube_with_open_top())
        doc["accessors"][0]["sparse"] = {"count": 1}
        with self.assertRaisesRegex(MeshError, "Sparse"):
            convert_case(doc, binary)

    def test_non_triangle_mode_is_rejected_by_convert(self):
        doc, binary = build_document(*cube_with_open_top())
        doc["meshes"][0]["primitives"][0]["mode"] = 5  # TRIANGLE_STRIP
        with self.assertRaisesRegex(MeshError, "Non-triangle"):
            convert_case(doc, binary)

    def test_non_indexed_primitive_is_rejected(self):
        doc, binary = build_document(*cube_with_open_top())
        del doc["meshes"][0]["primitives"][0]["indices"]
        with self.assertRaisesRegex(MeshError, "Non-indexed"):
            convert_case(doc, binary)

    def test_missing_position_attribute_is_rejected(self):
        doc, binary = build_document(*cube_with_open_top())
        del doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"]
        with self.assertRaisesRegex(MeshError, "POSITION"):
            convert_case(doc, binary)

    def test_position_accessor_must_be_vec3(self):
        verts, faces = cube_with_open_top()
        doc, binary = build_document(verts, faces, position_type="VEC4")
        with self.assertRaisesRegex(MeshError, "Expected VEC3"):
            convert_case(doc, binary)

    def test_index_accessor_must_use_unsigned_integers(self):
        doc, binary = build_document(*cube_with_open_top(), index_component=5126)
        with self.assertRaisesRegex(MeshError, "unsigned integer"):
            convert_case(doc, binary)

    def test_out_of_range_accessor_index_is_rejected(self):
        doc, binary = build_document(*cube_with_open_top())
        doc["meshes"][0]["primitives"][0]["attributes"]["POSITION"] = 99
        with self.assertRaisesRegex(MeshError, "Unknown accessor index"):
            convert_case(doc, binary)

    def test_centroid_fan_fallback_for_self_overlapping_seam(self):
        # Ear clipping finds no valid ear for this twisted four-vertex seam,
        # so cap_loop must fall back to a centroid fan.
        points = [(1, -1, 1), (0, -1, 2), (0, -1, 1), (-3, -3, 1)]
        original = len(points)
        triangles = cap_loop(points, [0, 1, 2, 3])
        self.assertEqual(len(points), original + 1)
        center = original
        for axis in range(3):
            self.assertAlmostEqual(
                points[center][axis], sum(p[axis] for p in points[:original]) / original
            )
        self.assertEqual(len(triangles), 4)
        self.assertTrue(all(center in triangle for triangle in triangles))
        loop_edges = {frozenset((triangle[0], triangle[1])) for triangle in triangles}
        self.assertEqual(loop_edges, {frozenset(edge) for edge in ((3, 2), (2, 1), (1, 0), (0, 3))})

    def test_invalid_glb_header(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "bad.glb"
            path.write_bytes(b"not a glb")
            with self.assertRaises(MeshError):
                read_glb(path)

    def test_unknown_mesh_is_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "valid.glb"
            path.write_bytes(fixture_glb())
            doc, binary = read_glb(path)
            with self.assertRaises(MeshError):
                select_mesh(doc, binary, "not-here")


if __name__ == "__main__":
    unittest.main()
