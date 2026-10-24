"""Fast offline contracts for A1 mini print workflow and Drive video archiving."""

from __future__ import annotations

import hashlib
import tempfile
import time
import unittest
import uuid
from pathlib import Path

from printing3d.ftps_storage import PrinterStorage, PrinterTransferError
from printing3d.print_workflow import FileJobStore, PrintGateError, PrintWorkflow
from printing3d.video_archive import ArchiveError, GoogleDriveResumableClient, VideoArchive

SHA = "a" * 64
REPORT = "b" * 64
FILENAME = "Logos_test_PETG_A1mini.gcode.3mf"


class PrintWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = FileJobStore(Path(self.tmp.name))
        self.workflow = PrintWorkflow(self.store)
        self.owner = "trusted:logos-user"
        self.job = self.store.create(self.owner, "artifact:allowed", SHA, "PETG")
        self.id = self.job["job_id"]

    def advance_to_user(self):
        self.workflow.advance(self.owner, self.id, "CAD_READY", {})
        self.workflow.advance(
            self.owner,
            self.id,
            "GEOMETRY_VERIFIED",
            {"report_sha256": REPORT, "geometry_passed": True},
        )
        self.workflow.advance(
            self.owner,
            self.id,
            "SLICED",
            {
                "filename": FILENAME,
                "artifact_sha256": SHA,
                "machine": "Bambu Lab A1 mini",
                "material": "PETG",
                "nozzle_mm": 0.4,
            },
        )
        self.workflow.advance(
            self.owner,
            self.id,
            "AUDITED",
            {
                "result": "ready_for_review",
                "single_plate": True,
                "report_sha256": REPORT,
                "artifact_sha256": SHA,
            },
        )
        self.workflow.advance(
            self.owner,
            self.id,
            "STAGED",
            {
                "filename": FILENAME,
                "remote_sha256": SHA,
                "readback_verified": True,
            },
        )
        self.workflow.advance(self.owner, self.id, "READY_FOR_USER", {"printer_idle": True})

    def test_no_skipping_slicing_or_audit(self):
        with self.assertRaises(PrintGateError):
            self.workflow.advance(self.owner, self.id, "STAGED", {"remote_sha256": SHA})
        self.assertEqual(self.store.read(self.owner, self.id)["state"], "REQUESTED")

    def test_wrong_owner_cannot_read_job(self):
        with self.assertRaises(PrintGateError):
            self.store.read("another-user", self.id)

    def test_wrong_profile_and_unsliced_artifact_rejected(self):
        self.workflow.advance(self.owner, self.id, "CAD_READY", {})
        self.workflow.advance(
            self.owner,
            self.id,
            "GEOMETRY_VERIFIED",
            {"report_sha256": REPORT, "geometry_passed": True},
        )
        for filename, material in [("unsliced.3mf", "PETG"), (FILENAME, "PLA")]:
            with self.assertRaises(PrintGateError):
                self.workflow.advance(
                    self.owner,
                    self.id,
                    "SLICED",
                    {
                        "filename": filename,
                        "artifact_sha256": SHA,
                        "machine": "Bambu Lab A1 mini",
                        "material": material,
                        "nozzle_mm": 0.4,
                    },
                )

    def test_bad_staging_readback_blocks(self):
        self.workflow.advance(self.owner, self.id, "CAD_READY", {})
        self.workflow.advance(
            self.owner,
            self.id,
            "GEOMETRY_VERIFIED",
            {"report_sha256": REPORT, "geometry_passed": True},
        )
        self.workflow.advance(
            self.owner,
            self.id,
            "SLICED",
            {
                "filename": FILENAME,
                "artifact_sha256": SHA,
                "material": "PETG",
                "machine": "Bambu Lab A1 mini",
                "nozzle_mm": 0.4,
            },
        )
        self.workflow.advance(
            self.owner,
            self.id,
            "AUDITED",
            {
                "result": "ready_for_review",
                "single_plate": True,
                "artifact_sha256": SHA,
                "report_sha256": REPORT,
            },
        )
        with self.assertRaises(PrintGateError):
            self.workflow.advance(
                self.owner,
                self.id,
                "STAGED",
                {"filename": FILENAME, "remote_sha256": REPORT, "readback_verified": True},
            )

    def test_start_requires_short_lived_signed_human_grant(self):
        self.advance_to_user()
        key = b"test-key-not-for-production!" * 2
        grant = self.workflow.sign_start_grant(
            self.store.read(self.owner, self.id),
            operator_id="authenticated-human",
            key=key,
            expires_at=int(time.time()) + 120,
            nonce=uuid.uuid4().hex,
        )
        tampered = dict(grant, artifact_sha256=REPORT)
        with self.assertRaises(PrintGateError):
            self.workflow.redeem_start(self.owner, self.id, tampered, key)
        self.assertEqual(self.store.read(self.owner, self.id)["state"], "READY_FOR_USER")
        receipt = self.workflow.redeem_start(self.owner, self.id, grant, key)
        self.assertEqual(receipt["state"], "START_PENDING")
        self.assertIsNotNone(receipt["start_grant_digest"])
        with self.assertRaises(PrintGateError):
            PrintWorkflow(FileJobStore(Path(self.tmp.name))).redeem_start(
                self.owner, self.id, grant, key
            )
        self.assertEqual(
            self.workflow.mark_start_result(self.owner, self.id, observed_printing=False)["state"],
            "START_UNKNOWN",
        )
        with self.assertRaises(PrintGateError):
            self.workflow.mark_start_result(self.owner, self.id, observed_printing=True)

    def test_finish_requires_confirmed_printing(self):
        self.advance_to_user()
        with self.assertRaises(PrintGateError):
            self.workflow.finish(self.owner, self.id, telemetry_finished=True)

    def test_reject_owner_directory_traversal(self):
        with self.assertRaises(PrintGateError):
            self.store.read(self.owner, "../invalid")


class FakeDrive:
    def __init__(self):
        self.sent = []
        self.existing = None
        self.limit, self.used = 10 * 1024**3, 0

    def quota(self):
        return self.limit, self.used

    def lookup(self, filename, sha256, parent_id):
        return self.existing

    def upload(self, source, proof, parent_id):
        self.sent.append(proof.sha256)
        self.existing = {
            "name": proof.job_id + "_" + proof.sha256[:12] + "_" + proof.filename,
            "id": "verified_drive_video_id",
            "size": str(proof.bytes),
            "md5Checksum": proof.md5,
        }
        return self.existing


class VideoArchiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.owner = "user:one"
        self.owner_hash = hashlib.sha256(self.owner.encode()).hexdigest()
        self.job = uuid.uuid4().hex
        folder = self.root / self.owner_hash / self.job
        folder.mkdir(parents=True)
        self.file = folder / "timelapse.mp4"
        self.file.write_bytes(b"fake mp4 test fixture" * 50)
        self.drive = FakeDrive()
        self.service = VideoArchive(self.root, self.drive, "verifiedfolder123")

    def test_upload_verifies_md5_size_and_is_idempotent(self):
        result = self.service.archive(self.owner, self.job, self.file)
        self.assertEqual(result["status"], "verified_upload")
        self.assertEqual(len(self.drive.sent), 1)
        self.assertEqual(self.service.archive(self.owner, self.job, self.file), result)
        self.assertEqual(len(self.drive.sent), 1)
        self.assertTrue(self.file.exists())

    def test_quota_exhaustion_preserves_video(self):
        self.drive.limit = self.file.stat().st_size
        with self.assertRaisesRegex(ArchiveError, "quota"):
            self.service.archive(self.owner, self.job, self.file)
        self.assertEqual(self.drive.sent, [])
        self.assertTrue(self.file.exists())

    def test_wrong_owner_or_external_file_rejected(self):
        with self.assertRaises(ArchiveError):
            self.service.archive("user:two", self.job, self.file)
        other = self.root / "outside.mp4"
        other.write_bytes(b"fake")
        with self.assertRaises(ArchiveError):
            self.service.archive(self.owner, self.job, other)

    def test_symlink_and_bad_extensions_rejected(self):
        link = self.file.parent / "link.mp4"
        link.symlink_to(self.file)
        with self.assertRaises(ArchiveError):
            self.service.archive(self.owner, self.job, link)
        unsafe = self.file.parent / "script.sh"
        unsafe.write_bytes(b"rm -rf /")
        with self.assertRaises(ArchiveError):
            self.service.archive(self.owner, self.job, unsafe)

    def test_remote_integrity_mismatch_blocks_success(self):
        self.drive.existing = {
            "id": "existing",
            "name": self.job + "_bad.mp4",
            "size": "999999",
            "md5Checksum": "wrong",
        }
        with self.assertRaisesRegex(ArchiveError, "metadata mismatch"):
            self.service.archive(self.owner, self.job, self.file)
        self.assertTrue(self.file.exists())

    def test_http_client_blocks_unapproved_host_without_token(self):
        called = []
        uploader = GoogleDriveResumableClient(lambda: called.append(True) or "token")
        with self.assertRaisesRegex(ArchiveError, "endpoint"):
            uploader._request("POST", "https://evil.example/exfil", b"test")
        self.assertEqual(called, [])


class FakeFTPS:
    def __init__(self, fingerprint, data=None, after_transfer_error=False):
        self.data = data if data is not None else {}
        self.after_transfer_error = after_transfer_error
        self.cwd_path = None
        self.password_set = False
        self.commands = []

    def connect(self, hostname, port):
        assert port == 990

    def login(self, username, password):
        assert username == "bblp"
        self.password_set = password == "fixture-code"

    def prot_p(self):
        pass

    def cwd(self, folder):
        self.cwd_path = folder

    def nlst(self):
        return list(self.data)

    def storbinary(self, command, stream, blocksize):
        self.commands.append(command.split()[0])
        self.data[command.split()[1]] = stream.read()
        if self.after_transfer_error:
            raise TimeoutError("simulated late STOR timeout")

    def retrbinary(self, command, callback, blocksize):
        self.commands.append(command.split()[0])
        for i in range(0, len(self.data[command.split()[1]]), blocksize):
            callback(self.data[command.split()[1]][i : i + blocksize])

    def quit(self):
        pass

    def close(self):
        pass


class FTPSTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.file = Path(self.tmp.name) / FILENAME
        self.file.write_bytes(b"sliced-test-fixture-not-real-gcode" * 80)
        self.sha = hashlib.sha256(self.file.read_bytes()).hexdigest()

    def make(self, fake):
        return PrinterStorage(
            hostname="device.example",
            fingerprint="c" * 64,
            secret_provider=lambda: "fixture-code",
            connector=lambda fp: fake,
        )

    def test_exact_hash_stage_with_readback(self):
        fake = FakeFTPS("c" * 64)
        result = self.make(fake).stage_verified(FILENAME, self.file, self.sha)
        self.assertTrue(result["readback_verified"])
        self.assertEqual(fake.commands, ["STOR", "RETR"])

    def test_ambiguous_stor_success_is_checked_without_retry(self):
        fake = FakeFTPS("c" * 64, after_transfer_error=True)
        result = self.make(fake).stage_verified(FILENAME, self.file, self.sha)
        self.assertTrue(result["readback_verified"])
        self.assertEqual(fake.commands.count("STOR"), 1)

    def test_existing_remote_mismatch_is_never_overwritten(self):
        fake = FakeFTPS("c" * 64, data={FILENAME: b"wrong"})
        with self.assertRaises(PrinterTransferError):
            self.make(fake).stage_verified(FILENAME, self.file, self.sha)
        self.assertNotIn("STOR", fake.commands)

    def test_no_unsliced_upload_or_path_injection(self):
        fake = FakeFTPS("c" * 64)
        for name in ("foo.3mf", "../malicious.gcode.3mf", "evil;rm.gcode.3mf"):
            with self.assertRaises(PrinterTransferError):
                self.make(fake).stage_verified(name, self.file, self.sha)
        self.assertEqual(fake.commands, [])

    def test_video_fetch_is_read_only(self):
        fake = FakeFTPS("c" * 64, data={"capture.mp4": b"video-fixture"})
        owner_hash = hashlib.sha256(b"user:one").hexdigest()
        job = uuid.uuid4().hex
        result = self.make(fake).fetch_video(
            owner_hash, job, "/timelapse", "capture.mp4", Path(self.tmp.name)
        )
        self.assertEqual(result.read_bytes(), b"video-fixture")
        self.assertEqual(fake.commands, ["RETR"])
        self.assertEqual(fake.cwd_path, "/timelapse")

    def test_no_deletion_or_start_protocol(self):
        fake = FakeFTPS("c" * 64)
        service = self.make(fake)
        self.assertFalse(hasattr(service, "start_print"))
        self.assertFalse(hasattr(service, "delete_video"))
