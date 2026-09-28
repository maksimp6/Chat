import json
import os
import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "alice.yml").read_text(encoding="utf-8")


def _step_run(name: str) -> str:
    lines = WORKFLOW.splitlines()
    start = lines.index(f"      - name: {name}")
    run_at = next(i for i in range(start, len(lines)) if lines[i].strip() == "run: |")
    body = []
    for line in lines[run_at + 1 :]:
        if line.strip() and len(line) - len(line.lstrip()) < 10:
            break
        body.append(line[10:])
    return "\n".join(body)


def test_actions_are_pinned_and_do_not_persist_credentials():
    uses = re.findall(r"uses:\s*(\S+)", WORKFLOW)
    assert uses
    assert all(re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref) for ref in uses)
    assert "persist-credentials: false" in WORKFLOW
    assert "pull_request_target" not in WORKFLOW
    assert "permissions: {}" in WORKFLOW


def test_only_trusted_mentions_or_label_trigger_alice():
    condition = WORKFLOW.split("    if: |", 1)[1].split("    runs-on:", 1)[0]
    assert condition.count("contains(github.event.comment.body, '@alice')") == 1
    assert condition.count('["OWNER","MEMBER","COLLABORATOR"]') == 2
    assert "github.event.label.name == 'alice'" in condition
    assert "!github.event.issue.pull_request" in condition


def test_untrusted_text_never_reaches_run_scripts_directly():
    for match in re.finditer(r"run: \|\n((?:\s{10}.*\n|\s*\n)+)", WORKFLOW):
        assert "${{" not in match.group(1)
    for field in ("issue.title", "issue.body", "comment.body"):
        assert f"${{{{ github.event.{field} }}}}" in WORKFLOW


def test_secrets_are_scoped_to_their_steps():
    run_alice = WORKFLOW.split("      - name: Run Alice", 1)[1].split("      - name:", 1)[0]
    publish = WORKFLOW.split("      - name: Open pull request and report", 1)[1]
    assert "secrets.YANDEX_API_KEY" in run_alice
    assert "ALICE_GITHUB_TOKEN" not in run_alice
    assert "secrets.ALICE_GITHUB_TOKEN" in publish
    assert WORKFLOW.count("secrets.ALICE_GITHUB_TOKEN") == 1


def _git_repo(tmp_path: Path, files):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    for name in files:
        path = repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x", encoding="utf-8")
    return repo


@pytest.mark.parametrize(
    ("files", "allowed"),
    [
        (["module.py"], True),
        ([".github/workflows/ci.yml"], False),
        ([".env"], False),
        (["android/release.jks"], False),
        ([f"f{i}.py" for i in range(21)], False),
    ],
)
def test_diff_guard(tmp_path, files, allowed):
    repo = _git_repo(tmp_path, files)
    result = subprocess.run(
        ["bash", "-e", "-c", _step_run("Guard the diff")],
        cwd=repo,
        env={**os.environ, "RUNNER_TEMP": str(tmp_path)},
        capture_output=True,
    )
    assert (result.returncode == 0) is allowed


def test_issue_text_is_saved_verbatim_and_report_does_not_mention_alice(tmp_path):
    env = {
        **os.environ,
        "RUNNER_TEMP": str(tmp_path),
        "ISSUE_TITLE": "Fix $(touch pwned) `id`",
        "ISSUE_BODY": "Body",
        "COMMENT_BODY": "@alice go",
    }
    subprocess.run(["bash", "-e", "-c", _step_run("Save issue text")], env=env, check=True)
    assert (tmp_path / "title.txt").read_text() == "Fix $(touch pwned) `id`\n"
    assert "@alice go" in (tmp_path / "body.txt").read_text()
    assert not Path("pwned").exists()

    (tmp_path / "result.json").write_text(
        json.dumps(
            {
                "status": "no_changes",
                "model": "aliceai-llm",
                "summary": "Ask @alice again",
                "billing": {"total_cost": 0.12, "currency": "RUB", "cost_status": "calculated"},
                "trace_id": "t1",
                "pending_tools": ["delete_path"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "files.txt").write_text("", encoding="utf-8")
    report_part = _step_run("Open pull request and report").split(
        'if [ -s "$RUNNER_TEMP/files.txt" ]'
    )[0]
    subprocess.run(["bash", "-e", "-c", report_part], env={**env, "GH_TOKEN": "unused"}, check=True)

    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "@alice" not in report
    assert "0.12 RUB (calculated)" in report
    assert "delete_path" in report


def test_app_token_is_preferred_over_personal_token():
    step = WORKFLOW.split("      - name: Create Alice Pro app token", 1)[1].split(
        "      - name:", 1
    )[0]
    assert "id: app-token" in step
    assert "if: vars.ALICE_APP_CLIENT_ID != ''" in step
    assert re.search(r"uses: actions/create-github-app-token@[0-9a-f]{40}", step)
    assert "client-id: ${{ vars.ALICE_APP_CLIENT_ID }}" in step
    assert "private-key: ${{ secrets.ALICE_APP_PRIVATE_KEY }}" in step
    permissions = sorted(re.findall(r"permission-([\w-]+): (\w+)", step))
    assert permissions == [("contents", "write"), ("issues", "write"), ("pull-requests", "write")]
    assert WORKFLOW.count("secrets.ALICE_APP_PRIVATE_KEY") == 1

    publish = WORKFLOW.split("      - name: Open pull request and report", 1)[1]
    assert "GH_TOKEN: ${{ steps.app-token.outputs.token || secrets.ALICE_GITHUB_TOKEN }}" in publish
    assert "APP_SLUG: ${{ steps.app-token.outputs.app-slug }}" in publish


def test_app_commits_use_the_bot_identity():
    script = _step_run("Open pull request and report")
    assert 'bot="$APP_SLUG[bot]"' in script
    assert 'gh api "users/$bot" --jq .id' in script
    assert 'git config user.email "$bot_id+$bot@users.noreply.github.com"' in script
