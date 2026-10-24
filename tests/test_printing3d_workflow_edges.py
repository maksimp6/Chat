"""Exhaustive failure-path coverage for the A1 mini state and storage gates.

Entirely offline: never contacts a printer or a user's Google Drive.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from printing3d.print_workflow import (
    FileJobStore,
    PrintGateError,
    PrintWorkflow,
    _basename,
    _material,
    _owner,
    _sha,
)

GOOD = "a" * 64
ALT = "b" * 64
VALID_NAME = "test_A1mini_PETG.gcode.3mf"


class WorkflowEdgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.root = Path(self.dir.name)
        self.store = FileJobStore(self.root / "jobs")
        self.flow = PrintWorkflow(self.store)
        self.owner = "trusted-owner"
        self.job = self.store.create(self.owner, "asset:known", GOOD, "PETG")
        self.job_id = self.job["job_id"]

    def step_to(self, target: str = "READY_FOR_USER") -> None:
        steps = {
            "CAD_READY": {},
            "GEOMETRY_VERIFIED": {"geometry_passed": True, "report_sha256": ALT},
            "SLICED": {
                "filename": VALID_NAME,
                "artifact_sha256": GOOD,
                "material": "PETG",
                "machine": "Bambu Lab A1 mini",
                "nozzle_mm": 0.4,
            },
            "AUDITED": {
                "result": "ready_for_review",
                "single_plate": True,
                "artifact_sha256": GOOD,
                "report_sha256": ALT,
            },
            "STAGED": {
                "readback_verified": True,
                "remote_sha256": GOOD,
                "filename": VALID_NAME,
            },
            "READY_FOR_USER": {"printer_idle": True},
        }
        for name, evidence in steps.items():
            self.flow.advance(self.owner, self.job_id, name, evidence)
            if name == target:
                return

    def test_primitive_rejections(self) -> None:
        for fn, value in [
            (_sha, "abc"),
            (_sha, None),
            (_basename, "../file.3mf"),
            (_basename, "!invalid"),
            (_material, "ABS"),
            (_owner, ""),
        ]:
            with self.subTest(value=value), self.assertRaises(PrintGateError):
                fn(value)
        with self.assertRaises(PrintGateError):
            _basename("unsliced.3mf", sliced=True)
        with self.assertRaises(PrintGateError):
            _owner("x" * 300)

    def test_create_rejects_bad_source_material_and_digest(self) -> None:
        inputs = [
            ("", GOOD, "PLA"),
            ("../secret", GOOD, "PLA"),
            ("valid", "invalid", "PLA"),
            ("valid", GOOD, "ABS"),
        ]
        for ref, sha, material in inputs:
            with self.subTest(ref=ref), self.assertRaises(PrintGateError):
                self.store.create(self.owner, ref, sha, material)

    def test_state_root_and_owner_symlink_rejected(self) -> None:
        sym = self.root / "link"
        sym.symlink_to(self.root / "jobs", target_is_directory=True)
        with self.assertRaises(PrintGateError):
            FileJobStore(sym)
        alternate = self.root / "foreign"
        alternate.mkdir()
        target = self.root / "jobs" / _owner("alien-owner")
        target.symlink_to(alternate, target_is_directory=True)
        with self.assertRaises(PrintGateError):
            self.store.create("alien-owner", "valid", GOOD, "PLA")

    def test_atomic_failure_removes_temporary_snapshot(self) -> None:
        path = self.root / "jobs" / "failure.json"
        with (
            patch("printing3d.print_workflow.os.replace", side_effect=OSError("EIO")),
            self.assertRaises(OSError),
        ):
            self.store._write(path, {"state": "fixture"})
        self.assertEqual(list(path.parent.glob(".print-*")), [])

    def test_collision_path_corruption_owner_hash_and_symlinks(self) -> None:
        with (
            patch(
                "printing3d.print_workflow.uuid.uuid4",
                return_value=SimpleNamespace(hex=self.job_id),
            ),
            self.assertRaises(PrintGateError),
        ):
            self.store.create(self.owner, "new", GOOD, "PLA")
        path = self.store._path(self.owner, self.job_id)
        path.write_text(json.dumps({**self.job, "owner_hash": ALT}))
        with self.assertRaises(PrintGateError):
            self.store.read(self.owner, self.job_id)
        path.unlink()
        foreign = self.root / "foreign-file"
        foreign.write_text("data")
        path.symlink_to(foreign)
        with self.assertRaises(PrintGateError):
            self.store.read(self.owner, self.job_id)

    def test_transition_checks_fail_closed(self) -> None:
        with self.assertRaises(PrintGateError):
            self.flow.advance(self.owner, self.job_id, "NO_SUCH_STEP", {})
        self.step_to("CAD_READY")
        for invalid in [
            {"geometry_passed": False, "report_sha256": ALT},
            {"geometry_passed": True, "report_sha256": "bad"},
        ]:
            with self.assertRaises(PrintGateError):
                self.flow.advance(self.owner, self.job_id, "GEOMETRY_VERIFIED", invalid)
        self.step_to_expected_geometry()

        wrong_slices = [
            {
                "filename": VALID_NAME,
                "artifact_sha256": GOOD,
                "material": "PETG",
                "machine": "Bambu Lab A1 mini",
                "nozzle_mm": 0.6,
            },
        ]
        for proof in wrong_slices:
            with self.assertRaises(PrintGateError):
                self.flow.advance(self.owner, self.job_id, "SLICED", proof)
        self.flow.advance(
            self.owner,
            self.job_id,
            "SLICED",
            {
                "filename": VALID_NAME,
                "artifact_sha256": GOOD,
                "material": "PETG",
                "machine": "Bambu Lab A1 mini",
                "nozzle_mm": 0.4,
            },
        )
        with self.assertRaises(PrintGateError):
            self.flow.advance(
                self.owner,
                self.job_id,
                "AUDITED",
                {
                    "result": "blocked",
                    "single_plate": True,
                    "artifact_sha256": GOOD,
                    "report_sha256": ALT,
                },
            )
        self.flow.advance(
            self.owner,
            self.job_id,
            "AUDITED",
            {
                "result": "ready_for_review",
                "single_plate": True,
                "artifact_sha256": GOOD,
                "report_sha256": ALT,
            },
        )
        self.flow.advance(
            self.owner,
            self.job_id,
            "STAGED",
            {"readback_verified": True, "remote_sha256": GOOD, "filename": VALID_NAME},
        )
        with self.assertRaises(PrintGateError):
            self.flow.advance(self.owner, self.job_id, "READY_FOR_USER", {"printer_idle": False})

    def step_to_expected_geometry(self) -> None:
        self.flow.advance(
            self.owner,
            self.job_id,
            "GEOMETRY_VERIFIED",
            {"geometry_passed": True, "report_sha256": ALT},
        )

    def test_signed_start_denials_and_finish(self) -> None:
        with self.assertRaises(PrintGateError):
            self.flow.sign_start_grant(
                self.job,
                operator_id="human",
                key=b"x" * 32,
                expires_at=int(time.time()) + 100,
                nonce="f" * 32,
            )
        self.step_to()
        ready = self.store.read(self.owner, self.job_id)
        key = b"x" * 32
        params = {
            "operator_id": "human",
            "key": key,
            "expires_at": int(time.time()) + 100,
            "nonce": "f" * 32,
        }
        for change in [
            {"operator_id": ""},
            {"key": b"weak"},
            {"nonce": "not-hex"},
            {"expires_at": int(time.time()) + 500},
        ]:
            with self.subTest(change=change), self.assertRaises(PrintGateError):
                self.flow.sign_start_grant(ready, **(params | change))
        broken = {**ready, "artifact": None}
        with self.assertRaises(PrintGateError):
            self.flow.sign_start_grant(broken, **params)
        grant = self.flow.sign_start_grant(ready, **params)
        for bad_grant, secret in [
            (grant, b"weak"),
            ({**grant, "expires_at": 0}, key),
            ({**grant, "signature": "invalid"}, key),
            ({**grant, "job_id": "e" * 32}, key),
        ]:
            with self.assertRaises(PrintGateError):
                self.flow.redeem_start(self.owner, self.job_id, bad_grant, secret)
        with (
            patch("printing3d.print_workflow.time.time", return_value=grant["expires_at"] + 1),
            self.assertRaisesRegex(PrintGateError, "expired"),
        ):
            self.flow.redeem_start(self.owner, self.job_id, grant, key)
        cross_job = {**grant, "job_id": uuid.uuid4().hex}
        payload = {
            name: cross_job[name]
            for name in ("job_id", "artifact_sha256", "operator_id", "expires_at", "nonce")
        }
        cross_job["signature"] = hmac.new(
            key,
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
            hashlib.sha256,
        ).hexdigest()
        with self.assertRaisesRegex(PrintGateError, "another print job"):
            self.flow.redeem_start(self.owner, self.job_id, cross_job, key)
        self.flow.redeem_start(self.owner, self.job_id, grant, key)
        self.flow.mark_start_result(self.owner, self.job_id, observed_printing=True)
        with self.assertRaises(PrintGateError):
            self.flow.finish(self.owner, self.job_id, telemetry_finished=False)
        self.flow.finish(self.owner, self.job_id, telemetry_finished=True)
        self.assertEqual(self.store.read(self.owner, self.job_id)["state"], "COMPLETE")

    def test_video_receipt_guard_and_duplicate(self) -> None:
        with self.assertRaises(PrintGateError):
            self.flow.add_video(self.owner, self.job_id, {"status": "verified_upload"})
        self.step_to()
        key = b"x" * 32
        grant = self.flow.sign_start_grant(
            self.store.read(self.owner, self.job_id),
            operator_id="human",
            key=key,
            expires_at=int(time.time()) + 100,
            nonce="e" * 32,
        )
        self.flow.redeem_start(self.owner, self.job_id, grant, key)
        self.flow.mark_start_result(self.owner, self.job_id, observed_printing=True)
        self.flow.finish(self.owner, self.job_id, telemetry_finished=True)
        for video in [
            {"status": "pending", "sha256": GOOD, "drive_id": "found"},
            {"status": "verified_upload", "sha256": "bad", "drive_id": "found"},
            {"status": "verified_upload", "sha256": GOOD, "drive_id": ""},
        ]:
            with self.assertRaises(PrintGateError):
                self.flow.add_video(self.owner, self.job_id, video)
        receipt = {
            "status": "verified_upload",
            "sha256": GOOD,
            "drive_id": "safe_drive_id",
            "bytes": 123,
        }
        self.flow.add_video(self.owner, self.job_id, receipt)
        with self.assertRaises(PrintGateError):
            self.flow.add_video(self.owner, self.job_id, receipt)


if __name__ == "__main__":
    unittest.main()
