import importlib.util
from pathlib import Path


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
        "draft": False,
        "behind_by": 0,
        "check_runs": [
            {"name": "CI", "status": "completed", "conclusion": "success"},
            {"name": "Format", "status": "completed", "conclusion": "success"},
        ],
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


def test_merge_readiness_fails_closed_for_draft_stale_pending_and_threads():
    result = merge_readiness.evaluate_snapshot(
        snapshot(
            draft=True,
            behind_by=2,
            check_runs=[{"name": "CI", "status": "in_progress", "conclusion": None}],
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


def test_merge_readiness_rejects_failed_or_missing_checks():
    failed = merge_readiness.evaluate_snapshot(
        snapshot(check_runs=[{"name": "CI", "status": "completed", "conclusion": "failure"}])
    )
    missing = merge_readiness.evaluate_snapshot(snapshot(check_runs=[]))

    assert blocker_codes(failed) == {"check_failed"}
    assert blocker_codes(missing) == {"checks_missing"}


def test_merge_readiness_rejects_wrong_base_and_truncated_threads():
    result = merge_readiness.evaluate_snapshot(
        snapshot(pr_base_ref="release", review_threads_truncated=True)
    )

    assert blocker_codes(result) == {"wrong_base", "review_threads_truncated"}


def test_merge_readiness_allows_success_neutral_and_skipped_checks():
    result = merge_readiness.evaluate_snapshot(
        snapshot(
            check_runs=[
                {"name": "CI", "status": "completed", "conclusion": "success"},
                {"name": "Optional", "status": "completed", "conclusion": "neutral"},
                {"name": "Preview", "status": "completed", "conclusion": "skipped"},
            ]
        )
    )

    assert result["ready"] is True
