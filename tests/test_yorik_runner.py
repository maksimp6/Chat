"""Negative handoff and uncertain-upload regressions, without printer access."""

import hashlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile
from unittest.mock import Mock, patch

from scripts import yorik_runner as worker
from scripts import yorik_slice as slicer

HEAD = "a" * 40


def package():
    profile = {
        "printer_model": "Bambu Lab A1 mini",
        "printer_variant": "0.4",
        "filament_type": ["PLA"],
        "curr_bed_type": "Textured PEI Plate",
        "nozzle_temperature": ["220"],
        "textured_plate_temp": ["60"],
    }
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("Metadata/project_settings.config", json.dumps(profile))
        archive.writestr(
            "Metadata/plate_1.gcode",
            "; HEADER_BLOCK_START\nM109 S220\nM190 S60\nG1 X10 E1.5\n" + "; test\n" * 900,
        )
    blob = stream.getvalue()
    audit = {
        "filename": worker.FILENAME,
        "printer": "Bambu Lab A1 mini",
        "material": "PLA",
        "nozzle_mm": "0.4",
        "bed": "Textured PEI Plate",
        "state": "SLICED_NOT_PRINTED",
        "head_sha": HEAD,
        "sha256": hashlib.sha256(blob).hexdigest(),
        "size_bytes": len(blob),
    }
    return blob, audit


class HandoffTests(unittest.TestCase):
    def test_only_successful_exact_head_same_repo_run(self):
        run = {
            "head_sha": HEAD,
            "status": "completed",
            "conclusion": "success",
            "path": worker.WORKFLOW,
            "head_repository": {"full_name": "maksimp6/Chat"},
        }
        worker.validate_run(run, HEAD)
        for key, value in (
            ("head_sha", "b" * 40),
            ("conclusion", "failure"),
            ("path", "other.yml"),
        ):
            with self.subTest(key=key), self.assertRaises(ValueError):
                worker.validate_run(dict(run, **{key: value}), HEAD)
        with self.assertRaises(ValueError):
            worker.validate_run(dict(run, head_repository={"full_name": "attacker/Chat"}), HEAD)

    def test_hash_and_head_tampering_are_rejected(self):
        blob, audit = package()
        worker.validate_package(blob, audit, HEAD)
        for modified in (dict(audit, sha256="0" * 64), dict(audit, head_sha="b" * 40)):
            with self.assertRaises(ValueError):
                worker.validate_package(blob, modified, HEAD)

    def test_wrong_material_and_size_are_rejected(self):
        blob, audit = package()
        for modified in (dict(audit, material="PETG"), dict(audit, size_bytes=1)):
            with self.assertRaises(ValueError):
                worker.validate_package(blob, modified, HEAD)

    def test_unsliced_model_cannot_be_staged(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("3D/3dmodel.model", "<model/>")
        blob, audit = package()
        blob = stream.getvalue()
        audit.update(size_bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        with self.assertRaises(KeyError):
            worker.validate_package(blob, audit, HEAD)

    def test_artifact_path_traversal_is_rejected_without_extraction(self):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, "w") as archive:
            archive.writestr("../receipt.json", "bad")
        with self.assertRaises(ValueError):
            worker.unpack_handoff(stream.getvalue(), HEAD)

    def test_certificate_pin_mismatch_closes_connection(self):
        connection = Mock()
        connection.getpeercert.return_value = b"different certificate"
        with patch.object(worker.ssl.SSLContext, "wrap_socket", return_value=connection):
            with self.assertRaises(worker.ssl.SSLError):
                worker.PinnedContext("0" * 64).wrap_socket(Mock())
        connection.close.assert_called_once()

    def test_comment_only_forged_gcode_is_rejected(self):
        blob, audit = package()
        stream = io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(blob)) as original, zipfile.ZipFile(stream, "w") as archive:
            archive.writestr(
                "Metadata/project_settings.config",
                original.read("Metadata/project_settings.config"),
            )
            archive.writestr("Metadata/plate_1.gcode", "; HEADER_BLOCK_START\n" + "; fake\n" * 900)
        blob = stream.getvalue()
        audit.update(size_bytes=len(blob), sha256=hashlib.sha256(blob).hexdigest())
        with self.assertRaises(ValueError):
            worker.validate_package(blob, audit, HEAD)

    def test_private_config_permissions_and_lan_host(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            parent.chmod(0o700)
            path = parent / "printer.json"
            data = {"host": "192.168.1.10", "certificate_sha256": "a" * 64, "access_code": "test"}
            path.write_text(json.dumps(data))
            path.chmod(0o600)
            self.assertEqual(worker.load_config(path)["host"], data["host"])
            path.chmod(0o644)
            with self.assertRaises(ValueError):
                worker.load_config(path)
            path.chmod(0o600)
            path.write_text(json.dumps(dict(data, host="8.8.8.8")))
            with self.assertRaises(ValueError):
                worker.load_config(path)

    def test_symlink_config_is_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            parent = Path(folder)
            parent.chmod(0o700)
            target = parent / "real.json"
            target.write_text("{}")
            path = parent / "printer.json"
            os.symlink(target, path)
            with self.assertRaises(OSError):
                worker.load_config(path)


class SliceAuditTests(unittest.TestCase):
    def result(self, size, **changes):
        plate = {"objects": [{"triangle_count": 23430, "bbox": size}]}
        plate.update(changes)
        return {"return_code": 0, "sliced_plates": [plate]}

    def verify(self, data):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "result.json"
            path.write_text(json.dumps(data))
            return slicer.validate_result(path, 23430)

    def test_real_rotated_cli_bbox_is_rejected(self):
        with self.assertRaises(ValueError):
            self.verify(self.result({"width": 45.111431, "depth": 45.111431, "height": 53.693531}))

    def test_original_dimensions_pass(self):
        self.verify(self.result({"width": 34, "depth": 34, "height": 53.693531}))

    def test_warning_is_not_accepted_as_ready(self):
        with self.assertRaises(ValueError):
            self.verify(
                self.result(
                    {"width": 34, "depth": 34, "height": 53.693531}, warning_message="unsafe"
                )
            )


class UploadTests(unittest.TestCase):
    def ftp(self, before, after):
        ftp = Mock()
        outcomes = iter((before, after))

        def retrieve(command, callback):
            blob = next(outcomes)
            if blob is None:
                raise worker.ftplib.error_perm("550 missing")
            callback(blob)

        ftp.retrbinary.side_effect = retrieve
        return ftp

    def test_upload_readback_is_required(self):
        ftp = self.ftp(None, b"job")
        report = worker.stage(ftp, b"job", hashlib.sha256(b"job").hexdigest())
        self.assertEqual(report["state"], "STAGED_VERIFIED_NOT_PRINTED")
        ftp.storbinary.assert_called_once()

    def test_timeout_after_success_does_not_duplicate_upload(self):
        ftp = self.ftp(None, b"job")
        ftp.storbinary.side_effect = TimeoutError()
        worker.stage(ftp, b"job", hashlib.sha256(b"job").hexdigest())
        ftp.storbinary.assert_called_once()

    def test_timeout_without_readback_blocks(self):
        ftp = self.ftp(None, None)
        ftp.storbinary.side_effect = TimeoutError()
        with self.assertRaises(ValueError):
            worker.stage(ftp, b"job", "a" * 64)
        ftp.storbinary.assert_called_once()

    def test_different_existing_file_is_not_overwritten(self):
        ftp = self.ftp(b"other", b"job")
        with self.assertRaises(ValueError):
            worker.stage(ftp, b"job", "a" * 64)
        ftp.storbinary.assert_not_called()

    def test_idempotent_matching_file_is_read_back_without_upload(self):
        ftp = self.ftp(b"job", b"job")
        worker.stage(ftp, b"job", "a" * 64)
        ftp.storbinary.assert_not_called()


if __name__ == "__main__":
    unittest.main()
