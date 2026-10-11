"""Negative tests for the immutable Memory DB gate."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from scripts.check_memory_contract_integrity import FROZEN_SHA256, verify

ROOT = Path(__file__).resolve().parents[1]
LEGACY = "agent_memory/file_memory_db.py"
FAST = "tests/test_ci_fast_contracts.py"


class IntegrityGuardContracts(unittest.TestCase):
    def test_manifest_covers_legacy_both_engines_and_runner(self):
        self.assertTrue(
            {
                LEGACY,
                FAST,
                "tests/test_memory_fast_core_contracts.py",
                "tests/test_memory_store.py",
                "scripts/run_fast_contracts.py",
            }
            <= set(FROZEN_SHA256)
        )

    def test_mutated_legacy_bytes_are_rejected(self):
        with TemporaryDirectory() as directory:
            target = Path(directory) / LEGACY
            target.parent.mkdir(parents=True)
            target.write_bytes((ROOT / LEGACY).read_bytes() + b"# changed\n")
            failures = verify(Path(directory), {LEGACY: FROZEN_SHA256[LEGACY]})
            self.assertTrue(any("SHA-256 differs" in failure for failure in failures))

    def test_fast_job_is_pinned_to_trusted_policy(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        fast_job = workflow.split("  fast-tests:\n", 1)[1].split("\n  backend:", 1)[0]
        self.assertIn("ref: 2081d17b7401958ca3972aa4bfd7c9eb022b4adf", fast_job)
        self.assertIn(
            "python .trusted-memory-policy/scripts/check_memory_contract_integrity.py", fast_job
        )
        self.assertIn("python scripts/run_fast_contracts.py", fast_job)

    def test_deleted_accepted_test_is_rejected(self):
        with TemporaryDirectory() as directory:
            failures = verify(Path(directory), {FAST: FROZEN_SHA256[FAST]})
            self.assertTrue(any("missing" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
