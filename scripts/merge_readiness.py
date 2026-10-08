#!/usr/bin/env python3
"""Read-only fail-closed merge readiness check for Alice Pro pull requests."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any

_ALLOWED_CONCLUSIONS = {"success", "neutral", "skipped"}
# These control jobs prove routing and selected-platform execution. Unlike a
# non-selected platform job, skipping them cannot be evidence of success.
_CHECK_CONCLUSIONS = {
    "Platform changes": {"success"},
    "CI required": {"success"},
}


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


def collect_snapshot(
    repo: str,
    pr_number: int,
    base_ref: str = "master",
    required_checks: list[str] | None = None,
) -> dict[str, Any]:
    owner, name = _split_repo(repo)
    pull = _gh_json([f"/repos/{repo}/pulls/{pr_number}"])
    head_sha = str(pull["head"]["sha"])
    base_sha = str(pull["base"]["sha"])

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
    pull_review_data = thread_data["data"]["repository"]["pullRequest"]
    threads = pull_review_data["reviewThreads"]

    final_pull = _gh_json([f"/repos/{repo}/pulls/{pr_number}"])
    final_head_sha = str(final_pull["head"]["sha"])
    final_base_sha = str(final_pull["base"]["sha"])

    return {
        "repo": repo,
        "pr_number": pr_number,
        "base_ref": base_ref,
        "pr_base_ref": pull["base"]["ref"],
        "head_sha": head_sha,
        "base_sha": base_sha,
        "final_head_sha": final_head_sha,
        "final_base_sha": final_base_sha,
        "snapshot_changed": final_head_sha != head_sha or final_base_sha != base_sha,
        "pr_state": str(final_pull.get("state") or ""),
        "merged": bool(final_pull.get("merged")),
        "draft": bool(final_pull["draft"]),
        "behind_by": int(compare["behind_by"]),
        "check_runs": checks.get("check_runs", []),
        "required_checks": list(dict.fromkeys(required_checks or [])),
        "solution_review": collect_solution_review_evidence(
            repo,
            pr_number,
            head_sha,
            base_sha,
        ),
        "review_threads": threads.get("nodes", []),
        "review_threads_truncated": bool(threads.get("pageInfo", {}).get("hasNextPage")),
    }


def _download_solution_review_artifact(repo: str, artifact_id: int) -> dict[str, Any] | None:
    """Download one bounded Actions artifact and parse solution-review.json."""
    with tempfile.TemporaryDirectory() as temp_dir:
        archive = Path(temp_dir) / "artifact.zip"
        subprocess.run(
            [
                "gh",
                "api",
                f"/repos/{repo}/actions/artifacts/{artifact_id}/zip",
                "--output",
                str(archive),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        if archive.stat().st_size > 1_000_000:
            return None
        with zipfile.ZipFile(archive) as bundle:
            names = bundle.namelist()
            if names != ["solution-review.json"]:
                return None
            info = bundle.getinfo("solution-review.json")
            if info.file_size > 64_000:
                return None
            payload = json.loads(bundle.read(info).decode("utf-8"))
    return payload if isinstance(payload, dict) else None


def collect_solution_review_evidence(
    repo: str,
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> dict[str, Any] | None:
    """Collect trusted evidence from successful solution-review workflow runs."""
    run_data = _gh_json(
        [
            f"/repos/{repo}/actions/workflows/solution-review.yml/runs"
            "?event=workflow_dispatch&per_page=100"
        ]
    )
    runs = run_data.get("workflow_runs", [])
    artifacts_by_run: dict[int, list[dict[str, Any]]] = {}
    for run in runs:
        run_id = int(run.get("id") or 0)
        if (
            not run_id
            or str(run.get("event") or "") != "workflow_dispatch"
            or str(run.get("head_branch") or "") != "master"
            or str(run.get("status") or "") != "completed"
            or str(run.get("conclusion") or "") != "success"
        ):
            continue
        artifact_data = _gh_json(
            [f"/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100"]
        )
        enriched: list[dict[str, Any]] = []
        for artifact in artifact_data.get("artifacts", []):
            item = dict(artifact)
            artifact_id = int(item.get("id") or 0)
            if artifact_id and not item.get("expired"):
                item["evidence"] = _download_solution_review_artifact(repo, artifact_id)
            enriched.append(item)
        artifacts_by_run[run_id] = enriched
    return select_solution_review_evidence(
        runs,
        artifacts_by_run,
        pr_number,
        head_sha,
        base_sha,
    )


def select_solution_review_evidence(
    runs: list[dict[str, Any]],
    artifacts_by_run: dict[int, list[dict[str, Any]]],
    pr_number: int,
    head_sha: str,
    base_sha: str,
) -> dict[str, Any] | None:
    """Select evidence issued by a successful exact-head solution-review run."""
    expected_name = f"solution-review-pr-{pr_number}-{head_sha}"
    candidates = sorted(runs, key=lambda item: int(item.get("id") or 0), reverse=True)
    for run in candidates:
        run_id = int(run.get("id") or 0)
        if (
            str(run.get("head_sha") or "") != head_sha
            or str(run.get("status") or "") != "completed"
            or str(run.get("conclusion") or "") != "success"
        ):
            continue
        for artifact in artifacts_by_run.get(run_id, []):
            if artifact.get("expired") or str(artifact.get("name") or "") != expected_name:
                continue
            evidence = artifact.get("evidence")
            if not isinstance(evidence, dict):
                continue
            if (
                evidence.get("schema_version") != 2
                or str(evidence.get("task") or "") != f"pr:{pr_number}"
                or str(evidence.get("reviewed_head_sha") or "") != head_sha
                or str(evidence.get("reviewed_base_sha") or "") != base_sha
                or int(evidence.get("workflow_run_id") or 0) != run_id
                or evidence.get("verification") != "hmac-sha256-verified"
                or evidence.get("review_report_sha256") is None
                or not isinstance(evidence.get("review_report"), dict)
            ):
                continue
            return evidence
    return None


def _solution_review_blocker(snapshot: dict[str, Any]) -> dict[str, Any] | None:
    evidence = snapshot.get("solution_review")
    if not isinstance(evidence, dict):
        return {
            "code": "solution_review_missing",
            "detail": "trusted solution-review evidence is missing",
        }

    required = {
        "task",
        "outcome",
        "reviewed_head_sha",
        "reviewed_base_sha",
        "reviewer_role",
        "reviewer_session",
        "implementation_role",
        "implementation_session",
        "provenance",
        "review_report_sha256",
        "verification",
    }
    if any(not str(evidence.get(field) or "").strip() for field in required):
        return {
            "code": "solution_review_invalid",
            "detail": "solution-review evidence is incomplete",
        }
    if evidence.get("schema_version") != 2:
        return {
            "code": "solution_review_invalid",
            "detail": "solution-review evidence schema version is unsupported",
        }

    report = evidence.get("review_report")
    if (
        evidence.get("verification") != "hmac-sha256-verified"
        or not isinstance(report, dict)
        or report.get("outcome") != evidence.get("outcome")
        or not isinstance(report.get("reviewed_files"), list)
        or not report["reviewed_files"]
        or not str(report.get("rationale") or "").strip()
        or not str(report.get("risk_assessment") or "").strip()
    ):
        return {"code": "solution_review_invalid", "detail": "authenticated substantive review report is missing"}

    outcome = str(evidence["outcome"])
    if outcome == "CHANGES_REQUESTED":
        return {
            "code": "solution_review_changes_requested",
            "detail": "solution reviewer requested implementation changes",
        }
    if outcome == "BLOCKED":
        return {
            "code": "solution_review_blocked",
            "detail": "solution review is blocked",
        }
    if outcome != "ACCEPTED":
        return {
            "code": "solution_review_invalid",
            "detail": "solution-review outcome is not recognized",
        }

    if (
        str(evidence["reviewed_head_sha"]) != str(snapshot["head_sha"])
        or str(evidence["reviewed_base_sha"]) != str(snapshot["base_sha"])
    ):
        return {
            "code": "solution_review_stale",
            "detail": "solution review is not bound to the exact current head and base",
        }

    if (
        str(evidence["reviewer_role"]) == str(evidence["implementation_role"])
        or str(evidence["reviewer_session"]) == str(evidence["implementation_session"])
    ):
        return {
            "code": "solution_review_not_independent",
            "detail": "solution reviewer role and session must be independent from implementation",
        }

    return None


def evaluate_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    blockers: list[dict[str, Any]] = []

    if snapshot.get("pr_state") != "open" or bool(snapshot.get("merged")):
        blockers.append(
            {
                "code": "pull_request_not_open",
                "detail": "pull request must be open and unmerged",
            }
        )

    if snapshot["draft"]:
        blockers.append({"code": "draft", "detail": "pull request is draft"})

    if snapshot["pr_base_ref"] != snapshot["base_ref"]:
        blockers.append(
            {
                "code": "wrong_base",
                "detail": f"base is {snapshot['pr_base_ref']}, expected {snapshot['base_ref']}",
            }
        )

    if snapshot.get("snapshot_changed"):
        blockers.append(
            {
                "code": "snapshot_changed",
                "detail": "pull request head or base changed while readiness was being collected",
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
    required_checks = [str(name) for name in snapshot.get("required_checks", []) if str(name)]
    if not required_checks:
        blockers.append(
            {
                "code": "required_checks_unconfigured",
                "detail": "required check names were not configured; readiness cannot be proven",
            }
        )
    else:
        for required_name in required_checks:
            matches = [
                check for check in check_runs if str(check.get("name") or "") == required_name
            ]
            if not matches:
                blockers.append(
                    {
                        "code": "required_check_missing",
                        "check": required_name,
                        "detail": f"{required_name}: no check run found on exact head",
                    }
                )
                continue

            check = max(matches, key=lambda item: int(item.get("id") or 0))
            status = str(check.get("status") or "")
            conclusion = check.get("conclusion")
            if status != "completed":
                blockers.append(
                    {
                        "code": "check_pending",
                        "check": required_name,
                        "detail": f"{required_name}: status={status or 'unknown'}",
                    }
                )
            elif conclusion not in _CHECK_CONCLUSIONS.get(required_name, _ALLOWED_CONCLUSIONS):
                blockers.append(
                    {
                        "code": "check_failed",
                        "check": required_name,
                        "detail": f"{required_name}: conclusion={conclusion or 'missing'}",
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

    review_blocker = _solution_review_blocker(snapshot)
    if review_blocker is not None:
        blockers.append(review_blocker)

    return {
        "ready": not blockers,
        "repo": snapshot["repo"],
        "pr_number": snapshot["pr_number"],
        "base_ref": snapshot["base_ref"],
        "head_sha": snapshot["head_sha"],
        "behind_by": behind_by,
        "blockers": blockers,
    }


def _collection_error_result(
    repo: str, pr_number: int, base_ref: str, exc: Exception
) -> dict[str, Any]:
    return {
        "ready": False,
        "repo": repo,
        "pr_number": pr_number,
        "base_ref": base_ref,
        "head_sha": None,
        "behind_by": None,
        "blockers": [
            {
                "code": "collection_error",
                "detail": f"merge readiness snapshot collection failed ({type(exc).__name__})",
            }
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    parser.add_argument("--pr", type=int, default=os.environ.get("PR_NUMBER"))
    parser.add_argument("--base", default="master")
    parser.add_argument(
        "--required-check",
        action="append",
        default=None,
        help="exact required check-run name; repeat for each required check",
    )
    parser.add_argument("--pretty", action="store_true")
    args = parser.parse_args()

    if not args.repo:
        parser.error("--repo or GITHUB_REPOSITORY is required")
    if args.pr is None:
        parser.error("--pr or PR_NUMBER is required")

    required_checks = args.required_check
    if required_checks is None:
        raw_required_checks = os.environ.get("MERGE_REQUIRED_CHECKS", "")
        required_checks = [name.strip() for name in raw_required_checks.split(",") if name.strip()]

    try:
        snapshot = collect_snapshot(
            args.repo,
            int(args.pr),
            args.base,
            required_checks=required_checks,
        )
        result = evaluate_snapshot(snapshot)
    except Exception as exc:
        result = _collection_error_result(args.repo, int(args.pr), args.base, exc)

    print(json.dumps(result, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if result["ready"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
