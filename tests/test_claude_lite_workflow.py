from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> str:
    return (ROOT / ".github" / "workflows" / "claude-lite.yml").read_text(encoding="utf-8")


def _workflow_parsed() -> dict:
    return yaml.safe_load(_workflow())


def test_claude_lite_action_trigger_matches_outer_workflow_trigger():
    workflow = _workflow()

    assert "contains(github.event.comment.body, '@claude-lite')" in workflow
    assert 'trigger_phrase: "@claude-lite"' in workflow


def test_claude_lite_keeps_pinned_action_and_least_privilege():
    workflow = _workflow()

    assert "anthropics/claude-code-action@756cc22e19660d20e8cc9496b4f242475a7f7790" in workflow
    assert "permissions: {}" in workflow
    assert "persist-credentials: false" in workflow
    assert "contents: write" in workflow
    assert "pull-requests: write" in workflow
    assert "issues: write" in workflow
    assert "actions: read" in workflow
    assert "contents: admin" not in workflow
    assert "pull_request_target" not in workflow


def test_claude_lite_progress_tracking_is_enabled_for_execution_evidence():
    workflow = _workflow()

    assert "track_progress: true" in workflow


def test_claude_direct_implementation_profile_uses_sonnet_and_enough_turns():
    workflow = _workflow()

    assert "--model claude-sonnet-4-6" in workflow
    assert "--max-turns 45" in workflow


def test_claude_direct_implementation_profile_has_bounded_write_and_validation_tools():
    workflow = _workflow()

    required = (
        "Edit",
        "Write",
        "Bash(git status:*)",
        "Bash(git diff:*)",
        "Bash(pytest:*)",
        "Bash(python:*)",
        "Bash(bash scripts/format.sh:*)",
    )
    for tool in required:
        assert tool in workflow

    assert 'Bash("*")' not in workflow
    assert "Bash(git push --force:*)" not in workflow
    assert "Bash(gh secret:*)" not in workflow
    assert "Bash(gh api repos/*/branches:*)" not in workflow


def test_concurrency_is_at_job_level_not_workflow_level():
    """Bot progress comments must not acquire the concurrency lock.

    Workflow-level concurrency is evaluated before any job `if:` condition, so
    a bot-triggered event that would be skipped by the actor guard still holds
    the lock while its (empty) run is pending.  Moving concurrency to job level
    lets the `if:` guard skip the job without ever touching the lock.
    """
    doc = _workflow_parsed()

    # No workflow-level concurrency — the lock must not be acquired before the
    # job-level actor guard runs.
    assert "concurrency" not in doc, (
        "concurrency must be at job level, not workflow level; "
        "a top-level concurrency block is acquired before the job `if:` guard "
        "can skip bot-triggered runs"
    )

    job = doc["jobs"]["claude"]

    # Job-level concurrency is present with the expected key shape.
    assert "concurrency" in job, (
        "jobs.claude must declare a concurrency block so human task runs "
        "serialize per issue/PR"
    )
    assert job["concurrency"]["cancel-in-progress"] is False
    assert job["concurrency"]["group"].startswith("claude-lite-")
