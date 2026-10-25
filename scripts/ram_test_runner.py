#!/usr/bin/env python3
"""Memory-only unittest runner with GitHub commit-status persistence.

No cache files, bytecode or coverage files are written by this runner.
Test code itself may write files. Only Python unittest-style tests are supported.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request

CONTEXT = "logos/ram-tests-v1"
ROOT = Path(__file__).resolve().parent.parent


def github_job():
    keys = ("GITHUB_RUN_ID", "RUNNER_NAME", "RUNNER_TEMP", "GITHUB_REPOSITORY", "GITHUB_SHA")
    return (os.environ.get("GITHUB_ACTIONS") == "true"
            and os.environ.get("CI") == "true"
            and all(os.environ.get(k) for k in keys))


def git_paths():
    command = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z", "--cached"],
        capture_output=True, check=True,
    )
    return sorted(p.decode("utf-8", "surrogateescape") for p in command.stdout.split(b"\0") if p)


def fingerprint(paths):
    h = hashlib.sha256()
    h.update(("ram-tests-v1|" + sys.version + "|" + sys.platform).encode())
    for name in paths:
        if not (name.endswith((".py", ".toml", ".ini", ".cfg", ".sh", ".yml", ".yaml", ".json")) or name in ("AGENTS.md", "requirements.txt")):
            continue
        path = ROOT / name
        if not path.is_file():
            continue
        # Include all tracked files, not just tests: conservative dependency invalidation.
        h.update(name.encode("utf-8", "surrogateescape") + b"\0")
        with path.open("rb") as source:
            while block := source.read(131072):
                h.update(block)
    return h.hexdigest()


def api(method, endpoint, token, payload=None):
    base = "https://api.github.com"
    body = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(
        base + endpoint, data=body, method=method,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "logos-ram-tests",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urllib.request.urlopen(req, timeout=5) as response:
        return json.load(response)


def previous_pass(repo, sha, token, digest):
    items = api("GET", f"/repos/{repo}/commits/{sha}/statuses?per_page=100", token)
    for item in items:
        if item.get("context") == CONTEXT:
            return item.get("state") == "success" and item.get("description") == "PASS:" + digest
    return False


def save_pass(repo, sha, token, digest):
    return api("POST", f"/repos/{repo}/statuses/{sha}", token, {
        "state": "success",
        "context": CONTEXT,
        "description": "PASS:" + digest,
    })


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="rerun even after a cached pass")
    parser.add_argument("--deadline", type=float, default=3.0,
                        help="performance goal in seconds; never abort or misreport tests")
    args = parser.parse_args()
    start = time.perf_counter()
    if not github_job():
        print("LOCAL_TESTS_FORBIDDEN: run inside a GitHub Actions job", file=sys.stderr)
        return 2
    # Tests that import this package require these marker variables.
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    token = os.environ.get("GITHUB_TOKEN", "")
    if not token:
        print("CACHE_UNAVAILABLE: GITHUB_TOKEN is required; cannot persist results", file=sys.stderr)
        return 2
    try:
        digest = fingerprint(git_paths())
        repo = os.environ["GITHUB_REPOSITORY"]
        sha = os.environ["GITHUB_SHA"]
        if not args.force:
            commits = subprocess.run(
                ["git", "-C", str(ROOT), "rev-list", "-n", "12", "HEAD"],
                capture_output=True, text=True, check=True,
            ).stdout.splitlines()
            for candidate in dict.fromkeys([sha] + commits):
                if previous_pass(repo, candidate, token, digest):
                    print(json.dumps({"status": "CACHED_PASS", "digest": digest,
                                      "executed": 0, "source_commit": candidate}))
                    return 0
    except (OSError, subprocess.CalledProcessError, urllib.error.URLError, ValueError) as exc:
        print("CACHE_READ_ERROR (fail closed): " + str(exc), file=sys.stderr)
        return 2
    try:
        suite = unittest.defaultTestLoader.discover(
            start_dir=str(ROOT / "tests"), pattern="test_*.py", top_level_dir=str(ROOT))
        count = suite.countTestCases()
        if count == 0:
            print("EMPTY_SUITE: refusing PASS", file=sys.stderr)
            return 2
        result = unittest.TextTestRunner(verbosity=1, stream=sys.stderr).run(suite)
    except (Exception, SystemExit) as exc:
        print("DISCOVERY_ERROR: " + repr(exc), file=sys.stderr)
        return 2
    elapsed = time.perf_counter() - start
    if not result.wasSuccessful():
        print(json.dumps({"status": "FAIL", "tests": count, "elapsed_sec": round(elapsed, 3)}))
        return 1
    try:
        save_pass(repo, sha, token, digest)
    except (OSError, urllib.error.URLError, ValueError) as exc:
        print("CACHE_WRITE_ERROR (not cached): " + str(exc), file=sys.stderr)
        return 2
    print(json.dumps({"status": "PASS", "tests": count, "elapsed_sec": round(elapsed, 3),
                      "goal_met": elapsed <= args.deadline, "digest": digest}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
