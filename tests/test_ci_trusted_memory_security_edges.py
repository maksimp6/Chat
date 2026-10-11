"""Independent negative admission tests; preserve the original 18 fixtures."""

from __future__ import annotations

from pathlib import Path
import unittest

from scripts import trusted_memory_admission as gate
from tests.test_ci_trusted_memory_admission import (
    BASE,
    HEAD,
    NEXT_HEAD,
    REPO,
    FakeGitHub,
)


class TrustedAdmissionSecurityEdges(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.policy = gate.read_policy(
            Path(__file__).resolve().parents[1] / "ci/trusted_memory_policy.json"
        )

    def test_core_engine_removal_rejected_even_when_accepted_tests_remain(self) -> None:
        api = FakeGitHub(self.policy)
        api.seed(base_stage="core", head_stage="core")
        api.files.pop((HEAD, self.policy["core_marker"]))
        decision = gate.audit_pr(api, self.policy, 1106, HEAD, BASE)
        self.assertFalse(decision.accepted)
        self.assertTrue(any("core implementation missing" in x for x in decision.errors))

    def test_fast_to_legacy_reversion_cannot_pass(self) -> None:
        api = FakeGitHub(self.policy)
        api.seed(base_stage="fast", head_stage="legacy")
        decision = gate.audit_pr(api, self.policy, 1106, HEAD, BASE)
        self.assertEqual(decision.stage, "fast")
        self.assertFalse(decision.accepted)

    def test_trusted_control_removal_from_candidate_rejected(self) -> None:
        api = FakeGitHub(self.policy)
        api.seed()
        marker = "scripts/trusted_memory_admission.py"
        api.present(BASE, marker)
        decision = gate.audit_pr(api, self.policy, 1106, HEAD, BASE)
        self.assertFalse(decision.accepted)
        self.assertTrue(any("trusted admission source" in x for x in decision.errors))

    def test_head_or_base_race_cannot_post_success(self) -> None:
        api = FakeGitHub(self.policy)
        api.seed(base_stage="core", head_stage="core")
        api.change_head_on_second_read = True
        state, decision = gate.execute(
            api,
            self.policy,
            1106,
            HEAD,
            BASE,
            f"https://github.com/{REPO}/actions/runs/5",
        )
        self.assertEqual(state, "failure")
        self.assertIsNotNone(decision)
        self.assertEqual([entry[1] for entry in api.statuses], ["pending", "failure"])

    def test_bad_policy_rejected_before_publishing_success(self) -> None:
        with self.assertRaises(gate.AdmissionError):
            gate.GitHubAPI("", REPO)
        self.assertFalse(gate.is_sha(NEXT_HEAD + "0"))


if __name__ == "__main__":
    unittest.main()
