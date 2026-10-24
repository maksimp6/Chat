"""Offline regression tests; fake provider replies never prove live AI execution."""

from __future__ import annotations

import copy
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from scripts import q14_ai_review as ai
from scripts.q14_simulation import CASES, NORMAL, Scenario, run_cases, simulate


def valid_review(stage: str) -> dict[str, object]:
    return {
        "stage": stage,
        "cases": [
            {
                "id": case.id,
                "model_outcome": case.expected,
                "finding": "Учебное заключение, не проверка объекта.",
                "evidence_ids": [f"{case.id}:E1"],
                "actual_harm": "NOT_ESTABLISHED",
                "potential_victims": "Исполнитель при наличии воздействия.",
                "responsibility": "Реальная ответственность не установлена.",
                "repair": "Проверить предпосылки перед продолжением.",
                "uncertainty": "Физический объект не наблюдался.",
            }
            for case in CASES
        ],
        "symbolic_roles": "Образы являются интерпретацией; права модели не расширены.",
    }


def fake_response(stage: str) -> dict[str, object]:
    return {
        "data": {
            "id": f"response-{stage}",
            "status": "completed",
            "usage": {"input_tokens": 12, "output_tokens": 24},
            "output": [
                {
                    "type": "message",
                    "content": [
                        {
                            "type": "output_text",
                            "text": json.dumps(valid_review(stage), ensure_ascii=False),
                        }
                    ],
                }
            ],
        },
        "request_id": f"request-{stage}",
    }


class ModelTests(unittest.TestCase):
    def test_all_twelve_cases_and_reproducibility(self) -> None:
        self.assertEqual(len(run_cases()), 12)
        self.assertEqual(run_cases(), run_cases())
        self.assertEqual([row["outcome"] for row in run_cases()], [case.expected for case in CASES])

    def test_negative_control_detects_unsafe_operation(self) -> None:
        self.assertEqual(simulate(CASES[7])["outcome"], "HAZARDOUS_OPERATION")
        self.assertEqual(simulate(replace(CASES[7], guards_enabled=True))["outcome"], "BLOCKED")

    def test_done_is_not_evidence(self) -> None:
        self.assertEqual(simulate(CASES[5])["facts"], [])
        self.assertEqual(simulate(CASES[5])["outcome"], "BLOCKED")

    def test_arm_context_changes_same_events(self) -> None:
        self.assertEqual(CASES[3].events, CASES[4].events)
        self.assertNotEqual(simulate(CASES[3])["outcome"], simulate(CASES[4])["outcome"])

    def test_reenergize_invalidates_stale_evidence(self) -> None:
        result = simulate(CASES[8])
        self.assertEqual(result["outcome"], "BLOCKED")
        self.assertNotIn("ABSENCE_CONFIRMED", result["facts"])

    def test_omitting_any_required_stage_never_admits(self) -> None:
        for index in range(len(NORMAL)):
            with self.subTest(omitted=NORMAL[index]):
                case = Scenario("T", "omitted stage", NORMAL[:index] + NORMAL[index + 1 :], "")
                self.assertNotEqual(simulate(case)["outcome"], "MODEL_ACCEPTED")

    def test_no_actual_injury_inferred_from_hazard(self) -> None:
        for result in run_cases():
            self.assertEqual(result["actual_harm"], "NOT_ESTABLISHED")
            self.assertFalse(result["physical_installation_verified"])

    def test_unknown_event_is_error(self) -> None:
        self.assertEqual(simulate(CASES[11])["outcome"], "ERROR")


class ReviewTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = ai.evidence_for_model(run_cases())

    def test_oracle_is_not_in_model_input(self) -> None:
        text = ai.prompt("accusation", self.evidence, [])
        self.assertNotIn('"expected"', text)
        self.assertNotIn('"outcome"', text)
        self.assertIn("PROPOSED_INTERPRETATIONS_NOT_NEW_PROJECT_LAW", text)

    def test_valid_schema(self) -> None:
        value = valid_review("accusation")
        self.assertEqual(ai.validate_review(json.dumps(value), "accusation", self.evidence), value)

    def test_rejects_missing_cases(self) -> None:
        value = valid_review("accusation")
        value["cases"] = []
        with self.assertRaisesRegex(ValueError, "review_case_count_invalid"):
            ai.validate_review(json.dumps(value), "accusation", self.evidence)

    def test_rejects_duplicate_or_unknown_ids(self) -> None:
        for replacement in ("S2", "FAKE"):
            value = valid_review("accusation")
            value["cases"][0]["id"] = replacement
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                ai.validate_review(json.dumps(value), "accusation", self.evidence)

    def test_rejects_invented_harm(self) -> None:
        value = valid_review("accusation")
        value["cases"][0]["actual_harm"] = "INJURY_CONFIRMED"
        with self.assertRaisesRegex(ValueError, "unsupported_actual_harm_claim"):
            ai.validate_review(json.dumps(value), "accusation", self.evidence)

    def test_rejects_cross_case_and_fabricated_evidence(self) -> None:
        for ref in ("S2:E1", "S1:E999"):
            value = valid_review("accusation")
            value["cases"][0]["evidence_ids"] = [ref]
            with (
                self.subTest(ref=ref),
                self.assertRaisesRegex(ValueError, "review_evidence_invalid"),
            ):
                ai.validate_review(json.dumps(value), "accusation", self.evidence)

    def test_rejects_wrong_stage(self) -> None:
        with self.assertRaisesRegex(ValueError, "review_stage_invalid"):
            ai.validate_review(json.dumps(valid_review("defence")), "accusation", self.evidence)

    def test_rejects_empty_text_and_missing_roles(self) -> None:
        for field in ("finding", "potential_victims", "responsibility", "repair", "uncertainty"):
            value = valid_review("accusation")
            value["cases"][0][field] = ""
            with self.subTest(field=field), self.assertRaises(ValueError):
                ai.validate_review(json.dumps(value), "accusation", self.evidence)
        value = valid_review("accusation")
        value["symbolic_roles"] = ""
        with self.assertRaisesRegex(ValueError, "review_roles_missing"):
            ai.validate_review(json.dumps(value), "accusation", self.evidence)

    def test_input_bound(self) -> None:
        with self.assertRaisesRegex(ValueError, "input_budget_exceeded"):
            ai.prompt("accusation", self.evidence, [{"untrusted": "x" * ai.MAX_INPUT_CHARS}])

    def test_response_must_be_completed_and_have_text(self) -> None:
        with self.assertRaisesRegex(ValueError, "provider_response_not_completed"):
            ai.response_text({"status": "incomplete"})
        with self.assertRaisesRegex(ValueError, "provider_text_missing"):
            ai.response_text({"status": "completed", "output": []})

    def test_false_model_success_fails_comparison(self) -> None:
        value = valid_review("decision_draft")
        value["cases"][7]["model_outcome"] = "MODEL_ACCEPTED"
        checked = ai.validate_review(json.dumps(value), "decision_draft", self.evidence)
        self.assertFalse(ai.evaluate_decision(checked)["all_match"])

    def test_transport_rejects_unapproved_route_without_connecting(self) -> None:
        with patch.object(ai.http.client, "HTTPSConnection") as connection:
            with self.assertRaisesRegex(ValueError, "provider_route_not_allowed"):
                ai.make_transport("fake", "project")("POST", "/not-allowed", {})
            connection.assert_not_called()

    def test_transport_does_not_follow_redirects_or_return_error_body(self) -> None:
        with patch.object(ai.http.client, "HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 302
            response.read.return_value = b"fake-secret"
            response.getheader.return_value = "request-1"
            result = ai.make_transport("fake-secret", "project")("GET", "/v1/models", None)
            self.assertEqual(result["error"], "provider_http_302")
            self.assertNotIn("fake-secret", json.dumps(result))
            self.assertEqual(connection.return_value.request.call_count, 1)
            connection.return_value.close.assert_called_once()
            connection.assert_called_once_with(ai.HOST, timeout=90)

    def test_transport_bounds_response(self) -> None:
        with patch.object(ai.http.client, "HTTPSConnection") as connection:
            response = connection.return_value.getresponse.return_value
            response.status = 200
            response.read.return_value = b"x" * (ai.MAX_RESPONSE_BYTES + 1)
            response.getheader.return_value = "request-1"
            result = ai.make_transport("fake", "project")("GET", "/v1/models", None)
            self.assertEqual(result["error"], "provider_response_too_large")
            response.read.assert_called_once_with(ai.MAX_RESPONSE_BYTES + 1)

    def test_redaction_before_json_escaping(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "escaped.json"
            secret = 'key"value'
            ai.write_json(path, {"echo": [secret]}, (secret,))
            self.assertEqual(json.loads(path.read_text())["echo"], ["[REDACTED]"])

    def test_invalid_outcome_types_fail_closed(self) -> None:
        for outcome in ([], {}, None, "invented"):
            value = valid_review("accusation")
            value["cases"][0]["model_outcome"] = outcome
            with self.subTest(outcome=outcome):
                with self.assertRaisesRegex(ValueError, "review_outcome_invalid"):
                    ai.validate_review(json.dumps(value), "accusation", self.evidence)

    def test_missing_and_invalid_credentials(self) -> None:
        for env in ({}, {"YANDEX_API_KEY": "fake", "YANDEX_PROJECT_ID": "a\nb"}):
            with self.subTest(env=env), self.assertRaises(ValueError):
                ai.credentials(env)

    def test_redaction_in_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "evidence.json"
            ai.write_json(
                path, {"echo": "fake-secret fake-project"}, ("fake-secret", "fake-project")
            )
            self.assertNotIn("fake-secret", path.read_text())
            self.assertNotIn("fake-project", path.read_text())

    def test_approval_checked_before_paid_request(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            for calls, reference in ((0, ""), (3, ""), (4, "owner")):
                with patch.object(ai, "make_transport") as transport:
                    with self.assertRaisesRegex(ValueError, "owner_budget_approval_required"):
                        ai.live_review(
                            transport,
                            "fake-project",
                            Path(folder),
                            self.evidence,
                            calls,
                            reference,
                            (),
                        )
                    transport.assert_not_called()

    def test_three_serial_calls_keep_usage_and_ids(self) -> None:
        seen: list[str] = []

        def send(method: str, path: str, payload: ai.JsonObject | None) -> ai.JsonObject:
            self.assertEqual((method, path), ("POST", "/v1/responses"))
            self.assertIsNotNone(payload)
            self.assertEqual(payload["max_output_tokens"], 4096)
            stage = ai.STAGES[len(seen)]
            seen.append(stage)
            return fake_response(stage)

        with tempfile.TemporaryDirectory() as folder:
            result = ai.live_review(
                send, "fake-project", Path(folder), self.evidence, 3, "test-only-approval", ()
            )
            self.assertEqual(seen, list(ai.STAGES))
            self.assertEqual(result["status"], "AI_REVIEWS_RECEIVED")
            self.assertEqual(len(result["calls"]), 3)
            self.assertIsNone(result["provider_confirmed_spend"])
            self.assertFalse(result["independent_experts"])
            self.assertEqual(result["calls"][0]["usage"]["input_tokens"], 12)

    def test_http_failure_never_retries_or_hides_blocker(self) -> None:
        seen: list[str] = []

        def send(method: str, path: str, payload: ai.JsonObject | None) -> ai.JsonObject:
            seen.append(path)
            return {"error": "provider_http_403", "request_id": "request-denied"}

        with tempfile.TemporaryDirectory() as folder:
            result = ai.live_review(
                send, "fake-project", Path(folder), self.evidence, 3, "test-only", ()
            )
            self.assertEqual(len(seen), 1)
            self.assertEqual(result["status"], "BLOCKED")
            self.assertEqual(result["calls"][0]["error"], "provider_http_403")

    def test_timeout_preserves_ambiguous_attempt_without_retry(self) -> None:
        calls = 0

        def send(method: str, path: str, payload: ai.JsonObject | None) -> ai.JsonObject:
            nonlocal calls
            calls += 1
            raise TimeoutError("fake-secret must not appear in ledger")

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(TimeoutError):
                ai.live_review(
                    send,
                    "fake-project",
                    Path(folder),
                    self.evidence,
                    3,
                    "test-only",
                    ("fake-secret",),
                )
            text = (Path(folder) / "ai-ledger.json").read_text()
            self.assertNotIn("fake-secret", text)
            self.assertEqual(json.loads(text)["calls"][0]["status"], "AMBIGUOUS_REQUEST_NO_RETRY")
            self.assertEqual(calls, 1)

    def test_invalid_output_preserves_response_and_usage(self) -> None:
        def send(method: str, path: str, payload: ai.JsonObject | None) -> ai.JsonObject:
            value = fake_response("accusation")
            value["data"]["output"][0]["content"][0]["text"] = "not json"
            return value

        with tempfile.TemporaryDirectory() as folder:
            with self.assertRaises(ValueError):
                ai.live_review(
                    send, "fake-project", Path(folder), self.evidence, 3, "test-only", ()
                )
            ledger = json.loads((Path(folder) / "ai-ledger.json").read_text())
            self.assertEqual(ledger["status"], "INCOMPLETE_NO_RETRY")
            self.assertEqual(ledger["calls"][0]["response_id"], "response-accusation")
            self.assertTrue((Path(folder) / "accusation-response.json").exists())

    def test_probe_is_get_only_and_not_generation_proof(self) -> None:
        def send(method: str, path: str, payload: ai.JsonObject | None) -> ai.JsonObject:
            self.assertEqual((method, path, payload), ("GET", "/v1/models", None))
            return {"data": {"data": [{"id": ai.MODEL}]}}

        result = ai.probe_access(send)
        self.assertEqual(result["status"], "MODEL_CATALOGUE_READ")
        self.assertFalse(result["generation_verified"])

    def test_probe_rejects_errors_and_empty_catalogue(self) -> None:
        for value in (
            {"error": "provider_http_401"},
            {"data": {"data": []}},
            {"data": {"data": [None, {}]}},
        ):

            def send(method: str, path: str, payload: ai.JsonObject | None) -> ai.JsonObject:
                return copy.deepcopy(value)

            self.assertEqual(ai.probe_access(send)["status"], "API_ACCESS_CHECK_FAILED")


if __name__ == "__main__":
    unittest.main()
