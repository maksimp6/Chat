"""The observer may disagree, but cannot invent reads or extend its budget."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from scripts import q14_observer as subject


def response(value):
    return {
        "data": {
            "id": "provider-response",
            "status": "completed",
            "usage": {"input_tokens": 10, "output_tokens": 20},
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": json.dumps(value)}],
                }
            ],
        }
    }


def decision(findings=None):
    return {
        "summary": "Не найдено доказанных замечаний.",
        "findings": findings or [],
        "limitations": "Только прочитанные данные.",
    }


class ObserverTests(unittest.TestCase):
    def setUp(self):
        self.source = {
            "file:allowed": {
                "kind": "file",
                "url": "source",
                "text": "one\ntwo\nthree\n",
            }
        }

    def test_read_exact_lines(self):
        result = subject.read_source(
            {"source": "file:allowed", "start": 2, "end": 3}, self.source, 100
        )
        self.assertEqual(result["content"], "2: two\n3: three")

    def test_unknown_source_denied(self):
        with self.assertRaisesRegex(ValueError, "source_not_allowed"):
            subject.read_source({"source": "file:/proc/self/environ"}, self.source, 100)

    def test_line_range_types_bounds(self):
        for start, end in [(False, 10), (-1, 10), (10, 1), (1, 201), ("1", 2)]:
            with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                subject.read_source(
                    {"source": "file:allowed", "start": start, "end": end}, self.source, 100
                )

    def test_evidence_budget(self):
        with self.assertRaisesRegex(ValueError, "evidence_budget_exceeded"):
            subject.read_source({"source": "file:allowed"}, self.source, 2)

    def test_git_path_denied_before_subprocess(self):
        with patch.object(subject.subprocess, "check_output") as execute:
            with self.assertRaises(ValueError):
                subject.git_text("../../secret")
            execute.assert_not_called()

    def test_host_denied_before_network(self):
        with patch.object(subject.http.client, "HTTPSConnection") as connect:
            with self.assertRaises(ValueError):
                subject.request("evil.example", "/", "secret")
            connect.assert_not_called()

    def test_empty_findings_allowed(self):
        self.assertEqual(subject.validate_decision(decision(), [])["findings"], [])

    def test_unread_evidence_rejected(self):
        with self.assertRaisesRegex(ValueError, "unread_evidence_cited"):
            subject.validate_decision(decision([{"evidence": ["invented"]}]), [])

    def test_incomplete_provider_response_rejected(self):
        with self.assertRaises(ValueError):
            subject.response_text({"status": "incomplete", "output": []})

    def test_secrets_redacted_recursively(self):
        self.assertEqual(
            subject.redact({"key": ["secret value"]}, ("secret",)),
            {"key": ["[REDACTED] value"]},
        )

    def test_model_chooses_real_read_before_decision(self):
        replies = iter(
            [
                response(
                    {
                        "reads": [{"source": "file:allowed", "start": 1, "end": 2}],
                        "decision": None,
                    }
                ),
                response({"reads": [], "decision": decision()}),
            ]
        )
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            with patch.object(subject, "read_source", wraps=subject.read_source) as reader:
                subject.inspect(self.source, lambda text: next(replies), reader, output, ())
                reader.assert_called_once()
            evidence = json.loads((output / "observations.json").read_text())
            self.assertEqual(evidence[0]["content"], "1: one\n2: two")
            ledger = json.loads((output / "ledger.json").read_text())
            self.assertEqual(len(ledger["calls"]), 2)

    def test_no_retry_after_provider_error(self):
        calls = []

        def fail(text):
            calls.append(text)
            raise TimeoutError("ambiguous")

        with tempfile.TemporaryDirectory() as directory, self.assertRaises(TimeoutError):
            subject.inspect(self.source, fail, subject.read_source, Path(directory), ())
        self.assertEqual(len(calls), 1)

    def test_read_loop_cannot_exceed_three_calls(self):
        calls = []

        def more(text):
            calls.append(text)
            return response({"reads": [{"source": "file:allowed"}], "decision": None})

        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "read_or_call_budget_invalid"),
        ):
            subject.inspect(self.source, more, subject.read_source, Path(directory), ())
        self.assertEqual(len(calls), 3)

    def test_large_input_never_sent(self):
        calls = []
        with (
            tempfile.TemporaryDirectory() as directory,
            self.assertRaisesRegex(ValueError, "input_budget_exceeded"),
        ):
            subject.inspect(
                {"huge": {"metadata": "x" * 50000}},
                lambda text: calls.append(text),
                None,
                Path(directory),
                (),
            )
        self.assertEqual(calls, [])

    def test_failed_read_does_not_become_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            evidence = []
            result = subject.observe(
                [{"source": "file:unknown"}],
                self.source,
                evidence,
                subject.read_source,
                Path(directory),
                (),
            )
        self.assertEqual(evidence, [])
        self.assertEqual(result[0]["tool_error"], "source_not_allowed")


if __name__ == "__main__":
    unittest.main()
