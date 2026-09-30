from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> str:
    return (ROOT / ".github" / "workflows" / "claude-lite.yml").read_text(encoding="utf-8")


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
