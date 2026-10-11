"""Contract tests for the optional Haiku-only GitHub task workflow."""

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_PATH = ROOT / ".github" / "workflows" / "claude-haiku.yml"


def _workflow():
    if not WORKFLOW_PATH.is_file():
        return {}
    return yaml.safe_load(WORKFLOW_PATH.read_text(encoding="utf-8"))


def _job(config):
    return config.get("jobs", {}).get("claude-haiku", {})


def _action_step(job):
    steps = [
        step
        for step in job.get("steps", [])
        if step.get("uses", "").startswith("anthropics/claude-code-action@")
    ]
    assert len(steps) == 1
    return steps[0]


def test_only_owner_authored_issue_comments_can_start_haiku():
    config = _workflow()
    events = config.get(True, config.get("on", {}))
    assert events == {"issue_comment": {"types": ["created"]}}

    job = _job(config)
    condition = job["if"]
    assert "startsWith(github.event.comment.body, '@claude-lite')" in condition
    assert "github.event.issue.user.login == 'maksimp6'" in condition
    assert "github.event.issue.pull_request == null" in condition
    assert all(role in condition for role in ("OWNER", "MEMBER", "COLLABORATOR"))

    action = _action_step(job)
    assert action["with"]["trigger_phrase"] == "@claude-lite"
    assert action["with"]["allowed_non_write_users"] == ""
    assert action["with"]["allowed_bots"] == ""
    assert action["with"]["include_comments_by_actor"] == "maksimp6"


def test_action_is_immutable_pinned_and_haiku_55_only():
    action = _action_step(_job(_workflow()))
    assert re.fullmatch(r"anthropics/claude-code-action@[0-9a-f]{40}", action["uses"])
    args = action["with"]["claude_args"]
    assert "--model claude-haiku-5-5" in args
    assert "--model " not in args.replace("--model claude-haiku-5-5", "", 1)
    assert "sonnet" not in args.lower()
    assert "opus" not in args.lower()


def test_run_has_documented_turn_spend_time_and_concurrency_bounds():
    config = _workflow()
    job = _job(config)
    args = _action_step(job)["with"]["claude_args"]
    assert "--max-turns 8" in args
    assert "--max-budget-usd 0.25" in args
    assert 1 <= job["timeout-minutes"] <= 15

    concurrency = config["concurrency"]
    assert "github.event.issue.number" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_github_token_cannot_merge_or_change_repository_configuration():
    job = _job(_workflow())
    assert job["permissions"] == {"contents": "write", "issues": "write"}
    action = _action_step(job)
    assert action["with"]["github_token"] == "${{ github.token }}"
    assert "pull-requests" not in job["permissions"]
    assert "actions" not in job["permissions"]
    assert "deployments" not in job["permissions"]
    assert "administration" not in job["permissions"]
    assert "id-token" not in job["permissions"]


def test_anthropic_key_is_scoped_and_action_output_is_not_dumped():
    job = _job(_workflow())
    action = _action_step(job)
    inputs = action["with"]
    assert inputs["anthropic_api_key"] == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert inputs["show_full_output"] == "false"
    assert inputs["display_report"] == "false"
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in str(job)
    assert "set -x" not in str(job)
    assert "secrets." not in str(job).replace("${{ secrets.ANTHROPIC_API_KEY }}", "")


def test_haiku_backend_is_not_a_ci_or_merge_readiness_requirement():
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8").lower()
    readiness = (
        ROOT / ".github" / "workflows" / "merge-readiness.yml"
    ).read_text(encoding="utf-8").lower()
    assert "haiku" not in ci
    assert "haiku" not in readiness
