"""Deterministic controls for the recorded tools question; run only in a runner job."""

import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

if not (os.environ.get("GITHUB_ACTIONS") == "true" and os.environ.get("CI") == "true"
        and all(os.environ.get(key) for key in ("GITHUB_RUN_ID", "RUNNER_NAME", "RUNNER_TEMP"))):
    raise RuntimeError("LOCAL_TESTS_FORBIDDEN: run this check in a GitHub Actions runner job")

from scripts.logos_tools_self_check import (
    SELF_CHECK_QUESTION, build_self_check_question, evaluate_tool_evidence, verify_file_effects,
)


class ToolsSelfCheckTests(unittest.TestCase):
    def setUp(self):
        self.question = SELF_CHECK_QUESTION + "\nКаталог приложения: fixture"
        self.categories = {"filesystem": ["read_file", "file_stat"], "device": ["device_action"]}
        self.active = ["filesystem"]
        self.before = [{"role": "assistant", "text": "old answer", "invocation_id": "old"}]
        self.after = self.before + [
            {"role": "user", "text": self.question, "invocation_id": None},
            {"role": "assistant", "text": "Всё доступно, ограничений нет", "invocation_id": "fresh"},
        ]
        self.trace = {
            "context": {"invocation_id": "fresh"}, "errors": [], "tool_calls": [], "events": [],
            "api_requests": [{"step": 1, "payload": {"tools": [
                {"type": "function", "name": name} for name in self.categories["filesystem"]
            ]}}],
            "responses": [{"step": 1, "response_id": "response-fresh", "raw": {
                "id": "response-fresh", "status": "completed", "output": [
                    {"type": "function_call", "name": name, "call_id": "call-" + name}
                    for name in self.categories["filesystem"]
                ],
            }}],
        }

    def call(self, name="read_file", success=True, phase="execution", **extra):
        return {"name": name, "call_id": "call-" + name, "error": False,
                "result": {"success": success, "metadata": {"phase": phase}}, **extra}

    def evaluate(self, **changes):
        values = {"categories": self.categories, "active_categories": self.active,
                  "before_messages": self.before, "after_messages": self.after,
                  "traces": {"fresh": self.trace}, "question": self.question}
        values.update(changes)
        return evaluate_tool_evidence(**values)

    def test_prose_only_claim_never_passes(self):
        report = self.evaluate()
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertEqual(report["coverage"]["called"], 0)
        self.assertTrue(all(row["status"] == "NOT_RUN" for row in report["tools"]))
        self.assertEqual(report["limits"]["unbounded"], "UNKNOWN")

    def test_stale_history_and_empty_reply_are_not_evidence(self):
        for after in [self.before, self.after[:-1], self.after[:-1] + [
                {"role": "assistant", "text": " ", "invocation_id": "fresh"}], self.after[:-1] + [
                {"role": "assistant", "text": "old repeated", "invocation_id": "old"}]]:
            with self.subTest(after=after):
                self.assertEqual(self.evaluate(after_messages=after)["status"], "NOT_RUN")

    def test_exact_question_and_append_only_history_are_required(self):
        changed = copy.deepcopy(self.after)
        changed[1]["text"] = SELF_CHECK_QUESTION
        self.assertEqual(self.evaluate(after_messages=changed)["reason"], "exact_new_question_not_persisted")
        changed = copy.deepcopy(self.after)
        changed[0]["text"] = "replaced old history"
        self.assertEqual(self.evaluate(after_messages=changed)["status"], "NOT_RUN")
        self.assertEqual(self.evaluate(after_messages=self.after + [
            {"role": "user", "text": "another question"}])["reason"], "reply_binding_ambiguous")

    def test_missing_trace_or_mismatched_context_cannot_cover_tools(self):
        self.trace["tool_calls"] = [self.call()]
        self.assertEqual(self.evaluate(traces={})["coverage"]["called"], 0)
        self.trace["context"]["invocation_id"] = "old"
        self.assertEqual(self.evaluate()["coverage"]["called"], 0)

    def test_partial_native_coverage_lists_unexecuted_and_inactive_tools(self):
        self.trace["tool_calls"] = [self.call()]
        report = self.evaluate()
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["coverage"]["succeeded"], 1)
        self.assertEqual(report["inactive_tools"], ["device_action"])
        self.assertEqual({row["name"]: row["status"] for row in report["tools"]},
                         {"file_stat": "NOT_RUN", "read_file": "CALL_SUCCEEDED"})

    def test_complete_call_coverage_still_does_not_prove_unlimited_parameters(self):
        self.trace["tool_calls"] = [self.call(), self.call("file_stat")]
        report = self.evaluate()
        self.assertEqual(report["coverage"]["status"], "PASS")
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["limits"]["parameter_domains"], "UNKNOWN")
        self.assertEqual(report["hosted_and_mcp"]["status"], "UNKNOWN")

    def test_real_executor_success_without_phase_counts_as_completed_call(self):
        self.trace["tool_calls"] = [{
            "name": "read_file", "call_id": "real-call", "error": False,
            "result": {"success": True, "metadata": {
                "tool": "read_file", "transport": "responses_api",
                "executor": "local", "duration_ms": 1.2,
            }},
        }]
        self.trace["responses"][0]["raw"]["output"][0]["call_id"] = "real-call"
        report = self.evaluate()
        row = next(row for row in report["tools"] if row["name"] == "read_file")
        self.assertEqual(row["status"], "CALL_SUCCEEDED")
        self.assertEqual(row["calls"][0]["phase"], "UNKNOWN")
        self.assertEqual(report["coverage"]["succeeded"], 1)
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(report["limits"]["parameter_domains"], "UNKNOWN")

    def test_failure_and_approval_controls_override_success_claims(self):
        for call in [self.call(success=False), self.call(phase="approval_required"),
                     self.call(error=True)]:
            with self.subTest(call=call):
                self.trace["tool_calls"] = [self.call(), call]
                self.assertEqual(self.evaluate()["status"], "FAIL")
        self.trace["tool_calls"] = [self.call()]
        self.trace["errors"] = [{"error": "do not export this"}]
        self.assertEqual(self.evaluate()["status"], "FAIL")

    def test_missing_call_identity_and_unexpected_tool_are_not_coverage(self):
        self.trace["tool_calls"] = [self.call(call_id=None), self.call("not_in_catalog")]
        report = self.evaluate()
        self.assertEqual(report["coverage"]["succeeded"], 0)
        self.assertEqual(report["unexpected_call_count"], 1)
        self.assertEqual(next(row for row in report["tools"] if row["name"] == "read_file")["status"], "UNKNOWN")

    def test_dispatcher_success_without_provider_call_is_not_model_coverage(self):
        self.trace["tool_calls"] = [self.call(), self.call("file_stat")]
        self.trace["responses"][0]["raw"]["output"] = []
        report = self.evaluate()
        self.assertEqual(report["status"], "NOT_RUN")
        self.assertEqual(report["coverage"]["called"], 0)
        self.assertEqual(report["unlinked_executor_call_count"], 2)

    def test_provider_call_without_executor_result_is_not_success(self):
        report = self.evaluate()
        self.assertEqual(report["coverage"]["succeeded"], 0)
        self.assertEqual(report["status"], "NOT_RUN")

    def test_provider_identity_requires_matching_name_and_round(self):
        for name, step in [("file_stat", 1), ("read_file", 2)]:
            with self.subTest(name=name, step=step):
                self.trace["tool_calls"] = [self.call(name=name, step=step, call_id="call-read_file")]
                report = self.evaluate()
                self.assertEqual(report["coverage"]["called"], 0)
                self.assertEqual(report["coverage"]["succeeded"], 0)

    def test_conflicting_provider_call_identity_is_not_coverage(self):
        self.trace["responses"][0]["raw"]["output"].append(
            {"type": "function_call", "name": "file_stat", "call_id": "call-read_file"})
        self.trace["tool_calls"] = [self.call()]
        self.assertEqual(self.evaluate()["coverage"]["called"], 0)

    def test_unfinished_function_item_is_not_successful_model_call_evidence(self):
        self.trace["tool_calls"] = [self.call()]
        self.trace["responses"][0]["raw"]["output"][0]["status"] = "in_progress"
        self.assertEqual(self.evaluate()["coverage"]["called"], 0)

    def test_native_nested_function_call_links_to_real_executor(self):
        output = self.trace["responses"][0]["raw"]["output"]
        self.trace["responses"][0]["raw"]["output"] = [
            {"type": "message", "status": "completed", "content": output}]
        self.trace["tool_calls"] = [self.call(), self.call("file_stat")]
        report = self.evaluate()
        self.assertEqual(report["coverage"]["succeeded"], 2)
        self.assertEqual(report["coverage"]["status"], "PASS")

    def test_non_null_empty_provider_error_is_a_failure(self):
        self.trace["responses"][0]["raw"]["error"] = {}
        self.assertEqual(self.evaluate()["status"], "FAIL")

    def test_incomplete_provider_overrides_local_completed_and_success_claims(self):
        self.trace["status"] = "completed"
        self.trace["tool_calls"] = [self.call(), self.call("file_stat")]
        self.trace["responses"][0]["raw"].update(
            status="incomplete", incomplete_details={"reason": "max_output_tokens"})
        report = self.evaluate()
        self.assertEqual(report["status"], "FAIL")
        self.assertEqual(report["provider"]["status"], "FAIL")
        self.assertEqual(report["provider"]["responses"][0]["incomplete_reason"], "max_output_tokens")

    def test_missing_terminal_state_or_outgoing_inventory_cannot_pass(self):
        self.trace["tool_calls"] = [self.call(), self.call("file_stat")]
        for field in ["status", "api_requests"]:
            with self.subTest(field=field):
                trace = copy.deepcopy(self.trace)
                if field == "status":
                    del trace["responses"][0]["raw"]["status"]
                else:
                    del trace[field]
                self.assertEqual(self.evaluate(traces={"fresh": trace})["status"], "PARTIAL")

    def test_catalogue_is_distinct_from_payload_inventory(self):
        self.trace["api_requests"][0]["payload"]["tools"] = [{"type": "function", "name": "read_file"}]
        self.trace["tool_calls"] = [self.call()]
        report = self.evaluate()
        self.assertEqual(report["inventory"]["not_offered"], ["file_stat"])
        self.assertEqual(report["inventory"]["offered"], ["read_file"])
        self.assertEqual(report["coverage"]["expected"], 2)
        self.assertEqual(report["status"], "PARTIAL")
        self.assertEqual(next(row for row in report["tools"] if row["name"] == "file_stat")["status"], "NOT_OFFERED")

    def test_empty_or_disabled_inventory_never_passes(self):
        self.assertEqual(self.evaluate(categories={})["status"], "ERROR")
        self.assertEqual(self.evaluate(active_categories=[])["status"], "NOT_RUN")
        self.assertEqual(self.evaluate(active_categories=["unknown"])["status"], "ERROR")

    def test_request_settings_export_only_known_structural_fields(self):
        self.trace["tool_calls"] = [self.call()]
        self.trace["events"] = [{"type": "api_request_manifest", "payload": {"manifest": {
            "capture_stage": "assembled_payload_before_post", "provider_effective": {"state": "unlimited"},
            "settings": {"max_output_tokens": {"state": "present", "value": 4096},
                         "max_tool_calls": {"state": "absent"},
                         "truncation": {"state": "present", "value": "disabled"},
                         "reasoning": {"state": "present", "effort": "high"},
                         "metadata": {"state": "present", "value": "SECRET_CANARY"}},
            "tools": {"count": 3, "types": {"function": 2, "web_search": 1}},
            "instructions": "SECRET_CANARY", "input": "SECRET_CANARY"}}}]
        request = self.evaluate()["limits"]["requests"][0]
        self.assertEqual(request["settings"]["max_output_tokens"]["value"], 4096)
        self.assertEqual(request["settings"]["max_tool_calls"], {"state": "absent"})
        self.assertEqual(request["provider_effective"], "UNKNOWN")
        self.assertEqual(request["offered_tool_types"], {"function": 2, "web_search": 1})
        self.assertNotIn("SECRET_CANARY", json.dumps(self.evaluate()))

    def test_no_arbitrary_arguments_results_errors_or_reply_are_exported(self):
        self.after[-1]["text"] = "SECRET_CANARY"
        call = self.call(call_id="SECRET_CANARY")
        call.update(arguments={"key": "SECRET_CANARY"}, extra="SECRET_CANARY")
        call["result"]["data"] = "SECRET_CANARY"
        self.trace["tool_calls"] = [call]
        report = self.evaluate()
        self.assertNotIn("SECRET_CANARY", json.dumps(report))
        self.assertIn("call_id_sha256", next(row for row in report["tools"] if row["name"] == "read_file")["calls"][0])

    def test_compact_question_binds_real_file_calls_to_nonce_and_folder(self):
        nonce = "a" * 32
        folder = "tmp/logos-tools-check-" + nonce
        question = build_self_check_question(
            {"categories": self.categories, "active_categories": self.active}, nonce, folder)
        self.assertIn('"name":"write_file"', question)
        self.assertIn('"source_path":"' + folder + '/payload.txt"', question)
        self.assertIn('"archive_path":"' + folder + '/payload.zip"', question)
        self.assertIn("LOGOS_TOOLS_SELF_CHECK:" + nonce, question)
        self.assertNotIn("device_action", question)
        self.assertLess(len(question), 2200)
        for invalid in ["/tmp/absolute", "tmp/../escape", "tmp/logos-tools-check-" + "b" * 32]:
            with self.subTest(folder=invalid), self.assertRaises(ValueError):
                build_self_check_question({"categories": self.categories, "active_categories": self.active}, nonce, invalid)

    def test_file_effects_require_exact_nonce_bytes_and_zip_contents(self):
        nonce = "c" * 32
        folder = "tmp/logos-tools-check-" + nonce
        content = ("LOGOS_TOOLS_SELF_CHECK:" + nonce).encode()
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / folder
            target.mkdir(parents=True)
            self.assertEqual(verify_file_effects(directory, nonce, folder)["status"], "FAIL")
            (target / "payload.txt").write_bytes(content)
            with zipfile.ZipFile(target / "payload.zip", "w") as archive:
                archive.writestr("payload.txt", content)
            report = verify_file_effects(directory, nonce, folder)
            self.assertEqual(report["status"], "PASS")
            self.assertEqual(report["actual_sha256"], report["expected_sha256"])
            (target / "payload.txt").write_bytes(b"wrong nonce")
            self.assertEqual(verify_file_effects(directory, nonce, folder)["status"], "FAIL")


if __name__ == "__main__":
    unittest.main()
