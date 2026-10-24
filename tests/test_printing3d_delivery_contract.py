"""Offline tests for A1 mini delivery contract."""
import tempfile
import unittest
from pathlib import Path
from zipfile import ZipFile

from printing3d.delivery_contract import InvalidPrintJob, stage_only, validate_job


class DeliveryContractTests(unittest.TestCase):
    def test_valid_sliced_archive_and_stage_only(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sample.gcode.3mf"
            with ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("Metadata/plate_1.gcode", "; test only")
            job = validate_job(path, printer_model="A1 mini", approved_profile="PLA-0.4")
            class FakeTransport:
                def stage(self, value):
                    self.job = value
                    return "staged.gcode.3mf"
                def verify(self, name, size):
                    return name == "staged.gcode.3mf" and size == path.stat().st_size
            self.assertEqual(stage_only(job, FakeTransport()), "staged.gcode.3mf")

    def test_rejects_unsliced_and_wrong_model(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "model.3mf"
            with ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
            with self.assertRaises(InvalidPrintJob):
                validate_job(path, printer_model="A1 mini", approved_profile="PLA-0.4")
            with self.assertRaises(InvalidPrintJob):
                validate_job(path, printer_model="P1S", approved_profile="PLA-0.4")

    def test_rejects_zip_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.gcode.3mf"
            with ZipFile(path, "w") as archive:
                archive.writestr("[Content_Types].xml", "<Types/>")
                archive.writestr("../bad.gcode", "; invalid")
            with self.assertRaises(InvalidPrintJob):
                validate_job(path, printer_model="A1 mini", approved_profile="PLA-0.4")


if __name__ == "__main__":
    unittest.main()
