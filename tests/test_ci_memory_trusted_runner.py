"""Adversarial regression checks for the pinned Memory DB test executor."""

from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import unittest

from scripts import run_fast_contracts_trusted as trusted

ROOT = Path(__file__).resolve().parents[1]


class TrustedImportContracts(unittest.TestCase):
    def test_inventory_has_exactly_fifteen_unique_cases(self):
        identities = [
            f"{relative}:{class_name}.{method}"
            for relative, classes in trusted.CASES.items()
            for class_name, methods in classes.items()
            for method in methods
        ]
        self.assertEqual(len(identities), 15)
        self.assertEqual(len(identities), len(set(identities)))

    def test_ci_invokes_trusted_and_compatibility_runner_with_shared_budget(self):
        workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
        lane = workflow.split("  fast-tests:\n", 1)[1].split("\n  backend:", 1)[0]
        self.assertIn(
            "python -I -S .trusted-memory-policy/scripts/run_fast_contracts_trusted.py", lane
        )
        self.assertIn("python scripts/run_fast_contracts.py", lane)
        self.assertIn("elapsed_ns > 2000000000", lane)

    def test_fabricated_success_with_skips_is_rejected(self):
        result = unittest.TestResult()
        result.testsRun = trusted.EXPECTED_COUNT
        result.skipped.append((self, "injected skip"))
        self.assertFalse(trusted.accepted(result))
        result.skipped.clear()
        result.expectedFailures.append((self, "xfail"))
        self.assertFalse(trusted.accepted(result))
        result.expectedFailures.clear()
        result.testsRun -= 1
        self.assertFalse(trusted.accepted(result))

    def test_modified_package_initializer_cannot_substitute_frozen_cases(self):
        with TemporaryDirectory() as folder:
            root = Path(folder)
            for relative in (
                "tests/test_ci_fast_contracts.py",
                "tests/test_memory_fast_core_contracts.py",
                "agent_memory/file_memory_db.py",
                "memory_engine/__init__.py",
                "memory_engine/store.py",
                "scripts/ci_platform_changes.py",
                ".github/workflows/ci.yml",
            ):
                target = root / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(ROOT / relative, target)
            (root / "tests/__init__.py").write_text(
                'raise RuntimeError("tampered package initializer executed")\n',
                encoding="utf-8",
            )
            injection = (
                "import importlib.util, pathlib, sys, types\n"
                "root = pathlib.Path(sys.argv[1])\n"
                "source = pathlib.Path(sys.argv[2])\n"
                "spec = importlib.util.spec_from_file_location('_test_trusted_runner', source)\n"
                "runner = importlib.util.module_from_spec(spec)\n"
                "spec.loader.exec_module(runner)\n"
                "fake = types.ModuleType('tests.test_ci_fast_contracts')\n"
                "sys.modules['tests.test_ci_fast_contracts'] = fake\n"
                "suite, identities = runner.load_cases(root)\n"
                "assert len(identities) == 15 and suite.countTestCases() == 15\n"
                "assert all(item.startswith('_trusted_memory_contract_') for item in identities)\n"
                "assert 'tests' not in sys.modules\n"
                "print('DIRECT_SOURCE_LOAD_VERIFIED')\n"
            )
            outcome = subprocess.run(
                [
                    sys.executable,
                    "-I",
                    "-S",
                    "-c",
                    injection,
                    str(root),
                    str(ROOT / "scripts/run_fast_contracts_trusted.py"),
                ],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            self.assertEqual(outcome.returncode, 0, outcome.stdout + outcome.stderr)
            self.assertIn("DIRECT_SOURCE_LOAD_VERIFIED", outcome.stdout)


if __name__ == "__main__":
    unittest.main()
