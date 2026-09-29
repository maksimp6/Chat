from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "merge-readiness.yml"


def test_merge_readiness_workflow_is_read_only_and_exact_head() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "name: Merge readiness" in workflow
    assert "contents: read" in workflow
    assert "checks: read" in workflow
    assert "pull-requests: read" in workflow
    assert "contents: write" not in workflow
    assert "pull-requests: write" not in workflow
    assert "actions: write" not in workflow
    assert "ref: ${{ github.event.pull_request.head.sha }}" in workflow
    assert "persist-credentials: false" in workflow
    assert "github.event.pull_request.base.ref == 'master'" in workflow
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


def test_merge_readiness_workflow_rechecks_after_review_events() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")

    assert "pull_request_review:" in workflow
    assert "types: [submitted, dismissed]" in workflow
    assert "pull_request_review_comment:" in workflow
    assert "types: [created, edited, deleted]" in workflow
    assert "pull_request_review_thread:" in workflow
    assert "types: [resolved, unresolved]" in workflow
