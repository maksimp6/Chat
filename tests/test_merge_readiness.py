import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest
import yaml


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts" / "merge_readiness.py"

spec = importlib.util.spec_from_file_location("merge_readiness", MODULE_PATH)
merge_readiness = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(merge_readiness)


def snapshot(**overrides):
    value = {
        "repo": "maksimp6/Chat",
        "pr_number": 123,
        "base_ref": "master",
        "pr_base_ref": "master",
        "head_sha": "abc123",
        "base_sha": "base123",
        "final_head_sha": "abc123",
        "final_base_sha": "base123",
        "snapshot_changed": False,
        "pr_state": "open",
        "merged": False,
        "draft": False,
        "behind_by": 0,
        "required_checks": ["Application tests", "PostgreSQL integration"],
        "check_runs": [
            {
                "id": 10,
                "name": "Application tests",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 11,
                "name": "PostgreSQL integration",
                "status": "completed",
                "conclusion": "success",
            },
            {
                "id": 12,
                "name": "Optional preview",
                "status": "completed",
                "conclusion": "skipped",
            },
        ],
        "solution_review": {
            "schema_version": 2,
            "task": "pr:123",
            "outcome": "ACCEPTED",
            "reviewed_head_sha": "abc123",
            "reviewed_base_sha": "base123",
            "reviewer_role": "team-lead",
            "reviewer_session": "review-session-1",
            "implementation_role": "backend-engineer",
            "implementation_session": "implementation-session-1",
            "provenance": "github-actions:solution-review",
            "verification": "hmac-sha256-verified",
            "review_report_sha256": "a" * 64,
            "review_report": {
                "outcome": "ACCEPTED",
                "reviewed_files": ["scripts/merge_readiness.py"],
                "rationale": "Detailed review",
                "risk_assessment": "No residual critical risk",
            },
        },
        "review_threads": [{"isResolved": True}],
        "review_threads_truncated": False,
    }
    value.update(overrides)
    return value


def blocker_codes(result):
    return {item["code"] for item in result["blockers"]}


def test_merge_readiness_accepts_only_fully_ready_exact_head():
    result = merge_readiness.evaluate_snapshot(snapshot())

    assert result["ready"] is True
    assert result["head_sha"] == "abc123"
    assert result["blockers"] == []


def test_merge_readiness_rejects_closed_merged_or_changed_snapshot():
    closed = merge_readiness.evaluate_snapshot(snapshot(pr_state="closed"))
    merged = merge_readiness.evaluate_snapshot(snapshot(pr_state="closed", merged=True))
    changed = merge_readiness.evaluate_snapshot(
        snapshot(final_head_sha="new-head", snapshot_changed=True)
    )

    assert blocker_codes(closed) == {"pull_request_not_open"}
    assert blocker_codes(merged) == {"pull_request_not_open"}
    assert blocker_codes(changed) == {"snapshot_changed"}


def test_merge_readiness_fails_closed_for_draft_stale_pending_and_threads():
    result = merge_readiness.evaluate_snapshot(
        snapshot(
            draft=True,
            behind_by=2,
            check_runs=[
                {
                    "id": 20,
                    "name": "Application tests",
                    "status": "in_progress",
                    "conclusion": None,
                },
                {
                    "id": 21,
                    "name": "PostgreSQL integration",
                    "status": "completed",
                    "conclusion": "success",
                },
            ],
            review_threads=[{"isResolved": False}],
        )
    )

    assert result["ready"] is False
    assert blocker_codes(result) == {
        "draft",
        "behind_master",
        "check_pending",
        "review_threads",
    }


def test_merge_readiness_rejects_failed_or_missing_required_checks():
    failed = merge_readiness.evaluate_snapshot(
        snapshot(
            check_runs=[
                {
                    "id": 30,
                    "name": "Application tests",
                    "status": "completed",
                    "conclusion": "failure",
                },
                {
                    "id": 31,
                    "name": "PostgreSQL integration",
                    "status": "completed",
                    "conclusion": "success",
                },
            ]
        )
    )
    missing = merge_readiness.evaluate_snapshot(
        snapshot(
            check_runs=[
                {
                    "id": 32,
                    "name": "Application tests",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        )
    )

    assert blocker_codes(failed) == {"check_failed"}
    assert blocker_codes(missing) == {"required_check_missing"}


def test_merge_readiness_rejects_unconfigured_required_checks():
    result = merge_readiness.evaluate_snapshot(snapshot(required_checks=[]))

    assert blocker_codes(result) == {"required_checks_unconfigured"}


def test_merge_readiness_rejects_wrong_base_and_truncated_threads():
    result = merge_readiness.evaluate_snapshot(
        snapshot(pr_base_ref="release", review_threads_truncated=True)
    )

    assert blocker_codes(result) == {"wrong_base", "review_threads_truncated"}


def test_merge_readiness_ignores_non_required_failed_checks():
    result = merge_readiness.evaluate_snapshot(
        snapshot(
            check_runs=[
                {
                    "id": 40,
                    "name": "Application tests",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 41,
                    "name": "PostgreSQL integration",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "id": 42,
                    "name": "Optional preview",
                    "status": "completed",
                    "conclusion": "failure",
                },
            ]
        )
    )

    assert result["ready"] is True


def test_merge_readiness_uses_latest_attempt_for_required_check():
    result = merge_readiness.evaluate_snapshot(
        snapshot(
            required_checks=["Application tests"],
            check_runs=[
                {
                    "id": 50,
                    "name": "Application tests",
                    "status": "completed",
                    "conclusion": "failure",
                },
                {
                    "id": 51,
                    "name": "Application tests",
                    "status": "completed",
                    "conclusion": "success",
                },
            ],
        )
    )

    assert result["ready"] is True


def test_collect_snapshot_uses_exact_head_and_propagates_thread_truncation(monkeypatch):
    calls = []
    pulls = iter(
        [
            {
                "head": {"sha": "head-1"},
                "base": {"ref": "master", "sha": "base-1"},
                "state": "open",
                "merged": False,
                "draft": False,
            },
            {
                "head": {"sha": "head-1"},
                "base": {"ref": "master", "sha": "base-1"},
                "state": "open",
                "merged": False,
                "draft": False,
            },
        ]
    )

    def fake_gh_json(args):
        calls.append(args)
        joined = " ".join(args)
        if "/pulls/123" in joined:
            return next(pulls)
        if "/compare/master...head-1" in joined:
            return {"behind_by": 0}
        if "/commits/head-1/check-runs?per_page=100" in joined:
            return {
                "check_runs": [
                    {
                        "id": 1,
                        "name": "Application tests",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]
            }
        if "/actions/workflows/solution-review.yml/runs" in joined:
            return {"workflow_runs": []}
        if args and args[0] == "graphql":
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [{"isResolved": True}],
                                "pageInfo": {"hasNextPage": True},
                            },
                        }
                    }
                }
            }
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(merge_readiness, "_gh_json", fake_gh_json)

    result = merge_readiness.collect_snapshot(
        "maksimp6/Chat",
        123,
        required_checks=["Application tests"],
    )

    assert result["head_sha"] == "head-1"
    assert result["base_sha"] == "base-1"
    assert result["snapshot_changed"] is False
    assert result["review_threads_truncated"] is True
    assert any("/compare/master...head-1" in " ".join(call) for call in calls)
    assert any("/commits/head-1/check-runs?per_page=100" in " ".join(call) for call in calls)


def test_collect_snapshot_marks_changed_head_or_base(monkeypatch):
    pulls = iter(
        [
            {
                "head": {"sha": "head-1"},
                "base": {"ref": "master", "sha": "base-1"},
                "state": "open",
                "merged": False,
                "draft": False,
            },
            {
                "head": {"sha": "head-2"},
                "base": {"ref": "master", "sha": "base-2"},
                "state": "open",
                "merged": False,
                "draft": False,
            },
        ]
    )

    def fake_gh_json(args):
        joined = " ".join(args)
        if "/pulls/123" in joined:
            return next(pulls)
        if "/compare/master...head-1" in joined:
            return {"behind_by": 0}
        if "/commits/head-1/check-runs?per_page=100" in joined:
            return {"check_runs": []}
        if "/actions/workflows/solution-review.yml/runs" in joined:
            return {"workflow_runs": []}
        if args and args[0] == "graphql":
            return {
                "data": {
                    "repository": {
                        "pullRequest": {
                            "reviewThreads": {
                                "nodes": [],
                                "pageInfo": {"hasNextPage": False},
                            },
                        }
                    }
                }
            }
        raise AssertionError(f"unexpected gh call: {args}")

    monkeypatch.setattr(merge_readiness, "_gh_json", fake_gh_json)

    result = merge_readiness.collect_snapshot("maksimp6/Chat", 123, required_checks=["CI"])

    assert result["snapshot_changed"] is True
    assert blocker_codes(merge_readiness.evaluate_snapshot(result)) >= {"snapshot_changed"}


def test_main_emits_structured_collection_error(monkeypatch, capsys):
    monkeypatch.setattr(
        merge_readiness,
        "collect_snapshot",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret provider output")),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "merge_readiness.py",
            "--repo",
            "maksimp6/Chat",
            "--pr",
            "123",
            "--required-check",
            "Application tests",
        ],
    )

    assert merge_readiness.main() == 1
    payload = json.loads(capsys.readouterr().out)

    assert payload["ready"] is False
    assert blocker_codes(payload) == {"collection_error"}
    assert "secret provider output" not in payload["blockers"][0]["detail"]
















def test_merge_readiness_rejects_missing_solution_review_evidence():
    result = merge_readiness.evaluate_snapshot(snapshot(solution_review=None))

    assert result["ready"] is False
    assert blocker_codes(result) == {"solution_review_missing"}


@pytest.mark.parametrize(
    ("override", "expected"),
    [
        ({"outcome": "CHANGES_REQUESTED"}, "solution_review_changes_requested"),
        ({"outcome": "BLOCKED"}, "solution_review_blocked"),
        ({"outcome": "LGTM"}, "solution_review_invalid"),
        ({"reviewed_head_sha": "old-head"}, "solution_review_stale"),
        ({"reviewed_base_sha": "old-base"}, "solution_review_stale"),
        ({"reviewer_role": "backend-engineer"}, "solution_review_not_independent"),
        (
            {"reviewer_session": "implementation-session-1"},
            "solution_review_not_independent",
        ),
        ({"provenance": ""}, "solution_review_invalid"),
        ({"task": ""}, "solution_review_invalid"),
    ],
)
def test_merge_readiness_rejects_invalid_solution_review_evidence(override, expected):
    evidence = dict(snapshot()["solution_review"])
    evidence.update(override)
    if "outcome" in override:
        # The authenticated report must agree with the evidence outcome.
        evidence["review_report"] = {
            **evidence["review_report"],
            "outcome": override["outcome"],
        }
    result = merge_readiness.evaluate_snapshot(snapshot(solution_review=evidence))

    assert result["ready"] is False
    assert blocker_codes(result) == {expected}


def test_merge_readiness_accepts_independent_role_session_on_same_github_owner():
    evidence = dict(snapshot()["solution_review"])
    evidence.update(
        reviewer_role="team-lead",
        reviewer_session="review-session-2",
        implementation_role="backend-engineer",
        implementation_session="implementation-session-1",
    )

    result = merge_readiness.evaluate_snapshot(snapshot(solution_review=evidence))

    assert result["ready"] is True
    assert result["blockers"] == []


def test_owner_github_identity_is_not_used_as_solution_review_independence():
    value = snapshot(
        solution_review=None,
        reviews=[
            {
                "state": "APPROVED",
                "commit": {"oid": "abc123"},
                "author": {"login": "someone-else"},
            }
        ],
    )

    result = merge_readiness.evaluate_snapshot(value)

    assert result["ready"] is False
    assert blocker_codes(result) == {"solution_review_missing"}


def signed_test_evidence(run_id=3):
    value = dict(snapshot()["solution_review"], workflow_run_id=run_id, runner_sha="master-sha")
    report = dict(value["review_report"])
    report.update(
        {
            "task": value["task"],
            "reviewed_head_sha": value["reviewed_head_sha"],
            "reviewed_base_sha": value["reviewed_base_sha"],
            "reviewer_role": value["reviewer_role"],
            "reviewer_session": value["reviewer_session"],
            "implementation_role": value["implementation_role"],
            "implementation_session": value["implementation_session"],
        }
    )
    value["review_report"] = report
    value["review_report_sha256"] = hashlib.sha256(
        json.dumps(report, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
    return value


def test_select_solution_review_evidence_accepts_only_exact_successful_run():
    runs = [
        {
            "id": 1,
            "head_sha": "old-master-sha",
            "path": ".github/workflows/solution-review.yml",
            "event": "workflow_dispatch",
            "head_branch": "master",
            "status": "completed",
            "conclusion": "success",
        },
        {
            "id": 2,
            "head_sha": "master-sha",
            "path": ".github/workflows/solution-review.yml",
            "event": "workflow_dispatch",
            "head_branch": "master",
            "status": "completed",
            "conclusion": "failure",
        },
        {
            "id": 3,
            "head_sha": "master-sha",
            "path": ".github/workflows/solution-review.yml",
            "event": "workflow_dispatch",
            "head_branch": "master",
            "status": "completed",
            "conclusion": "success",
        },
    ]
    artifacts = {
        3: [
            {
                "id": 88,
                "name": "solution-review-pr-123-abc123",
                "expired": False,
                "evidence": signed_test_evidence(3),
            }
        ]
    }

    evidence = merge_readiness.select_solution_review_evidence(
        runs, artifacts, 123, "abc123", "base123"
    )

    assert evidence["workflow_run_id"] == 3


def test_select_solution_review_evidence_rejects_mismatched_payload():
    run = {
        "id": 3,
        "head_sha": "master-sha",
        "path": ".github/workflows/solution-review.yml",
        "event": "workflow_dispatch",
        "head_branch": "master",
        "status": "completed",
        "conclusion": "success",
    }
    bad = dict(signed_test_evidence(3), task="pr:999")

    assert (
        merge_readiness.select_solution_review_evidence(
            [run],
            {
                3: [
                    {
                        "id": 88,
                        "name": "solution-review-pr-123-abc123",
                        "expired": False,
                        "evidence": bad,
                    }
                ]
            },
            123,
            "abc123",
            "base123",
        )
        is None
    )




def test_dispatch_from_master_accepts_distinct_reviewed_pr_head():
    run = {
        "id": 500,
        "event": "workflow_dispatch",
        "head_branch": "master",
        "head_sha": "master-sha",
        "path": ".github/workflows/solution-review.yml",
        "status": "completed",
        "conclusion": "success",
    }
    report = signed_test_evidence(500)
    artifact = {
        "id": 44,
        "name": "solution-review-pr-123-abc123",
        "expired": False,
        "evidence": report,
    }
    assert (
        merge_readiness.select_solution_review_evidence(
            [run], {500: [artifact]}, 123, "abc123", "base123"
        )
        == report
    )


@pytest.mark.parametrize("revision", ["head", "base"])
def test_signed_evidence_cannot_be_reused_after_revision_change(revision):
    run = {
        "id": 500,
        "event": "workflow_dispatch",
        "head_branch": "master",
        "head_sha": "master-sha",
        "path": ".github/workflows/solution-review.yml",
        "status": "completed",
        "conclusion": "success",
    }
    evidence = signed_test_evidence(500)
    artifact = {
        "id": 44,
        "name": "solution-review-pr-123-abc123",
        "expired": False,
        "evidence": evidence,
    }
    head = "next-head" if revision == "head" else "abc123"
    base = "next-base" if revision == "base" else "base123"
    assert (
        merge_readiness.select_solution_review_evidence([run], {500: [artifact]}, 123, head, base)
        is None
    )


def test_unconfirmed_accepted_is_rejected():
    value = dict(snapshot()["solution_review"])
    value.pop("verification")
    value["outcome"] = "ACCEPTED"
    result = merge_readiness.evaluate_snapshot(snapshot(solution_review=value))
    assert "solution_review_invalid" in blocker_codes(result)


def test_review_run_from_feature_branch_not_trusted():
    run = {
        "id": 500,
        "event": "workflow_dispatch",
        "head_branch": "feature",
        "head_sha": "abc123",
        "path": ".github/workflows/solution-review.yml",
        "status": "completed",
        "conclusion": "success",
    }
    evidence = signed_test_evidence(500)
    artifact = {
        "id": 44,
        "name": "solution-review-pr-123-abc123",
        "expired": False,
        "evidence": evidence,
    }
    assert (
        merge_readiness.select_solution_review_evidence(
            [run], {500: [artifact]}, 123, "abc123", "base123"
        )
        is None
    )
