"""Negative controls for a read-only model judgment, without paid calls."""

import json
import os
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from scripts import pr_court
from scripts.pr_court import bounded_input, extract_text, validate_pr


class PRCourtTest(unittest.TestCase):
    def test_missing_rules_produces_nonapproval_without_a_model_or_provider_credentials(self):
        pr = {
            "head": {"sha": "head", "repo": {"full_name": "o/r"}},
            "base": {"sha": "base", "ref": "develop"},
            "state": "open",
        }
        missing = urllib.error.HTTPError("https://api.github.com/rules", 404, "missing", {}, None)
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({"number": 1, "pull_request": pr}))
            env = {
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_REPOSITORY": "o/r",
                "GH_TOKEN": "synthetic-github-token",
            }
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with (
                    patch.dict(os.environ, env, clear=True),
                    patch.object(pr_court, "request", side_effect=[pr, [], missing]) as http,
                ):
                    self.assertEqual(pr_court.main(), 0)
                report = json.loads(Path("pr-court-output/report.json").read_text())
                self.assertEqual(report["status"], "NO_RULES_NOT_APPROVAL")
                self.assertEqual(report["model_call"], "NOT_RUN")
                self.assertFalse(report["merge_approval"])
                self.assertFalse(any(call.kwargs.get("provider") for call in http.call_args_list))
            finally:
                os.chdir(previous)

    def test_rule_read_permission_failure_is_not_treated_as_a_missing_file(self):
        denied = urllib.error.HTTPError("https://api.github.com/rules", 403, "denied", {}, None)
        with (
            patch.object(pr_court, "request", side_effect=denied),
            self.assertRaises(urllib.error.HTTPError),
        ):
            pr_court.read_base_rules("https://api.github.com/repos/o/r", "head", "synthetic")

    def test_empty_http_success_is_not_a_judgment(self):
        for response in ({}, {"id": "r", "status": "completed", "output": []}):
            with self.assertRaises(ValueError):
                extract_text(response)

    def test_incomplete_generation_is_not_success(self):
        with self.assertRaises(ValueError):
            extract_text({"id": "r", "status": "incomplete"})

    def test_completed_text_is_required(self):
        self.assertEqual(
            extract_text(
                {
                    "id": "r",
                    "status": "completed",
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "Риск"}]}
                    ],
                }
            ),
            "Риск",
        )

    def test_stale_sha_wrong_base_fork_and_closed_pr_rejected(self):
        pr = {
            "head": {"sha": "a", "repo": {"full_name": "o/r"}},
            "base": {"ref": "develop"},
            "state": "open",
        }
        validate_pr(pr, "a", "o/r")
        for sha, repo in (("b", "o/r"), ("a", "fork/r")):
            with self.assertRaises(ValueError):
                validate_pr(pr, sha, repo)
        for field, value in (("state", "closed"),):
            pr[field] = value
            with self.assertRaises(ValueError):
                validate_pr(pr, "a", "o/r")
        pr["state"] = "open"
        pr["base"]["ref"] = "master"
        with self.assertRaises(ValueError):
            validate_pr(pr, "a", "o/r")

    def test_advanced_base_is_not_reused(self):
        pr = {
            "head": {"sha": "a", "repo": {"full_name": "o/r"}},
            "base": {"ref": "develop", "sha": "new"},
            "state": "open",
        }
        with self.assertRaises(ValueError):
            validate_pr(pr, "a", "o/r", "old")

    def test_incomplete_or_missing_diff_rejected(self):
        for files in ([], [{"filename": "binary"}]):
            with self.assertRaises(ValueError):
                bounded_input("rules", {"changed_files": 1}, files)

    def test_truncated_patch_rejected(self):
        with self.assertRaises(ValueError):
            bounded_input(
                "rules",
                {"changed_files": 1, "title": "test"},
                [{"patch": "+one", "additions": 2, "deletions": 0}],
            )

    def test_oversize_not_silently_truncated(self):
        with self.assertRaises(ValueError):
            bounded_input("x" * 60001, {"changed_files": 0, "title": "test"}, [])


if __name__ == "__main__":
    unittest.main()
