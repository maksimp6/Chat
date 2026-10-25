"""Regression cases for the RAM runner. Execute only in an authorized runner job."""
import contextlib
import io
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

from scripts import ram_test_runner as runner


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.root_patch = mock.patch.object(runner, "ROOT", self.root)
        self.root_patch.start()
        self.addCleanup(self.root_patch.stop)
        packages = mock.patch.object(runner.importlib.metadata, "distributions", return_value=[])
        packages.start()
        self.addCleanup(packages.stop)
        self.write("app.py", "VALUE = 1\n")
        self.write("templates/chat.html", "first")
        self.write("tests/__init__.py", "")
        self.write("tests/test_a.py", "import unittest\n")
        self.write("tests/test_b.py", "import unittest\n")

    def write(self, name, content):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def digests(self, cache_env=()):
        paths = sorted(str(path.relative_to(self.root)) for path in self.root.rglob("*") if path.is_file())
        return runner.fingerprints(paths, "test_*.py", cache_env)

    def test_independent_test_change_only_invalidates_its_module(self):
        before = self.digests()
        self.write("tests/test_a.py", "import unittest\n# changed\n")
        after = self.digests()
        self.assertNotEqual(before["tests/test_a.py"], after["tests/test_a.py"])
        self.assertEqual(before["tests/test_b.py"], after["tests/test_b.py"])

    def test_non_python_shared_input_invalidates_every_module(self):
        before = self.digests()
        self.write("templates/chat.html", "second")
        after = self.digests()
        for name in before:
            self.assertNotEqual(before[name], after[name])

    def test_production_change_invalidates_every_module(self):
        before = self.digests()
        self.write("app.py", "VALUE = 2\n")
        after = self.digests()
        for name in before:
            self.assertNotEqual(before[name], after[name])

    def test_transitive_test_import_invalidates_consumers(self):
        self.write("tests/test_b.py", "from .test_a import VALUE\n")
        self.write("tests/test_c.py", "import tests.test_b\n")
        before = self.digests()
        self.write("tests/test_a.py", "VALUE = 2\n")
        after = self.digests()
        for name in before:
            self.assertNotEqual(before[name], after[name])

    def test_opaque_reader_depends_on_every_test_source(self):
        self.write("tests/test_b.py", "def helper(path):\n    return path.read_text()\n")
        before = self.digests()
        self.write("tests/test_a.py", "VALUE = 2\n")
        after = self.digests()
        self.assertNotEqual(before["tests/test_b.py"], after["tests/test_b.py"])

    def test_dependency_cycles_terminate_and_track_each_source(self):
        self.write("tests/test_a.py", "import tests.test_b\n")
        self.write("tests/test_b.py", "import tests.test_a\n")
        before = self.digests()
        self.write("tests/test_b.py", "import tests.test_a\nVALUE = 3\n")
        after = self.digests()
        for name in before:
            self.assertNotEqual(before[name], after[name])

    def test_installed_dependency_version_changes_invalidate_cache(self):
        before = self.digests()
        distribution = types.SimpleNamespace(metadata={"Name": "example"}, version="2")
        with mock.patch.object(runner.importlib.metadata, "distributions", return_value=[distribution]):
            after = self.digests()
        for name in before:
            self.assertNotEqual(before[name], after[name])

    def test_declared_environment_value_changes_invalidate_cache(self):
        with mock.patch.dict(runner.os.environ, {"LOGOS_CACHE_TEST_CONFIG": "one"}):
            before = self.digests(["LOGOS_CACHE_TEST_CONFIG"])
        with mock.patch.dict(runner.os.environ, {"LOGOS_CACHE_TEST_CONFIG": "two"}):
            after = self.digests(["LOGOS_CACHE_TEST_CONFIG"])
        for name in before:
            self.assertNotEqual(before[name], after[name])

    def test_deleted_shared_input_invalidates_cache(self):
        before = self.digests()
        (self.root / "templates/chat.html").unlink()
        after = self.digests()
        for name in before:
            self.assertNotEqual(before[name], after[name])


class CacheTests(unittest.TestCase):
    def setUp(self):
        self.context = runner.context_for("tests/test_a.py")
        self.digest = "a" * 64

    def status(self, state="success", label="PASS", count=3):
        return {
            "context": self.context,
            "state": state,
            "description": f"{label}:{self.digest}:{count}",
        }

    def test_newer_failure_blocks_older_pass_for_same_inputs(self):
        snapshots = {
            "new": {self.context: self.status("failure", "FAIL")},
            "old": {self.context: self.status()},
        }
        with mock.patch.object(runner, "commit_statuses", side_effect=lambda _, sha, __: snapshots[sha]):
            cache = runner.read_cache("owner/repo", ["new", "old"], "unused")
        self.assertNotIn((self.context, self.digest), cache)

    def test_pending_execution_blocks_older_pass(self):
        snapshots = {
            "new": {self.context: self.status("pending", "PENDING")},
            "old": {self.context: self.status()},
        }
        with mock.patch.object(runner, "commit_statuses", side_effect=lambda _, sha, __: snapshots[sha]):
            cache = runner.read_cache("owner/repo", ["new", "old"], "unused")
        self.assertEqual(cache, {})

    def test_cache_preserves_test_count_and_source_commit(self):
        with mock.patch.object(runner, "commit_statuses", return_value={self.context: self.status(count=7)}):
            cache = runner.read_cache("owner/repo", ["old"], "unused")
        self.assertEqual(cache[(self.context, self.digest)], {"tests": 7, "source_commit": "old"})

    def test_zero_tests_are_never_cached_as_a_pass(self):
        with mock.patch.object(runner, "commit_statuses", return_value={self.context: self.status(count=0)}):
            self.assertEqual(runner.read_cache("owner/repo", ["old"], "unused"), {})

    def test_status_pagination_keeps_latest_result_for_context(self):
        first = [self.status("failure", "FAIL")] + [
            {"context": "other", "state": "success", "description": "unrelated"}
            for _ in range(99)
        ]
        with mock.patch.object(runner, "api", side_effect=[first, [self.status()]]) as request:
            statuses = runner.commit_statuses("owner/repo", "sha", "unused")
        self.assertEqual(statuses[self.context]["state"], "failure")
        self.assertEqual(request.call_count, 2)

    def test_empty_history_needs_no_network(self):
        with mock.patch.object(runner, "api") as request:
            self.assertEqual(runner.read_cache("owner/repo", [], "unused"), {})
        request.assert_not_called()


class CollectionTests(unittest.TestCase):
    def test_function_tests_cannot_be_silently_omitted(self):
        module = types.ModuleType("tests.example")
        module.test_example = lambda: None
        with mock.patch.object(runner.importlib, "import_module", return_value=module):
            with self.assertRaisesRegex(ValueError, "function tests require pytest"):
                runner.load_module("tests/example.py")

    def test_empty_module_cannot_be_reported_as_a_pass(self):
        module = types.ModuleType("tests.example")
        with mock.patch.object(runner.importlib, "import_module", return_value=module):
            with self.assertRaisesRegex(ValueError, "no unittest cases"):
                runner.load_module("tests/example.py")

    def test_local_gate_stops_before_git_or_network(self):
        with (
            mock.patch.object(runner, "github_job", return_value=False),
            mock.patch.object(runner, "git") as git,
            mock.patch.object(runner, "api") as api,
            contextlib.redirect_stderr(io.StringIO()),
        ):
            self.assertEqual(runner.main([]), 2)
        git.assert_not_called()
        api.assert_not_called()
