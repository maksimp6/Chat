#!/usr/bin/env python3
"""Read-only fail-closed merge readiness check for Alice Pro pull requests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from typing import Any

_ALLOWED_CONCLUSIONS = {"success", "neutral", "skipped"}


def _gh_json(args: list[str]) -> dict[str, Any]:
    command = ["gh", "api", *args]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(completed.stdout)


def _split_repo(repo: str) -> tuple[str, str]:
    if "/" not in repo:
        raise ValueError("repository must be in owner/name form")
    owner, name = repo.split("/", 1)
    if not owner or not name:
        raise ValueError("repository must be in owner/name form")
    return owner, name


def collect_snapshot(repo: str, pr_number: int, base_ref: str = "master") -> dict[str, Any]:
    owner, name = _split_repo(repo)
    pull = _gh_json([f"/repos/{repo}/pulls/{pr_number}"])
    head_sha = str(pull["head"]["sha"])

    compare = _gh_json([f"/repos/{repo}/compare/{base_ref}...{head_sha}"])
    checks = _gh_json(
        [
            "-H",
            "Accept: application/vnd.github+json",
            f"/repos/{repo}/commits/{head_sha}/check-runs?per_page=100",
        ]
    )

    query = """
    query($owner: String!, $name: String!, $number: Int!) {
      repository(owner: $owner, name: $name) {
        pullRequest(number: $number) {
          reviewThreads(first: 100) {
            nodes { isResolved }
            pageInfo { hasNextPage }
          }
        }
      }
    }
    """
    thread_data = _gh_json(
        [
            "graphql",
            "-f",
            f"query={query}",
            "-F",
            f"owner={owner}",
            "-F",
            f"name={name}",
            "-F",
            f"number={pr_number}",
        ]
    )
    threads = thread_data["data"]["repository"]["pullRequest"]["reviewThreads"]

    return {
        "repo": repo,
        "pr_number": pr_number,
        "base_ref": base_ref,
        "pr_base_ref": pull["base"]["ref"],
        "head_sha": head_sha,
        "draft": bool(pull["draft"]),
        "behind_by": int(compare["behind_by"]),
        "check_runs": checks.get("check_runs", []),
        "review_threads": threads.get("nodes", []),
        "review_threads_truncated": bool(threads.get("pageInfo", {}).get("hasNextPage")),
    }


def evaluate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []

    if snapshot["draft"]:
        blockers.append({"code": "draft", "detail": "pull request is draft"})

    if snapshot["pr_base_ref"] != snapshot["base_ref"]:
        blockers.append(
            {
                "code": "wrong_base",
                "detail": f"base is {snapshot['pr_base_ref']}, expected {snapshot['base_ref']}",
            }
        )

    behind_by = int(snapshot["behind_by"])
    if behind_by:
        blockers.append(
            {
                "code": "behind_master",
                "detail": f"head is behind {snapshot['base_ref']} by {behind_by} commit(s)",
            }
        )

    check_runs = snapshot["check_runs"]
    if not check_runs:
        blockers.append({"code": "checks_missing", "detail": "no check runs found on exact head"})
    else:
        for check in check_runs:
            name = str(check.get("name") or "<unnamed>")
            status = str(check.get("status") or "")
            conclusion = check.get("conclusion")
            if status != "completed":
                blockers.append(
                    {
                        "code": "check_pending",
                        "check": name,
                        "detail": f"{name}: status={status or 'unknown'}",
                    }
                )
            elif conclusion not in _ALLOWED_CONCLUSIONS:
                blockers.append(
                    {
                        "code": "check_failed",
                        "check": name,
                        "detail": f"{name}: conclusion={conclusion or 'missing'}",
                    }
                )

    unresolved = sum(
        1 for thread in snapshot["review_threads"] if not bool(thread.get("isResolved"))
    )
    if unresolved:
        blockers.append(
            {
                "code": "review_threads",
                "detail": f"{unresolved} unresolved review thread(s)",
            }
        )
    if snapshot.get("review_threads_truncated"):
        blockers.append(
            {
                "code": "review_threads_truncated",
                "detail": "review thread result exceeded one page; readiness cannot be proven",
            }
        )

    return {
        "ready": not blockers,
        "repo": snapshot["repo"],
        "pr_number": snapshot["pr_number"],
        "base_ref": snapshot["base_ref"],
        "head_sha": snapshot["head_sha"],
        "behind_by": behind_by,
        "blockers": blockers,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pr", type=int, default=os.environ.get("PR_NUMBER"))
    parser.add_argument("--base", default="master")
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    if args.pr is None:
        parser.error("--pr or PR_NUMBER is required")

    result = evaluate_snapshot(collect_snapshot(args.repo, int(args.pr), args.base))
    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
