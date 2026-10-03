"""Offline provisioning checks. These do not establish live mirror readiness."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap_business_docs.sh"

FAKE_TOOL = r'''
import json
import os
from pathlib import Path
import sys

name = Path(sys.argv[0]).name
args = sys.argv[1:]
state = Path(os.environ["TEST_STATE"])
scenario = os.environ.get("TEST_SCENARIO", "success")
with (state / "calls.jsonl").open("a") as log:
    log.write(json.dumps({"tool": name, "args": args,
                          "host": os.environ.get("GH_HOST")}) + "\n")

def fail(code=1):
    print("SENSITIVE_DIAGNOSTIC_CANARY", file=sys.stderr)
    sys.exit(code)

if name == "gh":
    if "user" in args:
        print("someone-else\t1" if scenario == "wrong-owner"
              else "maksimp6\t293531601")
    elif args[:2] == ["repo", "create"]:
        if scenario == "create-fails":
            fail()
        (state / "created").touch()
    elif "repos/maksimp6/Chat-business-docs" in args:
        existing = scenario.startswith("existing-")
        created = (state / "created").exists()
        if not existing and not created:
            print("gh: unavailable (HTTP 403)" if scenario == "forbidden"
                  else "gh: Not Found (HTTP 404)", file=sys.stderr)
            sys.exit(1)
        private = "false" if scenario in {"existing-public", "created-public"} else "true"
        fork = "true" if scenario == "existing-fork" else "false"
        branch = "main" if scenario in {"existing-main", "final-main"} else "master"
        if ".default_branch" in args[-1]:
            print(f"{private}\t{fork}\t{branch}")
        else:
            print(f"{private}\t{fork}")
    else:
        fail(90)
elif name == "git":
    if args[0] == "init":
        Path(args[-1]).mkdir()
    elif args[0] == "commit" and scenario == "signing-fails":
        fail()
    elif "push" in args and scenario == "push-fails":
        fail()
'''


class BusinessDocsPolicyTests(unittest.TestCase):
    def test_contract_disabled_and_private_by_default(self):
        policy = json.loads(
            (ROOT / "docs/business/mirror-policy.json").read_text(encoding="utf-8")
        )
        self.assertIs(policy["enabled"], False)
        self.assertEqual(policy["default_classification"], "private")
        self.assertEqual(policy["public_repository"], "maksimp6/Chat")
        self.assertEqual(policy["private_repository"], "maksimp6/Chat-business-docs")
        self.assertEqual(policy["base_branch"], "master")
        self.assertEqual(policy["mapping"]["public_prefix"], "docs/business/public/")
        self.assertEqual(policy["mapping"]["private_public_prefix"], "public/")
        self.assertEqual(policy["private_only_prefixes"], ["private/", ".mirror/"])
        for key in (
            "allow_deletions",
            "allow_reverts",
            "allow_force_push",
            "allow_private_history_export",
        ):
            self.assertIs(policy[key], False)
        self.assertEqual(policy["conflict_policy"], "block_entire_batch")
        self.assertEqual(
            policy["export"]["approval"], "owner_exact_content_before_publication"
        )
        self.assertEqual(policy["export"]["history"], "public_only")
        self.assertEqual(policy["export"]["write_mode"], "protected_pull_request")
        self.assertEqual(policy["orchestration"]["execution_repository"], "private")
        self.assertIs(
            policy["orchestration"]["private_credentials_in_public_ci"], False
        )

    def test_public_seed_is_identical_to_bootstrap_seed(self):
        script = SCRIPT.read_text(encoding="utf-8")
        seed = script.split("cat > public/README.md <<'MD'\n", 1)[1].split("\nMD\n", 1)[0]
        public = (ROOT / "docs/business/public/README.md").read_text(encoding="utf-8")
        self.assertEqual(seed + "\n", public)


@unittest.skipUnless(shutil.which("bash"), "bash required for provisioning contract")
class BusinessDocsBootstrapTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        bindir = self.state / "bin"
        bindir.mkdir()
        for tool in ("gh", "git"):
            executable = bindir / tool
            executable.write_text(f"#!{sys.executable} -S\n" + FAKE_TOOL, encoding="utf-8")
            executable.chmod(0o700)
        self.env = dict(os.environ)
        self.env.update(
            PATH=str(bindir) + os.pathsep + self.env.get("PATH", ""),
            TEST_STATE=str(self.state),
            TMPDIR=str(self.state),
            GH_HOST="unrelated.invalid",
        )
        # Fake tools never access network or authentication state.

    def run_script(self, scenario="success", *args):
        env = {**self.env, "TEST_SCENARIO": scenario}
        result = subprocess.run(
            [shutil.which("bash"), str(SCRIPT), *args],
            capture_output=True,
            text=True,
            env=env,
            timeout=15,
            check=False,
        )
        self.assertNotIn("SENSITIVE_DIAGNOSTIC_CANARY", result.stdout + result.stderr)
        return result

    def calls(self):
        log = self.state / "calls.jsonl"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def assert_no_remote_write(self):
        calls = self.calls()
        self.assertFalse(any(call["args"][:2] == ["repo", "create"] for call in calls))
        self.assertFalse(any("push" in call["args"] for call in calls))

    def test_default_check_is_read_only(self):
        result = self.run_script()
        self.assertEqual(result.returncode, 2)
        self.assertIn("provisioning NOT verified", result.stderr)
        self.assert_no_remote_write()

    def test_wrong_identity_cannot_create(self):
        result = self.run_script("wrong-owner", "--create")
        self.assertEqual(result.returncode, 2)
        self.assert_no_remote_write()

    def test_forbidden_is_not_treated_as_missing(self):
        result = self.run_script("forbidden", "--create")
        self.assertEqual(result.returncode, 2)
        self.assertIn("cannot inspect", result.stderr)
        self.assert_no_remote_write()

    def test_existing_private_is_idempotent(self):
        result = self.run_script("existing-private", "--create")
        self.assertEqual(result.returncode, 0)
        self.assertIn("unchanged", result.stdout)
        self.assertIn("NOT verified", result.stdout)
        self.assert_no_remote_write()

    def test_existing_public_is_rejected(self):
        result = self.run_script("existing-public", "--create")
        self.assertEqual(result.returncode, 2)
        self.assert_no_remote_write()

    def test_existing_fork_is_rejected(self):
        result = self.run_script("existing-fork", "--create")
        self.assertEqual(result.returncode, 2)
        self.assert_no_remote_write()

    def test_existing_wrong_branch_is_not_renamed(self):
        result = self.run_script("existing-main", "--create")
        self.assertEqual(result.returncode, 2)
        self.assert_no_remote_write()

    def test_signing_failure_precedes_remote_creation(self):
        result = self.run_script("signing-fails", "--create")
        self.assertEqual(result.returncode, 2)
        self.assertIn("remote NOT created", result.stderr)
        self.assert_no_remote_write()

    def test_create_failure_does_not_push(self):
        result = self.run_script("create-fails", "--create")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(any("push" in call["args"] for call in self.calls()))

    def test_public_target_detected_before_push(self):
        result = self.run_script("created-public", "--create")
        self.assertEqual(result.returncode, 2)
        self.assertFalse(any("push" in call["args"] for call in self.calls()))

    def test_partial_push_failure_not_reported_as_complete(self):
        result = self.run_script("push-fails", "--create")
        self.assertEqual(result.returncode, 2)
        self.assertIn("may exist empty", result.stderr)
        self.assertNotIn("PROVISIONED:", result.stdout)

    def test_final_metadata_must_match(self):
        result = self.run_script("final-main", "--create")
        self.assertEqual(result.returncode, 2)
        self.assertIn("final private/master verification failed", result.stderr)

    def test_create_private_signed_master_without_sync_claim(self):
        result = self.run_script("success", "--create")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("automatic mirror NOT configured", result.stdout)
        calls = self.calls()
        create = next(i for i, call in enumerate(calls) if call["args"][:2] == ["repo", "create"])
        commit = next(i for i, call in enumerate(calls) if call["args"][0] == "commit")
        self.assertLess(commit, create)
        self.assertIn("--private", calls[create]["args"])
        self.assertTrue(all(call["host"] == "github.com" for call in calls))
        self.assertTrue(any(call["args"] == ["config", "commit.gpgsign", "true"] for call in calls))
        pushes = [call for call in calls if "push" in call["args"]]
        self.assertEqual(len(pushes), 1)
        self.assertEqual(pushes[0]["args"][-3:], ["push", "origin", "master"])
        self.assertFalse(any("--force" in call["args"] or "--mirror" in call["args"] for call in calls))
        self.assertTrue(any(call["args"] == ["remote", "add", "origin", "https://github.com/maksimp6/Chat-business-docs.git"] for call in calls))
        second = self.run_script("success", "--create")
        self.assertEqual(second.returncode, 0)
        self.assertIn("unchanged", second.stdout)
        self.assertEqual(sum(call["args"][:2] == ["repo", "create"] for call in self.calls()), 1)

    def test_invalid_mode_does_not_contact_github(self):
        result = self.run_script("success", "--force")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(self.calls(), [])


if __name__ == "__main__":
    unittest.main()
