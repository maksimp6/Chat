"""Replay the actual bad verdict and exercise real report acceptance boundaries."""

import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import pr_court
from scripts.pr_court_evidence import added_lines, validate_judgment

FIXTURE = Path(__file__).parent / "fixtures/pr_court_1422_response.json"
RULE = "Never commit secrets into the repository."
FILES = [
    {"filename": "example.py", "patch": "@@ -0,0 +1 @@\n+value = 1", "additions": 1, "deletions": 0}
]


def candidate():
    return {
        "claim": "Проверить значение",
        "rule_quote": RULE,
        "path": "example.py",
        "line": 1,
        "source_quote": "value = 1",
        "reason": "Предположение, требует ревью",
        "reproduction": "Проверить строку",
    }


class EvidenceTest(unittest.TestCase):
    def test_real_uncited_verdict_is_rejected(self):
        original = json.loads(FIXTURE.read_text())
        self.assertEqual(original["run_id"], "39385308582")
        with self.assertRaises(ValueError):
            validate_judgment(original["judgment"], RULE, FILES)

    def test_invented_quote_path_line_and_source_rejected(self):
        for field, value in (
            ("rule_quote", "Invented rule that never existed in base"),
            ("path", "nonexistent.py"),
            ("line", 9),
            ("line", True),
            ("source_quote", "leaked_actual_secret"),
            ("reproduction", ""),
        ):
            finding = candidate()
            finding[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate_judgment(
                    json.dumps({"findings": [finding], "limitations": []}), RULE, FILES
                )

    def test_citations_never_establish_guilt_or_semantic_truth(self):
        # Syntactically valid citations do NOT entail this alleged violation.
        result = validate_judgment(
            json.dumps({"findings": [candidate()], "limitations": []}), RULE, FILES
        )
        self.assertEqual(result["status"], "CANDIDATES_REQUIRE_REVIEW")
        self.assertEqual(result["semantic_quality"], "NOT_VERIFIED")
        self.assertFalse(result["merge_approval"])

    def test_empty_findings_are_not_an_approval(self):
        result = validate_judgment('{"findings": [], "limitations": []}', RULE, FILES)
        self.assertEqual(result["status"], "NO_CANDIDATES_NOT_APPROVAL")
        self.assertFalse(result["merge_approval"])

    def test_hunk_line_numbers_account_for_deletions_and_context(self):
        patch_text = "@@ -5,3 +5,3 @@\n context\n-old\n+new\n next\n@@ -20 +30 @@\n-old\n+later"
        self.assertEqual(added_lines(patch_text), {6: "new", 30: "later"})

    def test_live_entrypoint_rejects_original_even_after_successful_api_call(self):
        original = json.loads(FIXTURE.read_text())
        pr = {
            "head": {"sha": "head", "repo": {"full_name": "o/r"}},
            "base": {"sha": "base", "ref": "develop"},
            "state": "open",
            "changed_files": 1,
            "title": "test",
        }
        response = {
            "id": original["response_id"],
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": original["judgment"]}],
                }
            ],
        }
        replies = [pr, FILES, {"content": base64.b64encode(RULE.encode()).decode()}, response, pr]
        with tempfile.TemporaryDirectory() as directory:
            event = Path(directory) / "event.json"
            event.write_text(json.dumps({"number": 1422, "pull_request": pr}))
            env = {
                "GITHUB_EVENT_PATH": str(event),
                "GITHUB_REPOSITORY": "o/r",
                "GH_TOKEN": "secret-gh",
                "YANDEX_API_KEY": "secret-yandex",
                "YANDEX_PROJECT_ID": "secret-folder",
                "GITHUB_RUN_ID": "test",
                "GITHUB_STEP_SUMMARY": str(Path(directory) / "summary.md"),
            }
            previous = Path.cwd()
            try:
                os.chdir(directory)
                with (
                    patch.dict(os.environ, env),
                    patch.object(pr_court, "request", side_effect=replies) as http,
                ):
                    self.assertEqual(pr_court.main(), 1)
                self.assertEqual(
                    sum(call.kwargs.get("provider", False) for call in http.call_args_list), 1
                )
                result = json.loads(Path("pr-court-output/report.json").read_text())
                self.assertEqual(result["model_call"], "COMPLETED")
                self.assertEqual(result["status"], "MODEL_OUTPUT_REJECTED")
                self.assertFalse(result["merge_approval"])
            finally:
                os.chdir(previous)


if __name__ == "__main__":
    unittest.main()
