from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _workflow() -> str:
    return (ROOT / ".github" / "workflows" / "claude-lite.yml").read_text(encoding="utf-8")


def test_claude_lite_action_trigger_requires_explicit_command_position():
    workflow = _workflow()

    assert "startsWith(github.event.comment.body, '@claude-lite')" in workflow
    assert "startsWith(github.event.issue.body, '@claude-lite')" in workflow
    assert "startsWith(github.event.issue.title, '@claude-lite')" in workflow
    assert "contains(github.event.comment.body, '@claude-lite') &&" not in workflow
    assert 'trigger_phrase: "@claude-lite"' in workflow


def test_claude_lite_status_or_documentation_mentions_do_not_match_outer_trigger():
    workflow = _workflow()

    assert "startsWith(github.event.comment.body, '@claude-lite')" in workflow
    assert "contains(github.event.comment.body, '@claude-lite session')" in workflow
    assert "contains(github.event.issue.body, '@claude-lite session')" in workflow


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


def test_claude_lite_can_read_public_web_sources():
    workflow = _workflow()
    allowed = next(line for line in workflow.splitlines() if "--allowedTools" in line)

    assert "WebFetch" in allowed
    assert "WebSearch" in allowed
    assert "Bash(curl" not in allowed


def test_claude_lite_concurrency_is_job_scoped_after_trigger_guard():
    workflow = _workflow()

    assert "\nconcurrency:\n" not in workflow
    job_marker = "  claude:\n    name: Respond to @claude-lite\n"
    assert job_marker in workflow
    job_section = workflow.split(job_marker, 1)[1]
    assert "    concurrency:\n" in job_section
    assert "      group: claude-lite-" in job_section
    assert "      cancel-in-progress: false" in job_section


def test_maintainer_policy_separates_role_from_executable_backend_trigger():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    maintainer = agents.split("## Maintainer", 1)[1].split("## Architecture rules", 1)[0]
    governance = (ROOT / "docs" / "agents" / "process-observation-and-governance.md").read_text(
        encoding="utf-8"
    )
    observer_profile = (ROOT / ".github" / "agents" / "operations-observer.agent.md").read_text(
        encoding="utf-8"
    )

    assert "maintainer is a **role**" in maintainer
    assert "executable-backend evidence" in maintainer
    assert "explicit **Maintainer** intent" in maintainer
    assert "backend visibly acknowledges the dispatch" in maintainer
    assert "Do not hard-code a merge method" in maintainer
    assert "role-only Claude mention" in governance
    assert "author association allowed by the repository workflow" in governance
    assert "acknowledgement from that backend" in governance
    assert "workflow envelopes with\nno accepted inner trigger" in observer_profile


def test_documentary_trigger_guidance_avoids_accidental_execution():
    agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    governance = (ROOT / "docs" / "agents" / "process-observation-and-governance.md").read_text(
        encoding="utf-8"
    )

    assert "without reproducing the\n  literal mention trigger" in agents
    assert "Issue\nand PR comments are themselves workflow input." in governance
