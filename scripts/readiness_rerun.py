#!/usr/bin/env python3
"""Validate and optionally rerun an existing exact-head readiness job."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from typing import Any


def github_json(repo: str, suffix: str) -> dict[str, Any]:
    result = subprocess.run(
        ["gh", "api", f"repos/{repo}/{suffix}"], check=True, capture_output=True, text=True
    )
    return json.loads(result.stdout)


def validate_pull(pull: dict[str, Any], repo: str, head: str) -> None:
    if (
        pull.get("state") != "open"
        or pull.get("draft") is not False
        or pull.get("merged") is not False
        or pull.get("base", {}).get("ref") != "master"
        or pull.get("head", {}).get("sha") != head
        or pull.get("head", {}).get("repo", {}).get("full_name") != repo
        or pull.get("base", {}).get("repo", {}).get("full_name") != repo
    ):
        raise ValueError("PR must be open, non-draft, same-repository master PR at expected head")


def prepare_rerun(repo: str, pr: int, run_id: int, head: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*", repo):
        raise ValueError("invalid repository")
    if pr <= 0 or run_id <= 0 or not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ValueError("positive IDs and a full lowercase head SHA are required")
    pull = github_json(repo, f"pulls/{pr}")
    validate_pull(pull, repo, head)
    run = github_json(repo, f"actions/runs/{run_id}")
    if (
        run.get("path") != ".github/workflows/merge-readiness.yml"
        or run.get("event") != "pull_request"
        or run.get("head_sha") != head
        or run.get("status") != "completed"
        or run.get("repository", {}).get("full_name") != repo
        or not any(item.get("number") == pr for item in run.get("pull_requests", []))
    ):
        raise ValueError("run is not a completed readiness PR run bound to expected PR/head")
    jobs = github_json(repo, f"actions/runs/{run_id}/jobs?filter=latest&per_page=100")
    entries = jobs.get("jobs", [])
    if jobs.get("total_count") != len(entries):
        raise ValueError("incomplete job collection")
    matches = [job for job in entries if job.get("name") == "Merge readiness snapshot"]
    if len(matches) != 1:
        raise ValueError("exactly one readiness job is required")
    job = matches[0]
    job_id = job.get("id")
    check_url = job.get("check_run_url", "")
    match = re.fullmatch(
        r"https://api\.github\.com/repos/" + re.escape(repo) + r"/check-runs/([0-9]+)",
        check_url,
    )
    if (
        type(job_id) is not int
        or job_id <= 0
        or job.get("run_id") != run_id
        or job.get("status") != "completed"
        or job.get("conclusion") not in {"failure", "cancelled", "timed_out"}
        or not match
    ):
        raise ValueError("only a completed failed readiness job can be retried")
    check = github_json(repo, f"check-runs/{match[1]}")
    if (
        check.get("head_sha") != head
        or check.get("name") != "Merge readiness snapshot"
        or check.get("app", {}).get("id") != 15368
    ):
        raise ValueError("check producer or head binding mismatch")
    latest = github_json(repo, f"pulls/{pr}")
    validate_pull(latest, repo, head)
    if latest["base"].get("sha") != pull["base"].get("sha"):
        raise ValueError("PR base changed during collection")
    return {
        "repo": repo,
        "pr": pr,
        "head_sha": head,
        "base_sha": latest["base"].get("sha"),
        "job_id": job_id,
        "endpoint": f"repos/{repo}/actions/jobs/{job_id}/rerun",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="maksimp6/Chat")
    parser.add_argument("--pr", required=True, type=int)
    parser.add_argument("--run", required=True, type=int)
    parser.add_argument("--head", required=True)
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args()
    plan = prepare_rerun(args.repo, args.pr, args.run, args.head)
    if args.execute:
        # Recollect immediately before mutation; no force/bypass or new dispatch ref.
        fresh = prepare_rerun(args.repo, args.pr, args.run, args.head)
        if fresh != plan:
            raise ValueError("rerun target changed before mutation")
        subprocess.run(["gh", "api", "--method", "POST", plan["endpoint"]], check=True)
    print(json.dumps({**plan, "rerun_requested": args.execute}, sort_keys=True))


if __name__ == "__main__":
    main()
