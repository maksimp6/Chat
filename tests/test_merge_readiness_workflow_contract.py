from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "merge-readiness.yml"
WORKFLOW_PATH = WORKFLOW


def test_merge_readiness_workflow_is_read_only_and_exact_head() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Merge readiness snapshot" in workflow
    assert "contents: read" in workflow
    assert "checks: read" in workflow
    assert "pull-requests: write" in workflow
    assert "contents: write" not in workflow
    assert "actions: write" not in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.pull_request.draft == false" in workflow


def test_merge_readiness_workflow_requires_current_protected_checks() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    required_checks = [
        "Application tests",
        "PostgreSQL integration",
        "Android debug APK",
        "Auto-format repository",
        "Trivy repository scan",
        "Zizmor GitHub Actions audit",
        "CodeQL (python)",
        "CodeQL (javascript-typescript)",
    ]
    for check in required_checks:
        assert check in workflow

    assert "MERGE_REQUIRED_CHECKS" in workflow
    assert "python scripts/merge_readiness.py" in workflow


def test_merge_readiness_workflow_wait_is_bounded_and_fail_closed() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "max_attempts=180" in workflow
    assert "sleep 5" in workflow
    assert 'retryable = {"check_pending", "required_check_missing"}' in workflow
    assert "Merge readiness timed out waiting for required checks." in workflow
    assert "emit_summary" in workflow
    assert workflow.count("emit_summary") >= 3
    assert 'exit "$status"' in workflow
    assert "cancel-in-progress: true" in workflow


def test_merge_readiness_trigger_policy_avoids_comment_churn() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    trigger_block = workflow.split("permissions:", 1)[0]

    # Head-changing PR events and review verdict changes can invalidate readiness.
    assert "pull_request:" in trigger_block
    for event_type in ("opened", "synchronize", "reopened", "ready_for_review"):
        assert event_type in trigger_block
    assert "pull_request_review:" in trigger_block
    assert "submitted" in trigger_block
    assert "dismissed" in trigger_block

    # Individual inline comments are noisy and do not need to launch another
    # polling readiness run. Review-thread state is still evaluated by
    # scripts/merge_readiness.py when a supported readiness event runs.
    assert "pull_request_review_comment:" not in trigger_block
    assert "pull_request_review_thread:" not in trigger_block


def test_stacked_readiness_runs_for_child_prs_and_can_update_one_comment():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "github.event.pull_request.base.ref == 'master'" not in text
    assert "pull-requests: write" in text
    assert "<!-- alice-stack-readiness -->" in text
    assert "--method PATCH" in text
    assert "--method POST" in text
    assert 'contains("<!-- alice-stack-readiness -->")' in text


def test_stacked_readiness_reacts_to_topology_edit_and_close_events():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "edited" in text
    assert "closed" in text


def test_descendant_event_refreshes_root_comment_without_recursive_dispatch():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/stack_status.py" in text
    assert '"root_pr"' in text
    assert "<!-- alice-stack-root-readiness -->" in text
    assert "~~~mermaid" in text
    assert "issues/$root_pr/comments" in text
    assert "workflow_dispatch" not in text


def test_descendant_events_refresh_root_stack_comment_without_dispatch_loop():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "scripts/stack_status.py" in text
    assert "issues/$root_pr/comments" in text
    assert "repository_dispatch" not in text
    assert "Stack topology" in text
    assert "mermaid" in text


def test_descendant_event_updates_one_mermaid_comment_on_root():
    text = WORKFLOW_PATH.read_text(encoding="utf-8")
    assert "python scripts/stack_status.py" in text
    assert "<!-- alice-root-stack -->" in text
    assert "```mermaid" in text
    assert "issues/$root_pr/comments" in text
    assert "issues/comments/$comment_id" in text
