"""Real disk/replay/failure and CI admission contracts without app dependencies.

Load the actual FileMemoryDB source directly so that unrelated agent_memory package
imports (which currently load Alice's SQL runtime) do not pollute this fast lane.
Full application tests still cover package integration and real service backends.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from scripts import ci_platform_changes as routing

ROOT = Path(__file__).resolve().parents[1]
MEMORY_SOURCE = ROOT / "agent_memory" / "file_memory_db.py"


def _load_file_memory_db():
    spec = importlib.util.spec_from_file_location("_ci_fast_file_memory_db", MEMORY_SOURCE)
    if spec is None or spec.loader is None:
        raise RuntimeError("FileMemoryDB source is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


memory = _load_file_memory_db()


class FastMemoryContracts(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "alice.memory"

    def test_committed_values_survive_reopen_and_delete(self):
        db = memory.FileMemoryDB(self.path)
        self.assertEqual(db.put("theme", "gray"), 1)
        self.assertEqual(db.put("counter", 2), 2)
        self.assertEqual(db.delete("counter"), 3)

        reopened = memory.FileMemoryDB(self.path)
        self.assertEqual(reopened.sequence, 3)
        self.assertEqual(reopened.items(), {"theme": "gray"})

    def test_torn_final_journal_frame_is_recovered(self):
        memory.FileMemoryDB(self.path).put("safe", 1)
        with self.path.open("ab") as handle:
            handle.write(b'{"v":1,"seq":2')

        reopened = memory.FileMemoryDB(self.path)
        self.assertEqual(reopened.items(), {"safe": 1})
        self.assertTrue(self.path.read_bytes().endswith(b"\n"))

    def test_tampering_with_committed_value_fails_closed(self):
        memory.FileMemoryDB(self.path).put("safe", 1)
        original = self.path.read_bytes()
        self.assertIn(b'"value":1', original)
        self.path.write_bytes(original.replace(b'"value":1', b'"value":2', 1))

        with self.assertRaises(memory.FileMemoryCorruption):
            memory.FileMemoryDB(self.path)

    def test_failed_fsync_never_publishes_in_memory_state(self):
        db = memory.FileMemoryDB(self.path)
        with patch.object(memory.os, "fsync", side_effect=OSError("emulated disk fault")):
            with self.assertRaises(OSError):
                db.put("unsafe", 1)
        self.assertEqual(db.items(), {})
        self.assertEqual(db.sequence, 0)


class FastRoutingContracts(unittest.TestCase):
    def test_unknown_paths_select_every_platform(self):
        self.assertTrue(all(routing.classify(["unmapped/binary.wasm"]).values()))

    def test_web_path_includes_backend_postgres_and_mcp(self):
        plan = routing.classify(["static/app.js"])
        for platform in ("web", "backend", "android", "database", "mcp"):
            self.assertTrue(plan[platform])

    def test_selected_infrastructure_suite_cannot_be_skipped(self):
        plan = routing.classify(["cloud/cloudru/registry_client.py"])
        statuses = self._statuses()
        with self.assertRaisesRegex(ValueError, "infra"):
            routing.validate_results(plan, statuses)

    def test_fast_contracts_are_required_even_on_docs_only_pr(self):
        plan = routing.classify(["docs/README.md"])
        statuses = self._statuses()
        routing.validate_results(plan, statuses)
        statuses["fast-tests"] = "skipped"
        with self.assertRaisesRegex(ValueError, "fast-tests"):
            routing.validate_results(plan, statuses)

    @staticmethod
    def _statuses():
        statuses = dict.fromkeys(routing.JOBS, "skipped")
        statuses.update({"changes": "success", "code-rules": "success", "fast-tests": "success"})
        return statuses


class FastWorkflowContracts(unittest.TestCase):
    def test_fast_job_is_unconditional_and_has_no_dependency_install(self):
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        lane = source.split("  fast-tests:\n", 1)[1].split("\n  backend:", 1)[0]
        self.assertIn("python scripts/run_fast_contracts.py", lane)
        self.assertNotIn("pip install", lane)
        self.assertNotIn("npm install", lane)
        self.assertNotIn("\n    needs:", lane)
        self.assertNotIn("\n    if:", lane)

    def test_ci_required_needs_fast_contract_result(self):
        source = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
        required = source.split("  required:\n", 1)[1]
        self.assertIn("fast-tests", required.split("\n    runs-on:", 1)[0])


if __name__ == "__main__":
    unittest.main()
