"""Offline FDM shape benchmark unit tests; never contacts a printer."""

import hashlib
import io
import json
import math
import runpy
import struct
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from pathlib import Path

from printing3d.shape_benchmarks import (
    FACET_LIMIT,
    TRIANGLE_STRUCT,
    _ring,
    _triangles_for_prism,
    _verify_shell,
    assess_dimension,
    encode_binary_stl,
    build_coupons,
    inspect_binary_stl,
)


class TestShapeBenchmarks(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = build_coupons(self.folder)
        self.samples = {m["name"]: m for m in self.manifest["models"]}

    def _data(self, name="square"):
        return (self.folder / f"{name}.stl").read_bytes()

    def test_six_generated_shapes_are_closed_single_shells(self):
        self.assertEqual(len(self.samples), 6)
        for entry in self.samples.values():
            with self.subTest(name=entry["name"]):
                report = inspect_binary_stl(
                    self._data(entry["name"]), tuple(entry["nominal_extents_mm"])
                )
                self.assertTrue(report["watertight"])
                self.assertTrue(report["single_shell"])
                self.assertGreater(report["volume_mm3"], 0)

    def test_exact_reproducibility_and_sha256_manifest(self):
        with tempfile.TemporaryDirectory() as other:
            second = build_coupons(Path(other))
            self.assertEqual(self.manifest, second)
            self.assertEqual(
                (self.folder / "manifest.json").read_bytes(),
                (Path(other) / "manifest.json").read_bytes(),
            )
        for entry in self.manifest["models"]:
            self.assertEqual(hashlib.sha256(self._data(entry["name"])).hexdigest(), entry["sha256"])
            self.assertEqual(entry["state"], "SOURCE_ONLY_NOT_SLICED")
        self.assertFalse(self.manifest["physical_print_approved"])
        self.assertEqual(self.manifest["material"], "UNKNOWN")

    def test_original_expected_mm_is_not_derived_from_mesh(self):
        with self.assertRaisesRegex(ValueError, "dimensions differ"):
            inspect_binary_stl(self._data("square"), (21, 20, 10), 0.03)

    def test_hole_has_open_center_not_solid_cylinder(self):
        data = self._data("hole_ring")
        vertices = []
        for offset in range(84, len(data), TRIANGLE_STRUCT.size):
            row = TRIANGLE_STRUCT.unpack_from(data, offset)
            vertices.extend(tuple(row[k : k + 3]) for k in (3, 6, 9))
        # A ring's inner radius is 6 mm in the XY plane (no vertices at its center).
        radii = [math.hypot(x - 16, y - 16) for x, y, z in vertices]
        self.assertAlmostEqual(min(radii), 6.0, delta=0.0001)
        self.assertAlmostEqual(max(radii), 16.0, delta=0.0001)

    def test_reject_truncated_or_trailing_stl(self):
        for data in [b"", self._data()[:83], self._data()[:-1], self._data() + b"extra"]:
            with self.subTest(length=len(data)), self.assertRaises(ValueError):
                inspect_binary_stl(data)

    def test_reject_false_triangle_count(self):
        damaged = bytearray(self._data())
        struct.pack_into("<I", damaged, 80, 5000)
        with self.assertRaisesRegex(ValueError, "count/length"):
            inspect_binary_stl(bytes(damaged))

    def test_reject_nonfinite_vertex(self):
        damaged = bytearray(self._data())
        struct.pack_into("<f", damaged, 84 + 12, float("nan"))
        with self.assertRaisesRegex(ValueError, "non-finite"):
            inspect_binary_stl(bytes(damaged))

    def test_reject_corrupted_normals(self):
        damaged = bytearray(self._data())
        struct.pack_into("<fff", damaged, 84, 0.0, 0.0, 0.0)
        with self.assertRaisesRegex(ValueError, "normal"):
            inspect_binary_stl(bytes(damaged))

    def test_reject_open_mesh(self):
        data = bytearray(self._data())
        count = struct.unpack_from("<I", data, 80)[0]
        del data[-TRIANGLE_STRUCT.size :]
        struct.pack_into("<I", data, 80, count - 1)
        with self.assertRaisesRegex(ValueError, "open/non-manifold"):
            inspect_binary_stl(bytes(data))

    def test_reject_inverted_triangle_winding(self):
        damaged = bytearray(self._data())
        offset = 84
        first = bytes(damaged[offset + 12 : offset + 24])
        second = bytes(damaged[offset + 24 : offset + 36])
        damaged[offset + 12 : offset + 24] = second
        damaged[offset + 24 : offset + 36] = first
        with self.assertRaisesRegex(ValueError, "normal"):
            inspect_binary_stl(bytes(damaged))

    def test_reject_duplicate_triangle(self):
        data = bytearray(self._data())
        first = data[84 : 84 + TRIANGLE_STRUCT.size]
        data += first
        struct.pack_into("<I", data, 80, struct.unpack_from("<I", data, 80)[0] + 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            inspect_binary_stl(bytes(data))

    def test_reject_dimension_exceeding_coupon_limit(self):
        square = self._data()
        damaged = bytearray(square)
        for offset in range(84, len(damaged), TRIANGLE_STRUCT.size):
            normal_and_tri = list(TRIANGLE_STRUCT.unpack_from(damaged, offset))
            for idx in (3, 6, 9):
                normal_and_tri[idx] *= 10  # x values move from 0..20 to 0..200
            TRIANGLE_STRUCT.pack_into(damaged, offset, *normal_and_tri)
        with self.assertRaisesRegex(ValueError, "outside permitted range"):
            inspect_binary_stl(bytes(damaged))

    def test_reject_invalid_expected_dimensions_and_tolerance(self):
        for expected, tol in [
            ((20, 20, 10), -1),
            ((20, float("nan"), 10), 0.01),
            ((0, 20, 10), 0.01),
        ]:
            with self.assertRaises(ValueError):
                inspect_binary_stl(self._data(), expected, tol)

    def test_measurement_guard_bands(self):
        self.assertEqual(assess_dimension(20, 20.1, 0.2, 0.05)["verdict"], "PASS")
        self.assertEqual(assess_dimension(20, 20.18, 0.2, 0.05)["verdict"], "FAIL")
        self.assertEqual(assess_dimension(20, 20.1, None, 0.05)["verdict"], "MEASURED_UNRATED")
        self.assertEqual(assess_dimension(20, None, 0.2, 0.05)["verdict"], "UNVERIFIED")
        self.assertEqual(assess_dimension(20, 20.1, 0.2, None)["verdict"], "MEASURED_UNRATED")

    def test_reject_nonfinite_guard_band_values(self):
        for args in [(20, float("nan"), 0.2, 0), (20, 20, -0.2, 0), (0, 20, 0.2, 0)]:
            with self.assertRaises(ValueError):
                assess_dimension(*args)

    def test_reject_nonzero_stl_attribute(self):
        damaged = bytearray(self._data())
        struct.pack_into("<H", damaged, 84 + 48, 1)
        with self.assertRaisesRegex(ValueError, "attribute"):
            inspect_binary_stl(bytes(damaged))

    def test_manifest_has_explicit_unknown_material_and_source_only(self):
        disk = json.loads((self.folder / "manifest.json").read_text())
        self.assertEqual(disk["units_declared"], "mm")
        self.assertEqual(disk["build_volume_A1mini_mm"], [180, 180, 180])
        self.assertFalse(disk["physical_print_approved"])
        self.assertFalse(disk["standalone_calibration_print_allowed"])
        self.assertEqual(disk["physical_characteristics_status"], "UNVERIFIED")
        self.assertTrue(all(e["digital_fixture_only"] for e in disk["models"]))
        self.assertTrue(all(e["physical_state"] == "UNVERIFIED" for e in disk["models"]))
        self.assertTrue(all("measure_after_print" not in e for e in disk["models"]))
        self.assertTrue(all(e["state"] == "SOURCE_ONLY_NOT_SLICED" for e in disk["models"]))

    def test_reject_invalid_source_shape_parameters(self):
        with self.assertRaisesRegex(ValueError, "invalid prism"):
            _triangles_for_prism([(0, 0), (1, 0)], 1)
        with self.assertRaisesRegex(ValueError, "CCW"):
            _triangles_for_prism([(0, 0), (0, 1), (1, 0)], 1)
        with self.assertRaisesRegex(ValueError, "invalid ring"):
            _ring(5, 6, 8, 96)

    def test_encoder_fail_closed_limits(self):
        triangle = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 1.0, 0.0))
        with self.assertRaisesRegex(ValueError, "too many facets"):
            encode_binary_stl([triangle] * (FACET_LIMIT + 1))
        with self.assertRaisesRegex(ValueError, "degenerate triangle"):
            encode_binary_stl([((0, 0, 0), (0, 0, 0), (1, 1, 1))])

    def test_reject_disconnected_and_misoriented_valid_facets(self):
        profile = [(0, 0), (10, 0), (10, 10), (0, 10)]
        shell = _triangles_for_prism(profile, 10)
        second = [tuple((x + 30, y, z) for x, y, z in facet) for facet in shell]
        with self.assertRaisesRegex(ValueError, "multiple disconnected shells"):
            _verify_shell(shell + second)
        misoriented = list(shell)
        a, b, c = misoriented[0]
        misoriented[0] = (a, c, b)
        with self.assertRaisesRegex(ValueError, "inconsistent triangle winding"):
            _verify_shell(misoriented)
        inverted = [(a, c, b) for a, b, c in shell]
        with self.assertRaisesRegex(ValueError, "inverted or nonpositive volume"):
            _verify_shell(inverted)

    def test_offline_cli_only_generates_source_fixtures(self):
        output = io.StringIO()
        with patch.object(sys, "argv", ["shape_benchmarks.py", "--out", str(self.folder)]):
            with redirect_stdout(output):
                runpy.run_path(
                    str(Path(__file__).resolve().parents[1] / "printing3d" / "shape_benchmarks.py"),
                    run_name="__main__",
                )
        self.assertIn("Generated 6 source-only STL fixtures", output.getvalue())
        self.assertFalse(
            json.loads((self.folder / "manifest.json").read_text())["physical_print_approved"]
        )


if __name__ == "__main__":
    unittest.main()
