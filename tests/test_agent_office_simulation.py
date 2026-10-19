"""Runner-only regression checks for the fixture office rehearsal."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from agent_office import simulation

SOURCE_SHA = "c" * 40


class OfficeSimulationTests(unittest.TestCase):
    def test_rehearsal_uses_existing_stages_and_preserves_approval_boundary(self) -> None:
        report = simulation.simulate(SOURCE_SHA, "123")
        self.assertTrue(report.passed)
        self.assertEqual(len(report.plans), 6)
        self.assertTrue(all(p.backend == "github-actions" for p in report.plans))
        self.assertFalse(any(p.can_bypass_approval for p in report.plans))
        payload = report.payload()
        self.assertEqual(payload["source_sha"], SOURCE_SHA)
        self.assertEqual(payload["run_id"], "123")
        self.assertEqual(payload["mode"], "fixture-rehearsal")
        self.assertNotIn("VERIFIED", str(payload["status"]))

    def test_trace_has_real_model_states_and_no_work_from_green_workflow(self) -> None:
        report = simulation.simulate()
        states = [
            c.actual for c in report.checks if c.name.startswith("lifecycle/")
        ]
        self.assertEqual(
            states, ["queued", "acknowledged", "dispatched", "working", "verifying", "review", "done"]
        )
        checks = {c.name: c for c in report.checks}
        self.assertEqual(checks["green-workflow-without-backend"].actual, "queued")
        self.assertEqual(checks["completed-without-artifact"].actual, "failed")

    def test_negative_control_detects_a_state_function_that_always_claims_done(self) -> None:
        with patch.object(simulation.states, "derive_agent_task_state", return_value="done"):
            report = simulation.simulate()
        self.assertFalse(report.passed)
        broken = {c.name for c in report.checks if not c.passed}
        self.assertIn("green-workflow-without-backend", broken)
        self.assertIn("completed-without-artifact", broken)
        self.assertEqual(report.payload()["status"], "SIMULATION_FAIL")

    def test_negative_control_detects_disabled_solution_review_gate(self) -> None:
        with patch.object(
            simulation.dispatch, "assert_solution_review_accepted_for_head", return_value=None
        ):
            report = simulation.simulate()
        self.assertFalse(report.passed)
        broken = {c.name for c in report.checks if not c.passed}
        self.assertIn("solution-review/stale-head", broken)
        self.assertIn("solution-review/missing", broken)

    def test_unexpected_exception_is_not_converted_to_success(self) -> None:
        def crashed() -> None:
            raise RuntimeError("fixture crash")

        with self.assertRaisesRegex(RuntimeError, "fixture crash"):
            simulation.rejection_check("broken", crashed, ValueError)

    def test_report_bytes_are_reproducible_and_hashes_match(self) -> None:
        report = simulation.simulate(SOURCE_SHA, "123")
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            simulation.write_report(report, output)
            original = (output / "report.json").read_bytes()
            simulation.write_report(simulation.simulate(SOURCE_SHA, "123"), output)
            self.assertEqual((output / "report.json").read_bytes(), original)
            self.assertEqual(json.loads(original)["source_sha"], SOURCE_SHA)
            for row in (output / "SHA256SUMS").read_text().splitlines():
                digest, name = row.split("  ")
                self.assertEqual(hashlib.sha256((output / name).read_bytes()).hexdigest(), digest)
            self.assertIn("reviewer identity", (output / "report.md").read_text())

    def test_real_cli_negative_control_is_red_then_normal_cli_is_green(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = [
                sys.executable,
                "-m",
                "agent_office.simulation",
                "--source-sha",
                SOURCE_SHA,
                "--run-id",
                "123",
                "--output",
                directory,
            ]
            red = subprocess.run(base + ["--negative-control"], capture_output=True, timeout=10)
            self.assertEqual(red.returncode, 1, red.stderr.decode())
            self.assertIn(b"SIMULATION_FAIL", red.stdout)
            green = subprocess.run(base, capture_output=True, timeout=10)
            self.assertEqual(green.returncode, 0, green.stderr.decode())
            self.assertIn(b"SIMULATION_PASS", green.stdout)


if __name__ == "__main__":
    unittest.main()
