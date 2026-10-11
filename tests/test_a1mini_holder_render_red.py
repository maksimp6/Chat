"""RED render contracts for A1 mini holder CAD (Issue #1112).

A render must originate from real validated geometry, never an AI concept image.
The renderer is intentionally not implemented yet. Tests must fail closed.
"""
import hashlib
import importlib
import unittest


class HolderRenderRED(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.geometry = importlib.import_module("printing3d.holder_geometry")
        cls.renderer = importlib.import_module("printing3d.holder_render")

    def setUp(self):
        self.parts = self.geometry.build_holder_parts(ptfe_od_mm=4.0, cable_od_mm=5.0)

    def test_three_orthogonal_and_isometric_views(self):
        result = self.renderer.render_views(self.parts)
        self.assertEqual(set(result.views), {"isometric", "top", "side"})
        self.assertTrue(all(result.views[k].png_bytes.startswith(b"\\x89PNG\\r\\n\\x1a\\n")
                            for k in result.views))

    def test_images_are_not_empty_and_have_valid_dimensions(self):
        result = self.renderer.render_views(self.parts)
        for name, view in result.views.items():
            with self.subTest(view=name):
                self.assertGreaterEqual(view.width, 512)
                self.assertGreaterEqual(view.height, 512)
                self.assertGreater(len(view.png_bytes), 1024)
                self.assertGreater(view.non_background_pixel_ratio, 0.01)

    def test_render_reproducibility(self):
        first = self.renderer.render_views(self.parts)
        second = self.renderer.render_views(self.parts)
        for name in ("isometric", "top", "side"):
            self.assertEqual(
                hashlib.sha256(first.views[name].png_bytes).digest(),
                hashlib.sha256(second.views[name].png_bytes).digest(),
            )

    def test_provenance_links_to_geometry(self):
        result = self.renderer.render_views(self.parts)
        self.assertEqual(result.source_geometry_sha256,
                         self.geometry.geometry_sha256(self.parts))
        self.assertEqual(len(result.source_geometry_sha256), 64)
        self.assertTrue(result.renderer_version)

    def test_no_render_for_invalid_or_unverified_geometry(self):
        with self.assertRaises(self.renderer.GeometryNotVerifiedError):
            self.renderer.render_views(None)

    def test_ptfe_and_cable_are_visibly_separate(self):
        result = self.renderer.render_views(self.parts)
        self.assertTrue(result.visibility_report.ptfe_clip_visible)
        self.assertTrue(result.visibility_report.cable_clip_visible)
        self.assertFalse(result.visibility_report.ptfe_cable_intersection)

    def test_no_false_print_ready_claim(self):
        result = self.renderer.render_views(self.parts)
        self.assertEqual(result.artifact_kind, "cad_preview")
        self.assertFalse(result.print_ready)
        self.assertFalse(result.physical_fit_verified)


if __name__ == "__main__":
    unittest.main()
