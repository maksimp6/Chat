"""Deterministic, offline tests for Dota Workshop mesh conversion."""

import json
import struct
import tempfile
import unittest
from pathlib import Path

from scripts.dota_workshop_mesh import (
    MeshError,
    boundary_loops,
    closed_mesh,
    components,
    convert,
    physical_mesh,
    read_glb,
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


def fixture_glb(mesh_name="test_mesh"):
    verts, faces = cube_with_open_top()
    position = b"".join(struct.pack("<3f", *v) for v in verts)
    index = b"".join(struct.pack("<H", vertex) for face in faces for vertex in face)
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
                "type": "VEC3",
            },
            {
                "bufferView": 1,
                "componentType": 5123,
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


class MeshPipelineTests(unittest.TestCase):
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
        points, size = physical_mesh([(0, 0, 0), (1, 0, 0), (0, 1, 1)], 30, "head")
        self.assertAlmostEqual(max(size), 30)
        self.assertEqual(min(p[2] for p in points), 0)
        self.assertEqual(min(p[0] for p in points), 0)

    def test_glb_to_stl_report_is_review_gated(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            glb = folder / "tiny.glb"
            stl = folder / "tiny.stl"
            report = folder / "tiny.json"
            glb.write_bytes(fixture_glb())
            data = convert(
                glb, "test_mesh", stl, report, length_mm=15, orientation="head"
            )
            self.assertTrue(stl.read_text().startswith("solid "))
            self.assertTrue(
                stl.read_text().endswith("endsolid dota_ember_spirit_weapon\n")
            )
            self.assertFalse(data["print_ready"])
            self.assertEqual(data["capped_boundary_loops"], 1)
            self.assertEqual(data["final_faces"], 12)
            self.assertEqual(data["final_topology"]["open_edges"], 0)
            self.assertEqual(data["connected_components"], 1)
            self.assertEqual(
                json.loads(report.read_text())["stl_sha256"], data["stl_sha256"]
            )

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
            from scripts.dota_workshop_mesh import select_mesh

            with self.assertRaises(MeshError):
                select_mesh(doc, binary, "not-here")


if __name__ == "__main__":
    unittest.main()