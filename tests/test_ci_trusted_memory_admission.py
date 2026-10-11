"""Trusted Memory DB merge-admission tests; candidate files are data, never code."""

from __future__ import annotations

from copy import deepcopy
import base64
import hashlib
import io
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from ci import trusted_memory_admission as gate

REPO = "maksimp6/Chat"
HEAD = "a" * 40
BASE = "b" * 40
NEXT_HEAD = "c" * 40


class FakeGitHub:
    def __init__(self, policy: dict) -> None:
        self.policy = policy
        self.files: dict[tuple[str, str], gate.FileRecord] = {}
        self.statuses: list[tuple[str, str, str]] = []
        self.read_count = 0
        self.change_head_on_second_read = False
        self.pr = {
            "number": 1106,
            "state": "open",
            "head": {"sha": HEAD, "ref": "topic", "repo": {"full_name": REPO}},
            "base": {"sha": BASE, "ref": "develop", "repo": {"full_name": REPO}},
        }

    def get_pr(self, number: int) -> dict:
        assert number == 1106
        self.read_count += 1
        state = deepcopy(self.pr)
        if self.change_head_on_second_read and self.read_count >= 2:
            state["head"]["sha"] = NEXT_HEAD
        return state

    def file_at(self, sha: str, path: str) -> gate.FileRecord | None:
        return self.files.get((sha, path))

    def post_status(self, head: str, state: str, context: str, description: str, target_url: str) -> None:
        assert head == HEAD
        assert context == self.policy["status_context"]
        assert target_url.startswith("https://github.com/")
        assert len(description) <= 140
        self.statuses.append((head, state, description))

    def present(self, sha: str, path: str, digest: str | None = None,
                blob_sha: str = "d" * 40) -> None:
        if digest is None:
            digest = self.policy["pinned_sha256"].get(path, "e" * 64)
        self.files[sha, path] = gate.FileRecord(digest, blob_sha, 10)

    def seed(self, *, base_stage: str = "legacy", head_stage: str = "legacy") -> None:
        def install(sha: str, stage: str) -> None:
            for path in self.policy["always_required"]:
                self.present(sha, path)
            if stage in ("core", "fast"):
                self.present(sha, self.policy["core_marker"])
                for path in self.policy["core_contracts"]:
                    self.present(sha, path)
            if stage == "fast":
                for path in self.policy["pinned_sha256"]:
                    self.present(sha, path)
                self.present(sha, ".github/workflows/ci.yml",
                             blob_sha=self.policy["approved_ci_git_blob_sha"])

        install(BASE, base_stage)
        install(HEAD, head_stage)


class TrustedAdmissionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.policy = gate.read_policy(Path(__file__).resolve().parents[1]
                                      / "ci/trusted_memory_policy.json")

    def fixture(self, base: str = "legacy", head: str = "legacy") -> FakeGitHub:
        api = FakeGitHub(self.policy)
        api.seed(base_stage=base, head_stage=head)
        return api

    def decision(self, api: FakeGitHub) -> gate.Decision:
        return gate.audit_pr(api, self.policy, 1106, HEAD, BASE)

    def test_read_only_legacy_snapshot_passes(self):
        self.assertTrue(self.decision(self.fixture()).accepted)

    def test_legacy_source_modification_fails_closed(self):
        api = self.fixture()
        api.present(HEAD, "agent_memory/file_memory_db.py", "f" * 64)
        self.assertIn("agent_memory/file_memory_db.py", "|".join(self.decision(api).errors))

    def test_core_introduction_preserves_original_contracts(self):
        api = self.fixture(head="core")
        decision = self.decision(api)
        self.assertEqual(decision.stage, "core")
        self.assertTrue(decision.accepted)
        api.present(HEAD, "tests/test_memory_store.py", "f" * 64)
        self.assertFalse(self.decision(api).accepted)

    def test_complete_fast_stage_is_accepted(self):
        api = self.fixture(head="fast")
        decision = self.decision(api)
        self.assertEqual(decision.stage, "fast")
        self.assertTrue(decision.accepted, decision.errors)

    def test_delete_accepted_fast_test_is_rejected(self):
        api = self.fixture(base="fast", head="fast")
        api.files.pop((HEAD, "tests/test_ci_fast_contracts.py"))
        self.assertFalse(self.decision(api).accepted)

    def test_partial_fast_suite_is_rejected(self):
        api = self.fixture(head="legacy")
        api.present(HEAD, "tests/test_ci_fast_contracts.py")
        decision = self.decision(api)
        self.assertEqual(decision.stage, "fast")
        self.assertTrue(any("missing" in error for error in decision.errors))

    def test_fast_ci_workflow_removal_and_substitution_are_rejected(self):
        api = self.fixture(head="fast")
        api.files.pop((HEAD, ".github/workflows/ci.yml"))
        self.assertFalse(self.decision(api).accepted)
        api.present(HEAD, ".github/workflows/ci.yml", blob_sha="0" * 40)
        self.assertFalse(self.decision(api).accepted)

    def test_mutated_tests_init_and_import_hooks_fail_closed(self):
        api = self.fixture(head="fast")
        api.present(HEAD, "tests/__init__.py", "f" * 64)
        self.assertFalse(self.decision(api).accepted)
        api = self.fixture()
        api.present(HEAD, "scripts/__init__.py")
        self.assertFalse(self.decision(api).accepted)

    def test_self_modification_of_trusted_workflow_is_rejected(self):
        api = self.fixture()
        path = "ci/trusted_memory_admission.py"
        api.present(BASE, path, "9" * 64)
        api.present(HEAD, path, "8" * 64)
        self.assertTrue(any("trusted admission source" in x for x in self.decision(api).errors))

    def test_stale_head_or_base_event_never_passes(self):
        api = self.fixture(head="fast")
        api.pr["head"]["sha"] = NEXT_HEAD
        self.assertFalse(self.decision(api).accepted)
        api = self.fixture(head="fast")
        api.pr["base"]["sha"] = "0" * 40
        self.assertFalse(self.decision(api).accepted)

    def test_concurrent_head_update_rejects_stale_evidence(self):
        api = self.fixture(head="fast")
        api.change_head_on_second_read = True
        self.assertIn("stale evidence", " ".join(self.decision(api).errors))

    def test_wrong_base_branch_or_fork_fails_closed(self):
        api = self.fixture()
        api.pr["base"]["ref"] = "feature/unprotected"
        self.assertFalse(self.decision(api).accepted)
        api = self.fixture()
        api.pr["head"]["repo"]["full_name"] = "example/fork"
        self.assertFalse(self.decision(api).accepted)

    def test_exact_head_commit_status_emission_success_and_failure(self):
        url = "https://github.com/maksimp6/Chat/actions/runs/123"
        api = self.fixture(head="fast")
        state, result = gate.execute(api, self.policy, 1106, HEAD, BASE, url)
        self.assertEqual((state, result.accepted), ("success", True))
        self.assertEqual([status for _, status, _ in api.statuses], ["pending", "success"])
        api = self.fixture(head="fast")
        api.present(HEAD, "tests/test_ci_fast_contracts.py", "f" * 64)
        state, result = gate.execute(api, self.policy, 1106, HEAD, BASE, url)
        self.assertEqual((state, result.accepted), ("failure", False))
        self.assertEqual([status for _, status, _ in api.statuses], ["pending", "failure"])

    def test_api_failure_never_posts_success(self):
        api = self.fixture(head="fast")
        def fail(_number):
            raise gate.AdmissionError("GitHub unavailable")
        api.get_pr = fail
        state, result = gate.execute(api, self.policy, 1106, HEAD, BASE,
                                     "https://github.com/maksimp6/Chat/actions/runs/123")
        self.assertEqual((state, result), ("error", None))
        self.assertEqual([s for _, s, _ in api.statuses], ["pending", "error"])

    def test_real_client_checks_file_bytes_and_fixed_commit_sha(self):
        raw = b"accepted-test-bytes"
        payload = {
            "type": "file",
            "encoding": "base64",
            "content": base64.b64encode(raw).decode("ascii"),
            "size": len(raw),
            "sha": "d" * 40,
        }
        response = io.BytesIO(json.dumps(payload).encode("utf-8"))
        with patch.object(gate, "urlopen", return_value=response) as mocked:
            client = gate.GitHubAPI("not-a-real-token", REPO)
            result = client.file_at(HEAD, "tests/__init__.py")
        self.assertEqual(result.sha256, hashlib.sha256(raw).hexdigest())
        self.assertIn("?ref=" + HEAD, mocked.call_args.args[0].full_url)
        self.assertEqual(mocked.call_args.args[0].get_method(), "GET")

    def test_invalid_file_encoding_fails_closed(self):
        payload = {"type": "file", "encoding": "base64", "content": "!!?",
                   "size": 4, "sha": "d" * 40}
        with patch.object(gate, "urlopen", return_value=io.BytesIO(json.dumps(payload).encode())):
            with self.assertRaises(gate.AdmissionError):
                gate.GitHubAPI("not-a-real-token", REPO).file_at(HEAD, "tests/__init__.py")

    def test_trusted_workflow_never_checks_out_candidate_head(self):
        root = Path(__file__).resolve().parents[1]
        trusted = (root / ".github/workflows/trusted-memory-admission.yml").read_text()
        self.assertIn("pull_request_target:", trusted)
        self.assertIn("ref: ${{ github.event.pull_request.base.sha }}", trusted)
        self.assertNotIn("ref: ${{ github.event.pull_request.head.sha }}", trusted)
        self.assertIn("statuses: write", trusted)
        self.assertIn("python -I -S ci/trusted_memory_admission.py", trusted)
        selftest = (root / ".github/workflows/trusted-memory-selftest.yml").read_text()
        self.assertIn("pull_request:", selftest)
        self.assertNotIn("statuses: write", selftest)

    def test_file_digest_must_match_real_bytes(self):
        self.assertEqual(hashlib.sha256(b"").hexdigest(),
                         self.policy["pinned_sha256"]["tests/__init__.py"])
        self.assertFalse(gate.is_sha("a" * 39))
        self.assertFalse(gate.is_sha("z" * 40))


if __name__ == "__main__":
    unittest.main()
