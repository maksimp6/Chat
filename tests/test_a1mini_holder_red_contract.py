"""RED contract tests for A1 mini PTFE and cable holders.

These tests intentionally fail until the parametric holder geometry and its
validation adapter exist. No printer, slicer or network access is required.
"""

import importlib
import unittest


class HolderContractRED(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mod = importlib.import_module("printing3d.holder_geometry")

    def test_separate_ptfe_and_electrical_clips(self):
        parts = self.mod.build_holder_parts(ptfe_od_mm=4.0, cable_od_mm=5.0)
        self.assertIn("ptfe_clip", parts)
        self.assertIn("cable_clip", parts)
        self.assertNotEqual(parts["ptfe_clip"].mount_id, parts["cable_clip"].mount_id)

    def test_positive_retention_without_crushing_tube(self):
        parts = self.mod.build_holder_parts(ptfe_od_mm=4.0, cable_od_mm=5.0)
        self.assertGreater(parts["ptfe_clip"].retention_lip_mm, 0)
        self.assertGreaterEqual(parts["ptfe_clip"].minimum_free_bore_mm, 4.0)

    def test_reject_invalid_dimensions(self):
        for diameter in (0, -1, float("nan"), float("inf")):
            with self.subTest(diameter=diameter):
                with self.assertRaises(ValueError):
                    self.mod.build_holder_parts(ptfe_od_mm=diameter, cable_od_mm=5.0)

    def test_ptfe_bend_radius_gate(self):
        route = self.mod.route_ptfe(
            tube_od_mm=4.0,
            minimum_allowed_radius_mm=30.0,
            anchors_mm=[(0, 0, 0), (50, 0, 0), (100, 40, 0)],
        )
        self.assertGreaterEqual(route.minimum_radius_mm, 30.0)

    def test_full_travel_collision_gate(self):
        geometry = self.mod.build_holder_parts(ptfe_od_mm=4.0, cable_od_mm=5.0)
        report = self.mod.check_a1mini_clearance(geometry, travel_samples=101, clearance_min_mm=2.0)
        self.assertEqual(report.samples_checked, 101)
        self.assertGreaterEqual(report.minimum_clearance_mm, 2.0)
        self.assertFalse(report.collisions)

    def test_non_destructive_mount_and_vent_clearance(self):
        geometry = self.mod.build_holder_parts(ptfe_od_mm=4.0, cable_od_mm=5.0)
        self.assertFalse(geometry["ptfe_clip"].requires_drilling)
        self.assertFalse(geometry["cable_clip"].requires_drilling)
        self.assertTrue(self.mod.check_ventilation_clearance(geometry))

    def test_unmeasured_stock_spindle_fit_is_blocked(self):
        with self.assertRaises(self.mod.MissingMeasurementError):
            self.mod.verify_stock_spindle_fit(
                measured_spindle_mm=None,
                geometry=self.mod.build_holder_parts(ptfe_od_mm=4.0, cable_od_mm=5.0),
            )


if __name__ == "__main__":
    unittest.main()
