"""Negative controls for a read-only model judgment, without paid calls."""

import unittest

from scripts.pr_court import bounded_input, extract_text, validate_pr


class PRCourtTest(unittest.TestCase):
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
